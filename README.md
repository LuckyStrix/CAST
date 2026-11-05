# CAST

**C**apture, **A**lign, **S**urface, **T**exture: a phone video of an artifact in, a complete 3D model out.

Built for cuneiform tablets, picked up or still in the ground, and for capturers with no training.
It combines the phone capture of [FleshForge](https://github.com/LuckyStrix/FleshForge) with the
two-sided scanning of [Digital-Twins-of-Artifacts](https://github.com/LuckyStrix/Digital-Twins-of-Artifacts),
without a rig. The full plan is in [PLAN.md](PLAN.md).

**Status:** phone capture and upload work (Phase 1). 3D reconstruction is not built yet.

```
phone ──(invite link, Tailscale Funnel)──► capture site ──► worker ──► studio site (your tailnet)
```

- **Capture site** (public): a phone opens an invite link, follows the prompts and records. The video is
  sent while recording and kept on the phone until the server has all of it.
- **Studio site** (tailnet only): projects, invite links with QR codes, received captures.
- **Worker:** checks each upload. Reconstruction stages come next.

## Run it

Needs a Linux machine with Docker (a GPU becomes necessary in Phase 2) and a Tailscale account.

1. In the [Tailscale admin console](https://login.tailscale.com/admin): turn on **HTTPS certificates**
   (DNS page), allow **Funnel** in the access policy (`nodeAttrs` with `"attr": ["funnel"]`), and
   create an **auth key** (Settings → Keys).
2. Start it:
   ```sh
   cd docker
   cp .env.example .env    # paste the auth key into .env
   docker compose up -d --build
   ```
3. On any device on your tailnet open **`https://cast.<your-tailnet>.ts.net:8443`** (studio). Create a
   project, then an invite link, and scan its QR code with a phone. Phones need nothing installed and
   no Tailscale.

Phones: Safari on iPhone (iOS 16+) or Chrome on Android.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check cast tests
CAST_TRUST_LOCAL=1 CAST_PUBLIC_URL=http://127.0.0.1:8001 .venv/bin/cast-studio   # http://127.0.0.1:8002
.venv/bin/cast-capture                        # http://127.0.0.1:8001
.venv/bin/cast-worker                         # needs ffmpeg on PATH
python tests/fake_phone.py http://127.0.0.1:8001/c/<token> some.mp4 --drop 0.3
```

`CAST_TRUST_LOCAL=1` lets studio run without Tailscale. Never set it in the container. A desktop
browser can try the capture page on `localhost`, but phones need the HTTPS Funnel address for the camera.

## License

MIT. See `LICENSE`. Third-party licences: [LICENSES.md](LICENSES.md).
