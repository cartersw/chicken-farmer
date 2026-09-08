"""Test the frozen 008 turn-presentation hypothesis without retuning it.

This reports original-camera/input-history correspondence and replay camera
prediction errors. Neither relationship certifies physical input latency.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path
import struct

from .calibration_analysis import _read_ledger, _read_object
from .calibration_pixels import _audit, _owned, _require
from .calibration_replay_analysis import _number, _replay_pixels, _state, _vector, _wrapped
from .io import batches, exclusive_output, parsed_manifest, publish, sha256_file, staging_paths

FROZEN_HYPOTHESIS_SHA256 = "e2b6694f166e44639eed94b34e281ca975fbdee362db3643a224fe2289ddc29e"
DERIVATION_DEMO = "2543ea417181d95a09bec91910b46c4d3cc8af3eb56d7939bc9a23dad4932f8b"
TICK_RATE = 64
ANGLE_TOLERANCE = 0.001
CHANGING_ANGLE_THRESHOLD = 0.01
MAXIMUM_SNAPSHOT_GAP = 4


def _float32_clock_tolerance(seconds):
    """Half one stored-float32 ULP, expressed as ticks, plus double conversion slack."""
    _require(_number(seconds) and seconds > 0, "Invalid observed render clock")
    try:
        bits = struct.unpack("<I", struct.pack("<f", seconds))[0]
        current = struct.unpack("<f", struct.pack("<I", bits))[0]
        following = struct.unpack("<f", struct.pack("<I", bits+1))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("Observed render clock is outside float32 range") from error
    _require(math.isfinite(following) and abs(current-seconds) <= (following-current)/2,
             "Invalid float32 movie clock")
    return (following-current)*TICK_RATE/2 + 1e-7


def compare_input_history(states, commands):
    """Use a measured-clock rounding interval; never search for a fitted offset."""
    by_player = defaultdict(lambda: defaultdict(list))
    invalid_history = 0
    for row_index, command in enumerate(commands):
        steam = command.get("steam_id")
        if type(steam) is int and steam > 0:
            steam = str(steam)
        if not isinstance(steam, str) or not steam.isdigit() or int(steam) <= 0:
            continue
        history = command.get("input_history") or []
        _require(isinstance(history, list), "Malformed command input history")
        for index, item in enumerate(history):
            tick, fraction = item.get("render_tick_count"), item.get("render_tick_fraction")
            if (type(tick) is not int or tick < 0 or not _number(fraction) or not 0 <= fraction < 1):
                invalid_history += 1
                continue
            by_player[steam][tick+fraction].append({
                "command_row_index": row_index, "command_number": command.get("command_number"),
                "server_tick_executed": command.get("server_tick_executed"), "demo_tick": command.get("demo_tick"),
                "history_index": index, "view_yaw": item.get("view_yaw"), "view_pitch": item.get("view_pitch"),
                "player_tick_count": item.get("player_tick_count"), "player_tick_fraction": item.get("player_tick_fraction")})
    timelines = {player: sorted(values) for player, values in by_player.items()}
    rows = []
    for state in states:
        clock = state.get("render_time_seconds")
        tolerance = _float32_clock_tolerance(clock)
        value = clock*TICK_RATE
        times = timelines.get(state["steam_id"], [])
        candidates = times[bisect_left(times, value-tolerance):bisect_right(times, value+tolerance)]
        result = {"capture_index": state["capture_index"], "movie_render_clock_ticks": value,
                  "rounding_tolerance_ticks": tolerance, "status": "no_unique_history_clock",
                  "candidate_clock_count": len(candidates)}
        if len(candidates) == 1:
            timestamp = candidates[0]
            occurrences = by_player[state["steam_id"]][timestamp]
            angles = {(x["view_pitch"], x["view_yaw"]) for x in occurrences}
            result.update(history_render_clock_ticks=timestamp, clock_residual_ticks=value-timestamp,
                          occurrences=occurrences, conflicting_angles=len(angles) > 1)
            if len(angles) == 1:
                pitch, yaw = next(iter(angles))
                camera = state.get("rendered_camera", {}).get("angles")
                if _number(pitch) and _number(yaw) and _vector(camera):
                    errors = [_wrapped(camera[0]-pitch), _wrapped(camera[1]-yaw)]
                    result.update(status="unique_clock_and_angle_observation", camera_pitch_yaw_error_degrees=errors,
                                  camera_angles_within_tolerance=all(abs(x) <= ANGLE_TOLERANCE for x in errors))
        rows.append(result)
    residuals = [abs(r["clock_residual_ticks"]) for r in rows if "clock_residual_ticks" in r]
    return {"association": "recorded_render_clock_within_float32_rounding_interval",
            "clock_offset_fitted": False, "invalid_or_unavailable_history_entries": invalid_history,
            "observations": len(rows), "unique_history_clock_matches": len(residuals),
            "maximum_clock_residual_ticks": max(residuals, default=None),
            "camera_angle_matches": sum(r.get("camera_angles_within_tolerance") is True for r in rows), "frames": rows}


def replay_candidate(states, offset):
    """Evaluate the fixed +1 hypothesis or its fixed zero-offset control."""
    _require(type(offset) is int and offset in (0, 1), "Only frozen candidate/control offsets are supported")
    rows, skipped = [], defaultdict(int)
    for previous, current in zip(states, states[1:]):
        if (current["capture_index"] != previous["capture_index"]+1 or current["steam_id"] != previous["steam_id"] or
                current.get("player_identity") != previous.get("player_identity")):
            skipped["identity_or_capture_boundary"] += 1
            continue
        before, after = previous["pawn_state"], current["pawn_state"]
        if not (_vector(before.get("eye_angles")) and _vector(after.get("eye_angles")) and
                _vector(current.get("rendered_camera", {}).get("angles"))):
            skipped["angles_unavailable"] += 1
            continue
        difference = _wrapped(after["eye_angles"][1]-before["eye_angles"][1])
        if abs(difference) <= CHANGING_ANGLE_THRESHOLD:
            skipped["unchanged_eye_yaw"] += 1
            continue
        if abs(difference) >= 180-ANGLE_TOLERANCE:
            skipped["ambiguous_half_turn_direction"] += 1
            continue
        a, b, render = before.get("simulation_time"), after.get("simulation_time"), current.get("render_time_seconds")
        if not all(_number(x) and x >= 0 for x in (a, b, render)) or not 0 < (b-a)*TICK_RATE <= MAXIMUM_SNAPSHOT_GAP:
            skipped["snapshot_clock_unavailable_or_gap"] += 1
            continue
        start, end, image = a*TICK_RATE+offset, b*TICK_RATE+offset, render*TICK_RATE
        alpha = (image-start)/(end-start)
        predicted = _wrapped(before["eye_angles"][1]+min(1, max(0, alpha))*difference)
        actual = current["rendered_camera"]["angles"][1]
        error = _wrapped(actual-predicted)
        rows.append({"previous_capture_index": previous["capture_index"], "capture_index": current["capture_index"],
            "controller_tick_base": current["controller_tick_base"], "snapshot_interval_ticks": [start, end],
            "image_render_clock_ticks": image, "alpha_unclamped": alpha, "within_snapshot_interval": 0 <= alpha <= 1,
            "previous_eye_yaw": before["eye_angles"][1], "current_eye_yaw": after["eye_angles"][1],
            "snapshot_yaw_change_degrees": difference, "predicted_camera_yaw": predicted, "observed_camera_yaw": actual,
            "residual_degrees": error, "within_frozen_error_tolerance": abs(error) <= ANGLE_TOLERANCE})
    return {"snapshot_time_offset_ticks": offset, "maximum_absolute_error_tolerance_degrees": ANGLE_TOLERANCE,
            "evaluated_changing_snapshots": len(rows), "within_error_tolerance": sum(x["within_frozen_error_tolerance"] for x in rows),
            "maximum_absolute_residual_degrees": max((abs(x["residual_degrees"]) for x in rows), default=None),
            "increasing_yaw_examples": sum(x["snapshot_yaw_change_degrees"] > 0 for x in rows),
            "decreasing_yaw_examples": sum(x["snapshot_yaw_change_degrees"] < 0 for x in rows),
            "skipped": dict(skipped), "frames": rows}


def dispatch_capture_intervals(controls, states):
    ordered = [r for r in states if type(r.get("qpc_before")) is int]
    qpcs = [r["qpc_before"] for r in ordered]
    _require(all(b > a for a, b in zip(qpcs, qpcs[1:])), "Source capture QPC order is ambiguous")
    result = []
    for event in controls:
        if event.get("event") != "action_dispatch":
            continue
        command = event.get("command", "")
        if not (command.startswith(("+turn", "-turn", "setang "))):
            continue
        before, after = event.get("qpc_before"), event.get("qpc_after")
        _require(type(before) is int and type(after) is int and before <= after, "Invalid dispatch QPC interval")
        earlier = bisect_left(qpcs, before)-1
        later = bisect_right(qpcs, after)
        def clock_at(index):
            if not 0 <= index < len(ordered):
                return None
            return {k: ordered[index].get(k) for k in ("capture_index", "qpc_before", "controller_tick_base", "render_time_seconds")}
        result.append({"id": event.get("id"), "command": command, "qpc_dispatch_interval": [before, after],
                       "actual_elapsed_simulation_ms": event.get("actual_elapsed_ms"),
                       "previous_capture": clock_at(earlier), "next_capture": clock_at(later),
                       "association": "capture_dispatch_order_only_not_effect_or_consumption_time"})
    return result


def _markdown(report):
    candidate = report["replay_plus_one_candidate"]
    control = report["replay_zero_offset_control"]
    history = report["source_input_history"]
    return "\n".join(["# Turn presentation diagnostic", "",
        f"Role: **{report['experiment_role']}**. Training and physical-input timing remain unverified.", "",
        f"Original movie clocks match {history['unique_history_clock_matches']}/{history['observations']} recorded input-history clocks "
        f"within the stored float32 clock rounding interval; maximum residual {history['maximum_clock_residual_ticks']} ticks. "
        f"Camera pitch/yaw also agree within 0.001 degrees for {history['camera_angle_matches']} observations. "
        "Recoil and other camera effects can produce differences.", "",
        "The frozen candidate circularly interpolates the previous/current observed pawn-eye yaw at snapshot simulation times plus one tick, "
        "evaluated using the current movie render clock. It does not shift command timestamps.", "",
        f"Candidate: {candidate['within_error_tolerance']}/{candidate['evaluated_changing_snapshots']} changing snapshot pairs "
        f"within the frozen 0.001-degree tolerance; maximum error {candidate['maximum_absolute_residual_degrees']} degrees. "
        f"Coverage: {candidate['increasing_yaw_examples']} increasing-yaw, {candidate['decreasing_yaw_examples']} decreasing-yaw pairs.", "",
        f"Zero-offset control: maximum error {control['maximum_absolute_residual_degrees']} degrees.", "",
        "The candidate was derived from control008. A different recording is evaluated with the same fixed formula and tolerance, without fitting. "
        "Even a successful held-out comparison remains evidence about these captures, not proof of the native interpolation buffer, "
        "universal replay timing, button semantics, physical-device latency or exact input consumption.", ""])


@exclusive_output(file_output=True)
def analyze_turns(comparison_path: Path, parsed: Path, hypothesis_path: Path, out: Path):
    _require(out.suffix.lower() == ".json", "Turn report output must end in .json")
    markdown = out.with_suffix(".md")
    staged_md, staged = staging_paths([markdown, out])
    try:
        _require(sha256_file(hypothesis_path) == FROZEN_HYPOTHESIS_SHA256, "Frozen hypothesis hash mismatch; no retuning permitted")
        comparison_digest = sha256_file(comparison_path)
        comparison = _read_object(comparison_path)
        source_run, replay_run = Path(comparison["source_run"]).resolve(), Path(comparison["replay_run"]).resolve()
        source = _audit(source_run)
        replay, replay_movies = _replay_pixels(replay_run, source)
        _require(source["source_hashes"] == comparison["source_pixel_audit"]["source_hashes"] and
                 replay["source_hashes"] == comparison["replay_pixel_audit"]["source_hashes"], "Comparison source provenance mismatch")
        source_movies = [r for r in _read_ledger(_owned(source_run, "capture_ledger.jsonl")) if r["event"] == "movie_frame"]
        source_states, replay_states = [_state(r, True) for r in source_movies], [_state(r, False) for r in replay_movies]
        controls = _read_ledger(_owned(source_run, "calibration_ledger.jsonl"))
        manifest = parsed_manifest(parsed, ("usercmd.parquet",))
        _require(manifest.get("demo_id") == source["source_hashes"]["controlled.dem"] and manifest.get("tick_rate") == TICK_RATE,
                 "Canonical commands do not identify this 64Hz calibration recording")
        parsed_hashes = {name: sha256_file(parsed/name) for name in ("manifest.json", "usercmd.parquet")}
        commands = []
        for batch in batches(parsed/"usercmd.parquet"):
            commands.extend(batch)
            _require(len(commands) <= 200000, "Turn diagnostic command count exceeds bounded calibration scope")
        report = {"schema_version": 1, "profile": "controlled-turn-presentation-diagnostic-v1",
            "status": "fixed_candidate_evaluated", "training_ready": False, "physical_input_timing_verified": False,
            "input_consumption_timing_verified": False, "universal_clock_rule_verified": False,
            "experiment_role": "derivation_recording" if manifest["demo_id"] == DERIVATION_DEMO else "different_recording_fixed_candidate_test",
            "candidate_fitted_in_this_analysis": False, "hypothesis_sha256": FROZEN_HYPOTHESIS_SHA256,
            "hypothesis_path": str(hypothesis_path.resolve()), "comparison_path": str(comparison_path.resolve()),
            "comparison_sha256": comparison_digest, "parsed_path": str(parsed.resolve()), "parsed_source_hashes": parsed_hashes,
            "verifier_source_sha256": sha256_file(Path(__file__)), "source_evidence_hashes": source["source_hashes"],
            "replay_evidence_hashes": replay["source_hashes"], "source_pixel_comparison": source["pixel_comparison"],
            "replay_pixel_comparison": replay["pixel_comparison"], "parser_warnings": manifest.get("warnings", {}),
            "source_input_history": compare_input_history(source_states, commands),
            "replay_plus_one_candidate": replay_candidate(replay_states, 1),
            "replay_zero_offset_control": replay_candidate(replay_states, 0),
            "dispatch_capture_intervals": dispatch_capture_intervals(controls, source_states),
            "limits": ["Input-history render timestamps describe recorded game data, not physical input devices.",
                "The native interpolation buffer itself has not been observed; adjacent captured eye snapshots are a candidate model.",
                "No offset is fitted here, and no future command label or training acceptance is produced.",
                "Set-angle cuts, recoil, identity changes and different cadence may disprove this bounded candidate."]}
        _require(sha256_file(comparison_path) == comparison_digest and sha256_file(hypothesis_path) == FROZEN_HYPOTHESIS_SHA256 and
                 all(sha256_file(parsed/name) == digest for name, digest in parsed_hashes.items()), "Turn source evidence changed during analysis")
        for run, evidence in ((source_run, source), (replay_run, replay)):
            _require(all(sha256_file(_owned(run, name)) == digest for name, digest in evidence["source_hashes"].items()), "Capture evidence changed")
            for frame in evidence["frames"]:
                name = frame.get("archived_name", frame.get("tga_filename"))
                digest = frame.get("sha256", frame.get("file_sha256"))
                _require(sha256_file(_owned(run, "frames/"+name)) == digest, "Raw capture frame changed")
        staged_md.write_text(_markdown(report), encoding="utf-8")
        report["markdown_sha256"] = sha256_file(staged_md)
        staged.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        publish([staged_md, staged], [markdown, out])
        return report
    finally:
        staged.unlink(missing_ok=True)
        staged_md.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--parsed", type=Path, required=True)
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze_turns(args.comparison, args.parsed, args.hypothesis, args.output)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        parser.exit(2, f"Turn analysis failed: {error}\n")
    candidate = result["replay_plus_one_candidate"]
    print(json.dumps({"role": result["experiment_role"], "candidate_matches": candidate["within_error_tolerance"],
                      "candidate_examples": candidate["evaluated_changing_snapshots"],
                      "maximum_error_degrees": candidate["maximum_absolute_residual_degrees"], "training_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
