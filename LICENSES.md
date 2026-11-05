# Licences

CAST is MIT. Everything shipped in the image must be permissive (see PLAN.md §1 and §6a).
Add every new dependency or model here **before** using it.

| Component | Used for | Licence | Status |
|---|---|---|---|
| FastAPI, Starlette | web apps | MIT / BSD-3 | in use |
| Uvicorn | web server | BSD-3 | in use |
| segno | invite QR codes | BSD-3 | in use |
| FFmpeg (Debian build) | probing and decoding video | LGPL-2.1+ / GPL parts | in use, run as a separate program only |
| tailscale/tailscale image | Funnel and Serve | BSD-3 | in use |
| Python 3.12 slim image | base image | PSF | in use |
| COLMAP 4.2.1 (colmap/colmap image, incl. its global mapper) | camera poses, dense stereo | BSD-3 | in use |
| NVIDIA CUDA runtime + cuDNN (in the images / pip wheels) | GPU | NVIDIA licences (free to use, not ours to redistribute) | in use, pulled at build time |
| SAM 2 | object masks | Apache-2.0 (code and weights) | planned, check weights |
| BiRefNet via rembg | object masks | MIT (model and rembg) | in use |
| ONNX Runtime | running the models | MIT | in use |
| Open3D, SciPy, OpenCV, NumPy, Pillow | meshing, images | MIT / BSD / Apache-2.0 / HPND | in use |
| three.js r170 (vendored) | studio viewer | MIT | in use |
| ALIKED (COLMAP's `aliked-n16rot.onnx`) | features for camera solving | BSD-3-Clause | in use |
| LightGlue (COLMAP's `aliked-lightglue.onnx`) | feature matching | Apache-2.0 | in use |
| mvs-texturing (texrecon) with mve, rayint, mapmap_cpu; Eigen (MPL-2-only mode) | texture from the photos | BSD-3 / MPL-2.0 | in use, built from pinned sources |
| OpenCV aruco | scale markers | Apache-2.0 | planned (Phase 5) |
| gsplat (2DGS) | fast surfaces | Apache-2.0 | planned (Phase 6) |
| VGGT commercial checkpoint / MapAnything | live coverage | to check | candidates (Phase 4) |

**Not allowed in the image** (non-commercial): SuperPoint official weights, MASt3R/DUSt3R,
Inria Gaussian splatting code and methods built on it (2DGS/PGSR/GOF reference code).
pymeshlab is GPL: never import it.
