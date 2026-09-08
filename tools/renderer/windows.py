"""Experimental, bounded native Windows CS2 replay capture. Dry-run is the default.

Only an explicit --execute changes the game installation or launches CS2. Captured
video remains diagnostic: neither POV identity nor demo-to-frame timing is verified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parent
# Keep the standalone worker usable without installing this tools directory.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import settings_guard

TARGET_PATCH = "1.41.6.5"
PROFILE = "windows-pilot-v5-native-player-hud"
HUD_COMMANDS = [
    "cl_drawhud 1", "r_drawviewmodel 1", "cl_draw_only_deathnotices 0", "hud_scaling 1",
    "spec_show_xray 0", "spec_cameraman_xray 0", "spec_autodirector 0",
    "cl_radar_show_all_players_when_spectating 0",
    "cl_radar_square_when_spectating 0", "cl_radar_square_always 0",
    "cl_spec_show_bindings 0", "cl_spec_stats 0", "cl_drawhud_specvote 0",
    "cl_autohelp 0", "gameinstructor_enable 0", "hidehud 128", "spec_cameraman_ui 0",
    "cl_teamcounter_playercount_instead_of_avatars 1", "cl_show_equipment_value 0", "cl_showtextmsg 0",
    "cl_hud_telemetry_frametime_show 0", "cl_hud_telemetry_net_misdelivery_show 0",
    "cl_hud_telemetry_ping_show 0", "cl_hud_telemetry_serverrecvmargin_graph_show 0",
    "r_show_build_info 0", "cl_trueview_show_status 0",
]
MAX_PILOT_TICKS = 320
MOD_RE = re.compile(r"chicken-render-[0-9a-f]{32}\Z")
SETTINGS_SELECTORS = {
    "steam_local_cfg": ["cs2_*.vcfg", "cs2_*.vcfg_lastclouded", "cs2_video.txt", "cs2_video.txt.bak", "*.cfg"],
    "steam_remote": ["*.vcfg", "*.vcfg_lastclouded", "cfg/*.cfg", "cfg/*.vcfg"],
    "game_cfg": ["*.cfg", "*.vcfg"],
}
SETTINGS_EXCLUDES = {"steam_local_cfg": ["trustedlaunch.cfg"]}


def require_cs2_idle() -> None:
    if cs2_pids():
        raise ValueError("Close CS2 before backing up or restoring personal settings; no unrelated game process is terminated")


def discover_settings_roots(game: Path, steam_dir: Path | None = None,
                            steam_user_id: str | None = None) -> dict[str, Path]:
    """Find the actual Steam client/userdata, including games in other libraries."""
    registry_root = None
    active_user = None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                registry_root = Path(winreg.QueryValueEx(key, "SteamPath")[0])
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
                active_user = str(winreg.QueryValueEx(key, "ActiveUser")[0])
        except OSError:
            pass
    candidates = [steam_dir] if steam_dir is not None else [registry_root, *game.resolve().parents]
    if steam_dir is None:
        program_files = os.environ.get("ProgramFiles(x86)")
        if program_files:
            candidates.append(Path(program_files) / "Steam")
    clients = {path.resolve() for path in candidates if path is not None and
               (path / "steam.exe").is_file() and (path / "userdata").is_dir()}
    if len(clients) != 1:
        raise ValueError("Cannot identify one Steam client userdata directory; supply --steam-dir explicitly")
    steam = clients.pop()
    accounts = {path.name: path for path in (steam / "userdata").iterdir()
                if path.is_dir() and re.fullmatch(r"[0-9]{1,10}", path.name) and (path / "730/local").is_dir()}
    user = steam_user_id or (active_user if active_user in accounts else None)
    if user is None and len(accounts) == 1:
        user = next(iter(accounts))
    if user is None or not re.fullmatch(r"[0-9]{1,10}", user) or user not in accounts:
        raise ValueError("Cannot identify the CS2 settings account; supply --steam-user-id (userdata directory number)")
    base = accounts[user] / "730"
    return {"steam_local_cfg": base / "local/cfg", "steam_remote": base / "remote", "game_cfg": game.resolve() / "csgo/cfg"}


def relocate_owned_mod(game: Path, out: Path, mod_name: str) -> Path | None:
    """Move this inactive run's entire staging folder back to its workspace."""
    require_cs2_idle()
    if not MOD_RE.fullmatch(mod_name):
        raise ValueError("Invalid run-owned mod directory")
    game, out = game.resolve(), out.resolve()
    if out.is_relative_to(game) or game.is_relative_to(out):
        raise ValueError("Mod archive must be outside the game installation")
    source = game / "csgo" / mod_name
    destination = out / "renderer-sandbox"
    if not source.exists():
        return destination if destination.is_dir() else None
    if b"csgo/chicken-render-" in (game / "csgo/gameinfo.gi").read_bytes():
        raise ValueError("Restore the renderer search path before moving its plugin")
    if source.resolve() != source or destination.exists():
        raise ValueError("Unsafe or occupied mod archive location")
    # Reject links before any move; never traverse into a different installation.
    pending = [source]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Run-owned staging contains a reparse point; refusing to move it")
        if path.is_dir():
            pending.extend(path.iterdir())
    shutil.move(str(source), str(destination))
    if source.exists() or not destination.is_dir():
        raise RuntimeError("Run-owned plugin relocation did not complete")
    return destination


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def byte_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected one JSON object: {path}")
    return value


def atomic_bytes(path: Path, data: bytes) -> None:
    """Replace one owned file via a same-directory temporary file."""
    temporary = path.with_name(path.name + ".chicken-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def patch_gameinfo(original: bytes, mod_name: str) -> bytes:
    """Insert exactly one ASCII search path, preserving every original byte."""
    if not MOD_RE.fullmatch(mod_name):
        raise ValueError("Invalid owned game directory name")
    if b"csgo/chicken-render-" in original:
        raise ValueError("An earlier chicken-render search path remains; repair its journal first")
    expression = rb'(?m)^([ \t]*)(?:"SearchPaths"|SearchPaths)[ \t]*(?:\r?\n[ \t]*)?\{[ \t]*(\r?\n)'
    matches = list(re.finditer(expression, original))
    if len(matches) != 1:
        raise ValueError("Expected exactly one recognizable SearchPaths block in gameinfo.gi")
    match = matches[0]
    line = match.group(1) + b"\tGame\tcsgo/" + mod_name.encode("ascii") + match.group(2)
    return original[:match.end()] + line + original[match.end():]


def cs2_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    result = subprocess.run(
        ["tasklist.exe", "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [int(row[1]) for row in csv.reader(result.stdout.splitlines())
            if len(row) >= 2 and row[0].lower() == "cs2.exe"]


def restore_gameinfo(journal_path: Path, *, require_idle: bool = True) -> dict[str, Any]:
    """Recover only a verified original/owned-patched file; never merge unknown edits."""
    journal_path = journal_path.resolve()
    journal = read_json(journal_path)
    if journal.get("journal_version") != 1 or not MOD_RE.fullmatch(journal.get("mod_name", "")):
        raise ValueError("Unrecognized gameinfo recovery journal")
    game = Path(journal["game_dir"]).resolve()
    gameinfo = game / "csgo/gameinfo.gi"
    backup = journal_path.parent / "gameinfo.original.gi"
    lock_path = gameinfo.with_name("gameinfo.gi.chicken-render.lock")
    if str(gameinfo) != journal.get("gameinfo_path") or str(backup) != journal.get("backup_path"):
        raise ValueError("Recovery journal paths disagree with their owned locations")
    original = backup.read_bytes()
    patched = patch_gameinfo(original, journal["mod_name"])
    if byte_hash(original) != journal.get("original_sha256") or byte_hash(patched) != journal.get("patched_sha256"):
        raise ValueError("Recovery backup or journal hash mismatch; refusing restoration")
    if require_idle and cs2_pids():
        raise ValueError("Close CS2 before repairing gameinfo; repair never terminates a process by recorded PID")
    if lock_path.exists():
        lock = read_json(lock_path)
        if lock.get("run_id") != journal.get("run_id") or lock.get("journal_path") != str(journal_path):
            raise ValueError("A different run owns the gameinfo lock; refusing restoration")
    current = gameinfo.read_bytes()
    if byte_hash(current) == journal["patched_sha256"]:
        atomic_bytes(gameinfo, original)
    elif byte_hash(current) != journal["original_sha256"]:
        journal["state"] = "restore_conflict"
        journal["conflicting_sha256"] = byte_hash(current)
        atomic_json(journal_path, journal)
        raise ValueError(f"gameinfo.gi changed outside this run; preserved it and backup. Repair journal: {journal_path}")
    if gameinfo.read_bytes() != original:
        raise RuntimeError("Byte-exact gameinfo restoration verification failed")
    journal["state"] = "restored"
    journal["restored_at"] = time.time()
    atomic_json(journal_path, journal)
    if lock_path.exists():
        # Verify again immediately before releasing this run's own lock.
        if read_json(lock_path).get("run_id") != journal["run_id"]:
            raise ValueError("Gameinfo lock ownership changed during restoration")
        lock_path.unlink()
    return journal


class GameInfoLease:
    def __init__(self, game: Path, out: Path, run_id: str):
        self.game = game.resolve()
        self.out = out.resolve()
        self.run_id = run_id
        self.mod_name = "chicken-render-" + run_id
        self.mod_dir = self.game / "csgo" / self.mod_name
        self.gameinfo = self.game / "csgo/gameinfo.gi"
        self.journal_path = self.out / "gameinfo-recovery.json"
        self.lock_path = self.gameinfo.with_name("gameinfo.gi.chicken-render.lock")
        self.owns_lock = False

    def activate(self) -> None:
        original = self.gameinfo.read_bytes()
        patched = patch_gameinfo(original, self.mod_name)
        backup = self.out / "gameinfo.original.gi"
        with backup.open("xb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        journal = {
            "journal_version": 1, "run_id": self.run_id, "mod_name": self.mod_name,
            "game_dir": str(self.game), "gameinfo_path": str(self.gameinfo),
            "backup_path": str(backup), "original_sha256": byte_hash(original),
            "patched_sha256": byte_hash(patched), "state": "prepared", "prepared_at": time.time(),
        }
        atomic_json(self.journal_path, journal)
        with self.lock_path.open("x", encoding="utf-8") as handle:
            json.dump({"run_id": self.run_id, "journal_path": str(self.journal_path)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        self.owns_lock = True
        if self.gameinfo.read_bytes() != original:
            raise ValueError("gameinfo.gi changed while preparing the transaction; refusing to patch it")
        atomic_bytes(self.gameinfo, patched)
        journal["state"] = "active"
        atomic_json(self.journal_path, journal)

    def record_pid(self, pid: int) -> None:
        journal = read_json(self.journal_path)
        journal["owned_cs2_pid"] = pid
        atomic_json(self.journal_path, journal)

    def restore(self) -> dict[str, Any]:
        if not self.owns_lock:
            raise RuntimeError("This lease does not own the gameinfo transaction")
        return restore_gameinfo(self.journal_path)


def validate_job(job: dict[str, Any], *, max_ticks: int = MAX_PILOT_TICKS) -> dict[str, Any]:
    if job.get("schema_version") != 1 or job.get("timing_clock") != "demo_tick":
        raise ValueError("Expected schema_version=1 and timing_clock=demo_tick")
    if not re.fullmatch(r"[0-9a-f]{64}", str(job.get("demo_id", ""))):
        raise ValueError("demo_id must be a lowercase SHA-256")
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", str(job.get("clip_id", ""))):
        raise ValueError("clip_id must contain only letters, digits, and hyphens")
    for name in ("round_id", "player_slot", "spectator_user_id", "start_demo_tick", "end_demo_tick", "fps", "width", "height"):
        if not isinstance(job.get(name), int) or isinstance(job[name], bool):
            raise ValueError(f"Job requires integer {name}")
    if not 0 < int(job.get("steam_id", "0")) < 2**64 or job["round_id"] < 1:
        raise ValueError("Job requires resolved player/round identity")
    if not 0 <= job["player_slot"] <= 255 or not 0 <= job["spectator_user_id"] <= 255:
        raise ValueError("Invalid player slot or spectator_user_id")
    start, end = job["start_demo_tick"], job["end_demo_tick"]
    if not 71 <= start < end - 2 or end > 2147483500:
        raise ValueError("Require 71 <= start_demo_tick < end_demo_tick-2 with 32-bit replay ticks")
    if job["fps"] not in (32, 64) or not 320 <= job["width"] <= 1280 or not 180 <= job["height"] <= 720:
        raise ValueError("Pilot requires 32/64 fps and resolution between 320x180 and 1280x720")
    if job["width"] % 2 or job["height"] % 2:
        raise ValueError("Video dimensions must be even")
    if job["width"] * 9 != job["height"] * 16:
        raise ValueError("The inspected native HUD profile requires a 16:9 image")
    if not 4 <= max_ticks <= MAX_PILOT_TICKS:
        raise ValueError(f"This pilot worker supports --max-ticks between 4 and {MAX_PILOT_TICKS}")
    demo = Path(job.get("demo_path", "")).resolve()
    if not demo.is_file() or demo.suffix.lower() != ".dem":
        raise ValueError(f"Source demo does not exist: {demo}")
    with demo.open("rb") as handle:
        if handle.read(8) != b"PBDEMS2\x00":
            raise ValueError("Source is not a CS2 demo")
    if sha256_file(demo) != job["demo_id"]:
        raise ValueError("Source demo SHA-256 does not match the job")
    effective = {**job, "demo_path": str(demo), "training_ready": False}
    effective["end_demo_tick"] = min(end, start + max_ticks)
    # Profile changes must produce a new identity even without interval truncation.
    effective["source_clip_id"] = job["clip_id"]
    profile_digest = byte_hash(json.dumps({"profile": PROFILE, "hud_commands": HUD_COMMANDS,
                                         "replay_settle_ticks": 128, "hud_settle_pause_seconds": 6},
                                        sort_keys=True).encode())
    effective["renderer_profile_sha256"] = profile_digest
    key = f"{job['clip_id']}:{start}:{effective['end_demo_tick']}:{profile_digest}"
    effective["clip_id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
    if effective["end_demo_tick"] != end:
        effective["source_job_command_coverage"] = effective.pop("command_coverage", None)
        effective["source_job_interval"] = [start, end]
    effective["renderer_profile"] = PROFILE
    return effective


def make_sequence(job: dict[str, Any], warmup_seconds: float) -> list[dict[str, Any]]:
    if not math.isfinite(warmup_seconds) or not 0 <= warmup_seconds <= 120:
        raise ValueError("Warmup must be between 0 and 120 seconds")
    first, start, end = 64, job["start_demo_tick"], job["end_demo_tick"]
    sequences = []
    if warmup_seconds:
        warmup_tick = first + math.ceil(64 * warmup_seconds)
        sequences.append({"actions": [
            {"cmd": "go_to_next_sequence", "tick": warmup_tick},
            {"cmd": "quit", "tick": warmup_tick + 1000},
        ]})
    setup = ["sv_cheats 1", "demo_timescale 1", "demo_ui_mode 0", "volume 0",
             *HUD_COMMANDS, "cl_demo_predict 0", f"host_framerate {job['fps']}"]
    actions = [{"cmd": command, "tick": first} for command in setup]
    # Let the observer HUD and camera settle before saving pixels. Near the start
    # of a demo, shorten pre-roll while keeping all seek/setup commands after64.
    settle = min(128, start - 71)
    actions += [
        {"cmd": "pause_playback", "tick": first},
        {"cmd": f"demo_gototick {start - settle - 6}", "tick": first},
        {"cmd": "spec_mode 1", "tick": start - settle - 4},
        {"cmd": f"spec_player {job['spectator_user_id'] + 1}", "tick": start - settle - 2},
    ]
    # Replay seeking can leave a realtime HUD announcement active even after its
    # demo tick has passed. These existing two-second pause/resume actions allow
    # realtime UI timers to settle before capture without creating training frames.
    if settle >= 128:
        actions += [{"cmd": "pause_playback", "tick": start - delta} for delta in (96, 64, 32)]
    actions += [
        {"cmd": f"startmovie {job['clip_id']}_", "tick": start},
        {"cmd": "endmovie", "tick": end},
        {"cmd": "quit", "tick": end + 64},
    ]
    sequences.append({"actions": actions})
    return sequences


def launch_arguments(game: Path, job: dict[str, Any], demo: Path, log: Path,
                     allow_version_mismatch: bool) -> list[str]:
    arguments = [str(game / "bin/win64/cs2.exe"), "-steam", "-insecure", "-novid",
                 "-windowed", "-w", str(job["width"]), "-h", str(job["height"]),
                 "-forcenovsync", "-chicken-render-log", str(log),
                 "-chicken-capture-log", str(log.parent / "capture_ledger.jsonl"),
                 "-chicken-render-settings", str(log.parent / "replay-settings"),
                 "-chicken-render-isolation", str(log.parent / "settings-isolation.json")]
    if allow_version_mismatch:
        arguments += ["+demo_allow_game_mismatch", "1"]
    if "calibration_replay_profile" in job:
        if job["calibration_replay_profile"] != "cs2-controlled-calibration-replay-v1":
            raise ValueError("Unsupported controlled calibration replay profile")
        arguments.append("-chicken-calibration-replay")
    return arguments + ["+playdemo", str(demo)]


def require_isolation_plugin(plugin: Path) -> None:
    if b"CHICKEN_SETTINGS_ISOLATION_V1" not in plugin.read_bytes():
        raise ValueError("This DLL predates settings isolation; rebuild the Windows plugin before rendering")


def verify_settings_isolation(out: Path, expected_pid: int | None = None) -> dict[str, Any]:
    path = out / "settings-isolation.json"
    proof = read_json(path)
    expected = out / "replay-settings"
    search_paths = [part for part in str(proof.get("usrlocal_search_path", "")).split(";") if part]
    if (proof.get("schema_version") != 1 or proof.get("status") != "ready" or
            proof.get("policy") != "CHICKEN_SETTINGS_ISOLATION_V1" or
            Path(proof.get("settings_root", "")).resolve() != expected.resolve() or
            len(search_paths) != 1 or Path(search_paths[0]).resolve() != expected.resolve() or
            Path(proof.get("usrlocal_write_path", "")).resolve() != (expected / "cfg/chicken-isolation-probe.vcfg").resolve() or
            expected_pid is not None and proof.get("pid") != expected_pid or
            any(proof.get(field) is not True for field in
                ("cloud_hook_installed", "cloud_cache_uninitialized_at_install", "startup_guard_passed", "local_path_verified")) or
            proof.get("cloud_interface") != "STEAMREMOTESTORAGE_INTERFACE_VERSION016" or
            proof.get("scope") != "engine2_user_config_remote_storage"):
        raise ValueError("Native settings isolation did not prove its startup/path/Cloud guards; capture is not complete")
    return {**proof, "proof_path": str(path), "proof_sha256": sha256_file(path)}


def repair_run(journal_path: Path, *, seal_current_settings: bool = False) -> dict[str, Any]:
    """Recover the run in order: unload path, preferences, then owned plugin files."""
    require_cs2_idle()
    original = read_json(journal_path)
    result = {"gameinfo": restore_gameinfo(journal_path)}
    settings_path = journal_path.resolve().parent / "settings-recovery.json"
    if settings_path.exists():
        if seal_current_settings:
            settings_guard.seal_interrupted(settings_path, require_idle=require_cs2_idle)
        result["settings"] = settings_guard.restore_settings(settings_path, require_idle=require_cs2_idle)
    archived = relocate_owned_mod(Path(original["game_dir"]), journal_path.resolve().parent, original["mod_name"])
    result["archived_game_mod_dir"] = str(archived) if archived is not None else None
    return result


def steam_info(game: Path) -> dict[str, str]:
    path = game / "csgo/steam.inf"
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines() if "=" in line)


def resolve_tool(value: str, name: str) -> Path:
    resolved = shutil.which(value)
    if resolved:
        return Path(resolved).resolve()
    path = Path(value).resolve()
    if path.is_file():
        return path
    raise ValueError(f"{name} executable not found: {value}")


def check_plugin(plugin: Path) -> None:
    with plugin.open("rb") as handle:
        header = handle.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("Plugin is not a PE DLL")
        offset = struct.unpack_from("<I", header, 60)[0]
        handle.seek(offset)
        pe = handle.read(24)
    if len(pe) != 24 or pe[:4] != b"PE\x00\x00" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
        raise ValueError("Plugin must be a native Windows x64 DLL")


def preflight(game: Path, plugin: Path, ffmpeg: str, ffprobe: str,
              allow_version_mismatch: bool) -> tuple[Path, Path, dict[str, str]]:
    if sys.platform != "win32":
        raise ValueError("This execution path requires native Windows")
    if not (game / "bin/win64/cs2.exe").is_file() or not (game / "csgo/gameinfo.gi").is_file():
        raise ValueError("--game-dir must name the CS2 game directory containing bin/win64 and csgo")
    if cs2_pids():
        raise ValueError("CS2 is already running; close it before starting a replay worker")
    info = steam_info(game)
    if info.get("PatchVersion") != TARGET_PATCH and not allow_version_mismatch:
        raise ValueError(f"Installed CS2 {info.get('PatchVersion')} differs from plugin target {TARGET_PATCH}; this experimental attempt requires --allow-version-mismatch")
    check_plugin(plugin)
    return resolve_tool(ffmpeg, "ffmpeg"), resolve_tool(ffprobe, "ffprobe"), info


def capture_files(roots: list[Path], clip_id: str) -> list[Path]:
    pattern = re.compile(re.escape(clip_id) + r"_(\d{8})\.tga\Z", re.IGNORECASE)
    result: dict[Path, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        canonical_root = root.resolve()
        for candidate in root.rglob(clip_id + "_*.tga"):
            if not candidate.is_file() or not pattern.fullmatch(candidate.name):
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(canonical_root):
                raise ValueError("Capture escaped its designated movie directory")
            result[resolved] = candidate
    return sorted(result.values(), key=lambda path: (path.name, str(path.parent)))


def inspect_tga(path: Path, width: int, height: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        header = handle.read(18)
    if len(header) != 18:
        raise ValueError(f"Incomplete TGA header: {path}")
    identifier_size, color_map, image_type = header[:3]
    actual_width, actual_height, depth, descriptor = struct.unpack_from("<HHBB", header, 12)
    if color_map != 0 or image_type not in (2, 10) or depth not in (24, 32):
        raise ValueError(f"Unsupported TGA format: type={image_type}, map={color_map}, depth={depth}")
    if (actual_width, actual_height) != (width, height):
        raise ValueError(f"Captured dimensions {actual_width}x{actual_height} disagree with requested {width}x{height}")
    if image_type == 2 and path.stat().st_size < 18 + identifier_size + width * height * (depth // 8):
        raise ValueError(f"Incomplete TGA pixels: {path}")
    return {"image_type": image_type, "pixel_depth": depth, "top_origin": bool(descriptor & 32),
            "right_origin": bool(descriptor & 16), "pixel_decode": "ffmpeg TGA decoder (BGR/BGRA and origin handling)"}


def stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    # Popen keeps the actual process handle on Windows; no taskkill/PID-name search.
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def wait_for_game(process: subprocess.Popen[bytes], roots: list[Path], job: dict[str, Any],
                  timeout: float) -> int:
    started = time.monotonic()
    last_notice = started
    nominal_frames = math.ceil((job["end_demo_tick"] - job["start_demo_tick"]) * job["fps"] / 64)
    byte_limit = min(1024**3, (nominal_frames + 8) * (job["width"] * job["height"] * 4 + 2048))
    while process.poll() is None:
        elapsed = time.monotonic() - started
        if elapsed > timeout:
            stop_owned_process(process)
            raise TimeoutError(f"Owned CS2 process did not finish within {timeout:g} seconds")
        files = capture_files(roots, job["clip_id"])
        size = sum(path.stat().st_size for path in files)
        if len(files) > nominal_frames + 8 or size > byte_limit:
            stop_owned_process(process)
            raise RuntimeError("Capture exceeded its bounded pilot frame/disk budget; possible repeated movie sequence")
        if time.monotonic() - last_notice >= 15:
            print(f"Replay running: {elapsed:.0f}s elapsed, {len(files)} captured files, {size / 1024**2:.1f} MiB", flush=True)
            last_notice = time.monotonic()
        time.sleep(0.5)
    return process.returncode


def archive_frames(files: list[Path], frame_dir: Path, job: dict[str, Any],
                   destination_prefix: str | None = None) -> dict[str, Any]:
    if len(files) < 2:
        raise ValueError(f"Need at least two complete movie frames; found {len(files)}")
    if len({path.parent.resolve() for path in files}) != 1:
        raise ValueError("Movie prefix appeared in multiple capture directories; repeated sequence is ambiguous")
    expected = [f"{job['clip_id']}_{index:08d}.tga" for index in range(len(files))]
    if [path.name.lower() for path in files] != [name.lower() for name in expected]:
        raise ValueError("Capture frame numbers must be contiguous and begin at zero")
    header = inspect_tga(files[0], job["width"], job["height"])
    frame_dir.mkdir()
    provenance = []
    # Every input was selected by this run's unique movie prefix and checked against
    # designated absolute capture roots. Only these files are moved; no tree deletion.
    for index, source in enumerate(files):
        inspect_tga(source, job["width"], job["height"])
        filename = f"{destination_prefix or job['clip_id']}_{index:08d}.tga"
        destination = (frame_dir / filename).resolve()
        if not destination.is_relative_to(frame_dir.resolve()) or destination.exists():
            raise ValueError("Unsafe or occupied archive frame destination")
        digest = sha256_file(source)
        shutil.move(str(source.resolve()), str(destination))
        provenance.append({"capture_index": index, "source_name": source.name,
                           "archived_name": filename, "sha256": digest})
    atomic_json(frame_dir.parent / "capture_frame_files.json", {
        "schema_version": 1, "capture_prefix": job["clip_id"],
        "archived_prefix": destination_prefix or job["clip_id"], "frames": provenance,
    })
    return header


def encode_video(ffmpeg: Path, ffprobe: Path, out: Path, job: dict[str, Any],
                 frame_count: int, encoder: str) -> dict[str, Any]:
    video = out / (job["clip_id"] + ".mp4")
    args = [str(ffmpeg), "-hide_banner", "-nostdin", "-n", "-framerate", str(job["fps"]),
            "-start_number", "0", "-i", str(out / "frames" / (job["clip_id"] + "_%08d.tga")),
            "-an", "-c:v", encoder]
    args += ["-preset", "medium", "-crf", "18"] if encoder == "libx264" else ["-preset", "p4", "-cq", "20"]
    args += ["-pix_fmt", "yuv420p", "-fps_mode", "passthrough", "-movflags", "+faststart", str(video)]
    with (out / "ffmpeg.log").open("xb") as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=180,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}; see ffmpeg.log")
    probe = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0",
                            "-show_frames", "-show_streams", "-show_entries",
                            "frame=best_effort_timestamp_time:stream=width,height,r_frame_rate",
                            "-of", "json", str(video)], capture_output=True, text=True, check=True,
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    metadata = json.loads(probe.stdout)
    decoded, streams = metadata.get("frames", []), metadata.get("streams", [])
    if len(decoded) != frame_count or len(streams) != 1:
        raise ValueError("Decoded frame count/stream count does not match captured frames")
    if (streams[0].get("width"), streams[0].get("height")) != (job["width"], job["height"]):
        raise ValueError("Encoded dimensions do not match job")
    pts = [float(frame["best_effort_timestamp_time"]) for frame in decoded]
    if any(not math.isfinite(value) or value < 0 for value in pts) or any(right <= left for left, right in zip(pts, pts[1:])):
        raise ValueError("Decoded video PTS is invalid/nonmonotonic")
    atomic_json(out / (job["clip_id"] + ".pts.json"), {
        "schema_version": 1, "clip_id": job["clip_id"], "clock": "video_presentation",
        "demo_tick_mapping": None, "frames": [{"frame_index": index, "pts_seconds": value} for index, value in enumerate(pts)],
    })
    version = subprocess.run([str(ffmpeg), "-version"], capture_output=True, text=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.splitlines()[0]
    return {"video_uri": video.name, "video_sha256": sha256_file(video), "num_frames": len(pts),
            "ffmpeg_version": version, "encoder": encoder, "duration_seconds": len(pts) / job["fps"]}


def run_capture(args: argparse.Namespace, job: dict[str, Any], original_job: dict[str, Any]) -> dict[str, Any]:
    out, game, plugin = args.output.resolve(), args.game_dir.resolve(), args.plugin.resolve()
    if out.is_relative_to(game) or game.is_relative_to(out):
        raise ValueError("Output must be separate from the game installation")
    out.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    # A fresh capture prefix disambiguates repeat attempts, including engines that
    # write into the shared csgo/movie fallback rather than the temporary mod.
    capture_job = {**job, "clip_id": job["clip_id"] + "-" + run_id[:12]}
    lease = GameInfoLease(game, out, run_id)
    movie_roots = [lease.mod_dir / "movie", game / "csgo/movie"]
    manifest_path = out / (job["clip_id"] + ".render.json")
    report: dict[str, Any] = {
        "schema_version": 1, **{key: job[key] for key in ("clip_id", "demo_id", "round_id", "steam_id", "player_slot", "fps", "width", "height")},
        "requested_start_demo_tick": job["start_demo_tick"], "requested_end_demo_tick": job["end_demo_tick"],
        "renderer_profile": PROFILE, "capture_method": "native-windows-cs2-startmovie-tga",
        "renderer_profile_sha256": job["renderer_profile_sha256"],
        "hud_profile": {"requested_commands": HUD_COMMANDS, "visual_acceptance_verified": False,
                        "observer_settle_ticks": min(128, job["start_demo_tick"] - 71),
                        "intent": "Player-visible HUD, no spectator x-ray or all-player radar",
                        "hud_settle_pause_seconds": 6 if job["start_demo_tick"] >= 199 else 0,
                        "encoded_mask_profile": "none", "encoded_masks": [],
                        "raw_frames_masked": False},
        "timing_clock": "demo_tick", "timing_status": "unverified", "pov_verified": False,
        "interval_verified": False,
        "nominal_num_frames": math.ceil((job["end_demo_tick"] - job["start_demo_tick"]) * job["fps"] / 64),
        "training_ready": False, "command_time_basis": "unverified", "render_status": "preparing",
        "plugin_target_patch": TARGET_PATCH, "plugin_compatibility": "experimental_unverified",
        "version_mismatch_allowed": args.allow_version_mismatch, "run_id": run_id,
        "capture_prefix": capture_job["clip_id"],
        "started_at_unix": time.time(), "game_dir": str(game), "owned_game_mod_dir": str(lease.mod_dir),
        "recovery_journal": str(lease.journal_path), "source_job": original_job,
    }
    atomic_json(manifest_path, report)
    process = None
    settings = None
    failure: BaseException | None = None
    try:
        ffmpeg, ffprobe, info = preflight(game, plugin, args.ffmpeg, args.ffprobe, args.allow_version_mismatch)
        report["cs2_build"] = info
        report["plugin_sha256"] = sha256_file(plugin)
        report["plugin_source_sha256"] = report["plugin_sha256"]
        require_isolation_plugin(plugin)
        roots = discover_settings_roots(game, args.steam_dir, args.steam_user_id)
        settings = settings_guard.SettingsLease(out, run_id, roots,
            selectors={name: SETTINGS_SELECTORS[name] for name in roots},
            excludes={name: values for name, values in SETTINGS_EXCLUDES.items() if name in roots})
        report["settings_recovery_journal"] = str(settings.journal_path)
        report["settings_roots"] = {name: str(path) for name, path in roots.items()}
        report["settings_restored"] = False
        atomic_json(manifest_path, report)
        require_cs2_idle()
        settings.snapshot()
        settings.clone_root("steam_local_cfg", out / "replay-settings/cfg")
        atomic_json(manifest_path, report)
        with (out / "job.json").open("x", encoding="utf-8") as handle:
            json.dump(job, handle, indent=2)
        staged = out / "input.dem"
        with Path(job["demo_path"]).open("rb") as source, staged.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
        if sha256_file(staged) != job["demo_id"]:
            raise ValueError("Staged demo copy differs from the immutable source hash")
        with Path(str(staged) + ".json").open("x", encoding="utf-8") as handle:
            json.dump(make_sequence(capture_job, args.warmup_seconds), handle, indent=2)
        lease.mod_dir.mkdir()
        plugin_dir = lease.mod_dir / "bin/win64"
        plugin_dir.mkdir(parents=True)
        shutil.copyfile(plugin, plugin_dir / "server.dll")
        report["plugin_staged_sha256"] = sha256_file(plugin_dir / "server.dll")
        if report["plugin_staged_sha256"] != report["plugin_source_sha256"]:
            raise ValueError("Plugin changed during staging; refusing to launch with inconsistent DLL provenance")
        (lease.mod_dir / "movie").mkdir()
        if capture_files(movie_roots, capture_job["clip_id"]):
            raise ValueError("Capture prefix already exists; refusing to mix frames from separate attempts")
        settings.verify_unchanged()
        require_cs2_idle()
        lease.activate()
        if cs2_pids():
            raise ValueError("CS2 started during worker setup; refusing to launch another process")
        launch = launch_arguments(game, job, staged, out / "plugin.log", args.allow_version_mismatch)
        report["launch_arguments"] = launch
        report["render_status"] = "rendering"
        atomic_json(manifest_path, report)
        environment = {**os.environ, "SteamAppId": "730", "SteamGameId": "730",
                       "USRLOCALCSGO": str(out / "replay-settings")}
        report["settings_profile_directory"] = environment["USRLOCALCSGO"]
        print(f"Launching one offline replay; recovery journal: {lease.journal_path}", flush=True)
        with (out / "cs2-process.log").open("xb") as log:
            process = subprocess.Popen(launch, cwd=game, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            lease.record_pid(process.pid)
            report["owned_cs2_pid"] = process.pid
            atomic_json(manifest_path, report)
            code = wait_for_game(process, movie_roots, capture_job, args.timeout)
        report["cs2_exit_code"] = code
        if code:
            raise RuntimeError(f"Owned CS2 process exited with code {code}; plugin ABI/build may be incompatible")
    except BaseException as exc:
        failure = exc
        report["error"] = str(exc)
    finally:
        if process is not None:
            try:
                stop_owned_process(process)
            except BaseException as exc:
                report["process_cleanup_error"] = str(exc)
                failure = failure or exc
        if lease.owns_lock:
            try:
                lease.restore()
                report["gameinfo_restored"] = True
            except BaseException as exc:
                report["gameinfo_restored"] = False
                report["restoration_error"] = str(exc)
                failure = failure or exc
        else:
            report["gameinfo_restored"] = "not_modified"
        if settings is not None and settings.snapshot_complete:
            try:
                require_cs2_idle()
                if process is None:
                    settings.cancel_before_launch(require_cs2_idle)
                    report["settings_restored"] = "not_modified"
                else:
                    settings.seal_after_exit(require_cs2_idle)
                    restored = settings.restore(require_cs2_idle)
                    report["settings_restored"] = restored.get("state") == "restored"
                    report["settings_restore_verified"] = restored
            except BaseException as exc:
                report["settings_restored"] = False
                report["settings_restoration_error"] = str(exc)
                failure = failure or exc
        else:
            report["settings_restored"] = "not_modified"
        if report["gameinfo_restored"] in (True, "not_modified"):
            try:
                archived_mod = relocate_owned_mod(game, out, lease.mod_name)
                if archived_mod is not None:
                    report["archived_game_mod_dir"] = str(archived_mod)
                    movie_roots[0] = archived_mod / "movie"
                report["staged_plugin_removed_from_game"] = not lease.mod_dir.exists()
            except BaseException as exc:
                report["staged_plugin_removed_from_game"] = False
                report["plugin_cleanup_error"] = str(exc)
                failure = failure or exc
        report["render_status"] = "failed" if failure else "captured_timing_unverified"
        report["finished_at_unix"] = time.time()
        atomic_json(manifest_path, report)
    if failure:
        # Leave this run's frames/plugin folder and all logs intact for diagnosis.
        report["remaining_capture_files"] = [str(path) for path in capture_files(movie_roots, capture_job["clip_id"])]
        atomic_json(manifest_path, report)
        raise RuntimeError(f"{failure}. Failed manifest: {manifest_path}") from failure
    try:
        report["settings_isolation"] = verify_settings_isolation(out, expected_pid=process.pid)
        files = capture_files(movie_roots, capture_job["clip_id"])
        report["captured_frame_count"] = len(files)
        report["captured_count_matches_nominal"] = len(files) == report["nominal_num_frames"]
        report["tga_header"] = archive_frames(files, out / "frames", capture_job, destination_prefix=job["clip_id"])
        report["capture_frame_files"] = "capture_frame_files.json"
        report["capture_frame_files_sha256"] = sha256_file(out / "capture_frame_files.json")
        report.update(encode_video(ffmpeg, ffprobe, out, job, len(files), args.encoder))
        ledger = out / "capture_ledger.jsonl"
        if ledger.is_file():
            report["capture_ledger"] = ledger.name
            report["capture_ledger_sha256"] = sha256_file(ledger)
        report["render_status"] = "video_ready_timing_unverified"
    except BaseException as exc:
        report["render_status"] = "failed"
        report["error"] = str(exc)
        atomic_json(manifest_path, report)
        raise RuntimeError(f"{exc}. Failed manifest: {manifest_path}") from exc
    report["finished_at_unix"] = time.time()
    atomic_json(manifest_path, report)
    return report


def local_video_tool(name: str) -> str:
    candidates = list((ROOT.parents[1] / ".tools/ffmpeg").glob("*/bin/" + name + ".exe"))
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())
    return name


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, help="One canonical render-job JSON object")
    parser.add_argument("--output", type=Path, help="Fresh workspace output directory (must not exist)")
    parser.add_argument("--execute", action="store_true", help="Explicitly patch the game temporarily and launch native CS2")
    parser.add_argument("--repair", type=Path, help="Restore a previous run using its gameinfo-recovery.json; never launch/kill CS2")
    parser.add_argument("--repair-settings", type=Path, help="Recover a settings-recovery.json while CS2 is closed")
    parser.add_argument("--seal-current-settings", action="store_true",
                        help="Explicitly authorize sealing an interrupted run's current settings before recovery; never used by normal renders")
    parser.add_argument("--game-dir", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive/game"))
    parser.add_argument("--steam-dir", type=Path, help="Steam CLIENT installation owning userdata (not a separate game library)")
    parser.add_argument("--steam-user-id", help="Numeric Steam userdata directory; required when automatic account selection is ambiguous")
    parser.add_argument("--plugin", type=Path, default=ROOT / "build/plugin-windows/Release/server.dll")
    parser.add_argument("--ffmpeg", default=local_video_tool("ffmpeg"))
    parser.add_argument("--ffprobe", default=local_video_tool("ffprobe"))
    parser.add_argument("--allow-version-mismatch", action="store_true", help="Attempt the experimental plugin against a different game patch")
    parser.add_argument("--max-ticks", type=int, default=MAX_PILOT_TICKS, help="Pilot capture cap, 4..320 demo ticks; truncation gets a distinct clip ID")
    parser.add_argument("--warmup-seconds", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=300.0, help="Owned game process timeout in seconds")
    parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        if args.repair or args.repair_settings:
            if args.execute or args.spec or args.output:
                raise ValueError("--repair is a separate recovery operation")
            if args.repair and args.repair_settings:
                raise ValueError("Choose one recovery journal")
            if sys.platform != "win32":
                raise ValueError("Gameinfo repair must run on its native Windows worker")
            if args.repair:
                result = repair_run(args.repair, seal_current_settings=args.seal_current_settings)
            else:
                if args.seal_current_settings:
                    settings_guard.seal_interrupted(args.repair_settings, require_idle=require_cs2_idle)
                result = settings_guard.restore_settings(args.repair_settings, require_idle=require_cs2_idle)
            print(json.dumps(result, indent=2))
            return 0
        if args.seal_current_settings:
            raise ValueError("--seal-current-settings is only for explicit recovery of an interrupted run")
        if args.spec is None or args.output is None:
            raise ValueError("Supply --spec and --output (dry-run unless --execute is also supplied)")
        if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800:
            raise ValueError("--timeout must be between 10 and 1800 seconds")
        original = read_json(args.spec)
        job = validate_job(original, max_ticks=args.max_ticks)
        sequence = make_sequence(job, args.warmup_seconds)
        if args.output.exists():
            raise ValueError(f"Output must be a fresh directory: {args.output}")
        if not args.execute:
            print(json.dumps({
                "render_status": "planned", "training_ready": False, "timing_status": "unverified",
                "effective_job": job, "plugin_path": str(args.plugin.resolve()),
                "plugin_exists": args.plugin.is_file(), "plugin_target_patch": TARGET_PATCH,
                "ffmpeg_executable": args.ffmpeg, "ffprobe_executable": args.ffprobe,
                "interval_verified": False,
                "nominal_num_frames": math.ceil((job["end_demo_tick"] - job["start_demo_tick"]) * job["fps"] / 64),
                "version_mismatch_allowed": args.allow_version_mismatch,
                "launch_arguments": launch_arguments(args.game_dir.resolve(), job, args.output.resolve() / "input.dem",
                                                      args.output.resolve() / "plugin.log", args.allow_version_mismatch),
                "sequences": sequence, "note": "No output/game files changed. Execute requires an experimental compatible plugin and ffmpeg/ffprobe.",
                "settings_protection": {"required": True, "profile_path": str(args.output.resolve() / "replay-settings"),
                                        "recovery_journal": str(args.output.resolve() / "settings-recovery.json"),
                                        "policy": "cloned-user-config-native-cloud-guard-and-byte-exact-restore"},
            }, indent=2))
            return 0
        print(json.dumps(run_capture(args, job, original), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Windows replay worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
