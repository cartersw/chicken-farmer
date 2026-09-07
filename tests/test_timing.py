"""Native submission provenance tests; fixture timestamps remain diagnostic."""
import json
import hashlib
import struct

import pytest

from cs2_data.align import validate_timing
from cs2_data.io import sha256_file
from cs2_data.timing import (capture_boundaries, prepare_timing, verify_capture_evidence,
                            tga_rgb24, verify_readback_pixels, render_boundaries)


def records():
    return [{"event": "header", "schema_version": 1, "hook": "engine2.CMovieRecorder.movie_frame_submit",
             "engine_sha256": "a"*64, "pixel_correspondence_validated": False,
             "clock": "IDemoFile.GetDemoTick", "fraction_available": False},
            {"event": "movie_frame", "movie_name": "nonce_", "capture_index": 0,
             "counter_after": 1, "tga_filename": "nonce_00000000.tga", "replay_demo_tick": 100},
            {"event": "movie_frame", "movie_name": "nonce_", "capture_index": 1,
             "counter_after": 2, "tga_filename": "nonce_00000001.tga", "replay_demo_tick": 102},
            {"event": "movie_end", "movie_name": "nonce_", "next_capture_index": 2, "replay_demo_tick": 104}]


def render():
    return {"schema_version": 1, "demo_id": "demo", "clip_id": "stable", "round_id": 1,
            "player_slot": 2, "steam_id": "76561198000000001", "num_frames": 2,
            "capture_prefix": "nonce", "width": 640, "height": 360, "fps": 32,
            "video_uri": "stable.mp4", "video_sha256": "fixture", "pov_verified": False,
            "capture_frame_files": "capture_frame_files.json"}


@pytest.mark.parametrize("mutate,match", [
    (lambda rows: rows.pop(), "endpoint"),
    (lambda rows: rows[2].update(capture_index=2), "counters"),
    (lambda rows: rows[2].update(replay_demo_tick=100), "repeat/reverse"),
    (lambda rows: rows[3].update(replay_demo_tick=101), "repeat/reverse"),
    (lambda rows: rows[2].update(tga_filename="other_00000001.tga"), "filename"),
    (lambda rows: rows[1].update(movie_name="wrong_"), "identity"),
    (lambda rows: rows[0].update(hook="FRAME_STAGE"), "hook/clock"),
    (lambda rows: rows[1].update(replay_demo_tick=True), "finite"),
    (lambda rows: rows.append(dict(rows[-1])), "endpoint"),
])
def test_no_missing_frames_endpoints_or_manufactured_clocks(mutate, match):
    ledger = records()
    mutate(ledger)
    with pytest.raises(ValueError, match=match):
        capture_boundaries(ledger, render())


def test_actual_frame_archiving_provenance_and_diagnostic_gate(tmp_path, monkeypatch):
    clip_path, ledger, pts_path = (tmp_path / name for name in ("render.json", "capture.jsonl", "pts.json"))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    inventory = []
    for index in range(2):
        name = f"stable_{index:08d}.tga"
        frame = frames_dir / name
        frame.write_bytes(b"fixture frame bytes " + bytes([index]))
        inventory.append({"capture_index": index, "source_name": f"nonce_{index:08d}.tga",
                          "archived_name": name, "sha256": sha256_file(frame)})
    archive_path = tmp_path / "capture_frame_files.json"
    archive_path.write_text(json.dumps({"schema_version": 1, "capture_prefix": "nonce",
                                       "archived_prefix": "stable", "frames": inventory}))
    report = {**render(), "capture_frame_files_sha256": sha256_file(archive_path)}
    clip_path.write_text(json.dumps(report))
    ledger.write_text("\n".join(json.dumps(row) for row in records()))
    report["capture_ledger_sha256"] = sha256_file(ledger)
    clip_path.write_text(json.dumps(report))
    pts_path.write_text(json.dumps({"clock": "video_presentation", "clip_id": "stable",
                                    "frames": [{"frame_index": i, "pts_seconds": i/32} for i in range(2)]}))
    monkeypatch.setattr("cs2_data.align.verify_video", lambda *args: tmp_path / "stable.mp4")
    out = tmp_path / "timing"
    result = prepare_timing(clip_path, ledger, pts_path, frames_dir, out)
    assert result["timing_status"] == "observed_movie_submission"
    clip = json.loads((out / "clip.json").read_text())
    frames = [json.loads(line) for line in (out / "frames.jsonl").read_text().splitlines()]
    with pytest.raises(ValueError, match="measured"):
        validate_timing(frames, clip)
    validate_timing(frames, clip, diagnostic=True)
    with pytest.raises(ValueError, match="promoted"):
        validate_timing(frames, {**clip, "pov_verified": True, "timing_status": "measured"})
    evidence = verify_capture_evidence(clip, frames)
    assert evidence["pixel_correspondence"]["verified"] is False
    assert clip["pov_verified"] is False and clip["training_ready"] is False
    frames[-1]["source_demo_tick_end"] = 105
    with pytest.raises(ValueError, match="altered"):
        verify_capture_evidence(clip, frames)
    frames[-1]["source_demo_tick_end"] = 104
    frame.write_bytes(b"altered frame")
    with pytest.raises(ValueError, match="frame index/hash"):
        verify_capture_evidence(clip, frames)


def test_rgb_hash_decoding_covers_bgra_origin_and_rle(tmp_path):
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HHBB", header, 12, 2, 2, 32, 16)  # bottom-right origin
    # Desired top-left RGB: red green / blue white; encoded reverse both axes.
    pixels = [b"\xff\xff\xff\xff", b"\xff\x00\x00\xff", b"\x00\xff\x00\xff", b"\x00\x00\xff\xff"]
    path = tmp_path / "origin.tga"
    path.write_bytes(bytes(header) + b"".join(pixels))
    expected = bytes([255,0,0, 0,255,0, 0,0,255, 255,255,255])
    assert tga_rgb24(path) == expected
    header[2] = 10
    path.write_bytes(bytes(header) + bytes([3]) + b"".join(pixels))  # raw RLE packet
    assert tga_rgb24(path) == expected
    path.write_bytes(bytes(header) + bytes([131]) + pixels[0])  # repeated packet
    assert tga_rgb24(path) == bytes([255]*12)
    path.write_bytes(bytes(header) + bytes([132]) + pixels[0])
    with pytest.raises(ValueError, match="Invalid RLE"):
        tga_rgb24(path)


def test_pixel_readback_matches_bytes_and_rejects_wrong_frame_candidate(tmp_path):
    ledger = records()
    inventory = []
    for index in range(2):
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HHBB", header, 12, 1, 1, 24, 32)
        path = tmp_path / f"frame{index}.tga"
        path.write_bytes(bytes(header) + bytes([0,0,100+index]))
        digest = hashlib.sha256(bytes([100+index,0,0])).hexdigest()
        inventory.append({"path": str(path)})
        ledger.append({"event": "pixel_readback", "success": True,
                       "submission_candidate": dict(ledger[index+1]), "rgb_sha256": digest})
    report = verify_readback_pixels(ledger, inventory)
    assert report["verified"] is True and report["matched_frames"] == 2
    ledger[-1]["rgb_sha256"] = "0"*64
    with pytest.raises(ValueError, match="SHA256 disagrees"):
        verify_readback_pixels(ledger, inventory)


def test_fractional_render_time_requires_every_measured_endpoint():
    ledger = records()
    assert render_boundaries(ledger) is None
    for index, record in enumerate(ledger[1:]):
        record["render_time_seconds"] = (1100.5+index*2)/64
    assert render_boundaries(ledger) == [(1100.5+index*2)/64 for index in range(3)]
    del ledger[-1]["render_time_seconds"]
    with pytest.raises(ValueError, match="Every frame and endpoint"):
        render_boundaries(ledger)


def test_fractional_render_observations_drive_future_actions_instead_of_cursor(tmp_path, monkeypatch):
    import pyarrow.parquet as pq
    from cs2_data.align import align
    from cs2_data.calibration import calibrate
    from test_calibration import parsed_fixture, identity

    parsed, manifest = parsed_fixture(tmp_path)
    manifest["tick_rate"] = 64
    (parsed / "manifest.json").write_text(json.dumps(manifest))
    cal = tmp_path / "calibration"
    calibrate(parsed, cal, 1, int(identity()["steam_id"]), 2, 100, 107)
    clip_path, ledger, pts_path = (tmp_path / name for name in ("render.json", "capture.jsonl", "pts.json"))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    inventory = []
    for index in range(2):
        frame = frames_dir / f"stable_{index:08d}.tga"
        frame.write_bytes(b"fixture bytes " + bytes([index]))
        inventory.append({"capture_index": index, "source_name": f"nonce_{index:08d}.tga",
                          "archived_name": frame.name, "sha256": sha256_file(frame)})
    archive_path = tmp_path / "capture_frame_files.json"
    archive_path.write_text(json.dumps({"schema_version": 1, "capture_prefix": "nonce",
                                       "archived_prefix": "stable", "frames": inventory}))
    clip_path.write_text(json.dumps({**render(), **identity(), "capture_frame_files_sha256": sha256_file(archive_path)}))
    native = records()
    for index, event in enumerate(native[1:]):
        event["replay_demo_tick"] = 102+index*2  # cursor is ahead of image state
        event["render_time_seconds"] = (1100.5+index*2)/64
    ledger.write_text("\n".join(json.dumps(row) for row in native))
    report = json.loads(clip_path.read_text())
    report["capture_ledger_sha256"] = sha256_file(ledger)
    clip_path.write_text(json.dumps(report))
    pts_path.write_text(json.dumps({"clock": "video_presentation", "clip_id": "stable",
                                    "frames": [{"frame_index": i, "pts_seconds": i/32} for i in range(2)]}))
    monkeypatch.setattr("cs2_data.align.verify_video", lambda *args: tmp_path / "stable.mp4")
    timing_out = tmp_path / "timing"
    prepare_timing(clip_path, ledger, pts_path, frames_dir, timing_out)
    aligned = tmp_path / "aligned"
    report = align(parsed, timing_out / "frames.jsonl", timing_out / "clip.json", aligned,
                   calibration=cal, diagnostic=True)
    frames = pq.read_table(aligned / "frame_alignment.parquet").to_pylist()
    assert [row["source_demo_tick_start"] for row in frames] == [102, 104]
    assert [row["action_window_demo_tick_start"] for row in frames] == [100.5, 102.5]
    rows = pq.read_table(aligned / "aligned_commands.parquet").to_pylist()
    assert [row["demo_tick"] for row in rows] == [101, 102, 103, 104]
    assert [row["frame_index"] for row in rows] == [0, 0, 1, 1]
    assert report["observation_time_basis"] == "EventClientOutput_t.m_flRenderTime"
    assert report["training_ready"] is False
