# CAST

**C**apture, **A**lign, **S**urface, **T**exture: a phone video of an artifact in, a complete 3D model out.

Built for cuneiform tablets. It combines the phone capture of
[FleshForge](https://github.com/LuckyStrix/FleshForge) with the two-sided scanning of
[Digital-Twins-of-Artifacts](https://github.com/LuckyStrix/Digital-Twins-of-Artifacts), without a rig.

**Status:** planning. No code yet.

## Plan

**Capture (web, phone to PC).** The PC serves a capture page over Tailscale. The phone films and
uploads as it goes, so the PC has the footage when recording stops. A progress bar shows when there is
enough coverage: a rough estimate from the phone's motion sensors, then a quick camera solve on the PC.

**Modes**
- `object`: film round the tablet, flip it, film round again. The tablet is masked out of every frame
  (SAM2) and the flip is detected and dropped. The two passes are joined through the tablet's edges.
- `in-situ`: a tablet still in the ground. Nothing is masked; the tablet is labelled after
  reconstruction. A later `object` scan can be aligned into it to record exactly how it lay.

**Reconstruction**
- Live coverage: a feed-forward model (VGGT or MASt3R).
- Camera poses: SuperPoint + LightGlue features, GLOMAP mapping.
- Surface: 2DGS/PGSR, compared with COLMAP dense stereo for wedge detail.
- Coverage check: areas seen by too few views are flagged, not invented.

## License

MIT. See `LICENSE`.
