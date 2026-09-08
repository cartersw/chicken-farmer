import copy
import json

import pytest

from cs2_data.calibration_turns import (
    analyze_turns, compare_input_history, dispatch_capture_intervals, main, replay_candidate,
)


def state(index, tick, eye, camera, render_tick=None):
    return {"capture_index": index, "controller_tick_base": tick, "steam_id": "123",
            "player_identity": {"pawn_handle": 100, "controller_handle": 1},
            "render_time_seconds": (tick if render_tick is None else render_tick)/64,
            "qpc_before": 1000+index*100,
            "pawn_state": {"simulation_time": tick/64, "eye_angles": [0, eye, 0]},
            "rendered_camera": {"angles": [0, camera, 0]}}


def command(tick, fraction=0, yaw=0, pitch=0):
    return {"steam_id": "123", "command_number": 1, "demo_tick": 0, "server_tick_executed": tick,
            "input_history": [{"render_tick_count": tick, "render_tick_fraction": fraction,
                               "view_yaw": yaw, "view_pitch": pitch}]}


def test_frozen_candidate_uses_circular_yaw_and_actual_render_clock():
    rows = [state(0, 100, 170, 170), state(1, 102, -170, -180)]
    result = replay_candidate(rows, 1)
    assert result["evaluated_changing_snapshots"] == 1
    assert result["within_error_tolerance"] == 1
    assert result["frames"][0]["alpha_unclamped"] == 0.5
    assert result["frames"][0]["snapshot_interval_ticks"] == [101, 103]
    assert result["frames"][0]["residual_degrees"] == 0
    control = replay_candidate(rows, 0)
    assert control["maximum_absolute_residual_degrees"] == 10
    with pytest.raises(ValueError, match="frozen candidate"):
        replay_candidate(rows, 2)


def test_frozen_candidate_does_not_refit_failure():
    rows = [state(0, 100, 0, 0), state(1, 102, 20, 12)]
    result = replay_candidate(rows, 1)
    assert result["within_error_tolerance"] == 0
    assert result["maximum_absolute_residual_degrees"] == 2
    assert result["snapshot_time_offset_ticks"] == 1


def test_decreasing_yaw_stop_endpoint_and_clamping_are_explicit():
    rows = [state(0, 100, 0, 0), state(1, 102, -20, -10), state(2, 104, -22, -22, 106)]
    result = replay_candidate(rows, 1)
    assert result["decreasing_yaw_examples"] == 2
    assert result["within_error_tolerance"] == 2
    assert result["frames"][1]["alpha_unclamped"] == 1.5
    assert result["frames"][1]["within_snapshot_interval"] is False


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r[1]["player_identity"].update(pawn_handle=101), "identity_or_capture_boundary"),
    (lambda r: r[1].update(capture_index=3), "identity_or_capture_boundary"),
    (lambda r: r[1]["pawn_state"].update(simulation_time=99/64), "snapshot_clock_unavailable_or_gap"),
    (lambda r: r[1]["pawn_state"].update(simulation_time=106/64), "snapshot_clock_unavailable_or_gap"),
    (lambda r: r[1]["pawn_state"].update(eye_angles=[0, 0, 0]), "unchanged_eye_yaw"),
    (lambda r: r[1]["pawn_state"].update(eye_angles=[0, 180, 0]), "ambiguous_half_turn_direction"),
])
def test_unavailable_or_ambiguous_snapshots_are_not_evaluated(mutate, reason):
    rows = [state(0, 100, 0, 0), state(1, 102, 20, 10)]
    mutate(rows)
    result = replay_candidate(rows, 1)
    assert result["evaluated_changing_snapshots"] == 0
    assert result["maximum_absolute_residual_degrees"] is None
    assert result["skipped"][reason] == 1


def test_history_clock_rounding_interval_and_camera_angle_match():
    source = [state(0, 128, 10, 10)]
    history = command(128, 1e-6, yaw=10)
    result = compare_input_history(source, [history, copy.deepcopy(history)])
    assert result["unique_history_clock_matches"] == 1
    assert result["camera_angle_matches"] == 1
    assert len(result["frames"][0]["occurrences"]) == 2
    assert result["clock_offset_fitted"] is False
    assert result["maximum_clock_residual_ticks"] < result["frames"][0]["rounding_tolerance_ticks"]


def test_canonical_uint64_identity_matches_native_string_without_unknown_player_join():
    row = command(128, yaw=10)
    row["steam_id"] = 123
    missing = copy.deepcopy(row)
    missing["steam_id"] = None
    missing["input_history"][0]["view_yaw"] = 11
    result = compare_input_history([state(0, 128, 10, 10)], [row, missing])
    assert result["unique_history_clock_matches"] == result["camera_angle_matches"] == 1
    assert len(result["frames"][0]["occurrences"]) == 1


def test_history_camera_effect_difference_does_not_discard_clock_evidence():
    result = compare_input_history([state(0, 128, 10, 11)], [command(128, yaw=10)])
    assert result["unique_history_clock_matches"] == 1
    assert result["camera_angle_matches"] == 0
    assert result["frames"][0]["camera_pitch_yaw_error_degrees"] == [0, 1]


def test_conflicting_history_angles_or_multiple_clocks_are_ambiguous():
    source = [state(0, 128, 0, 0)]
    conflicting = compare_input_history(source, [command(128, yaw=10), command(128, yaw=11)])
    assert conflicting["frames"][0]["conflicting_angles"] is True
    assert conflicting["camera_angle_matches"] == 0
    times = compare_input_history(source, [command(128), command(128, fraction=1e-6)])
    assert times["unique_history_clock_matches"] == 0
    assert times["frames"][0]["candidate_clock_count"] == 2


def test_history_nearby_different_tick_is_not_shifted_to_fit():
    result = compare_input_history([state(0, 128, 0, 0)], [command(127)])
    assert result["unique_history_clock_matches"] == 0
    assert result["camera_angle_matches"] == 0


def test_invalid_history_fraction_is_unavailable():
    result = compare_input_history([state(0, 128, 0, 0)], [command(128, fraction=1)])
    assert result["invalid_or_unavailable_history_entries"] == 1
    assert result["unique_history_clock_matches"] == 0


def test_dispatch_bounds_are_wall_order_without_effect_claims():
    samples = [state(0, 100, 0, 0), state(1, 102, 0, 0)]
    event = {"event": "action_dispatch", "command": "+turnleft", "qpc_before": 1010, "qpc_after": 1020,
             "actual_elapsed_ms": 123}
    row = dispatch_capture_intervals([event], samples)[0]
    assert row["previous_capture"]["capture_index"] == 0
    assert row["next_capture"]["capture_index"] == 1
    assert row["association"] == "capture_dispatch_order_only_not_effect_or_consumption_time"
    event["qpc_after"] = 1000
    with pytest.raises(ValueError, match="dispatch QPC"):
        dispatch_capture_intervals([event], samples)


def test_hypothesis_tampering_cannot_publish_or_reuse_artifact(tmp_path):
    hypothesis = tmp_path / "hypothesis.json"
    hypothesis.write_text(json.dumps({"snapshot_time_offset_ticks": 2}))
    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="Frozen hypothesis hash mismatch"):
        analyze_turns(tmp_path / "comparison.json", tmp_path / "parsed", hypothesis, out)
    assert not out.exists()
    assert not out.with_suffix(".md").exists()
    assert not list(tmp_path.glob(".*partial*"))
    with pytest.raises(SystemExit) as error:
        main(["--comparison", str(tmp_path / "comparison.json"), "--parsed", str(tmp_path / "parsed"),
              "--hypothesis", str(hypothesis), "--output", str(out)])
    assert error.value.code == 2
