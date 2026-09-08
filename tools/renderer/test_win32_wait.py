"""Waitable-timer fixtures; no real sleep, global resolution change or input."""
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("chicken_win32_wait", Path(__file__).with_name("win32_wait.py"))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class Backend:
    def __init__(self):
        self.calls = []
        self.result = 0
        self.tick = 100_000
        self.frequency = 10_000_000
        self.create_error = self.arm_error = self.wait_error = self.cancel_error = self.close_error = None

    def create(self):
        self.calls.append(("create",))
        if self.create_error:
            raise self.create_error
        return 999

    def qpc_frequency(self):
        return self.frequency

    def qpc(self):
        self.tick += 20_100
        return self.tick

    def arm(self, handle, ticks):
        self.calls.append(("arm", handle, ticks))
        if self.arm_error:
            raise self.arm_error

    def wait(self, handle, timeout):
        self.calls.append(("wait", handle, timeout))
        if self.wait_error:
            raise self.wait_error
        return self.result

    def cancel(self, handle):
        self.calls.append(("cancel", handle))
        if self.cancel_error:
            raise self.cancel_error

    def close(self, handle):
        self.calls.append(("close", handle))
        if self.close_error:
            raise self.close_error


def test_default_wait_is_relative_one_shot_and_records_actual_interval():
    backend = Backend()
    with module.WindowsPollTimer(backend=backend) as timer:
        receipt = timer.wait()
        assert receipt["relative_due_time_100ns"] == -20_000
        assert receipt["requested_milliseconds"] == 2
        assert receipt["elapsed_milliseconds"] == 2.01
        assert receipt["wakeup_deadline_verified"] is False
        assert receipt["qpc_frequency"] == 10_000_000
    assert backend.calls == [("create",), ("arm", 999, -20_000), ("wait", 999, 1000), ("cancel", 999), ("close", 999)]


def test_win32_calls_request_high_resolution_and_zero_period_without_global_settings():
    calls = []
    def create(attributes, name, flags, access):
        calls.append(("create", attributes, name, flags, access))
        return 999
    def arm(handle, due, period, callback, context, wake, delay):
        ticks = ctypes.cast(due, ctypes.POINTER(ctypes.c_int64)).contents.value
        calls.append(("arm", handle, ticks, period, callback, context, wake, delay))
        return 1
    backend = object.__new__(module.Win32TimerBackend)
    backend.kernel = SimpleNamespace(CreateWaitableTimerExW=create, SetWaitableTimerEx=arm)
    assert backend.create() == 999
    backend.arm(999, -20_000)
    assert calls == [("create", None, None, 0x2, 0x00100002),
                     ("arm", 999, -20_000, 0, None, None, None, 0)]


def test_repeated_waits_rearm_same_scoped_timer_and_close_is_idempotent():
    backend = Backend()
    timer = module.WindowsPollTimer(backend=backend)
    timer.wait(2)
    timer.wait(0.10001)
    timer.close()
    timer.close()
    assert [call for call in backend.calls if call[0] == "create"] == [("create",)]
    assert [call for call in backend.calls if call[0] == "arm"] == [("arm", 999, -20_000), ("arm", 999, -1001)]
    assert backend.calls.count(("close", 999)) == 1
    with pytest.raises(RuntimeError, match="closed"):
        timer.wait()


@pytest.mark.parametrize("value", [0, -2, 1001, True, "2", None, float("nan"), float("inf")])
def test_invalid_interval_never_arms_timer(value):
    backend = Backend()
    with module.WindowsPollTimer(backend=backend) as timer:
        with pytest.raises(ValueError, match="interval"):
            timer.wait(value)
    assert not any(call[0] == "arm" for call in backend.calls)


def test_creation_failure_has_no_low_resolution_fallback():
    backend = Backend()
    backend.create_error = OSError("HIGH_RESOLUTION flag unsupported")
    with pytest.raises(OSError, match="unsupported"):
        module.WindowsPollTimer(backend=backend)
    assert backend.calls == [("create",)]


def test_missing_qpc_frequency_closes_created_timer():
    backend = Backend()
    backend.frequency = 0
    with pytest.raises(ValueError, match="frequency"):
        module.WindowsPollTimer(backend=backend)
    assert backend.calls[-2:] == [("cancel", 999), ("close", 999)]


@pytest.mark.parametrize("phase", ["arm", "wait"])
def test_native_failure_closes_timer_and_disables_later_waits(phase):
    backend = Backend()
    setattr(backend, phase + "_error", OSError("fixture " + phase + " failure"))
    timer = module.WindowsPollTimer(backend=backend)
    with pytest.raises(OSError, match=phase + " failure"):
        timer.wait()
    assert backend.calls[-2:] == [("cancel", 999), ("close", 999)]
    with pytest.raises(RuntimeError, match="closed"):
        timer.wait()


def test_watchdog_timeout_is_failure_not_claimed_precision():
    backend = Backend()
    backend.result = module.WAIT_TIMEOUT
    with pytest.raises(TimeoutError, match="watchdog"):
        with module.WindowsPollTimer(backend=backend) as timer:
            timer.wait()
    assert backend.calls[-1] == ("close", 999)


@pytest.mark.parametrize("result", [module.WAIT_FAILED, 128, None, False])
def test_unexpected_wait_result_fails_closed(result):
    backend = Backend()
    backend.result = result
    with pytest.raises(OSError, match="Unexpected"):
        with module.WindowsPollTimer(backend=backend) as timer:
            timer.wait()
    assert backend.calls[-1] == ("close", 999)


def test_cancel_failure_still_closes_handle():
    backend = Backend()
    backend.cancel_error = OSError("cancel failed")
    timer = module.WindowsPollTimer(backend=backend)
    with pytest.raises(OSError, match="cancel failed"):
        timer.close()
    assert backend.calls[-1] == ("close", 999)


def test_original_exception_survives_cleanup_failures():
    backend = Backend()
    backend.cancel_error = OSError("cancel failed")
    backend.close_error = OSError("close failed")
    with pytest.raises(ValueError, match="original") as caught:
        with module.WindowsPollTimer(backend=backend):
            raise ValueError("original")
    assert caught.value.timer_cleanup_error == "cancel failed"
    assert backend.calls[-1] == ("close", 999)


def test_keyboard_interrupt_still_closes_timer():
    backend = Backend()
    backend.wait_error = KeyboardInterrupt("interrupt")
    with pytest.raises(KeyboardInterrupt):
        with module.WindowsPollTimer(backend=backend) as timer:
            timer.wait()
    assert backend.calls[-1] == ("close", 999)


def test_real_backend_rejects_non_windows_platform(monkeypatch):
    monkeypatch.setattr(module.os, "name", "posix")
    with pytest.raises(OSError, match="requires Windows"):
        module.WindowsPollTimer()
