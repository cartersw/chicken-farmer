from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("settings_guard_tests", Path(__file__).parents[1] / "tools/renderer/settings_guard.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def idle():
    return None


def fixture(tmp_path, **kwargs):
    root = tmp_path / "personal"
    root.mkdir()
    (root / "a.cfg").write_bytes(b"original\r\n\x00\xff")
    (root / "sub").mkdir()
    (root / "sub/b.vcfg").write_bytes(b"second original")
    os.utime(root / "a.cfg", ns=(1_700_000_000_123456700, 1_700_000_000_123456700))
    lease = guard.SettingsLease(tmp_path / "run", "a" * 32, {"cfg": root}, **kwargs)
    return root, lease


def selected_state(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns,
            bool(p.stat().st_file_attributes & 1) if os.name == "nt" else not bool(p.stat().st_mode & stat.S_IWUSR))
            for p in root.rglob("*") if p.is_file()}


def mutate(root):
    os.chmod(root / "a.cfg", stat.S_IREAD | stat.S_IWRITE)
    (root / "a.cfg").write_bytes(b"render changed")
    (root / "sub/b.vcfg").unlink()
    (root / "new").mkdir()
    (root / "new/c.cfg").write_bytes(b"render new")


def test_byte_exact_restore_preserves_mtime_readonly_and_removes_only_new_poststate(tmp_path):
    root, lease = fixture(tmp_path)
    os.chmod(root / "a.cfg", stat.S_IREAD)
    before = selected_state(root)
    lease.snapshot()
    assert lease.snapshot_complete and lease.owns_locks
    mutate(root)
    lease.seal_after_exit(idle)
    result = lease.restore(idle)
    assert result["state"] == "restored" and result["verified_original_files"] == 2
    assert selected_state(root) == before
    assert not (root / "new").exists()
    assert not lease.owns_locks
    assert (lease.out / "settings-backup/000000.bin").read_bytes() == before["a.cfg"][0]
    assert any(p.read_bytes() == b"render changed" for p in (lease.out / "settings-post").glob("*.bin"))


def test_absent_root_is_recorded_without_creation_and_restored_to_absence(tmp_path):
    root = tmp_path / "absent"
    lease = guard.SettingsLease(tmp_path / "run", "b"*32, {"cfg": root})
    lease.snapshot()
    assert not root.exists()
    root.mkdir()
    (root / "new.cfg").write_bytes(b"created by game")
    lease.seal_after_exit(idle)
    lease.restore(idle)
    assert not root.exists()


def test_changes_after_seal_refuse_all_setting_mutations(tmp_path):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    (root / "a.cfg").write_bytes(b"later personal edit")
    current = selected_state(root)
    with pytest.raises(ValueError, match="outside the sealed"):
        lease.restore(idle)
    assert selected_state(root) == current
    assert lease.owns_locks


def test_full_backup_verification_precedes_first_restore_mutation(tmp_path):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    (lease.out / "settings-backup/000001.bin").write_bytes(b"corrupted later backup")
    current = selected_state(root)
    with pytest.raises(ValueError, match="backup hash/length"):
        lease.restore(idle)
    assert selected_state(root) == current


def test_interrupted_inflight_operation_resumes_without_repeating_completed_writes(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    original = selected_state(root)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    apply = guard._apply_operation
    calls = []
    def interrupted(path, report, index):
        apply(path, report, index)
        calls.append(index)
        if index == 1:
            raise OSError("simulated interruption after atomic mutation")
    monkeypatch.setattr(guard, "_apply_operation", interrupted)
    with pytest.raises(OSError, match="interruption"):
        lease.restore(idle)
    assert calls == [0, 1]
    monkeypatch.setattr(guard, "_apply_operation", apply)
    assert guard.restore_settings(lease.journal_path, idle)["state"] == "restored"
    assert selected_state(root) == original


def test_external_edit_to_completed_operation_is_preserved_on_resume(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    apply = guard._apply_operation
    def interrupted(path, report, index):
        if index == 1:
            raise OSError("simulated interruption")
        apply(path, report, index)
    monkeypatch.setattr(guard, "_apply_operation", interrupted)
    with pytest.raises(OSError):
        lease.restore(idle)
    (root / "a.cfg").write_bytes(b"user edited restored original")
    current = selected_state(root)
    monkeypatch.setattr(guard, "_apply_operation", apply)
    with pytest.raises(ValueError, match="outside the sealed"):
        guard.restore_settings(lease.journal_path, idle)
    assert selected_state(root) == current


def test_unsealed_recovery_requires_explicit_policy_and_keeps_current_byte_evidence(tmp_path):
    root, lease = fixture(tmp_path)
    before = selected_state(root)
    lease.snapshot()
    mutate(root)
    current = selected_state(root)
    with pytest.raises(ValueError, match="Unsealed"):
        guard.restore_settings(lease.journal_path, idle)
    assert selected_state(root) == current
    guard.seal_interrupted(lease.journal_path, idle)
    result = guard.restore_settings(lease.journal_path, idle)
    assert result["sealed_interrupted"] is True and selected_state(root) == before
    assert any(p.read_bytes() == b"render changed" for p in (lease.out / "settings-post").glob("*.bin"))


def test_completed_restore_is_noop_even_after_later_user_changes_or_live_process(tmp_path):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    lease.restore(idle)
    (root / "a.cfg").write_bytes(b"later real user setting")
    result = guard.restore_settings(lease.journal_path, lambda: pytest.fail("No idle callback needed for a no-op"))
    assert result["state"] == "restored" and (root / "a.cfg").read_bytes() == b"later real user setting"


def test_crash_after_terminal_journal_cleans_only_original_marker(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    lease.seal_after_exit(idle)
    release = guard._release_locks
    def interrupt(*args):
        raise OSError("interrupted lock cleanup")
    monkeypatch.setattr(guard, "_release_locks", interrupt)
    with pytest.raises(OSError, match="lock cleanup"):
        lease.restore(idle)
    assert len(list(tmp_path.glob(".chicken-settings-*.lock"))) == 1
    monkeypatch.setattr(guard, "_release_locks", release)
    (root / "a.cfg").write_bytes(b"user edit after completed restore")
    guard.restore_settings(lease.journal_path, lambda: False)
    assert list(tmp_path.glob(".chicken-settings-*.lock")) == []
    later = guard.SettingsLease(tmp_path / "later", "b"*32, {"cfg": root})
    later.snapshot()
    guard.restore_settings(lease.journal_path, lambda: False)
    assert later.verify_unchanged()["state"] == "unchanged"
    assert (root / "a.cfg").read_bytes() == b"user edit after completed restore"


def test_competing_transaction_releases_only_its_own_partially_acquired_locks(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    owner = guard.SettingsLease(tmp_path / "owner", "a"*32, {"root": second})
    owner.snapshot()
    contender = guard.SettingsLease(tmp_path / "contender", "b"*32, {"a": first, "b": second})
    with pytest.raises(FileExistsError):
        contender.snapshot()
    assert len(list(tmp_path.glob(".chicken-settings-*.lock"))) == 1
    assert not contender.owns_locks and not contender.snapshot_complete
    assert owner.verify_unchanged()["state"] == "unchanged"


def test_snapshot_failure_preserves_failure_evidence_and_releases_owned_locks(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    original = selected_state(root)
    monkeypatch.setattr(guard, "MAX_FILE_BYTES", 2)
    with pytest.raises(ValueError, match="oversized"):
        lease.snapshot()
    assert selected_state(root) == original and not lease.snapshot_complete and not lease.owns_locks
    assert json.loads(lease.journal_path.read_text())["state"] == "snapshot_failed"
    assert list(tmp_path.glob(".chicken-settings-*.lock")) == []


def test_snapshot_completion_journal_failure_does_not_authorize_launch_or_leave_locks(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    original = selected_state(root)
    save = guard._save
    def fail(path, report):
        if report["state"] == "snapshotted":
            raise OSError("completion journal failure")
        save(path, report)
    monkeypatch.setattr(guard, "_save", fail)
    with pytest.raises(OSError, match="journal failure"):
        lease.snapshot()
    assert not lease.snapshot_complete and not lease.owns_locks
    assert selected_state(root) == original
    assert list(tmp_path.glob(".chicken-settings-*.lock")) == []


def test_hard_kill_while_preparing_can_cancel_only_matching_partial_markers(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.cfg").write_bytes(b"untouched first settings")
    other = guard.SettingsLease(tmp_path / "other", "b"*32, {"root": second})
    other.snapshot()
    killed = guard.SettingsLease(tmp_path / "killed", "a"*32, {"first": first, "second": second})
    killed.out.mkdir()
    report = {"journal_version": 1, "run_id": killed.run_id, "state": "preparing", "roots": killed.roots}
    guard._save(killed.journal_path, report)
    marker = guard._lock_path(killed.roots["first"])
    marker.write_text(json.dumps(guard._lock_content(killed.journal_path, report, "first")))
    (killed.out / "settings-backup").mkdir()
    (killed.out / "settings-backup/000000.bin").write_bytes(b"partial snapshot evidence")
    with pytest.raises(ValueError, match="idle processes"):
        guard.restore_settings(killed.journal_path, lambda: False)
    result = guard.restore_settings(killed.journal_path, idle)
    assert result["state"] == "cancelled_before_launch"
    assert not marker.exists()
    assert other.verify_unchanged()["state"] == "unchanged"
    assert (first / "a.cfg").read_bytes() == b"untouched first settings"
    assert (killed.out / "settings-backup/000000.bin").read_bytes() == b"partial snapshot evidence"


def test_failed_snapshot_recovery_preserves_unknown_marker_contents(tmp_path):
    root, lease = fixture(tmp_path)
    lease.out.mkdir()
    report = {"journal_version": 1, "run_id": lease.run_id, "state": "snapshot_failed", "roots": lease.roots}
    guard._save(lease.journal_path, report)
    marker = guard._lock_path(lease.roots["cfg"])
    marker.write_text("unrecognized external marker")
    assert guard.restore_settings(lease.journal_path, idle)["state"] == "cancelled_before_launch"
    assert marker.read_text() == "unrecognized external marker"


@pytest.mark.parametrize("relative", ["../outside.cfg", "sub/../../outside.cfg", "C:/outside.cfg", "/outside.cfg", "a.cfg:stream"])
def test_forged_inventory_paths_cannot_escape_selected_root(tmp_path, relative):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    mutate(root)
    lease.seal_after_exit(idle)
    report = json.loads(lease.journal_path.read_text())
    report["original"]["files"][0]["relative"] = relative
    lease.journal_path.write_text(json.dumps(report))
    current = selected_state(root)
    with pytest.raises(ValueError, match="relative settings path"):
        lease.restore(idle)
    assert selected_state(root) == current


def test_backup_and_roots_cannot_overlap_and_reparse_roots_are_rejected(tmp_path, monkeypatch):
    root = tmp_path / "personal"
    root.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        guard.SettingsLease(root / "backup", "a"*32, {"cfg": root})
    original = Path.lstat
    def reparse(path):
        info = original(path)
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == root else info
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="reparse"):
        guard.SettingsLease(tmp_path / "run", "a"*32, {"cfg": root})


def test_selectors_are_rooted_and_excluded_content_is_never_restored_or_a_conflict(tmp_path):
    root, lease = fixture(tmp_path, selectors={"cfg": ["*.cfg", "sub/*.vcfg"]}, excludes={"cfg": ["trustedlaunch.cfg"]})
    (root / "socache.dt").write_bytes(b"original cache")
    (root / "trustedlaunch.cfg").write_bytes(b"original diagnostics")
    (root / "sub/unselected.cfg").write_bytes(b"nested config outside rooted selection")
    lease.snapshot()
    (root / "a.cfg").write_bytes(b"render cfg")
    lease.seal_after_exit(idle)
    (root / "socache.dt").write_bytes(b"new user cache")
    (root / "trustedlaunch.cfg").write_bytes(b"new user diagnostics")
    (root / "sub/unselected.cfg").write_bytes(b"new nested config")
    lease.restore(idle)
    assert (root / "a.cfg").read_bytes() == b"original\r\n\x00\xff"
    assert (root / "socache.dt").read_bytes() == b"new user cache"
    assert (root / "trustedlaunch.cfg").read_bytes() == b"new user diagnostics"
    assert (root / "sub/unselected.cfg").read_bytes() == b"new nested config"


def test_new_directory_with_excluded_content_is_retained_instead_of_deleting_user_cache(tmp_path):
    root = tmp_path / "absent"
    lease = guard.SettingsLease(tmp_path / "run", "c"*32, {"cfg": root}, selectors={"cfg": ["*.cfg"]})
    lease.snapshot()
    root.mkdir()
    (root / "new.cfg").write_bytes(b"render settings")
    (root / "socache.dt").write_bytes(b"user inventory cache")
    lease.seal_after_exit(idle)
    result = lease.restore(idle)
    assert result["retained_directories"] == ["cfg:."]
    assert (root / "socache.dt").read_bytes() == b"user inventory cache"
    assert not (root / "new.cfg").exists()


def test_idle_checks_block_seal_restore_and_cancel_without_personal_mutations(tmp_path):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    with pytest.raises(ValueError, match="idle processes"):
        lease.seal_after_exit(lambda: False)
    with pytest.raises(ValueError, match="idle processes"):
        lease.cancel_before_launch(lambda: False)
    lease.seal_after_exit(idle)
    with pytest.raises(ValueError, match="idle processes"):
        lease.restore(lambda: False)
    assert json.loads(lease.journal_path.read_text())["state"] == "sealed"


def test_prelaunch_change_is_detected_and_cancellation_preserves_it(tmp_path):
    root, lease = fixture(tmp_path)
    lease.snapshot()
    (root / "a.cfg").write_bytes(b"legitimate edit before Popen")
    with pytest.raises(ValueError, match="changed after snapshot"):
        lease.verify_unchanged()
    result = lease.cancel_before_launch(idle)
    assert result["state"] == "cancelled_before_launch"
    assert (root / "a.cfg").read_bytes() == b"legitimate edit before Popen"
    assert not lease.owns_locks and not lease.snapshot_complete
    assert list(tmp_path.glob(".chicken-settings-*.lock")) == []


def test_clone_uses_verified_snapshot_instead_of_changed_personal_files_and_is_writable(tmp_path):
    root, lease = fixture(tmp_path)
    os.chmod(root / "a.cfg", stat.S_IREAD)
    lease.snapshot()
    os.chmod(root / "a.cfg", stat.S_IREAD | stat.S_IWRITE)
    (root / "a.cfg").write_bytes(b"later personal edit")
    clone = lease.out / "replay-settings/cfg"
    result = lease.clone_root("cfg", clone)
    assert result["files"] == 2
    assert (clone / "a.cfg").read_bytes() == b"original\r\n\x00\xff"
    (clone / "a.cfg").write_bytes(b"isolated replay edit")
    assert (root / "a.cfg").read_bytes() == b"later personal edit"
    with pytest.raises(ValueError, match="fresh destination"):
        lease.clone_root("cfg", clone)


def test_clone_verifies_all_backups_before_creating_destination(tmp_path):
    _, lease = fixture(tmp_path)
    lease.snapshot()
    (lease.out / "settings-backup/000001.bin").write_bytes(b"bad")
    clone = lease.out / "replay-settings/cfg"
    with pytest.raises(ValueError, match="backup hash/length"):
        lease.clone_root("cfg", clone)
    assert not clone.exists()


def test_clone_cannot_occupy_reserved_poststate_attempt_folder(tmp_path):
    _, lease = fixture(tmp_path)
    lease.snapshot()
    with pytest.raises(ValueError, match="reserved recovery backup"):
        lease.clone_root("cfg", lease.out / "settings-post-attempt-01/cfg")


def test_interrupted_poststate_archive_is_preserved_and_explicit_seal_can_retry(tmp_path, monkeypatch):
    root, lease = fixture(tmp_path)
    before = selected_state(root)
    lease.snapshot()
    mutate(root)
    scan = guard._scan
    def interrupted(roots, backup=None, skip=None):
        result = scan(roots, backup, skip)
        if backup is not None and backup.name == "settings-post":
            raise OSError("interrupted poststate archive")
        return result
    monkeypatch.setattr(guard, "_scan", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        lease.seal_after_exit(idle)
    monkeypatch.setattr(guard, "_scan", scan)
    with pytest.raises(ValueError, match="incomplete poststate"):
        lease.seal_after_exit(idle)
    guard.seal_interrupted(lease.journal_path, idle)
    assert (lease.out / "settings-post").is_dir() and (lease.out / "settings-post-attempt-01").is_dir()
    guard.restore_settings(lease.journal_path, idle)
    assert selected_state(root) == before
