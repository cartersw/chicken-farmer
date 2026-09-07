"""Execution-clock and future-action regression tests; no synthetic timing certification."""
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data.align import align, assign_commands
from cs2_data.calibration import calibrate, fit_offset, load_calibration
from cs2_data.io import sha256_file
from cs2_data.normalize import normalize


def commands():
    return [{"demo_id": "demo-hash", "round_id": 1, "steam_id": 76561198000000001,
             "player_slot": 2, "command_row_id": 10 + index * 11, "command_number": 50 + index,
             "demo_tick": 100 + index, "server_tick_executed": 1100 + index,
             "client_tick": 1096 + index, "pawn_entity_handle": 91,
             "view_yaw": float(index), "view_pitch": 0.0, "mousedx_raw": index, "mousedy_raw": 0}
            for index in range(7)]


def parsed_fixture(tmp_path, rows=None):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    table = pa.Table.from_pylist(rows or commands())
    table = table.set_column(table.schema.get_field_index("steam_id"), "steam_id", table["steam_id"].cast(pa.uint64()))
    pq.write_table(table, parsed / "usercmd.parquet")
    manifest = {"demo_id": "demo-hash", "parse_status": "complete", "partial": False,
                "parser_schema_version": "2", "files": {"usercmd.parquet": sha256_file(parsed / "usercmd.parquet")}}
    (parsed / "manifest.json").write_text(json.dumps(manifest))
    return parsed, manifest


def identity():
    return {key: str(commands()[0][key]) if key == "steam_id" else commands()[0][key]
            for key in ("demo_id", "round_id", "steam_id", "player_slot")}


def anchors():
    return {"schema_version": 1, "measurement_method": "engine_command_execution_hook", **identity(),
            "capture_method": "fixture only", "plugin_sha256": "fixture-plugin-hash",
            "anchors": [{"command_row_id": row["command_row_id"], "server_tick_executed": row["server_tick_executed"],
                         "source_demo_tick": row["demo_tick"] + 0.25, "uncertainty_ticks": 0.01}
                        for row in commands()[::3]]}


def test_packet_offset_is_explicitly_inferred_not_execution_measurement():
    report = fit_offset(commands())
    assert report["offset_demo_ticks"] == -1000
    assert report["mapping_status"] == "inferred_from_packet_arrival"
    assert report["execution_timing_verified"] is False
    assert report["anchor_count"] == 0


@pytest.mark.parametrize("field,value,match", [
    ("server_tick_executed", 0, "Zero"),
    ("server_tick_executed", 100, "reversal"),
    ("server_tick_executed", 1120, "gap"),
    ("server_tick_executed", None, "Missing"),
    ("server_tick_executed", True, "Missing"),
    ("command_number", 55, "Command number"),
    ("client_tick", 10, "Client clock reset"),
    ("pawn_entity_handle", 92, "Pawn identity"),
    ("command_row_id", 10, "row IDs"),
    ("round_id", 2, "identity"),
])
def test_invalid_or_reset_clock_segments_are_rejected(field, value, match):
    rows = commands()
    rows[1][field] = value
    with pytest.raises(ValueError, match=match):
        fit_offset(rows)


def test_measured_anchors_handle_batched_packet_clock_without_assuming_packet_offset():
    rows = commands()
    rows[1]["demo_tick"] = rows[0]["demo_tick"]
    with pytest.raises(ValueError, match="offset changes"):
        fit_offset(rows)
    report = fit_offset(rows, anchors())
    assert report["offset_demo_ticks"] == -999.75
    assert report["mapping_status"] == "measured_execution_clock"


@pytest.mark.parametrize("mutate,match", [
    (lambda a: a.update(measurement_method="paired_packet_execution_clocks"), "engine_command"),
    (lambda a: a["anchors"][1].update(source_demo_tick=105), "uncertainty"),
    (lambda a: a["anchors"][1].update(server_tick_executed=42), "canonical"),
    (lambda a: a["anchors"][1].update(command_row_id=10), "canonical"),
    (lambda a: a["anchors"][1].update(uncertainty_ticks=1), "uncertainty"),
    (lambda a: a["anchors"][1].update(source_demo_tick=99), "increase"),
    (lambda a: a.update(player_slot=3), "identity"),
])
def test_independent_anchor_identity_order_and_uncertainty(mutate, match):
    data = anchors()
    mutate(data)
    with pytest.raises(ValueError, match=match):
        fit_offset(commands(), data)


def test_calibration_is_recomputed_and_evidence_is_hash_bound(tmp_path):
    parsed, manifest = parsed_fixture(tmp_path)
    cal = tmp_path / "candidate"
    calibrate(parsed, cal, 1, int(identity()["steam_id"]), 2, 100, 107)
    loaded = load_calibration(cal, manifest, identity(), commands())
    assert loaded["training_ready"] is False
    path = cal / "execution_calibration.json"
    data = json.loads(path.read_text())
    data["mapping_status"] = "measured_execution_clock"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="altered"):
        load_calibration(cal, manifest, identity(), commands())
    evidence = tmp_path / "engine-hook.jsonl"
    evidence.write_text("fixture engine records")
    measured = anchors()
    measured["evidence_files"] = [{"path": evidence.name, "sha256": sha256_file(evidence)}]
    anchors_path = tmp_path / "anchors.json"
    anchors_path.write_text(json.dumps(measured))
    cal = tmp_path / "measured"
    calibrate(parsed, cal, 1, int(identity()["steam_id"]), 2, 100, 107, anchors=anchors_path)
    assert load_calibration(cal, manifest, identity(), commands())["execution_timing_verified"] is True
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="evidence hash"):
        load_calibration(cal, manifest, identity(), commands())


def test_future_action_boundaries_exclude_already_observed_command():
    frames = [{"source_demo_tick_start": 100, "source_demo_tick_end": 102},
              {"source_demo_tick_start": 102, "source_demo_tick_end": 104}]
    rows = [{"command_row_id": 5, "mapped": 101}, {"command_row_id": 90, "mapped": 102},
            {"command_row_id": 121, "mapped": 104}]
    groups, assignment = assign_commands(frames, rows, time_field="mapped", future_actions=True)
    assert groups == [[5, 90], [121]]
    assert assignment == [0, 0, 1]
    with pytest.raises(ValueError, match="outside"):
        assign_commands(frames, [{"command_row_id": 1, "mapped": 100}], time_field="mapped", future_actions=True)


def test_calibrated_parquet_alignment_keeps_raw_ids_and_future_normalization(tmp_path, monkeypatch):
    parsed, _ = parsed_fixture(tmp_path)
    cal = tmp_path / "calibration"
    calibrate(parsed, cal, 1, int(identity()["steam_id"]), 2, 100, 107)
    norm = tmp_path / "normalization"
    normalize(parsed, norm)
    clip = {"schema_version": 1, "clip_id": "fixture", **identity(), "num_frames": 3,
            "timing_status": "measured", "timing_clock": "demo_tick", "pov_verified": True,
            "video_sha256": "fixture", "video_uri": "fixture.mp4"}
    frames = [{**{key: clip[key] for key in ("clip_id", "demo_id", "round_id", "steam_id", "player_slot")},
               "frame_index": i, "pts_seconds": i/32, "source_demo_tick_start": 100+2*i,
               "source_demo_tick_end": 102+2*i} for i in range(3)]
    clip_path, timing = tmp_path / "clip.json", tmp_path / "frames.jsonl"
    clip_path.write_text(json.dumps(clip))
    timing.write_text("\n".join(json.dumps(frame) for frame in frames))
    monkeypatch.setattr("cs2_data.align.verify_video", lambda *args: tmp_path / "fixture.mp4")
    out = tmp_path / "aligned"
    report = align(parsed, timing, clip_path, out, normalized=norm, calibration=cal)
    result = pq.read_table(out / "aligned_commands.parquet").to_pylist()
    assert [row["demo_tick"] for row in result] == list(range(101, 107))
    assert [row["frame_index"] for row in result] == [0, 0, 1, 1, 2, 2]
    assert [row["execution_demo_tick"] for row in result] == list(range(101, 107))
    assert [row["command_row_id"] for row in result] == [21, 32, 43, 54, 65, 76]
    assert all(row["delta_yaw_deg"] == 1 for row in result)
    assert report["action_interval_convention"] == "(start,end]"
    assert report["training_ready"] is False
    assert report["execution_calibration"]["mapping_status"] == "inferred_from_packet_arrival"
    frames[-1]["source_demo_tick_end"] = 107
    timing.write_text("\n".join(json.dumps(frame) for frame in frames))
    with pytest.raises(ValueError, match="extrapolation"):
        align(parsed, timing, clip_path, tmp_path / "outside", calibration=cal)
