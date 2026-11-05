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
| COLMAP / GLOMAP | camera poses, dense stereo | BSD-3 | planned (Phase 2) |
| SAM 2 | object masks | Apache-2.0 (code and weights) | planned, check weights |
| BiRefNet | first mask | MIT | planned, check weights |
| LightGlue (+ ALIKED) | feature matching | Apache-2.0 (+ BSD-3) | planned, check weights |
| hloc | matching glue | Apache-2.0 | planned |
| Open3D | alignment, meshing | MIT | planned |
| OpenCV (incl. aruco) | images, markers | Apache-2.0 | planned |
| gsplat (2DGS) | fast surfaces | Apache-2.0 | planned (Phase 6) |
| trimesh | export | MIT | planned |
| VGGT commercial checkpoint / MapAnything | live coverage | to check | candidates (Phase 4) |

**Not allowed in the image** (non-commercial): SuperPoint official weights, MASt3R/DUSt3R,
Inria Gaussian splatting code and methods built on it (2DGS/PGSR/GOF reference code).
pymeshlab is GPL: never import it.
