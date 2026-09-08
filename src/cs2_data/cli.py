"""Command line entry point for offline data preparation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .align import align
from .acceptance import accept_samples
from .calibration import calibrate
from .campaign import summarize_campaign
from .jobs import render_jobs
from .normalize import normalize
from .pipeline import process_render
from .timing import prepare_timing
from .viewer import viewer
from .validation import validate_clip
from .synchronization import audit_synchronization
from .causal_acceptance import accept_causal_samples
from .control_audit import audit_control_candidates, write_control_contract


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
    validation = commands.add_parser("validate-clip", help="Recheck capture, native POV, timing, and action evidence")
    validation.add_argument("--parsed", type=Path, required=True)
    validation.add_argument("--dataset", type=Path, required=True)
    validation.add_argument("--out", type=Path, required=True)
    validation.add_argument("--state-context", type=Path, help="Independent observed game-rule and weapon-clock sidecar from cs2-context")
    sync = commands.add_parser("audit-synchronization", help="Recompute retained protobuf/native clock associations without certifying input support")
    sync.add_argument("--parsed", type=Path, required=True)
    sync.add_argument("--dataset", type=Path, required=True)
    sync.add_argument("--network-clock", type=Path, required=True)
    sync.add_argument("--out", type=Path, required=True)
    causal = commands.add_parser("accept-causal-samples", help="Accept future server-command samples using freshly verified packet bounds")
    causal.add_argument("--parsed", type=Path, required=True)
    causal.add_argument("--dataset", type=Path, required=True)
    causal.add_argument("--network-clock", type=Path, required=True)
    causal.add_argument("--state-context", type=Path, required=True)
    causal.add_argument("--out", type=Path, required=True)
    causal.add_argument("--history-frames", type=int, default=8)
    causal.add_argument("--target-horizon-frames", type=int, default=2)
    control = commands.add_parser("control-contract", help="Write the proposed 32 Hz action JSON Schema; calibration remains unmeasured")
    control.add_argument("--out", type=Path, required=True)
    control_audit = commands.add_parser("audit-control-candidates", help="Reverify accepted observations and audit diagnostic two-command control candidates")
    control_audit.add_argument("--acceptance", type=Path, required=True)
    control_audit.add_argument("--out", type=Path, required=True)
    acceptance = commands.add_parser("accept-samples", help="Write accepted/rejected temporal sample manifests with explicit reasons")
    acceptance.add_argument("--parsed", type=Path, required=True)
    acceptance.add_argument("--dataset", type=Path, required=True)
    acceptance.add_argument("--validation", type=Path, required=True)
    acceptance.add_argument("--out", type=Path, required=True)
    acceptance.add_argument("--state-context", type=Path)
    acceptance.add_argument("--history-frames", type=int, default=8)
    acceptance.add_argument("--target-horizon-frames", type=int, default=1)
    acceptance.add_argument("--no-previous-actions", dest="include_previous_actions", action="store_false",
                            help="Exclude previous actions from sample inputs; default includes them")
    campaign = commands.add_parser("summarize-campaign", help="Revalidate and summarize several sample-acceptance runs")
    campaign.add_argument("--acceptance", type=Path, nargs="+", required=True)
    campaign.add_argument("--out", type=Path, required=True)
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
                  "validate-clip": validate_clip, "accept-samples": accept_samples,
                  "audit-synchronization": audit_synchronization,
                  "accept-causal-samples": accept_causal_samples,
                  "control-contract": write_control_contract,
                  "audit-control-candidates": audit_control_candidates,
                  "summarize-campaign": summarize_campaign,
                  "align": align, "viewer": viewer}[command](**args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "stage": command, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "complete", "stage": command, **report}, indent=2, allow_nan=False))
    return 0
