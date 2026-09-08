"""Measure synthetic relative-mouse response; never infer exact input latency.

Models map Windows relative mouse counts to observed yaw/pitch changes. Fit and
validation roles must come from the immutable plan, before observations exist.
SendInput acceptance alone is not evidence that the game consumed an input.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

from .calibration_analysis import _read_ledger, _read_object
from .calibration_buttons import _full_payload_evidence
from .calibration_pixels import _audit, _owned
from .calibration_replay_analysis import _state
from .calibration_turns import compare_input_history
from .causal_acceptance import _action_protobuf
from .io import batches, parsed_manifest, sha256_file
from .synthetic_input_plan import PROFILE, validate_synthetic_input_plan

INPUT_LEDGER_PROFILE = "cs2-windows-synthetic-input-ledger-v1"
BRIDGE_MARKER = "CHICKEN_SYNTHETIC_INPUT_BRIDGE_V1"
FROZEN_MOUSE_PLAN_SHA256 = "253dbf4ac046310815b0418d4c56285ac1de98f9e195765798611cc6a1d4df9b"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _wrapped(angle):
    return (angle + 180) % 360 - 180


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _one(rows, kind):
    values = [(index, row) for index, row in enumerate(rows) if row.get("event") == kind]
    _require(len(values) == 1, "Expected exactly one synthetic input " + kind)
    return values[0]


def verify_injection_evidence(input_rows, native_rows, native_plan, worker, plan_sha256):
    """Check plan, owned-process guards and queue insertion; never consumption.

Shared by mouse and keyboard response diagnostics. Native scheduling observation
and actual OS-call QPC brackets are retained as distinct measurements.
"""
    source_plan = validate_synthetic_input_plan({key: value for key, value in native_plan.items()
                                                if key not in ("movie_name", "demo_path")})
    header_index, header = _one(input_rows, "header")
    native_index, native_header = _one(native_rows, "header")
    ready_index, ready = _one(native_rows, "calibration_ready")
    stop_index, stop = _one(native_rows, "recording_stop_dispatched")
    complete_index, complete = _one(input_rows, "input_complete")
    pid, run_id = worker.get("owned_cs2_pid"), worker.get("run_id")
    frequency = native_header.get("qpc_frequency")
    _require(type(pid) is int and 0 < pid < 2**32 and isinstance(run_id, str) and run_id,
             "Missing owned synthetic calibration process identity")
    _require(type(frequency) is int and frequency > 0, "Missing native QPC frequency")
    _require(header_index == native_index == 0 and ready_index < stop_index,
             "Synthetic input/native lifecycle is out of order")
    _require(all(type(row.get("schema_version")) is int and row["schema_version"] == 1 for row in input_rows),
             "Unsupported synthetic input ledger schema")
    _require(header.get("profile") == INPUT_LEDGER_PROFILE and header.get("plan_sha256") == plan_sha256 and
             header.get("source_plan") == source_plan and header.get("run_id") == run_id and
             type(header.get("pid")) is int and header["pid"] == pid,
             "Synthetic input ledger plan or process provenance disagrees")
    _require(native_header.get("producer") == PROFILE and native_header.get("plan") == native_plan and
             native_header.get("synthetic_input_marker") == BRIDGE_MARKER and
             native_header.get("control_source") == "external_windows_SendInput",
             "Native ledger does not identify the synthetic input bridge")
    _require(ready.get("clock_basis") == "local_controller_tick_base_64hz" and
             type(ready.get("start_tick_base")) is int and ready["start_tick_base"] >= 0 and
             ready.get("local_connection_verified") is True,
             "Missing local native synthetic scheduling origin")
    frames = {}
    for index, row in enumerate(native_rows):
        if row.get("event") == "frame_sample" and ready_index < index < stop_index:
            frames.setdefault(row.get("qpc"), []).append(row)
    groups = []
    for event in source_plan["events"]:
        if not groups or groups[-1][0] != event["at_ms"]:
            groups.append((event["at_ms"], []))
        groups[-1][1].append(event)
    batches = [(index, row) for index, row in enumerate(input_rows) if row.get("event") == "injection_batch"]
    _require(len(batches) == len(groups), "Missing, duplicate or unexpected synthetic injection batch")
    previous_qpc, previous_elapsed = ready.get("qpc"), -1
    _require(type(previous_qpc) is int and type(stop.get("qpc")) is int, "Missing native recording QPC boundaries")
    for (index, batch), (at_ms, events) in zip(batches, groups):
        _require(header_index < index < complete_index and batch.get("events") == events and
                 type(batch.get("scheduled_at_ms")) is int and batch["scheduled_at_ms"] == at_ms,
                 "Synthetic injection order or events disagree with the immutable plan")
        elapsed, native_qpc = batch.get("actual_elapsed_ms"), batch.get("native_frame_qpc")
        _require(_number(elapsed) and max(at_ms, previous_elapsed) <= elapsed <= source_plan["duration_seconds"] * 1000 and
                 type(native_qpc) is int and len(frames.get(native_qpc, [])) == 1,
                 "Missing or early native scheduling observation")
        frame = frames[native_qpc][0]
        tick = frame.get("local_player", {}).get("controller_tick_base")
        _require(frame.get("elapsed_ms") == elapsed and type(tick) is int and
                 elapsed == (tick - ready["start_tick_base"]) * 1000 / 64 and
                 type(frame.get("owned_process_id")) is int and frame["owned_process_id"] == pid and
                 frame.get("local_connection_verified") is True,
                 "Injection scheduling observation is not the verified owned local player clock")
        receipt = batch.get("receipt", {})
        normalized = [{key: value for key, value in event.items() if key not in ("id", "at_ms")} for event in events]
        _require(receipt.get("control_source") == "windows_sendinput" and receipt.get("schema_version") == 1 and
                 receipt.get("status") == "inserted" and receipt.get("success") is True and
                 receipt.get("input_consumption_verified") is False and receipt.get("physical_device_latency_verified") is False and
                 all(type(receipt.get(key)) is int and receipt[key] == len(events) for key in ("requested_count", "inserted_count")) and
                 type(receipt.get("target_pid")) is int and receipt["target_pid"] == pid and receipt.get("run_id") == run_id and
                 receipt.get("events") == normalized and type(receipt.get("qpc_frequency")) is int and
                 receipt["qpc_frequency"] == frequency,
                 "Synthetic OS insertion receipt lacks exact event/count/clock provenance")
        before, after = receipt.get("qpc_before"), receipt.get("qpc_after")
        _require(type(before) is int and type(after) is int and
                 max(previous_qpc, native_qpc) <= before <= after <= stop["qpc"],
                 "Synthetic insertion QPC is missing, reversed or outside the recording")
        for key in ("guard_before", "guard_after"):
            guard = receipt.get(key, {})
            os_guard, scope = guard.get("os", {}), guard.get("scope", {})
            _require(all(os_guard.get(field) is True for field in
                         ("process_alive", "process_identity_verified", "foreground_matches")) and
                     type(os_guard.get("target_pid")) is int and os_guard["target_pid"] == pid and
                     type(os_guard.get("foreground_pid")) is int and os_guard["foreground_pid"] == pid and
                     type(os_guard.get("foreground_hwnd")) is int and os_guard["foreground_hwnd"] > 0 and
                     scope.get("verified") is True,
                     "Synthetic input lacks successful foreground and owned local-scope guards")
        previous_qpc, previous_elapsed = after, elapsed
    _require(type(complete.get("events_inserted")) is int and complete["events_inserted"] == len(source_plan["events"]) and
             type(complete.get("qpc")) is int and previous_qpc <= complete["qpc"] <= stop["qpc"] and
             complete.get("game_consumption_verified") is False and complete.get("training_ready") is False,
             "Synthetic input completion count/clock or consumption disclaimer disagrees")
    cleanups = [(index, row) for index, row in enumerate(input_rows) if row.get("event") == "cleanup"]
    _require(len(cleanups) == 2 and cleanups[0][1].get("reason") == "plan_complete" and
             cleanups[1][1].get("reason") == "worker_finally" and
             batches[-1][0] < cleanups[0][0] < complete_index < cleanups[1][0],
             "Synthetic cleanup lifecycle is incomplete or out of order")
    for _, row in cleanups:
        receipt = row.get("receipt", {})
        _require(receipt.get("success") is True and receipt.get("status") == "no_owned_inputs" and
                 receipt.get("events") == [] and type(receipt.get("requested_count")) is int and
                 receipt["requested_count"] == 0 and type(receipt.get("inserted_count")) is int and
                 receipt["inserted_count"] == 0 and receipt.get("target_pid") == pid and receipt.get("run_id") == run_id,
                 "Synthetic calibration ended with missing or unresolved input ownership")
    _require(not any(row.get("event") in ("input_failed", "input_error", "error") for row in input_rows),
             "Synthetic input ledger reports failure")
    return {"batches": [row for _, row in batches], "source_plan": source_plan, "ready": ready,
            "qpc_frequency": frequency, "run_id": run_id, "pid": pid,
            "queue_insertion_verified": True, "input_consumption_verified": False}


def _direction(case):
    x, y = case["dx"], case["dy"]
    if x and not y:
        return "x_positive" if x > 0 else "x_negative"
    if y and not x:
        return "y_positive" if y > 0 else "y_negative"
    return "diagonal" if x or y else "zero"


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(component) for component in value)


def measure_response(case, before, after, window, *, plateau_tolerance_degrees=0.02,
                     pitch_exclusion_degrees=85.0, maximum_stationary_velocity=1.0):
    """Compare declared pre/post windows of native original-camera snapshots.

The caller selects fixed windows from the immutable protocol, not from where a
desired response appears. Samples use calibration_replay_analysis._state's
representation. No response latency or event-to-command assignment is inferred.
"""
    if (not _number(plateau_tolerance_degrees) or plateau_tolerance_degrees <= 0 or
            not _number(pitch_exclusion_degrees) or not 0 < pitch_exclusion_degrees < 90 or
            not _number(maximum_stationary_velocity) or maximum_stationary_velocity < 0):
        raise ValueError("Invalid fixed mouse response exclusion policy")
    result = {key: case[key] for key in ("id", "split", "dx", "dy")}
    reasons = set()
    result.update(status="excluded", response_degrees=None,
                  before_capture_indices=[sample.get("capture_index") for sample in before],
                  after_capture_indices=[sample.get("capture_index") for sample in after],
                  plateau_tolerance_degrees=plateau_tolerance_degrees,
                  pitch_exclusion_degrees=pitch_exclusion_degrees,
                  observation_basis="fixed_original_camera_plateaus_with_pawn_eye_agreement",
                  shot_exclusion_basis="unchanged_observed_last_shot_ammo_weapon; aim_punch_not_directly_measured")
    if min(len(before), len(after)) < 3:
        reasons.add("insufficient_pre_or_post_plateau_samples")
    if not window:
        reasons.add("missing_response_window")
    if reasons:
        return {**result, "reasons": sorted(reasons)}
    indices = [sample.get("capture_index") for sample in window]
    qpcs = [sample.get("qpc_before") for sample in window]
    ticks = [sample.get("controller_tick_base") for sample in window]
    if (any(type(value) is not int or value < 0 for value in indices + qpcs + ticks) or
            any(b != a + 1 for a, b in zip(indices, indices[1:])) or
            any(b <= a for a, b in zip(qpcs, qpcs[1:])) or
            any(b < a for a, b in zip(ticks, ticks[1:]))):
        reasons.add("noncontiguous_capture_or_invalid_clock")
    if (any(sample not in window for sample in before + after) or
            before != window[:len(before)] or after != window[-len(after):] or
            len(before) + len(after) > len(window)):
        reasons.add("plateau_windows_not_distinct_ordered_endpoints")
    for samples in (before, after):
        plateau_ticks = [sample.get("controller_tick_base") for sample in samples]
        if len(set(value for value in plateau_ticks if type(value) is int)) < 3:
            reasons.add("plateau_lacks_observed_simulation_progress")
    identities = [(sample.get("steam_id"), sample.get("player_identity", {}).get("pawn_handle"),
                   sample.get("player_identity", {}).get("controller_handle")) for sample in window]
    if (any(type(steam) is not str or not steam.isdigit() or int(steam) <= 0 or
            type(pawn) is not int or not 0 < pawn < 2**32 - 1 or
            type(controller) is not int or not 0 < controller < 2**32 - 1
            for steam, pawn, controller in identities) or len(set(identities)) != 1):
        reasons.add("player_identity_unavailable_or_changed")
    for sample in window:
        pawn = sample.get("pawn_state", {})
        camera = sample.get("rendered_camera", {}).get("angles")
        eyes, velocity = pawn.get("eye_angles"), pawn.get("velocity")
        if (type(sample.get("player_identity", {}).get("life_state")) is not int or
                sample["player_identity"]["life_state"] != 0 or type(pawn.get("health")) is not int or pawn["health"] <= 0):
            reasons.add("player_not_observed_alive")
        if not _vector(camera) or not _vector(eyes):
            reasons.add("camera_or_eye_angles_unavailable")
        elif abs(camera[0]) >= pitch_exclusion_degrees or abs(eyes[0]) >= pitch_exclusion_degrees:
            reasons.add("pitch_near_clamp")
        if not _vector(velocity):
            reasons.add("stationarity_unobserved")
        elif math.sqrt(sum(component ** 2 for component in velocity)) > maximum_stationary_velocity:
            reasons.add("player_moving_during_mouse_probe")
    for key in ("last_shot_time", "ammo_clip", "active_weapon_handle"):
        values = [sample.get("pawn_state", {}).get(key) for sample in window]
        if any(not _number(value) for value in values):
            reasons.add("shot_or_weapon_state_unavailable")
        elif len(set(values)) != 1:
            reasons.add("shot_ammo_or_weapon_changed")
    if reasons:
        return {**result, "reasons": sorted(reasons)}

    plateaus = {}
    for source in ("camera", "eyes"):
        summaries = []
        for samples in (before, after):
            angles = [(sample["rendered_camera"]["angles"] if source == "camera" else
                       sample["pawn_state"]["eye_angles"]) for sample in samples]
            pitches = [value[0] for value in angles]
            yaw_origin = angles[0][1]
            yaws = [yaw_origin + _wrapped(value[1] - yaw_origin) for value in angles]
            spreads = [max(pitches) - min(pitches), max(yaws) - min(yaws)]
            if max(spreads) > plateau_tolerance_degrees:
                reasons.add("unstable_" + source + "_plateau")
            summaries.append({"pitch": statistics.median(pitches), "yaw": _wrapped(statistics.median(yaws)),
                              "pitch_yaw_spread_degrees": spreads})
        pitch = summaries[1]["pitch"] - summaries[0]["pitch"]
        yaw = _wrapped(summaries[1]["yaw"] - summaries[0]["yaw"])
        if abs(yaw) >= 180 - plateau_tolerance_degrees:
            reasons.add("ambiguous_half_turn")
        plateaus[source] = {"before": summaries[0], "after": summaries[1], "response_degrees": {"yaw": yaw, "pitch": pitch}}
    if any(abs(plateaus["camera"]["response_degrees"][axis] - plateaus["eyes"]["response_degrees"][axis]) >
           plateau_tolerance_degrees for axis in ("yaw", "pitch")):
        reasons.add("camera_and_pawn_eye_response_disagree")
    return {**result, "status": "excluded" if reasons else "measured", "reasons": sorted(reasons),
            "response_degrees": None if reasons else plateaus["camera"]["response_degrees"],
            "plateaus": plateaus, "steam_id": identities[0][0], "pawn_handle": identities[0][1]}


def fit_gain_matrix(cases, *, absolute_tolerance_degrees=0.05, relative_tolerance=0.05):
    """Fit only declared derivation cases, then predict untouched held-out cases.

Cases with exclusions cannot contribute to either fit or successful validation.
The zero-intercept model preserves a no-input/no-angle-change interpretation;
zero-input controls, if supplied, test that assumption independently.
"""
    if (not _number(absolute_tolerance_degrees) or absolute_tolerance_degrees <= 0 or
            not _number(relative_tolerance) or not 0 <= relative_tolerance < 1):
        raise ValueError("Invalid frozen angular validation tolerance")
    identifiers = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in identifiers or
                case.get("split") not in ("fit", "validation") or
                any(type(case.get(key)) is not int for key in ("dx", "dy"))):
            raise ValueError("Invalid or duplicate mouse calibration case")
        identifiers.add(case["id"])
        response = case.get("response_degrees")
        if case.get("status") == "measured" and (not isinstance(response, dict) or
                not all(_number(response.get(key)) for key in ("yaw", "pitch"))):
            raise ValueError("Measured calibration case lacks finite angular response")
    fit = [case for case in cases if case.get("status") == "measured" and case["split"] == "fit"]
    held = [case for case in cases if case.get("status") == "measured" and case["split"] == "validation"]
    required_directions = {"x_positive", "x_negative", "y_positive", "y_negative"}
    fit_directions, held_directions = {_direction(case) for case in fit}, {_direction(case) for case in held}
    issues = []
    if not required_directions <= fit_directions:
        issues.append("fit_missing_isolated_axis_or_sign")
    if not required_directions <= held_directions:
        issues.append("validation_missing_isolated_axis_or_sign")
    excluded = [case["id"] for case in cases if case.get("status") != "measured"]
    if excluded:
        issues.append("planned_cases_excluded_or_unobserved")
    xx = sum(case["dx"] ** 2 for case in fit)
    xy = sum(case["dx"] * case["dy"] for case in fit)
    yy = sum(case["dy"] ** 2 for case in fit)
    determinant = xx * yy - xy * xy
    matrix, inverse = None, None
    if xx == 0 or yy == 0 or determinant <= 1e-8 * xx * yy:
        issues.append("input_design_rank_deficient_or_ill_conditioned")
    else:
        matrix = []
        for axis in ("yaw", "pitch"):
            xr = sum(case["dx"] * case["response_degrees"][axis] for case in fit)
            yr = sum(case["dy"] * case["response_degrees"][axis] for case in fit)
            matrix.append([(yy * xr - xy * yr) / determinant, (xx * yr - xy * xr) / determinant])
        gain_determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
        row_norms = [math.hypot(*row) for row in matrix]
        if min(row_norms) < 1e-6 or abs(gain_determinant) <= 1e-6 * row_norms[0] * row_norms[1]:
            issues.append("observed_gain_zero_or_noninvertible")
        else:
            inverse = [[matrix[1][1] / gain_determinant, -matrix[0][1] / gain_determinant],
                       [-matrix[1][0] / gain_determinant, matrix[0][0] / gain_determinant]]
    evaluations = []
    if matrix is not None:
        for case in fit + held:
            predictions = [row[0] * case["dx"] + row[1] * case["dy"] for row in matrix]
            errors = [case["response_degrees"][axis] - predicted
                      for axis, predicted in zip(("yaw", "pitch"), predictions)]
            tolerances = [absolute_tolerance_degrees + relative_tolerance * abs(value) for value in predictions]
            evaluations.append({"id": case["id"], "split": case["split"], "dx": case["dx"], "dy": case["dy"],
                                "predicted_yaw_pitch_degrees": predictions, "residual_yaw_pitch_degrees": errors,
                                "tolerance_yaw_pitch_degrees": tolerances,
                                "within_frozen_tolerance": all(abs(error) <= tolerance
                                    for error, tolerance in zip(errors, tolerances))})
        if any(not row["within_frozen_tolerance"] for row in evaluations if row["split"] == "fit"):
            issues.append("fit_residual_exceeds_frozen_tolerance")
        if any(not row["within_frozen_tolerance"] for row in evaluations if row["split"] == "validation"):
            issues.append("held_out_sign_or_magnitude_response_disagrees")
    magnitudes = {}
    for axis, other in (("dx", "dy"), ("dy", "dx")):
        fitted = sorted({abs(case[axis]) for case in fit if case[axis] and not case[other]})
        tested = sorted({abs(case[axis]) for case in held if case[axis] and not case[other]})
        novel = sorted(set(tested) - set(fitted))
        magnitudes[axis] = {"fit": fitted, "held_out": tested, "novel_held_out": novel,
                            "tested_below_fit_range": bool(fitted and tested and min(tested) < min(fitted)),
                            "tested_above_fit_range": bool(fitted and tested and max(tested) > max(fitted))}
        if not novel:
            issues.append("validation_has_no_new_magnitude_" + axis)
    return {"status": "held_out_gain_validated_for_tested_cases" if not issues else "incomplete_or_failed_calibration",
            "model": "zero_intercept_2x2_least_squares", "output_axes": ["yaw_degrees", "pitch_degrees"],
            "input_axes": ["relative_mouse_dx", "relative_mouse_dy"],
            "degrees_per_mouse_count": matrix, "mouse_counts_per_degree": inverse,
            "fit_case_ids": [case["id"] for case in fit], "validation_case_ids": [case["id"] for case in held],
            "excluded_case_ids": excluded, "held_out_refitting_performed": False,
            "absolute_tolerance_degrees": absolute_tolerance_degrees, "relative_tolerance": relative_tolerance,
            "axis_magnitude_coverage": magnitudes, "evaluations": evaluations, "issues": issues,
            "exact_input_timing_verified": False, "training_ready": False, "live_control_ready": False}


def command_window_diagnostics(commands, before, after, dx, dy):
    """Retain raw commands in observed processed-number boundaries, not a delivery assignment."""
    start = before.get("pawn_state", {}).get("movement_last_command_number_processed")
    end = after.get("pawn_state", {}).get("movement_last_command_number_processed")
    result = {"association": "native_observed_processed_command_number_neighborhood_not_consumption_time",
              "before_last_processed_command": start, "after_last_processed_command": end,
              "input_consumption_verified": False, "status": "unavailable", "commands": []}
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        return {**result, "reason": "native_processed_command_boundaries_unavailable"}
    player = before.get("steam_id")
    selected = [row for row in commands if str(row.get("steam_id")) == player and
                type(row.get("command_number")) is int and start < row["command_number"] <= end]
    numbers = [row["command_number"] for row in selected]
    if numbers != list(range(start + 1, end + 1)):
        return {**result, "reason": "canonical_processed_number_window_not_unique_and_contiguous"}
    reasons, records = Counter(), []
    for row in selected:
        projected, _ = _action_protobuf(row)
        reasons.update(projected)
        raw = {key: row.get(key) for key in ("command_row_id", "command_number", "demo_tick", "client_tick",
                "server_tick_executed", "steam_id", "player_slot", "pawn_entity_handle", "base_present",
                "mousedx_raw", "mousedy_raw", "view_yaw", "view_pitch", "buttonstate1", "buttonstate2", "buttonstate3",
                "subtick_moves", "input_history")}
        raw["command_protobuf_sha256"] = hashlib.sha256(row["command_protobuf"]).hexdigest() if isinstance(row.get("command_protobuf"), bytes) else None
        records.append(raw)
    result.update(status="measured_raw_command_neighborhood", commands=records, projection_reason_counts=dict(reasons))
    sums = {}
    for field, injected in (("mousedx_raw", dx), ("mousedy_raw", dy)):
        values = [row.get(field) for row in selected]
        known = not reasons and all(row.get("base_present") is True and
                                   (row.get(field) is None or type(row[field]) is int) for row in selected)
        total = sum(0 if value is None else value for value in values) if known else None
        sums[field] = {"raw_present_count": sum(value is not None for value in values),
                       "raw_absent_count": sum(value is None for value in values),
                       "effective_sum_with_known_base_protobuf_defaults": total,
                       "injected_count": injected, "sum_matches_injected_count": total == injected if known else None}
    result["mouse_count_sums"] = sums
    result["sum_comparison_is_exact_event_assignment"] = False
    return result


def _mouse_cases(states, evidence):
    cases = []
    tick_origin, frequency = evidence["ready"]["start_tick_base"], evidence["qpc_frequency"]
    injections = evidence["batches"]
    timed = [(state, (state["controller_tick_base"] - tick_origin) * 1000 / 64) for state in states]
    for batch in injections:
        event = batch["events"][0]
        case = {"id": event["id"], "split": "fit" if event["id"].startswith("fit-") else "validation",
                "dx": event["dx"], "dy": event["dy"]}
        center = batch["actual_elapsed_ms"]
        before = [state for state, elapsed in timed if center - 250 <= elapsed <= center - 62.5]
        after = [state for state, elapsed in timed if center + 250 <= elapsed <= center + 437.5]
        window = [state for state, elapsed in timed if center - 250 <= elapsed <= center + 437.5]
        measured = measure_response(case, before, after, window)
        reasons = set(measured["reasons"])
        receipt = batch["receipt"]
        if before and after:
            # The complete pre plateau predates insertion, and the complete post
            # plateau follows insertion. A delayed call cannot masquerade as a
            # successful zero or gain response merely by its scheduled time.
            completions = []
            for state in before:
                endpoints = state.get("qpc_after"), state.get("pixel_readback_qpc_after")
                if any(type(value) is not int or value < state["qpc_before"] for value in endpoints):
                    reasons.add("pre_plateau_capture_completion_unavailable")
                else:
                    completions.append(max(endpoints))
            if (len(completions) == len(before) and max(completions) >= receipt["qpc_before"] or
                    not receipt["qpc_before"] <= receipt["qpc_after"] < after[0]["qpc_before"]):
                reasons.add("insertion_not_between_fixed_pre_and_post_plateaus")
            for other in injections:
                if other is batch:
                    continue
                other_receipt = other["receipt"]
                if (other_receipt["qpc_before"] <= after[-1]["qpc_before"] and
                        other_receipt["qpc_after"] >= before[0]["qpc_before"]):
                    reasons.add("another_injection_overlaps_response_window")
        measured.update(status="excluded" if reasons else "measured", reasons=sorted(reasons),
                        response_degrees=None if reasons else measured["response_degrees"],
                        scheduled_at_ms=batch["scheduled_at_ms"], scheduling_observation_elapsed_ms=center,
                        native_scheduling_frame_qpc=batch["native_frame_qpc"],
                        insertion_qpc_before=receipt["qpc_before"], insertion_qpc_after=receipt["qpc_after"],
                        pre_plateau_latest_capture_completion_qpc=max(completions) if before and after and len(completions) == len(before) else None,
                        native_observation_to_insertion_call_ms=(receipt["qpc_before"] - batch["native_frame_qpc"]) * 1000 / frequency,
                        fixed_windows_relative_to_scheduling_observation_ms={"before": [-250, -62.5], "after": [250, 437.5]},
                        exact_event_consumption_time_verified=False)
        cases.append((measured, before, after))
    return cases


def analyze_synthetic_input(run_dir, parsed, out):
    """Write a fresh JSON/Markdown mouse calibration with original pixel and demo binding."""
    run, parsed, out = Path(run_dir).resolve(), Path(parsed).resolve(), Path(out).resolve()
    markdown = out.with_suffix(".md")
    _require(out.suffix.lower() == ".json" and not out.exists() and not markdown.exists() and
             not out.is_relative_to(run) and not out.is_relative_to(parsed),
             "Mouse analysis needs fresh .json/.md outputs outside source evidence")
    paths = {name: _owned(run, name) for name in ("native-plan.json", "input_ledger.jsonl", "calibration.json",
                                                "calibration_ledger.jsonl", "capture_ledger.jsonl", "controlled.dem")}
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    frozen_path = Path(__file__).resolve().parents[2] / "tools/renderer/plans/synthetic-mouse-probe-012-v1.json"
    _require(sha256_file(frozen_path) == FROZEN_MOUSE_PLAN_SHA256, "Preregistered mouse protocol hash changed")
    expected_plan = _read_object(frozen_path)
    plan, worker = _read_object(paths["native-plan.json"]), _read_object(paths["calibration.json"])
    _require(worker.get("input_ledger", {}).get("path") == "input_ledger.jsonl" and
             worker["input_ledger"].get("sha256") == hashes["input_ledger.jsonl"],
             "Worker synthetic input ledger hash/path mismatch")
    native, inputs = _read_ledger(paths["calibration_ledger.jsonl"]), _read_ledger(paths["input_ledger.jsonl"])
    evidence = verify_injection_evidence(inputs, native, plan, worker, hashes["native-plan.json"])
    _require(evidence["source_plan"] == expected_plan, "Run does not match preregistered mouse derivation/held-out protocol")
    pixels = _audit(run)
    _require(pixels["status"] == "all_archived_pixels_match_readback", "Mouse response requires matching original captured pixels")
    captures = _read_ledger(paths["capture_ledger.jsonl"])
    readback_endpoints = {row["submission_candidate"]["capture_index"]: row.get("qpc_after")
                          for row in captures if row["event"] == "pixel_readback"}
    # _audit has already bound each readback to its unique original movie/TGA.
    # Keep both completion clocks: pixel copying can finish after movie submit.
    states = [{**_state(row, True), "qpc_after": row.get("qpc_after"),
               "pixel_readback_qpc_after": readback_endpoints.get(row["capture_index"])}
              for row in captures if row["event"] == "movie_frame"]
    manifest = parsed_manifest(parsed, ("usercmd.parquet",))
    _require(manifest.get("demo_id") == hashes["controlled.dem"] and manifest.get("tick_rate") == 64,
             "Mouse commands do not identify this original 64Hz recording")
    parsed_hashes = {name: sha256_file(parsed / name) for name in ("manifest.json", "usercmd.parquet")}
    commands = []
    for batch in batches(parsed / "usercmd.parquet"):
        commands.extend(batch)
        _require(len(commands) <= 200000, "Mouse diagnostic command count exceeds bounded scope")
    wire = _full_payload_evidence(paths["controlled.dem"], commands)
    _require(wire["standalone_full_payload_bytes_match"], "Mouse analysis requires original demo-bound full UserCmd payloads")
    cases = []
    for measured, before, after in _mouse_cases(states, evidence):
        measured["recorded_command_diagnostics"] = (command_window_diagnostics(commands, before[-1], after[-1],
                                                     measured["dx"], measured["dy"]) if before and after else
                                                     {"status": "unavailable", "reason": "missing_plateau_boundaries"})
        cases.append(measured)
    matrix = fit_gain_matrix(cases)
    history = compare_input_history(states, commands)
    issues = list(matrix["issues"])
    if any(case["recorded_command_diagnostics"].get("status") != "measured_raw_command_neighborhood" or
           case["recorded_command_diagnostics"].get("projection_reason_counts") for case in cases):
        issues.append("recorded_command_neighborhood_unavailable_or_projection_invalid")
    if history["camera_angle_matches"] != len(states):
        issues.append("not_all_original_cameras_match_unique_recorded_history_angles")
    report = {"schema_version": 1, "profile": "synthetic-relative-mouse-response-calibration-v1",
              "status": "bounded_mouse_gain_calibrated" if not issues else "incomplete_or_failed_mouse_calibration",
              "run_dir": str(run), "parsed_dir": str(parsed), "source_hashes": hashes, "parsed_hashes": parsed_hashes,
              "frozen_plan_sha256": FROZEN_MOUSE_PLAN_SHA256, "analyzer_source_sha256": sha256_file(Path(__file__)),
              "queue_insertion_verified": True, "input_consumption_verified": False,
              "physical_device_latency_verified": False, "exact_input_timing_verified": False,
              "training_ready": False, "live_control_ready": False, "gain_matrix": matrix, "cases": cases,
              "source_pixel_audit": pixels, "original_demo_command_binding": wire, "input_history": history,
              "issues": issues, "limits": [
                  "This measures SendInput relative-count response under this recorded game/settings profile, not physical mouse latency.",
                  "Scheduling observations and insertion QPC brackets do not locate engine input consumption or subtick delivery.",
                  "Fixed pre/post windows are selected before observing the response; excluded cases are never fitted.",
                  "Fit and held-out cases belong to one preregistered run; an independent repeat is still needed for broader validation.",
                  "Pitch near clamp, shots, weapon changes, movement and unstable camera/eye plateaus are excluded; aim punch is not directly measured.",
                  "Raw nullable mouse/history/subtick values remain evidence; command-window sums are not an exact per-event assignment.",
                  "The calibrated matrix is bounded to tested counts and the recorded settings/build; it does not authorize training or live control."]}
    _require(all(sha256_file(path) == hashes[name] for name, path in paths.items()) and
             all(sha256_file(parsed / name) == digest for name, digest in parsed_hashes.items()),
             "Mouse calibration source evidence changed during analysis")
    for frame in pixels["frames"]:
        _require(sha256_file(_owned(run, "frames/" + frame["tga_filename"])) == frame["file_sha256"],
                 "Original mouse response pixels changed during analysis")
    out.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("x", encoding="utf-8") as handle:
        handle.write(_markdown(report))
    report["markdown_sha256"] = sha256_file(markdown)
    with out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def _markdown(report):
    lines = ["# Synthetic mouse response calibration", "", f"Status: `{report['status']}`.", "",
             "Rows of the gain matrix are yaw/pitch degrees; columns are injected relative dx/dy counts.", "",
             "```json", json.dumps(report["gain_matrix"]["degrees_per_mouse_count"], indent=2), "```", "",
             "The matrix uses only the four preregistered fit cases. Eight held-out impulses test both signs at smaller and larger magnitudes.", "",
             "| Case | dx | dy | Response yaw/pitch (degrees) | Result |", "| --- | ---: | ---: | --- | --- |"]
    for case in report["cases"]:
        response = case.get("response_degrees")
        angles = "unavailable" if response is None else f"{response['yaw']:.6f} / {response['pitch']:.6f}"
        result = ", ".join(case["reasons"]) if case["reasons"] else case["status"]
        lines.append(f"| {case['id']} | {case['dx']} | {case['dy']} | {angles} | {result} |")
    lines += ["", "## Limits", ""] + ["- " + value for value in report["limits"]]
    if report["issues"]:
        lines += ["", "## Unresolved evidence", ""] + ["- " + value for value in report["issues"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--parsed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = analyze_synthetic_input(args.run_dir, args.parsed, args.output)
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as error:
        parser.exit(2, f"Synthetic mouse calibration failed: {error}\n")
    print(json.dumps({"status": report["status"], "gain_matrix": report["gain_matrix"]["degrees_per_mouse_count"],
                      "output": str(args.output), "training_ready": False}))
    return 0 if not report["issues"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
