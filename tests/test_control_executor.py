"""Pure compiler tests; deliberately issued toy profiles never send input."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json

import pytest

from cs2_data.control_contract import BUTTONS, CHANNELS, CONTRACT_ID, DECISION_PERIOD_NS
from cs2_data.control_executor import (ControlExecutor, MAX_CONTINUOUS_HOLD_NS, PreparedDecision,
                                       _nearest_signed, compile_sequence)
from cs2_data.control_label_audit import MeasuredControlProfile, _issue_profile


def profile(**overrides):
    # This private issuer is only a unit-test fixture; production callers must
    # obtain the typed object from the full original-evidence proof loader.
    data = {"schema_version": 1, "profile": "cs2-local-measured-controls-v1",
            "domain": "local_controlled_calibration", "provenance_sha256": "c" * 64,
            "degrees_per_mouse_count": [[-0.02, 0.0], [0.0, 0.04]],
            "mouse_counts_per_degree": [[-50.0, 0.0], [0.0, 25.0]],
            "binary_profile": {"bin/win64/engine2.dll": "a" * 64}, "sensitivity": 1.0,
            "provenance": {"test_fixture_only": True}, "supported_button_masks": {},
            "ready_requirements": {"sensitivity": "1", "m_yaw": "0.022", "m_pitch": "0.022"},
            "training_ready": False, "live_control_ready": False, "exact_input_timing_verified": False}
    data.update(overrides)
    return _issue_profile(data, set(), {}, {})


def action(*, yaw=0.0, pitch=0.0, held=(), events=()):
    return {"schema_version": 1, "contract_id": CONTRACT_ID,
            "decision_period_ns": DECISION_PERIOD_NS,
            "angular_delta_deg": {"yaw": yaw, "pitch": pitch},
            "buttons_held_at_start": {name: name in held for name in BUTTONS},
            "button_events": [dict(event) for event in events],
            "channel_status": {name: {"available": True, "calibration_status": "unmeasured"} for name in CHANNELS}}


def edge(button, pressed, offset=0):
    return {"button": button, "pressed": pressed, "offset_ns": offset}


def apply(executor, value):
    prepared = executor.prepare(value)
    result = prepared.to_dict()
    executor.commit(prepared)
    return result


def test_requires_typed_source_issued_profile_not_serialized_verified_flag():
    with pytest.raises(TypeError, match="issued MeasuredControlProfile"):
        ControlExecutor({**profile().to_dict(), "verified": True})
    with pytest.raises(ValueError, match="not issued"):
        ControlExecutor(object.__new__(MeasuredControlProfile))


@pytest.mark.parametrize("overrides", [
    {"mouse_counts_per_degree": [[1, 0], [0, 1]]},
    {"mouse_counts_per_degree": [[0, 0], [0, 0]]},
    {"mouse_counts_per_degree": [[float("nan"), 0], [0, 25]]},
    {"degrees_per_mouse_count": [1, 2]},
    {"sensitivity": 0}, {"sensitivity": True},
    {"binary_profile": {}}, {"binary_profile": {"engine": "not-a-hash"}},
    {"provenance_sha256": "not-a-hash"}])
def test_rejects_inconsistent_or_incomplete_calibration_profile(overrides):
    with pytest.raises(ValueError):
        ControlExecutor(profile(**overrides))


def test_prepare_is_staged_and_returned_views_are_detached():
    executor = ControlExecutor(profile())
    original = executor.state
    value = action(yaw=0.02, events=[edge("forward", True)])
    before = deepcopy(value)
    prepared = executor.prepare(value)
    assert executor.state == original and value == before
    mutable = prepared.to_dict()
    mutable["events"][0]["event"]["dx"] = 999
    mutable["held_after"]["forward"] = False
    value["button_events"].clear()
    assert prepared.events[0]["event"]["dx"] == -1
    executor.commit(prepared)
    assert executor.state["held"]["forward"] is True
    assert executor.state["emitted_mouse_count_totals"] == [-1, 0]
    assert executor.state["next_offset_ns"] == DECISION_PERIOD_NS


def test_failed_dispatch_discard_does_not_consume_residual_or_advance_state():
    executor = ControlExecutor(profile())
    prepared = executor.prepare(action(yaw=0.01))
    original_events = prepared.events
    executor.discard(prepared)
    assert executor.state["next_decision_index"] == 0
    assert executor.state["mouse_count_residual"] == [0, 0]
    again = executor.prepare(action(yaw=0.01))
    assert again.events == original_events
    executor.commit(again)
    assert executor.state["emitted_mouse_count_totals"] == [-1, 0]


def test_stale_copied_and_cross_executor_decisions_cannot_commit():
    executor, other = ControlExecutor(profile()), ControlExecutor(profile())
    prepared = executor.prepare(action())
    with pytest.raises(RuntimeError, match="Commit or discard"):
        executor.prepare(action())
    with pytest.raises(ValueError, match="stale"):
        executor.commit(replace(prepared))
    with pytest.raises(ValueError, match="stale"):
        other.commit(prepared)
    executor.commit(prepared)
    with pytest.raises(ValueError, match="stale"):
        executor.commit(prepared)


def test_altered_prepared_document_is_rejected_without_state_change():
    executor = ControlExecutor(profile())
    prepared = executor.prepare(action())
    object.__setattr__(prepared, "_document_json", "{}")
    with pytest.raises(ValueError, match="altered"):
        executor.commit(prepared)
    assert executor.state["next_decision_index"] == 0


@pytest.mark.parametrize("name", CHANNELS)
def test_unavailable_channel_is_not_silently_treated_as_zero(name):
    executor = ControlExecutor(profile())
    value = action()
    value[name] = None
    value["channel_status"][name]["available"] = False
    if name == "buttons_held_at_start":
        value["button_events"] = None
        value["channel_status"]["button_events"]["available"] = False
    with pytest.raises(ValueError, match="available values"):
        executor.prepare(value)
    assert executor.state["next_decision_index"] == 0


@pytest.mark.parametrize("button", ["walk", "attack2", "use", "drop", "scoreboard"])
def test_unsupported_controls_are_rejected_even_with_well_formed_actions(button):
    executor = ControlExecutor(profile())
    with pytest.raises(ValueError, match="unsupported"):
        executor.prepare(action(held=[button]))
    with pytest.raises(ValueError, match="Unsupported"):
        executor.prepare(action(events=[edge(button, True)]))


def test_held_state_is_continuity_not_an_implicit_press_request():
    executor = ControlExecutor(profile())
    with pytest.raises(ValueError, match="held-at-start"):
        executor.prepare(action(held=["forward"]))
    apply(executor, action(events=[edge("forward", True)]))
    with pytest.raises(ValueError, match="held-at-start"):
        executor.prepare(action())
    assert apply(executor, action(held=["forward"]))["events"] == []
    released = apply(executor, action(held=["forward"], events=[edge("forward", False, 10)]))
    assert released["events"] == [{"offset_ns": 10, "event": {"kind": "key", "key": "W", "pressed": False}}]


@pytest.mark.parametrize("semantic,kind,name", [
    ("forward", "key", "W"), ("back", "key", "S"), ("left", "key", "A"), ("right", "key", "D"),
    ("jump", "key", "SPACE"), ("crouch", "key", "LCTRL"), ("attack1", "mouse_button", "left"), ("reload", "key", "R")])
def test_supported_semantic_transitions_map_to_calibrated_controls(semantic, kind, name):
    result = compile_sequence([action(events=[edge(semantic, True, 7), edge(semantic, False, 100)])], profile())
    assert [event["decision_offset_ns"] for event in result["events"]] == [7, 100]
    field = "key" if kind == "key" else "button"
    assert [event["event"] for event in result["events"]] == [
        {"kind": kind, field: name, "pressed": True}, {"kind": kind, field: name, "pressed": False}]


@pytest.mark.parametrize("value,expected", [(Fraction(1, 2), 1), (Fraction(-1, 2), -1),
    (Fraction(3, 2), 2), (Fraction(-3, 2), -2), (Fraction(49, 100), 0), (Fraction(-49, 100), 0)])
def test_signed_rounding_ties_and_signs(value, expected):
    assert _nearest_signed(value) == expected


def test_fractional_carry_accumulates_small_actions_without_zero_step_oscillation():
    executor = ControlExecutor(profile())
    first = apply(executor, action(yaw=0.01))  # -0.5 counts => -1, +0.5 remainder.
    assert first["emitted_mouse_counts"] == [-1, 0]
    for _ in range(20):
        assert apply(executor, action())["events"] == []
    assert executor.state["mouse_count_residual"] == [0.5, 0.0]
    second = apply(executor, action(yaw=0.01))
    assert second["emitted_mouse_counts"] == [0, 0]
    assert executor.state["mouse_count_residual"] == [0.0, 0.0]
    # The accumulated desired -1 count was emitted exactly once.
    assert executor.state["emitted_mouse_count_totals"] == [-1, 0]


def test_subcount_sequence_emits_nearest_total_not_all_zero():
    result = compile_sequence([action(yaw=0.002) for _ in range(100)], profile())
    assert result["final_state"]["emitted_mouse_count_totals"] == [-10, 0]
    assert result["event_count"] == 10
    assert abs(result["final_state"]["mouse_count_residual"][0]) < 1e-12


def test_off_diagonal_inverse_is_applied_in_yaw_pitch_order():
    measured = profile(mouse_counts_per_degree=[[2, 1], [-1, 3]],
                       degrees_per_mouse_count=[[3/7, -1/7], [1/7, 2/7]])
    result = compile_sequence([action(yaw=2, pitch=1)], measured)
    assert result["events"][0]["event"] == {"kind": "mouse_move", "dx": 5, "dy": 1}
    assert result["decisions"][0]["predicted_emitted_angular_delta_deg"] == pytest.approx({"yaw": 2, "pitch": 1})


def test_finite_huge_angular_values_fail_without_changing_compiler_state():
    measured = profile(mouse_counts_per_degree=[[1, 1], [0, 1]], degrees_per_mouse_count=[[1, -1], [0, 1]])
    executor = ControlExecutor(measured)
    before = executor.state
    with pytest.raises(ValueError, match="overflow"):
        executor.prepare(action(yaw=1e308, pitch=1e308))
    assert executor.state == before
    assert apply(executor, action())["events"] == []


def test_exact_nanosecond_mapping_preserves_subdecision_event_offsets():
    result = compile_sequence([action(), action(yaw=0.02),
        action(events=[edge("jump", True, 7), edge("jump", False, DECISION_PERIOD_NS-1)])], profile())
    assert result["duration_ns"] == 93_750_000
    assert [event["offset_ns"] for event in result["events"]] == [31_250_000, 62_500_007, 93_749_999]
    assert [event["decision_index"] for event in result["events"]] == [1, 2, 2]
    assert result["events"][2]["decision_event_index"] == 1


def test_equal_offset_button_order_is_retained_after_explicit_mouse_impulse():
    result = compile_sequence([action(yaw=0.02, events=[edge("forward", True), edge("forward", False),
                                                      edge("forward", True), edge("forward", False)])], profile())
    assert result["events"][0]["event"]["kind"] == "mouse_move"
    assert [e["event"]["pressed"] for e in result["events"][1:]] == [True, False, True, False]
    assert all(e["offset_ns"] == 0 for e in result["events"])


def test_mouse_and_event_bounds_reject_without_clipping_or_state_change():
    executor = ControlExecutor(profile())
    before = executor.state
    with pytest.raises(ValueError, match="clipping"):
        executor.prepare(action(yaw=4.02))  # 201 counts.
    buttons = [edge("forward", value) for _ in range(4) for value in (True, False)]
    with pytest.raises(ValueError, match="event bound"):
        executor.prepare(action(yaw=0.02, events=buttons))
    assert executor.state == before
    assert apply(executor, action(yaw=4))["emitted_mouse_counts"] == [-200, 0]


def test_two_second_hold_limit_checks_idle_and_release_boundaries():
    executor = ControlExecutor(profile())
    apply(executor, action(events=[edge("forward", True)]))
    for _ in range(63):
        apply(executor, action(held=["forward"]))
    assert executor.state["next_offset_ns"] == MAX_CONTINUOUS_HOLD_NS
    with pytest.raises(ValueError, match="two seconds"):
        executor.prepare(action(held=["forward"]))
    with pytest.raises(ValueError, match="two seconds"):
        executor.prepare(action(held=["forward"], events=[edge("forward", False, 1)]))
    apply(executor, action(held=["forward"], events=[edge("forward", False, 0)]))
    assert not any(executor.state["held"].values())


def test_rolling_event_rate_is_checked_across_decisions_and_expires_at_one_second():
    executor = ControlExecutor(profile())
    burst = action(events=[edge("forward", value) for _ in range(4) for value in (True, False)])
    for _ in range(16):
        apply(executor, burst)
    with pytest.raises(ValueError, match="rolling one-second"):
        executor.prepare(burst)
    for _ in range(16):
        apply(executor, action())
    assert executor.state["next_offset_ns"] == 1_000_000_000
    apply(executor, burst)  # The first eight events have left (t-1s,t].


def test_offline_sequence_requires_final_release_and_limits_total_events():
    with pytest.raises(ValueError, match="end with every control released"):
        compile_sequence([action(events=[edge("forward", True)])], profile())
    with pytest.raises(ValueError, match="128-event"):
        compile_sequence([action(yaw=0.02) for _ in range(129)], profile())
    with pytest.raises(ValueError, match="3200-count"):
        compile_sequence([action(yaw=4) for _ in range(17)], profile())
    with pytest.raises(ValueError, match="at least one"):
        compile_sequence([], profile())
    with pytest.raises(ValueError, match="decision count"):
        compile_sequence((action() for _ in range(961)), profile())


def test_compilation_keeps_contract_unmeasured_and_does_not_grant_readiness():
    result = compile_sequence([action(yaw=0.02)], profile())
    assert result["training_ready"] is False and result["live_control_ready"] is False
    assert result["exact_input_timing_verified"] is False
    assert result["combined_aim_movement_calibrated_by_compiler"] is False
    assert all(value["calibration_status"] == "unmeasured" for value in result["decisions"][0]["action"]["channel_status"].values())
    assert result["calibration_profile"]["provenance"]["test_fixture_only"] is True
    json.dumps(result, allow_nan=False)
