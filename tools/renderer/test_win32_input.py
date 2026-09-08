"""No real input: ABI fixtures and adversarial owned-process lifecycle checks."""
import copy
import ctypes
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("chicken_win32_input", Path(__file__).with_name("win32_input.py"))
adapter_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter_module)
Error = adapter_module.InputAdapterError


def key(name="W", pressed=True):
    return {"kind": "key", "key": name, "pressed": pressed}


def button(name="left", pressed=True):
    return {"kind": "mouse_button", "button": name, "pressed": pressed}


def move(dx=20, dy=-10):
    return {"kind": "mouse_move", "dx": dx, "dy": dy}


class FakeBackend:
    def __init__(self):
        self.state = {"target_pid": 4242, "process_alive": True,
            "process_identity_verified": True, "foreground_pid": 4242,
            "foreground_hwnd": 99, "foreground_matches": True,
            "creation_time_100ns": 100, "pinned_creation_time_100ns": 100}
        self.down = []
        self.sent = []
        self.results = []
        self.time = 10000
        self.closed = False
        self.after_insert = None
        self.inspect_error = None
        self.close_error = None

    def open(self, pid, path):
        self.opened = (pid, path)

    def inspect(self):
        if self.inspect_error:
            raise self.inspect_error
        return dict(self.state)

    def key_state(self):
        return self.down[:]

    def qpc_frequency(self):
        return 10_000_000

    def qpc(self):
        self.time += 10
        return self.time

    def send_input(self, events, extra_info):
        self.sent.append((copy.deepcopy(events), extra_info))
        if self.after_insert:
            self.after_insert()
        result = self.results.pop(0) if self.results else len(events)
        if isinstance(result, BaseException):
            raise result
        return {"inserted_count": result, "win32_error": 0 if result == len(events) else 5}

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def setup(tmp_path):
    executable = tmp_path / "cs2.exe"
    executable.write_bytes(b"fake, never executed")
    backend = FakeBackend()
    scope = {"verified": True, "run_id": "fixture-001", "pid": 4242,
             "local_loopback": True, "native_frame_qpc": 10000}
    obj = adapter_module.WindowsInputAdapter(4242, "fixture-001", lambda: dict(scope),
        expected_executable=executable, backend=backend)
    return obj, backend, scope


def test_windows_input_abi_and_scan_codes():
    assert ctypes.sizeof(adapter_module.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert adapter_module.INPUT.payload.offset == (8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4)
    data = adapter_module._make_inputs([key(), key(pressed=False), move(-40, 20), button(), button(pressed=False)], 0x43480001)
    assert data[0].type == 1 and data[0].ki.wVk == 0 and data[0].ki.wScan == 0x11
    assert data[0].ki.dwFlags == 0x08 and data[1].ki.dwFlags == 0x0A
    assert data[0].ki.time == 0 and data[0].ki.dwExtraInfo == 0x43480001
    assert data[2].mi.dx == -40 and data[2].mi.dy == 20 and data[2].mi.dwFlags == 1
    assert data[3].mi.dwFlags == 2 and data[4].mi.dwFlags == 4
    assert all(data[index].mi.mouseData == 0 for index in (2, 3, 4))


@pytest.mark.parametrize("name,scan", [("W", 0x11), ("A", 0x1E), ("S", 0x1F), ("D", 0x20),
    ("SPACE", 0x39), ("LCTRL", 0x1D), ("LSHIFT", 0x2A), ("R", 0x13)])
def test_all_supported_keys_use_scan_code_path(name, scan):
    data = adapter_module._make_inputs([key(name)], 1)[0]
    assert data.ki.wScan == scan and data.ki.dwFlags == 8 and data.ki.wVk == 0


def test_success_records_qpc_guards_and_releases_only_owned_controls(setup):
    obj, backend, scope = setup
    receipt = obj.send([key(), button("right"), move()])
    assert receipt["status"] == "inserted" and receipt["success"]
    assert receipt["requested_count"] == receipt["inserted_count"] == 3
    assert receipt["qpc_after"] > receipt["qpc_before"]
    assert receipt["qpc_frequency"] == 10_000_000
    assert receipt["guard_before"]["os"]["foreground_pid"] == 4242
    assert receipt["guard_after"]["scope"]["verified"] is True
    assert receipt["input_consumption_verified"] is False
    assert receipt["physical_device_latency_verified"] is False
    assert receipt["extra_info"] & 0x80 == 0
    scope["verified"] = False
    assert receipt["guard_after"]["scope"]["verified"] is True  # Detached provenance.
    released = obj.release_all("test")
    assert released["status"] == "released"
    assert released["events"] == [key(pressed=False), button("right", False)]
    assert obj.close()["status"] == "no_owned_inputs"
    assert backend.closed and len(backend.sent) == 2


def test_paired_batch_has_no_remaining_owned_controls(setup):
    obj, backend, _ = setup
    obj.send([key(), key(pressed=False), key(), key(pressed=False)])
    assert obj.release_all()["requested_count"] == 0
    assert len(backend.sent) == 1


@pytest.mark.parametrize("events", [[], [move()] * 9, [key("ESC")], [button("middle")],
    [{"kind": "key", "key": "W", "down": True}], [key(pressed=1)], [move(True, 0)],
    [move(1.2, 0)], [move(0, 0)], [move(201, 0)], [move(150, 0), move(51, 0)],
    [{**move(), "at_ms": 1}], [{"kind": []}], [key(pressed=False)], [key(), key()]])
def test_invalid_batches_send_nothing_and_fail_closed(setup, events):
    obj, backend, _ = setup
    with pytest.raises(Error) as caught:
        obj.send(events)
    assert caught.value.receipt["insertion_attempted"] is False
    assert not backend.sent
    with pytest.raises(Error, match="closed or failed"):
        obj.send([move()])


def test_invalid_batch_releases_existing_owned_input(setup):
    obj, backend, _ = setup
    obj.send([key()])
    with pytest.raises(Error) as caught:
        obj.send([key()])
    assert caught.value.cleanup_receipt["status"] == "released"
    assert backend.sent[-1][0] == [key(pressed=False)]


@pytest.mark.parametrize("field,value", [("process_alive", False), ("process_identity_verified", False),
    ("foreground_matches", False), ("foreground_pid", 999), ("foreground_hwnd", 0), ("target_pid", 999)])
def test_failed_os_precheck_never_sends_press(setup, field, value):
    obj, backend, _ = setup
    backend.state[field] = value
    with pytest.raises(Error) as caught:
        obj.send([key()])
    assert caught.value.receipt["inserted_count"] == 0
    assert not backend.sent
    assert caught.value.receipt["failed_guard"]["guard"]["os"][field] == value


def test_focus_lost_after_insertion_records_failure_and_only_releases(setup):
    obj, backend, _ = setup
    backend.after_insert = lambda: backend.state.update(foreground_matches=False, foreground_pid=900)
    with pytest.raises(Error) as caught:
        obj.send([key(), button()])
    assert caught.value.receipt["inserted_count"] == 2
    assert caught.value.receipt["success"] is False
    assert caught.value.cleanup_receipt["status"] == "released"
    assert backend.sent[-1][0] == [key(pressed=False), button(pressed=False)]
    assert caught.value.cleanup_receipt["os_observation"]["foreground_pid"] == 900


def test_scope_guard_failure_releases_held_keys_without_requiring_scope(setup):
    obj, backend, scope = setup
    obj.send([key()])
    scope["verified"] = False
    with pytest.raises(Error) as caught:
        obj.send([move()])
    assert len(backend.sent) == 2 and backend.sent[-1][0] == [key(pressed=False)]
    assert caught.value.receipt["insertion_attempted"] is False


def test_scope_postcheck_failure_does_not_claim_success(setup):
    obj, backend, scope = setup
    backend.after_insert = lambda: scope.update(verified=False)
    with pytest.raises(Error) as caught:
        obj.send([move()])
    assert caught.value.receipt["inserted_count"] == 1
    assert caught.value.receipt["success"] is False
    assert len(backend.sent) == 1  # Mouse displacement cannot be undone.


@pytest.mark.parametrize("result", [0, 1, 2])
def test_partial_insertions_release_every_possible_press_without_retry(setup, result):
    obj, backend, _ = setup
    backend.results = [result]
    with pytest.raises(Error) as caught:
        obj.send([key(), key(pressed=False), button()])
    assert caught.value.receipt["inserted_count"] == result
    # Do not assume the returned count identifies an inserted prefix.
    assert backend.sent[1][0] == [key(pressed=False), button(pressed=False)]
    assert len(backend.sent) == 2
    assert caught.value.cleanup_receipt["success"]


def test_unknown_insertion_count_is_not_reported_as_zero(setup):
    obj, backend, _ = setup
    backend.results = [OSError("backend broke after call")]
    with pytest.raises(Error) as caught:
        obj.send([key()])
    assert caught.value.receipt["inserted_count"] is None
    assert caught.value.receipt["insertion_attempted"] is True
    assert caught.value.cleanup_receipt["status"] == "released"


def test_cleanup_failure_keeps_original_error_and_conservative_ownership(setup):
    obj, backend, _ = setup
    backend.results = [OSError("original send failed"), 0]
    with pytest.raises(Error, match="original send failed") as caught:
        obj.send([key()])
    assert caught.value.cleanup_receipt["status"] == "release_incomplete"
    assert obj.close()["status"] == "released"  # Explicit retry is releases only.
    assert backend.sent[-1][0] == [key(pressed=False)]


def test_release_after_process_disappears_still_attempts_only_owned_releases(setup):
    obj, backend, _ = setup
    obj.send([key()])
    backend.inspect_error = OSError("process unavailable")
    receipt = obj.release_all("process_exited")
    assert receipt["status"] == "released" and "process unavailable" in receipt["os_observation_error"]
    assert backend.sent[-1][0] == [key(pressed=False)]


def test_keyboard_interrupt_during_send_is_preserved_after_cleanup(setup):
    obj, backend, _ = setup
    backend.results = [KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt) as caught:
        obj.send([key()])
    assert caught.value.cleanup_receipt["success"]
    assert backend.sent[-1][0] == [key(pressed=False)]


def test_context_exception_is_not_masked_by_cleanup_failure(setup):
    obj, backend, _ = setup
    with pytest.raises(ValueError, match="original application error") as caught:
        with obj:
            obj.send([key()])
            backend.results = [0]
            raise ValueError("original application error")
    assert caught.value.cleanup_receipt["status"] == "release_incomplete"
    assert backend.closed


def test_close_reports_failure_and_prevents_new_inputs(setup):
    obj, backend, _ = setup
    obj.send([key()])
    backend.results = [0]
    with pytest.raises(Error, match="cleanup was incomplete") as caught:
        obj.close()
    assert backend.closed and caught.value.cleanup_receipt["inserted_count"] == 0
    assert obj.release_all()["status"] == "release_incomplete"
    with pytest.raises(Error, match="closed or failed"):
        obj.send([move()])
    assert obj.close() == caught.value.receipt


@pytest.mark.parametrize("held", [["W"], ["RSHIFT"], ["LALT"], ["MOUSE_RIGHT"], ["LWIN"]])
def test_initial_held_inputs_are_rejected_without_forced_release(tmp_path, held):
    executable = tmp_path / "cs2.exe"
    executable.touch()
    backend = FakeBackend()
    backend.down = held
    with pytest.raises(Error, match="initially be up") as caught:
        adapter_module.WindowsInputAdapter(4242, "fixture", lambda: {"verified": True},
            expected_executable=executable, backend=backend)
    assert caught.value.receipt["observed_keys_down"] == held
    assert not backend.sent and backend.closed


@pytest.mark.parametrize("scope", [{"verified": 1}, {"verified": False}, {}, None, {"verified": True, "qpc": float("nan")}])
def test_initial_scope_must_be_finite_explicit_verified_json(tmp_path, scope):
    executable = tmp_path / "cs2.exe"
    executable.touch()
    backend = FakeBackend()
    with pytest.raises(Error):
        adapter_module.WindowsInputAdapter(4242, "fixture", lambda: scope,
            expected_executable=executable, backend=backend)
    assert not backend.sent and backend.closed


@pytest.mark.parametrize("os_changes,verified", [
    ({"foreground_pid": 999, "foreground_matches": False}, True),
    ({"process_identity_verified": False}, True),
    ({}, False),
])
def test_preflight_preserves_failed_os_and_native_guard_evidence(tmp_path, os_changes, verified):
    executable = tmp_path / "cs2.exe"
    executable.touch()
    backend = FakeBackend()
    backend.state.update(os_changes)
    scope = {"verified": verified, "native_frame_qpc": 10000, "frame_age_seconds": 0.025}
    with pytest.raises(Error, match="guard failed") as caught:
        adapter_module.WindowsInputAdapter(4242, "fixture", lambda: scope,
            expected_executable=executable, backend=backend)
    receipt = caught.value.receipt
    assert receipt["operation"] == "preflight"
    assert receipt["insertion_attempted"] is False
    assert receipt["inserted_count"] == receipt["requested_count"] == 0
    assert receipt["success"] is False
    assert receipt["failed_guard"]["guard"] == {"os": backend.state, "scope": scope}
    assert backend.closed and not backend.sent
    # A later callback/state mutation cannot rewrite the captured failure.
    before = copy.deepcopy(receipt["failed_guard"])
    scope["verified"] = not verified
    backend.state["foreground_pid"] = 12345
    assert receipt["failed_guard"] == before


def test_preflight_close_error_does_not_replace_failed_guard(tmp_path):
    executable = tmp_path / "cs2.exe"
    executable.touch()
    backend = FakeBackend()
    backend.state.update(foreground_matches=False, foreground_pid=999)
    backend.close_error = OSError("fixture close failed")
    with pytest.raises(Error, match="guard failed") as caught:
        adapter_module.WindowsInputAdapter(4242, "fixture", lambda: {"verified": True},
            expected_executable=executable, backend=backend)
    receipt = caught.value.receipt
    assert receipt["failed_guard"]["guard"]["os"]["foreground_pid"] == 999
    assert receipt["close_error"] == "fixture close failed"
    assert backend.closed and not backend.sent


def test_qpc_failure_after_insertion_preserves_known_count(setup):
    obj, backend, _ = setup
    original = backend.qpc
    calls = 0
    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("QPC failed")
        return original()
    backend.qpc = fail_once
    with pytest.raises(Error) as caught:
        obj.send([key()])
    assert caught.value.receipt["inserted_count"] == 1
    assert caught.value.receipt["qpc_after"] is None
    assert caught.value.cleanup_receipt["success"]


def test_non_windows_public_clock_fails_without_input(monkeypatch):
    monkeypatch.setattr(adapter_module.os, "name", "posix")
    with pytest.raises(OSError, match="requires Windows"):
        adapter_module.query_performance_counter()


def test_invalid_constructor_arguments_are_rejected_before_backend_open(tmp_path):
    backend = FakeBackend()
    executable = tmp_path / "cs2.exe"
    executable.touch()
    for pid, run_id, path in [(True, "fixture", executable), (0, "fixture", executable),
                              (4242, "../escape", executable), (4242, "fixture", tmp_path / "other.exe")]:
        with pytest.raises(ValueError):
            adapter_module.WindowsInputAdapter(pid, run_id, lambda: {"verified": True},
                expected_executable=path, backend=backend)
    assert not hasattr(backend, "opened")
