"""One capture through every stage. Writes everything to /data/runs/<capture id>/.

Each finished stage is recorded in state.json, so a retry continues from the stage that
failed. `progress(stage, fraction)` is called as stages start and advance, for studio; run.log
gets a line per stage, colmap.log everything COLMAP prints.
"""

import json
import time
from pathlib import Path

import numpy as np

from cast.pipeline import PipelineError, colmap, frames, glb, masks, mesh

STAGES = ["frames", "masks", "poses", "dense", "mesh"]


def stages_for(mode: str) -> list[str]:
    return [s for s in STAGES if s != "masks" or mode == "object"]


def process(video: Path, meta: dict, mode: str, run_dir: Path,
            progress=lambda stage, fraction: None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"done": []}
    report = state.setdefault("report", {"mode": mode, "seconds": {}})
    log = run_dir / "colmap.log"
    images, mask_dir = run_dir / "frames", run_dir / "masks"
    dense_dir, cloud = run_dir / "dense", run_dir / "dense" / "fused.ply"
    use_masks = mode == "object"

    def note(text):
        with open(run_dir / "run.log", "a") as fh:
            fh.write(time.strftime("%H:%M:%S ") + text + "\n")

    def stage(name, fn):
        if name in state["done"]:
            return
        progress(name, 0.0)
        note(f"{name}: started")
        start = time.time()
        try:
            fn(lambda f: progress(name, f))
        except Exception as e:
            note(f"{name}: FAILED: {e}")
            raise
        report["seconds"][name] = round(time.time() - start)
        note(f"{name}: done in {report['seconds'][name]} s")
        state["done"].append(name)
        state_path.write_text(json.dumps(state, indent=1))

    def do_frames(p):
        markers = meta.get("phone", {}).get("markers", [])
        report["frames"] = frames.make_frames(video, markers, meta["video"]["duration"], mode,
                                              images, progress=p)
        note(f"frames: {report['frames']['frames']} kept")

    def do_masks(p):
        report["masks"] = masks.make_masks(images, mask_dir, progress=p)

    def do_poses(p):
        model, secs = colmap.poses(images, mask_dir if use_masks else None, run_dir / "sfm", log,
                                   progress=p)
        total = len(list(images.glob("*.jpg")))
        per_pass = {}
        for k in sorted({f.name.split("_")[0] for f in images.glob("*.jpg")}):
            per_pass[k] = {"frames": len(list(images.glob(f"{k}_*.jpg"))),
                           "placed": sum(n.startswith(k + "_") for n in model.names)}
        report["poses"] = {"placed": len(model.names), "frames": total, "passes": per_pass,
                           "points": model.points, "error_px": round(model.error, 3),
                           "colmap_seconds": {k: round(v) for k, v in secs.items()}}
        state["model"] = str(model.folder)
        state["cameras"] = {"names": model.names, "centres": model.centres.tolist(),
                            "ups": model.ups.tolist(), "forwards": model.forwards.tolist()}
        note(f"poses: {len(model.names)}/{total} frames placed, by pass {per_pass}")
        if len(model.names) < colmap.MIN_REGISTERED * total:
            raise PipelineError(f"Only {len(model.names)} of {total} frames could be placed. "
                                "Walk slower and keep the whole object in view.")
        if use_masks and any(p["placed"] < 0.5 * p["frames"] for p in per_pass.values()):
            raise PipelineError("The two sides could not be joined. Film the edges slowly, "
                                "before and after turning the object over.")

    def do_dense(p):
        secs = colmap.dense(images, Path(state["model"]), dense_dir, log,
                            progress=colmap.span(p, 0, 0.85))
        fusion_masks = None
        if use_masks:  # fusion reads undistorted images, so mask those
            fusion_masks = dense_dir / "masks"
            masks.make_masks(dense_dir / "images", fusion_masks, progress=colmap.span(p, 0.85, 0.92))
        secs["fusion"] = colmap.fuse(dense_dir, cloud, fusion_masks, log,
                                     progress=colmap.span(p, 0.92, 1.0))
        report["dense_seconds"] = {k: round(v) for k, v in secs.items()}

    def do_mesh(p):
        cams = state["cameras"]
        centres, ups = np.array(cams["centres"]), np.array(cams["ups"])
        centre = mesh.look_at_point(centres, np.array(cams["forwards"]))
        distance = float(np.median(np.linalg.norm(centres - centre, axis=1)))
        first = np.array([n.startswith("p1_") for n in cams["names"]])
        up = ups[first].mean(axis=0) if first.any() else ups.mean(axis=0)
        m = mesh.build(cloud, centre, radius=0.8 * distance, up=up)
        glb.write_glb(run_dir / "model.glb", m.vertices, m.faces, m.colors, m.normals)
        report["mesh"] = m.stats
        note(f"mesh: {m.stats['triangles']:,} triangles")

    jobs = {"frames": do_frames, "masks": do_masks, "poses": do_poses, "dense": do_dense,
            "mesh": do_mesh}
    for name in stages_for(mode):
        try:
            stage(name, jobs[name])
        finally:
            state_path.write_text(json.dumps(state, indent=1))
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    return report
