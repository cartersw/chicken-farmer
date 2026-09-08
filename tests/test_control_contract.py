"""Check control meaning, timing boundaries and refusal to invent button labels."""
from copy import deepcopy
import json
import struct

import pytest

from cs2_data.control_contract import (BUTTONS, CHANNELS, CONTRACT_ID, DECISION_PERIOD_NS,
                                      build_control_candidate, control_contract_schema, validate_control_action)


IDENTITY = {"demo_id": "a"*64, "round_id": 3, "steam_id": 76561198000000001, "player_slot": 4}


def vi(value):
    if value < 0:
        value += 2**64
    result = bytearray()
    while value > 127:
        result.append(value & 127 | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def integer(number, value):
    return vi(number << 3)+vi(value)


def embedded(number, payload):
    return vi(number << 3 | 2)+vi(len(payload))+payload


def floating(number, value):
    return vi(number << 3 | 5)+struct.pack("<f", value)


def command(end, yaw=0., pitch=0., flags=0, subtick=None, history=None):
    angles = floating(1, pitch)+floating(2, yaw)+floating(3, 0.)
    raw_buttons = integer(1, 1 << 55)+integer(2, 0)+integer(3, 3)
    raw = integer(1, end)+integer(2, end-3)+integer(14, 42)+embedded(3, raw_buttons)+embedded(4, angles)
    raw += floating(5, 1.)+floating(6, 0.)+floating(7, 0.)
    raw += integer(8, 0)+integer(9, 0)+integer(11, 3)+integer(12, -1)+integer(21, flags)
    if subtick is not None:
        raw += embedded(18, floating(3, subtick))
    raw = embedded(1, raw)
    if history is not None:
        raw += embedded(2, floating(5, history))
    row = {**IDENTITY, "command_row_id": end*10, "command_number": end, "client_tick": end-3,
           "server_tick_executed": end, "demo_tick": end-50, "pawn_entity_handle": 42,
           "alive": True, "is_warmup": False, "is_freeze_time": False,
           "base_present": True, "buttons_present": True, "viewangles_present": True,
           "forwardmove": 1., "leftmove": 0., "upmove": 0., "view_pitch": pitch,
           "view_yaw": yaw, "view_roll": 0., "mousedx_raw": 3, "mousedy_raw": -1,
           "impulse": 0, "weaponselect": 0, "buttonstate1": 1 << 55, "buttonstate2": 0,
           "buttonstate3": 3, "subtick_moves": [], "input_history": [], "command_protobuf": raw}
    if subtick is not None:
        row["subtick_moves"] = [{"button": None, "pressed": None, "when": subtick,
                                 "analog_forward_delta": None, "analog_left_delta": None,
                                 "pitch_delta": None, "yaw_delta": None}]
    if history is not None:
        row["input_history"] = [{"render_tick_count": None, "render_tick_fraction": history,
                                 "player_tick_count": None, "player_tick_fraction": None,
                                 "view_pitch": None, "view_yaw": None, "frame_number": None}]
    return row


def rows():
    return [command(102, 1., 3.), command(103, 4., 2.), command(104, 8., 0.)]


def candidate(values=None, upper=100):
    return build_control_candidate(values if values is not None else rows(),
        observation_upper_execution_tick=upper, identity=IDENTITY, observation_frame_index=7)


def proposed_action():
    result = candidate()["action"]
    result["buttons_held_at_start"] = {key: False for key in BUTTONS}
    result["button_events"] = []
    for channel in CHANNELS:
        result["channel_status"][channel]["available"] = True
    return result


def test_two_command_window_preserves_future_predecessor_and_masks_semantic_buttons():
    value = candidate()
    assert value["candidate_valid"]
    assert value["reason_codes"] == []
    assert value["normalization_predecessor_command_row_id"] == 1020
    assert value["target_command_row_ids"] == [1030, 1040]
    assert value["target_processing_window"] == {"start_execution_tick": 102, "end_execution_tick": 104}
    assert value["contributing_support_union"] == {"start_execution_tick": 101, "end_execution_tick": 104}
    assert value["target_start_after_information_bound_ns"] == DECISION_PERIOD_NS
    assert value["target_end_after_information_bound_ns"] == 2*DECISION_PERIOD_NS
    assert value["action"]["angular_delta_deg"] == {"yaw": 7., "pitch": -3.}
    assert value["action"]["buttons_held_at_start"] is None
    assert value["action"]["button_events"] is None
    assert value["action"]["channel_status"]["button_events"]["available"] is False
    assert validate_control_action(value["action"]) == []
    assert not value["training_ready"] and not value["live_control_ready"]
    assert not value["frame_bound_verified_by_this_function"]
    json.dumps(value, allow_nan=False)


@pytest.mark.parametrize("yaws,expected", [((179., -179., -176.), 5.), ((0., 150., -60.), 300.),
                                          ((-179., 179., 176.), -5.), ((0., -150., 60.), -300.)])
def test_yaw_wrap_is_per_transition_not_on_the_summed_rotation(yaws, expected):
    value = candidate([command(102+index, yaw) for index, yaw in enumerate(yaws)])
    assert value["candidate_valid"]
    assert value["action"]["angular_delta_deg"]["yaw"] == expected


def test_raw_planes_are_lossless_and_subticks_are_not_promoted_to_events():
    values = [command(102+index, subtick=0.5, history=0.25) for index in range(3)]
    before = deepcopy(values)
    value = candidate(values)
    assert value["candidate_valid"]
    assert values == before
    for entry, row in zip(value["command_provenance"], values):
        assert entry["raw_button_planes"] == {"buttonstate1": "0080000000000000", "buttonstate2": "0000000000000000",
                                              "buttonstate3": "0000000000000003"}
        assert entry["raw_subtick_moves"] == row["subtick_moves"]
        assert entry["raw_input_history"] == row["input_history"]
    value["command_provenance"][0]["raw_subtick_moves"][0]["when"] = 0.
    assert values[0]["subtick_moves"][0]["when"] == 0.5
    assert value["action"]["button_events"] is None


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_no_later_command_can_replace_a_missing_required_tick(missing):
    values = rows()+[command(105), command(106)]
    del values[missing]
    value = candidate(values)
    assert not value["candidate_valid"]
    assert "missing_required_execution_tick" in value["reason_codes"]
    assert value["action"]["angular_delta_deg"] is None


def test_duplicate_execution_tick_and_out_of_source_order_fail():
    assert "duplicate_required_execution_tick" in candidate(rows()+[command(103)])["reason_codes"]
    value = candidate(list(reversed(rows())))
    assert "canonical_source_sequence_discontinuity" in value["reason_codes"]
    assert not value["candidate_valid"]


@pytest.mark.parametrize("key,value,reason", [
    ("alive", False, "target_command_not_known_alive"),
    ("is_warmup", True, "target_command_is_warmup_not_false"),
    ("is_freeze_time", None, "target_command_is_freeze_time_not_false"),
    ("is_paused", True, "target_command_paused"),
    ("pawn_entity_handle", 43, "target_pawn_identity_change"),
    ("command_number", 105, "target_command_number_discontinuity"),
    ("client_tick", 102, "target_client_tick_discontinuity"),
    ("demo_tick", 52, "target_demo_tick_discontinuity"),
    ("command_row_id", 0, "target_command_row_id_discontinuity"),
    ("view_yaw", 99., "target_canonical_action_disagrees_with_protobuf"),
    ("buttons_present", False, "target_missing_buttons_present"),
    ("buttonstate1", None, "target_canonical_action_disagrees_with_protobuf"),
])
def test_command_state_clocks_and_projections_cannot_be_silently_repaired(key, value, reason):
    values = rows()
    values[1][key] = value
    result = candidate(values)
    assert not result["candidate_valid"]
    assert reason in result["reason_codes"]
    assert result["action"]["angular_delta_deg"] is None


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("kind", ["flags", "subtick", "history"])
def test_every_contributor_rejects_unsupported_flags_and_invalid_fractions(index, kind):
    values = rows()
    values[index] = command(102+index, **{kind: 1 if kind == "flags" else -0.5})
    value = candidate(values)
    assert not value["candidate_valid"]
    assert any(reason.endswith({"flags": "unsupported_cmd_flags", "subtick": "invalid_subtick_fraction",
                                "history": "invalid_history_fraction"}[kind]) for reason in value["reason_codes"])


def test_hidden_raw_subtick_is_rejected_even_when_its_projected_collection_is_cleared():
    values = rows()
    values[2] = command(104, subtick=-0.5)
    values[2]["subtick_moves"] = []
    value = candidate(values)
    assert "target_canonical_subtick_moves_disagrees_with_protobuf" in value["reason_codes"]
    assert not value["candidate_valid"]


@pytest.mark.parametrize("key", ["round_id", "steam_id", "player_slot", "demo_id"])
def test_identity_boundaries_are_not_joined(key):
    values = rows()
    values[1][key] = "different"
    with pytest.raises(ValueError, match="identity"):
        candidate(values)


@pytest.mark.parametrize("bound", [-1, 1.5, True, float("nan")])
def test_noninteger_information_bounds_are_outside_this_profile(bound):
    with pytest.raises(ValueError, match="integer observation bound"):
        candidate(upper=bound)


def test_schema_is_fresh_and_describes_the_same_fixed_period_and_buttons():
    schema = control_contract_schema()
    assert schema["properties"]["decision_period_ns"] == {"const": DECISION_PERIOD_NS}
    assert schema["properties"]["contract_id"] == {"const": CONTRACT_ID}
    assert set(schema["properties"]["buttons_held_at_start"]["anyOf"][0]["properties"]) == set(BUTTONS)
    schema["properties"].clear()
    assert control_contract_schema()["properties"]
    json.dumps(control_contract_schema(), allow_nan=False)


def test_held_state_and_ordered_transitions_express_a_tap_without_confusing_final_state():
    action = proposed_action()
    action["button_events"] = [{"offset_ns": 0, "button": "attack1", "pressed": True},
                               {"offset_ns": 10_000_000, "button": "attack1", "pressed": False}]
    assert validate_control_action(action) == []
    assert action["buttons_held_at_start"]["attack1"] is False
    action["button_events"][1]["offset_ns"] = 0
    assert validate_control_action(action) == []  # Array order resolves simultaneous records.


@pytest.mark.parametrize("event", [
    {"offset_ns": -1, "button": "attack1", "pressed": True},
    {"offset_ns": DECISION_PERIOD_NS, "button": "attack1", "pressed": True},
    {"offset_ns": True, "button": "attack1", "pressed": True},
    {"offset_ns": 0., "button": "attack1", "pressed": True},
    {"offset_ns": 0, "button": "attack1", "pressed": 1},
    {"offset_ns": 0, "button": "teleport", "pressed": True},
    {"offset_ns": 0, "button": "attack1", "pressed": True, "verified": True},
])
def test_invalid_event_offsets_types_controls_and_fields_are_rejected(event):
    action = proposed_action()
    action["button_events"] = [event]
    assert "action_event_invalid" in validate_control_action(action)


def test_event_order_and_redundant_press_are_rejected():
    action = proposed_action()
    action["button_events"] = [{"offset_ns": 2, "button": "attack1", "pressed": True},
                               {"offset_ns": 1, "button": "attack1", "pressed": True}]
    assert set(validate_control_action(action)) == {"action_events_out_of_order", "action_event_not_a_transition"}


def test_empty_events_do_not_mean_known_no_action_when_initial_state_is_unknown():
    action = candidate()["action"]
    action["button_events"] = []
    action["channel_status"]["button_events"]["available"] = True
    assert "action_events_require_initial_state" in validate_control_action(action)


@pytest.mark.parametrize("mutation,reason", [
    (lambda a: a.update(decision_period_ns=True), "action_decision_period_invalid"),
    (lambda a: a.update(schema_version=True), "action_schema_version_invalid"),
    (lambda a: a["angular_delta_deg"].update(yaw=float("inf")), "action_angular_delta_invalid"),
    (lambda a: a["angular_delta_deg"].update(yaw=10**1000), "action_angular_delta_invalid"),
    (lambda a: a["angular_delta_deg"].update(pitch=True), "action_angular_delta_invalid"),
    (lambda a: a["buttons_held_at_start"].update(attack1=1), "action_held_state_invalid"),
    (lambda a: a["channel_status"]["angular_delta_deg"].update(available=False), "action_angular_delta_deg_availability_disagrees"),
    (lambda a: a["channel_status"]["button_events"].update(calibration_status="verified"), "action_button_events_status_invalid"),
    (lambda a: a.update(verified=True), "action_fields_invalid"),
])
def test_payload_masks_calibration_and_scalar_types_are_checked(mutation, reason):
    action = proposed_action()
    mutation(action)
    assert reason in validate_control_action(action)
