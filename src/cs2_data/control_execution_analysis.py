"""Independent diagnostics for the frozen five-phase combined control program.

This checks compilation arithmetic, inserted batches, original UserCmd bytes,
and fixed before/after observations. It never assigns an exact consumption time
or promotes local scripted recordings into image-aligned training acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics

from .calibration_analysis import _read_ledger, _read_object
from .calibration_buttons import _full_payload_evidence
from .calibration_pixels import _audit, _owned
from .calibration_replay_analysis import _state
from .causal_acceptance import _action_protobuf
from .control_contract import BUTTONS, DECISION_PERIOD_NS
from .control_program import default_program, validate_program
from .io import batches, parsed_manifest, sha256_file
from .normalize import effective_scalar, wrap_angle
from .synthetic_input_analysis import command_window_diagnostics, verify_injection_evidence
from .synthetic_input_plan import validate_synthetic_input_plan
from .synthetic_keyboard_analysis import _player_identity

PROFILE = "cs2-combined-control-execution-diagnostic-v1"
EXECUTION_PLUGIN_SHA256 = "bf429fb57f176f15da368a9a9061a0c9076293b418fa89ea8cb87b5cb0266fdd"
FROZEN_PROGRAM_CANONICAL_SHA256 = "cfe431aab0e1e466aa9e2a39ab5a15f0f8986e17f361144b26eab7da3bc973a6"
PHASES = ((0, "forward", 8), (48, "left", 512), (96, "back", 16),
          (144, "right", 1024), (192, "crouch", 4))
CONTROLS = {"forward": "W", "left": "A", "back": "S", "right": "D", "crouch": "LCTRL"}
PROTOCOL = {"profile": PROFILE, "pre_window_ms": [-250, -62.5], "post_window_ms": [250, 437.5],
    "phase_decisions": 16, "minimum_plateau_samples": 3, "plateau_tolerance_degrees": .02,
    "absolute_angle_tolerance_degrees": .05, "relative_angle_tolerance": .05,
    "pitch_exclusion_degrees": 85, "movement_displacement_min": .25, "movement_speed_min": 1,
    "duck_amount_increase_min": .05, "windows_are_relative_to": "requested_program_phase_boundaries"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _same(actual, expected, name):
    _require(_json(actual) == _json(expected), "Compilation disagrees with independent " + name)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)


def _product(matrix, vector):
    return [math.fsum(row[i] * vector[i] for i in range(2)) for row in matrix]


def _round(value):
    magnitude = abs(value)
    integer = (magnitude.numerator * 2 + magnitude.denominator) // (2 * magnitude.denominator)
    return -integer if value < 0 else integer


def reverify_compilation_profile(recorded, output):
    """Paths in serialized provenance locate sources; fresh audits supply trust."""
    from .control_label_audit import load_measured_control_profile
    provenance = recorded["provenance"]
    inputs = provenance.get("calibration_inputs", {})
    names = ("mouse_run", "mouse_parsed", "keyboard_run", "keyboard_parsed", "keyboard_protocol")
    _require(set(inputs) == set(names), "Compilation lacks original calibration source pointers")
    for path, digest in provenance["source_files"].items():
        _require(sha256_file(Path(path)) == digest, "Compiled calibration source changed: " + path)
    fresh = load_measured_control_profile(*(inputs[name] for name in names), evidence_output=output)
    measured = fresh.to_dict()
    for name in ("profile", "domain", "degrees_per_mouse_count", "mouse_counts_per_degree", "binary_profile",
                 "sensitivity", "supported_button_masks", "ready_requirements"):
        _same(recorded[name], measured[name], "freshly remeasured profile " + name)
    for name in ("bound_demo_ids", "canonical_command_counts", "configuration_evidence", "calibration_plugin_sha256",
                 "mouse_frozen_plan_sha256", "keyboard_frozen_protocol", "implementation_sha256", "calibration_inputs"):
        _same(provenance[name], measured["provenance"][name], "fresh calibration provenance " + name)
    return fresh


def audit_compilation(program, compiled, plan, projection):
    """Recompute counts/carry/events without using ControlExecutor or project_events."""
    validate_program(program)
    _same(program, default_program(), "frozen five-phase program")
    _require(_digest(program) == FROZEN_PROGRAM_CANONICAL_SHA256, "Program differs from the frozen pre-capture canonical digest")
    validate_synthetic_input_plan(plan)
    profile = compiled["calibration_profile"]
    _same(compiled["calibration_profile_sha256"], _digest(profile), "profile digest")
    _same(profile["provenance_sha256"], _digest(profile["provenance"]), "profile provenance digest")
    forward, inverse = profile["degrees_per_mouse_count"], profile["mouse_counts_per_degree"]
    for matrix in (forward, inverse):
        _require(isinstance(matrix, list) and len(matrix) == 2 and all(isinstance(row, list) and
            len(row) == 2 and all(_number(v) for v in row) for row in matrix), "Invalid calibrated matrix")
    for i in range(2):
        for j in range(2):
            _require(math.isclose(math.fsum(forward[i][k] * inverse[k][j] for k in range(2)),
                float(i == j), abs_tol=1e-8, rel_tol=1e-8), "Calibrated matrices disagree")
    _same(compiled["decision_count"], len(program["actions"]), "decision count")
    _same(compiled.get("profile"), "cs2-bounded-control-executor-v1", "compiler profile")
    for flag in ("training_ready", "live_control_ready", "exact_input_timing_verified", "combined_aim_movement_calibrated_by_compiler", "calibration_sources_reverified_by_compiler"):
        _same(compiled.get(flag), False, "compiler readiness limit")
    _require(len(compiled["decisions"]) == len(program["actions"]), "Incomplete decision compilation")
    ideal_totals, emitted_totals = [Fraction(0), Fraction(0)], [0, 0]
    held, events, predictions = dict.fromkeys(BUTTONS, False), [], []
    for index, (action, recorded) in enumerate(zip(program["actions"], compiled["decisions"])):
        _same(action["buttons_held_at_start"], held, "held continuity")
        desired = [float(action["angular_delta_deg"][axis]) for axis in ("yaw", "pitch")]
        ideal = _product(inverse, desired)
        previous_residual = [float(a - b) for a, b in zip(ideal_totals, emitted_totals)]
        ideal_totals = [a + Fraction(b) for a, b in zip(ideal_totals, ideal)]
        rounded = [_round(value) for value in ideal_totals]
        emitted = [a - b for a, b in zip(rounded, emitted_totals)]
        emitted_totals = rounded
        local = ([{"offset_ns": 0, "event": {"kind": "mouse_move", "dx": emitted[0], "dy": emitted[1]}}]
                 if any(emitted) else [])
        for edge in action["button_events"]:
            _require(edge["button"] in CONTROLS and edge["offset_ns"] == 0, "Unsupported frozen phase control")
            held[edge["button"]] = edge["pressed"]
            local.append({"offset_ns": 0, "event": {"kind": "key", "key": CONTROLS[edge["button"]], "pressed": edge["pressed"]}})
        predicted = dict(zip(("yaw", "pitch"), _product(forward, emitted)))
        expected = {"action": action, "action_sha256": _digest(action), "decision_index": index,
            "start_offset_ns": index * DECISION_PERIOD_NS, "end_offset_ns": (index + 1) * DECISION_PERIOD_NS,
            "decision_period_ns": DECISION_PERIOD_NS, "ideal_mouse_counts": ideal,
            "emitted_mouse_counts": emitted, "mouse_count_residual_before": previous_residual,
            "mouse_count_residual_after": [float(a - b) for a, b in zip(ideal_totals, rounded)],
            "predicted_emitted_angular_delta_deg": predicted, "events": local, "held_after": held,
            "calibration_profile_sha256": compiled["calibration_profile_sha256"],
            "calibration_provenance_sha256": profile["provenance_sha256"],
            "rounding_policy": "cumulative_nearest_integer_half_away_from_zero"}
        for key, value in expected.items():
            _same(recorded.get(key), value, "decision " + key)
        for local_index, item in enumerate(local):
            events.append({"offset_ns": index * DECISION_PERIOD_NS, "decision_index": index,
                "decision_event_index": local_index, "decision_offset_ns": 0, "event": item["event"]})
        predictions.append(predicted)
    _same(compiled["events"], events, "flattened events")
    _same(compiled["event_count"], len(events), "event count")
    _same(compiled["duration_ns"], len(program["actions"]) * DECISION_PERIOD_NS, "duration")
    _same(compiled["final_state"], {"next_decision_index": len(program["actions"]),
        "next_offset_ns": len(program["actions"]) * DECISION_PERIOD_NS, "held": held, "pressed_since_ns": {},
        "ideal_mouse_count_totals": [float(v) for v in ideal_totals], "emitted_mouse_count_totals": emitted_totals,
        "mouse_count_residual": [float(a-b) for a, b in zip(ideal_totals, emitted_totals)]}, "final carry/state")
    absolute = sum(abs(e["event"]["dx"]) + abs(e["event"]["dy"]) for e in events if e["event"]["kind"] == "mouse_move")
    _same(compiled["total_absolute_mouse_counts"], absolute, "absolute count budget")
    projected, proofs = [], []
    for index, item in enumerate(events):
        offset = item["offset_ns"]
        exact = program["start_at_ns"] + offset
        at_ms = exact // 1_000_000
        name = f"decision-{item['decision_index']:04d}-event-{index:04d}"
        projected.append({"id": name, "at_ms": at_ms, **item["event"]})
        proofs.append({"id": name, "decision_index": item["decision_index"], "decision_start_ns": offset,
            "sequence_offset_ns": offset, "requested_capture_offset_ns": exact, "projected_at_ms": at_ms,
            "encoded_threshold_delta_ns": at_ms * 1_000_000 - exact,
            "threshold_equivalent_on_native_64hz_grid": True})
        native_threshold_ns = ((at_ms * 1_000_000 + 15_625_000 - 1) // 15_625_000) * 15_625_000
        _require(exact % DECISION_PERIOD_NS == 0 and native_threshold_ns == exact,
            "Projected threshold changes its requested native tick boundary")
    _same(plan, {"schema_version": 1, "producer": "cs2-synthetic-input-calibration-plan-v1", "map": "de_dust2",
        "fps": 32, "width": 1280, "height": 720, "duration_seconds": program["duration_seconds"], "events": projected}, "synthetic plan")
    _same(projection, proofs, "boundary projection")
    phases = []
    for start, control, mask in PHASES:
        end = start + PROTOCOL["phase_decisions"]
        phase_events = [event for event, proof in zip(projected, proofs) if start <= proof["decision_index"] <= end]
        counts = [sum(e[field] for e in phase_events if e["kind"] == "mouse_move") for field in ("dx", "dy")]
        phases.append({"id": control, "control_mask": mask, "start_decision": start, "end_decision": end,
            "start_ms": (program["start_at_ns"] + start * DECISION_PERIOD_NS) / 1e6,
            "end_ms": (program["start_at_ns"] + end * DECISION_PERIOD_NS) / 1e6,
            "event_ids": [e["id"] for e in phase_events], "emitted_mouse_counts": counts,
            "requested_yaw_pitch_degrees": [math.fsum(program["actions"][i]["angular_delta_deg"][axis] for i in range(start, end)) for axis in ("yaw", "pitch")],
            "predicted_emitted_yaw_pitch_degrees": _product(forward, counts)})
    return {"status": "independent_compilation_and_projection_agree", "decision_count": len(program["actions"]),
        "event_count": len(events), "total_absolute_mouse_counts": absolute, "phases": phases,
        "calibration_profile_provenance_sha256": profile["provenance_sha256"]}


def _angular_plateaus(before, after):
    reasons, result = set(), {}
    for source in ("camera", "eyes"):
        summaries = []
        for group in (before, after):
            angles = [s["rendered_camera"].get("angles") if source == "camera" else s["pawn_state"].get("eye_angles") for s in group]
            if not all(_vector(v) for v in angles):
                reasons.add("missing_" + source + "_angles")
                break
            if any(abs(v[0]) >= PROTOCOL["pitch_exclusion_degrees"] for v in angles):
                reasons.add("pitch_near_clamp")
            pitches = [v[0] for v in angles]
            yaws = [angles[0][1] + wrap_angle(v[1] - angles[0][1]) for v in angles]
            spread = [max(yaws) - min(yaws), max(pitches) - min(pitches)]
            if max(spread) > PROTOCOL["plateau_tolerance_degrees"]:
                reasons.add("unstable_" + source + "_plateau")
            summaries.append({"yaw": wrap_angle(statistics.median(yaws)), "pitch": statistics.median(pitches), "yaw_pitch_spread_degrees": spread})
        if len(summaries) == 2:
            result[source] = {"before": summaries[0], "after": summaries[1], "response_yaw_pitch_degrees": [
                wrap_angle(summaries[1]["yaw"] - summaries[0]["yaw"]), summaries[1]["pitch"] - summaries[0]["pitch"]]}
    if len(result) == 2 and any(abs(a-b) > PROTOCOL["plateau_tolerance_degrees"] for a, b in
        zip(result["camera"]["response_yaw_pitch_degrees"], result["eyes"]["response_yaw_pitch_degrees"])):
        reasons.add("camera_and_eye_responses_disagree")
    return result, reasons


def _latest_native_before(batch, frames, commands):
    """Retain newer FRAME_START evidence even when no newer movie has completed."""
    scheduled = [f for f in frames if f.get("qpc") == batch.get("native_frame_qpc")]
    if len(scheduled) != 1 or scheduled[0].get("elapsed_ms") != batch.get("actual_elapsed_ms"):
        return None
    origin = scheduled[0]
    identity = _player_identity(origin.get("local_player"))
    if identity is None:
        return None
    before = batch["receipt"]["qpc_before"]
    selected = sorted([f for f in frames if type(f.get("qpc")) is int and origin["qpc"] <= f["qpc"] <= before], key=lambda f: f["qpc"])
    if not selected or len({f["qpc"] for f in selected}) != len(selected) or any(_player_identity(f.get("local_player")) != identity for f in selected):
        return None
    numbers = []
    for frame in selected:
        player = frame["local_player"]
        number = player["pawn_state"].get("movement_last_command_number_processed")
        if type(number) is not int:
            return None
        hits = [c for c in commands if str(c.get("steam_id")) == identity[0] and c.get("command_number") == number]
        if len(hits) != 1 or hits[0].get("server_tick_executed") != player.get("controller_tick_base") or _action_protobuf(hits[0])[0]:
            return None
        numbers.append(number)
    if any(b < a for a, b in zip(numbers, numbers[1:])):
        return None
    last = selected[-1]
    return {"qpc": last["qpc"], "pawn_state": last["local_player"]["pawn_state"],
        "controller_tick_base": last["local_player"]["controller_tick_base"], "identity": identity}


def measure_phase(phase, states, evidence, commands):
    """Fixed observation windows; missing or contradictory effects stay visible."""
    origin = evidence["ready"]["start_tick_base"]
    timed = [(s, (s["controller_tick_base"] - origin) * 1000 / 64) for s in states]
    before = [s for s, t in timed if phase["start_ms"] - 250 <= t <= phase["start_ms"] - 62.5]
    after = [s for s, t in timed if phase["end_ms"] + 250 <= t <= phase["end_ms"] + 437.5]
    window = [s for s, t in timed if phase["start_ms"] - 250 <= t <= phase["end_ms"] + 437.5]
    response_window = [s for s, t in timed if phase["start_ms"] <= t <= phase["end_ms"] + 250]
    reasons = set()
    injections = [b for b in evidence["batches"] if any(e["id"] in phase["event_ids"] for e in b["events"])]
    result = {**phase, "before_capture_indices": [s["capture_index"] for s in before],
        "after_capture_indices": [s["capture_index"] for s in after], "insertion_batch_count": len(injections),
        "exact_event_consumption_time_verified": False}
    if min(len(before), len(after)) < 3 or not injections:
        return {**result, "status": "missing_response_evidence", "reasons": ["missing_fixed_plateau_or_insertion_evidence"]}
    for group in (before, after):
        if len(set(s["controller_tick_base"] for s in group)) < 3:
            reasons.add("plateau_lacks_unique_simulation_progress")
    for first, second in zip(window, window[1:]):
        if (second["capture_index"] != first["capture_index"] + 1 or second["qpc_before"] <= first["qpc_before"] or
            second["controller_tick_base"] < first["controller_tick_base"]):
            reasons.add("capture_or_native_clock_discontinuity")
    identity = [(s["steam_id"], s["player_identity"].get("pawn_handle"), s["player_identity"].get("controller_handle")) for s in window]
    if len(set(identity)) != 1 or any(not v[0].isdigit() or int(v[0]) <= 0 or
        any(type(x) is not int or not 0 < x < 2**32 - 1 for x in v[1:]) for v in identity):
        reasons.add("native_player_identity_missing_or_changed")
    for sample in window:
        if sample["player_identity"].get("life_state") != 0 or not _number(sample["pawn_state"].get("health")) or sample["pawn_state"]["health"] <= 0:
            reasons.add("native_player_not_alive")
    completions = []
    for sample in before:
        ends = [sample.get("qpc_after"), sample.get("pixel_readback_qpc_after")]
        if any(type(v) is not int or v < sample["qpc_before"] for v in ends):
            reasons.add("pre_plateau_completion_unavailable")
        else:
            completions.append(max(ends))
    first_insert = min(b["receipt"]["qpc_before"] for b in injections)
    last_insert = max(b["receipt"]["qpc_after"] for b in injections)
    before_press = _latest_native_before(injections[0], evidence.get("native_frames", []), commands)
    before_release = _latest_native_before(injections[-1], evidence.get("native_frames", []), commands)
    observed_before = {"press": before_press, "release": before_release}
    pre_input_numbers = {key: value.get("pawn_state", {}).get("movement_last_command_number_processed") if value else None
        for key, value in observed_before.items()}
    if any(type(v) is not int for v in pre_input_numbers.values()):
        reasons.add("pre_insertion_processed_command_boundary_unavailable")
    if any(value and value["identity"] != identity[0] for value in observed_before.values()):
        reasons.add("native_insertion_and_movie_player_identity_disagree")
    if (completions and max(completions) >= first_insert) or after[0]["qpc_before"] <= last_insert:
        reasons.add("insertions_not_between_completed_fixed_plateaus")
    for batch in evidence["batches"]:
        if batch not in injections and batch["receipt"]["qpc_before"] <= after[-1]["qpc_before"] and batch["receipt"]["qpc_after"] >= before[0]["qpc_before"]:
            reasons.add("other_phase_insertion_overlaps_response_window")
    for key in ("last_shot_time", "ammo_clip", "active_weapon_handle"):
        values = [s["pawn_state"].get(key) for s in window]
        if any(not _number(v) for v in values) or len(set(values)) != 1:
            reasons.add("shot_or_weapon_state_changed_or_unavailable")
    plateaus, angle_reasons = _angular_plateaus(before, after)
    reasons |= angle_reasons
    predicted = phase["predicted_emitted_yaw_pitch_degrees"]
    tolerances = [.05 + .05 * abs(value) for value in predicted]
    residuals = {key: [a-b for a, b in zip(value["response_yaw_pitch_degrees"], predicted)] for key, value in plateaus.items()}
    if any(abs(value) > limit for row in residuals.values() for value, limit in zip(row, tolerances)):
        reasons.add("observed_angular_response_disagrees_with_emitted_gain")
    # Native processed command numbers are checked against the unique original
    # packet-execution clock and identity. This is a neighborhood, not an OS
    # event-to-command or subtick timestamp assignment.
    raw = command_window_diagnostics(commands, before[-1], after[-1], *phase["emitted_mouse_counts"])
    start_n, end_n = raw.get("before_last_processed_command"), raw.get("after_last_processed_command")
    selected = [c for c in commands if str(c.get("steam_id")) == before[-1]["steam_id"] and
        type(c.get("command_number")) is int and type(start_n) is int and type(end_n) is int and start_n <= c["command_number"] <= end_n]
    valid_window = raw["status"] == "measured_raw_command_neighborhood" and not raw.get("projection_reason_counts") and bool(selected)
    if valid_window:
        valid_window = ([c["command_number"] for c in selected] == list(range(start_n, end_n + 1)) and
            selected[0]["server_tick_executed"] == before[-1]["controller_tick_base"] and
            selected[-1]["server_tick_executed"] == after[-1]["controller_tick_base"])
        for c in selected:
            # Native and protobuf handles have different serial encodings in
            # the measured sources. Keep each namespace stable independently;
            # Steam identity and unique N/E endpoints supply this association.
            valid_window &= (not _action_protobuf(c)[0] and c.get("alive") is True and
                type(c.get("pawn_entity_handle")) is int and 0 <= c["pawn_entity_handle"] < 2**32 - 1 and
                c["pawn_entity_handle"] == selected[0].get("pawn_entity_handle") and
                c.get("round_id") == selected[0].get("round_id") and c.get("player_slot") == selected[0].get("player_slot") and c.get("demo_id") == selected[0].get("demo_id"))
        for a, b in zip(selected, selected[1:]):
            valid_window &= b["server_tick_executed"] == a["server_tick_executed"] + 1 and b["demo_tick"] == a["demo_tick"] + 1
    if not valid_window:
        reasons.add("original_command_identity_or_clock_window_unavailable")
    if not all(item.get("sum_matches_injected_count") is True for item in raw.get("mouse_count_sums", {}).values()) or "mouse_count_sums" not in raw:
        reasons.add("recorded_mouse_counts_missing_or_disagree")
    presses, releases, earlier_changes, raw_steps = [], [], [], []
    recorded_angles = None
    mask = phase["control_mask"]
    if valid_window:
        for retained, original in zip(raw["commands"], selected[1:]):
            retained["buttons_present"] = original.get("buttons_present")
            retained["viewangles_present"] = original.get("viewangles_present")
        angles = [[effective_scalar(c, "view_" + axis, "viewangles_present") for axis in ("yaw", "pitch")] for c in selected]
        if all(c.get("viewangles_present") is True for c in selected) and all(_number(v) for pair in angles for v in pair):
            deltas = [[wrap_angle(b[0]-a[0]), b[1]-a[1]] for a, b in zip(angles, angles[1:])]
            if any(abs(d[0]) == 180 for d in deltas):
                reasons.add("recorded_angular_response_has_ambiguous_half_turn")
            else:
                recorded_angles = [math.fsum(d[axis] for d in deltas) for axis in range(2)]
                if any(abs(a-b) > tolerance for a, b, tolerance in zip(recorded_angles, predicted, tolerances)):
                    reasons.add("recorded_angular_response_disagrees_with_emitted_gain")
        else:
            reasons.add("recorded_angular_response_unavailable")
        for c in selected[1:]:
            held = effective_scalar(c, "buttonstate1", "buttons_present")
            changed = effective_scalar(c, "buttonstate2", "buttons_present")
            if c.get("buttons_present") is True and type(held) is int and type(changed) is int and changed & mask:
                key = "press" if held & mask else "release"
                if type(pre_input_numbers[key]) is int and c["command_number"] > pre_input_numbers[key]:
                    (presses if held & mask else releases).append(c["command_number"])
                else:
                    earlier_changes.append({"command_number": c["command_number"], "pressed": bool(held & mask)})
            raw_steps += [{"command_number": c["command_number"], **step} for step in c["subtick_moves"] if type(step.get("button")) is int and step["button"] & mask]
    if not presses or not releases:
        reasons.add("recorded_control_press_or_release_response_missing")
    pawn = before_press["pawn_state"] if before_press else {}
    response_window = [s for s in response_window if s["qpc_before"] > injections[0]["receipt"]["qpc_after"]]
    origins = [s["pawn_state"].get("origin") for s in response_window]
    velocities = [s["pawn_state"].get("velocity") for s in response_window]
    effect = {}
    if phase["id"] == "crouch":
        amounts = [s["pawn_state"].get("duck_amount") for s in response_window]
        known = _number(pawn.get("duck_amount")) and amounts and all(_number(v) for v in amounts)
        effect = {"duck_amount_before": pawn.get("duck_amount"), "maximum_duck_amount": max(amounts) if known else None,
            "ducked_observed": any(s["pawn_state"].get("ducked") is True for s in response_window)}
        if not known or max(amounts) - pawn["duck_amount"] <= .05 or not effect["ducked_observed"]:
            reasons.add("crouch_response_missing_or_unavailable")
    else:
        known = _vector(pawn.get("origin")) and origins and all(_vector(v) for v in origins + velocities)
        field, sign = (("forwardmove", 1 if phase["id"] == "forward" else -1) if phase["id"] in ("forward", "back")
                       else ("leftmove", 1 if phase["id"] == "left" else -1))
        matching = [c["command_number"] for c in selected if valid_window and type(pre_input_numbers["press"]) is int and
            c["command_number"] > pre_input_numbers["press"] and _number(c.get(field)) and c[field] * sign > 0]
        effect = {"maximum_horizontal_displacement_units": max(math.hypot(v[0]-pawn["origin"][0], v[1]-pawn["origin"][1]) for v in origins) if known else None,
            "maximum_horizontal_speed_units_per_second": max(math.hypot(v[0], v[1]) for v in velocities) if known else None,
            "analog_field_hypothesis": field, "analog_sign_hypothesis": sign, "matching_analog_command_numbers": matching}
        if not known or effect["maximum_horizontal_displacement_units"] <= .25 or effect["maximum_horizontal_speed_units_per_second"] <= 1 or not matching:
            reasons.add("movement_response_missing_or_unavailable")
    return {**result, "status": "combined_response_observed" if not reasons else "missing_or_inconsistent_response",
        "reasons": sorted(reasons), "angular_plateaus": plateaus, "angular_residual_yaw_pitch_degrees": residuals,
        "angular_tolerance_yaw_pitch_degrees": tolerances, "native_movement_or_crouch_effect": effect,
        "recorded_angular_response_yaw_pitch_degrees": recorded_angles,
        "raw_command_diagnostics": raw, "recorded_button_response": {"plane_change_press_command_numbers": presses,
            "plane_change_release_command_numbers": releases, "raw_subtick_records": raw_steps,
            "already_processed_changes_excluded_from_response": earlier_changes,
            "observed_processed_command_before_insertions": pre_input_numbers,
            "pre_insertion_native_frame_qpc": {key: value["qpc"] if value else None for key, value in observed_before.items()},
            "exact_event_count_order_or_time_verified": False},
        "player_association": {"basis": "same_steam_id_unique_processed_command_execution_endpoints_and_independently_stable_pawns",
            "native_pawn_handle": identity[0][1], "canonical_pawn_handle": selected[0].get("pawn_entity_handle") if selected else None,
            "numeric_pawn_handle_equivalence_verified": False},
        "first_insertion_qpc_before": first_insert, "last_insertion_qpc_after": last_insert,
        "latest_pre_plateau_completion_qpc": max(completions) if len(completions) == len(before) else None}


def analyze_control_execution(run, parsed, compilation, out):
    run, parsed, compilation, out = [Path(v).resolve() for v in (run, parsed, compilation, out)]
    markdown = out.with_suffix(".md")
    _require(not out.exists() and not markdown.exists() and not any(out.is_relative_to(p) for p in (run, parsed, compilation)),
        "Execution analysis requires fresh output outside source evidence")
    names = ("calibration.json", "calibration_ledger.jsonl", "capture_ledger.jsonl", "capture_frame_files.json",
             "native-plan.json", "input_ledger.jsonl", "controlled.dem", "renderer-sandbox/bin/win64/server.dll")
    paths = {name: _owned(run, name) for name in names}
    paths.update({"compilation/" + name: _owned(compilation, name) for name in ("program.json", "compiled.json", "projection.json", "synthetic-plan.json")})
    paths.update({"parsed/" + name: _owned(parsed, name) for name in ("manifest.json", "usercmd.parquet")})
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    worker, native_plan = _read_object(paths["calibration.json"]), _read_object(paths["native-plan.json"])
    _require(worker.get("plugin_sha256") == hashes["renderer-sandbox/bin/win64/server.dll"] == EXECUTION_PLUGIN_SHA256,
        "Execution requires the inspected ready-time settings plugin bytes")
    descriptor = worker.get("controller_execution", {})
    _require(descriptor.get("source_sha256") == {name: hashes["compilation/" + name] for name in
        ("program.json", "compiled.json", "projection.json", "synthetic-plan.json")}, "Worker compilation file hashes disagree")
    compiled, program, plan = [_read_object(compilation / name) for name in ("compiled.json", "program.json", "synthetic-plan.json")]
    projection = json.loads((compilation / "projection.json").read_text(encoding="utf-8-sig"))
    compilation_audit = audit_compilation(program, compiled, plan, projection)
    fresh_profile = reverify_compilation_profile(compiled["calibration_profile"], out.with_name(out.stem + "-profile-evidence"))
    _require(descriptor.get("calibration_provenance_sha256") == compiled["calibration_profile"]["provenance_sha256"], "Worker calibration provenance disagrees")
    _require(worker.get("native_plan_sha256") == hashes["native-plan.json"] and worker.get("input_ledger") ==
        {"path": "input_ledger.jsonl", "sha256": hashes["input_ledger.jsonl"]}, "Worker synthetic input evidence binding disagrees")
    pixels = _audit(run)
    _require(pixels.get("status") == "all_archived_pixels_match_readback" and
        pixels.get("native_local_player_stable_frames") == len(pixels.get("frames", [])) > 0,
        "Execution requires complete matching pixels and stable native observations during readback")
    native, inputs = _read_ledger(paths["calibration_ledger.jsonl"]), _read_ledger(paths["input_ledger.jsonl"])
    evidence = verify_injection_evidence(inputs, native, native_plan, worker, hashes["native-plan.json"])
    evidence["native_frames"] = [row for row in native if row.get("event") == "frame_sample"]
    _same(evidence["source_plan"], plan, "executed plan")
    _same(worker["binary_profile"], dict(fresh_profile.binary_profile), "execution build versus measured build")
    for name, expected in compiled["calibration_profile"]["ready_requirements"].items():
        actual = evidence["ready"].get("synthetic_input_ready_convars", {}).get(name)
        _require(isinstance(actual, str) and math.isfinite(float(actual)) and float(actual) == float(expected), "Actual ready-time calibrated setting unavailable: " + name)
    captures = _read_ledger(paths["capture_ledger.jsonl"])
    completions = {r["submission_candidate"]["capture_index"]: r.get("qpc_after") for r in captures if r["event"] == "pixel_readback"}
    states = [{**_state(r, True), "qpc_after": r.get("qpc_after"), "pixel_readback_qpc_after": completions.get(r["capture_index"])}
        for r in captures if r["event"] == "movie_frame"]
    manifest = parsed_manifest(parsed, ("usercmd.parquet",))
    _require(manifest.get("demo_id") == hashes["controlled.dem"] and manifest.get("tick_rate") == 64, "Execution commands require this original 64 Hz demo")
    commands = [row for batch in batches(parsed / "usercmd.parquet") for row in batch]
    _require(len(commands) <= 200000, "Execution command evidence exceeds bounded scope")
    wire = _full_payload_evidence(paths["controlled.dem"], commands)
    _require(wire["standalone_full_payload_bytes_match"], "Execution analysis requires byte-matched original full UserCmd payloads")
    for declared, measured in (("full_payload_count", "live_full_payload_count"), ("delta_payload_count", "live_delta_payload_count"), ("eligible_payload_count", "live_eligible_payload_count")):
        _require(manifest.get(declared) == wire[measured], "Execution canonical coverage differs from live source envelopes")
    phases = [measure_phase(phase, states, evidence, commands) for phase in compilation_audit["phases"]]
    report = {"schema_version": 1, "profile": PROFILE, "protocol": PROTOCOL,
        "status": "all_combined_phases_observed" if all(p["status"] == "combined_response_observed" for p in phases) else "incomplete_or_inconsistent_execution_response",
        "source_files": {name: {"path": str(path), "sha256": hashes[name]} for name, path in paths.items()},
        "frozen_program_canonical_sha256": FROZEN_PROGRAM_CANONICAL_SHA256,
        "analyzer_sha256": sha256_file(Path(__file__)), "compilation_audit": compilation_audit, "phases": phases,
        "calibration_sources_independently_reverified": True,
        "fresh_calibration_provenance_sha256": fresh_profile.provenance_sha256,
        "phase_status_counts": dict(Counter(p["status"] for p in phases)), "source_pixel_audit": pixels,
        "original_demo_command_binding": wire, "queue_insertion_verified": True,
        "actual_ready_settings_verified": True, "input_consumption_timing_verified": False,
        "exact_input_timing_verified": False, "image_alignment_verified": False, "competitive_source_verified": False,
        "physical_device_latency_verified": False, "training_ready": False, "live_policy_ready": False,
        "limits": ["Only the frozen five-phase scripted local program is evaluated.",
            "Compilation calibration provenance is retained separately from new run response measurements.",
            "Native processed-command windows and raw sums do not establish one-to-one OS input consumption or exact subtick delivery.",
            "Angular plateaus must be stable; player translation during these combined movement phases is allowed.",
            "No observation-frame training alignment, competitive source acceptance, or physical-device latency is established."]}
    _require(all(sha256_file(path) == hashes[name] for name, path in paths.items()), "Execution sources changed during analysis")
    fresh_profile.verify_sources_unchanged()
    for path, digest in compiled["calibration_profile"]["provenance"]["source_files"].items():
        _require(sha256_file(Path(path)) == digest, "Compiled calibration source changed during execution analysis: " + path)
    for frame in pixels["frames"]:
        _require(sha256_file(_owned(run, "frames/" + frame["tga_filename"])) == frame["file_sha256"], "Execution pixels changed during analysis")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Combined control execution diagnostic", "", "Status: `" + report["status"] + "`.", "",
        "| Phase | Emitted dx/dy | Camera yaw/pitch (degrees) | Result |", "| --- | --- | --- | --- |"]
    for phase in phases:
        observed = phase.get("angular_plateaus", {}).get("camera", {}).get("response_yaw_pitch_degrees")
        lines.append(f"| {phase['id']} | {phase['emitted_mouse_counts']} | {observed} | {', '.join(phase['reasons']) or phase['status']} |")
    lines += ["", "Exact input consumption, physical latency, training readiness and competitive applicability remain unverified."]
    with markdown.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    report["markdown_sha256"] = sha256_file(markdown)
    with out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "parsed", "compilation", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = analyze_control_execution(args.run, args.parsed, args.compilation, args.out)
    print(json.dumps({"status": result["status"], "phase_status_counts": result["phase_status_counts"], "training_ready": False}))


if __name__ == "__main__":
    main()
