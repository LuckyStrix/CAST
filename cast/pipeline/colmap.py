"""COLMAP, run as a subprocess (it is in the worker image): camera poses, then a dense cloud.

Checked against COLMAP 4.2.1 (colmap/colmap image). Option names that changed between releases
are looked up in the command's own help. Pose reading and pair lists come from FleshForge.
"""

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cast.pipeline import PipelineError

EXHAUSTIVE_MAX = 250  # above this many frames, match a chosen list of pairs
PAIR_WINDOW, PAIR_STRIDE = 10, 8
MIN_REGISTERED = 0.8  # fraction of frames that must get a camera pose

_help: dict = {}


def available() -> bool:
    return shutil.which("colmap") is not None


_COUNT = re.compile(r"\[(\d+)/(\d+)\]")  # "Processed file [12/160]", "Fusing image [3/160]"
_BLOCK = re.compile(r"\[(\d+)/(\d+), (\d+)/(\d+)\]")  # exhaustive "Matching block [1/4, 2/4]"
_VIEW = re.compile(r"Processing view (\d+) / (\d+)")  # patch_match_stereo
_REGISTER = re.compile(r"Registering image #\d+ \((\d+)\)")  # incremental mapper


def line_fraction(line: str, total_images: int = 0) -> float | None:
    """How far a COLMAP command is, from one line of its output, or None."""
    if m := _BLOCK.search(line):
        a, b, c, d = map(int, m.groups())
        return ((a - 1) * d + c) / (b * d)
    if m := _VIEW.search(line):
        return int(m.group(1)) / int(m.group(2))
    if m := _COUNT.search(line):
        return int(m.group(1)) / int(m.group(2))
    if total_images and (m := _REGISTER.search(line)):
        return min(1.0, int(m.group(1)) / total_images)
    return None


def run(command: str, args: list, log: Path, progress=None, total_images: int = 0,
        passes: int = 1) -> float:
    """Run `colmap command args`, appending its output to `log`. `progress(fraction)` is called
    as COLMAP reports how far it is. `passes`: how many times the command goes through every
    image (patch_match_stereo with geometric consistency: 2)."""
    cmd = ["colmap", command, *[str(a) for a in args]]
    start = time.time()
    done_passes, last = 0, 0.0
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n===== {' '.join(cmd)}\n")
        fh.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", bufsize=1)
        for line in proc.stdout:
            fh.write(line)
            f = line_fraction(line, total_images) if progress else None
            if f is not None:
                if f + 0.5 < last and done_passes + 1 < passes:  # counting started over
                    done_passes += 1
                last = f
                progress(min(1.0, (done_passes + f) / passes))
        code = proc.wait()
    if code != 0:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-8:])
        raise RuntimeError(f"colmap {command} failed (exit {code}):\n{tail}")
    return time.time() - start


def option(command: str, *names: str) -> str:
    if command not in _help:
        done = subprocess.run(["colmap", command, "-h"], check=False, capture_output=True, text=True)
        _help[command] = done.stdout + done.stderr
    for name in names:
        if name + " " in _help[command] or name + "\n" in _help[command]:
            return name
    raise RuntimeError(f"colmap {command} accepts none of {names}")


def match_pairs(names: list, window: int = PAIR_WINDOW, stride: int = PAIR_STRIDE) -> list:
    """Neighbours in order plus every `stride`-th frame: links laps and both passes."""
    n, keep = len(names), set()
    for i in range(n):
        for j in range(i + 1, min(n, i + window + 1)):
            keep.add((i, j))
        for j in range(0, n, stride):
            if j != i:
                keep.add((min(i, j), max(i, j)))
    return [(names[i], names[j]) for i, j in sorted(keep)]


@dataclass
class Model:
    folder: Path
    names: list  # registered image names
    centres: np.ndarray  # (N, 3) camera centres
    ups: np.ndarray  # (N, 3) camera up vectors (world)
    forwards: np.ndarray  # (N, 3) viewing directions (world)
    points: int
    error: float
    overlap: dict  # 3D points by which passes saw them: {"p1": n, "p2": n, "shared": n}


def quat_to_rot(qw, qx, qy, qz) -> np.ndarray:
    w, x, y, z = np.array([qw, qx, qy, qz], float) / np.linalg.norm([qw, qx, qy, qz])
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def parse_images_txt(text: str):
    """images.txt: pose lines alternate with point lines (which may be empty)."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    while lines and not lines[-1].strip():
        lines.pop()
    names, centres, ups, fwds = [], [], [], []
    for header in lines[0::2]:
        tok = header.split()
        R = quat_to_rot(*map(float, tok[1:5]))
        t = np.array(list(map(float, tok[5:8])))
        names.append(" ".join(tok[9:]))
        centres.append(-R.T @ t)
        ups.append(-R[1])  # COLMAP camera: +y down, +z forward
        fwds.append(R[2])
    return names, np.array(centres), np.array(ups), np.array(fwds)


def pass_overlap(images_text: str, points_text: str) -> dict:
    """How many 3D points each pass saw alone, and how many were seen from both ("shared").

    This is the real test of whether two passes joined: frames can all get a position while
    the two sides' positions only rest on a few (possibly wrong) common points."""
    lines = [ln for ln in images_text.splitlines() if not ln.startswith("#")]
    side = {}
    for header in lines[0::2]:
        tok = header.split()
        if len(tok) >= 10:
            side[tok[0]] = tok[9].split("_")[0]
    out = {"shared": 0}
    for ln in points_text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        seen = {side.get(i) for i in ln.split()[8::2]} - {None}
        key = "shared" if len(seen) > 1 else next(iter(seen), None)
        if key:
            out[key] = out.get(key, 0) + 1
    return out


def read_model(folder: Path, log: Path) -> Model:
    txt = folder.parent / (folder.name + "_txt")
    shutil.rmtree(txt, ignore_errors=True)
    txt.mkdir()
    run("model_converter", ["--input_path", folder, "--output_path", txt, "--output_type", "TXT"],
        log)
    images_text = (txt / "images.txt").read_text(errors="replace")
    points_text = (txt / "points3D.txt").read_text(errors="replace")
    names, centres, ups, fwds = parse_images_txt(images_text)
    errs = [float(ln.split()[7]) for ln in points_text.splitlines()
            if ln and not ln.startswith("#")]
    shutil.rmtree(txt)
    return Model(folder, names, centres, ups, fwds, len(errs),
                 float(np.mean(errs)) if errs else float("nan"),
                 pass_overlap(images_text, points_text))


def largest(sparse: Path, log: Path) -> Model | None:
    models = [read_model(p, log) for p in sorted(sparse.iterdir())
              if p.is_dir() and (p / "images.bin").exists()]
    return max(models, key=lambda m: len(m.names)) if models else None


def span(progress, lo: float, hi: float):
    """A progress callback for one part (lo..hi) of a stage."""
    return (lambda f: progress(lo + (hi - lo) * f)) if progress else None


def poses(images: Path, masks: Path | None, work: Path, log: Path,
          progress=None) -> tuple[Model, dict]:
    """Features, matching, mapping. Returns the model with the most frames, and timings."""
    work.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"
    for old in work.glob("database.db*"):
        old.unlink()
    names = sorted(p.name for p in images.glob("*.jpg"))
    secs = {}

    args = ["--database_path", db, "--image_path", images,
            "--ImageReader.single_camera", "1", "--ImageReader.camera_model", "SIMPLE_RADIAL",
            option("feature_extractor", "--FeatureExtraction.use_gpu", "--SiftExtraction.use_gpu"),
            "1"]
    if masks is not None:
        args += ["--ImageReader.mask_path", masks]
    secs["features"] = run("feature_extractor", args, log, span(progress, 0, 0.2))

    if len(names) <= EXHAUSTIVE_MAX:
        matcher, margs = "exhaustive_matcher", []
    else:
        pairs = work / "pairs.txt"
        pairs.write_text("".join(f"{a} {b}\n" for a, b in match_pairs(names)))
        matcher, margs = "matches_importer", ["--match_list_path", pairs, "--match_type", "pairs"]
    gpu = option(matcher, "--FeatureMatching.use_gpu", "--SiftMatching.use_gpu")
    secs["matching"] = run(matcher, ["--database_path", db, gpu, "1", *margs], log,
                           span(progress, 0.2, 0.55))

    best = None
    for mapper in ("global_mapper", "mapper"):  # global first (fast); incremental if it falls short
        out = work / mapper
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        try:
            secs[mapper] = run(mapper, ["--database_path", db, "--image_path", images,
                                        "--output_path", out], log,
                               span(progress, 0.55, 1.0), total_images=len(names))
            model = largest(out, log)
        except RuntimeError:
            model = None
        if model and (best is None or len(model.names) > len(best.names)):
            best = model
        if best and len(best.names) >= MIN_REGISTERED * len(names):
            break
    if best is None:
        raise PipelineError("No camera positions could be found. The object needs more texture "
                            "or light, or the walk round was too fast.")
    return best, secs


def undistort(images: Path, model_dir: Path, work: Path, log: Path, max_size: int,
              progress=None) -> float:
    shutil.rmtree(work, ignore_errors=True)
    return run("image_undistorter", [
        "--image_path", images, "--input_path", model_dir, "--output_path", work,
        "--output_type", "COLMAP", "--max_image_size", max_size], log, progress)


def crop_cameras(cameras_text: str, images_text: str, boxes: dict) -> tuple[str, str]:
    """Rewrite an undistorted (PINHOLE) model so each image has its own camera cropped to
    `boxes[name] = (x0, y0, x1, y1)`: size from the box, principal point moved by its corner,
    2D observations moved likewise. Images without a box keep their full frame."""
    cams = {}
    for ln in cameras_text.splitlines():
        if ln and not ln.startswith("#"):
            t = ln.split()
            if t[1] != "PINHOLE":
                raise RuntimeError(f"undistorted camera model is {t[1]}, expected PINHOLE")
            cams[t[0]] = [float(v) for v in t[2:8]]  # w h fx fy cx cy
    lines = [ln for ln in images_text.splitlines() if not ln.startswith("#")]
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) % 2:
        lines.append("")  # the last image's (empty) observation line
    out_cams, out_images = [], []
    for header, pts in zip(lines[0::2], lines[1::2]):
        t = header.split()
        image_id, name = t[0], " ".join(t[9:])
        w, h, fx, fy, cx, cy = cams[t[8]]
        x0, y0, x1, y1 = boxes.get(name, (0, 0, int(w), int(h)))
        out_cams.append(f"{image_id} PINHOLE {x1 - x0} {y1 - y0} {fx!r} {fy!r} "
                        f"{cx - x0!r} {cy - y0!r}")
        t[8] = image_id
        out_images.append(" ".join(t))
        p = pts.split()
        for i in range(0, len(p) - 2, 3):  # x y point3D_id
            p[i], p[i + 1] = repr(float(p[i]) - x0), repr(float(p[i + 1]) - y0)
        out_images.append(" ".join(p))
    return "\n".join(out_cams) + "\n", "\n".join(out_images) + "\n"


def mask_box(mask, margin: float, min_side: int):
    """(x0, y0, x1, y1) round the mask's object plus a margin, or None when there is no
    object or the box would be too small to be useful."""
    if mask is None or not mask.any():
        return None
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    pad = margin * max(xs.max() - xs.min(), ys.max() - ys.min())
    x0, y0 = max(0, int(xs.min() - pad)), max(0, int(ys.min() - pad))
    x1, y1 = min(w, int(xs.max() + pad) + 1), min(h, int(ys.max() + pad) + 1)
    if x1 - x0 < min_side or y1 - y0 < min_side:
        return None
    return x0, y0, x1, y1


def crop_to_masks(work: Path, mask_dir: Path, log: Path, margin: float = 0.12,
                  min_side: int = 256) -> dict:
    """Crop every undistorted image (and its mask) to the object and give each its own camera.
    Depth maps then spend their pixels on the object, not the table."""
    import cv2

    boxes, areas = {}, []
    for img_path in sorted((work / "images").iterdir()):
        mask_path = mask_dir / (img_path.name + ".png")
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        box = mask_box(mask, margin, min_side)
        if box is None:
            areas.append(1.0)
            continue
        x0, y0, x1, y1 = box
        image = cv2.imread(str(img_path))
        cv2.imwrite(str(img_path), image[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(mask_path), mask[y0:y1, x0:x1])
        boxes[img_path.name] = box
        areas.append((x1 - x0) * (y1 - y0) / (mask.shape[0] * mask.shape[1]))

    txt = work / "sparse_txt"
    shutil.rmtree(txt, ignore_errors=True)
    txt.mkdir()
    run("model_converter", ["--input_path", work / "sparse", "--output_path", txt,
                            "--output_type", "TXT"], log)
    cameras, images = crop_cameras((txt / "cameras.txt").read_text(),
                                   (txt / "images.txt").read_text(), boxes)
    sparse = work / "sparse"
    for old in sparse.iterdir():
        old.unlink()
    (sparse / "cameras.txt").write_text(cameras)
    (sparse / "images.txt").write_text(images)
    shutil.copy(txt / "points3D.txt", sparse / "points3D.txt")
    shutil.rmtree(txt)
    return {"images": len(areas), "cropped": len(boxes),
            "median_area_kept": round(float(np.median(areas)), 3) if areas else 1.0}


def stereo(work: Path, log: Path, max_size: int = 2000, progress=None) -> float:
    """A depth and normal map per image (GPU), with geometric consistency (two passes)."""
    return run("patch_match_stereo", [
        "--workspace_path", work, "--workspace_format", "COLMAP",
        "--PatchMatchStereo.max_image_size", max_size,
        "--PatchMatchStereo.geom_consistency", "1"], log, progress, passes=2)


def fuse(work: Path, out_ply: Path, masks: Path | None, log: Path, progress=None) -> float:
    args = ["--workspace_path", work, "--workspace_format", "COLMAP",
            "--input_type", "geometric", "--output_path", out_ply]
    if masks is not None:
        args += ["--StereoFusion.mask_path", masks]
    return run("stereo_fusion", args, log, progress)
