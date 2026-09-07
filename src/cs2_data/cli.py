"""Command line entry point for offline data preparation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .align import align
from .jobs import render_jobs
from .normalize import normalize
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
    jobs.add_argument("--max-state-gap-ticks", type=int, default=1)
    jobs.add_argument("--max-command-gap-ticks", type=int, default=4)
    jobs.add_argument("--allow-incomplete-commands", action="store_true",
                      help="Allow explicitly flagged diagnostic jobs even when commands are missing")
    alignment = commands.add_parser("align", help="Validate real video and join measured frame intervals to canonical commands")
    alignment.add_argument("--parsed", type=Path, required=True)
    alignment.add_argument("--timing", type=Path, required=True)
    alignment.add_argument("--clip", dest="clip_path", type=Path, required=True)
    alignment.add_argument("--out", type=Path, required=True)
    alignment.add_argument("--normalized", type=Path)
    alignment.add_argument("--max-gap-ticks", type=int, default=4)
    inspect = commands.add_parser("viewer", help="Create a standalone local video and action inspector")
    inspect.add_argument("--aligned", type=Path, required=True)
    inspect.add_argument("--out", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = vars(parser().parse_args(argv))
    command = args.pop("command")
    try:
        report = {"normalize": normalize, "render-jobs": render_jobs, "align": align, "viewer": viewer}[command](**args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "stage": command, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "complete", "stage": command, **report}, indent=2, allow_nan=False))
    return 0
