import hashlib
import json

import pytest

from cs2_data.calibration_analysis import analyze_calibration, main, CLOCK_BASIS, CONTROL_SOURCE


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def player(tick, x=0, yaw=179, ammo=20):
    return {"status": "observed", "controller_tick_base": tick, "pawn_handle": 1234,
            "steam_id": "76561198000000000", "pawn_state": {"origin": [x, 0, 0],
                "eye_angles": [0, yaw, 0], "ammo_clip": ammo, "last_shot_time": 0,
                "on_ground": True, "duck_amount": 0.0}}


def fixture(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    actions = [{"id": "press", "at_ms": 100, "command": "+forward"},
               {"id": "release", "at_ms": 200, "command": "-forward"}]
    plan = {"schema_version": 1, "producer": "cs2-controlled-calibration-plan-v1", "actions": actions,
            "fps": 32, "width": 1280, "height": 720, "duration_seconds": 3, "map": "de_dust2",
            "movie_name": "calibration-fixture", "demo_path": str(run / "controlled.dem")}
    write_json(run / "native-plan.json", plan)
    write_json(run / "calibration.json", {"status": "recorded_pending_independent_calibration_audit"})
    rows = [{"event": "header", "qpc_frequency": 1000, "control_source": "native_engine_console_dispatch"},
            {"event": "calibration_ready", "clock_basis": CLOCK_BASIS, "start_tick_base": 1000},
            {"event": "frame_sample", "elapsed_ms": 0, "qpc": 90, "local_player": player(1000)},
            {"event": "action_dispatch", **actions[0], "actual_elapsed_ms": 125, "qpc_before": 100,
             "qpc_after": 101, "local_player_before": player(1008), "local_player_after": player(1008)},
            {"event": "frame_sample", "elapsed_ms": 125, "qpc": 102, "local_player": player(1008, 1)},
            {"event": "action_dispatch", **actions[1], "actual_elapsed_ms": 218.75, "qpc_before": 200,
             "qpc_after": 202, "local_player_before": player(1014, 2), "local_player_after": player(1014, 2)},
            {"event": "frame_sample", "elapsed_ms": 250, "qpc": 300, "local_player": player(1016, 3, -179, 19)},
            {"event": "calibration_complete"}]
    write_rows(run / "calibration_ledger.jsonl", rows)
    captures, frames = [], []
    (run / "frames").mkdir()
    for i in range(2):
        name = f"calibration-fixture_{i:08d}.tga"
        data = f"archived-original-frame-{i}".encode()
        (run / "frames" / name).write_bytes(data)
        frames.append({"capture_index": i, "source_name": name, "archived_name": name,
                       "sha256": hashlib.sha256(data).hexdigest()})
        captures.append({"event": "movie_frame", "capture_index": i, "tga_filename": name,
                         "native_observation": {"local_player": player(1000 + i * 2)}, "qpc": 90 + i})
    write_json(run / "capture_frame_files.json", {"schema_version": 1, "frames": frames})
    write_rows(run / "capture_ledger.jsonl", captures)
    (run / "controlled.dem").write_bytes(b"PBDEMS2\x00" + b"\0" * 8 + b"fixture only")
    return run, rows


def codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_coherent_measurements_keep_simulation_and_wall_clock_separate(tmp_path):
    run, _ = fixture(tmp_path)
    out = tmp_path / "analysis"
    report = analyze_calibration(run, out)
    assert report["status"] == "measured_dispatch_diagnostics"
    assert report["control_source"] == CONTROL_SOURCE
    assert [a["dispatch_lateness_simulation_ms"] for a in report["actions"]] == [25, 18.75]
    assert [a["dispatch_call_wall_ms"] for a in report["actions"]] == [1, 2]
    assert report["control_pairs"][0]["dispatched_hold_simulation_ms"] == 93.75
    assert report["observed_sample_state_change"]["origin_net_displacement_engine_units"] == 3
    assert report["observed_sample_state_change"]["eye_angle_net_delta_degrees_wrapped"] == [0, 2, 0]
    assert report["observed_sample_state_change"]["ammo_clip_change"] == -1
    assert report["artifacts"]["controlled.dem"]["source2_header_present"] is True
    assert report["artifacts"]["controlled.dem"]["command_stream_extracted_or_verified"] is False
    assert report["training_ready"] is report["live_control_ready"] is False
    assert report["input_consumption_timing_verified"] is report["button_semantics_verified"] is False
    assert report["replay_comparison_verified"] is False
    assert json.loads((out / "report.json").read_text()) == report
    assert "not proof of input causation" in (out / "report.md").read_text()


def test_completion_without_dispatches_is_explicitly_incomplete(tmp_path):
    run, rows = fixture(tmp_path)
    write_rows(run / "calibration_ledger.jsonl", [r for r in rows if r["event"] != "action_dispatch"])
    report = analyze_calibration(run, tmp_path / "analysis")
    assert report["counts"]["complete_events"] == 1
    assert report["counts"]["dispatched_actions"] == 0
    assert report["status"] == "incomplete_or_inconsistent"
    assert {"no_action_dispatches_observed", "planned_actions_not_dispatched"} <= codes(report)


@pytest.mark.parametrize("change,reason", [
    (lambda r: r[3].update(actual_elapsed_ms=126), "elapsed_does_not_match_observed_tick_base"),
    (lambda r: r[1].update(clock_basis="wall_clock"), "scheduling_clock_unverified"),
    (lambda r: r[0].update(qpc_frequency=True), "invalid_qpc_frequency"),
    (lambda r: r[3].update(qpc_after=99), "invalid_qpc_interval"),
    (lambda r: r[4].update(qpc=1), "qpc_observation_regressed"),
    (lambda r: r[0].update(control_source="physical_keyboard"), "unsupported_or_missing_control_source"),
    (lambda r: r[3].update(command="+attack"), "unexpected_duplicate_or_mismatched_dispatch"),
    (lambda r: r[5].update(id="press"), "unexpected_duplicate_or_mismatched_dispatch"),
    (lambda r: r[5].update(command="+forward"), "duplicate_control_press"),
    (lambda r: r[3].update(command="-attack"), "control_release_without_press"),
    (lambda r: r[6]["local_player"].update(pawn_handle=999), "local_player_identity_changed_during_samples"),
])
def test_contradictory_measurements_never_become_success(tmp_path, change, reason):
    run, rows = fixture(tmp_path)
    change(rows)
    write_rows(run / "calibration_ledger.jsonl", rows)
    report = analyze_calibration(run, tmp_path / "analysis")
    assert reason in codes(report)
    assert report["status"] == "incomplete_or_inconsistent"
    assert report["training_ready"] is report["live_control_ready"] is False


def test_unknown_scheduling_clock_does_not_relabel_qpc_as_simulation_time(tmp_path):
    run, rows = fixture(tmp_path)
    rows[1]["clock_basis"] = "unknown"
    write_rows(run / "calibration_ledger.jsonl", rows)
    report = analyze_calibration(run, tmp_path / "analysis")
    assert all(a["dispatch_lateness_simulation_ms"] is None for a in report["actions"])
    assert [a["dispatch_call_wall_ms"] for a in report["actions"]] == [1, 2]


@pytest.mark.parametrize("remove", ["calibration_ready", "calibration_complete"])
def test_missing_lifecycle_boundary_is_reported(tmp_path, remove):
    run, rows = fixture(tmp_path)
    write_rows(run / "calibration_ledger.jsonl", [r for r in rows if r["event"] != remove])
    report = analyze_calibration(run, tmp_path / "analysis")
    assert "missing_or_duplicate_" + remove in codes(report)


def test_actions_after_completion_do_not_satisfy_orderly_evidence(tmp_path):
    run, rows = fixture(tmp_path)
    rows.insert(3, rows.pop())
    write_rows(run / "calibration_ledger.jsonl", rows)
    assert "observation_outside_ready_complete_interval" in codes(analyze_calibration(run, tmp_path / "analysis"))


def test_missing_release_remains_open_even_when_complete_is_present(tmp_path):
    run, rows = fixture(tmp_path)
    write_rows(run / "calibration_ledger.jsonl", [r for r in rows if r.get("id") != "release"])
    assert "controls_without_dispatched_release" in codes(analyze_calibration(run, tmp_path / "analysis"))


@pytest.mark.parametrize("raw", ['{"event":"header","qpc_frequency":NaN}\n',
                                   '{"event":"header","qpc_frequency":1e999}\n',
                                   '{"event":"header","event":"calibration_complete"}\n',
                                   '{"event":"action_dispatch"', '[]\n'])
def test_malformed_evidence_is_rejected_before_publication(tmp_path, raw):
    run, _ = fixture(tmp_path)
    (run / "calibration_ledger.jsonl").write_text(raw)
    out = tmp_path / "analysis"
    with pytest.raises(ValueError):
        analyze_calibration(run, out)
    assert not out.exists()


def test_missing_original_frames_and_changed_hashes_are_visible(tmp_path):
    run, _ = fixture(tmp_path)
    (run / "frames/calibration-fixture_00000000.tga").unlink()
    (run / "frames/calibration-fixture_00000001.tga").write_bytes(b"changed")
    report = analyze_calibration(run, tmp_path / "analysis")
    assert report["counts"]["archive_hash_matches"] == 0
    assert "missing_or_changed_archived_frame" in codes(report)


def test_archive_mapping_cannot_read_outside_run(tmp_path):
    run, _ = fixture(tmp_path)
    write_json(run / "capture_frame_files.json", {"frames": [{"capture_index": 0,
        "archived_name": "../outside.tga", "source_name": "outside.tga", "sha256": "not-used"}]})
    report = analyze_calibration(run, tmp_path / "analysis")
    assert "invalid_frame_archive_entry" in codes(report)
    assert report["archived_frames"] == []


def test_worker_declared_hash_is_checked_independently(tmp_path):
    run, _ = fixture(tmp_path)
    write_json(run / "calibration.json", {"status": "failed", "error": "fixture failure", "native_plan_sha256": "wrong"})
    report = analyze_calibration(run, tmp_path / "analysis")
    assert {"worker_reported_failure", "worker_artifact_hash_mismatch"} <= codes(report)


def test_malformed_worker_descriptor_is_reported_without_trusting_it(tmp_path):
    run, _ = fixture(tmp_path)
    write_json(run / "calibration.json", {"demo": ["not a hash descriptor"]})
    assert "invalid_worker_artifact_descriptor" in codes(analyze_calibration(run, tmp_path / "analysis"))


def test_absent_run_evidence_produces_diagnostic_report_with_readiness_false(tmp_path):
    run = tmp_path / "empty"
    run.mkdir()
    report = analyze_calibration(run, tmp_path / "analysis")
    assert report["status"] == "incomplete_or_inconsistent"
    assert report["counts"]["planned_actions"] == 0
    assert report["training_ready"] is report["live_control_ready"] is False


def test_report_never_overwrites_existing_output_or_changes_sources(tmp_path):
    run, _ = fixture(tmp_path)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    out = tmp_path / "analysis"
    analyze_calibration(run, out)
    with pytest.raises(ValueError, match="fresh"):
        analyze_calibration(run, out)
    assert before == {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}


def test_standalone_cli(tmp_path, capsys):
    run, _ = fixture(tmp_path)
    assert main(["--run-dir", str(run), "--output", str(tmp_path / "analysis")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["training_ready"] is printed["live_control_ready"] is False
