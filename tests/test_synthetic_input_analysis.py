import copy

import pytest

from cs2_data.synthetic_input_analysis import (
    BRIDGE_MARKER, INPUT_LEDGER_PROFILE, PROFILE, _mouse_cases,
    command_window_diagnostics, fit_gain_matrix, measure_response, verify_injection_evidence,
)


def cases(matrix=((-0.044, 0.001), (0.002, 0.044))):
    values = []
    for split, magnitude in (("fit", 40), ("validation", 20), ("validation", 80)):
        for dx, dy in ((magnitude, 0), (-magnitude, 0), (0, magnitude), (0, -magnitude)):
            values.append({"id": f"{split}-{dx}-{dy}", "split": split, "dx": dx, "dy": dy,
                           "status": "measured", "response_degrees": {
                               "yaw": matrix[0][0] * dx + matrix[0][1] * dy,
                               "pitch": matrix[1][0] * dx + matrix[1][1] * dy}})
    return values


def test_signed_cross_axis_matrix_predicts_held_out_smaller_and_larger_impulses():
    report = fit_gain_matrix(cases())
    assert report["status"] == "held_out_gain_validated_for_tested_cases"
    for observed, expected in zip(report["degrees_per_mouse_count"], [[-0.044, 0.001], [0.002, 0.044]]):
        assert observed == pytest.approx(expected)
    assert report["axis_magnitude_coverage"]["dx"]["tested_below_fit_range"]
    assert report["axis_magnitude_coverage"]["dy"]["tested_above_fit_range"]
    assert len(report["fit_case_ids"]) == 4 and len(report["validation_case_ids"]) == 8
    assert not report["held_out_refitting_performed"]
    assert not report["training_ready"] and not report["exact_input_timing_verified"]


def test_held_out_failure_cannot_modify_derived_matrix():
    original = cases()
    tampered = copy.deepcopy(original)
    tampered[-1]["response_degrees"]["pitch"] *= -1
    baseline, report = fit_gain_matrix(original), fit_gain_matrix(tampered)
    assert report["degrees_per_mouse_count"] == baseline["degrees_per_mouse_count"]
    assert "held_out_sign_or_magnitude_response_disagrees" in report["issues"]


def test_counts_report_success_but_no_game_response_is_not_a_calibration():
    report = fit_gain_matrix(cases(((0, 0), (0, 0))))
    assert report["mouse_counts_per_degree"] is None
    assert "observed_gain_zero_or_noninvertible" in report["issues"]


def test_diagonal_only_probes_cannot_identify_two_axes():
    data = [{"id": str(i), "split": split, "dx": delta, "dy": delta,
             "status": "measured", "response_degrees": {"yaw": delta * -.044, "pitch": delta * .044}}
            for i, (split, delta) in enumerate((("fit", 40), ("fit", -40), ("validation", 20), ("validation", -20)))]
    report = fit_gain_matrix(data)
    assert report["degrees_per_mouse_count"] is None
    assert "input_design_rank_deficient_or_ill_conditioned" in report["issues"]


def test_excluded_recoil_case_is_never_used_for_fitting():
    data = cases()
    data[0].update(status="excluded", reasons=["weapon_shot_changed"])
    data[0]["response_degrees"]["yaw"] = 10000
    report = fit_gain_matrix(data)
    assert data[0]["id"] not in report["fit_case_ids"]
    assert "planned_cases_excluded_or_unobserved" in report["issues"]
    assert report["degrees_per_mouse_count"][0][0] == pytest.approx(-.044)


def test_zero_impulse_detects_unexplained_view_drift():
    data = cases() + [{"id": "zero", "split": "validation", "dx": 0, "dy": 0,
                       "status": "measured", "response_degrees": {"yaw": .5, "pitch": 0.0}}]
    report = fit_gain_matrix(data)
    assert "held_out_sign_or_magnitude_response_disagrees" in report["issues"]


def test_duplicate_fit_or_validation_identifiers_are_rejected():
    data = cases()
    data[-1]["id"] = data[0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        fit_gain_matrix(data)


def snapshots():
    result = []
    for index in range(8):
        angles = [0.0, 179.0, 0.0] if index < 3 else [2.0, -177.0, 0.0]
        result.append({"capture_index": index, "qpc_before": 1000 + index * 30,
                       "qpc_after": 1001 + index * 30, "pixel_readback_qpc_after": 1002 + index * 30,
                       "controller_tick_base": 100 + index * 2,
                       "steam_id": "76561198845209628",
                       "player_identity": {"pawn_handle": 123, "controller_handle": 456, "life_state": 0},
                       "pawn_state": {"health": 100, "eye_angles": angles.copy(), "velocity": [0., 0., 0.],
                                      "ammo_clip": 20, "last_shot_time": 0., "active_weapon_handle": 789},
                       "rendered_camera": {"angles": angles.copy()}})
    return result


def measured(data):
    case = {"id": "impulse", "split": "fit", "dx": -90, "dy": 45}
    return measure_response(case, data[:3], data[-3:], data)


def test_native_plateau_response_preserves_yaw_wrap_and_unwrapped_pitch():
    report = measured(snapshots())
    assert report["status"] == "measured"
    assert report["response_degrees"] == {"yaw": 4.0, "pitch": 2.0}


@pytest.mark.parametrize("change,reason", [
    (lambda rows: rows[4]["pawn_state"].update(last_shot_time=10.), "shot_ammo_or_weapon_changed"),
    (lambda rows: rows[4]["pawn_state"].update(last_shot_time=None), "shot_or_weapon_state_unavailable"),
    (lambda rows: rows[4]["pawn_state"].update(velocity=[0., 5., 0.]), "player_moving_during_mouse_probe"),
    (lambda rows: rows[4]["player_identity"].update(pawn_handle=987), "player_identity_unavailable_or_changed"),
    (lambda rows: rows[4]["rendered_camera"].update(angles=[89., 0., 0.]), "pitch_near_clamp"),
    (lambda rows: rows[1]["rendered_camera"].update(angles=[0., 178., 0.]), "unstable_camera_plateau"),
    (lambda rows: [row["pawn_state"].update(eye_angles=[0., 179., 0.]) for row in rows], "camera_and_pawn_eye_response_disagree"),
    (lambda rows: rows[4].update(capture_index=99), "noncontiguous_capture_or_invalid_clock"),
    (lambda rows: [row.update(controller_tick_base=100) for row in rows], "plateau_lacks_observed_simulation_progress"),
])
def test_uncertain_or_confounded_responses_are_excluded(change, reason):
    rows = snapshots()
    change(rows)
    report = measured(rows)
    assert report["status"] == "excluded"
    assert report["response_degrees"] is None
    assert reason in report["reasons"]


def injection_evidence():
    event = {"id": "fit-x-positive", "at_ms": 1000, "kind": "mouse_move", "dx": 40, "dy": 0}
    source = {"schema_version": 1, "producer": PROFILE, "map": "de_dust2", "fps": 32, "width": 1280,
              "height": 720, "duration_seconds": 2, "events": [event]}
    plan = {**source, "movie_name": "calibration-" + "a" * 32, "demo_path": "owned/controlled.dem"}
    worker = {"owned_cs2_pid": 123, "run_id": "a" * 32}
    native = [{"event": "header", "schema_version": 1, "producer": PROFILE, "plan": plan,
               "synthetic_input_marker": BRIDGE_MARKER, "control_source": "external_windows_SendInput", "qpc_frequency": 1000},
              {"event": "calibration_ready", "qpc": 1000, "start_tick_base": 100,
               "clock_basis": "local_controller_tick_base_64hz", "local_connection_verified": True},
              {"event": "frame_sample", "qpc": 2000, "elapsed_ms": 1000., "owned_process_id": 123,
               "local_connection_verified": True, "local_player": {"controller_tick_base": 164}},
              {"event": "recording_stop_dispatched", "qpc": 3000}]
    guard = {"os": {"process_alive": True, "process_identity_verified": True, "target_pid": 123,
                    "foreground_pid": 123, "foreground_hwnd": 456, "foreground_matches": True}, "scope": {"verified": True}}
    receipt = {"schema_version": 1, "control_source": "windows_sendinput", "input_consumption_verified": False,
               "physical_device_latency_verified": False, "status": "inserted", "success": True,
               "requested_count": 1, "inserted_count": 1, "qpc_frequency": 1000, "qpc_before": 2001, "qpc_after": 2002,
               "target_pid": 123, "run_id": "a" * 32, "events": [{"kind": "mouse_move", "dx": 40, "dy": 0}],
               "guard_before": copy.deepcopy(guard), "guard_after": copy.deepcopy(guard)}
    rows = [{"event": "header", "profile": INPUT_LEDGER_PROFILE, "plan_sha256": "abc", "source_plan": source,
             "run_id": "a" * 32, "pid": 123},
            {"event": "injection_batch", "events": [event], "scheduled_at_ms": 1000, "actual_elapsed_ms": 1000.,
             "native_frame_qpc": 2000, "receipt": receipt},
            {"event": "cleanup", "reason": "plan_complete", "receipt": {"success": True, "status": "no_owned_inputs",
             "events": [], "requested_count": 0, "inserted_count": 0, "target_pid": 123, "run_id": "a" * 32}},
            {"event": "input_complete", "qpc": 2003, "events_inserted": 1, "game_consumption_verified": False, "training_ready": False},
            {"event": "cleanup", "reason": "worker_finally", "receipt": {"success": True, "status": "no_owned_inputs",
             "events": [], "requested_count": 0, "inserted_count": 0, "target_pid": 123, "run_id": "a" * 32}}]
    for row in rows:
        row["schema_version"] = 1
    return rows, native, plan, worker


def test_verified_insertion_retains_exact_batches_without_claiming_consumption():
    rows, native, plan, worker = injection_evidence()
    report = verify_injection_evidence(rows, native, plan, worker, "abc")
    assert report["batches"][0] is rows[1]
    assert report["queue_insertion_verified"] is True
    assert report["input_consumption_verified"] is False


@pytest.mark.parametrize("change", [
    lambda rows, native: rows[1]["receipt"].update(inserted_count=0),
    lambda rows, native: rows[1]["receipt"].update(qpc_frequency=10),
    lambda rows, native: rows[1]["receipt"].update(qpc_before=1999),
    lambda rows, native: rows[1]["receipt"]["guard_after"]["os"].update(foreground_pid=999),
    lambda rows, native: rows[1]["receipt"]["guard_before"]["scope"].update(verified=False),
    lambda rows, native: rows[1].update(actual_elapsed_ms=999.),
    lambda rows, native: rows[1].update(native_frame_qpc=2001),
    lambda rows, native: native[2].update(owned_process_id=999),
    lambda rows, native: native[2].update(local_connection_verified=False),
    lambda rows, native: native.insert(3, copy.deepcopy(native[2])),
    lambda rows, native: rows[1]["receipt"]["events"][0].update(dx=80),
    lambda rows, native: rows[2]["receipt"].update(status="released"),
    lambda rows, native: rows[4]["receipt"].update(success=False),
    lambda rows, native: rows[3].update(events_inserted=2),
])
def test_partial_unfocused_or_unbound_input_never_passes_provenance(change):
    rows, native, plan, worker = injection_evidence()
    change(rows, native)
    with pytest.raises(ValueError):
        verify_injection_evidence(rows, native, plan, worker, "abc")


def test_delayed_injection_cannot_use_an_unchanged_early_post_plateau():
    rows = []
    for i in range(49):
        sample = snapshots()[0]
        sample.update(capture_index=i, qpc_before=1000 + i * 30, qpc_after=1001 + i * 30,
                      pixel_readback_qpc_after=1002 + i * 30, controller_tick_base=100 + 2 * i)
        rows.append(sample)
    event = {"id": "fit-x-positive", "kind": "mouse_move", "at_ms": 1000, "dx": 40, "dy": 0}
    evidence = {"ready": {"start_tick_base": 100}, "qpc_frequency": 1000,
                "batches": [{"events": [event], "scheduled_at_ms": 1000, "actual_elapsed_ms": 1000.,
                             "native_frame_qpc": 1959, "receipt": {"qpc_before": 2390, "qpc_after": 2391}}]}
    case = _mouse_cases(rows, evidence)[0][0]
    assert case["status"] == "excluded"
    assert "insertion_not_between_fixed_pre_and_post_plateaus" in case["reasons"]


@pytest.mark.parametrize("unfinished", ["qpc_after", "pixel_readback_qpc_after"])
def test_input_inside_last_pre_capture_is_not_a_completed_baseline(unfinished):
    rows = []
    for i in range(49):
        sample = snapshots()[0]
        sample.update(capture_index=i, qpc_before=1000 + i * 30, qpc_after=1001 + i * 30,
                      pixel_readback_qpc_after=1002 + i * 30, controller_tick_base=100 + 2 * i)
        rows.append(sample)
    # The last pre-window image starts at QPC1900. Its unfinished operation
    # overlaps insertion, even though its start timestamp safely precedes it.
    rows[30][unfinished] = 1955
    event = {"id": "fit-x-positive", "kind": "mouse_move", "at_ms": 1000, "dx": 40, "dy": 0}
    evidence = {"ready": {"start_tick_base": 100}, "qpc_frequency": 1000,
                "batches": [{"events": [event], "scheduled_at_ms": 1000, "actual_elapsed_ms": 1000.,
                             "native_frame_qpc": 1949, "receipt": {"qpc_before": 1950, "qpc_after": 1951}}]}
    case = _mouse_cases(rows, evidence)[0][0]
    assert case["status"] == "excluded"
    assert "insertion_not_between_fixed_pre_and_post_plateaus" in case["reasons"]


def command_window():
    from test_causal_acceptance import command
    rows = [command(116, omitted=True), command(117)]
    before = {"steam_id": str(rows[0]["steam_id"]), "pawn_state": {"movement_last_command_number_processed": 115}}
    after = {"pawn_state": {"movement_last_command_number_processed": 117}}
    return rows, before, after


def test_mouse_command_sums_preserve_nullable_raw_fields_and_signed_counts():
    rows, before, after = command_window()
    report = command_window_diagnostics(rows, before, after, 3, -1)
    assert report["status"] == "measured_raw_command_neighborhood"
    assert report["commands"][0]["mousedx_raw"] is None
    assert report["mouse_count_sums"]["mousedx_raw"]["raw_absent_count"] == 1
    assert report["mouse_count_sums"]["mousedy_raw"]["effective_sum_with_known_base_protobuf_defaults"] == -1
    assert report["mouse_count_sums"]["mousedx_raw"]["sum_matches_injected_count"] is True
    assert report["sum_comparison_is_exact_event_assignment"] is False


def test_spoofed_mouse_column_does_not_become_a_valid_recorded_sum():
    rows, before, after = command_window()
    rows[1]["mousedx_raw"] = 99
    report = command_window_diagnostics(rows, before, after, 99, -1)
    assert report["projection_reason_counts"]
    assert report["mouse_count_sums"]["mousedx_raw"]["effective_sum_with_known_base_protobuf_defaults"] is None


def test_missing_recorded_command_is_not_silently_treated_as_zero_mouse_input():
    rows, before, after = command_window()
    report = command_window_diagnostics(rows[1:], before, after, 3, -1)
    assert report["status"] == "unavailable"
    assert "mouse_count_sums" not in report
