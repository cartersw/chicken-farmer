"""A 32 Hz action representation and uncalibrated recorded-command candidates.

This module neither executes controls nor approves training samples. A caller
must independently establish image information bounds and source command clocks.
Canonical protobufs are checked again before projecting any candidate values.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import math
from typing import Any, Sequence

from .causal_acceptance import _command_quality
from .causal_targets import IDENTITY
from .normalize import effective_scalar

CONTRACT_ID = "cs2-control-32hz-v1"
CANDIDATE_PROFILE = "future_two_command_control_candidate_v1"
DECISION_PERIOD_NS = 31_250_000
SERVER_TICK_NS = 15_625_000
BUTTONS = ("forward", "back", "left", "right", "jump", "crouch", "walk",
           "attack1", "attack2", "reload", "use", "drop", "scoreboard")
CHANNELS = ("angular_delta_deg", "buttons_held_at_start", "button_events")
MAX_BUTTON_EVENTS = 256


def control_contract_schema() -> dict[str, Any]:
    """Return a fresh JSON Schema for a proposed action, not an execution permit.

    Shape validation alone cannot enforce finite Python floats, event ordering,
    transition consistency or availability/value agreement. Run
    ``validate_control_action`` as well. Calibration remains unmeasured in v1.
    """
    def obj(properties, required=None):
        return {"type": "object", "properties": properties,
                "required": list(properties) if required is None else required,
                "additionalProperties": False}

    status = obj({"available": {"type": "boolean"},
                  "calibration_status": {"const": "unmeasured"}})
    angle = obj({"yaw": {"type": "number"}, "pitch": {"type": "number"}})
    held = obj({button: {"type": "boolean"} for button in BUTTONS})
    event = obj({"offset_ns": {"type": "integer", "minimum": 0, "exclusiveMaximum": DECISION_PERIOD_NS},
                 "button": {"enum": list(BUTTONS)}, "pressed": {"type": "boolean"}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CS2 32 Hz proposed action; calibration unmeasured",
            **obj({"schema_version": {"const": 1}, "contract_id": {"const": CONTRACT_ID},
                   "decision_period_ns": {"const": DECISION_PERIOD_NS},
                   "angular_delta_deg": {"anyOf": [angle, {"type": "null"}]},
                   "buttons_held_at_start": {"anyOf": [held, {"type": "null"}]},
                   "button_events": {"anyOf": [{"type": "array", "items": event,
                                                 "maxItems": MAX_BUTTON_EVENTS}, {"type": "null"}]},
                   "channel_status": obj({name: deepcopy(status) for name in CHANNELS})})}


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _keys(value: Any, required: Sequence[str]) -> bool:
    return isinstance(value, dict) and set(value) == set(required)


def validate_control_action(payload: Any) -> list[str]:
    """Return errors for a proposed action; an empty list proves only its format.

    Held state applies immediately BEFORE offset-zero events. Events are ordered
    by nondecreasing offset; equal offsets retain array order. A release needs
    a held button and a press needs a released button. A masked initial state
    requires masked events, since an empty event list cannot express unknown
    state. No physical mouse scale or calibrated timing is implied.
    """
    errors: set[str] = set()
    required = ("schema_version", "contract_id", "decision_period_ns", *CHANNELS, "channel_status")
    if not _keys(payload, required):
        return ["action_fields_invalid"]
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        errors.add("action_schema_version_invalid")
    if payload["contract_id"] != CONTRACT_ID:
        errors.add("action_contract_id_invalid")
    if type(payload["decision_period_ns"]) is not int or payload["decision_period_ns"] != DECISION_PERIOD_NS:
        errors.add("action_decision_period_invalid")
    status = payload["channel_status"]
    if not _keys(status, CHANNELS):
        errors.add("action_channel_status_invalid")
    else:
        for channel in CHANNELS:
            item = status[channel]
            if (not _keys(item, ("available", "calibration_status"))
                    or type(item.get("available")) is not bool
                    or item.get("calibration_status") != "unmeasured"):
                errors.add("action_"+channel+"_status_invalid")
            elif item["available"] is not (payload[channel] is not None):
                errors.add("action_"+channel+"_availability_disagrees")
    angle = payload["angular_delta_deg"]
    if angle is not None and (not _keys(angle, ("yaw", "pitch")) or not all(_finite(v) for v in angle.values())):
        errors.add("action_angular_delta_invalid")
    held = payload["buttons_held_at_start"]
    valid_held = _keys(held, BUTTONS) and all(type(v) is bool for v in held.values())
    if held is not None and not valid_held:
        errors.add("action_held_state_invalid")
    events = payload["button_events"]
    if events is not None:
        if not isinstance(events, list) or len(events) > MAX_BUTTON_EVENTS:
            errors.add("action_events_invalid")
        else:
            state = dict(held) if valid_held else None
            if state is None:
                errors.add("action_events_require_initial_state")
            previous_offset = -1
            for event in events:
                if (not _keys(event, ("offset_ns", "button", "pressed"))
                        or type(event.get("offset_ns")) is not int
                        or not 0 <= event["offset_ns"] < DECISION_PERIOD_NS
                        or event.get("button") not in BUTTONS
                        or type(event.get("pressed")) is not bool):
                    errors.add("action_event_invalid")
                    continue
                if event["offset_ns"] < previous_offset:
                    errors.add("action_events_out_of_order")
                previous_offset = event["offset_ns"]
                if state is not None:
                    if state[event["button"]] is event["pressed"]:
                        errors.add("action_event_not_a_transition")
                    state[event["button"]] = event["pressed"]
    return sorted(errors)


def _action(angle: dict[str, float] | None) -> dict[str, Any]:
    return {"schema_version": 1, "contract_id": CONTRACT_ID,
            "decision_period_ns": DECISION_PERIOD_NS, "angular_delta_deg": angle,
            "buttons_held_at_start": None, "button_events": None,
            "channel_status": {name: {"available": name == "angular_delta_deg" and angle is not None,
                                       "calibration_status": "unmeasured"} for name in CHANNELS}}


def _same_identity(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(row.get(key) is not None and str(row[key]) == str(expected[key]) for key in IDENTITY)


def _raw_provenance(row: dict[str, Any], *, valid: bool) -> dict[str, Any]:
    raw = row.get("command_protobuf")
    raw = raw if isinstance(raw, bytes) else None
    result = {name: row.get(name) for name in
              (*IDENTITY, "command_row_id", "demo_tick", "server_tick_executed", "command_number",
               "client_tick", "pawn_entity_handle")}
    result.update(command_protobuf_base64=base64.b64encode(raw).decode("ascii") if raw else None,
                  command_protobuf_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
                  raw_button_planes={"buttonstate"+str(i):
                      format(row["buttonstate"+str(i)], "016x")
                      if type(row.get("buttonstate"+str(i))) is int and 0 <= row["buttonstate"+str(i)] < 2**64
                      else None for i in (1, 2, 3)},
                  buttons_parent_present=row.get("buttons_present") is True,
                  projected_collections_retained=valid,
                  raw_subtick_moves=deepcopy(row.get("subtick_moves")) if valid else None,
                  raw_input_history=deepcopy(row.get("input_history")) if valid else None)
    return result


def build_control_candidate(commands: Sequence[dict[str, Any]], *, observation_upper_execution_tick: int,
                            identity: dict[str, Any], observation_frame_index: int | None = None) -> dict[str, Any]:
    """Build one diagnostic 64-to-32 Hz chunk after a caller-established bound B.

    The predecessor must execute at B+2 and the two targets at B+3 and B+4.
    Their enclosing support union is [B+1,B+4]. The targets' processing window
    is [B+2,B+4]; these three-tick provenance bounds are not a claim about exact
    event times or physical input latency. Candidate validity establishes local
    command quality only. No mutable ``verified`` flag or calibration override
    is accepted. Button semantics stay masked in every generated candidate.

    Commands are in canonical source order for one demo/player/round. No missing
    target may be substituted with a later command. A duplicate execution tick
    cannot be mapped to this one-command-per-tick profile. Source envelope,
    packet bound, image and complete pause-state checks remain the caller's job.
    """
    upper = observation_upper_execution_tick
    if type(upper) is not int or upper < 0:
        raise ValueError("The 64-to-32 Hz candidate profile needs a nonnegative integer observation bound")
    if any(identity.get(key) is None for key in IDENTITY):
        raise ValueError("Control candidates require complete demo/player/round identity")
    if observation_frame_index is not None and (type(observation_frame_index) is not int or observation_frame_index < 0):
        raise ValueError("Observation frame index must be a nonnegative integer or None")
    if any(not isinstance(row, dict) or not _same_identity(row, identity) for row in commands):
        raise ValueError("Command identity differs from the requested control candidate")
    result: dict[str, Any] = {"schema_version": 1, "profile": CANDIDATE_PROFILE, "contract_id": CONTRACT_ID,
        **{key: identity[key] for key in IDENTITY}, "observation_frame_index": observation_frame_index,
        "observation_upper_execution_tick": upper, "decision_period_ns": DECISION_PERIOD_NS,
        "server_tick_period_ns": SERVER_TICK_NS, "execution_clock": "server_tick_executed",
        "target_processing_window": {"start_execution_tick": upper+2, "end_execution_tick": upper+4},
        "contributing_support_union": {"start_execution_tick": upper+1, "end_execution_tick": upper+4},
        "target_start_after_information_bound_ns": 2*SERVER_TICK_NS,
        "target_end_after_information_bound_ns": 4*SERVER_TICK_NS,
        "exact_input_event_timing_known": False, "frame_bound_verified_by_this_function": False,
        "candidate_valid": False, "training_ready": False, "live_control_ready": False,
        "normalization_predecessor_command_row_id": None, "target_command_row_ids": [],
        "command_provenance": [], "per_command_angular_deltas_deg": [], "action": _action(None),
        "masked_channel_reasons": {"buttons_held_at_start": ["button_plane_semantics_not_calibrated"],
                                   "button_events": ["button_plane_semantics_not_calibrated",
                                                     "subtick_event_clock_not_calibrated"]}}
    reasons: set[str] = set()
    indices = []
    for tick in (upper+2, upper+3, upper+4):
        matches = [(index, row) for index, row in enumerate(commands)
                   if type(row.get("server_tick_executed")) is int and row["server_tick_executed"] == tick]
        if len(matches) != 1:
            reasons.add("missing_required_execution_tick" if not matches else "duplicate_required_execution_tick")
        else:
            indices.append(matches[0][0])
    if len(indices) != 3:
        result["reason_codes"] = sorted(reasons)
        return result
    selected = [commands[index] for index in indices]
    if indices != list(range(indices[0], indices[0]+3)):
        reasons.add("canonical_source_sequence_discontinuity")
    result["normalization_predecessor_command_row_id"] = selected[0].get("command_row_id")
    result["target_command_row_ids"] = [row.get("command_row_id") for row in selected[1:]]
    derived = []
    previous = None
    for index, row in enumerate(selected):
        local: set[str] = set()
        for name in ("command_row_id", "command_number", "client_tick", "demo_tick", "server_tick_executed", "pawn_entity_handle"):
            if type(row.get(name)) is not int or row[name] < 0:
                local.add("missing_or_invalid_"+name)
        # Unknown/missing phase state is rejected by _command_quality. The
        # canonical rows have no full pause history; the caller must check it.
        if row.get("is_paused") is True:
            local.add("command_paused")
        if previous is not None:
            if (type(previous.get("command_row_id")) is not int or type(row.get("command_row_id")) is not int
                    or row["command_row_id"] <= previous["command_row_id"]):
                local.add("command_row_id_discontinuity")
            # Tight first profile: repeated or skipped client/demo ticks are not
            # silently compressed into a 31.25 ms control period.
            for name in ("demo_tick", "client_tick", "server_tick_executed"):
                if type(previous.get(name)) is not int or type(row.get(name)) is not int or row[name] != previous[name]+1:
                    local.add(name+"_discontinuity")
        try:
            quality, values, _ = _command_quality(row, previous, identity, normalization_predecessor=index == 0)
            local |= quality
        except (KeyError, TypeError, ValueError, OverflowError):
            local.add("malformed_command_row")
            values = {}
        result["command_provenance"].append(_raw_provenance(row, valid=not local))
        reasons |= {("normalization_predecessor_" if index == 0 else "target_")+reason for reason in local}
        derived.append(values)
        previous = row
    if not reasons:
        increments = [{"command_row_id": row["command_row_id"], "yaw": float(value["delta_yaw_deg"]),
                       "pitch": float(value["delta_pitch_deg"])} for row, value in zip(selected[1:], derived[1:])]
        # Sum individually wrapped adjacent yaw differences. Wrapping the sum
        # would erase a >180-degree turn over the two-command window.
        angle = {axis: math.fsum(item[axis] for item in increments) for axis in ("yaw", "pitch")}
        result.update(candidate_valid=True, per_command_angular_deltas_deg=increments, action=_action(angle))
        result["recorded_analog_command_values"] = [
            {"command_row_id": row["command_row_id"], **{name: effective_scalar(row, name, "base_present")
                for name in ("forwardmove", "leftmove", "upmove", "weaponselect", "impulse", "mousedx_raw", "mousedy_raw")}}
            for row in selected[1:]]
    result["reason_codes"] = sorted(reasons)
    return result
