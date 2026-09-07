"""Command line entry point for offline data preparation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .align import align
from .calibration import calibrate
from .jobs import render_jobs
from .normalize import normalize
from .pipeline import process_render
from .timing import prepare_timing
from .viewer import viewer


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cs2-data", description="Prepare CS2 demo actions and align measured replay frames")
    commands = root.add_subparsers(dest="command", required=True)
    norm = commands.add_parser("normalize", help="Stream angular labels and mouse-scale diagnostics")
    norm.add_argument("--parsed", type=Path, required=True)
    norm.add_argument("--out", type=Path, required=True)
    norm.add_argument("--max-gap-ticks", type=int, default=4)
    jobs = commands.add_parser("render-jobs", help="Write alive player-round render jobs as JSONL")
    jobs.add_argument("--parsed", type=Path, required=True)
    jobs.add_argument("--out", type=Path, required=True)
    jobs.add_argument("--demo", type=Path)
    jobs.add_argument("--fps", type=int, default=32)
    jobs.add_argument("--width", type=int, default=1280)
    jobs.add_argument("--height", type=int, default=720)
    jobs.add_argument("--min-ticks", type=int, default=32)
    jobs.add_argument("--limit", type=int)
    jobs.add_argument("--round-id", type=int)
    jobs.add_argument("--steam-id", type=int)
    jobs.add_argument("--start-demo-tick", type=int, help="Optional requested interval start (requires end)")
    jobs.add_argument("--end-demo-tick", type=int, help="Optional exclusive interval end (requires start)")
    jobs.add_argument("--max-state-gap-ticks", type=int, default=1)
    jobs.add_argument("--max-command-gap-ticks", type=int, default=4)
    jobs.add_argument("--allow-incomplete-commands", action="store_true",
                      help="Allow explicitly flagged diagnostic jobs even when commands are missing")
    jobs.add_argument("--phase-manifest", type=Path,
                      help="Hash-bound match-phase sidecar from cs2-phases")
    jobs.add_argument("--allow-unverified-phase", action="store_true",
                      help="Explicitly allow diagnostic setup/unknown phase jobs")
    calibration = commands.add_parser("calibrate", help="Audit command execution clocks against replay ticks")
    calibration.add_argument("--parsed", type=Path, required=True)
    calibration.add_argument("--out", type=Path, required=True)
    calibration.add_argument("--round-id", type=int, required=True)
    calibration.add_argument("--steam-id", type=int, required=True)
    calibration.add_argument("--player-slot", type=int, required=True)
    calibration.add_argument("--start-demo-tick", type=int, required=True)
    calibration.add_argument("--end-demo-tick", type=int, required=True)
    calibration.add_argument("--anchors", type=Path,
                             help="Independent measured execution anchors and hashed evidence")
    calibration.add_argument("--max-gap-ticks", type=int, default=4)
    timing = commands.add_parser("prepare-timing", help="Reconcile native capture observations with retained frames")
    timing.add_argument("--clip", dest="clip_path", type=Path, required=True)
    timing.add_argument("--ledger", type=Path, required=True)
    timing.add_argument("--pts", dest="pts_path", type=Path, required=True)
    timing.add_argument("--frames", dest="frames_dir", type=Path, required=True)
    timing.add_argument("--out", type=Path, required=True)
    pipeline = commands.add_parser("process-render", help="Build a diagnostic frame/action dataset and viewer from one native capture")
    pipeline.add_argument("--parsed", type=Path, required=True)
    pipeline.add_argument("--render-dir", type=Path, required=True)
    pipeline.add_argument("--out", type=Path, required=True)
    pipeline.add_argument("--normalized", type=Path)
    alignment = commands.add_parser("align", help="Validate real video and join measured frame intervals to canonical commands")
    alignment.add_argument("--parsed", type=Path, required=True)
    alignment.add_argument("--timing", type=Path, required=True)
    alignment.add_argument("--clip", dest="clip_path", type=Path, required=True)
    alignment.add_argument("--out", type=Path, required=True)
    alignment.add_argument("--normalized", type=Path)
    alignment.add_argument("--calibration", type=Path,
                           help="Execution-clock calibration; inferred candidates remain diagnostic")
    alignment.add_argument("--diagnostic", action="store_true",
                           help="Inspect evidence-backed unverified capture timing/POV; never training-ready")
    alignment.add_argument("--max-gap-ticks", type=int, default=4)
    inspect = commands.add_parser("viewer", help="Create a standalone local video and action inspector")
    inspect.add_argument("--aligned", type=Path, required=True)
    inspect.add_argument("--out", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = vars(parser().parse_args(argv))
    command = args.pop("command")
    try:
        report = {"normalize": normalize, "render-jobs": render_jobs, "calibrate": calibrate,
                  "prepare-timing": prepare_timing, "process-render": process_render,
                  "align": align, "viewer": viewer}[command](**args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "stage": command, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "complete", "stage": command, **report}, indent=2, allow_nan=False))
    return 0
