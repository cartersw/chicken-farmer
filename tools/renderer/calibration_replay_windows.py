"""Replay an owned controlled calibration demo through the protected worker.

Dry-run is the default. The resulting footage is calibration evidence, not an
accepted competitive training dataset. Identity and tick arguments must come
from parsing this recording, not from the source plan's simulation milliseconds.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import calibration_windows as calibration
import windows as replay

PROFILE = "cs2-controlled-calibration-replay-v1"


def verified_recording(run: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run = run.resolve()
    report_path, plan_path, demo = run / "calibration.json", run / "native-plan.json", run / "controlled.dem"
    report_digest = replay.sha256_file(report_path)
    report = replay.read_json(report_path)
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1 or
            report.get("profile") != calibration.PROFILE or
            report.get("status") != "recorded_pending_independent_calibration_audit" or
            report.get("training_ready") is not False or report.get("timing_status") != "unverified" or
            any(report.get(key) is not True for key in
                ("gameinfo_restored", "settings_restored", "staged_plugin_removed_from_game"))):
        raise ValueError("Source must be a completed, restored controlled calibration run")
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Calibration run has no valid ownership identifier")
    pid = report.get("owned_cs2_pid")
    if type(pid) is not int or not 0 < pid < 2**32:
        raise ValueError("Calibration run has no valid owned process identity")
    watched = {report_path: report_digest, **{path: replay.sha256_file(path) for path in (
        plan_path, demo, run / "calibration_ledger.jsonl", run / "settings-recovery.json", run / "settings-isolation.json")}}
    plan = replay.read_json(plan_path)
    expected_plan = calibration.native_plan(report.get("source_plan"), run, run_id)
    if plan != expected_plan or report.get("capture_prefix") != plan["movie_name"]:
        raise ValueError("Calibration native plan or prefix disagrees with its owned source")
    if watched[plan_path] != report.get("native_plan_sha256"):
        raise ValueError("Calibration plan hash changed")
    completion = calibration.verify_native_completion(run / "calibration_ledger.jsonl", plan)
    if completion != report.get("native_completion"):
        raise ValueError("Calibration completion evidence disagrees with its manifest")
    metadata = report.get("demo", {})
    if (not isinstance(metadata, dict) or Path(metadata.get("path", "")).resolve() != demo or
            not demo.is_file() or demo.is_symlink() or demo.resolve() != demo or
            watched[demo] != metadata.get("sha256") or demo.stat().st_size != metadata.get("size_bytes")):
        raise ValueError("Owned calibration recording path, size or hash changed")
    journal = replay.read_json(run / "settings-recovery.json")
    if (journal.get("run_id") != run_id or journal.get("state") != "restored" or
            journal != report.get("settings_restore_verified")):
        raise ValueError("Calibration settings recovery journal is incomplete")
    isolation = replay.verify_settings_isolation(run, expected_pid=pid)
    if isolation != report.get("settings_isolation"):
        raise ValueError("Calibration native settings-isolation evidence changed")
    if any(replay.sha256_file(path) != digest for path, digest in watched.items()):
        raise ValueError("Calibration source evidence changed during verification")
    return report, {"calibration_run": str(run), "calibration_report_sha256": watched[report_path],
                    "native_plan_sha256": watched[plan_path],
                    "native_ledger_sha256": completion["ledger_sha256"], "demo_sha256": metadata["sha256"]}


def replay_job(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    report, provenance = verified_recording(args.calibration_run)
    if not isinstance(args.steam_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", args.steam_id):
        raise ValueError("Steam identity must be a canonical decimal string from the parsed calibration demo")
    recorded_identity = report["native_completion"]["local_readiness"]["local_player"].get("steam_id")
    if args.steam_id != recorded_identity:
        raise ValueError("Replay Steam identity must match the original controlled local player")
    if args.end_demo_tick - args.start_demo_tick > replay.MAX_PILOT_TICKS:
        raise ValueError("Calibration replay requests are bounded to 320 demo ticks; use distinct runs for additional windows")
    source_id = replay.byte_hash(json.dumps({"profile": PROFILE, "source": provenance,
        "interval": [args.start_demo_tick, args.end_demo_tick], "steam_id": args.steam_id,
        "player_slot": args.player_slot, "spectator_user_id": args.spectator_user_id}, sort_keys=True).encode())[:24]
    raw = {"schema_version": 1, "timing_clock": "demo_tick", "demo_id": provenance["demo_sha256"],
           "demo_path": str(args.calibration_run.resolve() / "controlled.dem"),
           "clip_id": "calibration-replay-" + source_id,
           "round_id": 1, "steam_id": args.steam_id, "player_slot": args.player_slot,
           "spectator_user_id": args.spectator_user_id,
           "start_demo_tick": args.start_demo_tick, "end_demo_tick": args.end_demo_tick,
           "fps": 32, "width": 1280, "height": 720,
           "calibration_replay_profile": PROFILE, "calibration_source": provenance,
           "training_scope": "controlled_calibration_diagnostic", "training_ready": False,
           "round_id_basis": "diagnostic_placeholder_not_competitive_round_acceptance"}
    return replay.validate_job(raw), raw


def argument_parser() -> argparse.ArgumentParser:
    parser = replay.argument_parser()
    parser.description = __doc__
    parser.add_argument("--calibration-run", type=Path, required=True)
    parser.add_argument("--start-demo-tick", type=int, required=True)
    parser.add_argument("--end-demo-tick", type=int, required=True)
    parser.add_argument("--steam-id", required=True)
    parser.add_argument("--player-slot", type=int, required=True)
    parser.add_argument("--spectator-user-id", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        if args.output is None or args.spec or args.repair or args.repair_settings or args.seal_current_settings:
            raise ValueError("Supply a fresh --output; arbitrary specs and recovery actions belong to the original replay CLI")
        if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800:
            raise ValueError("Timeout must be between 10 and 1800 seconds")
        if args.output.exists():
            raise ValueError("Calibration replay output must be a fresh directory")
        out, game = args.output.resolve(), args.game_dir.resolve()
        if out.is_relative_to(game) or game.is_relative_to(out):
            raise ValueError("Calibration replay output must be separate from the game installation")
        if args.max_ticks != replay.MAX_PILOT_TICKS:
            raise ValueError("Use an explicit bounded start/end interval instead of changing --max-ticks")
        effective, original = replay_job(args)
        sequence = replay.make_sequence(effective, args.warmup_seconds)
        if not args.execute:
            print(json.dumps({"status": "planned", "training_ready": False, "profile": PROFILE,
                "job": effective, "sequences": sequence,
                "launch_arguments": replay.launch_arguments(args.game_dir.resolve(), effective,
                    args.output.resolve() / "input.dem", args.output.resolve() / "plugin.log", args.allow_version_mismatch),
                "note": "No output or game files changed. This job is controlled calibration evidence."}, indent=2))
            return 0
        calibration.require_calibration_plugin(args.plugin.resolve())
        calibration.verify_binary_profile(args.game_dir.resolve())
        result = replay.run_capture(args, effective, original)
        replay.atomic_json(args.output / "calibration_replay.json", {
            "schema_version": 1, "profile": PROFILE, "training_ready": False,
            "calibration_source": original["calibration_source"],
            "render_manifest": effective["clip_id"] + ".render.json",
            "render_manifest_sha256": replay.sha256_file(args.output / (effective["clip_id"] + ".render.json")),
            "comparison_verified": False})
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Calibration replay error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
