"""Protected local CS2 control calibration. Dry-run unless --execute is supplied.

This measures dispatched engine controls, not physical keyboard/mouse latency.
Recorded images and demo commands remain diagnostic until independently audited.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import windows as replay

PROFILE = "cs2-controlled-calibration-plan-v1"
PLUGIN_MARKER = b"CHICKEN_CONTROLLED_CALIBRATION_V1"
# Reviewed calibration binaries. Update only after reviewing a changed build's
# native assumptions; --allow-version-mismatch cannot bypass this list.
BINARY_PROFILE = {
    "bin/win64/tier0.dll": "b9bf7c956bb31e11be649c31b096ea8813601748b9d984d39f10a9eb4458fb75",
    "bin/win64/rendersystemdx11.dll": "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500",
    "bin/win64/engine2.dll": "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07",
    "csgo/bin/win64/client.dll": "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae",
    "csgo/bin/win64/server.dll": "cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c",
    "bin/win64/networksystem.dll": "fc33c097eca4ab0049590985a772b5b2f556523933d0482f86217889e1283127",
    "bin/win64/schemasystem.dll": "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512",
    "bin/win64/filesystem_stdio.dll": "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef",
}
CONTROLS = frozenset(("forward", "back", "left", "right", "attack", "attack2",
                      "duck", "jump", "sprint", "reload", "turnleft", "turnright"))
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
ANGLE = re.compile(r"setang 0 (-?(?:0|[1-9][0-9]{0,2})) 0\Z")


def default_plan() -> dict[str, Any]:
    """A repeatable control probe, not a competitive gameplay demonstration."""
    events = [
        (1000, "forward_start", "+forward"), (1500, "forward_stop", "-forward"),
        (2500, "counterstrafe_forward", "+forward"), (3000, "counterstrafe_release", "-forward"),
        (3000, "counterstrafe_back", "+back"), (3100, "counterstrafe_stop", "-back"),
        (4000, "shot_press", "+attack"), (4050, "shot_release", "-attack"),
        (5000, "crouch_press", "+duck"), (5500, "crouch_release", "-duck"),
        (6500, "turn_start", "+turnleft"), (6800, "turn_stop", "-turnleft"),
        (8000, "left_start", "+left"), (8500, "left_stop", "-left"),
    ]
    return {"schema_version": 1, "producer": PROFILE, "map": "de_dust2",
            "fps": 32, "width": 1280, "height": 720, "duration_seconds": 10,
            "actions": [{"id": name, "at_ms": at, "command": command} for at, name, command in events]}


def validate_plan(value: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "producer", "map", "fps", "width", "height", "duration_seconds", "actions"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Calibration plan must contain exactly the documented schema fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["producer"] != PROFILE:
        raise ValueError("Unsupported calibration plan schema or producer")
    if value["map"] != "de_dust2" or any(type(value[key]) is not int or value[key] != expected
                                         for key, expected in (("fps", 32), ("width", 1280), ("height", 720))):
        raise ValueError("This calibrated worker supports only Dust2 at 1280x720 and 32 FPS")
    duration = value["duration_seconds"]
    if type(duration) is not int or not 2 <= duration <= 30:
        raise ValueError("Calibration duration must be an integer from 2 to 30 seconds")
    actions = value["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        raise ValueError("Calibration must contain between 1 and 128 control actions")
    seen, held, previous = set(), set(), -1
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "at_ms", "command"}:
            raise ValueError("Each action must contain exactly id, at_ms and command")
        name, at, command = action["id"], action["at_ms"], action["command"]
        if not isinstance(name, str) or not ID.fullmatch(name) or name in seen:
            raise ValueError("Action identifiers must be unique bounded lowercase names")
        seen.add(name)
        if type(at) is not int or not 0 <= at <= duration * 1000 - 500 or at < previous:
            raise ValueError("Actions must be ordered integer milliseconds and leave 500 ms of final idle time")
        previous = at
        if not isinstance(command, str):
            raise ValueError("Control command must be a string")
        if command[:1] in ("+", "-") and command[1:] in CONTROLS:
            control = command[1:]
            if command[0] == "+":
                if control in held:
                    raise ValueError("A held control cannot be pressed twice without release")
                held.add(control)
            else:
                if control not in held:
                    raise ValueError("A released control must have a preceding press")
                held.remove(control)
        else:
            angle = ANGLE.fullmatch(command)
            if angle is None or not -180 <= int(angle[1]) <= 180:
                raise ValueError("Only whitelisted controls and bounded setang yaw probes are allowed")
    if held:
        raise ValueError("Every pressed control must be released before calibration ends")
    return json.loads(json.dumps(value, allow_nan=False))


def native_plan(plan: dict[str, Any], out: Path, run_id: str) -> dict[str, Any]:
    validate_plan(plan)
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid owned calibration run identifier")
    out = out.resolve()
    if any(character in str(out) for character in '\";\r\n'):
        raise ValueError("Calibration paths cannot contain console command delimiters")
    return {**plan, "movie_name": "calibration-" + run_id,
            "demo_path": (out / "controlled.dem").as_posix()}


def launch_arguments(game: Path, out: Path, plan: dict[str, Any]) -> list[str]:
    # No arbitrary launch arguments or server address are accepted from a plan.
    return [str(game / "bin/win64/cs2.exe"), "-steam", "-insecure", "-novid", "-windowed",
            "-w", str(plan["width"]), "-h", str(plan["height"]), "-forcenovsync",
            "-chicken-render-log", str(out / "plugin.log"),
            "-chicken-capture-log", str(out / "capture_ledger.jsonl"),
            "-chicken-render-settings", str(out / "replay-settings"),
            "-chicken-render-isolation", str(out / "settings-isolation.json"),
            "-chicken-calibration-plan", str(out / "native-plan.json"),
            "-chicken-calibration-ledger", str(out / "calibration_ledger.jsonl"),
            "+sv_lan", "1", "+tv_enable", "1", "+map", plan["map"], "loopback=true"]


def require_calibration_plugin(plugin: Path) -> None:
    replay.require_isolation_plugin(plugin)
    if PLUGIN_MARKER not in plugin.read_bytes():
        raise ValueError("This DLL lacks controlled calibration; rebuild the protected Windows plugin")


def verify_binary_profile(game: Path) -> dict[str, str]:
    actual = {name: replay.sha256_file(game / name) for name in BINARY_PROFILE}
    changed = [name for name, expected in BINARY_PROFILE.items() if actual[name] != expected]
    if changed:
        raise ValueError("Installed calibration binaries require compatibility review before launch: " + ", ".join(changed))
    return actual


def recording_files(roots: list[Path], prefix: str) -> list[Path]:
    """Find only this run's exact recording name in designated game write roots."""
    if not re.fullmatch(r"calibration-[0-9a-f]{32}", prefix):
        raise ValueError("Invalid owned calibration recording prefix")
    result = set()
    for root in roots:
        candidate = root / (prefix + ".dem")
        if candidate.exists():
            metadata = candidate.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or
                    getattr(metadata, "st_file_attributes", 0) & 0x400 or
                    not candidate.resolve().is_relative_to(root.resolve())):
                raise ValueError("Calibration recording escaped its designated regular-file location")
            result.add(candidate.resolve())
    return sorted(result)


def archive_recording(roots: list[Path], out: Path, prefix: str) -> dict[str, Any]:
    destination = out.resolve() / "controlled.dem"
    files = recording_files(roots, prefix)
    if destination.exists():
        raise ValueError("Calibration demo archive destination already exists")
    if len(files) != 1:
        raise ValueError(f"Expected one recording with this run's unique prefix; found {len(files)}")
    source = files[0]
    if not 16 <= source.stat().st_size <= 1024**3:
        raise ValueError("Controlled recording is missing its header or exceeds its size budget")
    with source.open("rb") as handle:
        if handle.read(8) != b"PBDEMS2\x00":
            raise ValueError("Controlled recording is not a Source 2 demo")
    digest, size = replay.sha256_file(source), source.stat().st_size
    # The exact prefix, canonical roots and file type were checked above. No
    # wildcard deletion or recursive move is involved in archiving a recording.
    shutil.move(str(source), str(destination))
    if destination.stat().st_size != size or replay.sha256_file(destination) != digest:
        raise ValueError("Controlled demo changed while archiving; preserve the run for inspection")
    return {"path": str(destination), "source_path": str(source), "source_name": source.name,
            "sha256": digest, "size_bytes": size}


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate keys in native calibration evidence")
        value[key] = item
    return value


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite calibration value")
    return result


def first_person_ready(local: Any) -> bool:
    """Distinguish a successfully read null observer pointer from a missing read."""
    if not isinstance(local, dict) or local.get("observer_services_pointer_observed") is not True:
        return False
    present, mode = local.get("observer_services_present"), local.get("observer_mode")
    observer_clear = (present is False and "observer_mode" in local and mode is None or
                      present is True and type(mode) is int and mode == 0)
    if (not observer_clear or local.get("first_person_camera_verified") is not True or
            type(local.get("camera_view_entity_handle")) is not int or local["camera_view_entity_handle"] != 2**32 - 1):
        return False
    state = local.get("pawn_state")
    camera, origin = local.get("camera_origin"), state.get("origin") if isinstance(state, dict) else None
    if any(not isinstance(vector, list) or len(vector) != 3 or
           any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
           for vector in (camera, origin)):
        return False
    return math.hypot(camera[0] - origin[0], camera[1] - origin[1]) <= 2 and 24 <= camera[2] - origin[2] <= 76


def verify_native_completion(path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Check that one protected run completed its declared control schedule.

    This is a capture-completeness check, not an input-timing calibration result.
    Later analysis must independently compare the commands and original/replay
    observations; a well-formed ledger alone cannot establish those relations.
    """
    if not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024**2:
        raise ValueError("Missing or oversized native calibration ledger")
    digest = replay.sha256_file(path)
    events = []
    with path.open("rb") as handle:
        while line := handle.readline(1024 * 1024 + 1):
            if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                raise ValueError("Native calibration ledger has an oversized or incomplete record")
            try:
                value = json.loads(line, object_pairs_hook=_json_object, parse_float=_finite_float,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite calibration value")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Native calibration ledger is not valid JSONL") from exc
            if not isinstance(value, dict) or not isinstance(value.get("event"), str):
                raise ValueError("Native calibration ledger record has no event type")
            if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
                raise ValueError("Native calibration ledger has an unsupported schema")
            if len(events) >= 100000:
                raise ValueError("Native calibration ledger has too many records")
            events.append(value)
    def one(kind: str) -> tuple[int, dict[str, Any]]:
        matches = [(index, row) for index, row in enumerate(events) if row["event"] == kind]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one native {kind} record")
        return matches[0]
    header_index, header = one("header")
    ready_index, ready = one("calibration_ready")
    complete_index, complete = one("calibration_complete")
    if header_index != 0 or not header_index < ready_index < complete_index:
        raise ValueError("Native calibration lifecycle records are out of order")
    if any(row["event"] in ("calibration_error", "calibration_failed", "error") for row in events):
        raise ValueError("Native calibration reported a failure")
    if (header.get("control_source") != "dispatched_engine_controls_not_physical_device_latency" or
            header.get("producer") != PROFILE or header.get("plugin_marker") != PLUGIN_MARKER.decode() or
            header.get("physical_input_timestamps") is not False or header.get("training_ready") is not False or
            header.get("plan") != plan or type(header.get("qpc_frequency")) is not int or header["qpc_frequency"] <= 0):
        raise ValueError("Native calibration header lacks the dispatched-control clock provenance")
    if (ready.get("clock_basis") != "local_controller_tick_base_64hz" or
            type(ready.get("start_tick_base")) is not int or ready["start_tick_base"] < 0 or
            ready.get("map") != plan["map"] or
            any(ready.get(key) is not True for key in
                ("local_connection_verified", "local_player_alive", "commands_verified"))):
        raise ValueError("Native calibration did not prove local readiness and command availability")
    local = ready.get("local_player", {})
    state = local.get("pawn_state", {}) if isinstance(local, dict) else {}
    if (not isinstance(local, dict) or not isinstance(state, dict) or
            local.get("status") != "observed" or type(local.get("controller_tick_base")) is not int or
            local["controller_tick_base"] != ready["start_tick_base"] or
            not first_person_ready(local) or
            type(local.get("life_state")) is not int or local["life_state"] != 0 or
            type(state.get("health")) is not int or state["health"] <= 0):
        raise ValueError("Native calibration readiness has no alive first-person player observation")
    stop_index, stop = one("recording_stop_dispatched")
    if (not ready_index < stop_index < complete_index or
            type(complete.get("actions_dispatched")) is not int or
            complete["actions_dispatched"] != len(plan["actions"]) or
            complete.get("training_ready") is not False or complete.get("timing_status") != "unverified" or
            type(stop.get("elapsed_ms")) not in (int, float) or not math.isfinite(stop["elapsed_ms"]) or
            stop["elapsed_ms"] < plan["duration_seconds"] * 1000):
        raise ValueError("Native calibration has no complete recording-stop sequence")
    for record in (header, ready, stop, complete):
        if type(record.get("qpc")) is not int or record["qpc"] < 0:
            raise ValueError("Native calibration lifecycle QPC is missing or invalid")
    if not header["qpc"] <= ready["qpc"] <= stop["qpc"] <= complete["qpc"]:
        raise ValueError("Native calibration lifecycle QPC moved backwards")
    dispatched = [(index, row) for index, row in enumerate(events) if row["event"] == "action_dispatch"]
    if len(dispatched) != len(plan["actions"]):
        raise ValueError("Native calibration did not dispatch exactly the planned actions")
    previous_qpc, previous_elapsed = ready["qpc"], -1.0
    for (index, row), action in zip(dispatched, plan["actions"]):
        if not ready_index < index < stop_index or any(row.get(key) != action[key] for key in ("id", "command", "at_ms")):
            raise ValueError("Native action order or contents disagree with the immutable plan")
        if type(row.get("at_ms")) is not int:
            raise ValueError("Native action schedule must use integer milliseconds")
        before, after, elapsed = row.get("qpc_before"), row.get("qpc_after"), row.get("actual_elapsed_ms")
        if (type(before) is not int or type(after) is not int or not previous_qpc <= before <= after <= stop["qpc"] or
                type(elapsed) not in (int, float) or not math.isfinite(elapsed) or
                not max(previous_elapsed, action["at_ms"]) <= elapsed <= plan["duration_seconds"] * 1000 + 250):
            raise ValueError("Native action clocks are missing, reversed, early or outside the run")
        previous_qpc, previous_elapsed = after, elapsed
    if replay.sha256_file(path) != digest:
        raise ValueError("Native calibration evidence changed while checking completion")
    return {"status": "schedule_completed", "ledger_sha256": digest,
            "record_count": len(events), "action_count": len(dispatched),
            "clock_basis": ready["clock_basis"], "start_tick_base": ready["start_tick_base"],
            "qpc_frequency": header["qpc_frequency"], "local_readiness": ready,
            "calibration_accuracy_verified": False, "training_ready": False}


def wait_for_calibration(process: subprocess.Popen, roots: list[Path], plan: dict[str, Any],
                         out: Path, timeout: float) -> int:
    start = time.monotonic()
    notice = start
    nominal = plan["duration_seconds"] * plan["fps"]
    maximum_frames = nominal + 64
    byte_limit = maximum_frames * (plan["width"] * plan["height"] * 4 + 4096)
    while process.poll() is None:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise TimeoutError(f"Owned calibration process did not finish within {timeout:g} seconds")
        frames = replay.capture_files(roots, plan["movie_name"])
        size = sum(path.stat().st_size for path in frames)
        if len(frames) > maximum_frames or size > byte_limit:
            raise RuntimeError("Controlled calibration exceeded its bounded frame/disk budget")
        demo_roots = [root.parent for root in roots]
        if demo_roots:
            demo_roots.append(demo_roots[-1].parent)
        demos = recording_files(demo_roots, plan["movie_name"])
        if len(demos) > 1 or any(path.stat().st_size > 1024**3 for path in demos):
            raise RuntimeError("Controlled calibration has duplicate or oversized demo recordings")
        for filename, limit in (("controlled.dem", 1024**3), ("calibration_ledger.jsonl", 256 * 1024**2),
                                ("capture_ledger.jsonl", 512 * 1024**2)):
            path = out / filename
            if path.exists() and path.stat().st_size > limit:
                raise RuntimeError(f"Controlled calibration exceeded its {filename} size budget")
        if elapsed - (notice - start) >= 15:
            print(f"Calibration running: {elapsed:.0f}s elapsed, {len(frames)} frames, {size / 1024**2:.1f} MiB", flush=True)
            notice = time.monotonic()
        time.sleep(0.5)
    return process.returncode


def run_calibration(args: argparse.Namespace, original_plan: dict[str, Any]) -> dict[str, Any]:
    original_plan = validate_plan(original_plan)
    if not args.execute:
        raise ValueError("Executing calibration requires explicit --execute")
    if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800:
        raise ValueError("Timeout must be between 10 and 1800 seconds")
    out, game, plugin = args.output.resolve(), args.game_dir.resolve(), args.plugin.resolve()
    if out.is_relative_to(game) or game.is_relative_to(out):
        raise ValueError("Calibration output must be separate from the game installation")
    run_id = uuid.uuid4().hex
    plan = native_plan(original_plan, out, run_id)
    out.mkdir(parents=True, exist_ok=False)
    lease = replay.GameInfoLease(game, out, run_id)
    movie_roots = [lease.mod_dir / "movie", game / "csgo/movie"]
    manifest = out / "calibration.json"
    report = {"schema_version": 1, "profile": PROFILE, "run_id": run_id, "status": "preparing",
              "training_ready": False, "timing_status": "unverified", "physical_input_timestamps": False,
              "control_source": "native_engine_console_dispatch", "map": plan["map"],
              "source_plan": original_plan, "capture_prefix": plan["movie_name"],
              "game_dir": str(game), "owned_game_mod_dir": str(lease.mod_dir),
              "recovery_journal": str(lease.journal_path), "started_at_unix": time.time(),
              "settings_policy": "cloned-user-config-native-cloud-guard-and-byte-exact-restore",
              "steam_cloud_mode_changed": False, "normal_steam_launch_options_changed": False}
    replay.atomic_json(manifest, report)
    process, settings, failure = None, None, None
    try:
        ffmpeg, ffprobe, info = replay.preflight(game, plugin, args.ffmpeg, args.ffprobe,
                                                args.allow_version_mismatch)
        require_calibration_plugin(plugin)
        report["binary_profile"] = verify_binary_profile(game)
        report.update(cs2_build=info, plugin_sha256=replay.sha256_file(plugin))
        roots = replay.discover_settings_roots(game, args.steam_dir, args.steam_user_id)
        settings = replay.settings_guard.SettingsLease(out, run_id, roots,
            selectors={name: replay.SETTINGS_SELECTORS[name] for name in roots},
            excludes={name: values for name, values in replay.SETTINGS_EXCLUDES.items() if name in roots})
        report["settings_recovery_journal"] = str(settings.journal_path)
        report["settings_roots"] = {name: str(path) for name, path in roots.items()}
        replay.atomic_json(manifest, report)
        replay.require_cs2_idle()
        settings.snapshot()
        settings.clone_root("steam_local_cfg", out / "replay-settings/cfg")
        replay.atomic_json(out / "native-plan.json", plan)
        report["native_plan_sha256"] = replay.sha256_file(out / "native-plan.json")
        plugin_dir = lease.mod_dir / "bin/win64"
        plugin_dir.mkdir(parents=True)
        shutil.copyfile(plugin, plugin_dir / "server.dll")
        if replay.sha256_file(plugin_dir / "server.dll") != report["plugin_sha256"]:
            raise ValueError("Plugin changed during staging; calibration provenance is inconsistent")
        (lease.mod_dir / "movie").mkdir()
        if replay.capture_files(movie_roots, plan["movie_name"]):
            raise ValueError("Calibration movie prefix already exists")
        if recording_files([lease.mod_dir, game / "csgo", game], plan["movie_name"]):
            raise ValueError("Calibration recording prefix already exists")
        settings.verify_unchanged()
        if verify_binary_profile(game) != report["binary_profile"]:
            raise ValueError("Calibration binaries changed while preparing the run")
        replay.require_cs2_idle()
        lease.activate()
        replay.require_cs2_idle()
        launch = launch_arguments(game, out, plan)
        report.update(launch_arguments=launch, status="running")
        replay.atomic_json(manifest, report)
        environment = {**os.environ, "SteamAppId": "730", "SteamGameId": "730",
                       "USRLOCALCSGO": str(out / "replay-settings")}
        print(f"Launching one protected local calibration; recovery journal: {lease.journal_path}", flush=True)
        with (out / "cs2-process.log").open("xb") as log:
            process = subprocess.Popen(launch, cwd=game, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            lease.record_pid(process.pid)
            report["owned_cs2_pid"] = process.pid
            replay.atomic_json(manifest, report)
            code = wait_for_calibration(process, movie_roots, plan, out, args.timeout)
        report["cs2_exit_code"] = code
        if code:
            raise RuntimeError(f"Owned calibration process exited with code {code}")
        if replay.sha256_file(out / "native-plan.json") != report["native_plan_sha256"]:
            raise ValueError("Native calibration plan changed while the game was running")
    except BaseException as exc:
        failure = exc
        report["error"] = str(exc)
    finally:
        if process is not None:
            try:
                replay.stop_owned_process(process)
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
                replay.require_cs2_idle()
                if process is None:
                    settings.cancel_before_launch(replay.require_cs2_idle)
                    report["settings_restored"] = "not_modified"
                else:
                    settings.seal_after_exit(replay.require_cs2_idle)
                    restored = settings.restore(replay.require_cs2_idle)
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
                archive = replay.relocate_owned_mod(game, out, lease.mod_name)
                if archive is not None:
                    report["archived_game_mod_dir"] = str(archive)
                    movie_roots[0] = archive / "movie"
                report["staged_plugin_removed_from_game"] = not lease.mod_dir.exists()
            except BaseException as exc:
                report["staged_plugin_removed_from_game"] = False
                report["plugin_cleanup_error"] = str(exc)
                failure = failure or exc
        report.update(status="failed" if failure else "captured_unverified", finished_at_unix=time.time())
        replay.atomic_json(manifest, report)
    if failure is not None:
        raise RuntimeError(f"{failure}. Failed manifest: {manifest}") from failure
    try:
        if any(report.get(key) is not True for key in
               ("gameinfo_restored", "settings_restored", "staged_plugin_removed_from_game")):
            raise ValueError("Calibration protection did not verify restoration and plugin removal")
        report["settings_isolation"] = replay.verify_settings_isolation(out, expected_pid=process.pid)
        report["native_completion"] = verify_native_completion(out / "calibration_ledger.jsonl", plan)
        report["demo"] = archive_recording([movie_roots[0].parent, game / "csgo", game], out, plan["movie_name"])
        files = replay.capture_files(movie_roots, plan["movie_name"])
        capture_job = {"clip_id": plan["movie_name"], **{key: plan[key] for key in ("fps", "width", "height")}}
        report["tga_header"] = replay.archive_frames(files, out / "frames", capture_job)
        report["capture_frame_files_sha256"] = replay.sha256_file(out / "capture_frame_files.json")
        report.update(replay.encode_video(ffmpeg, ffprobe, out, capture_job, len(files), args.encoder))
        for name in ("calibration_ledger.jsonl", "capture_ledger.jsonl"):
            path = out / name
            if not path.is_file() or not path.stat().st_size:
                raise ValueError(f"Missing native calibration evidence: {name}")
            report[name.removesuffix(".jsonl")] = {"path": name, "sha256": replay.sha256_file(path)}
        report["status"] = "recorded_pending_independent_calibration_audit"
    except BaseException as exc:
        report.update(status="failed", error=str(exc))
        replay.atomic_json(manifest, report)
        raise RuntimeError(f"{exc}. Failed manifest: {manifest}") from exc
    replay.atomic_json(manifest, report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="Bounded schema-1 plan; defaults to the ten-second control probe")
    parser.add_argument("--output", required=True, type=Path, help="Fresh workspace directory")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--game-dir", type=Path, default=Path("C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive/game"))
    parser.add_argument("--steam-dir", type=Path)
    parser.add_argument("--steam-user-id")
    parser.add_argument("--plugin", type=Path, default=ROOT / "build/plugin-windows/Release/server.dll")
    parser.add_argument("--allow-version-mismatch", action="store_true")
    parser.add_argument("--ffmpeg", default=replay.local_video_tool("ffmpeg"))
    parser.add_argument("--ffprobe", default=replay.local_video_tool("ffprobe"))
    parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    parser.add_argument("--timeout", type=float, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800:
            raise ValueError("Timeout must be between 10 and 1800 seconds")
        plan = validate_plan(replay.read_json(args.plan) if args.plan else default_plan())
        if args.output.exists():
            raise ValueError("Calibration output must be a fresh directory")
        game, out = args.game_dir.resolve(), args.output.resolve()
        if out.is_relative_to(game) or game.is_relative_to(out):
            raise ValueError("Calibration output must be separate from the game installation")
        if not args.execute:
            effective = native_plan(plan, out, "0" * 32)
            print(json.dumps({"status": "planned", "training_ready": False,
                              "control_source": "native_engine_console_dispatch",
                              "physical_input_timestamps": False, "native_plan": effective,
                              "required_binary_profile": BINARY_PROFILE,
                              "launch_arguments": launch_arguments(game, out, effective),
                              "nominal_frames": plan["fps"] * plan["duration_seconds"],
                              "note": "No game, Steam settings, or output files changed. Native timing requires an independent audit."}, indent=2))
        else:
            print(json.dumps(run_calibration(args, plan), indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Calibration error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
