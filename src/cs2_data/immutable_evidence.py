"""Reuse SHA256 only while Windows prevents writes/deletes to the same file.

Other platforms use ordinary uncached verification. Code files are never pinned.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import threading

_held = {}
_names = set()
_mutex = threading.RLock()

if os.name == "nt":
    from ctypes import wintypes
    _api = ctypes.WinDLL("kernel32", use_last_error=True)
    _api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _api.CreateFileW.restype = wintypes.HANDLE
    _api.CloseHandle.argtypes = [wintypes.HANDLE]


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size


def guarded_digest(path):
    if not _held or Path(path).name.casefold() not in _names:
        return None
    path = Path(path).resolve()
    with _mutex:
        record = _held.get(path)
        if record is None:
            return None
        if _identity(path) != record["identity"]:
            raise ValueError("Locked evidence path changed identity: "+str(path))
        return record["sha256"]


def _acquire(path, expected):
    path = Path(path).resolve()
    if path.suffix.lower() not in (".sqlite", ".jsonl", ".zip"):
        raise ValueError("Only immutable session data may use guarded hashes")
    with _mutex:
        if path in _held:
            if guarded_digest(path) != expected:
                raise ValueError("Locked evidence hash differs from its binding")
            _held[path]["references"] += 1
            return path
        # FILE_SHARE_READ deliberately excludes writes and delete/replace.
        handle = _api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024*1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("Immutable session evidence changed: "+str(path))
            _held[path] = {"handle": handle, "sha256": expected, "identity": _identity(path), "references": 1}
            _names.add(path.name.casefold())
        except BaseException:
            _api.CloseHandle(handle)
            raise
    return path


def _release(path):
    with _mutex:
        record = _held[path]
        record["references"] -= 1
        if not record["references"]:
            _api.CloseHandle(record["handle"])
            del _held[path]
            if not any(p.name.casefold() == path.name.casefold() for p in _held):
                _names.discard(path.name.casefold())


@contextmanager
def hold_files(files):
    held = []
    try:
        if os.name == "nt":
            for path, digest in files.items():
                held.append(_acquire(path, digest))
        yield
    finally:
        for path in reversed(held):
            _release(path)


def retain_files(files):
    """Validator processes keep shared read locks until their pool shuts down."""
    if os.name != "nt":
        return
    for path, digest in files.items():
        if guarded_digest(path) is None:
            _acquire(path, digest)
        elif guarded_digest(path) != digest:
            raise ValueError("Retained session evidence binding changed")


@atexit.register
def _close_all():
    for path, record in list(_held.items()):
        _api.CloseHandle(record["handle"])
        del _held[path]
    _names.clear()
