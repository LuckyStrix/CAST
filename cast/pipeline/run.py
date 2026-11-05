"""One capture through every stage. Writes everything to /data/runs/<capture id>/.

Each finished stage is recorded in state.json, so a retry continues from the stage that
failed. `progress(stage)` is called as each stage starts, for studio.
"""

import json
import time
from pathlib import Path

import numpy as np

from cast.pipeline import PipelineError, colmap, frames, glb, masks, mesh

STAGES = ["frames", "masks", "poses", "dense", "mesh"]


def process(video: Path, meta: dict, mode: str, run_dir: Path, progress=lambda s: None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"done": []}
    report = state.setdefault("report", {"mode": mode, "seconds": {}})
    log = run_dir / "colmap.log"
    images, mask_dir = run_dir / "frames", run_dir / "masks"
    dense_dir, cloud = run_dir / "dense", run_dir / "dense" / "fused.ply"
    use_masks = mode == "object"

    def stage(name, fn):
        if name in state["done"]:
            return
        progress(name)
        start = time.time()
        fn()
        report["seconds"][name] = round(time.time() - start)
        state["done"].append(name)
        state_path.write_text(json.dumps(state, indent=1))

    def do_frames():
        markers = meta.get("phone", {}).get("markers", [])
        report["frames"] = frames.make_frames(video, markers, meta["video"]["duration"], mode,
                                              images)

    def do_masks():
        if use_masks:
            report["masks"] = masks.make_masks(images, mask_dir)

    def do_poses():
        model, secs = colmap.poses(images, mask_dir if use_masks else None, run_dir / "sfm", log)
        total = len(list(images.glob("*.jpg")))
        per_pass = {}
        for p in sorted({f.name.split("_")[0] for f in images.glob("*.jpg")}):
            per_pass[p] = {"frames": len(list(images.glob(f"{p}_*.jpg"))),
                           "placed": sum(n.startswith(p + "_") for n in model.names)}
        report["poses"] = {"placed": len(model.names), "frames": total, "passes": per_pass,
                           "points": model.points, "error_px": round(model.error, 3),
                           "colmap_seconds": {k: round(v) for k, v in secs.items()}}
        state["model"] = str(model.folder)
        if len(model.names) < colmap.MIN_REGISTERED * total:
            raise PipelineError(f"Only {len(model.names)} of {total} frames could be placed. "
                                "Walk slower and keep the whole object in view.")
        if use_masks and any(p["placed"] < 0.5 * p["frames"] for p in per_pass.values()):
            raise PipelineError("The two sides could not be joined. Film the edges slowly, "
                                "before and after turning the object over.")

    def do_dense():
        model = colmap.read_model(Path(state["model"]), log)
        secs = colmap.dense(images, model, dense_dir, log)
        fusion_masks = None
        if use_masks:  # fusion reads undistorted images, so mask those
            fusion_masks = dense_dir / "masks"
            masks.make_masks(dense_dir / "images", fusion_masks)
        secs["fusion"] = colmap.fuse(dense_dir, cloud, fusion_masks, log)
        report["dense_seconds"] = {k: round(v) for k, v in secs.items()}

    def do_mesh():
        model = colmap.read_model(Path(state["model"]), log)
        centre = mesh.look_at_point(model.centres, model.forwards)
        distance = float(np.median(np.linalg.norm(model.centres - centre, axis=1)))
        first = np.array([n.startswith("p1_") for n in model.names])
        up = model.ups[first].mean(axis=0) if first.any() else model.ups.mean(axis=0)
        m = mesh.build(cloud, centre, radius=0.8 * distance, up=up)
        glb.write_glb(run_dir / "model.glb", m.vertices, m.faces, m.colors, m.normals)
        report["mesh"] = m.stats

    for name, fn in zip(STAGES, [do_frames, do_masks, do_poses, do_dense, do_mesh]):
        try:
            stage(name, fn)
        finally:
            state_path.write_text(json.dumps(state, indent=1))
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    return report
