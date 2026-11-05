"""The worker: one job at a time, found by polling SQLite.

Jobs, in order: check each received upload is one real video within limits, then (in the GPU
image, where COLMAP is installed) reconstruct checked captures one at a time.
"""

import json
import logging
import shutil
import subprocess
import time

from cast.common import config, db

log = logging.getLogger("cast.worker")
STALE_DAYS = 14  # an upload not touched for this long is given up and its chunks deleted


def probe(path, timeout=60) -> dict:
    """ffprobe the file. Raises ValueError with a message a capturer can understand."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
             "-show_format", str(path)],
            capture_output=True, timeout=timeout, check=True, text=True,
        ).stdout
    except subprocess.CalledProcessError:
        raise ValueError("The video could not be read. Please record again.") from None
    except subprocess.TimeoutExpired:
        raise ValueError("The video could not be read in time. Please record again.") from None
    info = json.loads(out)
    video = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if len(video) != 1:
        raise ValueError("The upload is not a single video. Please record again.")
    v = video[0]
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    if not duration:  # browser-recorded WebM has no duration in its header: read the last frame time
        try:
            times = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "packet=pts_time", "-of", "csv=p=0", str(path)],
                check=False, capture_output=True, timeout=timeout * 5, text=True,
            ).stdout.split()
        except subprocess.TimeoutExpired:
            raise ValueError("The video could not be read in time. Please record again.") from None
        duration = max((float(t) for t in times if t.replace(".", "", 1).isdigit()), default=0)
    return {
        "codec": v.get("codec_name"),
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": v.get("avg_frame_rate"),
        "duration": round(duration, 2),
    }


def check(cfg: config.Config, cap) -> None:
    path = db.video_path(cfg.inbox, cap["id"], cap["mime"])
    with db.connect(cfg.db) as con:
        try:
            p = probe(path)
            if min(p["width"], p["height"]) < 480:
                raise ValueError("The video is too small. Use the main camera at full quality.")
            if p["duration"] < 5:
                raise ValueError("The video is too short. Walk all the way round the object.")
            if p["duration"] > cfg.max_capture_seconds + 10:
                raise ValueError("The video is too long.")
        except ValueError as e:
            db.set_capture(con, cap["id"], status="rejected", error=str(e))
            return
        meta = json.loads(cap["meta"])
        meta["video"] = p
        db.set_capture(con, cap["id"], status="checked", meta=meta)


def drop_stale(cfg: config.Config) -> None:
    cutoff = time.time() - STALE_DAYS * 86400
    with db.connect(cfg.db) as con:
        rows = con.execute(
            "SELECT id FROM captures WHERE status = 'uploading' AND updated < ?", (cutoff,)
        ).fetchall()
        for r in rows:  # mark first, so a late chunk is refused rather than re-creating files
            db.set_capture(con, r["id"], status="abandoned", error="Upload never finished.")
            shutil.rmtree(cfg.inbox / r["id"], ignore_errors=True)


def reconstruct(cfg: config.Config, cap) -> None:
    from cast.pipeline import PipelineError
    from cast.pipeline.run import process

    cid = cap["id"]
    with db.connect(cfg.db) as con:
        db.set_capture(con, cid, status="processing", error="")

    def progress(stage):
        with db.connect(cfg.db) as con:
            db.set_capture(con, cid, stage=stage)

    meta = json.loads(cap["meta"])
    try:
        report = process(db.video_path(cfg.inbox, cid, cap["mime"]), meta, cap["mode"],
                         cfg.runs / cid, progress)
    except PipelineError as e:
        with db.connect(cfg.db) as con:
            db.set_capture(con, cid, status="failed", error=str(e))
        return
    meta["report"] = report
    with db.connect(cfg.db) as con:
        db.set_capture(con, cid, status="done", stage="", meta=meta)


def step(cfg: config.Config, can_reconstruct: bool = False) -> bool:
    """Do one job if there is one. True if something was done."""
    with db.connect(cfg.db) as con:
        cap = con.execute(
            "SELECT * FROM captures WHERE status = 'received' ORDER BY updated LIMIT 1"
        ).fetchone()
        job = check
        if cap is None and can_reconstruct:
            # 'processing' first: a job the worker was doing when it last stopped
            cap = con.execute(
                "SELECT * FROM captures WHERE status IN ('processing', 'checked')"
                " ORDER BY status = 'processing' DESC, updated LIMIT 1"
            ).fetchone()
            job = reconstruct
    if cap is None:
        return False
    try:
        job(cfg, cap)
    except Exception:  # never let one bad job crash-loop the worker and block the queue
        log.exception("%s of capture %s failed", job.__name__, cap["id"])
        with db.connect(cfg.db) as con:
            if job is check:
                db.set_capture(con, cap["id"], status="rejected",
                               error="The server could not check this video. Please record again.")
            else:  # a bug or crash, not the capture's fault: keep it retryable from studio
                db.set_capture(con, cap["id"], status="failed",
                               error="Reconstruction crashed. See the worker log, then Retry.")
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    db.init(cfg.db)
    from cast.pipeline import colmap

    can_reconstruct = colmap.available()
    log.info("worker started; reconstruction %s", "on" if can_reconstruct else "off (no COLMAP)")
    beat = cfg.data / "worker.heartbeat"
    last_sweep = 0.0
    while True:
        beat.touch()
        if time.time() - last_sweep > 3600:
            drop_stale(cfg)
            last_sweep = time.time()
        if not step(cfg, can_reconstruct):
            time.sleep(2)
