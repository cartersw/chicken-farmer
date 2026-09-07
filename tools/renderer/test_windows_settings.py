"""Renderer integration fixtures; no installed game or personal Steam files are used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    "chicken_windows_settings_renderer", Path(__file__).with_name("windows.py")
)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class FakeProcess:
    """Only models the process handle owned by a renderer attempt."""

    pid = 987654

    def __init__(self):
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        assert self.returncode is not None, "Fixture cannot wait on a real/live process"
        return self.returncode


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def files_below(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def capture(tmp_path, monkeypatch):
    game = tmp_path / "fake library/game"
    steam = tmp_path / "fake Steam"
    account = steam / "userdata/100000001/730"
    gameinfo = write(game / "csgo/gameinfo.gi",
                     b'"GameInfo"\r\n{\r\n FileSystem\r\n {\r\n  SearchPaths\r\n  {\r\n   Game csgo\r\n  }\r\n }\r\n}\r\n')
    write(steam / "steam.exe", b"never executed")
    settings = {
        "keys": write(account / "local/cfg/cs2_user_keys_0_slot0.vcfg", b'"bindings"\r\n{ "w" "+forward" }\r\n'),
        "user": write(account / "local/cfg/cs2_user_convars_0_slot0.vcfg", b'"sensitivity" "1.2345"\r\n'),
        "video": write(account / "local/cfg/cs2_video.txt", b'"setting.defaultres" "1920"\r\n'),
        "clouded": write(account / "local/cfg/cs2_user_convars_0_slot0.vcfg_lastclouded", b"cloud comparison\r\n"),
        "remote": write(account / "remote/cs2_user_convars.vcfg", b"remote personal configuration\r\n"),
        "autoexec": write(game / "csgo/cfg/autoexec.cfg", b"volume 0.75\r\n"),
        "boot": write(game / "csgo/cfg/boot.vcfg", b"personal boot\r\n"),
    }
    originals = {name: path.read_bytes() for name, path in settings.items()}
    mtimes = {name: path.stat().st_mtime_ns for name, path in settings.items()}
    demo = write(tmp_path / "source.dem", b"PBDEMS2\x00fixture demo bytes")
    source = {"schema_version": 1, "timing_clock": "demo_tick",
              "demo_id": worker.sha256_file(demo), "demo_path": str(demo),
              "clip_id": "settings-fixture", "round_id": 1,
              "steam_id": "76561198254835598", "player_slot": 3,
              "spectator_user_id": 3, "start_demo_tick": 6000, "end_demo_tick": 6004,
              "fps": 32, "width": 320, "height": 180}
    job = worker.validate_job(source)
    plugin = write(tmp_path / "plugin.dll", b"fixture CHICKEN_SETTINGS_ISOLATION_V1 never loaded")
    output = tmp_path / "attempt"
    args = worker.argument_parser().parse_args([
        "--output", str(output), "--game-dir", str(game), "--plugin", str(plugin), "--execute"
    ])
    args.steam_dir = steam
    args.steam_user_id = "100000001"
    process = FakeProcess()
    state = {"launched": False}
    monkeypatch.setattr(worker, "cs2_pids", lambda: [process.pid]
                        if state["launched"] and process.poll() is None else [])
    monkeypatch.setattr(worker, "preflight", lambda *a: (Path("ffmpeg"), Path("ffprobe"), {"PatchVersion": "fixture"}))
    launch = Mock(side_effect=AssertionError("Every fixture must explicitly simulate Popen; CS2 must never launch"))
    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    fixture = SimpleNamespace(game=game, steam=steam, account=account, settings=settings,
                              originals=originals, mtimes=mtimes, gameinfo=gameinfo,
                              original_gameinfo=gameinfo.read_bytes(), job=job, source=source,
                              plugin=plugin, output=output, args=args, process=process,
                              state=state, launch=launch)
    return fixture


def manifest(capture):
    return worker.read_json(capture.output / (capture.job["clip_id"] + ".render.json"))


def assert_original_settings(capture):
    for name, path in capture.settings.items():
        assert path.read_bytes() == capture.originals[name], name
        assert path.stat().st_mtime_ns == capture.mtimes[name], name


def mutate_personal_settings(capture):
    capture.settings["user"].write_bytes(b"render profile values\n")
    capture.settings["video"].write_bytes(b"small render resolution\n")
    capture.settings["remote"].unlink()
    capture.settings["boot"].write_bytes(b"render boot\n")
    return write(capture.account / "local/cfg/cs2_user_keys_0_slot3.vcfg", b"new render configuration")


def test_snapshot_clone_and_environment_exist_before_popen(capture, monkeypatch):
    def failed_launch(arguments, **kwargs):
        journal = worker.read_json(capture.output / "settings-recovery.json")
        assert journal["state"] == "snapshotted"
        assert journal["original"]["files"]
        assert (capture.output / "settings-backup").is_dir()
        clone = capture.output / "replay-settings/cfg"
        for name in ("keys", "user", "video", "clouded"):
            assert (clone / capture.settings[name].name).read_bytes() == capture.originals[name]
        assert Path(kwargs["env"]["USRLOCALCSGO"]) == capture.output / "replay-settings"
        assert kwargs["env"]["SteamAppId"] == kwargs["env"]["SteamGameId"] == "730"
        assert Path(arguments[arguments.index("-chicken-render-settings") + 1]) == clone.parent
        assert Path(arguments[arguments.index("-chicken-render-isolation") + 1]) == capture.output / "settings-isolation.json"
        assert b"csgo/chicken-render-" in capture.gameinfo.read_bytes()
        assert_original_settings(capture)
        raise OSError("simulated process creation failure")

    capture.launch.side_effect = failed_launch
    with pytest.raises(RuntimeError, match="simulated process creation failure"):
        worker.run_capture(capture.args, capture.job, capture.source)
    capture.launch.assert_called_once()
    assert_original_settings(capture)
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    report = manifest(capture)
    assert report["settings_restored"] == "not_modified"
    assert report["gameinfo_restored"] is True
    assert report["staged_plugin_removed_from_game"] is True
    assert report["render_status"] == "failed"


@pytest.mark.parametrize("outcome", ["crash", "timeout", "interrupt"])
def test_started_process_failure_restores_settings_bytes_and_metadata(capture, monkeypatch, outcome):
    def launch(*args, **kwargs):
        capture.state["launched"] = True
        return capture.process

    created = []

    def wait(*args):
        created.append(mutate_personal_settings(capture))
        if outcome == "crash":
            capture.process.returncode = -1073741819
            return capture.process.returncode
        if outcome == "timeout":
            raise TimeoutError("simulated replay timeout")
        raise KeyboardInterrupt("simulated interruption")

    capture.launch.side_effect = launch
    monkeypatch.setattr(worker, "wait_for_game", wait)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(capture.args, capture.job, capture.source)
    assert capture.process.poll() is not None
    assert capture.process.terminated == (0 if outcome == "crash" else 1)
    assert capture.process.killed == 0
    assert_original_settings(capture)
    assert not created[0].exists()
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    report = manifest(capture)
    assert report["render_status"] == "failed"
    assert report["settings_restored"] is True
    assert report["gameinfo_restored"] is True
    assert report["staged_plugin_removed_from_game"] is True
    journal = worker.read_json(capture.output / "settings-recovery.json")
    assert journal["state"] == "restored"
    assert journal["post"]["files"]


def test_live_process_prevents_settings_restore_and_mod_relocation(capture, monkeypatch):
    def launch(*args, **kwargs):
        capture.state["launched"] = True
        return capture.process

    def wait(*args):
        mutate_personal_settings(capture)
        raise TimeoutError("simulated wait failure")

    def cannot_stop(process):
        assert process is capture.process
        raise OSError("simulated owned handle termination failure")

    capture.launch.side_effect = launch
    monkeypatch.setattr(worker, "wait_for_game", wait)
    monkeypatch.setattr(worker, "stop_owned_process", cannot_stop)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(capture.args, capture.job, capture.source)
    assert capture.process.poll() is None
    assert capture.settings["user"].read_bytes() == b"render profile values\n"
    assert not capture.settings["remote"].exists()
    assert b"csgo/chicken-render-" in capture.gameinfo.read_bytes()
    report = manifest(capture)
    assert report["settings_restored"] is False
    assert report["gameinfo_restored"] is False
    assert "Close CS2" in report["settings_restoration_error"]
    assert Path(report["owned_game_mod_dir"]).is_dir()
    assert not (capture.output / "renderer-sandbox").exists()
    assert worker.read_json(capture.output / "settings-recovery.json")["state"] == "snapshotted"


def test_external_edit_before_launch_is_preserved_by_cancellation(capture, monkeypatch):
    copy = worker.shutil.copyfile
    external = b"user edited sensitivity while renderer was staging\r\n"

    def copy_with_edit(source, destination, *args, **kwargs):
        result = copy(source, destination, *args, **kwargs)
        if Path(destination).name == "server.dll":
            capture.settings["user"].write_bytes(external)
        return result

    monkeypatch.setattr(worker.shutil, "copyfile", copy_with_edit)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(capture.args, capture.job, capture.source)
    capture.launch.assert_not_called()
    assert capture.settings["user"].read_bytes() == external
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    report = manifest(capture)
    assert report["settings_restored"] == "not_modified"
    assert report["gameinfo_restored"] == "not_modified"
    assert report["staged_plugin_removed_from_game"] is True
    # Cancellation must release the snapshot's own locks despite the external edit.
    second_out = capture.output.parent / "second-attempt"
    second_out.mkdir()
    roots = worker.discover_settings_roots(capture.game, capture.steam, capture.args.steam_user_id)
    second = worker.settings_guard.SettingsLease(second_out, "f" * 32, roots,
        selectors=worker.SETTINGS_SELECTORS, excludes=worker.SETTINGS_EXCLUDES)
    second.snapshot()
    second.cancel_before_launch(require_idle=worker.require_cs2_idle)


def test_normal_cs2_started_during_staging_is_not_patched(capture, monkeypatch):
    copy = worker.shutil.copyfile

    def copy_while_game_starts(source, destination, *args, **kwargs):
        result = copy(source, destination, *args, **kwargs)
        if Path(destination).name == "server.dll":
            capture.state["launched"] = True
        return result

    monkeypatch.setattr(worker.shutil, "copyfile", copy_while_game_starts)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(capture.args, capture.job, capture.source)
    capture.launch.assert_not_called()
    assert capture.process.terminated == capture.process.killed == 0
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    assert not (capture.game / "csgo/gameinfo.gi.chicken-render.lock").exists()
    assert_original_settings(capture)
    report = manifest(capture)
    assert report["gameinfo_restored"] == "not_modified"
    assert report["settings_restored"] is False  # Cancellation waits until this external game closes.


def stage_interrupted_run(capture):
    capture.output.mkdir()
    run_id = "d" * 32
    roots = worker.discover_settings_roots(capture.game, capture.steam, capture.args.steam_user_id)
    settings = worker.settings_guard.SettingsLease(capture.output, run_id, roots,
        selectors=worker.SETTINGS_SELECTORS, excludes=worker.SETTINGS_EXCLUDES)
    settings.snapshot()
    gameinfo = worker.GameInfoLease(capture.game, capture.output, run_id)
    write(gameinfo.mod_dir / "bin/win64/server.dll", b"inactive interrupted-run plugin")
    gameinfo.activate()
    mutate_personal_settings(capture)
    return gameinfo, settings


def test_interrupted_recovery_requires_explicit_seal_and_retains_evidence(capture):
    gameinfo, settings = stage_interrupted_run(capture)
    with pytest.raises(ValueError, match="Unsealed"):
        worker.repair_run(gameinfo.journal_path)
    # Removing the temporary search path is safe, but unknown current preferences
    # remain untouched until the caller explicitly chooses interrupted recovery.
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    assert capture.settings["user"].read_bytes() == b"render profile values\n"
    assert gameinfo.mod_dir.exists()
    assert worker.read_json(settings.journal_path)["state"] == "snapshotted"
    result = worker.repair_run(gameinfo.journal_path, seal_current_settings=True)
    assert result["settings"]["state"] == "restored"
    assert result["settings"]["sealed_interrupted"] is True
    assert_original_settings(capture)
    assert not gameinfo.mod_dir.exists()
    assert (capture.output / "renderer-sandbox/bin/win64/server.dll").read_bytes() == b"inactive interrupted-run plugin"
    assert (capture.output / "settings-backup").is_dir()
    assert (capture.output / "settings-post").is_dir()
    # A second recovery cannot reapply changes or discard the archived plugin.
    assert worker.repair_run(gameinfo.journal_path)["settings"]["state"] == "restored"


def test_interrupted_recovery_refuses_any_live_cs2_before_mutation(capture):
    gameinfo, settings = stage_interrupted_run(capture)
    before_game = files_below(capture.game)
    before_account = files_below(capture.account)
    capture.state["launched"] = True
    with pytest.raises(ValueError, match="Close CS2"):
        worker.repair_run(gameinfo.journal_path, seal_current_settings=True)
    assert files_below(capture.game) == before_game
    assert files_below(capture.account) == before_account
    assert worker.read_json(settings.journal_path)["state"] == "snapshotted"
    assert capture.process.terminated == capture.process.killed == 0


def isolation_proof(capture):
    profile = capture.output / "replay-settings"
    return {"schema_version": 1, "policy": "CHICKEN_SETTINGS_ISOLATION_V1",
            "status": "ready", "pid": capture.process.pid,
            "settings_root": str(profile), "cloud_hook_installed": True,
            "cloud_cache_uninitialized_at_install": True, "startup_guard_passed": True,
            "local_path_verified": True,
            "cloud_interface": "STEAMREMOTESTORAGE_INTERFACE_VERSION016",
            "scope": "engine2_user_config_remote_storage",
            "usrlocal_search_path": str(profile),
            "usrlocal_write_path": str(profile / "cfg/chicken-isolation-probe.vcfg"),
            "steam_client_process_isolated": False,
            "last_observation": "server_shutdown_guard_still_installed"}


def simulate_completed_capture(capture, monkeypatch, proof):
    def launch(*args, **kwargs):
        capture.state["launched"] = True
        return capture.process

    def wait(process, roots, job, timeout):
        assert process is capture.process
        mutate_personal_settings(capture)
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HHBB", header, 12, job["width"], job["height"], 24, 32)
        for index in range(2):
            write(roots[0] / f"{job['clip_id']}_{index:08d}.tga",
                  bytes(header) + bytes([index, 0, 255]) * job["width"] * job["height"])
        if proof is not None:
            write(capture.output / "settings-isolation.json", json.dumps(proof).encode())
        capture.process.returncode = 0
        return 0

    def encode(ffmpeg, ffprobe, out, job, count, encoder):
        assert capture.process.poll() == 0
        assert_original_settings(capture)
        assert capture.gameinfo.read_bytes() == capture.original_gameinfo
        assert count == 2
        video = write(out / (job["clip_id"] + ".mp4"), b"fixture encoder output; never decoded")
        return {"video_uri": video.name, "video_sha256": worker.sha256_file(video), "num_frames": count}

    capture.launch.side_effect = launch
    monkeypatch.setattr(worker, "wait_for_game", wait)
    encoder = Mock(side_effect=encode)
    monkeypatch.setattr(worker, "encode_video", encoder)
    return encoder


def test_success_requires_native_handshake_and_restores_before_encoding(capture, monkeypatch):
    proof = isolation_proof(capture)
    encode = simulate_completed_capture(capture, monkeypatch, proof)
    result = worker.run_capture(capture.args, capture.job, capture.source)
    encode.assert_called_once()
    assert result["render_status"] == "video_ready_timing_unverified"
    assert result["settings_restored"] is True
    assert result["gameinfo_restored"] is True
    assert result["staged_plugin_removed_from_game"] is True
    assert result["settings_isolation"]["proof_sha256"] == worker.sha256_file(capture.output / "settings-isolation.json")
    assert result["settings_isolation"]["steam_client_process_isolated"] is False
    assert result["training_ready"] is False
    assert capture.process.terminated == 0
    assert_original_settings(capture)
    assert len(list((capture.output / "frames").glob("*.tga"))) == 2
    assert not (capture.account / "local/cfg/cs2_user_keys_0_slot3.vcfg").exists()


@pytest.mark.parametrize("defect", [
    "missing", "status", "settings_root", "policy", "pid", "local_path_verified",
    "cloud_hook_installed", "cloud_cache_uninitialized_at_install", "startup_guard_passed",
    "cloud_interface", "scope", "usrlocal_search_path", "usrlocal_write_path",
])
def test_incomplete_native_handshake_fails_after_safe_restoration(capture, monkeypatch, defect):
    proof = isolation_proof(capture)
    if defect == "missing":
        proof = None
    elif defect in ("local_path_verified", "cloud_hook_installed", "cloud_cache_uninitialized_at_install", "startup_guard_passed"):
        proof[defect] = False
    elif defect == "pid":
        proof[defect] += 1
    elif defect in ("settings_root", "usrlocal_search_path", "usrlocal_write_path"):
        proof[defect] = str(capture.account / "local")
    else:
        proof[defect] = "unverified"
    encode = simulate_completed_capture(capture, monkeypatch, proof)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(capture.args, capture.job, capture.source)
    encode.assert_not_called()
    assert_original_settings(capture)
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo
    report = manifest(capture)
    assert report["render_status"] == "failed"
    assert report["settings_restored"] is True
    assert report["gameinfo_restored"] is True
    assert report["staged_plugin_removed_from_game"] is True
    assert not (capture.output / "frames").exists()


def test_old_plugin_is_rejected_before_settings_snapshot_or_launch(capture, monkeypatch):
    capture.plugin.write_bytes(b"old plugin without the isolation capability")
    snapshot = Mock(side_effect=AssertionError("Old plugins must fail before creating a settings lease"))
    monkeypatch.setattr(worker.settings_guard, "SettingsLease", snapshot)
    with pytest.raises(RuntimeError, match="predates settings isolation"):
        worker.run_capture(capture.args, capture.job, capture.source)
    snapshot.assert_not_called()
    capture.launch.assert_not_called()
    assert not (capture.output / "settings-recovery.json").exists()
    assert_original_settings(capture)
    assert capture.gameinfo.read_bytes() == capture.original_gameinfo


def test_dry_run_leaves_all_fixture_files_untouched(capture, monkeypatch, capsys):
    spec = write(capture.output.parent / "job.json", json.dumps(capture.source).encode())
    before = files_below(capture.output.parent)
    discovery = Mock(side_effect=AssertionError("Dry-run cannot access personal settings"))
    monkeypatch.setattr(worker, "discover_settings_roots", discovery)
    code = worker.main(["--spec", str(spec), "--output", str(capture.output),
                        "--game-dir", str(capture.game), "--plugin", str(capture.plugin)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["render_status"] == "planned"
    assert files_below(capture.output.parent) == before
    assert not capture.output.exists()
    discovery.assert_not_called()
    capture.launch.assert_not_called()


def test_settings_discovery_uses_explicit_steam_client_and_account(capture):
    write(capture.steam / "userdata/100000002/730/local/cfg/cs2_video.txt", b"another user's settings")
    roots = worker.discover_settings_roots(capture.game, capture.steam, "100000001")
    assert roots == {"steam_local_cfg": capture.account / "local/cfg",
                     "steam_remote": capture.account / "remote",
                     "game_cfg": capture.game / "csgo/cfg"}
    with pytest.raises(ValueError, match="settings account"):
        worker.discover_settings_roots(capture.game, capture.steam, "999999999")


def test_relocation_moves_only_the_named_owned_mod(capture):
    capture.output.mkdir()
    name = "chicken-render-" + "a" * 32
    owned = write(capture.game / "csgo" / name / "bin/win64/server.dll", b"owned plugin")
    neighbor = write(capture.game / "csgo" / ("chicken-render-" + "b" * 32) / "keep.cfg", b"different run")
    user = write(capture.game / "csgo/user-mod/keep.cfg", b"user mod")
    destination = worker.relocate_owned_mod(capture.game, capture.output, name)
    assert destination == capture.output / "renderer-sandbox"
    assert (destination / "bin/win64/server.dll").read_bytes() == b"owned plugin"
    assert not owned.exists()
    assert neighbor.read_bytes() == b"different run"
    assert user.read_bytes() == b"user mod"
    assert worker.relocate_owned_mod(capture.game, capture.output, name) == destination


@pytest.mark.parametrize("name", ["../user-mod", "chicken-render-", "user-mod", "chicken-render-" + "a" * 32 + "/other"])
def test_relocation_rejects_unowned_names_without_moving_files(capture, name):
    capture.output.mkdir()
    before = files_below(capture.game)
    with pytest.raises(ValueError, match="Invalid run-owned"):
        worker.relocate_owned_mod(capture.game, capture.output, name)
    assert files_below(capture.game) == before
    assert list(capture.output.iterdir()) == []


@pytest.mark.parametrize("blocker", ["active_path", "occupied_destination", "inside_game", "live_process"])
def test_relocation_preserves_owned_mod_when_safety_checks_fail(capture, blocker):
    capture.output.mkdir()
    name = "chicken-render-" + "a" * 32
    owned = write(capture.game / "csgo" / name / "bin/win64/server.dll", b"owned plugin")
    out = capture.output
    if blocker == "active_path":
        capture.gameinfo.write_bytes(worker.patch_gameinfo(capture.original_gameinfo, name))
    elif blocker == "occupied_destination":
        write(out / "renderer-sandbox/keep.cfg", b"existing archive")
    elif blocker == "inside_game":
        out = capture.game / "archive"
    else:
        capture.state["launched"] = True
    before = files_below(capture.game)
    with pytest.raises(ValueError):
        worker.relocate_owned_mod(capture.game, out, name)
    assert owned.read_bytes() == b"owned plugin"
    assert files_below(capture.game) == before
