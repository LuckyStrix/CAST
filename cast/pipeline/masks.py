"""Object masks (`object` mode): white = the object, black = everything else.

When the object is turned over, the table and room no longer agree with it, so camera poses
must come from the object alone: COLMAP reads these masks when extracting features and again
when fusing depth maps. COLMAP's naming: the mask for `p1_000001.jpg` is `p1_000001.jpg.png`.

Model: BiRefNet (MIT) through rembg (MIT), on the GPU when onnxruntime can use it.
"""

from pathlib import Path

import numpy as np

from cast.pipeline import PipelineError

MODEL = "birefnet-general-lite"
ERODE_PX_PER_1000 = 3  # pull the edge in: edge pixels are part background


def session():
    try:
        import onnxruntime

        if hasattr(onnxruntime, "preload_dlls"):
            onnxruntime.preload_dlls()  # CUDA/cuDNN from the nvidia-* pip packages
        from rembg import new_session

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return new_session(MODEL, providers=providers)
    except Exception as e:  # noqa: BLE001 - model missing or runtime broken: an operator problem
        raise PipelineError(f"The masking model could not be loaded: {e}") from None


def make_masks(images: Path, out: Path, sess=None) -> dict:
    import cv2
    from PIL import Image
    from rembg import remove

    sess = sess or session()
    out.mkdir(parents=True, exist_ok=True)
    coverage = []
    for path in sorted(images.glob("*.jpg")):
        im = Image.open(path).convert("RGB")
        mask = np.asarray(remove(im, session=sess, only_mask=True).convert("L")) > 127
        k = max(1, round(ERODE_PX_PER_1000 * max(im.size) / 1000))
        mask = cv2.erode(mask.astype(np.uint8), np.ones((2 * k + 1, 2 * k + 1), np.uint8)) > 0
        Image.fromarray((mask * 255).astype(np.uint8)).save(out / (path.name + ".png"))
        coverage.append(float(mask.mean()))
    if not coverage:
        raise PipelineError("No frames to mask.")
    median = float(np.median(coverage))
    if median < 0.01:
        raise PipelineError("The object could not be found in the frames. Fill more of the "
                            "picture with it, on a plain surface of a different colour.")
    return {"model": MODEL, "images": len(coverage), "coverage_median": round(median, 3),
            "empty": int(sum(c < 0.005 for c in coverage))}
