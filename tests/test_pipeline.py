import json
import struct

import numpy as np
import pytest

from cast.common import db
from cast.pipeline import PipelineError, colmap, frames, glb, mesh
from cast.worker import loop


def test_passes_skip_the_turn_over():
    markers = [{"t": 0, "event": "start"}, {"t": 30, "event": "pass1_end"},
               {"t": 40, "event": "pass2_start"}, {"t": 70, "event": "stop"}]
    assert frames.passes(markers, 70.5, "object") == [(1, 0, 29.3), (2, 40.7, 70)]
    # in the ground: nothing moves, so the whole recording is one segment
    assert frames.passes(markers, 70.5, "in-situ") == [(1, 0, 70)]
    # no markers (e.g. an old capture): the whole video
    assert frames.passes([], 12, "object") == [(1, 0, 12)]
    # a pass too short to use is dropped, and the other keeps its real number
    short = [{"t": 2, "event": "pass1_end"}, {"t": 5, "event": "pass2_start"}]
    assert frames.passes(short, 30, "object") == [(2, 5.7, 30)]


def test_split_and_select():
    assert frames.split_count([(1, 0, 30), (2, 40, 70)], 160) == [80, 80]
    assert frames.split_count([(1, 0, 10), (2, 10, 100)], 100) == [20, 90]  # at least 20 each
    assert frames.select_sharpest([1, 9, 2, 3, 8, 1], 2) == [1, 4]
    assert frames.select_sharpest([1, 2], 5) == [0, 1]


def test_pairs_link_far_frames():
    names = [f"f{i}" for i in range(40)]
    pairs = set(colmap.match_pairs(names, window=2, stride=8))
    assert ("f1", "f2") in pairs and ("f1", "f3") in pairs and ("f1", "f4") not in pairs
    assert ("f5", "f32") in pairs  # every 8th frame links distant parts


def test_parse_images_txt():
    text = ("# header\n"
            "1 1 0 0 0 0 0 5 1 p1_000001.jpg\n\n"
            "2 0 0 1 0 1 0 0 1 p2_000001.jpg\n1 2 3\n")
    names, centres, ups, fwds = colmap.parse_images_txt(text)
    assert names == ["p1_000001.jpg", "p2_000001.jpg"]
    np.testing.assert_allclose(centres[0], [0, 0, -5])
    np.testing.assert_allclose(ups[0], [0, -1, 0])
    np.testing.assert_allclose(fwds[0], [0, 0, 1])


def test_look_at_and_up():
    # cameras on a circle all looking at (1, 2, 3)
    target = np.array([1.0, 2.0, 3.0])
    ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    centres = target + np.stack([np.cos(ang), np.zeros_like(ang), np.sin(ang)], 1) * 2
    np.testing.assert_allclose(mesh.look_at_point(centres, target - centres), target, atol=1e-9)
    for up in ([0, 0, 1], [1, 1, 0], [0, -1, 0], [0, 1, 0]):
        R = mesh.up_rotation(np.array(up, float))
        np.testing.assert_allclose(R @ (np.array(up) / np.linalg.norm(up)), [0, 1, 0], atol=1e-9)
        assert abs(np.linalg.det(R) - 1) < 1e-9


def test_glb_layout(tmp_path):
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    path = tmp_path / "t.glb"
    glb.write_glb(path, v, np.array([[0, 1, 2]]), colors_srgb=np.full((3, 3), 0.5))
    data = path.read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    jlen = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + jlen])
    assert doc["accessors"][0]["max"] == [1, 1, 0]
    with pytest.raises(ValueError):
        glb.write_glb(path, v, np.array([[0, 1, 5]]))


def _checked(cfg, token):
    cid = "6f1c2c1e-6b1f-4d7e-9a43-0b8c1f2d3e4a"
    with db.connect(cfg.db) as con:
        con.execute(
            "INSERT INTO captures (id, project_id, invite, mode, status, mime, meta, created,"
            " updated) VALUES (?, 1, ?, 'object', 'checked', 'video/mp4', ?, 0, 0)",
            (cid, token, json.dumps({"video": {"duration": 30}})))
    return cid


def _row(cfg, cid):
    with db.connect(cfg.db) as con:
        return con.execute("SELECT * FROM captures WHERE id = ?", (cid,)).fetchone()


def test_worker_reconstructs_and_reports(cfg, token, monkeypatch):
    cid = _checked(cfg, token)
    stages = []

    def fake(video, meta, mode, run_dir, progress):
        progress("frames", 0.5)
        row = _row(cfg, cid)
        stages.append(row["stage"])
        p = json.loads(row["progress"])
        assert p["stage"] == "frames" and 0 < p["fraction"] < 0.1 and p["eta_seconds"] > 0
        return {"poses": {"placed": 10}}

    monkeypatch.setattr("cast.pipeline.run.process", fake)
    assert not loop.step_check(cfg)  # the upload checker leaves checked captures alone
    assert loop.step_reconstruct(cfg)
    row = _row(cfg, cid)
    assert stages == ["frames"] and row["status"] == "done"
    assert json.loads(row["meta"])["report"]["poses"]["placed"] == 10


def test_worker_reports_pipeline_errors(cfg, token, monkeypatch):
    cid = _checked(cfg, token)

    def fail(*a, **k):
        raise PipelineError("The two sides could not be joined.")

    monkeypatch.setattr("cast.pipeline.run.process", fail)
    loop.step_reconstruct(cfg)
    row = _row(cfg, cid)
    assert row["status"] == "failed" and "joined" in row["error"]


def test_retry(studio, cfg, token):
    cid = _checked(cfg, token)
    assert studio.post(f"/api/captures/{cid}/retry").status_code == 409  # not finished yet
    with db.connect(cfg.db) as con:
        db.set_capture(con, cid, status="failed", error="x")
    assert studio.post(f"/api/captures/{cid}/retry").status_code == 200
    assert _row(cfg, cid)["status"] == "checked"
    assert studio.get(f"/api/captures/{cid}/model.glb").status_code == 404
    assert studio.get("/api/captures/../../etc/model.glb").status_code == 404


def test_process_killing_capture_is_not_retried_forever(cfg, token, monkeypatch):
    # The process dies mid-run (e.g. out of memory): the row stays 'processing' and the
    # restarted worker picks it up again. After MAX_ATTEMPTS starts it must give up.
    cid = _checked(cfg, token)

    def killed(*a, **k):
        raise SystemExit  # stands in for SIGKILL: nothing after this line runs

    monkeypatch.setattr("cast.pipeline.run.process", killed)
    for _ in range(loop.MAX_ATTEMPTS):
        with pytest.raises(SystemExit):
            loop.step_reconstruct(cfg)
        assert _row(cfg, cid)["status"] == "processing"
    loop.step_reconstruct(cfg)
    row = _row(cfg, cid)
    assert row["status"] == "failed" and row["error"] == loop.CRASHED
    assert not loop.step_reconstruct(cfg)  # the queue moves on


def test_heartbeat_thread(tmp_path):
    import time

    beat = tmp_path / "hb"
    loop.heartbeat(beat, every=0.05)
    time.sleep(0.2)
    assert beat.exists()


def test_tracker_eta():
    now = [0.0]
    t = loop.Tracker(["frames", "poses", "dense"], {"frames": 10, "poses": 20, "dense": 70},
                     clock=lambda: now[0])
    p = t.update("frames", 0.0)
    assert p["fraction"] == 0 and p["eta_seconds"] == 100
    now[0] = 40  # poses: half done in 30 s, so about 30 s left here, plus 70 for dense
    t.update("poses", 0.0)
    now[0] = 70
    p = t.update("poses", 0.5)
    assert p["eta_seconds"] == 100 and p["fraction"] == 0.2
    p = t.update("dense", 0.0)  # a new stage starts from its estimate
    assert p["eta_seconds"] == 70


def test_estimates_learn_from_finished_runs(cfg, token):
    cid = _checked(cfg, token)
    with db.connect(cfg.db) as con:
        db.set_capture(con, cid, status="done",
                       meta={"report": {"seconds": {"dense": 300, "frames": 20}}})
    est = loop.stage_estimates(cfg)
    assert est["dense"] == 300 and est["frames"] == 20 and est["mesh"] == loop.DEFAULT_SECONDS["mesh"]


def test_colmap_progress_lines():
    from cast.pipeline.colmap import line_fraction

    assert line_fraction("I1105 Processed file [40/160]") == 0.25
    assert line_fraction("Matching block [2/4, 1/4]") == 5 / 16
    assert line_fraction("Processing view 80 / 160 for p1_000080.jpg") == 0.5
    assert line_fraction("Registering image #12 (30)", total_images=60) == 0.5
    assert line_fraction("Registering image #12 (30)") is None
    assert line_fraction("nothing here") is None


def test_crop_cameras_moves_principal_point_and_points():
    cams = "# c\n1 PINHOLE 3200 1800 2500.0 2500.0 1600.0 900.0\n"
    imgs = ("# i\n"
            "1 1 0 0 0 0 0 0 1 p1_000001.jpg\n100.0 200.0 7 1700.5 950.25 -1\n"
            "2 1 0 0 0 0 0 0 1 p1_000002.jpg\n")
    c, i = colmap.crop_cameras(cams, imgs, {"p1_000001.jpg": (1000, 500, 2200, 1400)})
    c_lines, i_lines = c.splitlines(), i.splitlines()
    assert c_lines[0] == "1 PINHOLE 1200 900 2500.0 2500.0 600.0 400.0"
    assert c_lines[1] == "2 PINHOLE 3200 1800 2500.0 2500.0 1600.0 900.0"  # no box: whole
    assert i_lines[0].split()[8] == "1" and i_lines[2].split()[8] == "2"  # own camera each
    assert i_lines[1].split() == ["-900.0", "-300.0", "7", "700.5", "450.25", "-1"]
    assert i_lines[3] == ""


def test_mask_box():
    m = np.zeros((1000, 2000), np.uint8)
    m[400:600, 900:1100] = 255
    assert colmap.mask_box(m, 0.1, 100) == (880, 380, 1119, 619)  # object 900..1099 + 19.9
    assert colmap.mask_box(m, 0.1, 300) is None  # too small: keep the whole image
    assert colmap.mask_box(np.zeros((10, 10), np.uint8), 0.1, 1) is None


def test_pass_overlap_counts_shared_points():
    images = ("1 1 0 0 0 0 0 0 1 p1_000001.jpg\n\n2 1 0 0 0 0 0 0 1 p1_000002.jpg\n\n"
              "3 1 0 0 0 0 0 0 1 p2_000001.jpg\n\n")
    points = ("# id x y z r g b err track\n"
              "1 0 0 0 0 0 0 0.5 1 0 2 0\n"  # side 1 only
              "2 0 0 0 0 0 0 0.5 1 1 3 4\n"  # both sides
              "3 0 0 0 0 0 0 0.5 3 9\n")  # side 2 only
    assert colmap.pass_overlap(images, points) == {"shared": 1, "p1": 1, "p2": 1}
