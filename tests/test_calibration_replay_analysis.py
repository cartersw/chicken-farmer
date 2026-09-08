import copy
import json

import pytest

from cs2_data.calibration_replay_analysis import _compare_states, _transitions, analyze_calibration_replay, main
from test_calibration_pixels import digest, fixture as source_fixture, refresh, write_json


def fixture(tmp_path):
    source, worker, archive, records = source_fixture(tmp_path)
    for row in records:
        if row["event"] not in ("movie_frame", "pixel_readback"):
            continue
        movie = row["submission_candidate"] if row["event"] == "pixel_readback" else row
        index = movie["capture_index"]
        state = {"status": "observed", "steam_id": "76561198845209628", "controller_tick_base": 100+2*index,
                 "pawn_handle": 123, "pawn_state": {"origin": [index, 0, 0], "eye_angles": [0, 179 if index == 0 else -179, 0],
                 "ammo_clip": 20-index, "ducked": bool(index), "duck_amount": float(index)}}
        movie["native_observation"] = {"local_player": copy.deepcopy(state)}
        movie["render_time_seconds"] = 1+index/32
        movie["replay_demo_tick"] = -1
        if row["event"] == "pixel_readback":
            row["native_observation"] = {"local_player": copy.deepcopy(state)}
            row["native_observation_after"] = {"local_player": copy.deepcopy(state)}
    refresh(source, worker, archive, records)
    replay = tmp_path / "replay"
    (replay / "frames").mkdir(parents=True)
    (replay / "input.dem").write_bytes((source / "controlled.dem").read_bytes())
    clip, prefix = "example", "example-12345"
    movies = [r for r in records if r["event"] == "movie_frame"]
    rows = [copy.deepcopy(records[0])]
    replay_archive = {"schema_version": 1, "capture_prefix": prefix, "archived_prefix": clip, "frames": []}
    for index, movie in enumerate(movies):
        movie = copy.deepcopy(movie)
        movie.update(movie_name=prefix+"_", tga_filename=f"{prefix}_{index:08d}.tga", replay_demo_tick=11+2*index)
        state = movie["native_observation"].pop("local_player")
        state.update(observer_mode=2, observer_target_handle=123, controller_pawn_handle=123, camera_view_entity_handle=4294967295)
        movie["native_observation"]["observed_pov"] = state
        pixel = copy.deepcopy(next(r for r in records if r["event"] == "pixel_readback" and r["submission_candidate"]["capture_index"] == index))
        pixel["submission_candidate"] = {k: copy.deepcopy(v) for k, v in movie.items() if k != "counter_after"}
        pixel["native_observation"] = copy.deepcopy(movie["native_observation"])
        pixel["native_observation_after"] = copy.deepcopy(movie["native_observation"])
        rows += [pixel, movie]
        name = f"{clip}_{index:08d}.tga"
        (replay / "frames" / name).write_bytes((source / "frames" / archive["frames"][index]["archived_name"]).read_bytes())
        replay_archive["frames"].append({"capture_index": index, "source_name": movie["tga_filename"], "archived_name": name,
                                         "sha256": digest(replay / "frames" / name)})
    rows.append({"event": "movie_end", "movie_name": prefix+"_", "next_capture_index": 2})
    render = {"render_status": "video_ready_timing_unverified", "cs2_exit_code": 0, "num_frames": 2,
              "clip_id": clip, "capture_prefix": prefix, "steam_id": "76561198845209628",
              "demo_id": digest(source / "controlled.dem"), "plugin_sha256": worker["plugin_sha256"],
              "source_job": {"calibration_replay_profile": "cs2-controlled-calibration-replay-v1", "calibration_source": {
                  "calibration_report_sha256": digest(source / "calibration.json"),
                  "native_ledger_sha256": digest(source / "calibration_ledger.jsonl"), "demo_sha256": digest(source / "controlled.dem")}}}
    save(replay, render, replay_archive, rows)
    return source, replay, render, replay_archive, rows


def save(replay, render, archive, rows):
    write_json(replay / "capture_frame_files.json", archive)
    (replay / "capture_ledger.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    for name in ("capture_ledger", "capture_frame_files"):
        filename = name + (".jsonl" if name == "capture_ledger" else ".json")
        render[name] = filename
        render[name+"_sha256"] = digest(replay / filename)
    write_json(replay / "example.render.json", render)


def test_replay_pixels_and_numeric_states_without_causal_promotion(tmp_path):
    source, replay, *_ = fixture(tmp_path)
    out = tmp_path / "comparison.json"
    result = analyze_calibration_replay(source, replay, out)
    assert result["replay_pixel_audit"]["pixel_comparison"]["matched_frames"] == 2
    assert result["replay_pixel_audit"]["pov_identity_check_frames"] == 2
    assert result["numeric_tick_state_comparison"]["matched_frames"] == 2
    assert result["numeric_tick_state_comparison"]["frames"][1]["origin_distance"] == 0
    assert result["replay_state_transitions"][0]["changes"]["eye_yaw"]["wrapped_delta_degrees"] == 2
    for key in ("training_ready", "live_control_ready", "clock_equivalence_verified", "input_consumption_timing_verified",
                "original_to_replay_pixel_equivalence_verified", "button_semantics_verified"):
        assert result[key] is False
    assert result["command_diagnostics"]["status"] == "not_supplied"
    assert out.with_suffix(".md").is_file()
    assert digest(out.with_suffix(".md")) == result["markdown_sha256"]
    assert "does not assert that original and replay images are equal" in out.with_suffix(".md").read_text()
    with pytest.raises(ValueError, match="overwrite"):
        analyze_calibration_replay(source, replay, out)


@pytest.mark.parametrize("change,reason", [
    (lambda w,a,r: w.update(cs2_exit_code=3221225477), "clean process exit"),
    (lambda w,a,r: w.update(plugin_sha256="0"*64), "same inspected plugin"),
    (lambda w,a,r: w["source_job"]["calibration_source"].update(native_ledger_sha256="0"*64), "source binding"),
    (lambda w,a,r: r[0].update(native_profile="legacy"), "capture profile"),
    (lambda w,a,r: r[3].update(submission_candidate=copy.deepcopy(r[1]["submission_candidate"])), "association"),
    (lambda w,a,r: r[1].update(rgb_sha256="0"*64), "SHA256 disagrees"),
    (lambda w,a,r: r[1]["submission_candidate"].update(qpc_before=9999), "candidate disagrees"),
    (lambda w,a,r: r[1].update(width=1,height=2), "dimensions disagree"),
    (lambda w,a,r: a["frames"][0].update(archived_name="../outside.tga"), "filenames disagree"),
    (lambda w,a,r: r[-1].update(next_capture_index=True), "endpoint"),
    (lambda w,a,r: w.update(source_job=[]), "Malformed calibration replay"),
])
def test_invalid_replay_provenance_refuses_publication(tmp_path, change, reason):
    source, replay, render, archive, rows = fixture(tmp_path)
    change(render, archive, rows)
    save(replay, render, archive, rows)
    out = tmp_path / "result.json"
    with pytest.raises(ValueError, match=reason):
        analyze_calibration_replay(source, replay, out)
    assert not out.exists()
    assert not list(tmp_path.glob(".*partial*"))


def test_different_demo_cannot_compare(tmp_path):
    source, replay, *_ = fixture(tmp_path)
    (replay / "input.dem").write_bytes(b"different")
    with pytest.raises(ValueError, match="not the audited source"):
        analyze_calibration_replay(source, replay, tmp_path / "out.json")


def test_state_difference_is_reported_even_with_valid_pixels(tmp_path):
    source, replay, render, archive, rows = fixture(tmp_path)
    for row in rows:
        if row["event"] == "movie_frame":
            row["native_observation"]["observed_pov"]["pawn_state"]["origin"][0] += 10
        elif row["event"] == "pixel_readback":
            for obj in (row, row["submission_candidate"]):
                obj["native_observation"]["observed_pov"]["pawn_state"]["origin"][0] += 10
            row["native_observation_after"]["observed_pov"]["pawn_state"]["origin"][0] += 10
    save(replay, render, archive, rows)
    result = analyze_calibration_replay(source, replay, tmp_path / "out.json")
    assert result["numeric_tick_state_comparison"]["frames"][0]["origin_distance"] == 10
    assert result["replay_pixel_audit"]["pixel_comparison"]["verified"] is True


def test_ambiguous_controller_ticks_are_not_silently_selected():
    state = {"capture_index": 0, "controller_tick_base": 42, "steam_id": "123", "pawn_state": {}}
    result = _compare_states([state, dict(state, capture_index=1)], [state])
    assert result["matched_frames"] == 0
    assert result["frames"][0]["source_candidates"] == 2
    assert _transitions([state]) == []
    replay_duplicate = _compare_states([state], [state, dict(state, capture_index=1)])
    assert replay_duplicate["matched_frames"] == 0
    assert replay_duplicate["unmatched_or_ambiguous_frames"] == 2
    assert replay_duplicate["frames"][0]["replay_candidates"] == 2


def test_respawn_and_controller_reset_are_marked_as_identity_boundaries():
    before = {"capture_index": 0, "controller_tick_base": 42, "steam_id": "123", "pawn_state": {"ammo_clip": 0},
              "player_identity": {"pawn_handle": 100, "controller_handle": 1}}
    after = dict(before, capture_index=1, pawn_state={"ammo_clip": 20}, player_identity={"pawn_handle": 101, "controller_handle": 1})
    transition = _transitions([before, after])[0]
    assert transition["association"] == "identity_or_clock_boundary_not_state_transition"
    assert transition["changes"] == {}
    assert transition["current_identity"]["pawn_handle"] == 101


def test_late_raw_frame_mutation_cannot_publish_previous_verified_pixels(tmp_path, monkeypatch):
    import cs2_data.calibration_replay_analysis as module
    source, replay, *_ = fixture(tmp_path)
    original = module._command_diagnostics
    def mutate_after_pixel_audit(path, evidence):
        result = original(path, evidence)
        frame = next((replay / "frames").glob("*.tga"))
        frame.write_bytes(frame.read_bytes()+b"late-edit")
        return result
    monkeypatch.setattr(module, "_command_diagnostics", mutate_after_pixel_audit)
    with pytest.raises(ValueError, match="Raw frame changed before publication"):
        analyze_calibration_replay(source, replay, tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()
    assert not (tmp_path / "out.md").exists()


def test_command_report_is_bound_but_not_promoted(tmp_path):
    source, replay, *_ = fixture(tmp_path)
    command = tmp_path / "commands.json"
    data = {"demo_id": digest(source / "controlled.dem"), "training_ready": True, "sources": {
        name: {"sha256": digest(source / name)} for name in ("controlled.dem", "calibration_ledger.jsonl")}}
    write_json(command, data)
    result = analyze_calibration_replay(source, replay, tmp_path / "out.json", command)
    assert result["command_diagnostics"]["recomputed_here"] is False
    assert result["training_ready"] is False
    data["demo_id"] = "0"*64
    write_json(command, data)
    with pytest.raises(ValueError, match="Command report demo mismatch"):
        analyze_calibration_replay(source, replay, tmp_path / "bad.json", command)


def test_cli(tmp_path, capsys):
    source, replay, *_ = fixture(tmp_path)
    assert main(["--source-run", str(source), "--replay-run", str(replay), "--output", str(tmp_path / "out.json")]) == 0
    assert json.loads(capsys.readouterr().out)["training_ready"] is False
