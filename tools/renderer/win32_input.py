"""Scoped Windows SendInput adapter for an owned, foreground CS2 process.

SendInput is a global desktop API, not a PID-targeted transport. The foreground
can change between a check and insertion; a successful receipt proves insertion
only, never CS2 consumption or physical-device latency. Relative mouse counts
are not promised to equal raw hardware counts or camera degrees.

The caller supplies a fresh native local-session scope_guard. No focus forcing,
process launching, settings changes, background threads or hooks occur here.
Cleanup emits only releases for this adapter's possibly pressed controls, even
after focus or scope is lost. Such releases can reach the newly focused window.
GetAsyncKeyState describes observed OS state, not physical input provenance.

Primary contracts: Microsoft SendInput, KEYBDINPUT, MOUSEINPUT,
GetAsyncKeyState, QueryPerformanceCounter and QueryFullProcessImageNameW docs.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable


SCANCODES = {"W": 0x11, "A": 0x1E, "S": 0x1F, "D": 0x20,
             "SPACE": 0x39, "LCTRL": 0x1D, "LSHIFT": 0x2A, "R": 0x13}
VIRTUAL_KEYS = {"W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44, "R": 0x52,
                "SPACE": 0x20, "LCTRL": 0xA2, "RCTRL": 0xA3,
                "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LALT": 0xA4,
                "RALT": 0xA5, "LWIN": 0x5B, "RWIN": 0x5C,
                "MOUSE_LEFT": 0x01, "MOUSE_RIGHT": 0x02,
                "MOUSE_MIDDLE": 0x04, "MOUSE_X1": 0x05, "MOUSE_X2": 0x06}
MAX_BATCH_EVENTS = 8
MAX_BATCH_MOUSE_COUNTS = 200
PROVENANCE = "windows_sendinput"


def _query_performance(name):
    if os.name != "nt":
        raise OSError("The native QPC clock requires Windows")
    function = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
    function.argtypes = [ctypes.POINTER(ctypes.c_int64)]
    function.restype = ctypes.c_int
    value = ctypes.c_int64()
    if not function(ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return value.value


def query_performance_counter():
    """Read the Windows QPC clock used by the native frame bridge."""
    return _query_performance("QueryPerformanceCounter")


def query_performance_frequency():
    """Return native QPC units per second, distinct from simulation ticks."""
    value = _query_performance("QueryPerformanceFrequency")
    if value <= 0:
        raise OSError("The native QPC frequency is invalid")
    return value

# Fixed-width Windows ABI types also permit layout tests on non-Windows hosts.
WORD, DWORD, LONG, ULONG_PTR = ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int32, ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", DWORD), ("payload", INPUT_UNION)]


class FILETIME(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class InputAdapterError(RuntimeError):
    """Failure with auditable attempted insertion and best-effort cleanup."""

    def __init__(self, message, *, receipt=None, cleanup_receipt=None):
        super().__init__(message)
        self.receipt = receipt
        self.cleanup_receipt = cleanup_receipt


def _detached(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def _make_inputs(events, extra_info):
    """Build only the already validated scan-code and relative mouse inputs."""
    result = (INPUT * len(events))()
    for item, event in zip(result, events):
        kind = event["kind"]
        if kind == "key":
            item.type = 1
            item.ki = KEYBDINPUT(0, SCANCODES[event["key"]],
                0x0008 | (0 if event["pressed"] else 0x0002), 0, extra_info)
        elif kind == "mouse_move":
            item.type = 0
            item.mi = MOUSEINPUT(event["dx"], event["dy"], 0, 0x0001, 0, extra_info)
        else:
            flags = {("left", True): 0x0002, ("left", False): 0x0004,
                     ("right", True): 0x0008, ("right", False): 0x0010}
            item.type = 0
            item.mi = MOUSEINPUT(0, 0, 0, flags[event["button"], event["pressed"]], 0, extra_info)
    return result


class Win32Backend:
    """Small injectable boundary; the production backend pins a process handle."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("The real input backend requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.handle = None
        self.kernel.OpenProcess.argtypes = [DWORD, ctypes.c_int, DWORD]
        self.kernel.OpenProcess.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_int
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, DWORD]
        self.kernel.WaitForSingleObject.restype = DWORD
        self.kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = ctypes.c_int
        self.kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
        for name in ("QueryPerformanceCounter", "QueryPerformanceFrequency"):
            function = getattr(self.kernel, name)
            function.argtypes = [ctypes.POINTER(ctypes.c_int64)]
            function.restype = ctypes.c_int
        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        self.user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWORD)]
        self.user.GetWindowThreadProcessId.restype = DWORD
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_int16
        self.user.SendInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user.SendInput.restype = ctypes.c_uint32

    def open(self, pid, expected_executable):
        self.pid = pid
        self.expected_executable = _path_key(expected_executable)
        self.handle = self.kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.creation_time, image = self._identity()
            if _path_key(image) != self.expected_executable:
                raise ValueError("Owned process executable does not match expected cs2.exe")
        except BaseException:
            self.close()
            raise

    def _identity(self):
        creation, exit_time, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not self.kernel.GetProcessTimes(self.handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (creation.high << 32) | creation.low, image.value

    def inspect(self):
        wait = self.kernel.WaitForSingleObject(self.handle, 0)
        if wait not in (0, 258):
            raise OSError("Cannot verify owned process liveness")
        creation, image = self._identity()
        hwnd = self.user.GetForegroundWindow()
        foreground = DWORD()
        if hwnd and not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(foreground)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"target_pid": self.pid, "process_alive": wait == 258,
                "process_identity_verified": creation == self.creation_time and _path_key(image) == self.expected_executable,
                "creation_time_100ns": creation, "pinned_creation_time_100ns": self.creation_time,
                "executable": image, "expected_executable": self.expected_executable,
                "foreground_hwnd": int(hwnd or 0), "foreground_pid": foreground.value,
                "foreground_matches": bool(hwnd) and foreground.value == self.pid}

    def key_state(self):
        return [name for name, key in VIRTUAL_KEYS.items() if self.user.GetAsyncKeyState(key) & 0x8000]

    def qpc_frequency(self):
        value = ctypes.c_int64()
        if not self.kernel.QueryPerformanceFrequency(ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def qpc(self):
        value = ctypes.c_int64()
        if not self.kernel.QueryPerformanceCounter(ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def send_input(self, events, extra_info):
        inputs = _make_inputs(events, extra_info)
        ctypes.set_last_error(0)
        inserted = int(self.user.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)))
        return {"inserted_count": inserted, "win32_error": ctypes.get_last_error()}

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


class WindowsInputAdapter:
    """One synchronous owned-process input session; no implicit foregrounding.

    scope_guard() must return a JSON-compatible dict with verified exactly True.
    It is called before and after every batch; the caller owns freshness and
    local-session semantics. A backend used in tests implements open, inspect,
    key_state, qpc, qpc_frequency, send_input and close like Win32Backend.
    """

    def __init__(self, pid: int, run_id: str, scope_guard: Callable[[], dict], *,
                 expected_executable, backend=None):
        if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF:
            raise ValueError("Expected a positive Windows process ID")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("Expected a bounded run identifier")
        expected = Path(expected_executable)
        if not expected.is_absolute() or expected.name.lower() != "cs2.exe" or not expected.is_file():
            raise ValueError("expected_executable must be an existing absolute cs2.exe path")
        if not callable(scope_guard):
            raise ValueError("A native scope_guard callback is required")
        self.pid, self.run_id, self.scope_guard = pid, run_id, scope_guard
        self.backend = backend if backend is not None else Win32Backend()
        self._owned = set()
        self._failed = self._closed = False
        self._lock = threading.RLock()
        self._last_close_receipt = None
        # Avoid SDL's touch marker range and bit 0x80; this is identification,
        # not an attempt to disguise the events' injected provenance.
        self.extra_info = 0x43480000 | (int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:2], "little") & 0x7F)
        self.frequency = None
        preflight = self._receipt("preflight", [])
        try:
            self.backend.open(pid, expected.resolve())
            self.frequency = self.backend.qpc_frequency()
            if type(self.frequency) is not int or self.frequency <= 0:
                raise ValueError("QPC frequency is unavailable")
            preflight["qpc_frequency"] = self.frequency
            preflight["guard_before"] = self._guard()
            held = self.backend.key_state()
            if not isinstance(held, list) or any(not isinstance(item, str) for item in held):
                raise ValueError("Invalid initial OS key-state observation")
            preflight["observed_keys_down"] = held
            if held:
                raise ValueError("Relevant keys, mouse buttons and modifiers must initially be up")
            preflight.update(status="ready", success=True)
            self.preflight_receipt = preflight
        except BaseException as error:
            preflight["error"] = str(error)
            if isinstance(error, InputAdapterError) and error.receipt:
                preflight["failed_guard"] = error.receipt
            self._failed = self._closed = True
            try:
                self.backend.close()
            except Exception as cleanup:
                preflight["close_error"] = str(cleanup)
            if not isinstance(error, Exception):
                raise
            raise InputAdapterError(str(error), receipt=preflight) from error

    def _receipt(self, operation, events):
        return {"schema_version": 1, "control_source": PROVENANCE,
                "input_consumption_verified": False, "physical_device_latency_verified": False,
                "operation": operation, "status": "not_inserted", "success": False,
                "target_pid": self.pid, "run_id": self.run_id, "events": events,
                "requested_count": len(events), "inserted_count": 0,
                "insertion_attempted": False,
                "qpc_frequency": self.frequency, "qpc_before": None, "qpc_after": None,
                "extra_info": self.extra_info, "win32_error": None}

    def _guard(self):
        # Check native scope first, then sample OS foreground immediately before
        # insertion. Neither this ordering nor the postcheck removes the race.
        scope = _detached(self.scope_guard())
        os_guard = _detached(self.backend.inspect())
        evidence = {"scope": scope, "os": os_guard}
        good_scope = isinstance(scope, dict) and scope.get("verified") is True
        good_os = isinstance(os_guard, dict) and all(os_guard.get(key) is True for key in
            ("process_alive", "process_identity_verified", "foreground_matches"))
        good_os = good_os and os_guard.get("target_pid") == self.pid and os_guard.get("foreground_pid") == self.pid and bool(os_guard.get("foreground_hwnd"))
        if not good_scope or not good_os:
            raise InputAdapterError("Owned process, foreground or native local-session guard failed", receipt={"guard": evidence})
        return evidence

    def _validate(self, events):
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_BATCH_EVENTS:
            raise ValueError("Input batch requires one to eight events")
        projected, possible, normalized = set(self._owned), set(self._owned), []
        counts = 0
        fields = {"key": {"kind", "key", "pressed"}, "mouse_button": {"kind", "button", "pressed"},
                  "mouse_move": {"kind", "dx", "dy"}}
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("kind"), str) or event["kind"] not in fields or set(event) != fields[event["kind"]]:
                raise ValueError("Input event must match its exact normalized schema")
            kind = event["kind"]
            if kind == "mouse_move":
                if type(event["dx"]) is not int or type(event["dy"]) is not int:
                    raise ValueError("Relative mouse counts must be integers")
                count = abs(event["dx"]) + abs(event["dy"])
                counts += count
                if not 1 <= count <= MAX_BATCH_MOUSE_COUNTS or counts > MAX_BATCH_MOUSE_COUNTS:
                    raise ValueError("Relative mouse batch exceeds the bounded 200-count profile")
            else:
                field = "key" if kind == "key" else "button"
                allowed = SCANCODES if kind == "key" else ("left", "right")
                name, pressed = event[field], event["pressed"]
                if not isinstance(name, str) or name not in allowed or type(pressed) is not bool:
                    raise ValueError("Input control or pressed value is outside the supported profile")
                identity = kind, name
                if pressed:
                    if identity in projected:
                        raise ValueError("Cannot press an already owned held control")
                    projected.add(identity)
                    possible.add(identity)
                else:
                    if identity not in projected:
                        raise ValueError("Cannot release a control not pressed by this adapter")
                    projected.remove(identity)
            normalized.append(dict(event))
        return normalized, projected, possible

    def _insert(self, receipt):
        receipt["qpc_before"] = self.backend.qpc()
        receipt.update(insertion_attempted=True, inserted_count=None)
        result = self.backend.send_input(receipt["events"], self.extra_info)
        if not isinstance(result, dict) or type(result.get("inserted_count")) is not int:
            raise ValueError("Input backend returned an invalid insertion result")
        receipt.update(inserted_count=result["inserted_count"], win32_error=result.get("win32_error"))
        receipt["qpc_after"] = self.backend.qpc()
        if not 0 <= receipt["inserted_count"] <= receipt["requested_count"]:
            raise ValueError("Input backend returned an impossible insertion count")
        if type(receipt["qpc_before"]) is not int or type(receipt["qpc_after"]) is not int or receipt["qpc_after"] < receipt["qpc_before"]:
            raise ValueError("Input backend returned an invalid QPC interval")
        if receipt["inserted_count"] != receipt["requested_count"]:
            raise RuntimeError("SendInput inserted only part or none of the requested batch; delivery is unknown")

    def send(self, events: list[dict[str, Any]]) -> dict:
        with self._lock:
            if self._closed or self._failed:
                raise InputAdapterError("Input adapter is closed or failed; new input is disabled")
            receipt = self._receipt("send", [])
            try:
                normalized, projected, possible = self._validate(events)
                receipt.update(events=normalized, requested_count=len(normalized))
                receipt["guard_before"] = self._guard()
                # A partial insertion does not certify which events reached the
                # queue. Keep every attempted press eligible for cleanup.
                self._owned = possible
                self._insert(receipt)
                self._owned = projected
                receipt["guard_after"] = self._guard()
                receipt.update(status="inserted", success=True)
                return receipt
            except BaseException as error:
                self._failed = True
                receipt["error"] = str(error)
                if isinstance(error, InputAdapterError) and error.receipt:
                    receipt["failed_guard"] = error.receipt
                receipt["status"] = "failed"
                cleanup = self.release_all("send_failed")
                if not isinstance(error, Exception):
                    error.receipt, error.cleanup_receipt = receipt, cleanup
                    raise
                raise InputAdapterError(str(error), receipt=receipt, cleanup_receipt=cleanup) from error

    def release_all(self, reason="requested") -> dict:
        """Release only possibly owned controls; never requires active scope.

        Releases are global and best effort. On partial failure ownership remains
        conservative, allowing another explicit cleanup attempt or close().
        """
        with self._lock:
            events = [{"kind": kind, "key" if kind == "key" else "button": name, "pressed": False}
                      for kind, name in sorted(self._owned)]
            receipt = self._receipt("release_all", events)
            receipt["reason"] = str(reason)
            receipt["delivery_to_cs2_verified"] = False
            if not events:
                receipt.update(status="no_owned_inputs", success=True)
                return receipt
            if self._closed:
                receipt.update(status="release_incomplete", error="Adapter is closed with unresolved owned controls")
                return receipt
            try:
                receipt["os_observation"] = _detached(self.backend.inspect())
            except Exception as error:
                receipt["os_observation_error"] = str(error)
            try:
                self._insert(receipt)
                self._owned.clear()
                receipt.update(status="released", success=True)
            except BaseException as error:
                self._failed = True
                receipt.update(status="release_incomplete", error=str(error))
                # Do not mask an original interruption with a cleanup failure.
            return receipt

    def close(self):
        with self._lock:
            if self._closed:
                return self._last_close_receipt
            receipt = self.release_all("close")
            try:
                self.backend.close()
            except Exception as error:
                receipt.update(success=False, close_error=str(error))
            finally:
                self._closed = True
                self._last_close_receipt = receipt
            if not receipt["success"]:
                raise InputAdapterError("Input adapter cleanup was incomplete", receipt=receipt, cleanup_receipt=receipt)
            return receipt

    def __enter__(self):
        if self._closed or self._failed:
            raise InputAdapterError("Cannot enter a closed or failed input adapter")
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except InputAdapterError as cleanup:
            if exc is None:
                raise
            exc.cleanup_receipt = cleanup.receipt
        return False
