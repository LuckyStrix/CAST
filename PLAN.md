# CAST: build plan

**C**apture, **A**lign, **S**urface, **T**exture. A phone video of an artifact in, a complete 3D
model out. The first target is cuneiform tablets, either lifted or still in the ground.

This is the working plan for building CAST with Claude Code. Each phase ends in something that runs and
can be checked. Work top to bottom and don't start a phase until the one before it meets its "done when".

---

## 1. What CAST must be

1. **Usable by someone with no training.** A volunteer at a dig in any country opens a link on their
   phone, follows the prompts, and gets a good scan. They never see a setting, a file or a term like
   "SfM".
2. **Easy for anyone to host.** One GPU PC, `docker compose up`, one Tailscale auth key, and a setup
   page in the browser. Today the only host is the maintainer, but nothing may assume that.
3. **Web only.** There are two sites. Nothing is installed on the phone and nothing to learn on the PC.
   - **Capture site** (phone): public through Tailscale Funnel and locked down to one job, receiving
     scans.
   - **Studio site** (PC or any tailnet device): projects, invites, progress, the 3D viewer, downloads.
     Reachable on the tailnet only.
4. **Any iPhone or Android.** Safari on iOS 16+ and Chrome on Android 10+ are the targets, with no
   features that only one of them has unless there is a fallback.
5. **Honest output.** CAST never shows invented surface as real. Unseen areas are flagged and the
   capturer is told how to fix them.
6. **Permissive licences only.** Every component (code and model weights) must be MIT, BSD, Apache-2.0
   or similar in the shipped image. Non-commercial tools are allowed only as opt-in **research add-ons**
   (section 6a). GPL tools may only be called as separate programs, never imported.
7. **Simple code and docs.** One maintainer. Plain Python backend, plain HTML/JS frontend with no build
   step, short READMEs. Pick the boring option.

---

## 2. Architecture

```
 phone (Safari / Chrome)                         host PC (one docker compose)
 ─────────────────────                           ────────────────────────────────────────────
                         HTTPS, Tailscale Funnel ┌ tailscale  (sidecar: funnel :443, serve :8443)
  capture site  ───────────────────────────────► │    │
  (invite link)                                  │    ├─► capture   (FastAPI, public, upload-only)
                                                 │    │        │ writes /data/inbox
  studio site   ◄───── tailnet only (:8443) ──── │    └─► studio    (FastAPI, tailnet-only)
  (any tailnet device)                           │             │ SQLite job queue
                                                 └     worker   (GPU: the pipeline) ◄─┘
                                                       /data volume: projects, runs, models
```

- **One image, three processes** (`capture`, `studio`, `worker`) plus the official `tailscale/tailscale`
  sidecar. They share `/data` and the SQLite database (`/data/cast.db`).
- **The capture process can't do anything admin.** It runs separately, on its own port, with its own
  small set of routes. If it were compromised it could only write uploads to the inbox.
- **The worker** takes one job at a time from SQLite (no Redis, no Celery). Each stage writes to its run
  folder and records its inputs, so a rerun continues where it stopped (the stage caching from
  FleshForge).
- **The frontend** is static HTML, CSS and vanilla JS. three.js is vendored for the viewer and the
  capture pages have no framework.
- **Languages:** every user-facing string lives in `web/i18n/<lang>.json` from day one. English first,
  others added by contributors. Icons and illustrations come before words.

### Repo layout (target)

```
cast/
  capture/        capture app (routes, tokens, upload handling)
  studio/         studio app (projects, invites, runs, viewer API)
  worker/         job loop
  pipeline/       stages, one module each; pure logic, no web code
  common/         db, config, storage paths, logging
web/
  capture/        phone pages (guide, record, upload, status)
  studio/         PC pages (setup, projects, run, viewer)
  i18n/           strings
docker/           Dockerfile, compose.yml, tailscale serve config
tests/            unit tests, synthetic tablet, fake phone
LICENSES.md       every dependency and model, its licence, where it comes from
```

---

## 3. Networking and security (Funnel done safely)

Funnel makes the capture site reachable from the public internet, so it's treated as hostile ground.

- **Only capture is funnelled.** In the sidecar's serve config, `:443` goes to `capture` with
  `AllowFunnel`, and `:8443` goes to `studio` without it, so studio is reachable on the tailnet only.
  Studio also requires the `Tailscale-User-Login` header that `tailscale serve` adds for tailnet users
  (Funnel traffic doesn't carry it), and binds only to the container network so the header can't be
  spoofed from elsewhere. *Check both against current Tailscale docs in Phase 0.*
- **Invite links.** Every capture needs a token: 128-bit random, tied to one project, with an expiry,
  an optional upload limit and a revoke button. Studio shows the link as a QR code. No token, no page
  (it just says "ask your organiser for a link").
- **Upload limits:** maximum size per capture (default 2 GB) and duration (default 5 min), uploaded in
  chunks of at most 8 MB, rate-limited per token and per IP. The server picks every file name, never the
  client.
- **Content checks:** the worker (not capture) runs `ffprobe` and then `ffmpeg` on the upload, with
  time and memory limits, and rejects anything that isn't a single video stream. Metadata, including
  GPS, is stripped from stored frames.
- **Privacy and heritage safety:**
  - Before recording, a consent screen covers who is filming and whether they are allowed to scan this
    object.
  - A location is stored only if the organiser turns it on for the project, and is never shown on
    capture pages. Find spots of buried objects are attractive to looters.
  - Faces of bystanders are not kept: the masks remove everything that isn't the object.
- **Secrets:** the Tailscale auth key and studio admin settings live in `.env`. `.env` is gitignored and
  the setup page writes it.
- **Updates:** images are pinned by digest, and a `cast update` script pulls new images and restarts.

---

## 4. The capture experience (phone)

Designed for a first-time user in bright sun with one hand free.

1. **Open the link** (QR code from the organiser). The page loads in under 2 s on 3G and is cached by a
   service worker afterwards.
2. **Choose a mode,** shown as two big pictures: "Object I can pick up" (`object`) or "Object in the
   ground" (`in-situ`).
3. **Checklist:** three illustrated cards covering shade or even light, a plain contrasting surface, and
   gloves for `object` mode (coloured nitrile, which also makes hands easy to remove).
4. **Allow the camera and motion sensors.** On iOS motion permission needs a tap
   (`DeviceMotionEvent.requestPermission()`); without it the app falls back to camera-only guidance.
5. **Record, with live guidance:**
   - **Progress ring** for the current pass, filled by how far the phone has turned (gyroscope).
     Replaced by the server's coverage estimate when connected (Phase 4).
   - **Live checks on the phone,** on downscaled frames in a canvas: too dark, too blurry ("slow down"),
     object too small ("move closer"). Shown as one short message plus an icon, with a sound or
     vibration (vibration on Android only; iOS gets sound).
   - **`object` mode:** at the end of pass 1 the phone says **"Flip it over"** and shows an animation.
     The ring resets for pass 2.
   - **`in-situ` mode:** one loop low, one loop higher. No flip.
   - **Done** lights up when coverage is enough. Users can stop early, but are warned.
6. **Upload while recording.** `MediaRecorder` chunks (H.264 MP4 on Safari, MP4 or WebM on Chrome) are
   uploaded with resumable, numbered chunk uploads. Chunks are also kept in IndexedDB until the server
   confirms them, so a dropped connection or a reload loses nothing.
7. **Offline:** if there's no connection, everything goes to IndexedDB with phone-only guidance, and
   the page uploads when back online. iOS may clear stored data after about a week without use, so the
   page says "keep this page and open it when you have signal".
8. **Status page:** "Received. Processing." with a progress bar, then a small 3D preview and either "All
   good, thank you!" or one clear redo request ("The bottom is missing. Please film one more pass,
   tilted down.").

**Known phone limits to design around:**
- iOS Safari ignores most exposure, focus and white-balance locks. Ask for even light and normalise
  colour on the server.
- A page in the background stops recording, so keep the screen awake with the Wake Lock API (iOS 16.4+,
  Android Chrome). Older phones get a "keep the screen on" message.
- Highest resolution is around 1080p or 4K depending on the phone. Request 4K at 30 fps and accept what
  the phone gives.

---

## 5. The studio experience (PC)

- **First-run setup page:** GPU check, disk check, Tailscale status (shows the public capture URL),
  and a test scan of the bundled sample video. Every failure gives a plain fix ("NVIDIA Container
  Toolkit not found → install with this one command").
- **Projects:** name, mode default, invite links and QR codes, list of captures.
- **Run page:** a stage-by-stage progress bar with time remaining, the live log behind a "details"
  toggle, and a retry button.
- **Viewer:** three.js viewer showing the model with coverage overlaid (red = not seen). There are
  lighting presets, including raking light for reading wedges, and a ruler when scale is known.
- **`in-situ` tools:** click the tablet to label it. Pick a lifted `object` scan to align into an
  `in-situ` scan.
- **Downloads:** GLB (viewing), PLY and OBJ (research), preview PNGs, and `report.json` (all numbers
  and settings). A zip of everything for archiving.

---

## 6. The pipeline (worker)

Every stage is a module in `cast/pipeline/` with `run(run_dir, inputs) -> outputs`. Stage inputs and
outputs are recorded in `state.json` (the caching rules from FleshForge, including identifying input
files by content).

| # | Stage | What it does | Main tools (licence) |
|---|---|---|---|
| 1 | ingest | probe, normalise video, strip metadata | ffmpeg (LGPL build, run as a separate program) |
| 2 | frames | pull candidate frames, keep the sharpest per time window, normalise colour | ffmpeg, OpenCV (Apache-2.0) |
| 3 | segment | split into passes: detect the flip (mask jump, blur, hands), drop flip frames | OpenCV, own code |
| 4 | masks | object mask tracked through each pass; hands subtracted | SAM 2 (Apache-2.0), seeded by BiRefNet (MIT) |
| 5 | features | keypoints and matches, including across passes | ALIKED (BSD-3) or SIFT + LightGlue (Apache-2.0), via hloc (Apache-2.0) |
| 6 | poses | camera solve; both passes together if they connect | GLOMAP / COLMAP global mapper (BSD) |
| 6b | join (fallback) | solve each pass alone, then FPFH + RANSAC + ICP | Open3D (MIT) |
| 7 | scale | ArUco card → millimetres; otherwise flag "no scale" | OpenCV aruco (Apache-2.0) |
| 8 | dense | depth and surface | COLMAP PatchMatch (BSD); 2DGS via gsplat (Apache-2.0) after benchmarking (Phase 6) |
| 9 | mesh | Poisson or TSDF, close openings, decimate | Open3D; mesh repair ported from FleshForge |
| 10 | coverage | per-face count of good views; mark unseen and filled areas; pick the redo hint | own code |
| 11 | texture | vertex colours first, texture atlas later | Open3D / own |
| 12 | export | GLB, PLY, OBJ, previews, report | trimesh (MIT), own GLB writer from FleshForge |

**Licence traps:**
- **SuperPoint's official weights are non-commercial.** Use ALIKED or SIFT with LightGlue.
- **The original Inria Gaussian splatting code** (and 2DGS/PGSR built on it) is non-commercial. Use
  gsplat's implementation.
- **DUSt3R/MASt3R are CC BY-NC.** Excluded.
- **Neural nets for live coverage** (Phase 4): VGGT's commercial-use checkpoint and MapAnything are
  candidates. **Read each licence before use** and record it in `LICENSES.md`.
- **pymeshlab is GPL.** Don't import it. Use Open3D, or call MeshLab as a separate program if really
  needed.

### 6a. Research add-ons (opt-in, non-commercial)

CAST itself stays MIT and ships only permissive components. Tools under non-commercial licences
(MASt3R / MASt3R-SfM, Inria-based surface methods such as 2DGS/PGSR/GOF) are optional add-ons:

- **Never in the image or the repo.** Each add-on is a small manifest in `addons/<name>.toml`
  giving the source URL, a pinned version or hash, the licence name and link, and which stage it
  replaces or adds.
- **The host opts in.** Studio → Settings → Add-ons shows the licence text and a plain summary ("not for
  commercial use, including paid scanning work"). Clicking "I accept" records who accepted and when,
  then downloads into `/data/addons/`. Nothing happens without that click.
- **Clearly marked.** A run that used an add-on records it in `report.json` and in a note shown with the
  model in studio, and the downloaded zip includes the add-on's licence. Anyone receiving a model can
  then see what terms touched it.
- **Same stage interface.** An add-on is a drop-in for one pipeline stage (e.g. `poses` via
  MASt3R-SfM) and runs as a separate program, so its code never mixes with CAST's.
- **Only if it earns its place.** An add-on is added only after a benchmark shows it beats the
  permissive default on real captures (first candidate: MASt3R for joining `object` passes, tested in
  Phase 2).

**`in-situ` mode differences:** stage 4 is skipped (no masks) and stage 3 has nothing to split. Stage
10 counts coverage for the whole scene. A **label** step (studio click → SAM 2 → 3D labels) and an
**align-lifted** step (ICP of the lifted `object` model onto the labelled tablet) run on request.

**Quality gates (refuse bad scans, as FleshForge does):** stop before the dense stage if under 80% of
frames have a camera position, or if the passes don't connect and the fallback alignment fails. Each
gate produces a plain-language redo message for the phone status page.

---

## 7. Phases

### Phase 0: groundwork (small)
- Compose file with tailscale sidecar, `capture` and `studio` "hello" pages, and the serve/funnel
  config. Prove: phone on cellular reaches capture over Funnel; studio can't be reached over Funnel; a
  tailnet laptop reaches studio.
- Write `LICENSES.md` and check every component above.
- SQLite schema: projects, invites, captures, runs, stages.
- **Done when:** `docker compose up` on a fresh Linux GPU box gives both sites and the security checks
  above pass.

### Phase 1: capture site, uploads only
- Invite tokens and QR codes in studio; capture page with checklist, record, chunked resumable upload,
  IndexedDB buffer, status page.
- Phone-only guidance: gyroscope ring, blur, brightness and size checks, flip prompt.
- `tests/fake_phone.py` uploads a video the way a phone would (dropped chunks, reloads).
- **Done when:** a 3-minute capture from an iPhone and an Android phone, on cellular, with the
  connection cut mid-way, arrives complete and plays.

### Phase 2: `object` pipeline, offline
- Stages 1–12 with COLMAP dense stereo. Build the **synthetic tablet**: a rendered clay slab with
  wedge marks, filmed in two passes with a flip and fake gloved hands, for automated tests.
- **Done when:** the synthetic tablet comes back closed with both faces, and 3 real tablet videos give
  watertight models with honest coverage maps. Write down any failures.
- Benchmark joining the passes with and without the MASt3R add-on (section 6a). Build the add-on
  mechanism only if MASt3R clearly wins.

### Phase 3: studio
- Run progress, viewer with coverage overlay, downloads, retry, setup page, bundled sample video.
- **Done when:** someone who has never seen CAST sets it up from the README and runs the sample scan
  without help.

### Phase 4: live coverage
- While uploading, the worker solves a rough set of camera positions every few seconds on keyframes
  (neural net from Phase 0's licence check, or GLOMAP on a small set if none passes). It sends a
  coverage sphere and "flip now" or "done" back to the phone.
- **Done when:** the Done light only comes on for captures that then pass every quality gate (test
  on 10 captures).

### Phase 5: `in-situ` mode
- Unmasked pipeline, marker targets for scale and "up", studio labelling, align-lifted step.
- **Done when:** a tablet half-buried in a sand tray is scanned in place, lifted, scanned again, and the
  lifted model lines up with the exposed surface to within 1 mm.

### Phase 6: speed
- Benchmark on the same captures: COLMAP dense stereo vs 2DGS (gsplat), measuring time, GPU memory,
  and wedge depth against a reference (a known printed test tablet, or the Digital-Twins rig).
- Make the faster one the default only if its wedge depth is within tolerance; keep the other as an
  option.
- **Done when:** the default path runs in under 15 min for a 2-minute capture on a 12 GB GPU.

### Phase 7: worldwide readiness
- Two more languages, tested by native speakers; usability test with 5 untrained people (watch, don't
  help); Windows host notes (Docker Desktop + WSL2 GPU); a one-page printable field guide with QR space.
- **Done when:** 4 of 5 untrained testers produce a passing scan on their first try.

---

## 8. Testing

- **Unit:** pure pipeline logic, token rules, chunk reassembly, state caching. Fast, no GPU.
- **Synthetic end-to-end:** the synthetic tablet through the whole pipeline. Skipped without a GPU.
- **Fake phone:** uploads, drops, reloads, expired and revoked tokens, oversize files, wrong file
  types.
- **Security checks in CI:** studio routes return 404 through the capture port; capture refuses
  without a token; path traversal attempts fail.
- **Real scans** are never committed (people's hands, and objects that may have legal limits on
  sharing). Keep them in `runs/`, which is gitignored.
- CI: `ruff` + `pytest` on every push (GitHub Actions, CPU only).

---

## 9. Hardware

- **Host:** Linux with an NVIDIA GPU (12 GB+ VRAM recommended, 8 GB minimum for the slow path), 32 GB
  RAM, 200 GB disk, Docker + NVIDIA Container Toolkit. Windows through Docker Desktop and WSL2 comes
  later (Phase 7).
- **Phone:** iOS 16+ Safari or Android 10+ Chrome, 1080p camera or better.
- **Optional:** a printed ArUco scale card (PDF in the repo) and coloured nitrile gloves.

---

## 10. Risks and open questions

| Risk | Plan |
|---|---|
| The two `object` passes don't connect through thin or worn edges | Fallback alignment (6b); capture prompt asks for a slow edge-on sweep before the flip; measure in Phase 2 |
| iOS web limits (no exposure lock, background suspension, storage eviction) | Even-light checklist, Wake Lock, upload while recording, clear messages |
| No permissive neural net is good enough for live coverage | Fall back to GLOMAP on a small set of keyframes; slower updates but still works |
| Funnel misconfiguration exposes studio | Separate process and port, identity-header check, CI test, check at every Phase 0 change |
| Phone video quality too low for fine wedges | Report the measured detail level honestly; recommend 4K; the Digital-Twins rig stays the high-precision option |
| Abuse of public capture links | Expiring, revocable, per-project tokens; size and rate limits; nothing is ever served back publicly except the uploader's own status |

**Open, to decide later:**
- Whether studio should export directly to a heritage archive (e.g. an IIIF 3D or Sketchfab-style
  publish step).
- Whether a central "hub" mode (many hosts feeding one archive) is wanted.
