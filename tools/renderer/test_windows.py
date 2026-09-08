"""Fixture tests: game configuration restoration, bounded capture, and process ownership."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
from unittest.mock import Mock

import pytest

MODULE = Path(__file__).with_name("windows.py")
SPEC = importlib.util.spec_from_file_location("chicken_windows_renderer", MODULE)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def game_fixture(tmp_path):
    game = tmp_path / "fake game"
    (game / "csgo").mkdir(parents=True)
    original = b'"GameInfo"\r\n{\r\n\tFileSystem\r\n\t{\r\n\t\tSearchPaths\r\n\t\t{\r\n\t\t\tGame\tcsgo // preserve\r\n\t\t}\r\n\t}\r\n}\r\n'
    (game / "csgo/gameinfo.gi").write_bytes(original)
    out = tmp_path / "output"
    out.mkdir()
    return game, out, original


def job_fixture(tmp_path):
    demo = tmp_path / "a demo.dem"
    demo.write_bytes(b"PBDEMS2\x00" + b"fixture data")
    return {"schema_version": 1, "timing_clock": "demo_tick", "demo_id": worker.sha256_file(demo),
            "demo_path": str(demo), "clip_id": "originalclip", "round_id": 1,
            "steam_id": "76561198254835598", "player_slot": 3, "spectator_user_id": 3,
            "start_demo_tick": 1279, "end_demo_tick": 2458, "fps": 32, "width": 640, "height": 360}


def settings_fixture(tmp_path, game):
    steam = tmp_path / "steam-client"
    steam.mkdir()
    (steam / "steam.exe").write_bytes(b"fixture only")
    cfg = steam / "userdata/123/730/local/cfg"
    cfg.mkdir(parents=True)
    (cfg / "cs2_machine_convars.vcfg").write_bytes(b'"volume" "0.7"\r\n')
    (steam / "userdata/123/730/remote").mkdir()
    (game / "csgo/cfg").mkdir(exist_ok=True)
    return ["--steam-dir", str(steam), "--steam-user-id", "123"]


def test_current_competitive_profile_has_distinct_identity_and_launch_mode(tmp_path):
    source = job_fixture(tmp_path)
    historical = worker.validate_job(source)
    current = worker.validate_job({**source, "competitive_replay_profile": worker.COMPETITIVE_PROFILE})
    assert current["clip_id"] != historical["clip_id"]
    assert current["renderer_profile_sha256"] != historical["renderer_profile_sha256"]
    assert current["renderer_profile"] == worker.COMPETITIVE_RENDERER_PROFILE
    args = worker.launch_arguments(tmp_path, current, Path(source["demo_path"]), tmp_path / "plugin.log", True)
    assert "-chicken-competitive-replay" in args
    assert "-chicken-calibration-replay" not in args
    assert "-insecure" in args


@pytest.mark.parametrize("extra", [{"competitive_replay_profile": "caller-verified"},
    {"competitive_replay_profile": worker.COMPETITIVE_PROFILE, "calibration_replay_profile": "cs2-controlled-calibration-replay-v1"}])
def test_competitive_profile_cannot_alias_calibration_or_unreviewed_profile(tmp_path, extra):
    with pytest.raises(ValueError, match="competitive replay profile"):
        worker.validate_job({**job_fixture(tmp_path), **extra})


def test_gameinfo_roundtrip_and_crash_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    game, out, original = game_fixture(tmp_path)
    lease = worker.GameInfoLease(game, out, "a" * 32)
    lease.activate()
    patched = lease.gameinfo.read_bytes()
    assert b"Game\tcsgo/chicken-render-" in patched
    assert (out / "gameinfo.original.gi").read_bytes() == original
    assert lease.lock_path.exists()
    # A fresh recovery call simulates restarting Python after an interrupted worker.
    report = worker.restore_gameinfo(lease.journal_path)
    assert report["state"] == "restored"
    assert lease.gameinfo.read_bytes() == original
    assert not lease.lock_path.exists()
    assert worker.restore_gameinfo(lease.journal_path)["state"] == "restored"


def test_restore_preserves_unknown_changes_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    game, out, original = game_fixture(tmp_path)
    lease = worker.GameInfoLease(game, out, "b" * 32)
    lease.activate()
    external = lease.gameinfo.read_bytes() + b"// a separate edit\r\n"
    lease.gameinfo.write_bytes(external)
    with pytest.raises(ValueError, match="changed outside"):
        lease.restore()
    assert lease.gameinfo.read_bytes() == external
    assert (out / "gameinfo.original.gi").read_bytes() == original
    assert lease.lock_path.exists()
    assert json.loads(lease.journal_path.read_text())["state"] == "restore_conflict"


@pytest.mark.parametrize("profile,leaf", [("competitive-hud-archive-search-v1", b"pakchicken_hud_dir.vpk"),
    (worker.HUD_ARCHIVE_SEARCH_PROFILE, b"pakchicken_hud.vpk")])
def test_private_hud_mount_survives_interrupted_worker_recovery(tmp_path, monkeypatch, profile, leaf):
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    game, out, original = game_fixture(tmp_path)
    lease = worker.GameInfoLease(game, out, "e" * 32, hud_archive_profile=profile)
    lease.activate()
    patched = lease.gameinfo.read_bytes()
    assert patched.count(b"/" + leaf) == 2
    assert b"Mod\tcsgo/chicken-render-" in patched
    if profile == worker.HUD_ARCHIVE_SEARCH_PROFILE:
        assert patched.index(b"Game\tcsgo/" + lease.mod_name.encode() + b"\r\n") < patched.index(b"/pakchicken_hud.vpk")
    assert json.loads(lease.journal_path.read_text())["hud_archive_profile"] == profile
    worker.restore_gameinfo(lease.journal_path)
    assert lease.gameinfo.read_bytes() == original
    assert not lease.lock_path.exists()


@pytest.mark.parametrize("profile", ["arbitrary-path", "../outside.vpk", True])
def test_unknown_hud_mount_journal_cannot_authorize_restoration(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    game, out, _ = game_fixture(tmp_path)
    lease = worker.GameInfoLease(game, out, "f" * 32, hud_archive_profile=worker.HUD_ARCHIVE_SEARCH_PROFILE)
    lease.activate()
    before = lease.gameinfo.read_bytes()
    journal = worker.read_json(lease.journal_path)
    journal["hud_archive_profile"] = profile
    worker.atomic_json(lease.journal_path, journal)
    with pytest.raises(ValueError, match="HUD archive search profile"):
        worker.restore_gameinfo(lease.journal_path)
    assert lease.gameinfo.read_bytes() == before
    assert lease.lock_path.exists()


def test_restore_rejects_other_owner_and_live_cs2(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    game, out, _ = game_fixture(tmp_path)
    lease = worker.GameInfoLease(game, out, "c" * 32)
    lease.activate()
    monkeypatch.setattr(worker, "cs2_pids", lambda: [456])
    with pytest.raises(ValueError, match="Close CS2"):
        lease.restore()
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    lease.lock_path.write_text(json.dumps({"run_id": "different"}))
    with pytest.raises(ValueError, match="different run"):
        lease.restore()


def test_job_hash_pilot_schedule_and_launch_quoting(tmp_path):
    original = job_fixture(tmp_path)
    job = worker.validate_job(original, max_ticks=128)
    assert job["end_demo_tick"] == 1407
    assert job["clip_id"] != original["clip_id"]
    assert original["end_demo_tick"] == 2458
    assert worker.validate_job(original, max_ticks=128)["clip_id"] == job["clip_id"]
    actions = {entry["cmd"]: entry["tick"] for entry in worker.make_sequence(job, 0)[0]["actions"]}
    assert actions["spec_player 4"] == 1149
    assert actions["startmovie " + job["clip_id"] + "_"] == 1279
    assert actions["endmovie"] == 1407
    assert actions["cl_drawhud 1"] == actions["r_drawviewmodel 1"] == 64
    assert actions["spec_show_xray 0"] == 64
    assert actions["cl_radar_show_all_players_when_spectating 0"] == 64
    assert actions["cl_spec_show_bindings 0"] == 64
    short = {**original, "end_demo_tick": 1407}
    assert worker.validate_job(short)["clip_id"] != short["clip_id"]
    assert worker.validate_job(short)["source_clip_id"] == short["clip_id"]
    demo = tmp_path / "path with spaces/input.dem"
    args = worker.launch_arguments(tmp_path / "game", job, demo, tmp_path / "log file.log", True)
    assert args[args.index("+playdemo") + 1] == str(demo)
    assert "-insecure" in args and "-steam" in args
    assert "+demo_allow_game_mismatch" in args
    Path(original["demo_path"]).write_bytes(b"PBDEMS2\x00tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        worker.validate_job(original)


def test_only_owned_process_handle_is_stopped():
    process = Mock()
    process.poll.return_value = None
    worker.stop_owned_process(process)
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=15)
    process.kill.assert_not_called()
    exited = Mock()
    exited.poll.return_value = 0
    worker.stop_owned_process(exited)
    exited.terminate.assert_not_called()


@pytest.mark.parametrize("ticks", [640, 1280])
def test_longer_competitive_capture_uses_one_bounded_sequence(tmp_path, ticks):
    original = job_fixture(tmp_path)
    original.update(competitive_replay_profile=worker.COMPETITIVE_PROFILE,
                    end_demo_tick=original["start_demo_tick"] + ticks)
    # Existing callers still get the historical five-second default cap.
    assert worker.validate_job(original)["end_demo_tick"] == original["start_demo_tick"] + 320
    effective = worker.validate_job(original, max_ticks=ticks)
    assert effective["end_demo_tick"] == original["end_demo_tick"]
    sequence = worker.make_sequence(effective, 0)
    commands = [entry["cmd"] for entry in sequence[0]["actions"]]
    assert len(sequence) == 1
    assert sum(command.startswith("startmovie ") for command in commands) == 1
    assert commands.count("endmovie") == commands.count("quit") == 1
    boundaries = {entry["cmd"]: entry["tick"] for entry in sequence[0]["actions"]}
    assert boundaries["endmovie"] - boundaries["startmovie " + effective["clip_id"] + "_"] == ticks
    assert boundaries["quit"] == original["end_demo_tick"] + 64


@pytest.mark.parametrize("profile,ticks", [(None, 640), ("calibration", 640),
    ("competitive", 1281), ("competitive", True), ("competitive", 640.0)])
def test_capture_bound_cannot_be_widened_by_legacy_or_invalid_limits(tmp_path, profile, ticks):
    original = job_fixture(tmp_path)
    if profile == "competitive":
        original["competitive_replay_profile"] = worker.COMPETITIVE_PROFILE
    elif profile == "calibration":
        original["calibration_replay_profile"] = "cs2-controlled-calibration-replay-v1"
    with pytest.raises(ValueError, match="max-ticks"):
        worker.validate_job(original, max_ticks=ticks)


def test_full_demo_profile_runs_two_minutes_only_at_decided_format(tmp_path):
    source = job_fixture(tmp_path)
    source.update(competitive_replay_profile=worker.COMPETITIVE_PROFILE,
                  full_demo_profile=worker.FULL_DEMO_PROFILE,
                  end_demo_tick=source["start_demo_tick"]+7680)
    effective = worker.validate_job(source, max_ticks=7680)
    assert effective["end_demo_tick"] == source["end_demo_tick"]
    assert len(worker.make_sequence(effective, 0)) == 1
    for changed in ({"width": 1280, "height": 720}, {"fps": 64}, {"full_demo_profile": "unknown"}):
        with pytest.raises(ValueError):
            worker.validate_job({**source, **changed}, max_ticks=7680)
    del source["full_demo_profile"]
    with pytest.raises(ValueError, match="max-ticks"):
        worker.validate_job(source, max_ticks=7680)


@pytest.mark.parametrize("ticks,fps", [(640, 32), (1280, 32), (1280, 64)])
def test_current_long_capture_disk_budget_holds_all_bgra_frames(tmp_path, monkeypatch, ticks, fps):
    original = job_fixture(tmp_path)
    original.update(competitive_replay_profile=worker.COMPETITIVE_PROFILE, width=1280, height=720,
                    fps=fps, end_demo_tick=original["start_demo_tick"]+ticks)
    job = worker.validate_job(original, max_ticks=ticks)
    process = Mock(); process.poll.side_effect = [None, 0]; process.returncode = 0
    frame = Mock(); frame.stat.return_value.st_size = 18+1280*720*4
    monkeypatch.setattr(worker, "capture_files", lambda *a: [frame]*(ticks*fps//64))
    monkeypatch.setattr(worker.time, "sleep", lambda *a: None)
    assert worker.wait_for_game(process, [], job, 300) == 0
    process.terminate.assert_not_called()


@pytest.mark.parametrize("overflow", ["frame_count", "bytes", "legacy_bytes"])
def test_larger_capture_still_stops_owned_process_on_budget_overflow(tmp_path, monkeypatch, overflow):
    original = job_fixture(tmp_path)
    if overflow != "legacy_bytes":
        original["competitive_replay_profile"] = worker.COMPETITIVE_PROFILE
    original.update(width=1280, height=720)
    job = worker.validate_job(original)
    count = 169 if overflow == "frame_count" else 160
    frame = Mock(); frame.stat.return_value.st_size = 18+1280*720*4 if overflow == "frame_count" else 1024**3
    process = Mock(); process.poll.return_value = None
    monkeypatch.setattr(worker, "capture_files", lambda *a: [frame]*count)
    stop = Mock(); monkeypatch.setattr(worker, "stop_owned_process", stop)
    with pytest.raises(RuntimeError, match="frame/disk budget"):
        worker.wait_for_game(process, [], job, 300)
    stop.assert_called_once_with(process)


@pytest.mark.parametrize("already_exited", [False, True])
@pytest.mark.parametrize("overflow", ["frame_count", "bytes"])
def test_final_capture_budget_is_checked_after_process_exit(tmp_path, monkeypatch, already_exited, overflow):
    original = job_fixture(tmp_path)
    original.update(competitive_replay_profile=worker.COMPETITIVE_PROFILE, width=1280, height=720,
                    end_demo_tick=original["start_demo_tick"]+640)
    job = worker.validate_job(original, max_ticks=640)
    normal = Mock(); normal.stat.return_value.st_size = 18+1280*720*4
    oversized = Mock(); oversized.stat.return_value.st_size = 6*1024**3
    final_files = [normal]*329 if overflow == "frame_count" else [oversized]
    process = Mock(); process.returncode = 0
    process.poll.side_effect = [0, 0] if already_exited else [None, 0, 0]
    capture = Mock(side_effect=[final_files] if already_exited else [[normal]*320, final_files])
    monkeypatch.setattr(worker, "capture_files", capture)
    monkeypatch.setattr(worker.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="frame/disk budget"):
        worker.wait_for_game(process, [], job, 300)
    assert capture.call_count == (1 if already_exited else 2)
    # Cleanup remains restricted to the owned process handle; an exited process
    # needs no termination and its evidence is left available for diagnosis.
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_already_exited_capture_within_budget_retains_exit_code(tmp_path, monkeypatch):
    job = worker.validate_job(job_fixture(tmp_path))
    frame = Mock(); frame.stat.return_value.st_size = 18+640*360*4
    capture = Mock(return_value=[frame]*160)
    monkeypatch.setattr(worker, "capture_files", capture)
    process = Mock(); process.poll.return_value = 0; process.returncode = 0
    assert worker.wait_for_game(process, [], job, 300) == 0
    capture.assert_called_once_with([], job["clip_id"])
    process.terminate.assert_not_called()


def test_tga_validation_and_prefix_scoped_archive(tmp_path):
    capture = tmp_path / "movie"
    capture.mkdir()
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HHBB", header, 12, 2, 1, 24, 32)
    payload = bytes(header) + b"\x00\x00\xff\xff\x00\x00"  # TGA BGR: red, blue.
    for index in range(2):
        (capture / f"ourclip_{index:08d}.tga").write_bytes(payload)
    unrelated = capture / "usercapture_00000000.tga"
    unrelated.write_bytes(payload)
    files = worker.capture_files([capture], "ourclip")
    meta = worker.archive_frames(files, tmp_path / "frames", {"clip_id": "ourclip", "width": 2, "height": 1})
    assert meta["top_origin"] is True
    assert len(list((tmp_path / "frames").glob("*.tga"))) == 2
    assert unrelated.read_bytes() == payload
    bad = capture / "short.tga"
    bad.write_bytes(bytes(header))
    with pytest.raises(ValueError, match="Incomplete TGA pixels"):
        worker.inspect_tga(bad, 2, 1)


def test_dry_run_has_no_output_or_game_mutation(tmp_path, capsys):
    job = job_fixture(tmp_path)
    spec = tmp_path / "job.json"
    spec.write_text(json.dumps(job))
    out = tmp_path / "no-output"
    code = worker.main(["--spec", str(spec), "--output", str(out), "--game-dir", str(tmp_path / "no-game")])
    assert code == 0
    assert not out.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["training_ready"] is False
    assert report["timing_status"] == "unverified"


def test_real_tga_encode_preserves_color_origin_and_pts(tmp_path):
    local_bins = sorted((worker.ROOT.parents[1] / ".tools/ffmpeg").glob("*/bin/ffmpeg.exe"))
    if not local_bins:
        pytest.skip("Local ffmpeg installation is optional for fixture-only checks")
    ffmpeg = local_bins[-1]
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    width, height = 64, 32
    job = {"clip_id": "colorfixture", "width": width, "height": height, "fps": 32}
    frames = tmp_path / "frames"
    frames.mkdir()
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HHBB", header, 12, width, height, 24, 0)  # Bottom-origin BGR.
    bottom_blue = b"\xff\x00\x00" * (width * height // 2)
    top_red = b"\x00\x00\xff" * (width * height // 2)
    for index in range(3):
        (frames / f"colorfixture_{index:08d}.tga").write_bytes(bytes(header) + bottom_blue + top_red)
    result = worker.encode_video(ffmpeg, ffprobe, tmp_path, job, 3, "libx264")
    assert result["num_frames"] == 3
    pts = json.loads((tmp_path / "colorfixture.pts.json").read_text())
    assert pts["demo_tick_mapping"] is None
    assert [frame["pts_seconds"] for frame in pts["frames"]] == pytest.approx([0, 1 / 32, 2 / 32])
    decoded = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(tmp_path / "colorfixture.mp4"),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                             capture_output=True, check=True).stdout
    assert len(decoded) == width * height * 3
    top, bottom = decoded[:3], decoded[-3:]
    assert top[0] > 200 and top[1] < 30 and top[2] < 30
    assert bottom[0] < 30 and bottom[1] < 30 and bottom[2] > 200


@pytest.mark.parametrize("failure_point", ["launch", "active_journal"])
def test_capture_failure_restores_gameinfo_and_publishes_failed_manifest(tmp_path, monkeypatch, failure_point):
    game, unused_out, original_gameinfo = game_fixture(tmp_path)
    source_job = job_fixture(tmp_path)
    job = worker.validate_job(source_job, max_ticks=128)
    plugin = tmp_path / "fixture-plugin.dll"
    plugin.write_bytes(b"fixture, never loaded CHICKEN_SETTINGS_ISOLATION_V1")
    output = tmp_path / "failed-attempt"
    args = worker.argument_parser().parse_args(["--output", str(output), "--game-dir", str(game),
                                                 "--plugin", str(plugin), "--execute", *settings_fixture(tmp_path, game)])
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    monkeypatch.setattr(worker, "preflight", lambda *args: (Path("ffmpeg"), Path("ffprobe"), {"PatchVersion": "fixture"}))
    if failure_point == "launch":
        def failed_launch(*args, **kwargs):
            raise OSError("fixture launch failure")
        monkeypatch.setattr(worker.subprocess, "Popen", failed_launch)
    else:
        original_json_write = worker.atomic_json
        def failed_journal_write(path, value):
            if path.name == "gameinfo-recovery.json" and value.get("state") == "active":
                raise OSError("fixture active journal failure")
            return original_json_write(path, value)
        monkeypatch.setattr(worker, "atomic_json", failed_journal_write)
    with pytest.raises(RuntimeError, match="Failed manifest"):
        worker.run_capture(args, job, source_job)
    assert (game / "csgo/gameinfo.gi").read_bytes() == original_gameinfo
    assert not (game / "csgo/gameinfo.gi.chicken-render.lock").exists()
    assert json.loads((output / "gameinfo-recovery.json").read_text())["state"] == "restored"
    manifest = json.loads((output / (job["clip_id"] + ".render.json")).read_text())
    assert manifest["render_status"] == "failed"
    assert manifest["gameinfo_restored"] is True
    assert manifest["training_ready"] is False
    assert manifest["interval_verified"] is False


def test_changed_plugin_is_rejected_before_activating_gameinfo(tmp_path, monkeypatch):
    game, _, original_gameinfo = game_fixture(tmp_path)
    original = job_fixture(tmp_path)
    job = worker.validate_job(original)
    plugin = tmp_path / "fixture-plugin.dll"
    plugin.write_bytes(b"original build CHICKEN_SETTINGS_ISOLATION_V1")
    output = tmp_path / "changed-build"
    args = worker.argument_parser().parse_args(["--output", str(output), "--game-dir", str(game),
                                                "--plugin", str(plugin), "--execute", *settings_fixture(tmp_path, game)])
    monkeypatch.setattr(worker, "cs2_pids", lambda: [])
    monkeypatch.setattr(worker, "preflight", lambda *args: (Path("ffmpeg"), Path("ffprobe"), {}))
    copy = worker.shutil.copyfile
    def changed_copy(source, target, *args, **kwargs):
        result = copy(source, target, *args, **kwargs)
        if Path(target).name == "server.dll":
            Path(target).write_bytes(b"rebuilt during staging")
        return result
    monkeypatch.setattr(worker.shutil, "copyfile", changed_copy)
    with pytest.raises(RuntimeError, match="Plugin changed during staging"):
        worker.run_capture(args, job, original)
    assert (game / "csgo/gameinfo.gi").read_bytes() == original_gameinfo
    assert not (game / "csgo/gameinfo.gi.chicken-render.lock").exists()
    manifest = json.loads((output / (job["clip_id"] + ".render.json")).read_text())
    assert manifest["gameinfo_restored"] == "not_modified"
    assert manifest["plugin_source_sha256"] != manifest["plugin_staged_sha256"]
