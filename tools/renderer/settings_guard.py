"""Bounded, durable restoration of explicitly selected CS2 configuration files.

This module never launches a process, changes Steam Cloud, or discovers settings
roots. The caller supplies roots and an idle-process assertion. Interrupted,
unsealed captures require the separate explicit seal_interrupted operation.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable
import uuid

MAX_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 16384
MAX_DEPTH = 16
JOURNAL_NAME = "settings-recovery.json"
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
ALIAS = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
IdleCheck = Callable[[], Any]


def _absolute(path: Path) -> Path:
    # abspath normalizes dot segments without silently following a junction.
    return Path(os.path.abspath(path))


def _no_links(path: Path) -> None:
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Settings paths cannot contain symlinks/reparse points: {part}")


def _relative(value: str, *, directory: bool = False, pattern: bool = False) -> str:
    if value == "." and directory:
        return value
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value
            or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/"))
            or len(value.split("/")) > MAX_DEPTH):
        raise ValueError("Invalid rooted relative settings path")
    if not pattern and any(character in value for character in "*?[]"):
        raise ValueError("Settings inventory cannot contain glob paths")
    return value


def _matches(relative: str, pattern: str) -> bool:
    # Globs are rooted: '*.cfg' matches only a root file, 'cfg/*.cfg' one level.
    name, expression = relative.casefold().split("/"), pattern.casefold().split("/")
    def match(parts, globs):
        if not globs:
            return not parts
        if globs[0] == "**":
            return match(parts, globs[1:]) or bool(parts) and match(parts[1:], globs)
        return bool(parts) and fnmatch.fnmatchcase(parts[0], globs[0]) and match(parts[1:], globs[1:])
    return bool(match(name, expression))


def _selected(root: dict[str, Any], relative: str) -> bool:
    return (root["selectors"] is None or any(_matches(relative, p) for p in root["selectors"])) and not any(
        _matches(relative, p) for p in root["excludes"])


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: Path) -> tuple[bytes, dict[str, Any]]:
    _no_links(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
        raise ValueError(f"Unsupported or oversized settings file: {path}")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    after = path.stat()
    if (len(data) > MAX_FILE_BYTES or before.st_size != len(data) or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns or before.st_ino != after.st_ino):
        raise ValueError(f"Settings file changed while being read: {path}")
    readonly = bool(getattr(after, "st_file_attributes", 0) & 1) if os.name == "nt" else not bool(after.st_mode & stat.S_IWUSR)
    return data, {"sha256": _hash(data), "size": len(data), "mtime_ns": after.st_mtime_ns,
                  "mode": stat.S_IMODE(after.st_mode), "readonly": readonly}


def _fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("sha256", "size", "mtime_ns", "readonly")}


def _atomic(path: Path, data: bytes) -> None:
    _no_links(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _save(path: Path, report: dict[str, Any]) -> None:
    _atomic(path, (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf8"))


def _json(path: Path) -> dict[str, Any]:
    _no_links(path)
    result = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(result, dict):
        raise ValueError("Expected a settings journal object")
    return result


def _idle(callback: IdleCheck) -> None:
    if not callable(callback):
        raise ValueError("Settings sealing/restoration requires an idle-process assertion")
    if callback() is False:
        raise ValueError("Settings restoration requires idle processes")


def _roots(out: Path, roots: dict[str, Path], selectors=None, excludes=None) -> dict[str, Any]:
    if not roots or len(roots) > 16 or set(selectors or {}) - set(roots) or set(excludes or {}) - set(roots):
        raise ValueError("Invalid explicit settings root selection")
    _no_links(out)
    result = {}
    for alias, requested in sorted(roots.items()):
        if not ALIAS.fullmatch(alias):
            raise ValueError("Invalid settings root alias")
        path = _absolute(requested)
        _no_links(path)
        if not path.parent.is_dir() or path == path.parent or path.exists() and not path.is_dir():
            raise ValueError("A settings root must be a directory with an existing parent")
        if path == out or path in out.parents or out in path.parents:
            raise ValueError("Settings backup/output must not overlap a settings root")
        if any(path == Path(r["path"]) or path in Path(r["path"]).parents or Path(r["path"]) in path.parents for r in result.values()):
            raise ValueError("Settings roots must not overlap")
        patterns = (selectors or {}).get(alias)
        excluded = (excludes or {}).get(alias, [])
        if excluded is None:
            raise ValueError("Settings exclusions must be a glob list")
        for values in (patterns, excluded):
            if values is not None and (not isinstance(values, list) or not values or len(values) > 128):
                if values != [] or values is patterns:
                    raise ValueError("Settings selectors must be nonempty bounded glob lists")
            for value in values or []:
                _relative(value, pattern=True)
        result[alias] = {"path": str(path), "selectors": patterns, "excludes": excluded}
    return result


def _lock_path(root: dict[str, Any]) -> Path:
    path = Path(root["path"])
    token = _hash(os.path.normcase(str(path)).encode())[:32]
    return path.parent / (".chicken-settings-" + token + ".lock")


def _lock_content(journal_path: Path, report: dict[str, Any], alias: str) -> dict[str, Any]:
    return {"run_id": report["run_id"], "journal_path": str(journal_path), "root": alias}


def _check_locks(path: Path, report: dict[str, Any]) -> None:
    for alias, root in report["roots"].items():
        marker = _lock_path(root)
        if not marker.is_file() or _json(marker) != _lock_content(path, report, alias):
            raise ValueError(f"Settings transaction no longer owns its root lock: {marker}")


def _release_locks(path: Path, report: dict[str, Any]) -> None:
    for alias, root in report["roots"].items():
        marker = _lock_path(root)
        if marker.exists():
            if _json(marker) != _lock_content(path, report, alias):
                raise ValueError("Refusing to release another settings transaction's lock")
            marker.unlink()


def _release_terminal_locks(path: Path, report: dict[str, Any]) -> None:
    """Finish a crashed lock release without touching a later transaction."""
    for alias, root in report.get("roots", {}).items():
        marker = _lock_path(root)
        try:
            if marker.is_file() and _json(marker) == _lock_content(path, report, alias):
                marker.unlink()
        except (OSError, ValueError):
            # Unknown or later markers are never removed by terminal recovery.
            continue


def _scan(roots: dict[str, Any], backup: Path | None = None, skip: Path | None = None) -> dict[str, Any]:
    files, directories = [], []
    total = entries = 0
    if backup is not None:
        _no_links(backup)
        backup.mkdir()
    for alias, root in roots.items():
        base = Path(root["path"])
        _no_links(base)
        if not base.exists():
            continue
        stack = [(base, ".")]
        while stack:
            directory, relative = stack.pop()
            _no_links(directory)
            info = directory.stat()
            directories.append({"root": alias, "relative": relative, "mtime_ns": info.st_mtime_ns,
                                "mode": stat.S_IMODE(info.st_mode)})
            children = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
            for child in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("Settings tree exceeds the bounded entry count")
                _no_links(child)
                rel = child.relative_to(base).as_posix()
                _relative(rel)
                if child == skip:
                    continue
                info = child.lstat()
                if stat.S_ISDIR(info.st_mode):
                    stack.append((child, rel))
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError("Settings trees may contain only regular files and directories")
                elif _selected(root, rel):
                    data, metadata = _read_file(child)
                    total += len(data)
                    if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                        raise ValueError("Settings snapshot exceeds the bounded file count/size")
                    row = {"root": alias, "relative": rel, **metadata, "backup_index": len(files)}
                    if backup is not None:
                        destination = backup / f"{len(files):06d}.bin"
                        with destination.open("xb") as handle:
                            handle.write(data)
                            handle.flush()
                            os.fsync(handle.fileno())
                    files.append(row)
    return {"files": files, "directories": directories}


def _key(row: dict[str, Any]) -> str:
    return row["root"] + ":" + row["relative"]


def _state(inventory: dict[str, Any]) -> dict[str, Any]:
    return {"files": {_key(row): _fingerprint(row) for row in inventory["files"]},
            "directories": sorted(_key(row) for row in inventory["directories"])}


def _target(report: dict[str, Any], row: dict[str, Any]) -> Path:
    relative = _relative(row["relative"], directory=True)
    target = Path(report["roots"][row["root"]]["path"]).joinpath(*PurePosixPath(relative).parts)
    _no_links(target)
    return target


def _validate_inventory(report: dict[str, Any], inventory: dict[str, Any]) -> None:
    keys = set()
    if not isinstance(inventory.get("files"), list) or not isinstance(inventory.get("directories"), list):
        raise ValueError("Invalid settings inventory")
    if len(inventory["files"]) > MAX_FILES or len(inventory["directories"]) > MAX_ENTRIES:
        raise ValueError("Oversized settings inventory")
    total = 0
    for kind in ("directories", "files"):
        for index, row in enumerate(inventory[kind]):
            if row.get("root") not in report["roots"]:
                raise ValueError("Unknown settings inventory root")
            _relative(row.get("relative"), directory=kind == "directories")
            normalized = _key(row).casefold()
            if normalized in keys:
                raise ValueError("Duplicate settings inventory path")
            keys.add(normalized)
            if type(row.get("mtime_ns")) is not int or type(row.get("mode")) is not int or not 0 <= row["mode"] <= 0o7777:
                raise ValueError("Invalid settings metadata")
            if kind == "files":
                if (row.get("backup_index") != index or type(row.get("size")) is not int or not 0 <= row["size"] <= MAX_FILE_BYTES
                        or type(row.get("readonly")) is not bool or not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", ""))
                        or not _selected(report["roots"][row["root"]], row["relative"])):
                    raise ValueError("Invalid settings backup index, size, hash or selection")
                total += row["size"]
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Oversized settings backup")


def _load(path: Path) -> dict[str, Any]:
    if path.name != JOURNAL_NAME:
        raise ValueError("Unrecognized settings journal filename")
    report = _json(path)
    if report.get("journal_version") != 1 or not TOKEN.fullmatch(report.get("run_id", "")):
        raise ValueError("Unrecognized settings recovery journal")
    if report.get("state") in ("restored", "cancelled_before_launch"):
        return report  # Never inspect or undo later changes after completion.
    roots = report.get("roots", {})
    checked = _roots(path.parent, {alias: Path(row["path"]) for alias, row in roots.items()},
                     {alias: row["selectors"] for alias, row in roots.items() if row["selectors"] is not None},
                     {alias: row["excludes"] for alias, row in roots.items()})
    if checked != roots:
        raise ValueError("Recovery roots disagree with their canonical paths/selectors")
    for key in ("original", "post"):
        if key in report:
            _validate_inventory(report, report[key])
    return report


def _verify_backups(path: Path, report: dict[str, Any]) -> None:
    for key, folder in (("original", "settings-backup"), ("post", _post_folder(report))):
        _verify_inventory_backup(path, report[key], folder)


def _post_folder(report: dict[str, Any]) -> str:
    attempt = report.get("post_attempt", 0)
    if type(attempt) is not int or not 0 <= attempt <= 32:
        raise ValueError("Invalid fixed poststate backup attempt")
    return "settings-post" if attempt == 0 else f"settings-post-attempt-{attempt:02d}"


def _verify_inventory_backup(path: Path, inventory: dict[str, Any], folder: str) -> None:
    for row in inventory["files"]:
        backup = path.parent / folder / f"{row['backup_index']:06d}.bin"
        data, _ = _read_file(backup)
        if len(data) != row["size"] or _hash(data) != row["sha256"]:
            raise ValueError("Settings backup hash/length mismatch; no restoration performed")


def _operations(report: dict[str, Any]) -> list[dict[str, Any]]:
    original, post = report["original"], report["post"]
    old_dirs, new_dirs = ({_key(r): r for r in inv["directories"]} for inv in (original, post))
    old_files, new_files = ({_key(r): r for r in inv["files"]} for inv in (original, post))
    operations = [{"kind": "mkdir", "row": old_dirs[key]} for key in sorted(old_dirs.keys()-new_dirs.keys(), key=lambda k: (k.count("/"), k))]
    for key in sorted(old_files.keys() | new_files.keys()):
        before, after = new_files.get(key), old_files.get(key)
        if before is None or after is None or _fingerprint(before) != _fingerprint(after):
            operations.append({"kind": "write" if after else "unlink", "row": after or before, "before": before})
    operations.extend({"kind": "rmdir", "row": new_dirs[key]} for key in sorted(new_dirs.keys()-old_dirs.keys(), key=lambda k: (-k.count("/"), k)))
    return operations


def _advance(state: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    result = {"files": dict(state["files"]), "directories": list(state["directories"])}
    key, kind = _key(operation["row"]), operation["kind"]
    if kind == "write":
        result["files"][key] = _fingerprint(operation["row"])
    elif kind == "unlink":
        result["files"].pop(key, None)
    elif kind == "mkdir":
        result["directories"].append(key)
    elif kind == "rmdir" and not operation.get("retained"):
        result["directories"].remove(key)
    result["directories"].sort()
    return result


def _temp_target(report: dict[str, Any], index: int, operation: dict[str, Any]) -> Path | None:
    if operation["kind"] != "write":
        return None
    target = _target(report, operation["row"])
    return target.with_name(f".chicken-settings-{report['run_id']}-{index:06d}.tmp")


def _apply_operation(path: Path, report: dict[str, Any], index: int) -> None:
    operation = report["operations"][index]
    row, kind = operation["row"], operation["kind"]
    target = _target(report, row)
    if kind == "mkdir":
        target.mkdir()
    elif kind == "rmdir":
        if any(target.iterdir()):
            # Never remove excluded files merely to restore directory absence.
            operation["retained"] = True
            _save(path, report)
        else:
            target.rmdir()
    elif kind == "unlink":
        os.chmod(target, target.stat().st_mode | stat.S_IWUSR)
        target.unlink()
    else:
        backup = path.parent / "settings-backup" / f"{row['backup_index']:06d}.bin"
        data, _ = _read_file(backup)
        if len(data) != row["size"] or _hash(data) != row["sha256"]:
            raise ValueError("Settings backup changed during restoration; current files preserved")
        temporary = _temp_target(report, index, operation)
        if temporary.exists():
            _, metadata = _read_file(temporary)
            if _fingerprint(metadata) != _fingerprint(row):
                raise ValueError("Incomplete or changed owned restoration temporary file; preserved for review")
        else:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(temporary, ns=(row["mtime_ns"], row["mtime_ns"]))
            os.chmod(temporary, row["mode"])
        if target.exists():
            os.chmod(target, target.stat().st_mode | stat.S_IWUSR)
        os.replace(temporary, target)


def _seal(path: Path, require_idle: IdleCheck, *, interrupted: bool) -> dict[str, Any]:
    report = _load(path)
    if report["state"] in ("sealed", "restoring", "restored"):
        return report
    if report["state"] != "snapshotted":
        raise ValueError("Only a complete settings snapshot can be sealed")
    _idle(require_idle)
    _check_locks(path, report)
    attempt = 0
    while (path.parent / _post_folder({"post_attempt": attempt})).exists():
        if not interrupted:
            raise ValueError("An incomplete poststate archive exists; preserve it and explicitly seal_interrupted")
        attempt += 1
        if attempt > 32:
            raise ValueError("Settings recovery exceeded bounded poststate archive attempts")
    report["post_attempt"] = attempt
    _save(path, report)
    inventory = _scan(report["roots"], path.parent / _post_folder(report))
    if _state(_scan(report["roots"])) != _state(inventory):
        raise ValueError("Settings changed while sealing; poststate evidence preserved")
    report.update(post=inventory, state="sealed", sealed_interrupted=interrupted,
                  completed_operations=0, active_operation=None)
    report["operations"] = _operations(report)
    _save(path, report)
    return report


def seal_interrupted(journal_path: Path, require_idle: IdleCheck) -> dict[str, Any]:
    """Explicit recovery policy: archive the reviewed idle current state, then seal.

    Callers must require an explicit user recovery flag before invoking this.
    Original and current byte evidence remain durable after restoration.
    """
    return _seal(_absolute(journal_path), require_idle, interrupted=True)


def restore_settings(journal_path: Path, require_idle: IdleCheck) -> dict[str, Any]:
    path = _absolute(journal_path)
    report = _load(path)
    if report["state"] in ("restored", "cancelled_before_launch"):
        _release_terminal_locks(path, report)
        return report
    if report["state"] in ("preparing", "snapshot_failed"):
        # The worker cannot launch before snapshot completion. An interrupted
        # snapshot only created evidence/markers, so cancellation is sufficient.
        _idle(require_idle)
        report.update(state="cancelled_before_launch", recovery_reason="snapshot_never_completed",
                      personal_files_modified=False)
        _save(path, report)
        _release_terminal_locks(path, report)
        return report
    if report["state"] not in ("sealed", "restoring"):
        raise ValueError("Unsealed settings recovery preserves current files; explicitly seal_interrupted after idle review")
    _idle(require_idle)
    _check_locks(path, report)
    _verify_backups(path, report)  # Verify every backup before touching any setting.
    operations = report["operations"]
    expected_operations = _operations(report)
    if [{k: v for k, v in op.items() if k != "retained"} for op in operations] != expected_operations:
        raise ValueError("Recovery operations disagree with fixed inventory-derived paths")
    done, active = report["completed_operations"], report.get("active_operation")
    if (type(done) is not int or not 0 <= done <= len(operations)
            or active is not None and (type(active) is not int or active != done or active >= len(operations))):
        raise ValueError("Invalid recovery progress")
    expected = _state(report["post"])
    for operation in operations[:done]:
        expected = _advance(expected, operation)
    temporary = _temp_target(report, active, operations[active]) if active is not None and active < len(operations) else None
    actual = _state(_scan(report["roots"], skip=temporary))
    if actual != expected:
        applied = _advance(expected, operations[active]) if active is not None and active < len(operations) else None
        if applied is not None and actual == applied:
            done += 1
            report.update(completed_operations=done, active_operation=None)
            _save(path, report)
            expected = applied
        else:
            writable = {"files": dict(expected["files"]), "directories": expected["directories"]}
            key = _key(operations[active]["row"]) if active is not None and active < len(operations) else None
            if key in writable["files"]:
                writable["files"][key] = {**writable["files"][key], "readonly": False}
            if actual != writable or key is None:
                raise ValueError("Settings changed outside the sealed transaction; current files and backups preserved")
    report["state"] = "restoring"
    for index in range(done, len(operations)):
        _idle(require_idle)
        operation = operations[index]
        target = _target(report, operation["row"])
        key = _key(operation["row"])
        if operation["kind"] in ("write", "unlink"):
            current = _fingerprint(_read_file(target)[1]) if target.exists() else None
            wanted = expected["files"].get(key)
            allowed_writable = dict(wanted, readonly=False) if wanted else None
            if current != wanted and not (report.get("active_operation") == index and current == allowed_writable):
                raise ValueError("Setting changed during restoration; preserved external change")
        report["active_operation"] = index
        _save(path, report)
        _apply_operation(path, report, index)
        expected = _advance(expected, operation)
        report.update(completed_operations=index+1, active_operation=None)
        _save(path, report)
    if _state(_scan(report["roots"])) != expected:
        raise ValueError("Settings changed during restoration; completion withheld")
    _idle(require_idle)
    # Restore directory timestamps last; these are metadata, not conflict clocks.
    for row in sorted(report["original"]["directories"], key=lambda r: -r["relative"].count("/")):
        target = _target(report, row)
        os.utime(target, ns=(row["mtime_ns"], row["mtime_ns"]))
        os.chmod(target, row["mode"])
    report.update(state="restored", verified_original_files=len(report["original"]["files"]),
                  verified_post_files=len(report["post"]["files"]),
                  restored_operations=len(operations),
                  retained_directories=[_key(op["row"]) for op in operations if op.get("retained")])
    _save(path, report)
    _release_locks(path, report)
    return report


class SettingsLease:
    def __init__(self, out: Path, run_id: str, roots: dict[str, Path], *, selectors=None, excludes=None):
        if not TOKEN.fullmatch(run_id):
            raise ValueError("Settings run ID must be 32 lowercase hexadecimal characters")
        self.out, self.run_id = _absolute(out), run_id
        self.roots = _roots(self.out, roots, selectors, excludes)
        self.journal_path = self.out / JOURNAL_NAME
        self.snapshot_complete = False
        self.owns_locks = False

    def snapshot(self) -> dict[str, Any]:
        self.out.mkdir(parents=True, exist_ok=True)
        if self.journal_path.exists() or (self.out / "settings-backup").exists() or (self.out / "settings-post").exists():
            raise ValueError("Refusing to overwrite existing settings recovery artifacts")
        report = {"journal_version": 1, "run_id": self.run_id, "state": "preparing", "roots": self.roots}
        _save(self.journal_path, report)
        acquired = []
        try:
            for alias, root in self.roots.items():
                marker = _lock_path(root)
                _no_links(marker)
                with marker.open("x", encoding="utf8") as handle:
                    json.dump(_lock_content(self.journal_path, report, alias), handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                acquired.append((alias, root))
                self.owns_locks = True
            original = _scan(self.roots, self.out / "settings-backup")
            if _state(_scan(self.roots)) != _state(original):
                raise ValueError("Settings changed during snapshot; launch must not proceed")
            report.update(original=original, state="snapshotted")
            _save(self.journal_path, report)
            self.snapshot_complete = True
            return report
        except BaseException as error:
            report.update(state="snapshot_failed", error=str(error))
            try:
                _save(self.journal_path, report)
            finally:
                owned = {alias: root for alias, root in acquired}
                _release_locks(self.journal_path, {**report, "roots": owned})
                self.owns_locks = False
            raise

    def seal_after_exit(self, require_idle: IdleCheck) -> dict[str, Any]:
        return _seal(self.journal_path, require_idle, interrupted=False)

    def restore(self, require_idle: IdleCheck) -> dict[str, Any]:
        result = restore_settings(self.journal_path, require_idle)
        self.owns_locks = False
        return result

    def verify_unchanged(self) -> dict[str, Any]:
        report = _load(self.journal_path)
        if report["state"] != "snapshotted":
            raise ValueError("Prelaunch settings verification requires a complete unsealed snapshot")
        _check_locks(self.journal_path, report)
        _verify_inventory_backup(self.journal_path, report["original"], "settings-backup")
        if _state(_scan(report["roots"])) != _state(report["original"]):
            raise ValueError("Personal settings changed after snapshot; cancel before launch and preserve the edit")
        return {"state": "unchanged", "verified_files": len(report["original"]["files"])}

    def clone_root(self, label: str, destination: Path) -> dict[str, Any]:
        report = _load(self.journal_path)
        if report["state"] != "snapshotted" or label not in report["roots"]:
            raise ValueError("Cloning requires a known root in a completed unsealed snapshot")
        _check_locks(self.journal_path, report)
        _verify_inventory_backup(self.journal_path, report["original"], "settings-backup")
        destination = _absolute(destination)
        _no_links(destination)
        if destination.exists():
            raise ValueError("Settings clone requires a fresh destination")
        for root in report["roots"].values():
            source = Path(root["path"])
            if destination == source or source in destination.parents or destination in source.parents:
                raise ValueError("Settings clone cannot overlap a personal settings root")
        for protected in (self.out / "settings-backup", self.out / "settings-post"):
            if destination == protected or protected in destination.parents or destination in protected.parents:
                raise ValueError("Settings clone cannot overlap its recovery backups")
        if self.out in destination.parents:
            first = destination.relative_to(self.out).parts[0]
            if first.startswith("settings-post-attempt-"):
                raise ValueError("Settings clone cannot overlap a reserved recovery backup attempt")
        destination.mkdir(parents=True)
        count = 0
        for row in report["original"]["directories"]:
            if row["root"] == label:
                destination.joinpath(*PurePosixPath(row["relative"]).parts).mkdir(parents=True, exist_ok=True)
        for row in report["original"]["files"]:
            if row["root"] != label:
                continue
            target = destination.joinpath(*PurePosixPath(row["relative"]).parts)
            backup = self.out / "settings-backup" / f"{row['backup_index']:06d}.bin"
            data, _ = _read_file(backup)
            if len(data) != row["size"] or _hash(data) != row["sha256"]:
                raise ValueError("Settings backup changed during clone; personal files preserved")
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(target, ns=(row["mtime_ns"], row["mtime_ns"]))
            os.chmod(target, row["mode"] | stat.S_IWUSR)
            count += 1
        return {"state": "cloned", "root": label, "destination": str(destination), "files": count}

    def cancel_before_launch(self, require_idle: IdleCheck) -> dict[str, Any]:
        report = _load(self.journal_path)
        if report["state"] in ("preparing", "snapshot_failed"):
            report = restore_settings(self.journal_path, require_idle)
            self.owns_locks = False
            self.snapshot_complete = False
            return report
        if report["state"] == "cancelled_before_launch":
            _release_terminal_locks(self.journal_path, report)
            self.owns_locks = False
            self.snapshot_complete = False
            return report
        if report["state"] != "snapshotted":
            raise ValueError("Only an unsealed prelaunch snapshot can be cancelled")
        _idle(require_idle)
        _check_locks(self.journal_path, report)
        report["state"] = "cancelled_before_launch"
        _save(self.journal_path, report)
        _release_locks(self.journal_path, report)
        self.owns_locks = False
        self.snapshot_complete = False
        return report
