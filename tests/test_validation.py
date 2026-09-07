"""Evidence tests: wrong players, wrong phases, gaps and retrospective targets."""
import hashlib
import copy
import json
import struct

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data.io import sha256_file
from cs2_data.validation import (PAUSE_FLAGS, canonical_action_checks, expected_state,
    load_state_context, load_validation, native_observation_checks, raw_equal, shot_clock_checks, validate_clip)


def cmd(tick, **fields):
    return {"command_row_id": tick, "demo_tick": tick, "server_tick_executed": tick+1000,
            "frame_index": 0, "base_present": True, "buttons_present": True,
            "viewangles_present": True, "view_yaw": 0.0, "view_pitch": 0.0,
            "buttonstate1": 0, "forwardmove": 0.0, "leftmove": 0.0, "subtick_moves": [], **fields}


def test_firing_is_checked_outcome_to_input_and_missing_data_does_not_pass():
    event = [{"kind": "weapon_fire", "demo_tick": 102}]
    masks = {"attack1": 1}
    assert canonical_action_checks([cmd(102)], [], event, masks)["canonical_fire"]["status"] == "failed"
    assert canonical_action_checks([cmd(102, buttonstate1=1)], [], event, masks)["canonical_fire"]["status"] == "passed"
    assert canonical_action_checks([cmd(102, buttonstate1=None, buttons_present=False)], [], event, masks)["canonical_fire"]["status"] == "unknown"
    # A held fire input while reloading cannot be treated as a missing shot.
    assert canonical_action_checks([cmd(102, buttonstate1=1)], [], [], masks)["canonical_fire"]["status"] == "unknown"


def test_angles_wrap_but_a_wrong_state_tick_does_not_match():
    rows = [cmd(100, view_yaw=179.0), cmd(101, view_yaw=-179.0)]
    states = [{"demo_tick": 101, "view_yaw": 181.0, "view_pitch": 0.0}]
    assert canonical_action_checks(rows, states, [], {})["canonical_aim"]["status"] == "passed"
    states[0]["view_yaw"] = 176
    assert canonical_action_checks(rows, states, [], {})["canonical_aim"]["status"] == "failed"
    states[0]["demo_tick"] = 102
    assert canonical_action_checks(rows, states, [], {})["canonical_aim"]["status"] == "unknown"


def test_state_comparison_never_interpolates_across_missing_ticks():
    states = [{"demo_tick": 100, "view_yaw": 179.0}, {"demo_tick": 101, "view_yaw": -179.0}]
    assert expected_state(states, 100.5)["view_yaw"] == 180
    assert expected_state(states, 99.5) is None
    states[1]["demo_tick"] = 102
    assert expected_state(states, 101) is None


def test_jump_crouch_require_actual_transitions_not_just_button_presence():
    commands = [cmd(100, buttonstate1=6), cmd(101, buttonstate1=6)]
    states = [{"demo_tick": 100, "on_ground": True, "crouching": False},
              {"demo_tick": 101, "on_ground": False, "crouching": True, "velocity_z": 250}]
    result = canonical_action_checks(commands, states, [], {"jump": 2, "crouch": 4})
    assert result["canonical_jump"]["status"] == result["canonical_crouch"]["status"] == "passed"
    states[1]["demo_tick"] = 103
    result = canonical_action_checks(commands, states, [], {"jump": 2, "crouch": 4})
    assert result["canonical_jump"]["status"] == result["canonical_crouch"]["status"] == "unknown"


def test_stationary_obstructed_movement_is_inconclusive_not_a_label_failure():
    result = canonical_action_checks([cmd(100, forwardmove=1)],
        [{"demo_tick": 100, "velocity_x": 0, "velocity_y": 0}], [], {})
    assert result["canonical_move"]["status"] == "unknown"


def native_fixture():
    frame = {"event": "movie_frame", "capture_index": 0, "native_observation": {"schema_version": 1,
        "source_phase": "movie_submission", "clock_on_engine_thread": True,
        "observed_pov": {"status": "observed", "steam_id": "123", "pawn_handle": 91, "controller_handle": 42,
            "observer_target_handle": 91, "controller_pawn_handle": 91,
            "observer_mode": 2, "camera_view_entity_handle": 2**32-1,
            "resolution_path": "local_pawn.observer_services.target_handle.pawn.controller_handle.steam_id",
            "pawn_state": {"origin": [0, 0, 0], "on_ground": True, "ducked": False, "ammo_clip": 20}},
        "rendered_camera": {"source": "CViewRender.current_view_at_observation_boundary", "angles": [0, 10, 0], "origin": [0, 0, 64],
                            "matrix_input_angles": [0, 10, 0], "matrix_input_origin": [0, 0, 64], "matrix_built_framecount": 42}}}
    before = copy.deepcopy(frame["native_observation"])
    after = copy.deepcopy(before)
    before["source_phase"], after["source_phase"] = "pixel_readback_before", "pixel_readback_after"
    return [frame, {"event": "pixel_readback", "submission_candidate": {"capture_index": 0},
                    "native_observation": before, "native_observation_after": after}]


def test_native_pov_is_exact_and_camera_eye_origin_is_not_compared_to_feet():
    native = native_fixture()
    frames = [{"action_window_demo_tick_start": 100}]
    states = [{"demo_tick": 100, "view_yaw": 10, "view_pitch": 0,
        "position_x": 0, "position_y": 0, "position_z": 0, "on_ground": True, "crouching": False, "ammo_clip": 20}]
    result = native_observation_checks(native, frames, states, {"steam_id": "123", "player_slot": 2})
    assert result["pov_identity"]["status"] == result["visual_move"]["status"] == "passed"
    assert result["observation_clock"]["status"] == "unknown"  # Even perfect values are not an epoch anchor.
    native[0]["native_observation"]["observed_pov"]["steam_id"] = "124"
    assert native_observation_checks(native, frames, states, {"steam_id": "123", "player_slot": 2})["pov_identity"]["status"] == "failed"


def test_missing_pov_and_native_render_fields_do_not_inherit_manifest_approval():
    result = native_observation_checks([{"event": "movie_frame"}], [{}], [],
                                       {"steam_id": "123", "player_slot": 2, "pov_verified": True})
    assert result["pov_identity"]["status"] == result["visual_aim"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["chase", "view_override", "reciprocal_handle", "readback_switch"])
def test_correct_steam_target_does_not_certify_wrong_camera_mode_or_capture_switch(change):
    rows = native_fixture()
    pov = rows[0]["native_observation"]["observed_pov"]
    if change == "chase":
        pov["observer_mode"] = 3
    elif change == "view_override":
        pov["camera_view_entity_handle"] = 91
    elif change == "reciprocal_handle":
        pov["controller_pawn_handle"] = 92
    else:
        rows[1]["native_observation_after"]["observed_pov"]["steam_id"] = "999"
    result = native_observation_checks(rows, [{"action_window_demo_tick_start": 100}], [], {"steam_id": "123", "player_slot": 2})
    assert result["pov_identity"]["status"] == "failed"


def test_static_native_fields_are_not_dynamic_action_profile_proof():
    rows = native_fixture()
    result = native_observation_checks(rows, [{"action_window_demo_tick_start": 100}],
        [{"demo_tick": 100, "on_ground": True, "crouching": False, "ammo_clip": 20}], {"steam_id": "123", "player_slot": 2})
    assert result["visual_jump"]["status"] == "passed"  # Local state agreement only.
    assert result["native_transition_coverage"]["status"] == "unknown"
    assert result["native_transition_coverage"]["evidence_count"] == 0
    assert result["observation_clock"]["status"] == "unknown"


def test_nan_raw_label_equality_preserves_local_quality_handling():
    assert raw_equal({"subtick_moves": [{"when": float("nan")}]}, {"subtick_moves": [{"when": float("nan")}]})
    assert not raw_equal({"when": float("nan")}, {"when": 0})


def test_measured_shot_can_precede_image_even_when_integer_execution_is_future():
    commands = [cmd(102, subtick_moves=[{"button": 1, "pressed": True, "when": 0.25}])]
    context = {"schema_version": 2, "producer": "cs2-context-v2", "shot_observation_policy": "unique-weapon-event-per-observed-tick-v1",
        "weapon_fire_observations": [{"demo_tick": 102, "observed_demo_tick": 102, "ambiguous_observation": False, "steam_id": "123", "round_id": 1,
        "player_slot": 2, "weapon_last_shot_time_seconds": 1101.25/64}]}
    frames = [{"render_time_seconds_start": 1101.5/64, "render_time_seconds_end": 1103.5/64}]
    result = shot_clock_checks(context, commands, frames, {"steam_id": "123", "round_id": 1, "player_slot": 2}, 64, 1)
    assert result["weapon_shot_clock"]["status"] == "passed"
    assert result["weapon_shot_future"]["status"] == "failed"
    assert commands[0]["server_tick_executed"] > frames[0]["render_time_seconds_start"]*64


def test_signed_or_missing_attack_fraction_cannot_certify_shot_clock():
    commands = [cmd(102, subtick_moves=[{"button": 1, "pressed": True, "when": -0.01}])]
    context = {"schema_version": 2, "producer": "cs2-context-v2", "shot_observation_policy": "unique-weapon-event-per-observed-tick-v1",
        "weapon_fire_observations": [{"demo_tick": 102, "observed_demo_tick": 102, "ambiguous_observation": False, "steam_id": "123", "round_id": 1,
        "player_slot": 2, "weapon_last_shot_time_seconds": 1101.25/64}]}
    result = shot_clock_checks(context, commands, [{}], {"steam_id": "123", "round_id": 1, "player_slot": 2}, 64, 1)
    assert result["weapon_shot_clock"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["legacy", "ambiguous", "delayed"])
def test_weapon_property_observation_must_unambiguously_belong_to_exact_event_tick(change):
    commands = [cmd(102, subtick_moves=[{"button": 1, "pressed": True, "when": 0.25}])]
    context = {"schema_version": 2, "producer": "cs2-context-v2", "shot_observation_policy": "unique-weapon-event-per-observed-tick-v1",
        "weapon_fire_observations": [{"demo_tick": 102, "observed_demo_tick": 102, "ambiguous_observation": False,
            "steam_id": "123", "round_id": 1, "player_slot": 2, "weapon_last_shot_time_seconds": 1101.25/64}]}
    if change == "legacy":
        context.update(schema_version=1, producer="cs2-context-v1")
    elif change == "ambiguous":
        context["weapon_fire_observations"][0]["ambiguous_observation"] = True
    else:
        context["weapon_fire_observations"][0]["observed_demo_tick"] = 103
    result = shot_clock_checks(context, commands, [{}], {"steam_id": "123", "round_id": 1, "player_slot": 2}, 64, 1)
    assert result["weapon_shot_clock"]["status"] == "unknown"


def test_pause_is_recomputed_and_ambiguous_ticks_stay_unknown(tmp_path):
    context = {"schema_version": 1, "producer": "cs2-context-v1", "parse_status": "complete", "partial": False,
        "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0", "property_prefix": "m_pGameRules.",
        "demo_id": "demo", "source_demo_sha256": "demo", "tick_rate": 64, "timing_clock": "demo_tick",
        "required_pause_flags": list(PAUSE_FLAGS), "segments": [{"start_demo_tick": 100, "end_demo_tick": 110,
            "round_id": 1, "is_paused": False, "ambiguous_tick": False, "pause_flags": dict.fromkeys(PAUSE_FLAGS, False)}]}
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    assert load_state_context(path, {"demo_id": "demo", "tick_rate": 64})["segments"][0]["is_paused"] is False
    assert load_state_context(path, {"demo_id": "demo", "tick_rate": 64})["pause_evidence_verified"] is False
    context["segments"][0]["pause_flags"][PAUSE_FLAGS[0]] = True
    path.write_text(json.dumps(context))
    with pytest.raises(ValueError, match="five observed flags"):
        load_state_context(path, {"demo_id": "demo", "tick_rate": 64})
    context["segments"][0].update(ambiguous_tick=True, is_paused=None)
    path.write_text(json.dumps(context))
    assert load_state_context(path, {"demo_id": "demo", "tick_rate": 64})["segments"][0]["is_paused"] is None


@pytest.mark.parametrize("changes", [
    {"warning_policy": "unverified"}, {"evidence_loss_warnings": 1}, {"evidence_loss_warnings": False},
    {"warnings": {"2": 1}}, {"warnings": {"1": -1}}, {"parser": "another-parser"},
    {"property_prefix": "cs_gamerules_data."},
])
def test_v2_context_requires_lossless_warning_audit_and_known_property_source(tmp_path, changes):
    context = {"schema_version": 2, "producer": "cs2-context-v2", "parse_status": "complete", "partial": False,
        "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0", "property_prefix": "m_pGameRules.",
        "demo_id": "demo", "source_demo_sha256": "demo", "tick_rate": 64, "timing_clock": "demo_tick",
        "required_pause_flags": list(PAUSE_FLAGS), "segments": [], "warning_policy": "rule-and-shot-evidence-v1",
        "warnings": {"1": 2, "13": 1}, "evidence_loss_warnings": 0}
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    assert load_state_context(path, {"demo_id": "demo", "tick_rate": 64})["pause_evidence_verified"] is True
    context.update(changes)
    path.write_text(json.dumps(context))
    with pytest.raises(ValueError, match="State context"):
        load_state_context(path, {"demo_id": "demo", "tick_rate": 64})


def dataset_fixture(tmp_path, monkeypatch):
    from test_calibration import parsed_fixture, commands, identity
    from cs2_data.calibration import calibrate
    from cs2_data.timing import prepare_timing
    from cs2_data.align import align
    from test_timing import records, render
    parsed, manifest = parsed_fixture(tmp_path)
    states = [{**identity(), "steam_id": int(identity()["steam_id"]), "demo_tick": row["demo_tick"],
               "view_yaw": row["view_yaw"], "view_pitch": 0.0} for row in commands()]
    pq.write_table(pa.Table.from_pylist(states), parsed / "player_state.parquet")
    pq.write_table(pa.Table.from_pylist([{"demo_id": identity()["demo_id"], "round_id": 1}]), parsed / "rounds.parquet")
    event_schema = pa.schema([(key, pa.int64() if key in ("round_id", "player_slot", "demo_tick") else pa.uint64() if key == "steam_id" else pa.string())
                             for key in (*identity(), "demo_tick", "kind")])
    pq.write_table(pa.Table.from_pylist([], schema=event_schema), parsed / "events.parquet")
    manifest.update(tick_rate=64, files={name: sha256_file(parsed / name) for name in
        ("usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet")})
    (parsed / "manifest.json").write_text(json.dumps(manifest))
    dataset = tmp_path / "dataset"
    calibrate(parsed, dataset / "calibration", 1, int(identity()["steam_id"]), 2, 100, 107)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_rows = records()
    archive = []
    for index in range(2):
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HHBB", header, 12, 1, 1, 24, 32)
        image = raw_dir / f"stable_{index:08d}.tga"
        image.write_bytes(bytes(header)+bytes([0, 0, 100+index]))
        archive.append({"capture_index": index, "source_name": f"nonce_{index:08d}.tga", "archived_name": image.name, "sha256": sha256_file(image)})
        ledger_rows.append({"event": "pixel_readback", "success": True, "submission_candidate": dict(ledger_rows[index+1]),
                            "rgb_sha256": hashlib.sha256(bytes([100+index, 0, 0])).hexdigest()})
    for index in range(3):
        ledger_rows[index+1]["render_time_seconds"] = (1100.5+index*2)/64
    ledger_path = raw_dir / "capture.jsonl"
    ledger_path.write_text("\n".join(json.dumps(row) for row in ledger_rows))
    archive_path = raw_dir / "capture_frame_files.json"
    archive_path.write_text(json.dumps({"schema_version": 1, "capture_prefix": "nonce", "archived_prefix": "stable", "frames": archive}))
    video = raw_dir / "stable.mp4"
    video.write_bytes(b"fixture video; decoding mocked, TGA and ledger hashes are real")
    report = {**render(), **identity(), "width": 1, "height": 1, "video_uri": str(video), "video_sha256": sha256_file(video),
              "capture_ledger_sha256": sha256_file(ledger_path), "capture_frame_files_sha256": sha256_file(archive_path)}
    render_path = raw_dir / "render.json"
    render_path.write_text(json.dumps(report))
    pts = raw_dir / "pts.json"
    pts.write_text(json.dumps({"clip_id": "stable", "clock": "video_presentation", "frames": [
        {"frame_index": i, "pts_seconds": i/32} for i in range(2)]}))
    monkeypatch.setattr("cs2_data.align.verify_video", lambda *args: video)
    monkeypatch.setattr("cs2_data.validation.verify_video", lambda *args: video)
    prepare_timing(render_path, ledger_path, pts, raw_dir, dataset / "timing")
    align(parsed, dataset / "timing/frames.jsonl", dataset / "timing/clip.json", dataset / "aligned",
          calibration=dataset / "calibration", diagnostic=True)
    (dataset / "pipeline_manifest.json").write_text(json.dumps({"status": "complete"}))
    return parsed, dataset


def test_real_artifact_chain_is_rechecked_and_report_flags_cannot_approve(tmp_path, monkeypatch):
    parsed, dataset = dataset_fixture(tmp_path, monkeypatch)
    out = tmp_path / "validation"
    report = validate_clip(parsed, dataset, out)
    assert report["checks"]["capture_integrity"]["status"] == "passed"
    assert report["checks"]["pixel_correspondence"]["status"] == "passed"
    assert report["checks"]["execution_clock"]["status"] == "unknown"
    assert load_validation(out, parsed, dataset) == report
    report["checks"]["execution_clock"]["status"] = "passed"
    (out / "clip_validation.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="edited statuses"):
        load_validation(out, parsed, dataset)


def test_changed_native_pixels_are_rejected_even_with_existing_approval_report(tmp_path, monkeypatch):
    parsed, dataset = dataset_fixture(tmp_path, monkeypatch)
    out = tmp_path / "validation"
    validate_clip(parsed, dataset, out)
    raw = tmp_path / "raw/stable_00000000.tga"
    raw.write_bytes(raw.read_bytes()[:-1]+b"\xff")
    with pytest.raises(ValueError, match="frame index/hash"):
        load_validation(out, parsed, dataset)
