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
    assert frames.passes(markers, 70.5, "object") == [(0, 29.3), (40.7, 70)]
    # in the ground: nothing moves, so the whole recording is one segment
    assert frames.passes(markers, 70.5, "in-situ") == [(0, 70)]
    # no markers (e.g. an old capture): the whole video
    assert frames.passes([], 12, "object") == [(0, 12)]
    # a pass too short to use is dropped
    short = [{"t": 2, "event": "pass1_end"}, {"t": 5, "event": "pass2_start"}]
    assert frames.passes(short, 30, "object") == [(5.7, 30)]


def test_split_and_select():
    assert frames.split_count([(0, 30), (40, 70)], 160) == [80, 80]
    assert frames.split_count([(0, 10), (10, 100)], 100) == [20, 90]  # at least 20 each
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
        progress("frames")
        stages.append(_row(cfg, cid)["stage"])
        return {"poses": {"placed": 10}}

    monkeypatch.setattr("cast.pipeline.run.process", fake)
    assert not loop.step(cfg)  # without COLMAP, checked captures wait
    assert loop.step(cfg, can_reconstruct=True)
    row = _row(cfg, cid)
    assert stages == ["frames"] and row["status"] == "done"
    assert json.loads(row["meta"])["report"]["poses"]["placed"] == 10


def test_worker_reports_pipeline_errors(cfg, token, monkeypatch):
    cid = _checked(cfg, token)

    def fail(*a, **k):
        raise PipelineError("The two sides could not be joined.")

    monkeypatch.setattr("cast.pipeline.run.process", fail)
    loop.step(cfg, can_reconstruct=True)
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
