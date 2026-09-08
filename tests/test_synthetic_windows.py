import copy
import importlib.util
import json
from pathlib import Path

import pytest

from test_calibration_windows import worker as calibration, settled_completion_events, ledger, protected_run, assert_restored

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("synthetic_windows", ROOT / "tools/renderer/synthetic_windows.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def fixture(tmp_path):
    plan = worker.native_plan(json.loads((ROOT / "tools/renderer/plans/synthetic-mouse-probe-012-v1.json").read_text()),
                              tmp_path, "b" * 32)
    events = settled_completion_events({**plan, "actions": []})
    header = events[0]
    header.update(plan=plan, producer=worker.PROFILE, control_source=worker.CONTROL_SOURCE,
                  synthetic_input_marker=worker.MARKER, owned_process_id=123,
                  native_profile="cs2-14180-calibration-v1",
                  engine_sha256=calibration.BINARY_PROFILE["bin/win64/engine2.dll"])
    ready = next(row for row in events if row["event"] == "calibration_ready")
    ready.update(synthetic_input_bridge_verified=True, local_connection="loopback", connection_evidence={"verified": True})
    ready["local_player"]["team"] = 2
    extra = {"schema_version": 1, "event": "synthetic_bridge_ready", "qpc": ready["qpc"],
             "owned_process_id": 123, "local_connection_verified": True, "start_tick_base": ready["start_tick_base"],
             "marker": worker.MARKER}
    frame = {"schema_version": 1, "event": "frame_sample", "qpc": ready["qpc"] + 1, "elapsed_ms": 0.0,
             "owned_process_id": 123, "local_connection_verified": True, "local_player": copy.deepcopy(ready["local_player"])}
    events.insert(events.index(ready) + 1, extra)
    events.insert(events.index(extra) + 1, frame)
    events[-1]["external_events_planned"] = len(plan["events"])
    return plan, events


def active_bridge(tmp_path):
    plan, events = fixture(tmp_path)
    path = ledger(tmp_path / "native.jsonl", events[:-2])
    now = [events[-3]["qpc"] + 10]
    bridge = worker.NativeBridge(path, plan, 123, lambda: now[0], 1000000)
    bridge.read_more()
    return bridge, now, events


def test_native_ready_scope_is_bound_to_run_and_clock(tmp_path):
    bridge, _, _ = active_bridge(tmp_path)
    scope = bridge.scope_guard()
    assert scope["verified"] is True and scope["owned_process_id"] == 123
    assert scope["elapsed_ms"] == 0 and scope["local_connection"] == "loopback"
    bridge.close()


@pytest.mark.parametrize("change", [
    lambda row: row.update(owned_process_id=999),
    lambda row: row.update(local_connection_verified=False),
    lambda row: row.update(elapsed_ms=31.25),
    lambda row: row.update(qpc=0),
    lambda row: row["local_player"].update(pawn_handle=999),
    lambda row: row["local_player"].update(life_state=1),
    lambda row: row["local_player"].update(observer_mode=5),
    lambda row: row["local_player"].update(camera_view_entity_handle=42),
])
def test_frame_scope_or_clock_loss_blocks_input(tmp_path, change):
    bridge, _, events = active_bridge(tmp_path)
    row = copy.deepcopy(events[-3])
    change(row)
    with pytest.raises(ValueError, match="scope, identity or scheduling clock"):
        bridge.accept(row)
    bridge.close()


def test_stale_scope_and_future_qpc_block_input(tmp_path):
    bridge, now, _ = active_bridge(tmp_path)
    for value in (bridge.frame["qpc"] - 1, bridge.frame["qpc"] + 250001):
        now[0] = value
        with pytest.raises(ValueError, match="stale"):
            bridge.scope_guard()
    bridge.close()


def test_movement_prediction_offset_does_not_fail_alive_identity_scope(tmp_path):
    bridge, _, events = active_bridge(tmp_path)
    row = copy.deepcopy(events[-3])
    row["local_player"].update(camera_origin=[10, 10, 64], first_person_camera_verified=False)
    bridge.accept(row)
    assert bridge.scope_guard()["verified"]
    bridge.close()


def test_stop_revokes_input_scope(tmp_path):
    bridge, _, events = active_bridge(tmp_path)
    bridge.accept(events[-2])
    with pytest.raises(ValueError, match="active native"):
        bridge.scope_guard()
    bridge.close()


def test_incomplete_native_line_is_not_used(tmp_path):
    plan, events = fixture(tmp_path)
    path = tmp_path / "stream.jsonl"
    line = json.dumps(events[0]).encode()
    path.write_bytes(line[:100])
    bridge = worker.NativeBridge(path, plan, 123, lambda: 0, 1000000)
    bridge.read_more()
    assert bridge.header is None
    with path.open("ab") as handle:
        handle.write(line[100:] + b"\n")
    bridge.read_more()
    assert bridge.header["plan"] == plan
    bridge.close()


def test_synthetic_completion_has_no_console_actions(tmp_path):
    plan, events = fixture(tmp_path)
    report = worker.verify_native_completion(ledger(tmp_path / "complete.jsonl", events), plan, require_settle=True)
    assert report["action_count"] == 0 and report["training_ready"] is False
    events.insert(-2, {"schema_version": 1, "event": "action_dispatch", "command": "+forward"})
    with pytest.raises(ValueError, match="exactly"):
        worker.verify_native_completion(ledger(tmp_path / "bad.jsonl", events), plan, require_settle=True)


def test_due_batches_preserve_boundaries_and_reject_late_collapse():
    events = [{"at_ms": 1000}, {"at_ms": 1000}, {"at_ms": 1100}]
    assert worker.due_batch(events, 0, {"elapsed_ms": 999}) == []
    assert worker.due_batch(events, 0, {"elapsed_ms": 1031.25}) == events[:2]
    with pytest.raises(ValueError, match="collapse"):
        worker.due_batch(events, 0, {"elapsed_ms": 1100})
    with pytest.raises(ValueError, match="lateness"):
        worker.due_batch(events, 0, {"elapsed_ms": 2000})


def test_budget_io_uses_idle_windows_away_from_input_deadlines():
    events = [{"at_ms": 1000}, {"at_ms": 1031}]
    assert worker.budget_check_window(None, events, 0, {})
    assert worker.budget_check_window({"elapsed_ms": 700}, events, 0, {})
    assert not worker.budget_check_window({"elapsed_ms": 750}, events, 0, {})
    assert not worker.budget_check_window({"elapsed_ms": 700}, events, 0, {("key", "W"): 1})
    assert worker.budget_check_window({"elapsed_ms": 1050}, events, 2, {})


def test_synthetic_adapter_failure_uses_existing_protected_cleanup(protected_run, monkeypatch):
    run = protected_run
    run.args.plugin.write_bytes(run.args.plugin.read_bytes() + worker.MARKER.encode())
    plan = json.loads((ROOT / "tools/renderer/plans/synthetic-mouse-probe-012-v1.json").read_text())

    def launch(argv, **kwargs):
        assert "-chicken-synthetic-input" in argv
        run.state["launched"] = True
        return run.process

    def fail(*args):
        run.personal.write_bytes(b"temporary render settings")
        raise RuntimeError("simulated foreground loss")

    monkeypatch.setattr(calibration.subprocess, "Popen", launch)
    monkeypatch.setattr(worker, "wait_for_calibration", fail)
    with pytest.raises(RuntimeError, match="foreground loss"):
        calibration.run_calibration(run.args, plan, backend=worker)
    report = assert_restored(run)
    assert report["settings_restored"] is True and run.process.terminated == 1
    assert report["control_source"] == "external_windows_SendInput"
