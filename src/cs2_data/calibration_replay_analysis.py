"""Audit retained calibration replay pixels and compare measured state clocks.

Numeric controller-tick equality is a diagnostic association, not a proof that
the original client and replay observed the same interpolation/input phase.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import struct

from .calibration_analysis import _read_ledger, _read_object
from .calibration_pixels import BINARIES, NATIVE_PROFILE, _audit, _integer, _owned, _require
from .io import exclusive_output, publish, sha256_file, staging_paths
from .timing import verify_readback_pixels


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(x) for x in value)


def _wrapped(value):
    return (value + 180) % 360 - 180


def _state(row, local):
    observation = row.get("native_observation", {})
    player = observation.get("local_player" if local else "observed_pov", {})
    pawn = player.get("pawn_state", {})
    tick = player.get("controller_tick_base")
    _require(player.get("status") == "observed" and _integer(tick), "Unavailable observed player/tick")
    _require(isinstance(player.get("steam_id"), str) and player["steam_id"].isdigit(), "Missing observed Steam identity")
    _require(isinstance(pawn, dict), "Malformed observed pawn state")
    return {"capture_index": row["capture_index"], "controller_tick_base": tick,
            "steam_id": player["steam_id"], "replay_demo_tick": row.get("replay_demo_tick"),
            "render_time_seconds": row.get("render_time_seconds"),
            "qpc_before": row.get("qpc_before"), "pawn_state": pawn,
            "player_identity": {key: player.get(key) for key in ("pawn_handle", "controller_handle", "life_state")},
            "rendered_camera": observation.get("rendered_camera", {})}


def _transitions(states):
    transitions = []
    for previous, current in zip(states, states[1:]):
        clocks = ("capture_index", "controller_tick_base", "replay_demo_tick", "render_time_seconds", "qpc_before")
        boundary = {"previous": {k: previous.get(k) for k in clocks}, "current": {k: current.get(k) for k in clocks}}
        if (current["steam_id"] != previous["steam_id"] or
                current.get("player_identity") != previous.get("player_identity") or
                current["controller_tick_base"] < previous["controller_tick_base"]):
            transitions.append({**boundary, "association": "identity_or_clock_boundary_not_state_transition", "changes": {},
                                "previous_identity": previous.get("player_identity"), "current_identity": current.get("player_identity")})
            continue
        before, after = previous["pawn_state"], current["pawn_state"]
        changes = {}
        for key in ("ammo_clip", "last_shot_time", "ducked", "duck_amount", "on_ground"):
            if key in before and key in after and before[key] != after[key]:
                changes[key] = {"before": before[key], "after": after[key]}
        if _vector(before.get("eye_angles")) and _vector(after.get("eye_angles")):
            delta = _wrapped(after["eye_angles"][1]-before["eye_angles"][1])
            if abs(delta) > 0.01:
                changes["eye_yaw"] = {"before": before["eye_angles"][1], "after": after["eye_angles"][1],
                                      "wrapped_delta_degrees": delta}
        if changes:
            transitions.append({**boundary, "changes": changes,
                                "association": "state_change_between_observations_not_exact_effect_time"})
    return transitions


def _compare_states(original, replay):
    indexed = defaultdict(list)
    for state in original:
        indexed[(state["steam_id"], state["controller_tick_base"])].append(state)
    replay_counts = Counter((s["steam_id"], s["controller_tick_base"]) for s in replay)
    rows = []
    for state in replay:
        matches = indexed[(state["steam_id"], state["controller_tick_base"])]
        candidates = replay_counts[(state["steam_id"], state["controller_tick_base"])]
        row = {"replay_capture_index": state["capture_index"], "controller_tick_base": state["controller_tick_base"],
               "source_candidates": len(matches), "replay_candidates": candidates, "status": "no_unique_numeric_tick_match"}
        if len(matches) == 1 and candidates == 1:
            source = matches[0]
            a, b = source["pawn_state"], state["pawn_state"]
            row.update(status="same_numeric_controller_tick", source_capture_index=source["capture_index"],
                       exact_phase_verified=False)
            for key in ("origin", "velocity"):
                if _vector(a.get(key)) and _vector(b.get(key)):
                    row[key+"_distance"] = math.dist(a[key], b[key])
            if _vector(a.get("eye_angles")) and _vector(b.get("eye_angles")):
                row["eye_angles_delta_degrees_wrapped"] = [_wrapped(y-x) for x, y in zip(a["eye_angles"], b["eye_angles"])]
            source_camera, replay_camera = source.get("rendered_camera", {}).get("angles"), state.get("rendered_camera", {}).get("angles")
            if _vector(source_camera) and _vector(replay_camera):
                row["rendered_camera_angles_delta_degrees_wrapped"] = [_wrapped(y-x) for x, y in zip(source_camera, replay_camera)]
            if _number(source.get("render_time_seconds")) and _number(state.get("render_time_seconds")):
                row["render_time_seconds_numeric_delta"] = state["render_time_seconds"]-source["render_time_seconds"]
            for key in ("ammo_clip", "ducked", "duck_amount", "on_ground", "last_shot_time"):
                if key in a and key in b:
                    row[key] = {"source": a[key], "replay": b[key], "equal": a[key] == b[key]}
        rows.append(row)
    return {"association": "same_steam_id_and_numeric_controller_tick_base_only", "clock_equivalence_verified": False,
            "matched_frames": sum(x["status"] == "same_numeric_controller_tick" for x in rows),
            "unmatched_or_ambiguous_frames": sum(x["status"] != "same_numeric_controller_tick" for x in rows), "frames": rows}


def _replay_pixels(run, source):
    manifests = list(run.glob("*.render.json"))
    _require(len(manifests) == 1, "Replay must contain exactly one render manifest")
    names = (manifests[0].name, "capture_ledger.jsonl", "capture_frame_files.json", "input.dem")
    paths = {name: _owned(run, name) for name in names}
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    worker = _read_object(paths[manifests[0].name])
    _require(worker.get("render_status") == "video_ready_timing_unverified" and
             type(worker.get("cs2_exit_code")) is int and worker["cs2_exit_code"] == 0,
             "Replay requires completed capture and clean process exit")
    _require(worker.get("capture_ledger") == "capture_ledger.jsonl" and
             worker.get("capture_ledger_sha256") == hashes["capture_ledger.jsonl"] and
             worker.get("capture_frame_files") == "capture_frame_files.json" and
             worker.get("capture_frame_files_sha256") == hashes["capture_frame_files.json"], "Replay ledger/archive hash mismatch")
    _require(hashes["input.dem"] == source["source_hashes"]["controlled.dem"] == worker.get("demo_id"),
             "Replay demo is not the audited source recording")
    _require(isinstance(source.get("plugin_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", source["plugin_sha256"]) and
             worker.get("plugin_sha256") == source["plugin_sha256"], "Replay and source must use the same inspected plugin")
    job = worker.get("source_job", {})
    _require(job.get("calibration_replay_profile") == "cs2-controlled-calibration-replay-v1", "Unsupported replay job profile")
    binding = job.get("calibration_source", {})
    for key, filename in (("calibration_report_sha256", "calibration.json"), ("native_ledger_sha256", "calibration_ledger.jsonl"),
                          ("demo_sha256", "controlled.dem")):
        _require(binding.get(key) == source["source_hashes"][filename], "Replay calibration source binding mismatch")
    records = _read_ledger(paths["capture_ledger.jsonl"])
    archive = _read_object(paths["capture_frame_files.json"])
    headers = [r for r in records if r["event"] == "header"]
    _require(len(headers) == 1 and records[0] is headers[0], "Missing/duplicate native replay header")
    header = headers[0]
    _require(header.get("schema_version") == 1 and header.get("native_profile") == NATIVE_PROFILE and
             header.get("hook") == "engine2.CMovieRecorder.movie_frame_submit+ReadTexturePixels" and
             header.get("engine_sha256") == BINARIES["bin/win64/engine2.dll"] and
             header.get("native_observation", {}).get("client_sha256") == BINARIES["csgo/bin/win64/client.dll"],
             "Unsupported native replay capture profile")
    movies = [r for r in records if r["event"] == "movie_frame"]
    pixels = [r for r in records if r["event"] == "pixel_readback"]
    ends = [r for r in records if r["event"] == "movie_end"]
    frames, count, prefix, clip = archive.get("frames"), worker.get("num_frames"), worker.get("capture_prefix"), worker.get("clip_id")
    _require(_integer(count) and 1 <= count <= 20000, "Invalid replay frame count")
    _require(isinstance(prefix, str) and re.fullmatch(r"[a-zA-Z0-9-]{1,100}", prefix) and
             isinstance(clip, str) and re.fullmatch(r"[a-zA-Z0-9-]{1,100}", clip), "Unsafe replay frame prefix")
    _require(isinstance(frames, list) and archive.get("schema_version") == 1 and
             archive.get("capture_prefix") == prefix and archive.get("archived_prefix") == clip and
             len(frames) == len(movies) == len(pixels) == count, "Replay frame/archive count or identity mismatch")
    _require(len(ends) == 1 and type(ends[0].get("next_capture_index")) is int and
             ends[0]["next_capture_index"] == count and ends[0].get("movie_name") == prefix+"_" and
             records.index(ends[0]) > records.index(movies[-1]), "Invalid replay movie endpoint")
    by_index = {}
    for pixel in pixels:
        candidate = pixel.get("submission_candidate", {})
        index = candidate.get("capture_index")
        _require(_integer(index) and index < count and index not in by_index, "Duplicate/invalid replay readback association")
        by_index[index] = pixel
    inventory, provenance = [], []
    for index, (frame, movie) in enumerate(zip(frames, movies)):
        source_name, archived_name = f"{prefix}_{index:08d}.tga", f"{clip}_{index:08d}.tga"
        _require(type(frame.get("capture_index")) is int and type(movie.get("capture_index")) is int and
                 frame["capture_index"] == movie["capture_index"] == index and
                 type(movie.get("counter_after")) is int and movie["counter_after"] == index+1,
                 "Replay capture counters are not contiguous")
        _require(frame.get("source_name") == movie.get("tga_filename") == source_name and
                 frame.get("archived_name") == archived_name and movie.get("movie_name") == prefix+"_",
                 "Replay archived/native filenames disagree")
        path = _owned(run, "frames/"+archived_name)
        digest = sha256_file(path)
        _require(digest == frame.get("sha256"), "Replay archived TGA hash mismatch")
        pixel = by_index[index]
        _require(all(k in movie and movie[k] == v for k, v in pixel["submission_candidate"].items()),
                 "Replay readback candidate disagrees with movie")
        with path.open("rb") as handle:
            raw_header = handle.read(18)
        _require(len(raw_header) == 18, "Truncated replay TGA")
        dimensions = struct.unpack_from("<HH", raw_header, 12)
        _require(type(pixel.get("width")) is int and type(pixel.get("height")) is int and
                 (pixel["width"], pixel["height"]) == dimensions, "Replay TGA/native dimensions disagree")
        pov = movie.get("native_observation", {}).get("observed_pov", {})
        stable = pov == pixel.get("native_observation", {}).get("observed_pov") == pixel.get("native_observation_after", {}).get("observed_pov")
        target = pov.get("pawn_handle")
        in_eye = (_integer(target) and target != 4294967295 and pov.get("status") == "observed" and
                  pov.get("steam_id") == worker.get("steam_id") and type(pov.get("observer_mode")) is int and
                  pov["observer_mode"] == 2 and target == pov.get("observer_target_handle") == pov.get("controller_pawn_handle") and
                  pov.get("camera_view_entity_handle") == 4294967295)
        inventory.append({"path": str(path)})
        provenance.append({"capture_index": index, "source_name": source_name, "archived_name": archived_name,
                           "sha256": digest, "pov_matches_in_eye_identity_checks": in_eye,
                           "pov_stable_across_submission_readback": stable})
    comparison = verify_readback_pixels(records, inventory)
    _require(all(sha256_file(Path(item["path"])) == row["sha256"] for item, row in zip(inventory, provenance)),
             "Replay TGA changed during audit")
    _require(all(sha256_file(paths[name]) == digest for name, digest in hashes.items()), "Replay evidence changed during audit")
    return {"source_hashes": hashes, "plugin_sha256": worker["plugin_sha256"], "native_profile": header["native_profile"],
            "binary_profile_evidence": "native_engine_client_header_and_same_guarded_plugin_as_source",
            "pixel_comparison": comparison, "frames": provenance,
            "pov_identity_check_frames": sum(r["pov_matches_in_eye_identity_checks"] for r in provenance),
            "pov_stable_frames": sum(r["pov_stable_across_submission_readback"] for r in provenance)}, movies


def _command_diagnostics(path, source):
    if path is None:
        return {"status": "not_supplied"}
    digest = sha256_file(path)
    report = _read_object(path)
    _require(report.get("demo_id") == source["source_hashes"]["controlled.dem"], "Command report demo mismatch")
    for name in ("controlled.dem", "calibration_ledger.jsonl"):
        _require(report.get("sources", {}).get(name, {}).get("sha256") == source["source_hashes"][name],
                 "Command diagnostic source binding mismatch")
    _require(sha256_file(path) == digest, "Command diagnostic changed during audit")
    return {"status": "linked_reported_command_diagnostics", "path": str(path), "sha256": digest,
            "recomputed_here": False, "reported": {k: report.get(k) for k in
                ("profile", "coverage", "command_clocks", "events", "state_transitions", "raw_command_transitions", "dispatches")}}


def comparison_summary(comparison):
    rows = comparison["frames"]
    summary = {"matched_frames": comparison["matched_frames"],
               "unmatched_or_ambiguous_frames": comparison["unmatched_or_ambiguous_frames"]}
    for key in ("origin_distance", "render_time_seconds_numeric_delta"):
        values = [r[key] for r in rows if key in r]
        summary[key] = {"observations": len(values), "minimum": min(values, default=None), "maximum": max(values, default=None)}
    for key in ("eye_angles_delta_degrees_wrapped", "rendered_camera_angles_delta_degrees_wrapped"):
        values = [abs(v) for r in rows for v in r.get(key, [])]
        summary[key] = {"maximum_absolute": max(values, default=None)}
    summary["equal_state_pairs"] = {key: sum(r.get(key, {}).get("equal") is True for r in rows)
        for key in ("ammo_clip", "ducked", "duck_amount", "on_ground", "last_shot_time")}
    return summary


def render_markdown(report):
    replay = report["replay_pixel_audit"]
    count = replay["pixel_comparison"]["matched_frames"]
    summary = comparison_summary(report["numeric_tick_state_comparison"])
    lines = ["# Original recording and replay comparison", "",
        "Diagnostic only. Training readiness and live-control readiness remain **false**.", "",
        f"All {count} replay images match their own native pixel readback. The original recording has "
        f"{report['source_pixel_audit']['pixel_comparison']['matched_frames']} independently matched images. "
        "This does not assert that original and replay images are equal.", "",
        f"Replay in-eye identity checks: {replay['pov_identity_check_frames']}/{count}; "
        f"stable POV through submission/readback: {replay['pov_stable_frames']}/{count}.", "",
        f"Matching Steam identity and numeric controller tick gives {summary['matched_frames']} unique pairs; "
        f"{summary['unmatched_or_ambiguous_frames']} frames lack an unambiguous pair. "
        "These numeric matches do not prove equal render or input-consumption phase.", "",
        "| State at matched controller ticks | Equal pairs |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in summary["equal_state_pairs"].items()]
    lines += ["", f"Maximum observed position difference: {summary['origin_distance']['maximum']} engine units. "
        f"Maximum wrapped pawn eye-angle difference: {summary['eye_angles_delta_degrees_wrapped']['maximum_absolute']} degrees. "
        f"Maximum wrapped rendered-camera angle difference: {summary['rendered_camera_angles_delta_degrees_wrapped']['maximum_absolute']} degrees.", "",
        "Replay minus original render-clock readings at matched controller ticks range from "
        f"{summary['render_time_seconds_numeric_delta']['minimum']} to {summary['render_time_seconds_numeric_delta']['maximum']} seconds. "
        "This measured difference is not an applied synchronization correction.", "",
        "| Observed change | Original frame boundary / controller ticks | Replay frame boundary / controller ticks |",
        "| --- | --- | --- |"]
    for key in ("ammo_clip", "ducked"):
        def boundaries(name):
            return [f"{x['previous']['capture_index']}→{x['current']['capture_index']} / "
                    f"{x['previous']['controller_tick_base']}→{x['current']['controller_tick_base']} "
                    f"({x['changes'][key]['before']}→{x['changes'][key]['after']})"
                    for x in report[name] if key in x["changes"]]
        a, b = boundaries("source_state_transitions"), boundaries("replay_state_transitions")
        lines.append(f"| {key} | {'; '.join(a) or 'unobserved'} | {'; '.join(b) or 'unobserved'} |")
    lines += ["", "Boundaries identify sampled state changes, not exact input or effect times. "
        "The JSON retains every measured clock, camera/eye angle, identity, frame hash and linked command-report provenance. "
        "Prediction, interpolation, recoil and HUD presentation remain relevant differences. "
        "No button semantics or original-to-replay pixel equivalence is certified.", ""]
    return "\n".join(lines)


@exclusive_output(file_output=True)
def analyze_calibration_replay(source_run: Path, replay_run: Path, out: Path, command_report: Path | None = None):
    """Publish a fresh diagnostic JSON; never accepts samples or equates clocks."""
    _require(out.suffix.lower() == ".json", "Replay report output must end in .json")
    markdown = out.with_suffix(".md")
    staged_markdown, staged = staging_paths([markdown, out])
    try:
        try:
            source_run, replay_run = Path(source_run).resolve(), Path(replay_run).resolve()
            _require(source_run != replay_run, "Source and replay runs must differ")
            source = _audit(source_run)
            replay, replay_movies = _replay_pixels(replay_run, source)
            source_records = _read_ledger(_owned(source_run, "capture_ledger.jsonl"))
            source_movies = [r for r in source_records if r["event"] == "movie_frame"]
            original_states = [_state(r, True) for r in source_movies]
            replay_states = [_state(r, False) for r in replay_movies]
            command = _command_diagnostics(command_report, source)
            for run, evidence in ((source_run, source), (replay_run, replay)):
                _require(all(sha256_file(_owned(run, name)) == digest for name, digest in evidence["source_hashes"].items()),
                         "Evidence changed before publication")
                for frame in evidence["frames"]:
                    name = frame.get("archived_name", frame.get("tga_filename"))
                    digest = frame.get("sha256", frame.get("file_sha256"))
                    _require(sha256_file(_owned(run, "frames/"+name)) == digest, "Raw frame changed before publication")
            report = {"schema_version": 1, "profile": "controlled_calibration_replay_diagnostics_v1",
                "status": "pixels_verified_state_comparison_diagnostic", "training_ready": False, "live_control_ready": False,
                "input_consumption_timing_verified": False, "original_to_replay_pixel_equivalence_verified": False,
                "button_semantics_verified": False, "clock_equivalence_verified": False,
                "source_run": str(source_run), "replay_run": str(replay_run),
                "verifier_source_sha256": sha256_file(Path(__file__)), "source_pixel_audit": source,
                "replay_pixel_audit": replay, "numeric_tick_state_comparison": _compare_states(original_states, replay_states),
                "source_observations": original_states, "replay_observations": replay_states,
                "source_state_transitions": _transitions(original_states), "replay_state_transitions": _transitions(replay_states),
                "command_diagnostics": command,
                "limits": ["Each image matches its own native readback; source and replay images are not asserted equal.",
                    "Controller tick equality is a numeric association; it does not certify the same render or input phase.",
                    "Pawn simulation_tick, simulation_time, render_time, demo cursor and controller tick remain distinct recorded fields.",
                    "State transitions bound sampled changes only; they are not exact input-consumption timestamps.",
                    "Command report evidence is linked and preserved, not independently re-extracted by this stage.",
                    "HUD differences, interpolation, prediction and recoil can change original versus replay pixels."]}
        except (AttributeError, TypeError, KeyError, IndexError) as error:
            raise ValueError("Malformed calibration replay evidence") from error
        report["comparison_summary"] = comparison_summary(report["numeric_tick_state_comparison"])
        staged_markdown.write_text(render_markdown(report), encoding="utf-8")
        report["markdown_sha256"] = sha256_file(staged_markdown)
        staged.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        publish([staged_markdown, staged], [markdown, out])
        return report
    finally:
        staged.unlink(missing_ok=True)
        staged_markdown.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--replay-run", type=Path, required=True)
    parser.add_argument("--command-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze_calibration_replay(args.source_run, args.replay_run, args.output, args.command_report)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Calibration replay audit failed: {error}\n")
    print(json.dumps({"status": result["status"], "replay_pixels": result["replay_pixel_audit"]["pixel_comparison"],
                      "numeric_tick_matches": result["numeric_tick_state_comparison"]["matched_frames"], "training_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
