"""Compile a bounded 32 Hz action program and run it through protected OS input.

Dry-run validates the requested actions without creating files or launching CS2.
Execution first recomputes the original mouse/keyboard calibration evidence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import calibration_windows as calibration
import synthetic_windows as synthetic
from cs2_data.control_executor import compile_sequence
from cs2_data.control_label_audit import load_measured_control_profile
from cs2_data.control_program import default_program, project_events, validate_program

PROJECT = ROOT.parents[1]
DATA = PROJECT / "data/calibration"
MOUSE_DEMO = "fcab7de82ed3cf8a5a38754d122379f54f4ad0bb4cf72a633c393b5c808aaef1"
KEYBOARD_DEMO = "1e0e8d1127fa0172732bfbf5fff8817a005b78d3c407a1c12a223e2de9a1324e"


def verify_ready_configuration(row, profile):
    actual = row.get("synthetic_input_ready_convars", {})
    for name, required in profile.ready_requirements.items():
        try:
            if not isinstance(actual[name], str):
                raise ValueError("Native convar readback is not a string")
            value = float(actual[name])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("Missing actual ready-time controller setting: " + name) from error
        if not math.isfinite(value) or value != float(required):
            raise ValueError("Ready-time controller setting differs from its calibrated requirement: " + name)


class ExecutionBackend:
    PROFILE, CONTROL_SOURCE = synthetic.PROFILE, synthetic.CONTROL_SOURCE
    validate_plan = staticmethod(synthetic.validate_plan)
    native_plan = staticmethod(synthetic.native_plan)
    launch_arguments = staticmethod(synthetic.launch_arguments)

    def __init__(self, profile, compilation_dir):
        profile.require_checked()
        self.profile, self.compilation_dir = profile, compilation_dir
        self.evidence = {name: calibration.replay.sha256_file(compilation_dir / name)
                         for name in ("program.json", "compiled.json", "synthetic-plan.json", "projection.json")}

    def require_plugin(self, plugin):
        synthetic.require_plugin(plugin)
        if b"synthetic_input_ready_convars" not in plugin.read_bytes():
            raise ValueError("Controller execution requires the ready-time settings readback plugin")
        if dict(self.profile.binary_profile) != calibration.BINARY_PROFILE:
            raise ValueError("Controller calibration and installed-build requirements disagree")
        self.profile.verify_sources_unchanged()

    def wait_for_calibration(self, process, roots, plan, out, timeout):
        profile = self.profile
        class ExecutionBridge(synthetic.NativeBridge):
            def accept(self, row):
                super().accept(row)
                if row.get("event") == "calibration_ready":
                    verify_ready_configuration(row, profile)
        return synthetic.wait_for_calibration(process, roots, plan, out, timeout, bridge_factory=ExecutionBridge)

    def verify_native_completion(self, path, plan, *, require_settle=False):
        result = calibration.verify_native_completion(path, plan, require_settle=require_settle)
        verify_ready_configuration(result["local_readiness"], self.profile)
        return result

    def verify_input_completion(self, out, plan, pid):
        result = synthetic.verify_input_completion(out, plan, pid)
        for name, digest in self.evidence.items():
            if calibration.replay.sha256_file(self.compilation_dir / name) != digest:
                raise ValueError("Controller compilation evidence changed during execution")
        result["controller_execution"] = {"profile": "cs2-control-execution-program-v1",
            "source_directory": str(self.compilation_dir), "source_sha256": self.evidence,
            "calibration_provenance_sha256": self.profile.provenance_sha256,
            "schedule": "exact_32hz_boundaries_encoded_as_equivalent_native_tick_thresholds",
            "game_response_verified": False, "training_ready": False}
        return result


def argument_parser():
    parser = calibration.argument_parser()
    parser.description = __doc__
    parser.add_argument("--evidence-output", type=Path, required=True, help="Fresh workspace directory for reverified profile and compilation")
    parser.add_argument("--mouse-run", type=Path, default=DATA / "synthetic-001")
    parser.add_argument("--mouse-parsed", type=Path, default=DATA / "synthetic-001-parsed" / MOUSE_DEMO)
    parser.add_argument("--keyboard-run", type=Path, default=DATA / "synthetic-004")
    parser.add_argument("--keyboard-parsed", type=Path, default=DATA / "synthetic-004-parsed" / KEYBOARD_DEMO)
    parser.add_argument("--keyboard-protocol", type=Path, default=ROOT / "plans/synthetic-keyboard-protocol-v1.json")
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        program = validate_program(calibration.replay.read_json(args.plan) if args.plan else default_program())
        out, evidence, game = args.output.resolve(), args.evidence_output.resolve(), args.game_dir.resolve()
        if (out.exists() or evidence.exists() or not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800 or
                out.is_relative_to(game) or game.is_relative_to(out) or evidence.is_relative_to(game) or
                game.is_relative_to(evidence) or out.is_relative_to(evidence) or evidence.is_relative_to(out)):
            raise ValueError("Controller run and evidence need separate fresh paths outside the game and a bounded timeout")
        if not args.execute:
            print(json.dumps({"status": "planned", "decision_count": len(program["actions"]),
                "decision_period_ns": 31250000, "program_duration_seconds": len(program["actions"]) / 32,
                "capture_duration_seconds": program["duration_seconds"], "profile_revalidation_required": True,
                "actual_ready_settings_readback_required": True, "training_ready": False}, indent=2))
            return 0
        profile = load_measured_control_profile(args.mouse_run, args.mouse_parsed, args.keyboard_run,
            args.keyboard_parsed, args.keyboard_protocol, evidence_output=evidence / "calibration")
        compiled = compile_sequence(program["actions"], profile)
        plan, projection = project_events(program, compiled["events"])
        for name, value in (("program.json", program), ("compiled.json", compiled),
                            ("synthetic-plan.json", plan), ("projection.json", projection)):
            calibration.replay.atomic_json(evidence / name, value)
        report = calibration.run_calibration(args, plan, backend=ExecutionBackend(profile, evidence))
        print(json.dumps({"manifest": str(out / "calibration.json"), "compilation": str(evidence),
            "status": report["status"], "decisions": compiled["decision_count"], "events": compiled["event_count"],
            "frames": report["num_frames"], "demo": report["demo"], "settings_restored": report["settings_restored"]}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print("Controller execution error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
