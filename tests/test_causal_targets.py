"""Temporal counterexamples; fixture status strings never certify real evidence."""
from copy import deepcopy

import pytest

from cs2_data.causal_targets import select_causal_targets, verify_causal_targets


IDENTITY = {"demo_id": "source-demo", "round_id": 3, "steam_id": "76561198000000001", "player_slot": 4}
CONTRACT = {**IDENTITY, "schema_version": 1, "status": "verified", "support_interval": "[N-1,N]",
            "execution_clock": "server_tick_executed", "contract_sha256": "a" * 64}


def fixture(count=12, fractional=0.0):
    commands = [{**IDENTITY, "steam_id": int(IDENTITY["steam_id"]), "command_row_id": 1000+index,
                 "command_number": 50+index, "client_tick": 96+index,
                 "server_tick_executed": 100+index, "pawn_entity_handle": 42,
                 "subtick_moves": [{"when": 0.25, "button": 1, "pressed": True}],
                 "input_history": [{"render_tick_fraction": 0.5, "player_tick_fraction": 0.25}]}
                for index in range(count*2+3)]
    frames = [{**IDENTITY, "frame_index": index, "clock_segment_id": "segment-1",
               "observation_upper_execution_tick": 100+2*index+fractional,
               "next_observation_upper_execution_tick": 102+2*index+fractional,
               "bound_status": "verified", "contract_sha256": CONTRACT["contract_sha256"]}
              for index in range(count)]
    return commands, frames


def select(commands, frames, **kwargs):
    return select_causal_targets(commands, frames, identity=IDENTITY, support_contract=CONTRACT, **kwargs)


def test_first_full_command_uses_eight_images_and_explicit_gap_without_previous_features():
    commands, frames = fixture()
    original = deepcopy((commands, frames))
    rows = select(commands, frames)
    assert [r["observation_frame_index"] for r in rows if r["temporal_eligible"]] == [7, 8, 9, 10]
    row = rows[7]
    assert row["history_frame_indices"] == list(range(8))
    assert row["target_horizon_frame_indices"] == [7, 8]
    assert row["observation_upper_execution_tick"] == 114
    assert row["target_cutoff_execution_tick"] == 118
    assert row["target_command_row_ids"] == [1016]
    assert row["target_support"] == [{"command_row_id": 1016, "execution_clock": "server_tick_executed",
        "support_start_execution_tick": 115, "support_end_execution_tick": 116, "support_interval": "[N-1,N]"}]
    assert row["remaining_future_command_row_ids"] == [1017, 1018]
    assert row["excluded_overlap_command_row_ids"] == [1014, 1015]
    assert row["unlabelled_gap_ticks"] == 1
    assert row["target_end_delay_from_upper_bound_ticks"] == 2
    assert row["previous_action_features_included"] is False
    assert row["previous_action_command_row_ids"] == []
    assert row["normalization_predecessor_command_row_ids"] == [1015]
    assert all(r["training_ready"] is False for r in rows)
    assert (commands, frames) == original


def test_integer_end_in_future_does_not_make_overlapping_subtick_command_future():
    commands, frames = fixture(fractional=0.5)
    # The N=115 command fires at114.25 before the upper bound114.5, despite N>114.5.
    row = select(commands, frames)[7]
    assert commands[15]["server_tick_executed"] > row["observation_upper_execution_tick"]
    assert commands[15]["server_tick_executed"]-1+commands[15]["subtick_moves"][0]["when"] < row["observation_upper_execution_tick"]
    assert row["excluded_overlap_command_row_ids"] == [1015]
    assert row["target_command_row_ids"] == [1016]
    assert row["unlabelled_gap_ticks"] == 0.5
    assert row["temporal_eligible"]


def test_exact_boundary_is_excluded_without_epsilon_or_rounding_waiver():
    commands, frames = fixture()
    exact = select(commands, frames)[7]
    assert exact["target_command_row_ids"] == [1016]  # N115 starts exactly at114.
    commands2, frames2 = fixture(fractional=-1e-9)
    before = select(commands2, frames2)[7]
    assert before["target_command_row_ids"] == [1015]
    assert 0 < before["unlabelled_gap_ticks"] < 1e-8
    commands3, frames3 = fixture(fractional=1e-9)
    assert select(commands3, frames3)[7]["target_command_row_ids"] == [1016]


def test_command_crossing_deadline_is_not_split_or_assigned_to_an_earlier_image():
    commands, frames = fixture(count=1)
    frames[0].update(observation_upper_execution_tick=100.5, next_observation_upper_execution_tick=101.5)
    row = select(commands, frames, history_frames=1, target_horizon_frames=1)[0]
    assert row["target_command_row_ids"] == []
    assert "no_complete_future_command" in row["reason_codes"]
    assert row["excluded_overlap_command_row_ids"] == [1001]


def test_missing_actual_endpoint_does_not_get_a_generated_horizon():
    commands, frames = fixture()
    assert "insufficient_future_intervals" in select(commands, frames)[-1]["reason_codes"]
    frames[-1].pop("next_observation_upper_execution_tick")
    with pytest.raises(ValueError, match="bounds must be finite"):
        select(commands, frames)


@pytest.mark.parametrize("change,reason", [
    ({"subtick_moves": [{"when": -0.001}]}, "invalid_subtick_fraction"),
    ({"subtick_moves": [{"when": 1.00001}]}, "invalid_subtick_fraction"),
    ({"subtick_moves": [{"when": float("nan")}]}, "invalid_subtick_fraction"),
    ({"subtick_moves": [{"when": True}]}, "invalid_subtick_fraction"),
    ({"subtick_moves": [{"when": "0.2"}]}, "invalid_subtick_fraction"),
    ({"subtick_moves": [None]}, "invalid_subtick_moves"),
    ({"subtick_moves": None}, "missing_subtick_moves"),
    ({"input_history": [{"render_tick_fraction": float("inf")}]}, "invalid_history_fraction"),
    ({"input_history": [{"player_tick_fraction": -0.5}]}, "invalid_history_fraction"),
    ({"input_history": None}, "missing_input_history"),
])
def test_bad_selected_command_time_rejects_only_affected_candidates(change, reason):
    commands, frames = fixture()
    commands[16].update(change)
    rows = select(commands, frames)
    assert reason in rows[7]["reason_codes"]
    assert not rows[7]["temporal_eligible"]
    assert rows[8]["temporal_eligible"]
    assert rows[7]["target_command_row_ids"] == [1016]  # Do not silently choose a cleaner later command.


def test_bad_overlap_command_does_not_poison_image_history_or_become_a_feature():
    commands, frames = fixture()
    commands[15]["subtick_moves"] = [{"when": -0.3}]
    row = select(commands, frames)[7]
    assert row["temporal_eligible"]
    assert 1015 in row["excluded_overlap_command_row_ids"]
    assert 1015 not in row["target_command_row_ids"]
    assert row["previous_action_command_row_ids"] == []


def test_present_submessages_allow_defined_zero_defaults_without_rewriting_raw_rows():
    commands, frames = fixture()
    commands[16].update(subtick_moves=[{"when": None}], input_history=[{}])
    assert select(commands, frames)[7]["temporal_eligible"]
    assert commands[16]["subtick_moves"] == [{"when": None}]


@pytest.mark.parametrize("field,value,reason", [
    ("command_number", 100, "command_number_discontinuity"),
    ("client_tick", 0, "client_tick_reset"),
    ("pawn_entity_handle", 99, "pawn_identity_changed"),
])
def test_selected_command_requires_source_predecessor_continuity(field, value, reason):
    commands, frames = fixture()
    commands[16][field] = value
    assert reason in select(commands, frames)[7]["reason_codes"]


def test_missing_first_future_command_is_reported_not_silently_replaced():
    commands, frames = fixture()
    del commands[16]
    row = select(commands, frames)[7]
    assert row["target_command_row_ids"] == [1017]
    assert {"first_future_command_coverage_gap", "command_number_discontinuity", "server_tick_executed_discontinuity"}.issubset(row["reason_codes"])
    assert not row["temporal_eligible"]


@pytest.mark.parametrize("field,value", [("server_tick_executed", None), ("server_tick_executed", 0),
    ("server_tick_executed", True), ("server_tick_executed", 116.5), ("client_tick", "112"),
    ("command_number", None), ("pawn_entity_handle", -1)])
def test_unknown_command_identity_or_time_cannot_be_ordered(field, value):
    commands, frames = fixture()
    commands[16][field] = value
    with pytest.raises(ValueError, match="canonical|Unknown zero"):
        select(commands, frames)


@pytest.mark.parametrize("target", ["command_identity", "frame_identity", "duplicate_id", "execution_reversal",
                                      "frame_index", "reversed_bound", "different_contract", "nonfinite_bound"])
def test_mismatched_or_reversed_clock_records_cannot_be_used(target):
    commands, frames = fixture()
    if target == "command_identity": commands[16]["steam_id"] = 123
    elif target == "frame_identity": frames[7]["round_id"] = 5
    elif target == "duplicate_id": commands[16]["command_row_id"] = commands[15]["command_row_id"]
    elif target == "execution_reversal": commands[16]["server_tick_executed"] = 110
    elif target == "frame_index": frames[7]["frame_index"] = 6
    elif target == "reversed_bound": frames[7]["next_observation_upper_execution_tick"] = 113
    elif target == "different_contract": frames[7]["contract_sha256"] = "b" * 64
    else: frames[7]["observation_upper_execution_tick"] = float("nan")
    with pytest.raises(ValueError):
        select(commands, frames)


def test_unknown_bound_and_segment_changes_reject_only_intersecting_windows():
    commands, frames = fixture(count=25)
    frames[7]["bound_status"] = "unknown"
    frames[7]["observation_upper_execution_tick"] = None
    rows = select(commands, frames, history_frames=3)
    assert rows[5]["temporal_eligible"] and rows[10]["temporal_eligible"]
    assert all("observation_bound_unknown" in rows[i]["reason_codes"] for i in (6, 7, 8, 9))
    _, frames = fixture(count=25)
    for frame in frames[7:]: frame["clock_segment_id"] = "segment-2"
    rows = select(commands, frames, history_frames=3)
    assert all("clock_segment_boundary" in rows[i]["reason_codes"] for i in (6, 7, 8))
    assert rows[9]["temporal_eligible"]


def test_unknown_frame_cannot_hide_a_same_segment_clock_reversal():
    commands, frames = fixture()
    frames[1].update(bound_status="unknown", observation_upper_execution_tick=None,
                     next_observation_upper_execution_tick=None)
    for frame in frames[2:]:
        frame["observation_upper_execution_tick"] -= 10
        frame["next_observation_upper_execution_tick"] -= 10
    with pytest.raises(ValueError, match="clock reversal"):
        select(commands, frames)


@pytest.mark.parametrize("change", [
    {"status": "unknown"}, {"support_interval": "[N,N+1]"},
    {"execution_clock": "demo_tick"}, {"contract_sha256": None}, {"round_id": 99}, {"schema_version": True},
])
def test_boolean_approval_cannot_replace_the_supported_contract_shape(change):
    commands, frames = fixture()
    contract = {**CONTRACT, **change, "training_ready": True}
    with pytest.raises(ValueError, match="caller-verified whole-command"):
        select_causal_targets(commands, frames, identity=IDENTITY, support_contract=contract)


@pytest.mark.parametrize("defect", ["earlier_id", "later_id", "shifted_support", "changed_bound", "previous_actions", "training_ready", "boolean_schema"])
def test_recomputation_rejects_shifted_labels_and_feature_leakage(defect):
    commands, frames = fixture()
    rows = select(commands, frames)
    verify_causal_targets(rows, commands, frames, identity=IDENTITY, support_contract=CONTRACT)
    if defect == "earlier_id": rows[7]["target_command_row_ids"] = [1015]
    elif defect == "later_id": rows[7]["target_command_row_ids"] = [1017]
    elif defect == "shifted_support": rows[7]["target_support"][0]["support_start_execution_tick"] -= 1
    elif defect == "changed_bound": rows[7]["observation_upper_execution_tick"] -= 0.5
    elif defect == "previous_actions": rows[7]["previous_action_command_row_ids"] = [[1016]]
    elif defect == "training_ready": rows[7]["training_ready"] = True
    else: rows[7]["schema_version"] = True
    with pytest.raises(ValueError, match="recomputed"):
        verify_causal_targets(rows, commands, frames, identity=IDENTITY, support_contract=CONTRACT)


def test_even_consistent_fixture_assertions_never_publish_training_acceptance():
    commands, frames = fixture()
    assert any(row["temporal_eligible"] for row in select(commands, frames))
    assert not any(row["training_ready"] for row in select(commands, frames))


def strict_select(commands, frames, **kwargs):
    return select(commands, frames, require_future_predecessor=True, **kwargs)


def test_future_delta_profile_places_both_commands_after_observation_and_retains_provenance():
    commands, frames = fixture()
    original = deepcopy((commands, frames))
    row = strict_select(commands, frames)[7]
    assert row["policy"] == "first_complete_future_command_and_predecessor_v1"
    assert row["require_future_predecessor"] is True
    assert row["observation_upper_execution_tick"] == 114
    assert row["target_command_row_ids"] == [1017]
    assert row["target_support"] == [{"command_row_id": 1017, "execution_clock": "server_tick_executed",
        "support_start_execution_tick": 116, "support_end_execution_tick": 117, "support_interval": "[N-1,N]"}]
    assert row["normalization_predecessor_command_row_ids"] == [1016]
    assert row["normalization_predecessor_support"] == [{"command_row_id": 1016, "execution_clock": "server_tick_executed",
        "support_start_execution_tick": 115, "support_end_execution_tick": 116, "support_interval": "[N-1,N]"}]
    assert row["normalization_support_start_execution_tick"] == 115
    assert row["unlabelled_gap_ticks"] == 1
    assert row["target_command_start_delay_ticks"] == 2
    assert row["target_end_delay_from_upper_bound_ticks"] == 3
    assert row["remaining_future_command_row_ids"] == [1018]
    assert row["excluded_predecessor_overlap_command_row_ids"] == [1016]
    assert row["history_frame_indices"] == list(range(8))
    assert row["previous_action_command_row_ids"] == []
    assert row["previous_action_features_included"] is False
    assert row["temporal_eligible"] is True
    assert row["training_ready"] is False
    assert (commands, frames) == original


def test_legacy_selection_and_output_are_unchanged_when_option_is_absent_or_false():
    commands, frames = fixture()
    implicit = select(commands, frames)
    explicit = select(commands, frames, require_future_predecessor=False)
    assert implicit == explicit
    assert implicit[7]["target_command_row_ids"] == [1016]
    assert implicit[7]["normalization_predecessor_command_row_ids"] == [1015]
    assert "normalization_predecessor_support" not in implicit[7]


@pytest.mark.parametrize("flag", [None, 0, 1, "true", [], {}])
def test_future_predecessor_profile_requires_an_explicit_boolean(flag):
    commands, frames = fixture()
    with pytest.raises(ValueError, match="must be a boolean"):
        select(commands, frames, require_future_predecessor=flag)


def test_old_target_is_future_but_its_delta_predecessor_still_overlaps_the_image_bound():
    commands, frames = fixture(fractional=0.5)
    old, new = select(commands, frames)[7], strict_select(commands, frames)[7]
    assert old["target_support"][0]["support_start_execution_tick"] > old["observation_upper_execution_tick"]
    predecessor = next(row for row in commands if row["command_row_id"] == old["normalization_predecessor_command_row_ids"][0])
    assert predecessor["server_tick_executed"]-1 < old["observation_upper_execution_tick"]
    assert new["target_command_row_ids"] == [1017]
    assert new["normalization_predecessor_support"][0]["support_start_execution_tick"] > new["observation_upper_execution_tick"]
    assert new["unlabelled_gap_ticks"] == 0.5


def test_predecessor_exact_boundary_is_excluded_without_rounding_or_epsilon():
    commands, frames = fixture()
    assert strict_select(commands, frames)[7]["target_command_row_ids"] == [1017]
    commands, frames = fixture(fractional=-1e-9)
    before = strict_select(commands, frames)[7]
    assert before["target_command_row_ids"] == [1016]
    assert 0 < before["unlabelled_gap_ticks"] < 1e-8
    commands, frames = fixture(fractional=1e-9)
    assert strict_select(commands, frames)[7]["target_command_row_ids"] == [1017]


def test_complete_pair_must_fit_actual_horizon_without_fabricating_an_extra_frame():
    commands, frames = fixture()
    row = strict_select(commands, frames, target_horizon_frames=1)[7]
    assert row["target_cutoff_execution_tick"] == 116
    assert row["target_command_row_ids"] == []
    assert row["excluded_predecessor_overlap_command_row_ids"] == [1016]
    assert "no_complete_future_command_with_future_predecessor" in row["reason_codes"]
    assert "insufficient_future_intervals" in strict_select(commands, frames)[-1]["reason_codes"]


def test_partial_target_at_fractional_endpoint_is_not_split_for_aim_delta():
    commands, _ = fixture()
    _, frames = fixture(count=1)
    frames[0].update(observation_upper_execution_tick=114, next_observation_upper_execution_tick=116.75)
    row = strict_select(commands, frames, history_frames=1, target_horizon_frames=1)[0]
    assert row["target_command_row_ids"] == []
    assert "no_complete_future_command_with_future_predecessor" in row["reason_codes"]


def test_missing_first_future_predecessor_does_not_silently_shift_the_delta_pair():
    commands, frames = fixture()
    del commands[16]
    row = strict_select(commands, frames)[7]
    assert row["target_command_row_ids"] == [1018]
    assert row["normalization_predecessor_command_row_ids"] == [1017]
    assert "first_future_predecessor_coverage_gap" in row["reason_codes"]
    assert row["temporal_eligible"] is False


def test_missing_first_target_cannot_turn_a_two_tick_delta_into_a_consecutive_delta():
    commands, frames = fixture()
    del commands[17]
    row = strict_select(commands, frames)[7]
    assert row["target_command_row_ids"] == [1018]
    assert row["normalization_predecessor_command_row_ids"] == [1016]
    assert {"command_number_discontinuity", "server_tick_executed_discontinuity"}.issubset(row["reason_codes"])
    assert row["temporal_eligible"] is False


def test_valid_normalization_pair_does_not_require_a_third_command_outside_its_support():
    commands, frames = fixture()
    # E116 is the first retained canonical row. E117 can still use E116's
    # known base orientation; E115 is not needed to form that difference.
    commands = commands[16:]
    row = strict_select(commands, frames)[7]
    assert row["target_command_row_ids"] == [1017]
    assert row["normalization_predecessor_command_row_ids"] == [1016]
    assert row["excluded_missing_predecessor_command_row_ids"] == [1016]
    assert row["temporal_eligible"] is True


@pytest.mark.parametrize("change,reason", [
    ({"subtick_moves": [{"when": -0.01}]}, "normalization_predecessor_invalid_subtick_fraction"),
    ({"subtick_moves": [{"when": 1.01}]}, "normalization_predecessor_invalid_subtick_fraction"),
    ({"subtick_moves": None}, "normalization_predecessor_missing_subtick_moves"),
    ({"input_history": [{"render_tick_fraction": float("nan")}]}, "normalization_predecessor_invalid_history_fraction"),
])
def test_predecessor_support_quality_rejects_pair_without_skipping_to_a_cleaner_target(change, reason):
    commands, frames = fixture()
    commands[16].update(change)
    rows = strict_select(commands, frames)
    assert rows[7]["target_command_row_ids"] == [1017]
    assert reason in rows[7]["reason_codes"]
    assert rows[7]["temporal_eligible"] is False
    assert rows[8]["temporal_eligible"] is True


def test_strict_profile_preserves_target_quality_checks_and_ignores_unselected_overlap_values():
    commands, frames = fixture()
    commands[15]["subtick_moves"] = [{"when": -0.2}]
    assert strict_select(commands, frames)[7]["temporal_eligible"] is True
    commands[17]["subtick_moves"] = [{"when": -0.2}]
    rows = strict_select(commands, frames)
    assert "invalid_subtick_fraction" in rows[7]["reason_codes"]
    assert rows[7]["target_command_row_ids"] == [1017]
    assert rows[8]["temporal_eligible"] is True


def test_strict_profile_preserves_observation_history_and_segment_gates():
    commands, frames = fixture(count=25)
    frames[7]["bound_status"] = "unknown"
    frames[7]["observation_upper_execution_tick"] = None
    rows = strict_select(commands, frames, history_frames=3)
    assert all("observation_bound_unknown" in rows[i]["reason_codes"] for i in (6, 7, 8, 9))
    assert rows[10]["temporal_eligible"] is True
    commands, frames = fixture(count=25)
    for frame in frames[7:]:
        frame["clock_segment_id"] = "segment-2"
    rows = strict_select(commands, frames, history_frames=3)
    assert all("clock_segment_boundary" in rows[i]["reason_codes"] for i in (6, 7, 8))
    assert rows[9]["temporal_eligible"] is True


def test_same_tick_adjacent_source_commands_use_actual_support_not_invented_extra_tick():
    commands, frames = fixture()
    # Multiple adjacent source commands may have the same enclosing server tick.
    # Both supports are still strictly future; the helper does not shift one.
    commands[17]["server_tick_executed"] = 116
    row = strict_select(commands, frames)[7]
    assert row["target_command_row_ids"] == [1017]
    assert row["target_support"][0]["support_end_execution_tick"] == 116
    assert row["normalization_predecessor_support"][0]["support_end_execution_tick"] == 116
    assert row["temporal_eligible"] is True


@pytest.mark.parametrize("defect", ["predecessor_id", "predecessor_support", "target_id", "previous_feature", "policy", "gap"])
def test_strict_profile_recomputation_rejects_shifted_delta_or_leaked_predecessor(defect):
    commands, frames = fixture()
    rows = strict_select(commands, frames)
    kwargs = {"identity": IDENTITY, "support_contract": CONTRACT, "require_future_predecessor": True}
    verify_causal_targets(rows, commands, frames, **kwargs)
    if defect == "predecessor_id": rows[7]["normalization_predecessor_command_row_ids"] = [1015]
    elif defect == "predecessor_support": rows[7]["normalization_predecessor_support"][0]["support_start_execution_tick"] -= 1
    elif defect == "target_id": rows[7]["target_command_row_ids"] = [1016]
    elif defect == "previous_feature": rows[7]["previous_action_command_row_ids"] = [1016]
    elif defect == "policy": rows[7]["policy"] = "first_complete_future_command_v1"
    else: rows[7]["unlabelled_gap_ticks"] = 2
    with pytest.raises(ValueError, match="recomputed"):
        verify_causal_targets(rows, commands, frames, **kwargs)


def test_strict_profile_is_not_interchangeable_with_legacy_recomputation():
    commands, frames = fixture()
    with pytest.raises(ValueError, match="recomputed"):
        verify_causal_targets(strict_select(commands, frames), commands, frames,
                              identity=IDENTITY, support_contract=CONTRACT)
