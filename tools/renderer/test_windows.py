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
