"""Worker/real-adapter integration with fake Win32, game process and bridge.

No game is launched and no Windows input API is called. These fixtures exercise
the boundary between scheduling, final native guards and failure cleanup.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("synthetic_windows_runtime_fixture", ROOT / "tools/renderer/synthetic_windows.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
import win32_input
import win32_wait


class FakeBackend:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.closed = False
        self.on_send = None
        self.close_error = None
        self.foreground = True

    def open(self, pid, expected):
        self.pid, self.expected = pid, expected

    def inspect(self):
        return {"target_pid": self.pid, "process_alive": True,
                "process_identity_verified": True, "foreground_matches": self.foreground,
                "foreground_pid": self.pid if self.foreground else 999,
                "foreground_hwnd": 12345}

    def key_state(self):
        return []

    def qpc_frequency(self):
        return 1_000_000

    def qpc(self):
        return self.clock()

    def send_input(self, events, extra_info):
        self.sent.append([dict(event) for event in events])
        if self.on_send:
            callback, self.on_send = self.on_send, None
            callback()
        return {"inserted_count": len(events), "win32_error": 0}

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeBridge:
    def __init__(self, path, plan, pid, clock, frequency):
        self.frame = {"elapsed_ms": 500.0, "qpc": 999_000}
        self.pid, self.plan = pid, plan
        self.clock = clock
        self.closed = False

    def read_more(self):
        pass

    def scope_guard(self):
        return {"verified": True, "owned_process_id": self.pid,
                "run_id": self.plan["movie_name"].removeprefix("calibration-"),
                "local_connection_verified": True, "local_connection": "loopback",
                "elapsed_ms": self.frame["elapsed_ms"], "frame_qpc": self.frame["qpc"]}

    def close(self):
        self.closed = True


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    executable = tmp_path / "cs2.exe"
    executable.write_bytes(b"fixture, not executable")
    plan = {"schema_version": 1, "producer": worker.PROFILE, "map": "de_dust2",
            "fps": 32, "width": 1280, "height": 720, "duration_seconds": 2,
            "movie_name": "calibration-" + "a" * 32, "demo_path": str(tmp_path / "controlled.dem"),
            "events": [{"id": "move", "at_ms": 500, "kind": "mouse_move", "dx": 20, "dy": 0}]}
    (tmp_path / "native-plan.json").write_text(json.dumps(plan))
    tick = [1_000_000]
    def clock():
        tick[0] += 100
        return tick[0]
    backend = FakeBackend(clock)
    state = SimpleNamespace(out=tmp_path, plan=plan, backend=backend, adapter=None, bridge=None,
                            before_send=None, created=0, clock=clock, timer=None,
                            timer_creation_error=None, timer_wait_error=None)
    polls = iter((None, 0))
    process = SimpleNamespace(pid=123, args=[str(executable)], returncode=0,
                              poll=lambda: next(polls, 0))
    state.process = process
    real_adapter = win32_input.WindowsInputAdapter
    def create_adapter(pid, run_id, scope_guard, **kwargs):
        state.created += 1
        adapter = real_adapter(pid, run_id, scope_guard, backend=backend, **kwargs)
        state.adapter = adapter
        original_send = adapter.send
        def send(events):
            # Model newly flushed native state after scheduling, immediately
            # before the real adapter asks the worker's final scope guard.
            if state.before_send:
                callback, state.before_send = state.before_send, None
                callback()
            return original_send(events)
        adapter.send = send
        return adapter
    def create_bridge(*args):
        state.bridge = FakeBridge(*args)
        return state.bridge
    class FakeTimer:
        def __init__(self):
            if state.timer_creation_error:
                raise state.timer_creation_error
            self.closed = False
            self.waits = []
            state.timer = self
        def wait(self, milliseconds=2):
            self.waits.append(milliseconds)
            if state.timer_wait_error:
                raise state.timer_wait_error
        def close(self):
            self.closed = True
    monkeypatch.setattr(win32_input, "WindowsInputAdapter", create_adapter)
    monkeypatch.setattr(win32_input, "query_performance_counter", clock)
    monkeypatch.setattr(win32_input, "query_performance_frequency", lambda: 1_000_000)
    monkeypatch.setattr(worker, "NativeBridge", create_bridge)
    monkeypatch.setattr(win32_wait, "WindowsPollTimer", FakeTimer)
    monkeypatch.setattr(worker.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    def run():
        return worker.wait_for_calibration(process, [], plan, tmp_path, timeout=60)
    state.run = run
    yield state
    if state.timer is not None:
        assert state.timer.closed  # Every successful and failed worker path.


def ledger_rows(runtime):
    return [json.loads(line) for line in (runtime.out / "input_ledger.jsonl").read_text().splitlines()]


def fail_log_events(runtime, monkeypatch, events):
    original_open = Path.open
    class FailingLog:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def write(self, text):
            event = json.loads(text)["event"]
            if event in events:
                raise OSError("fixture log failure: " + event)
            return self.handle.write(text)
        def flush(self):
            self.handle.flush()
    def patched_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == runtime.out / "input_ledger.jsonl" and args and args[0] == "x":
            return FailingLog(handle)
        return handle
    monkeypatch.setattr(Path, "open", patched_open)


def held_key_plan(runtime):
    runtime.plan["events"] = [
        {"id": "press", "at_ms": 500, "kind": "key", "key": "W", "pressed": True},
        {"id": "release", "at_ms": 1000, "kind": "key", "key": "W", "pressed": False}]
    (runtime.out / "native-plan.json").write_text(json.dumps(runtime.plan))


def test_advancing_frame_records_final_preinsertion_scope(runtime):
    runtime.before_send = lambda: runtime.bridge.frame.update(elapsed_ms=515.625, qpc=1_000_050)
    assert runtime.run() == 0
    batch = next(row for row in ledger_rows(runtime) if row["event"] == "injection_batch")
    assert batch["scheduled_at_ms"] == 500
    assert batch["actual_elapsed_ms"] == 515.625
    assert batch["native_frame_qpc"] == 1_000_050
    assert batch["receipt"]["guard_before"]["scope"]["frame_qpc"] == batch["native_frame_qpc"]
    assert batch["receipt"]["requested_count"] == batch["receipt"]["inserted_count"] == 1
    assert runtime.backend.closed and runtime.bridge.closed


def test_advancing_frame_past_lateness_limit_blocks_insertion(runtime):
    runtime.before_send = lambda: runtime.bridge.frame.update(elapsed_ms=640.625, qpc=1_000_050)
    with pytest.raises(win32_input.InputAdapterError, match="lateness bound"):
        runtime.run()
    assert runtime.backend.sent == []
    assert runtime.backend.closed and runtime.bridge.closed
    assert not any(row["event"] == "injection_batch" for row in ledger_rows(runtime))


def test_advancing_frame_to_second_boundary_cannot_collapse_batches(runtime):
    runtime.plan["events"].append({"id": "next", "at_ms": 600, "kind": "mouse_move", "dx": -20, "dy": 0})
    runtime.before_send = lambda: runtime.bridge.frame.update(elapsed_ms=609.375, qpc=1_000_050)
    with pytest.raises(win32_input.InputAdapterError, match="collapse"):
        runtime.run()
    assert runtime.backend.sent == []
    assert runtime.backend.closed and runtime.bridge.closed


def test_inserted_press_may_cross_next_boundary_then_release_uses_fresh_scope(runtime):
    runtime.plan["events"] = [
        {"id": "attack-press", "at_ms": 500, "kind": "mouse_button", "button": "left", "pressed": True},
        {"id": "attack-release", "at_ms": 532, "kind": "mouse_button", "button": "left", "pressed": False}]
    polls = iter((None, None, 0))
    runtime.process.poll = lambda: next(polls, 0)
    # The press has already been inserted when native simulation passes the
    # release boundary. Postguard must still verify scope, without applying the
    # old batch's scheduling guard to an already completed insertion.
    runtime.backend.on_send = lambda: runtime.bridge.frame.update(elapsed_ms=546.875, qpc=1_000_050)
    assert runtime.run() == 0
    batches = [row for row in ledger_rows(runtime) if row["event"] == "injection_batch"]
    assert len(batches) == 2
    assert batches[0]["actual_elapsed_ms"] == 500.0
    assert batches[0]["receipt"]["guard_after"]["scope"]["elapsed_ms"] == 546.875
    assert batches[1]["scheduled_at_ms"] == 532
    assert batches[1]["actual_elapsed_ms"] == 546.875
    assert all(row["receipt"]["success"] for row in batches)
    assert runtime.backend.sent == [
        [{"kind": "mouse_button", "button": "left", "pressed": True}],
        [{"kind": "mouse_button", "button": "left", "pressed": False}]]
    assert runtime.backend.closed and runtime.bridge.closed


def test_failed_injection_log_still_releases_held_control_and_closes(runtime, monkeypatch):
    held_key_plan(runtime)
    fail_log_events(runtime, monkeypatch, {"injection_batch", "input_failed", "cleanup"})
    with pytest.raises(OSError, match="fixture log failure: injection_batch") as caught:
        runtime.run()
    assert runtime.backend.sent == [
        [{"kind": "key", "key": "W", "pressed": True}],
        [{"kind": "key", "key": "W", "pressed": False}]]
    assert runtime.backend.closed and runtime.bridge.closed
    assert "input_failed" in caught.value.input_logging_error
    assert "cleanup" in caught.value.input_cleanup_error


@pytest.mark.parametrize("error", [RuntimeError("original dispatch failure"), KeyboardInterrupt("original interruption")])
def test_original_send_failure_survives_log_and_close_failures(runtime, monkeypatch, error):
    held_key_plan(runtime)
    fail_log_events(runtime, monkeypatch, {"input_failed", "cleanup"})
    def fail_send():
        runtime.backend.close_error = OSError("secondary handle-close failure")
        raise error
    runtime.backend.on_send = fail_send
    expected_type = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else win32_input.InputAdapterError
    with pytest.raises(expected_type, match=str(error)) as caught:
        runtime.run()
    assert runtime.backend.sent[-1] == [{"kind": "key", "key": "W", "pressed": False}]
    assert runtime.backend.closed and runtime.bridge.closed
    assert caught.value.cleanup_receipt["status"] == "released"
    assert "input_failed" in caught.value.input_logging_error
    assert "cleanup was incomplete" in caught.value.input_cleanup_error


def test_cleanup_log_failure_does_not_skip_adapter_close(runtime, monkeypatch):
    fail_log_events(runtime, monkeypatch, {"cleanup"})
    with pytest.raises(OSError, match="fixture log failure: cleanup"):
        runtime.run()
    assert runtime.backend.closed and runtime.bridge.closed
    assert len(runtime.backend.sent) == 1  # One mouse move; no pretend inverse cleanup movement.


def test_adapter_ready_log_failure_closes_preflight_handle_without_input(runtime, monkeypatch):
    fail_log_events(runtime, monkeypatch, {"adapter_ready"})
    with pytest.raises(OSError, match="adapter_ready"):
        runtime.run()
    assert runtime.created == 1 and runtime.backend.closed and runtime.bridge.closed
    assert runtime.backend.sent == []


def test_adapter_creation_failure_keeps_no_owned_inputs_and_closes_bridge(runtime):
    runtime.backend.foreground = False
    with pytest.raises(win32_input.InputAdapterError, match="foreground"):
        runtime.run()
    assert runtime.adapter is None
    assert runtime.backend.closed and runtime.bridge.closed and runtime.backend.sent == []


def test_timer_creation_failure_never_constructs_adapter_or_sends_input(runtime):
    runtime.timer_creation_error = OSError("high-resolution timer unavailable")
    with pytest.raises(OSError, match="timer unavailable"):
        runtime.run()
    assert runtime.created == 0 and runtime.backend.sent == []
    assert runtime.bridge.closed and runtime.timer is None


def test_timer_wait_failure_releases_held_input_before_returning_to_process_owner(runtime):
    held_key_plan(runtime)
    runtime.timer_wait_error = TimeoutError("timer watchdog elapsed")
    with pytest.raises(TimeoutError, match="timer watchdog"):
        runtime.run()
    assert runtime.timer.waits == [2]
    assert runtime.backend.sent == [
        [{"kind": "key", "key": "W", "pressed": True}],
        [{"kind": "key", "key": "W", "pressed": False}]]
    assert runtime.backend.closed and runtime.bridge.closed and runtime.timer.closed
