"""Scoped high-resolution waitable timer for the Windows input polling worker.

No timeBeginPeriod, process priorities, settings, input, or background threads.
The HIGH_RESOLUTION creation flag requires Windows 10 version 1803 or later;
unsupported platforms fail rather than falling back to a less precise sleep.
Timer signaling does not guarantee that Python resumes by a particular deadline.
Always retain the worker's measured frame-age, schedule and held-control guards.

Microsoft primary API contracts:
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-setwaitabletimerex
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject
"""
from __future__ import annotations

import ctypes
import math
import os
import threading


PROFILE = "windows_high_resolution_waitable_poll_timer_v1"
CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002
TIMER_MODIFY_STATE = 0x00000002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


class Win32TimerBackend:
    """Win32 boundary with fixed-width declarations and no process handles."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("High-resolution Windows polling requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateWaitableTimerExW": ([ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32], ctypes.c_void_p),
            "SetWaitableTimerEx": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64), ctypes.c_int32,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "WaitForSingleObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
            "CancelWaitableTimer": ([ctypes.c_void_p], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
            "QueryPerformanceCounter": ([ctypes.POINTER(ctypes.c_int64)], ctypes.c_int),
            "QueryPerformanceFrequency": ([ctypes.POINTER(ctypes.c_int64)], ctypes.c_int),
        }
        try:
            for name, (arguments, result) in declarations.items():
                function = getattr(self.kernel, name)
                function.argtypes, function.restype = arguments, result
        except AttributeError as error:
            raise OSError("Required high-resolution waitable-timer API is unavailable") from error

    def create(self):
        # An unnamed, noninheritable auto-reset timer belongs only to this scope.
        handle = self.kernel.CreateWaitableTimerExW(None, None,
            CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, "Cannot create required high-resolution waitable timer; no sleep fallback is used")
        return handle

    def arm(self, handle, relative_100ns):
        due = ctypes.c_int64(relative_100ns)
        if not self.kernel.SetWaitableTimerEx(handle, ctypes.byref(due), 0, None, None, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, handle, timeout_ms):
        result = int(self.kernel.WaitForSingleObject(handle, timeout_ms))
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def cancel(self, handle):
        if not self.kernel.CancelWaitableTimer(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def _counter(self, name):
        result = ctypes.c_int64()
        if not getattr(self.kernel, name)(ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.value

    def qpc(self):
        return self._counter("QueryPerformanceCounter")

    def qpc_frequency(self):
        return self._counter("QueryPerformanceFrequency")


class WindowsPollTimer:
    """Use as a context manager; wait() arms one relative, one-shot timer.

    A test backend supplies create/arm/wait/cancel/close/qpc/qpc_frequency.
    Calls are serialized so close cannot invalidate a pending wait's handle.
    Waiting or timing errors close the timer and disable further waits.
    """

    def __init__(self, *, backend=None):
        self._backend = backend if backend is not None else Win32TimerBackend()
        self._lock = threading.RLock()
        self._handle = None
        self._closed = False
        try:
            self._handle = self._backend.create()
            if not self._handle:
                raise OSError("Required high-resolution timer creation returned no handle")
            self.frequency = self._backend.qpc_frequency()
            if type(self.frequency) is not int or self.frequency <= 0:
                raise ValueError("High-resolution poll timer has no valid QPC frequency")
        except BaseException as error:
            self._close_preserving(error)
            raise

    def wait(self, milliseconds=2):
        if type(milliseconds) not in (int, float) or not math.isfinite(milliseconds) or not 0 < milliseconds <= 1000:
            raise ValueError("Poll timer interval must be finite and greater than zero, at most 1000 milliseconds")
        relative_100ns = -math.ceil(milliseconds * 10_000)
        watchdog_ms = max(1000, math.ceil(milliseconds) + 100)
        with self._lock:
            if self._closed:
                raise RuntimeError("Poll timer is closed")
            try:
                before = self._backend.qpc()
                self._backend.arm(self._handle, relative_100ns)
                result = self._backend.wait(self._handle, watchdog_ms)
                after = self._backend.qpc()
                if result == WAIT_TIMEOUT:
                    raise TimeoutError("High-resolution poll timer did not signal before its watchdog")
                if type(result) is not int or result != WAIT_OBJECT_0:
                    raise OSError("Unexpected wait result for high-resolution poll timer: " + str(result))
                if type(before) is not int or type(after) is not int or after < before:
                    raise ValueError("Poll timer returned an invalid QPC interval")
                return {"profile": PROFILE, "high_resolution_timer_created": True,
                        "requested_milliseconds": milliseconds, "relative_due_time_100ns": relative_100ns,
                        "qpc_frequency": self.frequency, "qpc_before": before, "qpc_after": after,
                        "elapsed_milliseconds": (after - before) * 1000 / self.frequency,
                        "wait_result": result, "watchdog_milliseconds": watchdog_ms,
                        "wakeup_deadline_verified": False}
            except BaseException as error:
                self._close_preserving(error)
                raise

    def _close_preserving(self, error):
        try:
            self.close()
        except BaseException as cleanup:
            error.timer_cleanup_error = str(cleanup)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handle, self._handle = self._handle, None
            if handle is None:
                return
            error = None
            try:
                self._backend.cancel(handle)
            except BaseException as failure:
                error = failure
            finally:
                try:
                    self._backend.close(handle)
                except BaseException as failure:
                    if error is None:
                        error = failure
                    else:
                        error.timer_handle_close_error = str(failure)
            if error is not None:
                raise error

    def __enter__(self):
        if self._closed:
            raise RuntimeError("Cannot enter a closed poll timer")
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None:
            self._close_preserving(exc)
        else:
            self.close()
        return False
