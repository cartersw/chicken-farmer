"""Label math fixtures use a private test issuer; real source proof is audited separately."""
from copy import deepcopy

import pytest

from cs2_data import control_labels as labels
from cs2_data.control_label_audit import MeasuredControlProfile, _issue_profile, canonical_row_sha256
from cs2_data.causal_acceptance import protobuf_fields
from test_calibration_buttons import row
from test_causal_acceptance import embedded, floating, integer


def command(number, planes=(8, 0, 0), steps=(), yaw=None, pitch=None):
    value = row(number, planes, steps)
    base = protobuf_fields(value["command_protobuf"])[1][0][1]
    if yaw is not None:
        base = base.replace(floating(2, float(number)), floating(2, float(yaw)))
        value["view_yaw"] = float(yaw)
    if pitch is not None:
        base = base.replace(floating(1, 1.0), floating(1, float(pitch)))
        value["view_pitch"] = float(pitch)
    value["command_protobuf"] = embedded(1, base)
    return value


def commands():
    return [command(number) for number in (100, 101, 102)]


def proof(rows, masks=None):
    return _issue_profile({"provenance_sha256": "a" * 64, "binary_profile": {"server.dll": "b" * 64},
        "supported_button_masks": labels.BUTTON_MASKS if masks is None else masks},
        {canonical_row_sha256(row) for row in rows}, {}, {})


def build(rows):
    return labels.build_control_label(rows, profile=proof(rows))


def with_client_ticks(rows, ticks):
    for row, tick in zip(rows, ticks):
        base = protobuf_fields(row["command_protobuf"])[1][0][1]
        base = base.replace(integer(2, row["client_tick"]), integer(2, tick), 1)
        row["client_tick"] = tick
        row["command_protobuf"] = embedded(1, base)
    return rows


@pytest.mark.parametrize("ticks", [(1635, 1635, 1637), (1635, 1637, 1637)])
def test_measured_generation_clock_repeats_do_not_break_consecutive_execution_commands(ticks):
    rows = with_client_ticks([command(n) for n in (908, 909, 910)], ticks)
    report = build(rows)
    assert report["label_valid"] and report["fully_observed"]
    assert [row["client_tick"] for row in report["raw_command_provenance"]] == list(ticks)
    assert report["command_interval"]["target_execution_ticks"] == [909, 910]


@pytest.mark.parametrize("ticks", [(1635, 1634, 1637), (1635, 1638, 1638)])
def test_generation_clock_reset_or_large_jump_is_outside_measured_scope(ticks):
    report = build(with_client_ticks(commands(), ticks))
    assert not report["label_valid"]
    assert "client_tick_outside_measured_generation_cadence" in report["reason_codes"]


def test_two_targets_and_predecessor_give_a_masked_field_contract_not_general_readiness():
    report = build(commands())
    assert report["profile"] == labels.PROFILE
    assert report["label_valid"] and report["fully_observed"]
    assert report["decision_period_ns"] == 31_250_000 and report["target_command_count"] == 2
    assert report["aim_delta_deg"]["yaw"] == {"value": 2.0, "valid": True, "reason_codes": []}
    assert report["buttons"]["forward"]["held_start"]["value"] is True
    assert report["buttons"]["forward"]["net_changed"]["value"] is False
    assert report["command_interval"]["target_command_row_ids"] == [101, 102]
    assert report["source_calibration"]["provenance_sha256"] == "a" * 64
    assert all(report[key] is False for key in ("training_ready", "live_control_ready", "image_alignment_verified",
        "competitive_source_verified", "exact_input_timing_verified"))
    assert all(report[key]["value"] is None and not report[key]["valid"] for key in labels.EXACT_CHANNELS)


def test_no_profile_preserves_raw_evidence_but_cannot_enable_any_label():
    report = labels.build_control_label(commands())
    assert not report["label_valid"] and not report["fully_observed"]
    assert report["source_calibration"] is None
    assert report["raw_command_provenance"][0]["command_protobuf_sha256"]
    assert not any(cell["valid"] for cell in report["aim_delta_deg"].values())
    assert "independently_measured_source_profile_unavailable" in report["reason_codes"]


@pytest.mark.parametrize("profile", [{"verified": True}, True, object()])
def test_a_caller_flag_or_dictionary_cannot_enable_semantic_labels(profile):
    with pytest.raises(ValueError, match="loader-created"):
        labels.build_control_label(commands(), profile=profile)


def test_an_unissued_object_or_replaced_profile_state_is_rejected():
    with pytest.raises(ValueError, match="not issued"):
        labels.build_control_label(commands(), profile=object.__new__(MeasuredControlProfile))
    p = proof(commands())
    object.__setattr__(p, "_data", dict(p._data))
    with pytest.raises(ValueError, match="not issued"):
        labels.build_control_label(commands(), profile=p)


@pytest.mark.parametrize("field,value", [("alive", False), ("round_id", 5), ("demo_id", "b" * 64),
                                       ("view_yaw", 0.0), ("buttons_present", None), ("extra_derived_field", 42)])
def test_profile_binds_complete_typed_source_rows_not_only_raw_protobuf(field, value):
    rows = commands(); p = proof(rows)
    rows[1][field] = value
    with pytest.raises(ValueError, match="outside the checked"):
        labels.build_control_label(rows, profile=p)


def test_missing_button_parent_masks_state_but_preserves_aim_and_other_known_boundaries():
    rows = [command(100, None), command(101), command(102)]
    report = build(rows)
    forward = report["buttons"]["forward"]
    assert report["label_valid"] and not report["fully_observed"]
    assert report["aim_delta_deg"]["yaw"]["valid"]
    assert not forward["held_start"]["valid"] and forward["held_start"]["value"] is None
    assert forward["held_mid"]["value"] is True and forward["held_end"]["value"] is True
    assert report["raw_command_provenance"][0]["buttons_present"] is False
    assert report["raw_command_provenance"][0]["button_planes_hex"] == [None, None, None]


def test_present_empty_button_parent_supplies_defaults_without_overwriting_raw_omission():
    report = build([command(n, (None, None, None)) for n in (100, 101, 102)])
    assert report["fully_observed"]
    assert all(not report["buttons"][name]["held_end"]["value"] for name in labels.BUTTON_MASKS)
    assert report["raw_command_provenance"][0]["button_scalar_presence"] == [False, False, False]
    assert report["raw_command_provenance"][0]["button_planes_hex"] == [None, None, None]


def test_target_parent_absence_keeps_net_changes_unknown_not_false():
    report = build([command(100), command(101, None), command(102)])
    f = report["buttons"]["forward"]
    assert f["held_start"]["valid"] and f["held_end"]["valid"]
    assert f["net_changed"]["value"] is None and not f["net_changed"]["valid"]
    assert f["recorded_activity_present"]["value"] is None


def test_verified_plane2_changes_can_supply_net_direction_when_predecessor_parent_is_absent():
    rows = [command(100, None), command(101, (8, 8, 0)), command(102, (8, 0, 0))]
    f = build(rows)["buttons"]["forward"]
    assert not f["held_start"]["valid"]
    assert f["net_changed"]["value"] is True and f["net_pressed"]["value"] is True
    assert f["net_released"]["value"] is False


def test_press_then_release_across_two_commands_retains_activity_despite_zero_net_change():
    rows = [command(100, (0, 0, 0)), command(101, (8, 8, 0), [(8, True, .25)]),
            command(102, (0, 8, 0), [(8, False, .75)])]
    f = build(rows)["buttons"]["forward"]
    assert [f[k]["value"] for k in ("held_start", "held_mid", "held_end")] == [False, True, False]
    assert [c["value"] for c in f["per_command_net_changed"]] == [True, True]
    assert f["net_changed"]["value"] is False and f["net_pressed"]["value"] is False and f["net_released"]["value"] is False
    assert f["recorded_activity_present"]["value"] is True
    assert f["unresolved_rapid_activity"]["value"] is False


@pytest.mark.parametrize("final_state,steps", [(0, [(8, True, 0.0), (8, False, 0.0)]), (8, [(8, True, 0.0)])])
def test_plane3_positive_tap_or_collapsed_triple_never_becomes_no_recorded_action(final_state, steps):
    rows = [command(100, (0, 0, 0)), command(101, (final_state, final_state, 8), steps),
            command(102, (final_state, 0, 0))]
    report = build(rows); f = report["buttons"]["forward"]
    assert f["recorded_activity_present"]["value"] is True and f["unresolved_rapid_activity"]["value"] is True
    assert all(report[key]["value"] is None for key in labels.EXACT_CHANNELS)
    assert report["raw_command_provenance"][1]["subtick_moves"] == rows[1]["subtick_moves"]


def test_positive_plane3_with_no_raw_subtick_list_still_preserves_unresolved_activity():
    rows = [command(100, (0, 0, 0)), command(101, (0, 0, 8)), command(102, (0, 0, 0))]
    f = build(rows)["buttons"]["forward"]
    assert f["net_changed"]["value"] is False
    assert f["recorded_activity_present"]["value"] is True and f["unresolved_rapid_activity"]["value"] is True


def test_raw_edge_can_report_activity_when_button_parent_is_absent_without_guessing_held_state():
    rows = [command(100, None), command(101, None, [(8, True, .25)]), command(102, None)]
    f = build(rows)["buttons"]["forward"]
    assert f["recorded_activity_present"]["value"] is True
    assert not f["held_mid"]["valid"] and not f["net_changed"]["valid"]
    assert not f["unresolved_rapid_activity"]["valid"]


def test_plane2_contradiction_masks_affected_button_without_erasing_other_fields():
    rows = [command(100), command(101, (8, 8, 0)), command(102)]
    report = build(rows)
    assert report["label_valid"] and report["aim_delta_deg"]["yaw"]["valid"]
    assert not report["buttons"]["forward"]["held_end"]["valid"]
    assert report["buttons"]["crouch"]["held_end"]["valid"]
    assert "plane2_disagrees_with_known_held_boundaries" in report["buttons"]["forward"]["held_end"]["reason_codes"]


def test_reload_held_net_change_does_not_invent_subtick_events():
    rows = [command(100, (0, 0, 0)), command(101, (8192, 8192, 0)), command(102, (8192, 0, 0))]
    report = build(rows); r = report["buttons"]["reload"]
    assert r["held_end"]["value"] and r["net_pressed"]["value"] and r["recorded_activity_present"]["value"]
    assert all(not c["subtick_moves"] for c in report["raw_command_provenance"])
    assert report["exact_button_event_count"]["value"] is None


def test_unsupported_profile_button_is_masked_independently():
    rows = commands(); masks = {k: v for k, v in labels.BUTTON_MASKS.items() if k != "reload"}
    report = labels.build_control_label(rows, profile=proof(rows, masks))
    assert report["buttons"]["forward"]["held_end"]["valid"]
    assert not report["buttons"]["reload"]["held_end"]["valid"]


@pytest.mark.parametrize("angles,expected", [([179, -179, -175], 6.0), ([0, 170, -20], 340.0), ([20, 10, -5], -25.0)])
def test_yaw_wraps_each_increment_without_wrapping_away_a_large_total(angles, expected):
    rows = [command(n, yaw=angle) for n, angle in zip((100, 101, 102), angles)]
    assert build(rows)["aim_delta_deg"]["yaw"]["value"] == expected


def test_pitch_changes_are_signed_degree_differences_not_mouse_counts():
    rows = [command(n, pitch=angle) for n, angle in zip((100, 101, 102), (10, 12, 9))]
    report = build(rows)
    assert report["per_command_aim_delta_deg"]["pitch"] == [2.0, -3.0]
    assert report["aim_delta_deg"]["pitch"]["value"] == -1.0


def test_exact_half_turn_masks_only_ambiguous_yaw():
    rows = [command(n, yaw=angle) for n, angle in zip((100, 101, 102), (0, 180, 170))]
    report = build(rows)
    assert not report["aim_delta_deg"]["yaw"]["valid"]
    assert report["aim_delta_deg"]["pitch"]["valid"] and report["buttons"]["forward"]["held_end"]["valid"]


def test_missing_angle_parent_masks_aim_while_button_fields_stay_usable():
    rows = commands()
    base = protobuf_fields(rows[1]["command_protobuf"])[1][0][1]
    angles = floating(1, 1.0) + floating(2, 101.0) + floating(3, 0.0)
    rows[1].update(command_protobuf=embedded(1, base.replace(embedded(4, angles), b"")),
                   viewangles_present=False, view_yaw=None, view_pitch=None, view_roll=None)
    report = build(rows)
    assert not report["aim_delta_deg"]["yaw"]["valid"] and not report["aim_delta_deg"]["pitch"]["valid"]
    assert report["buttons"]["forward"]["held_end"]["valid"]


@pytest.mark.parametrize("change", ["gap", "order", "clock", "duplicate", "pawn", "dead", "warmup", "freeze", "pause", "projection", "flags", "fraction"])
def test_bad_quality_or_discontinuous_chunks_never_bridge_into_valid_labels(change):
    rows = commands()
    if change == "gap": rows[2] = command(103)
    elif change == "order": rows = [rows[1], rows[0], rows[2]]
    elif change == "clock": rows[1]["server_tick_executed"] += 1
    elif change == "duplicate": rows[2] = deepcopy(rows[1])
    elif change == "pawn": rows[1]["pawn_entity_handle"] += 1
    elif change == "dead": rows[1]["alive"] = False
    elif change == "warmup": rows[1]["is_warmup"] = True
    elif change == "freeze": rows[1]["is_freeze_time"] = None
    elif change == "pause": rows[1]["is_paused"] = True
    elif change == "projection": rows[1]["buttonstate1"] = 16
    elif change == "fraction": rows[1] = command(101, steps=[(8, True, 1.0)])
    else:
        base = protobuf_fields(rows[1]["command_protobuf"])[1][0][1]
        rows[1]["command_protobuf"] = embedded(1, base + integer(21, 128))
    report = build(rows)
    assert not report["label_valid"] and report["reason_codes"]
    assert not any(cell["valid"] for cell in report["aim_delta_deg"].values())


@pytest.mark.parametrize("rows", [[], [None] * 3, commands()[:2], [*commands(), command(103)]])
def test_builder_requires_exactly_three_canonical_rows(rows):
    with pytest.raises(ValueError, match="exactly"):
        labels.build_control_label(rows)


def test_generated_schema_cannot_silently_enable_exact_events_or_training():
    schema = labels.control_label_schema()
    assert schema["properties"]["training_ready"] == {"const": False}
    for channel in labels.EXACT_CHANNELS:
        assert schema["properties"][channel]["properties"]["value"] == {"type": "null"}
        assert schema["properties"][channel]["properties"]["valid"] == {"const": False}
    schema["properties"]["buttons"]["properties"]["forward"]["properties"].clear()
    assert labels.control_label_schema()["properties"]["buttons"]["properties"]["forward"]["properties"]
