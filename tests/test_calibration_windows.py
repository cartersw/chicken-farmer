"""Calibration contract and recovery integration; never launches an installed game."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "calibration_worker_tests", Path(__file__).parents[1] / "tools/renderer/calibration_windows.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_dry_run_has_no_filesystem_or_game_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("Dry run launched a process"))
    monkeypatch.setattr(worker.replay, "preflight", lambda *a: pytest.fail("Dry run touched execution preflight"))
    out = tmp_path / "dry"
    assert worker.main(["--output", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "planned" and report["training_ready"] is False
    assert report["physical_input_timestamps"] is False
    assert not out.exists()
    launch = report["launch_arguments"]
    assert "-insecure" in launch and "+sv_lan" in launch and "+map" in launch
    assert launch[-1] == "loopback=true"
    assert "+connect" not in launch and "+playdemo" not in launch


@pytest.mark.parametrize("command", ["connect example.com", "+attack; connect example.com", "+attack\nquit",
                                       "exec autoexec", "setang 0 181 0", "setang 0 nan 0", "+forward 1",
                                       "quit", "tv_record arbitrary", "host_writeconfig"])
def test_external_or_unbounded_commands_are_rejected(command):
    plan = worker.default_plan()
    plan["actions"][0]["command"] = command
    with pytest.raises(ValueError, match="whitelisted"):
        worker.validate_plan(plan)


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version=True), lambda p: p.update(fps=True),
    lambda p: p.update(duration_seconds=121), lambda p: p.update(duration_seconds=10.0),
    lambda p: p.update(map="de_dust2;connect localhost"), lambda p: p.update(server="localhost"),
    lambda p: p["actions"][1].update(id=p["actions"][0]["id"]),
    lambda p: p["actions"][1].update(at_ms=999),
    lambda p: p["actions"][1].update(at_ms=True),
    lambda p: p["actions"][-1].update(at_ms=9999),
    lambda p: p["actions"].pop(), lambda p: p["actions"].pop(0),
    lambda p: p["actions"][1].update(command="+forward"),
])
def test_plan_cannot_hide_unbounded_or_ambiguous_actions(change):
    plan = worker.default_plan()
    change(plan)
    with pytest.raises(ValueError):
        worker.validate_plan(plan)


def test_same_time_release_and_counterstrafe_preserves_order_and_source():
    plan = worker.default_plan()
    before = copy.deepcopy(plan)
    checked = worker.validate_plan(plan)
    assert [a["command"] for a in checked["actions"] if a["at_ms"] == 3000] == ["-forward", "+back"]
    checked["actions"][0]["id"] = "changed"
    assert plan == before


def test_console_path_delimiters_cannot_be_injected(tmp_path):
    with pytest.raises(ValueError, match="delimiters"):
        worker.native_plan(worker.default_plan(), tmp_path / "a;quit", "a" * 32)


def test_worker_requires_both_native_calibration_and_settings_isolation(tmp_path):
    plugin = tmp_path / "plugin.dll"
    plugin.write_bytes(worker.PLUGIN_MARKER)
    with pytest.raises(ValueError, match="settings isolation"):
        worker.require_calibration_plugin(plugin)
    plugin.write_bytes(b"CHICKEN_SETTINGS_ISOLATION_V1")
    with pytest.raises(ValueError, match="lacks controlled calibration"):
        worker.require_calibration_plugin(plugin)


class FakeProcess:
    pid = 987654

    def __init__(self):
        self.returncode = None
        self.terminated = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15

    def kill(self):
        pytest.fail("Unexpected kill in fixture")

    def wait(self, timeout=None):
        assert self.returncode is not None
        return self.returncode


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def protected_run(tmp_path, monkeypatch):
    game, steam, out = tmp_path / "game", tmp_path / "Steam", tmp_path / "calibration"
    gameinfo = write(game / "csgo/gameinfo.gi", b'"GameInfo"\r\n{\r\n FileSystem\r\n {\r\n  SearchPaths\r\n  {\r\n   Game csgo\r\n  }\r\n }\r\n}\r\n')
    write(steam / "steam.exe", b"fixture only")
    local = steam / "userdata/100000001/730/local/cfg"
    personal = write(local / "cs2_user_convars_0_slot0.vcfg", b"personal configuration\r\n")
    remote = write(steam / "userdata/100000001/730/remote/cs2_user.vcfg", b"remote original")
    autoexec = write(game / "csgo/cfg/autoexec.cfg", b"personal autoexec")
    originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (personal, remote, autoexec, gameinfo)}
    plugin = write(tmp_path / "plugin.dll", b"CHICKEN_SETTINGS_ISOLATION_V1 " + worker.PLUGIN_MARKER + worker.SETTLE_POLICY.encode())
    args = worker.argument_parser().parse_args(["--output", str(out), "--game-dir", str(game),
        "--steam-dir", str(steam), "--steam-user-id", "100000001", "--plugin", str(plugin), "--execute"])
    process = FakeProcess()
    state = {"launched": False}
    monkeypatch.setattr(worker.replay, "cs2_pids", lambda: [process.pid]
                        if state["launched"] and process.poll() is None else [])
    monkeypatch.setattr(worker.replay, "preflight", lambda *a: (Path("ffmpeg"), Path("ffprobe"), {"PatchVersion": "fixture"}))
    monkeypatch.setattr(worker, "verify_binary_profile", lambda *a: {"fixture": "not live"})
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("Fixture must supply a fake launch"))
    return SimpleNamespace(game=game, out=out, personal=personal, remote=remote, autoexec=autoexec,
                           gameinfo=gameinfo, originals=originals, args=args, process=process, state=state)


def assert_restored(run):
    for path, (data, mtime) in run.originals.items():
        assert path.read_bytes() == data
        if path != run.gameinfo:
            assert path.stat().st_mtime_ns == mtime
    report = worker.replay.read_json(run.out / "calibration.json")
    assert report["gameinfo_restored"] is True
    assert report["staged_plugin_removed_from_game"] is True
    assert not list((run.game / "csgo").glob("chicken-render-*"))
    assert report["training_ready"] is False
    return report


def test_failed_process_creation_restores_search_path_and_preserves_personal_settings(protected_run, monkeypatch):
    run = protected_run

    def launch(argv, **kwargs):
        assert b"csgo/chicken-render-" in run.gameinfo.read_bytes()
        assert (run.out / "replay-settings/cfg" / run.personal.name).read_bytes() == run.originals[run.personal][0]
        assert kwargs["env"]["USRLOCALCSGO"] == str(run.out / "replay-settings")
        assert worker.replay.read_json(run.out / "settings-recovery.json")["state"] == "snapshotted"
        assert "-insecure" in argv
        raise OSError("simulated creation failure")

    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="simulated creation failure"):
        worker.run_calibration(run.args, worker.default_plan())
    report = assert_restored(run)
    assert report["settings_restored"] == "not_modified"
    assert report["status"] == "failed"


@pytest.mark.parametrize("failure", ["crash", "timeout", "interrupt"])
def test_process_failures_stop_only_owned_handle_and_restore_settings(protected_run, monkeypatch, failure):
    run = protected_run
    extra = run.personal.with_name("cs2_user_new.vcfg")

    def launch(*args, **kwargs):
        run.state["launched"] = True
        return run.process

    def wait(*args):
        run.personal.write_bytes(b"controlled setting changes")
        run.remote.unlink()
        extra.write_bytes(b"new controlled preferences")
        if failure == "crash":
            run.process.returncode = 5
            return 5
        if failure == "timeout":
            raise TimeoutError("simulated timeout")
        raise KeyboardInterrupt("simulated interrupt")

    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    monkeypatch.setattr(worker, "wait_for_calibration", wait)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_calibration(run.args, worker.default_plan())
    report = assert_restored(run)
    assert report["settings_restored"] is True
    assert report["settings_restore_verified"]["verified_original_files"] == 3
    assert run.process.terminated == (0 if failure == "crash" else 1)
    assert not extra.exists()
    assert report["status"] == "failed"


def test_existing_game_never_gets_terminated_or_modified(protected_run, monkeypatch):
    run = protected_run
    monkeypatch.setattr(worker.replay, "require_cs2_idle", lambda: (_ for _ in ()).throw(ValueError("CS2 is already running")))
    with pytest.raises(RuntimeError, match="already running"):
        worker.run_calibration(run.args, worker.default_plan())
    assert run.process.terminated == 0
    assert run.gameinfo.read_bytes() == run.originals[run.gameinfo][0]
    assert run.personal.read_bytes() == run.originals[run.personal][0]


def test_frame_budget_stops_capture_before_unbounded_output(tmp_path, monkeypatch):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "b" * 32)
    process = FakeProcess()
    path = write(tmp_path / "frame.tga", b"small fixture")
    monkeypatch.setattr(worker.replay, "capture_files", lambda *args: [path] * 385)
    with pytest.raises(RuntimeError, match="bounded frame/disk"):
        worker.wait_for_calibration(process, [], plan, tmp_path, 10)


def test_output_inside_game_is_refused_before_mutation(protected_run):
    run = protected_run
    run.args.output = run.game / "calibration"
    with pytest.raises(ValueError, match="separate"):
        worker.run_calibration(run.args, worker.default_plan())
    assert not run.args.output.exists()


def completion_events(plan):
    events = [{"event": "header", "producer": worker.PROFILE, "plugin_marker": worker.PLUGIN_MARKER.decode(),
               "control_source": "dispatched_engine_controls_not_physical_device_latency",
               "qpc_frequency": 1000000, "qpc": 0, "plan": plan,
               "physical_input_timestamps": False, "training_ready": False},
              {"event": "calibration_ready", "qpc": 1, "clock_basis": "local_controller_tick_base_64hz",
               "start_tick_base": 100, "map": "de_dust2", "local_connection_verified": True,
               "local_player_alive": True, "commands_verified": True,
               "local_player": {"status": "observed", "controller_tick_base": 100,
                                "observer_services_pointer_observed": True, "observer_services_present": True,
                                "observer_mode": 0, "life_state": 0,
                                "camera_view_entity_handle": 2**32 - 1, "camera_origin": [0, 0, 64],
                                "first_person_camera_verified": True,
                                "pawn_state": {"health": 100, "origin": [0, 0, 0]}}}]
    for index, action in enumerate(plan["actions"]):
        events.append({"event": "action_dispatch", **action, "actual_elapsed_ms": action["at_ms"],
                       "qpc_before": 10 + index * 2, "qpc_after": 11 + index * 2})
    events.extend([{"event": "recording_stop_dispatched", "elapsed_ms": plan["duration_seconds"] * 1000, "qpc": 1000},
                   {"event": "calibration_complete", "qpc": 1001, "actions_dispatched": len(plan["actions"]),
                    "training_ready": False, "timing_status": "unverified"}])
    return [{"schema_version": 1, **event} for event in events]


def ledger(path, events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def settled_completion_events(plan):
    events = completion_events(plan)
    events[0]["startup_settle_policy"] = worker.SETTLE_POLICY
    for event in events[1:]:
        for key in ("qpc", "qpc_before", "qpc_after"):
            if key in event:
                event[key] += 9000000
    samples = [{"schema_version": 1, "event": "startup_settle_sample", "qpc": 7000000 + i * 31250,
                "callback_gap_qpc": 31250, "eligible": True, "controller_tick_base": 128 + 2 * i,
                "pawn_handle": 456} for i in range(65)]
    evidence = {"policy": worker.SETTLE_POLICY, "start_qpc": 7000000, "end_qpc": 9000000,
                "first_tick_base": 128, "last_tick_base": 256, "pawn_handle": 456, "sample_count": 65,
                "reset_count": 0, "max_callback_gap_qpc": 31250, "required_seconds": 2,
                "maximum_gap_ms": 250, "minimum_samples": 32, "minimum_tick_advance": 64,
                "physical_input_timing_verified": False}
    events[1]["startup_settle"] = evidence.copy()
    events[1]["start_tick_base"] = 300
    events[1]["local_player"].update(controller_tick_base=300, pawn_handle=456)
    return [events[0], {"schema_version": 1, "event": "local_setup_dispatched", "qpc": 500000},
            *samples, {"schema_version": 1, "event": "startup_settle_complete", "qpc": 9000000,
                       "evidence": evidence}, *events[1:]]


def test_completed_schedule_remains_uncalibrated_and_not_training_ready(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "c" * 32)
    path = ledger(tmp_path / "ledger.jsonl", completion_events(plan))
    result = worker.verify_native_completion(path, plan)
    assert result["status"] == "schedule_completed" and result["action_count"] == 14
    assert result["calibration_accuracy_verified"] is False and result["training_ready"] is False


@pytest.mark.parametrize("change", [
    lambda e: e.pop(0), lambda e: e.append(e[1]), lambda e: e.pop(2),
    lambda e: e[2].update(command="+back"), lambda e: e[2].update(qpc_before=-1),
    lambda e: e[2].update(qpc_after=9), lambda e: e[3].update(qpc_before=10),
    lambda e: e[2].update(actual_elapsed_ms=999), lambda e: e[2].update(actual_elapsed_ms=float("nan")),
    lambda e: e[1].update(local_connection_verified=1), lambda e: e[1].update(commands_verified=False),
    lambda e: e[1].update(clock_basis="wall_time"), lambda e: e[1].update(start_tick_base=True),
    lambda e: e[1]["local_player"].update(status="unavailable"),
    lambda e: e[1]["local_player"]["pawn_state"].update(health=0),
    lambda e: e[1]["local_player"].update(observer_mode=False),
    lambda e: e[-2].update(elapsed_ms=9999), lambda e: e[-1].update(actions_dispatched=13),
    lambda e: e[-1].update(qpc=999), lambda e: e[-1].update(training_ready=True),
    lambda e: e.insert(-1, {"schema_version": 1, "event": "calibration_failed"}),
    lambda e: e[0].update(qpc_frequency=True), lambda e: e[0].update(schema_version=True),
])
def test_incomplete_or_inconsistent_native_evidence_is_rejected(tmp_path, change):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "d" * 32)
    events = completion_events(plan)
    change(events)
    with pytest.raises(ValueError):
        worker.verify_native_completion(ledger(tmp_path / "ledger.jsonl", events), plan)


@pytest.mark.parametrize("data", [b'{}\n', b'{"event":"header","event":"header"}\n',
                                   b'{"schema_version":1,"event":"header"}',
                                   b'{"schema_version":1,"event":"header","value":1e309}\n'])
def test_stub_truncated_or_ambiguous_ledgers_are_rejected(tmp_path, data):
    path = write(tmp_path / "ledger.jsonl", data)
    with pytest.raises(ValueError):
        worker.verify_native_completion(path, worker.native_plan(worker.default_plan(), tmp_path, "e" * 32))


def test_unreviewed_binary_update_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "BINARY_PROFILE", {"engine.dll": "0" * 64})
    write(tmp_path / "engine.dll", b"unreviewed build")
    with pytest.raises(ValueError, match="compatibility review"):
        worker.verify_binary_profile(tmp_path)


def test_archive_demo_only_moves_unique_run_owned_basename(tmp_path):
    game, out = tmp_path / "game", tmp_path / "out"
    game.mkdir()
    out.mkdir()
    prefix = "calibration-" + "f" * 32
    original = write(game / (prefix + ".dem"), b"PBDEMS2\x00" + b"fixture header")
    other = write(game / "normal-match.dem", b"personal recording")
    result = worker.archive_recording([game], out, prefix)
    assert not original.exists() and other.read_bytes() == b"personal recording"
    assert (out / "controlled.dem").read_bytes() == b"PBDEMS2\x00fixture header"
    assert result["source_name"] == prefix + ".dem"


def test_duplicate_recordings_are_preserved_for_inspection(tmp_path):
    prefix = "calibration-" + "f" * 32
    roots = [tmp_path / "mod", tmp_path / "game"]
    out = tmp_path / "out"
    out.mkdir()
    for root in roots:
        write(root / (prefix + ".dem"), b"PBDEMS2\x00" + b"fixture header")
    with pytest.raises(ValueError, match="Expected one recording"):
        worker.archive_recording(roots, out, prefix)
    assert all((root / (prefix + ".dem")).exists() for root in roots)
    assert not (out / "controlled.dem").exists()


def test_completed_protected_run_archives_evidence_after_restoration(protected_run, monkeypatch):
    run = protected_run

    def launch(*args, **kwargs):
        run.state["launched"] = True
        return run.process

    def wait(process, roots, plan, out, timeout):
        ledger(out / "calibration_ledger.jsonl", settled_completion_events(plan))
        write(out / "capture_ledger.jsonl", b'{"event":"fixture_capture"}\n')
        write(roots[0].parent / (plan["movie_name"] + ".dem"), b"PBDEMS2\x00fixture header")
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HHBB", header, 12, plan["width"], plan["height"], 24, 32)
        for index in range(2):
            write(roots[0] / f"{plan['movie_name']}_{index:08d}.tga",
                  bytes(header) + bytes(plan["width"] * plan["height"] * 3))
        run.personal.write_bytes(b"test changed preferences")
        process.returncode = 0
        return 0

    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    monkeypatch.setattr(worker, "wait_for_calibration", wait)
    monkeypatch.setattr(worker.replay, "verify_settings_isolation", lambda *a, **k: {"status": "ready"})
    monkeypatch.setattr(worker.replay, "encode_video", lambda *a: {"num_frames": a[-2]})
    report = worker.run_calibration(run.args, worker.default_plan())
    assert_restored(run)
    assert report["status"] == "recorded_pending_independent_calibration_audit"
    assert report["native_completion"]["calibration_accuracy_verified"] is False
    assert report["num_frames"] == 2
    assert (run.out / "controlled.dem").read_bytes() == b"PBDEMS2\x00fixture header"
    assert len(list((run.out / "frames").glob("*.tga"))) == 2
    assert not list((run.out / "renderer-sandbox").glob("*.dem"))


def test_changed_native_plan_is_preserved_as_failed_evidence(protected_run, monkeypatch):
    run = protected_run

    def launch(*args, **kwargs):
        run.state["launched"] = True
        return run.process

    def wait(*args):
        (run.out / "native-plan.json").write_text("{}")
        run.process.returncode = 0
        return 0

    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    monkeypatch.setattr(worker, "wait_for_calibration", wait)
    with pytest.raises(RuntimeError, match="plan changed"):
        worker.run_calibration(run.args, worker.default_plan())
    assert_restored(run)
    assert (run.out / "native-plan.json").read_text() == "{}"


def test_observed_null_observer_pointer_is_distinct_from_unreadable(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "b" * 32)
    events = completion_events(plan)
    local = events[1]["local_player"]
    local.update(observer_services_present=False, observer_mode=None)
    path = tmp_path / "ledger.jsonl"
    assert worker.verify_native_completion(ledger(path, events), plan)["status"] == "schedule_completed"
    local["observer_services_pointer_observed"] = False
    with pytest.raises(ValueError, match="first-person"):
        worker.verify_native_completion(ledger(path, events), plan)


@pytest.mark.parametrize("change", [
    lambda p: p.pop("observer_services_pointer_observed"),
    lambda p: p.update(observer_services_pointer_observed=1),
    lambda p: p.update(observer_services_present=None),
    lambda p: p.update(observer_services_present=False, observer_mode=0),
    lambda p: p.update(observer_services_present=True, observer_mode=None),
    lambda p: p.update(observer_mode=2), lambda p: p.update(first_person_camera_verified=False),
    lambda p: p.update(camera_view_entity_handle=None), lambda p: p.update(camera_view_entity_handle=42),
    lambda p: p.update(camera_origin=[2.01, 0, 64]), lambda p: p.update(camera_origin=[0, 0, 23.99]),
    lambda p: p.update(camera_origin=[0, 0, 76.01]), lambda p: p.update(camera_origin=[float("nan"), 0, 64]),
])
def test_first_person_readiness_requires_observed_pointer_and_camera_evidence(change):
    local = completion_events(worker.default_plan())[1]["local_player"]
    change(local)
    assert worker.first_person_ready(local) is False
