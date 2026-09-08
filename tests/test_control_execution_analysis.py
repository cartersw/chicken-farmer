from copy import deepcopy
import struct

import pytest

from cs2_data import control_execution_analysis as audit
from cs2_data.control_executor import compile_sequence
from cs2_data.control_label_audit import BUTTON_MASKS
from cs2_data.control_program import default_program, project_events
from cs2_data.causal_acceptance import protobuf_fields
from test_control_executor import profile
from test_control_labels import command
from test_causal_acceptance import embedded, floating, integer


def compilation():
    provenance = {"test_fixture_only": True}
    measured = profile(provenance=provenance, provenance_sha256=audit._digest(provenance),
        supported_button_masks=BUTTON_MASKS)
    program = default_program()
    compiled = compile_sequence(program["actions"], measured)
    plan, projection = project_events(program, compiled["events"])
    return program, compiled, plan, projection


def test_independent_arithmetic_checks_all_decisions_carry_and_threshold_projection():
    result = audit.audit_compilation(*compilation())
    assert result["decision_count"] == 256
    assert result["status"] == "independent_compilation_and_projection_agree"
    assert [p["id"] for p in result["phases"]] == ["forward", "left", "back", "right", "crouch"]
    assert result["phases"][0]["start_ms"] == 1000 and result["phases"][0]["end_ms"] == 1500


@pytest.mark.parametrize("field", ["ideal_mouse_counts", "emitted_mouse_counts", "mouse_count_residual_before",
    "mouse_count_residual_after", "predicted_emitted_angular_delta_deg", "action_sha256", "action", "held_after",
    "start_offset_ns", "decision_index", "events", "calibration_provenance_sha256", "rounding_policy"])
def test_mutated_per_decision_compilation_never_passes(field):
    program, compiled, plan, projection = compilation()
    compiled["decisions"][5][field] = None
    with pytest.raises(ValueError, match="independent decision"):
        audit.audit_compilation(program, compiled, plan, projection)


@pytest.mark.parametrize("field", ["events", "event_count", "duration_ns", "final_state", "total_absolute_mouse_counts"])
def test_mutated_sequence_summary_never_passes(field):
    program, compiled, plan, projection = compilation()
    compiled[field] = None
    with pytest.raises(ValueError, match="independent"):
        audit.audit_compilation(program, compiled, plan, projection)


def test_threshold_ceil_instead_of_floor_changes_a_boundary_and_is_rejected():
    program, compiled, plan, projection = compilation()
    candidate = next(row for row in projection if row["encoded_threshold_delta_ns"] < 0)
    candidate["projected_at_ms"] += 1
    with pytest.raises(ValueError, match="boundary projection"):
        audit.audit_compilation(program, compiled, plan, projection)


def test_program_roles_cannot_be_changed_to_fit_observed_response():
    program, compiled, plan, projection = compilation()
    program["actions"][0]["angular_delta_deg"]["yaw"] = 0.0
    with pytest.raises(ValueError, match="frozen five-phase program"):
        audit.audit_compilation(program, compiled, plan, projection)


def f32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def measured_phase():
    phase = {"id": "forward", "control_mask": 8, "start_decision": 0, "end_decision": 16,
        "start_ms": 1000., "end_ms": 1500., "event_ids": ["start", "stop"],
        "emitted_mouse_counts": [40, 8], "predicted_emitted_yaw_pitch_degrees": [-.88, .176]}
    rows = []
    for n in range(80, 185):
        held = 8 if 118 <= n < 150 else 0
        changed = 8 if n in (118, 150) else 0
        row = command(n, (held, changed, 0), yaw=f32(-.88 if n >= 120 else 0), pitch=f32(.176 if n >= 120 else 0))
        base = protobuf_fields(row["command_protobuf"])[1][0][1]
        dx, dy = (40, 8) if n == 120 else (0, 0)
        base = base.replace(integer(11, 3), integer(11, dx)).replace(integer(12, -1), integer(12, dy))
        row.update(command_protobuf=embedded(1, base), mousedx_raw=dx, mousedy_raw=dy)
        rows.append(row)
    states = []
    for index, n in enumerate(range(100, 177, 2)):
        angles = [f32(.176 if n >= 120 else 0), f32(-.88 if n >= 120 else 0), 0.]
        states.append({"capture_index": index, "controller_tick_base": n, "qpc_before": n*1000,
            "qpc_after": n*1000+10, "pixel_readback_qpc_after": n*1000+20,
            "steam_id": str(rows[0]["steam_id"]), "player_identity": {"pawn_handle": 42, "controller_handle": 123, "life_state": 0},
            "rendered_camera": {"angles": angles}, "pawn_state": {"health": 100, "eye_angles": angles,
                "origin": [float(max(0, min(n-116, 32))), 0., 0.], "velocity": [10. if 116 <= n < 150 else 0., 0., 0.],
                "movement_last_command_number_processed": n, "last_shot_time": 1., "ammo_clip": 20,
                "active_weapon_handle": 7, "ducked": False, "duck_amount": 0.}})
    evidence = {"ready": {"start_tick_base": 52}, "batches": [
        {"events": [{"id": "start"}], "native_frame_qpc": 115990, "actual_elapsed_ms": 1000., "receipt": {"qpc_before": 116100, "qpc_after": 116200}},
        {"events": [{"id": "stop"}], "native_frame_qpc": 147990, "actual_elapsed_ms": 1500., "receipt": {"qpc_before": 148100, "qpc_after": 148200}}],
        "native_frames": [{"qpc": s["qpc_before"] - 10, "elapsed_ms": (s["controller_tick_base"] - 52) * 1000 / 64,
            "local_player": {"status": "observed", "steam_id": s["steam_id"], **deepcopy(s["player_identity"]),
                "controller_tick_base": s["controller_tick_base"], "pawn_state": deepcopy(s["pawn_state"])}} for s in states]}
    return phase, states, evidence, rows


def test_combined_response_allows_translation_and_checks_raw_counts_plus_angles_and_button_effect():
    result = audit.measure_phase(*measured_phase())
    assert result["status"] == "combined_response_observed", result["reasons"]
    assert result["native_movement_or_crouch_effect"]["maximum_horizontal_displacement_units"] == 32.
    assert result["raw_command_diagnostics"]["mouse_count_sums"]["mousedx_raw"]["sum_matches_injected_count"]
    assert result["recorded_button_response"]["plane_change_press_command_numbers"] == [118]
    assert result["recorded_button_response"]["plane_change_release_command_numbers"] == [150]
    assert result["exact_event_consumption_time_verified"] is False


@pytest.mark.parametrize("change,reason", [
    ("angle", "observed_angular_response_disagrees_with_emitted_gain"),
    ("moving_plateau", "unstable_camera_plateau"),
    ("translation", "movement_response_missing_or_unavailable"),
    ("identity", "native_player_identity_missing_or_changed"),
    ("death", "native_player_not_alive"),
    ("gap", "capture_or_native_clock_discontinuity"),
    ("readback", "insertions_not_between_completed_fixed_plateaus"),
    ("late_call", "insertions_not_between_completed_fixed_plateaus"),
    ("shot", "shot_or_weapon_state_changed_or_unavailable"),
    ("endpoint", "original_command_identity_or_clock_window_unavailable"),
    ("command_gap", "original_command_identity_or_clock_window_unavailable"),
    ("counts", "recorded_mouse_counts_missing_or_disagree"),
    ("buttons", "recorded_control_press_or_release_response_missing")])
def test_observed_failures_are_retained_instead_of_retuning_windows(change, reason):
    phase, states, evidence, rows = measured_phase()
    if change == "angle":
        for s in states:
            s["rendered_camera"]["angles"] = s["pawn_state"]["eye_angles"] = [0., 0., 0.]
    elif change == "moving_plateau": states[-2]["rendered_camera"]["angles"] = [0., 1., 0.]
    elif change == "translation":
        for s in states: s["pawn_state"]["origin"] = [0., 0., 0.]
    elif change == "identity": states[10]["player_identity"]["pawn_handle"] = 9
    elif change == "death": states[10]["player_identity"]["life_state"] = 1
    elif change == "gap": states[10]["capture_index"] += 1
    elif change == "readback": states[0]["pixel_readback_qpc_after"] = 116101
    elif change == "late_call": evidence["batches"][-1]["receipt"]["qpc_after"] = 164001
    elif change == "shot": states[10]["pawn_state"]["ammo_clip"] = 19
    elif change == "endpoint": states[-1]["pawn_state"]["movement_last_command_number_processed"] -= 1
    elif change == "command_gap": rows.pop(40)
    elif change == "counts": phase["emitted_mouse_counts"] = [39, 8]
    elif change == "buttons": phase["control_mask"] = 16
    result = audit.measure_phase(phase, states, evidence, rows)
    assert result["status"] == "missing_or_inconsistent_response"
    assert reason in result["reasons"]


def test_crouch_uses_observed_duck_response_not_keyboard_receipt_alone():
    phase, states, evidence, rows = measured_phase()
    phase["id"] = "crouch"  # Isolate the native effect rule with the same raw mask fixture.
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "crouch_response_missing_or_unavailable" in result["reasons"]
    for s in states:
        if 120 <= s["controller_tick_base"] <= 150:
            s["pawn_state"].update(ducked=True, duck_amount=1.)
    result = audit.measure_phase(phase, states, evidence, rows)
    assert result["status"] == "combined_response_observed", result["reasons"]


def test_response_already_processed_before_actual_os_insertion_cannot_satisfy_press():
    phase, states, evidence, rows = measured_phase()
    evidence["batches"][0]["receipt"] = {"qpc_before": 120100, "qpc_after": 120200}
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "recorded_control_press_or_release_response_missing" in result["reasons"]
    assert result["recorded_button_response"]["already_processed_changes_excluded_from_response"] == [
        {"command_number": 118, "pressed": True}]
    assert result["recorded_button_response"]["observed_processed_command_before_insertions"]["press"] == 120


def test_recorded_reloadlike_release_before_actual_release_call_cannot_satisfy_response():
    phase, states, evidence, rows = measured_phase()
    evidence["batches"][1]["receipt"] = {"qpc_before": 152100, "qpc_after": 152200}
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "recorded_control_press_or_release_response_missing" in result["reasons"]
    assert result["recorded_button_response"]["already_processed_changes_excluded_from_response"] == [
        {"command_number": 150, "pressed": False}]


def test_source_command_angles_must_agree_even_when_native_plateaus_look_correct():
    phase, states, evidence, rows = measured_phase()
    for row in rows:
        if row["command_number"] >= 120:
            base = protobuf_fields(row["command_protobuf"])[1][0][1]
            base = base.replace(floating(2, row["view_yaw"]), floating(2, 0.))
            row.update(view_yaw=0., command_protobuf=embedded(1, base))
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "recorded_angular_response_disagrees_with_emitted_gain" in result["reasons"]
    assert "observed_angular_response_disagrees_with_emitted_gain" not in result["reasons"]


def test_vertical_motion_cannot_pass_a_horizontal_movement_phase():
    phase, states, evidence, rows = measured_phase()
    for state in states:
        pawn = state["pawn_state"]
        pawn["origin"] = [0., 0., pawn["origin"][0]]
        pawn["velocity"] = [0., 0., pawn["velocity"][0]]
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "movement_response_missing_or_unavailable" in result["reasons"]
    assert result["native_movement_or_crouch_effect"]["maximum_horizontal_displacement_units"] == 0.


def test_wrong_analog_axis_or_sign_cannot_corroborate_horizontal_motion():
    phase, states, evidence, rows = measured_phase()
    phase["id"] = "left"  # The synthetic command fixture contains only forward analog input.
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "movement_response_missing_or_unavailable" in result["reasons"]
    assert result["native_movement_or_crouch_effect"]["matching_analog_command_numbers"] == []


def test_canonical_and_native_handles_keep_distinct_numeric_namespaces():
    phase, states, evidence, rows = measured_phase()
    for row in rows:
        base = protobuf_fields(row["command_protobuf"])[1][0][1]
        base = base.replace(integer(14, 42), integer(14, 885040))
        row.update(pawn_entity_handle=885040, command_protobuf=embedded(1, base))
    for state in states:
        state["player_identity"]["pawn_handle"] = 1769776
    for frame in evidence["native_frames"]:
        frame["local_player"]["pawn_handle"] = 1769776
    result = audit.measure_phase(phase, states, evidence, rows)
    assert result["status"] == "combined_response_observed", result["reasons"]
    assert result["player_association"]["canonical_pawn_handle"] == 885040
    assert result["player_association"]["native_pawn_handle"] == 1769776
    assert result["player_association"]["numeric_pawn_handle_equivalence_verified"] is False


def test_newer_frame_start_proves_old_response_before_insert_even_without_new_movie():
    phase, states, evidence, rows = measured_phase()
    newer = deepcopy(next(f for f in evidence["native_frames"] if f["local_player"]["controller_tick_base"] == 118))
    newer["qpc"] = 116090
    evidence["native_frames"].append(newer)
    result = audit.measure_phase(phase, states, evidence, rows)
    assert "recorded_control_press_or_release_response_missing" in result["reasons"]
    assert result["recorded_button_response"]["observed_processed_command_before_insertions"]["press"] == 118


def test_serialized_profile_flags_do_not_replace_a_fresh_loader_call(monkeypatch, tmp_path):
    from cs2_data import control_label_audit
    _, compiled, _, _ = compilation()
    recorded = compiled["calibration_profile"]
    pointers = {key: str(tmp_path / key) for key in ("mouse_run", "mouse_parsed", "keyboard_run", "keyboard_parsed", "keyboard_protocol")}
    recorded["provenance"].update(calibration_inputs=pointers, source_files={})
    recorded["verified"] = True
    calls = []
    def blocked(*args, **kwargs):
        calls.append((args, kwargs))
        raise ValueError("Source proof intentionally failed")
    monkeypatch.setattr(control_label_audit, "load_measured_control_profile", blocked)
    with pytest.raises(ValueError, match="Source proof intentionally failed"):
        audit.reverify_compilation_profile(recorded, tmp_path / "fresh")
    assert len(calls) == 1 and calls[0][1] == {"evidence_output": tmp_path / "fresh"}


def test_changed_compiled_proof_source_is_rejected_before_loading(monkeypatch, tmp_path):
    _, compiled, _, _ = compilation()
    recorded = compiled["calibration_profile"]
    source = tmp_path / "source.json"
    source.write_text("changed")
    recorded["provenance"].update(calibration_inputs={key: "test-pointer" for key in
        ("mouse_run", "mouse_parsed", "keyboard_run", "keyboard_parsed", "keyboard_protocol")}, source_files={str(source): "0"*64})
    with pytest.raises(ValueError, match="Compiled calibration source changed"):
        audit.reverify_compilation_profile(recorded, tmp_path / "fresh")


def test_missing_fixed_plateau_is_reported_without_searching_for_a_better_window():
    phase, states, evidence, rows = measured_phase()
    result = audit.measure_phase(phase, states[10:], evidence, rows)
    assert result["status"] == "missing_response_evidence"


def test_protocol_is_fixed_before_actual_capture():
    assert audit.PROTOCOL["pre_window_ms"] == [-250, -62.5]
    assert audit.PROTOCOL["post_window_ms"] == [250, 437.5]
    assert audit.PROTOCOL["absolute_angle_tolerance_degrees"] == .05
    assert audit.PROTOCOL["relative_angle_tolerance"] == .05
