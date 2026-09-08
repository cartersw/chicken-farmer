"""Partially masked 32 Hz labels under independently measured local source proof.

This is a new label profile. It does not change control-contract v1, approve
competitive footage, assign physical event times or issue live input commands.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import math
import re
from typing import Any, Sequence

from .causal_acceptance import _action_protobuf
from .normalize import effective_scalar, wrap_angle

PROFILE = "cs2-scoped-control-label-32hz-v1"
DECISION_PERIOD_NS = 31_250_000
SERVER_TICK_PERIOD_NS = 15_625_000
BUTTON_MASKS = {"forward": 8, "back": 16, "left": 512, "right": 1024,
                "crouch": 4, "jump": 2, "attack1": 1, "reload": 8192}
BUTTON_FIELDS = ("held_start", "held_mid", "held_end", "net_changed", "net_pressed", "net_released",
                 "recorded_activity_present", "unresolved_rapid_activity")
EXACT_CHANNELS = ("exact_button_event_count", "exact_button_event_order", "exact_button_event_offsets_ns")
IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot", "pawn_entity_handle")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _cell(value=None, reasons=()):
    reasons = sorted(set(reasons))
    return {"value": None if reasons else value, "valid": not reasons and value is not None,
            "reason_codes": reasons}


def _masked(reason):
    return _cell(reasons=[reason])


def _tri_or(cells, unavailable):
    if any(cell["valid"] and cell["value"] for cell in cells):
        return _cell(True)
    if all(cell["valid"] for cell in cells):
        return _cell(False)
    return _masked(unavailable)


def _raw(row):
    protobuf = row.get("command_protobuf")
    result = {key: deepcopy(row.get(key)) for key in (*IDENTITY, "command_row_id", "command_number", "client_tick",
        "demo_tick", "server_tick_executed", "alive", "is_warmup", "is_freeze_time", "is_paused", "base_present",
        "buttons_present", "viewangles_present", "view_yaw", "view_pitch", "view_roll", "subtick_moves", "input_history")}
    result["button_planes_hex"] = [f"0x{row['buttonstate'+str(i)]:016x}" if type(row.get('buttonstate'+str(i))) is int
        and 0 <= row['buttonstate'+str(i)] < 2**64 else None for i in (1, 2, 3)]
    result["button_scalar_presence"] = [row.get("buttonstate" + str(i)) is not None for i in (1, 2, 3)]
    result["command_protobuf_base64"] = base64.b64encode(protobuf).decode("ascii") if isinstance(protobuf, bytes) else None
    result["command_protobuf_sha256"] = hashlib.sha256(protobuf).hexdigest() if isinstance(protobuf, bytes) else None
    return result


def _chunk_reasons(rows):
    reasons = set()
    identity = tuple(rows[0].get(key) for key in IDENTITY)
    for index, row in enumerate(rows):
        if tuple(row.get(key) for key in IDENTITY) != identity:
            reasons.add("command_identity_changed")
        if (not isinstance(row.get("demo_id"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["demo_id"]) or
            type(row.get("round_id")) is not int or row["round_id"] <= 0 or type(row.get("steam_id")) is not int or
            not 0 < row["steam_id"] < 2**64 or type(row.get("player_slot")) is not int or row["player_slot"] < 0):
            reasons.add("command_identity_unavailable_or_invalid")
        for name in ("command_row_id", "command_number", "client_tick", "demo_tick", "server_tick_executed", "pawn_entity_handle"):
            if type(row.get(name)) is not int or row[name] < 0:
                reasons.add("missing_or_invalid_" + name)
        if row.get("server_tick_executed") == 0:
            reasons.add("unavailable_execution_tick")
        if row.get("alive") is not True:
            reasons.add("command_not_known_alive")
        for name in ("is_warmup", "is_freeze_time"):
            if row.get(name) is not False:
                reasons.add("command_" + name + "_not_false")
        if row.get("is_paused") is True:
            reasons.add("command_paused")
        raw_reasons, _ = _action_protobuf(row)
        reasons |= raw_reasons
        for name in ("subtick_moves", "input_history"):
            collection = row.get(name)
            if not isinstance(collection, list):
                reasons.add("missing_" + name)
                continue
            for item in collection:
                if not isinstance(item, dict):
                    reasons.add("invalid_" + name)
                    continue
                fractions = ("when",) if name == "subtick_moves" else ("render_tick_fraction", "player_tick_fraction")
                for field in fractions:
                    value = item.get(field)
                    if value is not None and (not _finite(value) or not 0 <= value < 1):
                        reasons.add("unsupported_raw_fraction")
                if name == "subtick_moves":
                    bit, pressed = item.get("button"), item.get("pressed")
                    if bit is not None and (type(bit) is not int or not 0 <= bit < 2**64):
                        reasons.add("invalid_raw_subtick_button")
                    if pressed is not None and type(pressed) is not bool:
                        reasons.add("invalid_raw_subtick_pressed")
        if index:
            previous = rows[index - 1]
            for clock in ("command_number", "demo_tick", "server_tick_executed"):
                if type(row.get(clock)) is not int or type(previous.get(clock)) is not int or row[clock] != previous[clock] + 1:
                    reasons.add(clock + "_discontinuity")
            # At the measured 32 Hz render cadence this generation clock can
            # repeat and then advance by two while command/execution ticks
            # each advance once. Preserve that independent clock verbatim;
            # resets or larger jumps fall outside this measured local scope.
            if (type(row.get("client_tick")) is not int or type(previous.get("client_tick")) is not int or
                not 0 <= row["client_tick"] - previous["client_tick"] <= 2):
                reasons.add("client_tick_outside_measured_generation_cadence")
            if (type(row.get("command_row_id")) is not int or type(previous.get("command_row_id")) is not int or
                row["command_row_id"] <= previous["command_row_id"]):
                reasons.add("canonical_source_order_not_strictly_increasing")
    return reasons


def _plane_bit(row, plane, mask):
    if row.get("buttons_present") is not True:
        return _masked("button_parent_absent")
    value = effective_scalar(row, "buttonstate" + str(plane), "buttons_present")
    if type(value) is not int or not 0 <= value < 2**64:
        return _masked("invalid_button_plane")
    return _cell(bool(value & mask))


def _button(rows, mask):
    held = [_plane_bit(row, 1, mask) for row in rows]
    changes = [_plane_bit(row, 2, mask) for row in rows[1:]]
    rapid = [_plane_bit(row, 3, mask) for row in rows[1:]]
    result = {"held_start": held[0], "held_mid": held[1], "held_end": held[2], "per_command_net_changed": changes}
    for index, change in enumerate(changes):
        if held[index]["valid"] and held[index + 1]["valid"] and change["valid"] and (
            held[index]["value"] ^ held[index + 1]["value"]) != change["value"]:
            reason = "plane2_disagrees_with_known_held_boundaries"
            return {**{field: _masked(reason) for field in BUTTON_FIELDS},
                    "per_command_net_changed": [_masked(reason), _masked(reason)]}
    net = (_cell(changes[0]["value"] ^ changes[1]["value"]) if all(c["valid"] for c in changes)
           else _masked("target_button_parent_absent_for_net_change"))
    result["net_changed"] = net
    for name, end in (("net_pressed", True), ("net_released", False)):
        result[name] = (_cell(False) if net["valid"] and not net["value"] else
                        _cell(held[2]["value"] is end) if net["valid"] and held[2]["valid"] else
                        _masked("net_change_or_end_state_unavailable"))
    raw_activity = any(type(item.get("button")) is int and item["button"] & mask
                       for row in rows[1:] for item in row["subtick_moves"])
    result["unresolved_rapid_activity"] = _tri_or(rapid, "target_button_parent_absent_for_plane3")
    result["recorded_activity_present"] = (_cell(True) if raw_activity else
        _tri_or([*changes, *rapid], "target_button_parent_absent_for_activity"))
    return result


def _aim(rows, axis):
    field = "view_" + axis
    if any(row.get("viewangles_present") is not True for row in rows):
        return _masked("viewangles_parent_absent"), []
    values = [effective_scalar(row, field, "viewangles_present") for row in rows]
    if not all(_finite(value) for value in values):
        return _masked("nonfinite_view_" + axis), []
    deltas = [(wrap_angle(b - a) if axis == "yaw" else b - a) for a, b in zip(values, values[1:])]
    if not all(_finite(value) for value in deltas):
        return _masked("nonfinite_delta_" + axis), []
    if axis == "yaw" and any(abs(delta) == 180 for delta in deltas):
        return _masked("ambiguous_half_turn_direction"), deltas
    total = math.fsum(deltas)
    return _cell(float(total)), deltas


def build_control_label(commands: Sequence[dict[str, Any]], *, profile=None) -> dict[str, Any]:
    """Build a 32 Hz partial label from exactly predecessor + two 64 Hz commands.

    The predecessor supplies the initial boundary, not an extra target tick.
    Each target's plane 2 is a scoped net boundary-change bit. The aggregate net
    change is their XOR; it is not an event count. Plane-3 activity and raw edge
    records prevent a same-end-state tap from being labeled as no recorded action.

    A sealed, freshly checked source profile is required to unmask fields. Even
    then, missing parent messages stay masked. No image or competitive proof is
    established here, and the old all-unmeasured v1 contract remains unchanged.
    """
    if not isinstance(commands, (list, tuple)) or len(commands) != 3 or any(not isinstance(r, dict) for r in commands):
        raise ValueError("A control label requires exactly predecessor, first target and second target commands")
    rows = list(commands)
    calibration = None
    if profile is not None:
        from .control_label_audit import MeasuredControlProfile
        if not isinstance(profile, MeasuredControlProfile):
            raise ValueError("Control labels require a loader-created measured profile, not a verification flag")
        profile.require_checked()
        profile.require_command_rows(rows)
        calibration = {"profile": profile.profile_id, "domain": profile.domain,
            "provenance_sha256": profile.provenance_sha256, "binary_profile": dict(profile.binary_profile)}
    reasons = _chunk_reasons(rows)
    if profile is None:
        reasons.add("independently_measured_source_profile_unavailable")
    result = {"schema_version": 1, "profile": PROFILE, "decision_period_ns": DECISION_PERIOD_NS,
        "server_tick_period_ns": SERVER_TICK_PERIOD_NS, "target_command_count": 2,
        "label_valid": False, "fully_observed": False, "training_ready": False, "live_control_ready": False,
        "image_alignment_verified": False, "competitive_source_verified": False, "exact_input_timing_verified": False,
        "source_calibration": calibration, "reason_codes": sorted(reasons),
        "command_interval": {"predecessor_execution_tick": rows[0].get("server_tick_executed"),
            "target_execution_ticks": [row.get("server_tick_executed") for row in rows[1:]],
            "predecessor_command_row_id": rows[0].get("command_row_id"),
            "target_command_row_ids": [row.get("command_row_id") for row in rows[1:]],
            "basis": "two_consecutive_recorded_command_boundaries_not_physical_event_times"},
        "aim_delta_deg": {}, "per_command_aim_delta_deg": {}, "buttons": {},
        **{name: _masked("exact_event_count_order_and_time_not_calibrated") for name in EXACT_CHANNELS},
        "raw_command_provenance": [_raw(row) for row in rows],
        "limits": ["A valid partial label does not imply that every field is available.",
            "Scalar defaults apply only inside raw-verified present parent messages.",
            "Held states are recorded command-boundary states, not exact physical key-down intervals.",
            "Net changes do not encode how many transitions occurred or their order.",
            "Plane-3 positives and raw subticks preserve recorded activity without inventing event times.",
            "No scoped calibration matrix or Windows count conversion is applied to recorded degree targets.",
            "Image causality, complete competitive round/phase proof and broader build compatibility remain separate."]}
    for axis in ("yaw", "pitch"):
        cell, deltas = (_cell(reasons=reasons), []) if reasons else _aim(rows, axis)
        result["aim_delta_deg"][axis] = cell
        result["per_command_aim_delta_deg"][axis] = deltas
    for name, mask in BUTTON_MASKS.items():
        local = set(reasons)
        if profile is not None and profile.supported_button_masks.get(name) != mask:
            local.add("button_not_supported_by_measured_profile")
        result["buttons"][name] = ({**{field: _cell(reasons=local) for field in BUTTON_FIELDS},
                                    "per_command_net_changed": [_cell(reasons=local), _cell(reasons=local)]}
                                   if local else _button(rows, mask))
    fields = [*result["aim_delta_deg"].values(), *(cell for button in result["buttons"].values()
              for name, cell in button.items() if name != "per_command_net_changed"),
              *(cell for button in result["buttons"].values() for cell in button["per_command_net_changed"])]
    result["label_valid"] = not reasons and any(cell["valid"] for cell in fields)
    result["fully_observed"] = result["label_valid"] and all(cell["valid"] for cell in fields)
    result["status"] = "fully_observed_scoped_label" if result["fully_observed"] else "partially_observed_scoped_label" if result["label_valid"] else "fully_masked_label"
    return result


def control_label_schema():
    """JSON Schema for published label fields; proof verification remains separate."""
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    def cell(kind):
        return obj({"value": {"type": [kind, "null"]}, "valid": {"type": "boolean"},
                    "reason_codes": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}})
    masked = obj({"value": {"type": "null"}, "valid": {"const": False},
                  "reason_codes": {"type": "array", "minItems": 1, "items": {"type": "string"}}})
    button = obj({**{name: cell("boolean") for name in BUTTON_FIELDS},
                  "per_command_net_changed": {"type": "array", "minItems": 2, "maxItems": 2, "items": cell("boolean")}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Scoped partial CS2 command labels; training acceptance unavailable",
        **obj({"schema_version": {"const": 1}, "profile": {"const": PROFILE},
            "decision_period_ns": {"const": DECISION_PERIOD_NS}, "server_tick_period_ns": {"const": SERVER_TICK_PERIOD_NS},
            "target_command_count": {"const": 2}, "label_valid": {"type": "boolean"}, "fully_observed": {"type": "boolean"},
            **{name: {"const": False} for name in ("training_ready", "live_control_ready", "image_alignment_verified",
                "competitive_source_verified", "exact_input_timing_verified")},
            "status": {"enum": ["fully_observed_scoped_label", "partially_observed_scoped_label", "fully_masked_label"]},
            "source_calibration": {"type": ["object", "null"]}, "reason_codes": {"type": "array", "items": {"type": "string"}},
            "command_interval": {"type": "object"}, "aim_delta_deg": obj({axis: cell("number") for axis in ("yaw", "pitch")}),
            "per_command_aim_delta_deg": obj({axis: {"type": "array", "maxItems": 2, "items": {"type": "number"}} for axis in ("yaw", "pitch")}),
            "buttons": obj({name: deepcopy(button) for name in BUTTON_MASKS}), **{name: deepcopy(masked) for name in EXACT_CHANNELS},
            "raw_command_provenance": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "object"}},
            "limits": {"type": "array", "items": {"type": "string"}}})}
