"""Recompute bounded clip evidence; diagnostics never certify unobserved actions.

Canonical command/state agreement is reported separately from native observations.
An idle clip, a guessed epoch, or a hand-edited approval flag cannot validate timing.
"""
from __future__ import annotations

import bisect
import math
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .align import load_frames, same_identity, validate_timing, verify_video
from .calibration import finite_number, identity_filter, load_calibration
from .io import exclusive_output, parsed_manifest, publish, read_json, sha256_file, staging_paths, write_json
from .normalize import effective_scalar, wrap_angle
from .timing import read_ledger, verify_capture_evidence

VERSION = 2
TOLERANCES = {
    "canonical_angle_degrees": 0.25,
    "native_angle_degrees": 0.5,
    "native_position_units": 2.0,
    "dynamic_angle_degrees": 0.25,
    "minimum_dynamic_observations": 8,
    "minimum_dynamic_span_ticks": 16,
    "fire_command_window_ticks": 1,
    "jump_command_window_ticks": 4,
    "crouch_command_window_ticks": 8,
    "state_gap_ticks": 1,
}
PAUSE_FLAGS = ("m_bGamePaused", "m_bMatchWaitingForResume", "m_bTerroristTimeOutActive",
               "m_bCTTimeOutActive", "m_bTechnicalTimeOut")


def raw_equal(left: Any, right: Any) -> bool:
    """Preserve malformed numbers as data for the sample-level quality gate."""
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(raw_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(raw_equal(a, b) for a, b in zip(left, right))
    return left == right


def pov_status(observation: dict[str, Any], readback: dict[str, Any] | None, clip: dict[str, Any]) -> tuple[str, list[str]]:
    """Verify first-person target and camera stability at actual pixel readback."""
    pov = observation.get("observed_pov", {})
    if pov.get("status") != "observed" or pov.get("steam_id") is None:
        return "unknown", ["native_observer_target_unresolved"]
    if str(pov["steam_id"]) != str(clip["steam_id"]):
        return "failed", ["native_steam_identity_mismatch"]
    # Current fingerprinted client.dll's embedded ObserverMode_t enum declares
    # IN_EYE=2 (member RVA1af2220); NONE=0,FIXED=1,CHASE=3,ROAMING=4.
    required = ("observer_mode", "camera_view_entity_handle", "pawn_handle", "controller_handle",
                "observer_target_handle", "controller_pawn_handle")
    if any(pov.get(key) is None for key in required):
        return "unknown", ["missing_native_camera_or_reciprocal_handle_evidence"]
    if pov["observer_mode"] != 2 or pov["camera_view_entity_handle"] != 2**32-1:
        return "failed", ["observer_not_first_person_or_view_entity_override"]
    if not pov["pawn_handle"] == pov["observer_target_handle"] == pov["controller_pawn_handle"]:
        return "failed", ["native_pawn_controller_chain_not_reciprocal"]
    if not readback:
        return "unknown", ["native_readback_identity_stability_unavailable"]
    camera = observation.get("rendered_camera")
    if not isinstance(camera, dict) or not isinstance(camera.get("angles"), list) or not isinstance(camera.get("origin"), list):
        return "unknown", ["native_camera_vectors_unavailable"]
    if (len(camera["angles"]) != 3 or len(camera["origin"]) != 3
            or not all(finite_number(value) for value in camera["angles"]+camera["origin"])
            or type(camera.get("matrix_built_framecount")) is not int or camera["matrix_built_framecount"] < 0):
        return "unknown", ["native_camera_vectors_or_matrix_frame_invalid"]
    if camera.get("angles") != camera.get("matrix_input_angles") or camera.get("origin") != camera.get("matrix_input_origin"):
        return "unknown", ["camera_snapshot_and_matrix_inputs_disagree"]
    for field, phase in (("native_observation", "pixel_readback_before"), ("native_observation_after", "pixel_readback_after")):
        observed = readback.get(field, {})
        if observed.get("source_phase") != phase or observed.get("clock_on_engine_thread") is not True:
            return "unknown", ["native_readback_observation_unavailable"]
        if observed.get("observed_pov") != pov or observed.get("rendered_camera") != camera:
            return "failed", ["native_pov_or_camera_changed_during_capture"]
    return "passed", []


def load_state_context(path: Path, canonical: dict[str, Any]) -> dict[str, Any]:
    """Check source-bound observed-tick segments and derive pause from all flags."""
    report = read_json(path)
    if ((report.get("schema_version"), report.get("producer")) not in ((1, "cs2-context-v1"), (2, "cs2-context-v2"))
            or report.get("parse_status") != "complete" or report.get("partial") is not False
            or report.get("demo_id") != canonical["demo_id"]
            or report.get("source_demo_sha256") != canonical.get("sha256", canonical["demo_id"])
            or report.get("tick_rate") != canonical.get("tick_rate")
            or report.get("parser") != "demoinfocs-golang/v6"
            or report.get("parser_version") != canonical.get("parser_version", "v6.0.0-alpha.0")
            or report.get("property_prefix") != "m_pGameRules."
            or report.get("timing_clock") != "demo_tick"
            or set(report.get("required_pause_flags", [])) != set(PAUSE_FLAGS)):
        raise ValueError("State context schema or canonical source identity mismatch")
    audited = report["schema_version"] == 2
    if audited:
        warnings = report.get("warnings")
        if (report.get("warning_policy") != "rule-and-shot-evidence-v1"
                or type(report.get("evidence_loss_warnings")) is not int or report["evidence_loss_warnings"] != 0
                or not isinstance(warnings, dict) or any(key not in ("1", "13") or type(count) is not int or count <= 0
                    for key, count in warnings.items())):
            raise ValueError("State context warning provenance does not establish lossless rule/shot evidence")
    previous_end = None
    for segment in report.get("segments", []):
        start, end = segment.get("start_demo_tick"), segment.get("end_demo_tick")
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start
                or (previous_end is not None and start < previous_end)
                or type(segment.get("round_id")) is not int):
            raise ValueError("State context segments overlap, reverse, or have invalid identity")
        values = [segment.get("pause_flags", {}).get(flag) for flag in PAUSE_FLAGS]
        if any(value is not None and type(value) is not bool for value in values):
            raise ValueError("State context pause flags must be booleans or null")
        paused = None if segment.get("ambiguous_tick") is not False or any(value is None for value in values) else any(values)
        if segment.get("is_paused") is not paused:
            raise ValueError("State context pause status disagrees with its five observed flags")
        previous_end = end
    return {**report, "pause_evidence_verified": audited}


def context_at(context: dict[str, Any], tick: int, round_id: int) -> dict[str, Any] | None:
    return next((row for row in context["segments"] if row["round_id"] == round_id
                 and row["start_demo_tick"] <= tick < row["end_demo_tick"]), None)


def shot_clock_checks(context: dict[str, Any], commands: list[dict[str, Any]],
                       frames: list[dict[str, Any]], clip: dict[str, Any], tick_rate: float,
                       attack_mask: int) -> dict[str, Any]:
    """Cross-check observed weapon clock and action phase without fitting an offset.

    The end-tick-minus-one-plus-subtick formula is evaluated, never silently
    applied to other controls or held-fire shots without an observed press.
    """
    clock_rows, future_rows = [], []
    by_tick = {}
    for command in commands:
        by_tick.setdefault(command["demo_tick"], []).append(command)
    for shot in context.get("weapon_fire_observations", []):
        if (context.get("schema_version") != 2 or context.get("producer") != "cs2-context-v2"
                or context.get("shot_observation_policy") != "unique-weapon-event-per-observed-tick-v1"
                or shot.get("ambiguous_observation") is not False or shot.get("observed_demo_tick") != shot.get("demo_tick")):
            continue
        if (str(shot.get("steam_id")) != str(clip["steam_id"]) or shot.get("round_id") != clip["round_id"]
                or shot.get("player_slot") != clip["player_slot"]):
            continue
        second = shot.get("weapon_last_shot_time_seconds")
        candidates = []
        for command in by_tick.get(shot.get("demo_tick"), []):
            for event in command.get("subtick_moves", []):
                when = event.get("when")
                if event.get("button") == attack_mask and event.get("pressed") is True and finite_number(when) and 0 <= when <= 1:
                    candidates.append((command, when))
        if len(candidates) != 1 or not finite_number(second):
            continue
        command, when = candidates[0]
        residual = abs(second*tick_rate-(command["server_tick_executed"]-1+when))
        item = {"command_row_id": command["command_row_id"], "frame_index": command["frame_index"],
                "demo_tick": shot["demo_tick"], "weapon_last_shot_time_seconds": second,
                "server_tick_executed": command["server_tick_executed"], "attack_subtick_when": when,
                "residual_ticks": residual, "status": "passed" if residual <= 0.03125 else "failed"}
        clock_rows.append(item)
        frame = frames[command["frame_index"]]
        future = frame["render_time_seconds_start"] < second <= frame["render_time_seconds_end"]
        future_rows.append({**item, "status": "passed" if future else "failed",
            "observation_seconds": frame["render_time_seconds_start"], "next_observation_seconds": frame["render_time_seconds_end"]})
    return {"weapon_shot_clock": aggregate("weapon_last_shot_vs_execution_end_minus_one_plus_subtick_v1", clock_rows,
                reason="missing_or_disagreeing_shot_clock_evidence", tolerances={"max_residual_ticks": 0.03125}),
            "weapon_shot_future": aggregate("weapon_last_shot_between_observed_render_times_v1", future_rows,
                reason="missing_shot_or_recorded_shot_outside_future_interval", tolerances={"interval": "(start,end]", "slack_seconds": 0})}


def check(status: str, method: str, reasons: list[str], *, frames=(), commands=(),
          evidence=(), metrics=None, tolerances=None) -> dict[str, Any]:
    if status not in ("passed", "failed", "unknown"):
        raise ValueError("Unsupported validation status")
    return {"status": status, "method": method, "reason_codes": reasons,
            "scope": {"frame_indices": sorted(set(frames)), "command_row_ids": sorted(set(commands))},
            "evidence_count": len(evidence), "evidence": list(evidence),
            "metrics": metrics or {}, "tolerances": tolerances or {}}


def aggregate(method: str, evidence: list[dict[str, Any]], *, reason: str, tolerances=None):
    passed = [row for row in evidence if row["status"] == "passed"]
    failed = [row for row in evidence if row["status"] == "failed"]
    status = "failed" if failed else "passed" if passed and len(passed) == len(evidence) else "unknown"
    return check(status, method, [] if status == "passed" else [reason], evidence=evidence,
                 frames=[row["frame_index"] for row in passed if row.get("frame_index") is not None],
                 commands=[row["command_row_id"] for row in passed if row.get("command_row_id") is not None],
                 metrics={"passed": len(passed), "failed": len(failed), "unknown": len(evidence)-len(passed)-len(failed)},
                 tolerances=tolerances)


def command_buttons(row: dict[str, Any]) -> int | None:
    value = effective_scalar(row, "buttonstate1", "buttons_present")
    return value if type(value) is int and value >= 0 else None


def pressed(row: dict[str, Any], mask: int) -> bool | None:
    buttons = command_buttons(row)
    if not mask or buttons is None:
        return None
    return bool(buttons & mask) or any(event.get("button") == mask and event.get("pressed") is True
                                    for event in row.get("subtick_moves", []))


def canonical_action_checks(commands: list[dict[str, Any]], states: list[dict[str, Any]],
                            events: list[dict[str, Any]], masks: dict[str, int]) -> dict[str, Any]:
    """Independent streams corroborate action meaning, not pixel observation time.

    Checks run from outcomes to inputs: holding attack need not cause a shot and
    holding jump against a ceiling need not cause a takeoff. Missing observations
    stay unknown, including action classes never exercised in the interval.
    """
    by_tick = {row["demo_tick"]: row for row in states}
    evidence = {name: [] for name in ("fire", "aim", "move", "jump", "crouch")}
    for event in events:
        if event.get("kind") != "weapon_fire":
            continue
        tick = event["demo_tick"]
        nearby = [row for row in commands if tick-TOLERANCES["fire_command_window_ticks"] <= row["demo_tick"] <= tick]
        values = [pressed(row, masks.get("attack1", 0)) for row in nearby]
        status = "passed" if any(value is True for value in values) else "failed" if values and all(value is False for value in values) else "unknown"
        evidence["fire"].append({"demo_tick": tick, "status": status,
                                 "command_row_ids": [row["command_row_id"] for row in nearby], "weapon": event.get("weapon")})
    previous_command = None
    for row in commands:
        state = by_tick.get(row["demo_tick"])
        yaw = effective_scalar(row, "view_yaw", "viewangles_present")
        pitch = effective_scalar(row, "view_pitch", "viewangles_present")
        if previous_command is not None and all(finite_number(value) for value in (yaw, pitch)):
            before_yaw = effective_scalar(previous_command, "view_yaw", "viewangles_present")
            before_pitch = effective_scalar(previous_command, "view_pitch", "viewangles_present")
            if all(finite_number(value) for value in (before_yaw, before_pitch)):
                dynamic = max(abs(wrap_angle(yaw-before_yaw)), abs(pitch-before_pitch))
                if dynamic >= TOLERANCES["dynamic_angle_degrees"]:
                    available = state is not None and all(finite_number(state.get(key)) for key in ("view_yaw", "view_pitch"))
                    error = max(abs(wrap_angle(yaw-state["view_yaw"])), abs(pitch-state["view_pitch"])) if available else None
                    evidence["aim"].append({"command_row_id": row["command_row_id"], "demo_tick": row["demo_tick"],
                        "status": "unknown" if error is None else "passed" if error <= TOLERANCES["canonical_angle_degrees"] else "failed",
                        "max_angle_error_degrees": error})
        previous_command = row
        if state is not None:
            movement = [effective_scalar(row, key, "base_present") for key in ("forwardmove", "leftmove")]
            velocity = [state.get(key) for key in ("velocity_x", "velocity_y")]
            if all(finite_number(value) for value in movement+velocity) and any(abs(value) > 0.01 for value in movement):
                speed = math.hypot(*velocity)
                # An obstruction, braking or acceleration can prevent motion; it
                # is not evidence that a perfectly recorded input is wrong.
                evidence["move"].append({"command_row_id": row["command_row_id"], "demo_tick": row["demo_tick"],
                    "status": "passed" if speed >= 5 else "unknown", "planar_speed": speed})
    for before, after in zip(states, states[1:]):
        if after["demo_tick"]-before["demo_tick"] != 1:
            continue
        candidates = []
        if before.get("on_ground") is True and after.get("on_ground") is False and finite_number(after.get("velocity_z")) and after["velocity_z"] > 50:
            candidates.append(("jump", TOLERANCES["jump_command_window_ticks"]))
        if before.get("crouching") is False and after.get("crouching") is True:
            candidates.append(("crouch", TOLERANCES["crouch_command_window_ticks"]))
        for name, window in candidates:
            nearby = [row for row in commands if after["demo_tick"]-window <= row["demo_tick"] <= after["demo_tick"]]
            values = [pressed(row, masks.get(name, 0)) for row in nearby]
            evidence[name].append({"demo_tick": after["demo_tick"],
                "command_row_ids": [row["command_row_id"] for row in nearby],
                "status": "passed" if any(value is True for value in values) else "unknown"})
    return {"canonical_"+name: aggregate("canonical_outcome_to_input_v1", rows,
            reason="missing_inconclusive_or_disagreeing_canonical_action_evidence", tolerances=TOLERANCES)
            for name, rows in evidence.items()}


def bounded_state(states: list[dict[str, Any]], tick: float) -> tuple[dict[str, Any] | None, dict[str, Any] | None, float]:
    """Return two adjacent actual states; never bridge gaps or extrapolate."""
    ticks = [row["demo_tick"] for row in states]
    right = bisect.bisect_left(ticks, tick)
    if right < len(states) and ticks[right] == tick:
        return states[right], states[right], 0.0
    if right == 0 or right == len(states) or ticks[right]-ticks[right-1] != 1:
        return None, None, 0.0
    return states[right-1], states[right], tick-ticks[right-1]


def expected_state(states: list[dict[str, Any]], tick: float) -> dict[str, Any] | None:
    before, after, fraction = bounded_state(states, tick)
    if before is None:
        return None
    result = {"before_demo_tick": before["demo_tick"], "after_demo_tick": after["demo_tick"]}
    for key in ("view_yaw", "view_pitch", "position_x", "position_y", "position_z", "velocity_x", "velocity_y", "velocity_z"):
        left, right = before.get(key), after.get(key)
        if finite_number(left) and finite_number(right):
            delta = wrap_angle(right-left) if key == "view_yaw" else right-left
            result[key] = left+fraction*delta
    for key in ("on_ground", "crouching", "ammo_clip", "active_weapon"):
        if before.get(key) == after.get(key):
            result[key] = before.get(key)
    return result


def native_observation_checks(records: list[dict[str, Any]], frames: list[dict[str, Any]],
                              states: list[dict[str, Any]], clip: dict[str, Any],
                              commands: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Conservative adapter for the versioned native observation contract.

    The legacy ledger has no such observations and always yields unknown. Only
    direct render-state fields are used for visual comparisons; an engine-stage
    pawn snapshot cannot be renamed into a rendered image observation.
    """
    evidence = {name: [] for name in ("pov_identity", "visual_aim", "visual_move", "visual_fire", "visual_jump", "visual_crouch")}
    native = [row for row in records if row.get("event") == "movie_frame"]
    readbacks = {row.get("submission_candidate", {}).get("capture_index"): row
                 for row in records if row.get("event") == "pixel_readback"}
    command_numbers = {row["command_number"]: row for row in commands or []}
    execution_observations = []
    for index, (row, frame) in enumerate(zip(native, frames)):
        observation = row.get("native_observation")
        if (not isinstance(observation, dict) or observation.get("schema_version") != 1
                or observation.get("source_phase") != "movie_submission" or observation.get("clock_on_engine_thread") is not True):
            continue
        pov = observation.get("observed_pov", {})
        if (pov.get("status") == "observed" and pov.get("resolution_path") in (
                "local_pawn.observer_services.target_handle.pawn.controller_handle.steam_id",
                "local_controller.pawn_handle.observer_services.target_handle.pawn.controller_handle.steam_id")
                and pov.get("steam_id") is not None and pov.get("pawn_handle") is not None and pov.get("controller_handle") is not None):
            status, reasons = pov_status(observation, readbacks.get(index), clip)
            if pov.get("player_slot") is not None:
                if pov["player_slot"] != clip["player_slot"]:
                    status, reasons = "failed", ["native_player_slot_mismatch"]
            evidence["pov_identity"].append({"frame_index": index, "status": status, "reason_codes": reasons,
                "observed_steam_id": str(pov["steam_id"]), "observed_player_slot": pov.get("player_slot"),
                "observer_mode": pov.get("observer_mode"), "camera_view_entity_handle": pov.get("camera_view_entity_handle")})
        pawn = pov.get("pawn_state", {})
        number, tick = pawn.get("last_executed_command_number"), pawn.get("last_executed_command_tick")
        if number is not None or tick is not None:
            raw = command_numbers.get(number)
            execution_observations.append({"frame_index": index, "observed_command_number": number,
                "observed_command_tick": tick, "canonical_command_row_id": raw["command_row_id"] if raw else None,
                "native_tick_minus_server_execution_tick": tick-raw["server_tick_executed"] if raw and type(tick) is int else None,
                "movement_last_command_number_processed": pawn.get("movement_last_command_number_processed"),
                "simulation_tick": pawn.get("simulation_tick"), "simulation_time": pawn.get("simulation_time"),
                "controller_tick_base": pov.get("controller_tick_base")})
        camera = observation.get("rendered_camera", {})
        if not isinstance(camera, dict) or camera.get("source") != "CViewRender.current_view_at_observation_boundary":
            continue
        angles = camera.get("angles")
        state = {}
        if isinstance(angles, list) and len(angles) == 3:
            state.update(view_pitch=angles[0], view_yaw=angles[1])
        # Camera origin is an eye position; canonical position is the pawn's
        # feet. Never compare them or subtract an invented view-height offset.
        if isinstance(pawn.get("origin"), list) and len(pawn["origin"]) == 3:
            state.update(zip(("position_x", "position_y", "position_z"), pawn["origin"]))
        if type(pawn.get("ducked")) is bool:
            state["crouching"] = pawn["ducked"]
        for field in ("on_ground", "crouching", "ammo_clip"):
            if field in pawn:
                state[field] = pawn[field]
        expected = expected_state(states, frame["action_window_demo_tick_start"])
        if expected is None:
            continue
        pairs = [(state.get(key), expected.get(key)) for key in ("view_yaw", "view_pitch")]
        if all(finite_number(value) for pair in pairs for value in pair):
            error = max(abs(wrap_angle(pairs[0][0]-pairs[0][1])), abs(pairs[1][0]-pairs[1][1]))
            evidence["visual_aim"].append({"frame_index": index, "status": "passed" if error <= TOLERANCES["native_angle_degrees"] else "unknown",
                "max_angle_error_degrees": error, "reason_codes": [] if error <= TOLERANCES["native_angle_degrees"] else ["camera_effects_or_observation_phase_not_modeled"]})
        position = [(state.get(key), expected.get(key)) for key in ("position_x", "position_y", "position_z")]
        if all(finite_number(value) for pair in position for value in pair):
            error = math.sqrt(sum((actual-wanted)**2 for actual, wanted in position))
            evidence["visual_move"].append({"frame_index": index, "status": "passed" if error <= TOLERANCES["native_position_units"] else "unknown",
                "position_error_units": error, "reason_codes": [] if error <= TOLERANCES["native_position_units"] else ["pawn_interpolation_or_observation_phase_not_modeled"]})
        for name, field in (("visual_jump", "on_ground"), ("visual_crouch", "crouching"), ("visual_fire", "ammo_clip")):
            if state.get(field) is not None and expected.get(field) is not None:
                evidence[name].append({"frame_index": index, "status": "passed" if state[field] == expected[field] else "failed",
                    "observed": state[field], "expected": expected[field], "field": field})
    checks = {name: aggregate("native_observation_vs_canonical_state_v1", rows,
               reason="missing_or_disagreeing_native_observation", tolerances=TOLERANCES) for name, rows in evidence.items()}
    # Agreement with an assumed fractional coordinate can corroborate it but is
    # not a measured render/execution epoch anchor. A future native execution
    # contract must establish that relation directly, then be verified here.
    checks["observation_clock"] = check("unknown", "native_render_clock_review_v1",
        ["render_execution_epoch_or_observation_phase_unmeasured"],
        metrics={"render_state_angle_matches": checks["visual_aim"]["metrics"].get("passed", 0)})
    checks["native_execution_observations"] = check("unknown", "native_last_executed_command_fields_v1",
        ["native_command_field_semantics_not_certified"], evidence=execution_observations,
        metrics={"canonical_command_number_matches": sum(row["canonical_command_row_id"] is not None for row in execution_observations)})
    transitions = []
    for index in range(1, len(native)):
        before = native[index-1].get("native_observation", {})
        after = native[index].get("native_observation", {})
        previous_pawn = before.get("observed_pov", {}).get("pawn_state", {})
        current_pawn = after.get("observed_pov", {}).get("pawn_state", {})
        changed = []
        for name, field in (("fire", "ammo_clip"), ("jump", "on_ground"), ("crouch", "ducked")):
            a, b = previous_pawn.get(field), current_pawn.get(field)
            if a is not None and b is not None and a != b:
                changed.append(name)
        for name, source, field in (("camera", "rendered_camera", "angles"), ("movement", "pawn_state", "origin")):
            a = before.get(source, {}).get(field) if source == "rendered_camera" else previous_pawn.get(field)
            b = after.get(source, {}).get(field) if source == "rendered_camera" else current_pawn.get(field)
            if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 3 and all(finite_number(v) for v in a+b):
                if max(abs(wrap_angle(y-x)) if name == "camera" else abs(y-x) for x, y in zip(a, b)) > 0.25:
                    changed.append(name)
        if changed:
            transitions.append({"before_frame_index": index-1, "frame_index": index, "observed_modalities": changed})
    checks["native_transition_coverage"] = check("unknown", "observed_native_changes_not_profile_calibration_v1",
        ["transitions_require_independent_action_and_phase_calibration"], evidence=transitions,
        metrics={name: sum(name in row["observed_modalities"] for row in transitions) for name in ("fire", "camera", "movement", "jump", "crouch")})
    return checks


def recompute_validation(parsed: Path, dataset: Path, state_context: Path | None = None) -> dict[str, Any]:
    """Read actual artifacts and compute deterministic evidence, without writes."""
    parsed = parsed.resolve()
    dataset = dataset.resolve()
    canonical = parsed_manifest(parsed, ["usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"])
    alignment_path = dataset / "aligned/alignment_manifest.json"
    alignment = read_json(alignment_path)
    clip_path, timing_path = dataset / "timing/clip.json", dataset / "timing/frames.jsonl"
    clip, timings = read_json(clip_path), load_frames(timing_path)
    if alignment.get("status") != "complete" or alignment.get("alignment_version") != 1 or not same_identity(alignment, clip):
        raise ValueError("Validation requires a complete matching alignment")
    if canonical["demo_id"] != clip["demo_id"]:
        raise ValueError("Canonical source and clip identity disagree")
    sources = []
    def bind(role, path, expected=None):
        path = Path(path).resolve()
        digest = sha256_file(path)
        if expected is not None and digest != expected:
            raise ValueError(f"Validation source hash mismatch: {role}")
        sources.append({"role": role, "path": str(path), "sha256": digest})
    bind("pipeline_manifest", dataset / "pipeline_manifest.json")
    bind("alignment_manifest", alignment_path)
    bind("parsed_manifest", parsed / "manifest.json", alignment.get("source_parsed_manifest_sha256"))
    for name in ("usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"):
        bind("canonical_"+name, parsed / name, canonical["files"][name])
    context = None
    if state_context is not None:
        context = load_state_context(state_context, canonical)
        bind("state_context", state_context)
    for name, digest in alignment["files"].items():
        bind(name, dataset / "aligned" / name, digest)
    bind("timing_clip", clip_path, alignment["source_clip_manifest_sha256"])
    bind("timing_frames", timing_path, alignment["source_timing_sha256"])
    validate_timing(timings, clip, diagnostic=True)
    capture = verify_capture_evidence(clip, timings)
    bind("video", verify_video(clip, clip_path, timings), clip["video_sha256"])
    for name in ("ledger", "render_manifest", "frame_inventory"):
        bind(name, capture["evidence"][name+"_path"], capture["evidence"][name+"_sha256"])
    render = read_json(Path(capture["evidence"]["render_manifest_path"]))
    phase = render.get("source_job", {}).get("phase_evidence", {})
    if phase.get("source_phase_path"):
        bind("phase_sidecar", phase["source_phase_path"], phase["source_phase_sha256"])
    frames = pq.read_table(dataset / "aligned/frame_alignment.parquet").to_pylist()
    commands = pq.read_table(dataset / "aligned/aligned_commands.parquet").to_pylist()
    all_commands = ds.dataset(parsed / "usercmd.parquet", format="parquet").to_table(filter=identity_filter(clip)).to_pylist()
    calibration = load_calibration(dataset / "calibration", canonical, clip, all_commands)
    bind("execution_calibration", calibration["calibration_path"], calibration["calibration_sha256"])
    if alignment.get("execution_calibration") != calibration:
        raise ValueError("Aligned calibration disagrees with recomputed original evidence")
    if len(frames) != len(timings) or len(commands) != alignment["command_count"]:
        raise ValueError("Aligned frame/command counts disagree")
    canonical_by_id = {row["command_row_id"]: row for row in all_commands}
    grouped = {index: [] for index in range(len(frames))}
    seen_commands = set()
    for row in commands:
        raw = canonical_by_id.get(row["command_row_id"])
        if raw is None or any(not raw_equal(row.get(key), value) for key, value in raw.items()):
            raise ValueError("Aligned command differs from its canonical raw row")
        index = row["frame_index"]
        if index not in grouped or row["command_row_id"] in seen_commands:
            raise ValueError("Aligned command has duplicate or invalid frame identity")
        seen_commands.add(row["command_row_id"])
        grouped[index].append(row["command_row_id"])
        execution = raw["server_tick_executed"]+calibration["offset_demo_ticks"]
        if row.get("execution_demo_tick") != execution or not frames[index]["action_window_demo_tick_start"] < execution <= frames[index]["action_window_demo_tick_end"]:
            raise ValueError("Aligned command is not a recomputed future target")
    for index, (frame, timing) in enumerate(zip(frames, timings)):
        if not same_identity(frame, clip) or frame["frame_index"] != index or frame["command_row_ids"] != grouped[index]:
            raise ValueError("Aligned frame identity or command index disagrees")
        for field in ("source_demo_tick_start", "source_demo_tick_end", "render_time_seconds_start", "render_time_seconds_end"):
            if frame[field] != timing[field]:
                raise ValueError("Aligned frame clock differs from native timing evidence")
        for suffix in ("start", "end"):
            expected = timing["render_time_seconds_"+suffix]*canonical["tick_rate"]+calibration["offset_demo_ticks"]
            if frame["action_window_demo_tick_"+suffix] != expected:
                raise ValueError("Aligned action interval differs from recomputed render clock")
    start, end = frames[0]["action_window_demo_tick_start"], frames[-1]["action_window_demo_tick_end"]
    expected_commands = {row["command_row_id"] for row in all_commands
        if calibration["first_command_row_id"] <= row["command_row_id"] <= calibration["last_command_row_id"]
        and start < row["server_tick_executed"]+calibration["offset_demo_ticks"] <= end}
    if seen_commands != expected_commands:
        raise ValueError("Aligned targets omit or add commands in the calibrated future interval")
    state_filter = identity_filter(clip) & (ds.field("demo_tick") >= math.floor(start)-8) & (ds.field("demo_tick") <= math.ceil(end)+8)
    states = ds.dataset(parsed / "player_state.parquet", format="parquet").to_table(filter=state_filter).to_pylist()
    states.sort(key=lambda row: row["demo_tick"])
    if len({row["demo_tick"] for row in states}) != len(states):
        raise ValueError("Canonical player states have duplicate tick identity")
    event_filter = identity_filter(clip) & (ds.field("demo_tick") > start) & (ds.field("demo_tick") <= end)
    events = ds.dataset(parsed / "events.parquet", format="parquet").to_table(filter=event_filter).to_pylist()
    indices = list(range(len(frames)))
    pixels = capture["pixel_correspondence"]
    checks = {
        "capture_integrity": check("passed", "rehash_native_and_canonical_artifacts_v1", [], frames=indices,
            evidence=[{"source_count": len(sources), "frame_count": len(frames), "future_command_count": len(commands)}]),
        "pixel_correspondence": check("passed" if pixels.get("verified") else "unknown", "tga_rgb_to_native_readback_sha256_v1",
            [] if pixels.get("verified") else ["native_readback_pixels_unavailable"], frames=indices if pixels.get("verified") else (),
            evidence=[pixels]),
        "execution_clock": check("passed" if calibration["execution_timing_verified"] else "unknown", calibration["method"],
            [] if calibration["execution_timing_verified"] else ["packet_execution_offset_is_inferred"],
            frames=indices if calibration["execution_timing_verified"] else (),
            commands=sorted(seen_commands) if calibration["execution_timing_verified"] else (),
            evidence=[{"offset_demo_ticks": calibration["offset_demo_ticks"], "anchor_count": calibration["anchor_count"]}]),
    }
    checks.update(canonical_action_checks(commands, states, events, canonical.get("button_masks", {})))
    checks.update(native_observation_checks(read_ledger(Path(capture["evidence"]["ledger_path"])), frames, states, clip, all_commands))
    if context is not None:
        checks.update(shot_clock_checks(context, commands, frames, clip, canonical["tick_rate"], canonical.get("button_masks", {}).get("attack1", 0)))
    return {"schema_version": 1, "validation_version": VERSION, "status": "complete", "training_ready": False,
            **{key: clip[key] for key in ("clip_id", "demo_id", "round_id", "steam_id", "player_slot")},
            "dataset_directory": str(dataset), "parsed_directory": str(parsed),
            "state_context_path": str(state_context.resolve()) if state_context is not None else None,
            "num_frames": len(frames), "command_count": len(commands), "source_files": sources,
            "checks": checks, "policy": "Canonical comparisons do not certify replay pixels. Unknown evidence never passes acceptance; scope is exact and cannot be extended to untested frames/actions."}


@exclusive_output()
def validate_clip(parsed: Path, dataset: Path, out: Path, state_context: Path | None = None) -> dict[str, Any]:
    report = recompute_validation(parsed, dataset, state_context)
    destinations = [out / "clip_validation.json"]
    staged = staging_paths(destinations)
    write_json(staged[0], report)
    publish(staged, destinations)
    return report


def load_validation(path: Path, parsed: Path, dataset: Path, state_context: Path | None = None) -> dict[str, Any]:
    path = path / "clip_validation.json" if path.is_dir() else path
    actual = read_json(path)
    expected = recompute_validation(parsed, dataset, state_context)
    if actual != expected:
        raise ValueError("Validation report differs from recomputed source evidence; edited statuses cannot approve samples")
    return actual
