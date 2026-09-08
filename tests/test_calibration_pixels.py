import copy
import hashlib
import json
import struct

import pytest

from cs2_data.calibration_pixels import BINARIES, NATIVE_PROFILE, audit_calibration_pixels, main


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True)
    prefix = "calibration-" + "1"*32
    worker = {"status": "recorded_pending_independent_calibration_audit", "cs2_exit_code": 0,
              "binary_profile": dict(BINARIES), "num_frames": 2, "capture_prefix": prefix,
              "plugin_sha256": "f"*64}
    archive = {"schema_version": 1, "capture_prefix": prefix, "archived_prefix": prefix, "frames": []}
    rows = [{"event": "header", "schema_version": 1, "native_profile": NATIVE_PROFILE,
             "hook": "engine2.CMovieRecorder.movie_frame_submit+ReadTexturePixels",
             "engine_sha256": BINARIES["bin/win64/engine2.dll"],
             "native_observation": {"client_sha256": BINARIES["csgo/bin/win64/client.dll"]}}]
    samples = []
    for i in range(2):
        rgb = bytes([10+i, 20, 30, 40, 50, 60])
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HHBB", header, 12, 2, 1, 24, 32)
        bgr = b"".join(rgb[x:x+3][::-1] for x in (0, 3))
        name = f"{prefix}_{i:08d}.tga"
        (run / "frames" / name).write_bytes(header+bgr)
        player = {"status": "observed", "controller_tick_base": 100+2*i, "pawn_handle": 123}
        movie = {"event": "movie_frame", "capture_index": i, "counter_after": i+1,
                 "movie_name": prefix+"_", "tga_filename": name,
                 "native_observation": {"local_player": player}, "qpc_before": 1000+i*100}
        candidate = copy.deepcopy(movie)
        del candidate["counter_after"]
        pixel = {"event": "pixel_readback", "success": True, "width": 2, "height": 1,
                 "submission_candidate": candidate, "rgb_sha256": hashlib.sha256(rgb).hexdigest(),
                 "native_observation": {"local_player": copy.deepcopy(player)},
                 "native_observation_after": {"local_player": copy.deepcopy(player)}}
        rows += [pixel, movie]  # Native writes can be nested; candidate links are authoritative.
        archive["frames"].append({"capture_index": i, "source_name": name, "archived_name": name,
                                   "sha256": digest(run / "frames" / name)})
        samples.extend([{"event": "frame_sample", "local_player": player}]*2)
    rows.append({"event": "movie_end", "next_capture_index": 2, "movie_name": prefix+"_"})
    (run / "controlled.dem").write_bytes(b"PBDEMS2\0fixture")
    worker["demo"] = {"sha256": digest(run / "controlled.dem")}
    (run / "calibration_ledger.jsonl").write_text("".join(json.dumps(x)+"\n" for x in
        samples+[{"event": "calibration_complete"}]))
    refresh(run, worker, archive, rows)
    return run, worker, archive, rows


def refresh(run, worker, archive, rows):
    write_json(run / "capture_frame_files.json", archive)
    (run / "capture_ledger.jsonl").write_text("".join(json.dumps(x)+"\n" for x in rows))
    worker["capture_frame_files_sha256"] = digest(run / "capture_frame_files.json")
    for name in ("capture_ledger", "calibration_ledger"):
        worker[name] = {"path": name+".jsonl", "sha256": digest(run / (name+".jsonl"))}
    write_json(run / "calibration.json", worker)


def test_pixels_match_without_relabeling_clocks_or_readiness(tmp_path):
    run, *_ = fixture(tmp_path)
    report = audit_calibration_pixels(run, tmp_path / "analysis" / "pixels.json")
    assert report["pixel_comparison"]["matched_frames"] == 2
    assert report["pixel_comparison"]["distinct_pixel_hashes"] == 2
    assert report["native_local_player_stable_frames"] == 2
    assert report["frame_start_tick_base"]["observations"] == 4
    assert report["frame_start_tick_base"]["unique_values"] == 2
    assert report["frame_start_tick_base"]["successive_tick_delta_counts"] == {"0": 2, "2": 1}
    assert report["training_ready"] is report["live_control_ready"] is False
    assert report["input_consumption_timing_verified"] is report["pixel_camera_causal_phase_verified"] is False


@pytest.mark.parametrize("change,reason", [
    (lambda w,a,r: r[3].update(submission_candidate=copy.deepcopy(r[1]["submission_candidate"])), "association"),
    (lambda w,a,r: r[1].update(rgb_sha256="0"*64), "SHA256 disagrees"),
    (lambda w,a,r: r[1]["submission_candidate"].update(qpc_before=999), "candidate disagrees"),
    (lambda w,a,r: r[1].update(width=1, height=2), "dimensions"),
    (lambda w,a,r: r[1].update(success=False), "readback failed"),
    (lambda w,a,r: r[0].update(engine_sha256="0"*64), "header disagrees"),
    (lambda w,a,r: w["binary_profile"].update({"csgo/bin/win64/client.dll": "0"*64}), "binary profile"),
    (lambda w,a,r: a["frames"][0].update(archived_name="../outside.tga"), "filename mismatch"),
    (lambda w,a,r: r[2].update(capture_index=False), "counters"),
    (lambda w,a,r: r[-1].update(next_capture_index=1), "endpoint"),
    (lambda w,a,r: w.update(cs2_exit_code=3221225477), "clean process exit"),
    (lambda w,a,r: w.update(demo=[]), "Malformed calibration evidence"),
    (lambda w,a,r: r[0].update(native_observation=None), "Malformed calibration evidence"),
])
def test_false_evidence_is_rejected_without_publishing(tmp_path, change, reason):
    run, worker, archive, rows = fixture(tmp_path)
    change(worker, archive, rows)
    refresh(run, worker, archive, rows)
    out = tmp_path / "analysis" / "pixels.json"
    with pytest.raises(ValueError, match=reason):
        audit_calibration_pixels(run, out)
    assert not out.exists()
    assert not list(out.parent.glob(".*partial*"))
    assert not (out.parent / ".cs2-data.lock").exists()


def test_rehashed_modified_frame_still_must_match_native_pixels(tmp_path):
    run, worker, archive, rows = fixture(tmp_path)
    path = run / "frames" / archive["frames"][0]["archived_name"]
    data = bytearray(path.read_bytes())
    data[-1] ^= 127
    path.write_bytes(data)
    archive["frames"][0]["sha256"] = digest(path)
    refresh(run, worker, archive, rows)
    with pytest.raises(ValueError, match="SHA256 disagrees"):
        audit_calibration_pixels(run, tmp_path / "pixels.json")


def test_source_hash_mismatch_cannot_be_published(tmp_path):
    run, *_ = fixture(tmp_path)
    with (run / "capture_ledger.jsonl").open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="ledger hash/path"):
        audit_calibration_pixels(run, tmp_path / "pixels.json")


def test_resolved_frame_path_cannot_escape_run(tmp_path, monkeypatch):
    run, _, archive, _ = fixture(tmp_path)
    target = run / "frames" / archive["frames"][0]["archived_name"]
    real_resolve = type(target).resolve
    def resolve(path, *args, **kwargs):
        return tmp_path / "outside.tga" if path == target else real_resolve(path, *args, **kwargs)
    monkeypatch.setattr(type(target), "resolve", resolve)
    with pytest.raises(ValueError, match="escapes"):
        audit_calibration_pixels(run, tmp_path / "pixels.json")


def test_state_change_does_not_invalidate_correct_pixel_identity(tmp_path):
    run, worker, archive, rows = fixture(tmp_path)
    rows[1]["native_observation_after"]["local_player"]["controller_tick_base"] += 1
    refresh(run, worker, archive, rows)
    report = audit_calibration_pixels(run, tmp_path / "pixels.json")
    assert report["pixel_comparison"]["matched_frames"] == 2
    assert report["native_local_player_stable_frames"] == 1
    assert report["pixel_camera_causal_phase_verified"] is False


def test_repeated_pixels_remain_explicitly_ambiguous(tmp_path):
    run, worker, archive, rows = fixture(tmp_path)
    first, second = [run / "frames" / f["archived_name"] for f in archive["frames"]]
    second.write_bytes(first.read_bytes())
    archive["frames"][1]["sha256"] = digest(second)
    rows[3]["rgb_sha256"] = rows[1]["rgb_sha256"]
    refresh(run, worker, archive, rows)
    report = audit_calibration_pixels(run, tmp_path / "pixels.json")
    assert report["pixel_comparison"]["matched_frames"] == 2
    assert report["pixel_comparison"]["distinct_pixel_hashes"] == 1
    assert report["pixel_comparison"]["repeated_pixel_frames"] == 1
    assert report["training_ready"] is False


def test_extra_recording_modules_do_not_change_pixel_contract(tmp_path):
    run, worker, archive, rows = fixture(tmp_path)
    worker["binary_profile"].update({"bin/win64/tier0.dll": "b"*64,
                                      "csgo/bin/win64/server.dll": "a"*64})
    refresh(run, worker, archive, rows)
    report = audit_calibration_pixels(run, tmp_path / "pixels.json")
    assert report["binary_profile"] == worker["binary_profile"]
    assert report["pixel_contract_binary_requirements"] == BINARIES
    assert "csgo/bin/win64/server.dll" not in report["pixel_contract_binary_requirements"]
    assert report["input_consumption_timing_verified"] is False


def test_cli_and_immutable_output(tmp_path, capsys):
    run, *_ = fixture(tmp_path)
    out = tmp_path / "pixels.json"
    assert main(["--run-dir", str(run), "--output", str(out)]) == 0
    original = out.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        audit_calibration_pixels(run, out)
    assert out.read_bytes() == original
    assert "all_archived_pixels_match_readback" in capsys.readouterr().out
