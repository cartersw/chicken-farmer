from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "calibration_replay_tests", Path(__file__).parents[1] / "tools/renderer/calibration_replay_windows.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def source(tmp_path, monkeypatch):
    run = tmp_path / "source"
    run.mkdir()
    run_id = "a" * 32
    original = worker.calibration.default_plan()
    original.update(duration_seconds=2, actions=[{"id": "angle_probe", "command": "setang 0 15 0", "at_ms": 500}])
    plan = worker.calibration.native_plan(original, run, run_id)
    player = {"status": "observed", "steam_id": "76561198323592528", "controller_tick_base": 100,
              "observer_services_pointer_observed": True, "observer_services_present": False,
              "observer_mode": None, "life_state": 0,
              "camera_view_entity_handle": 2**32 - 1, "camera_origin": [0, 0, 64],
              "first_person_camera_verified": True, "pawn_state": {"health": 100, "origin": [0, 0, 0]}}
    rows = [
        {"event": "header", "producer": worker.calibration.PROFILE,
         "plugin_marker": worker.calibration.PLUGIN_MARKER.decode(), "plan": plan,
         "control_source": "dispatched_engine_controls_not_physical_device_latency",
         "qpc_frequency": 1000000, "qpc": 1, "physical_input_timestamps": False, "training_ready": False},
        {"event": "calibration_ready", "qpc": 2, "clock_basis": "local_controller_tick_base_64hz",
         "start_tick_base": 100, "map": "de_dust2", "local_connection_verified": True,
         "local_player_alive": True, "commands_verified": True, "local_player": player},
        {"event": "action_dispatch", **plan["actions"][0], "actual_elapsed_ms": 500, "qpc_before": 3, "qpc_after": 4},
        {"event": "recording_stop_dispatched", "qpc": 5, "elapsed_ms": 2000},
        {"event": "calibration_complete", "qpc": 6, "actions_dispatched": 1,
         "training_ready": False, "timing_status": "unverified"},
    ]
    (run / "calibration_ledger.jsonl").write_text("".join(json.dumps({"schema_version": 1, **row}) + "\n" for row in rows))
    save(run / "native-plan.json", plan)
    save(run / "settings-recovery.json", {"state": "restored", "run_id": run_id})
    demo = run / "controlled.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture header")
    proof = {"status": "ready", "fixture": "native proof independently covered by existing renderer tests"}
    save(run / "settings-isolation.json", proof)
    report = {"schema_version": 1, "profile": worker.calibration.PROFILE,
              "status": "recorded_pending_independent_calibration_audit", "training_ready": False,
              "timing_status": "unverified", "gameinfo_restored": True, "settings_restored": True,
              "staged_plugin_removed_from_game": True, "run_id": run_id, "source_plan": original,
              "capture_prefix": plan["movie_name"], "native_plan_sha256": worker.replay.sha256_file(run / "native-plan.json"),
              "native_completion": worker.calibration.verify_native_completion(run / "calibration_ledger.jsonl", plan),
              "settings_isolation": proof, "owned_cs2_pid": 987654,
              "settings_restore_verified": {"state": "restored", "run_id": run_id},
              "demo": {"path": str(demo), "sha256": worker.replay.sha256_file(demo), "size_bytes": demo.stat().st_size}}
    save(run / "calibration.json", report)
    monkeypatch.setattr(worker.replay, "verify_settings_isolation", lambda *a, **kw: copy.deepcopy(proof))
    return run


def argv(source, out):
    return ["--calibration-run", str(source), "--output", str(out), "--start-demo-tick", "200",
            "--end-demo-tick", "300", "--steam-id", "76561198323592528", "--player-slot", "0", "--spectator-user-id", "0"]


def test_owned_source_dry_run_is_diagnostic_and_does_not_launch(source, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker.replay, "run_capture", lambda *a: pytest.fail("Dry run launched renderer"))
    out = tmp_path / "replay"
    assert worker.main(argv(source, out)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["profile"] == worker.PROFILE and report["training_ready"] is False
    assert report["job"]["training_scope"] == "controlled_calibration_diagnostic"
    assert report["job"]["round_id_basis"] == "diagnostic_placeholder_not_competitive_round_acceptance"
    assert "-chicken-calibration-replay" in report["launch_arguments"]
    assert "-insecure" in report["launch_arguments"]
    assert not out.exists()


@pytest.mark.parametrize("change", [
    lambda run: (run / "controlled.dem").write_bytes(b"changed source recording"),
    lambda run: save(run / "settings-recovery.json", {"state": "snapshotted", "run_id": "a" * 32}),
    lambda run: save(run / "native-plan.json", {}),
    lambda run: (run / "calibration_ledger.jsonl").write_text('{}\n'),
])
def test_incomplete_or_changed_source_is_rejected(source, change):
    change(source)
    with pytest.raises(ValueError):
        worker.verified_recording(source)


@pytest.mark.parametrize("field,value", [("settings_restored", False), ("staged_plugin_removed_from_game", False),
                                         ("status", "failed"), ("training_ready", True)])
def test_failed_protection_cannot_be_hidden_by_demo_presence(source, field, value):
    report = worker.replay.read_json(source / "calibration.json")
    report[field] = value
    save(source / "calibration.json", report)
    with pytest.raises(ValueError, match="completed, restored"):
        worker.verified_recording(source)


def test_replay_identity_must_match_original_player(source, tmp_path):
    args = worker.argument_parser().parse_args(argv(source, tmp_path / "out"))
    args.steam_id = "76561198323592529"
    with pytest.raises(ValueError, match="original controlled local player"):
        worker.replay_job(args)


def test_replay_interval_is_bounded_without_silent_truncation(source, tmp_path):
    args = worker.argument_parser().parse_args(argv(source, tmp_path / "out"))
    args.end_demo_tick = 600
    with pytest.raises(ValueError, match="bounded to 320"):
        worker.replay_job(args)


def test_ordinary_replay_launch_does_not_enable_calibration_binary_profile(tmp_path):
    job = {"width": 1280, "height": 720}
    launch = worker.replay.launch_arguments(tmp_path, job, tmp_path / "demo.dem", tmp_path / "log", False)
    assert "-chicken-calibration-replay" not in launch
    job["calibration_replay_profile"] = "arbitrary"
    with pytest.raises(ValueError, match="Unsupported"):
        worker.replay.launch_arguments(tmp_path, job, tmp_path / "demo.dem", tmp_path / "log", False)


def test_execution_delegates_to_protected_replay_worker(source, tmp_path, monkeypatch):
    out = tmp_path / "replay"
    called = []
    monkeypatch.setattr(worker.calibration, "require_calibration_plugin", lambda *a: None)
    monkeypatch.setattr(worker.calibration, "verify_binary_profile", lambda *a: {"fixture": "reviewed"})

    def capture(args, effective, original):
        assert args.execute is True
        assert effective["calibration_replay_profile"] == worker.PROFILE
        assert original["calibration_source"]["calibration_run"] == str(source)
        called.append(effective)
        args.output.mkdir()
        result = {"training_ready": False, "render_status": "fixture"}
        save(args.output / (effective["clip_id"] + ".render.json"), result)
        return result

    monkeypatch.setattr(worker.replay, "run_capture", capture)
    assert worker.main(argv(source, out) + ["--execute"]) == 0
    assert len(called) == 1
    provenance = worker.replay.read_json(out / "calibration_replay.json")
    assert provenance["comparison_verified"] is False and provenance["training_ready"] is False


@pytest.mark.parametrize("pid", [None, True, 0, -1, 2**32, "987654"])
def test_source_settings_proof_must_bind_to_a_real_owned_process_identity(source, pid):
    report = worker.replay.read_json(source / "calibration.json")
    report["owned_cs2_pid"] = pid
    save(source / "calibration.json", report)
    with pytest.raises(ValueError, match="owned process identity"):
        worker.verified_recording(source)


def test_source_change_during_verification_cannot_get_a_new_hash_attached_to_old_contents(source, monkeypatch):
    proof = worker.replay.read_json(source / "settings-isolation.json")

    def changed(*args, **kwargs):
        report = worker.replay.read_json(source / "calibration.json")
        report["source_plan"]["actions"][0]["command"] = "setang 0 30 0"
        save(source / "calibration.json", report)
        return proof

    monkeypatch.setattr(worker.replay, "verify_settings_isolation", changed)
    with pytest.raises(ValueError, match="changed during verification"):
        worker.verified_recording(source)


def test_restoration_journal_must_match_the_worker_recorded_proof(source):
    journal = worker.replay.read_json(source / "settings-recovery.json")
    journal["unexpected_alteration"] = True
    save(source / "settings-recovery.json", journal)
    with pytest.raises(ValueError, match="recovery journal"):
        worker.verified_recording(source)
