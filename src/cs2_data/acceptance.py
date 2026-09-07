"""Fail-closed, evidence-bound acceptance of temporal image/action samples.

This stage never changes a capture, alignment, validation report or canonical row.
Only individual accepted records receive training_ready=True.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .io import exclusive_output, parsed_manifest, publish, read_json, sha256_file, staging_paths, write_json
from .jobs import phase_evidence
from .normalize import effective_scalar, transition

IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot")
REQUIRED_CHECKS = ("capture_integrity", "pixel_correspondence", "pov_identity", "observation_clock",
                   "execution_clock", "visual_fire", "visual_aim", "visual_move", "visual_jump", "visual_crouch")
BOUNDARY_REASONS = {"insufficient_history_frames", "missing_previous_action_context", "insufficient_future_intervals"}
SCOPED_CHECKS = {"pov_identity", "visual_fire", "visual_aim", "visual_move", "visual_jump", "visual_crouch"}


def load_validation(path: Path, parsed: Path, dataset: Path,
                    state_context: Path | None = None) -> dict[str, Any]:
    # The validator independently recomputes evidence, so editing report statuses
    # cannot turn an unverified capture into accepted training samples.
    from .validation import load_validation as verified_load
    return verified_load(path, parsed, dataset, state_context=state_context)


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def same_identity(row: dict[str, Any], identity: dict[str, Any]) -> bool:
    return all(row.get(key) is not None and str(row[key]) == str(identity[key]) for key in IDENTITY)


def equal_values(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equal_values(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equal_values(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    return left == right


def command_reasons(row: dict[str, Any], previous: dict[str, Any] | None,
                    identity: dict[str, Any]) -> set[str]:
    reasons = set()
    if not isinstance(row.get("command_protobuf"), bytes) or not row["command_protobuf"]:
        reasons.add("missing_raw_command_protobuf")
    if not same_identity(row, identity):
        reasons.add("command_identity_mismatch")
    for field in ("base_present", "buttons_present", "viewangles_present"):
        if row.get(field) is not True:
            reasons.add("missing_" + field)
    for field in ("command_number", "client_tick", "server_tick_executed", "pawn_entity_handle"):
        if type(row.get(field)) is not int or row[field] < 0:
            reasons.add("missing_or_invalid_" + field)
    if row.get("server_tick_executed") == 0:
        reasons.add("unavailable_execution_tick")
    if row.get("alive") is not True:
        reasons.add("command_not_known_alive")
    for field in ("is_warmup", "is_freeze_time"):
        if row.get(field) is not False:
            reasons.add("command_" + field + "_not_false")
    for field in ("forwardmove", "leftmove", "upmove", "mousedx_raw", "mousedy_raw"):
        if not finite(effective_scalar(row, field, "base_present")):
            reasons.add("nonfinite_or_missing_action")
    for field in ("view_yaw", "view_pitch", "view_roll"):
        if not finite(effective_scalar(row, field, "viewangles_present")):
            reasons.add("nonfinite_or_missing_viewangles")
    for field in ("buttonstate1", "buttonstate2", "buttonstate3"):
        value = effective_scalar(row, field, "buttons_present")
        if type(value) is not int or not 0 <= value < 2**64:
            reasons.add("invalid_button_mask")
    for field in ("subtick_moves", "input_history"):
        entries = row.get(field)
        if not isinstance(entries, list):
            reasons.add("missing_" + field)
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                reasons.add("invalid_" + field)
                continue
            fractions = ("when",) if field == "subtick_moves" else ("render_tick_fraction", "player_tick_fraction")
            for name in fractions:
                # The repeated message is present; an omitted protobuf scalar has
                # its defined zero default. Explicit negative/nonfinite values stay invalid.
                value = entry.get(name)
                if value is not None and (not finite(value) or not 0 <= value <= 1):
                    reasons.add("invalid_subtick_fraction" if field == "subtick_moves" else "invalid_history_fraction")
            for name in ("analog_forward_delta", "analog_left_delta", "pitch_delta", "yaw_delta", "view_pitch", "view_yaw"):
                if entry.get(name) is not None and not finite(entry[name]):
                    reasons.add("nonfinite_nested_action")
    if previous is None:
        reasons.add("missing_command_predecessor")
    else:
        if type(previous.get("command_number")) is not int or row.get("command_number") != previous["command_number"] + 1:
            reasons.add("command_number_discontinuity")
        if previous.get("alive") is not True or previous.get("base_present") is not True or previous.get("viewangles_present") is not True:
            reasons.add("invalid_command_predecessor")
        for clock in ("demo_tick", "server_tick_executed"):
            if not finite(row.get(clock)) or not finite(previous.get(clock)) or not 0 <= row[clock] - previous[clock] <= 1:
                reasons.add(clock + "_discontinuity")
        if not finite(row.get("client_tick")) or not finite(previous.get("client_tick")) or row["client_tick"] < previous["client_tick"]:
            reasons.add("client_tick_reset")
        if row.get("pawn_entity_handle") != previous.get("pawn_entity_handle"):
            reasons.add("pawn_identity_change")
    if row.get("aim_valid") is not True or row.get("reset_reason") is not None:
        reasons.add("normalized_aim_invalid")
    if previous is not None:
        try:
            yaw, pitch, reset = transition(previous, row, max_gap_ticks=1)
        except (KeyError, TypeError, ValueError):
            yaw, pitch, reset = None, None, "invalid_input"
        for field, expected in (("delta_yaw_deg", yaw), ("delta_pitch_deg", pitch)):
            if reset is not None or not finite(row.get(field)) or expected is None or not math.isclose(row[field], expected, abs_tol=1e-4, rel_tol=1e-6):
                reasons.add("normalized_aim_mismatch")
    for axis in ("x", "y"):
        if row.get(f"moused{axis}_effective") != effective_scalar(row, f"moused{axis}_raw", "base_present"):
            reasons.add("normalized_mouse_mismatch")
    return reasons


def proof_reasons(report: dict[str, Any], frame_indices: set[int], command_ids: set[int]) -> set[str]:
    reasons = set()
    checks = report.get("checks", {})
    for name in REQUIRED_CHECKS:
        check = checks.get(name, {})
        status = check.get("status", "unknown")
        rows = check.get("evidence", [])
        # A bad observation elsewhere cannot invalidate independent good POV or
        # action observations here. This exception never waives integrity or
        # clock failures, and a global summary alone cannot supply local proof.
        local_passes = {item.get("frame_index") for item in rows
                        if item.get("status") == "passed" and type(item.get("frame_index")) is int}
        if status != "passed" and (name not in SCOPED_CHECKS or not frame_indices.issubset(local_passes)):
            reasons.add(f"validation_{name}_{status}" if status in ("failed", "unknown") else f"validation_{name}_unknown")
            continue
        for item in rows:
            touches = item.get("frame_index") in frame_indices or item.get("command_row_id") in command_ids
            if touches and item.get("status", "passed") != "passed":
                reasons.add(f"validation_{name}_" + ("failed" if item.get("status") == "failed" else "unknown"))
        if not check.get("method") or type(check.get("evidence_count")) is not int or check["evidence_count"] < 1:
            reasons.add(f"validation_{name}_missing_evidence")
        scope = check.get("scope", {})
        covered = scope.get("frame_indices", [])
        if not isinstance(covered, list) or any(type(i) is not int for i in covered) or not frame_indices.issubset(set(covered)):
            reasons.add(f"validation_{name}_scope_missing")
        if name == "execution_clock":
            ids = scope.get("command_row_ids", [])
            if not isinstance(ids, list) or any(type(i) is not int for i in ids) or not command_ids.issubset(set(ids)):
                reasons.add("validation_execution_clock_command_scope_missing")
    # Supplemental negative evidence must not be hidden behind broader passed
    # checks. A measured shot outside its alleged future interval is a rejection.
    for name in ("weapon_shot_clock", "weapon_shot_future"):
        check = checks.get(name, {})
        for item in check.get("evidence", []):
            if item.get("command_row_id") in command_ids and item.get("status") != "passed":
                reasons.add("validation_" + name + "_" + ("failed" if item.get("status") == "failed" else "unknown"))
    return reasons


def state_reasons(states: dict[int, dict[str, Any]], ticks: range, identity: dict[str, Any],
                  round_info: dict[str, Any], context: list[dict[str, Any]] | None = None) -> set[str]:
    reasons = set()
    for tick in ticks:
        row = states.get(tick)
        if row is None:
            reasons.add("missing_player_state")
            continue
        if not same_identity(row, identity):
            reasons.add("state_identity_mismatch")
        if row.get("alive") is not True:
            reasons.add("state_not_known_alive")
        flags = {key: row.get(key) for key in ("is_warmup", "is_freeze_time", "is_paused")}
        if context is not None:
            segments = [s for s in context if s["start_demo_tick"] <= tick < s["end_demo_tick"]]
            if len(segments) != 1 or segments[0].get("round_id") != identity["round_id"]:
                reasons.add("context_coverage_missing")
            else:
                segment = segments[0]
                if segment.get("ambiguous_tick") is not False:
                    reasons.add("context_ambiguous_tick")
                for key in flags:
                    if flags[key] is None:
                        if key == "is_paused" and segment.get("_pause_evidence_verified") is not True:
                            reasons.add("context_pause_evidence_unverified")
                        else:
                            flags[key] = segment.get(key)
                    elif segment.get(key) is not None and flags[key] != segment[key]:
                        reasons.add("context_state_disagrees")
                if segment.get("match_started") is not True or segment.get("game_phase") not in (2, 3):
                    reasons.add("context_not_live_competitive")
        for key, value in flags.items():
            if value is None:
                reasons.add(key + "_unknown")
            elif value is not False:
                reasons.add(key + "_active")
        start, end = round_info.get("freeze_end_tick"), round_info.get("end_tick")
        if start is None or end is None or not start <= tick < end:
            reasons.add("outside_live_round")
    return reasons


def sample_candidates(frames: list[dict[str, Any]], commands: dict[int, dict[str, Any]],
                      command_issues: dict[int, set[str]], states: dict[int, dict[str, Any]],
                      round_info: dict[str, Any], phase: dict[str, Any], validation: dict[str, Any],
                      identity: dict[str, Any], history_frames: int = 8, target_horizon_frames: int = 1,
                      include_previous_actions: bool = True, context: list[dict[str, Any]] | None = None):
    """Yield every candidate once, including boundary candidates with explicit rejection."""
    for end_index in range(len(frames)):
        first = end_index - history_frames + 1
        history = list(range(max(0, first), end_index + 1))
        targets = list(range(end_index, min(len(frames), end_index + target_horizon_frames)))
        previous = list(range(max(0, first-1), end_index)) if include_previous_actions else []
        reasons = set()
        if first < 0:
            reasons.add("insufficient_history_frames")
        if include_previous_actions and first < 1:
            reasons.add("missing_previous_action_context")
        if len(targets) < target_horizon_frames:
            reasons.add("insufficient_future_intervals")
        needed_frames = sorted(set(history + targets + previous))
        # Historical images alone do not expose their following inputs. When
        # previous actions are features, use only each image's PRECEDING interval.
        input_frames = sorted(set(targets + previous))
        needed_ids = sorted({rid for i in input_frames for rid in frames[i]["command_row_ids"]})
        target_ids = [rid for i in targets for rid in frames[i]["command_row_ids"]]
        previous_ids = [[rid for rid in frames[i]["command_row_ids"]] for i in previous]
        if any(not frames[i]["command_row_ids"] for i in targets + previous):
            reasons.add("empty_required_action_interval")
        if phase.get("phase_verified") is not True or phase.get("phase") != "competitive":
            reasons.add("competitive_phase_unverified")
        reasons |= proof_reasons(validation, set(needed_frames), set(needed_ids))
        for rid in needed_ids:
            reasons |= command_issues[rid]
        # Require observed canonical state across both replay cursor and mapped
        # action domains. No interpolation or absent-pause-as-false substitution.
        starts = [frames[i][key] for i in needed_frames for key in ("source_demo_tick_start", "action_window_demo_tick_start")]
        ends = [frames[i][key] for i in needed_frames for key in ("source_demo_tick_end", "action_window_demo_tick_end")]
        starts.extend(commands[rid]["demo_tick"] for rid in needed_ids)
        ends.extend(commands[rid]["demo_tick"] for rid in needed_ids)
        first_tick, last_tick = math.floor(min(starts)), math.ceil(max(ends))
        reasons |= state_reasons(states, range(first_tick, last_tick+1), identity, round_info, context)
        yield {**identity, "observation_frame_index": end_index,
               "history_frame_indices": history, "previous_action_interval_frame_indices": previous,
               "previous_action_command_row_ids": previous_ids, "target_interval_frame_indices": targets,
               "target_command_row_ids": target_ids, "checked_command_row_ids": needed_ids,
               "checked_state_tick_start": first_tick, "checked_state_tick_end_exclusive": last_tick+1,
               "reason_codes": sorted(reasons), "training_ready": not reasons}


@exclusive_output()
def accept_samples(parsed: Path, dataset: Path, validation: Path, out: Path,
                   history_frames: int = 8, target_horizon_frames: int = 1,
                   include_previous_actions: bool = True,
                   state_context: Path | None = None) -> dict[str, Any]:
    """Publish new accepted/rejected JSONL manifests after recomputing validation."""
    if type(history_frames) is not int or history_frames < 1 or type(target_horizon_frames) is not int or target_horizon_frames < 1:
        raise ValueError("history_frames and target_horizon_frames must be positive integers")
    if type(include_previous_actions) is not bool:
        raise ValueError("include_previous_actions must be boolean")
    if any(p.name != ".cs2-data.lock" for p in out.iterdir()):
        raise ValueError("Sample acceptance requires a fresh output directory")
    source = parsed_manifest(parsed, ("usercmd.parquet", "player_state.parquet", "rounds.parquet"))
    pipeline = read_json(dataset / "pipeline_manifest.json")
    alignment = read_json(dataset / "aligned/alignment_manifest.json")
    if (pipeline.get("status") != "complete" or pipeline.get("schema_version") != 1 or
            alignment.get("status") != "complete" or alignment.get("alignment_version") != 1 or
            pipeline.get("stages", {}).get("alignment") != alignment):
        raise ValueError("Acceptance requires a complete, matching pipeline/alignment manifest")
    identity = {key: alignment[key] for key in IDENTITY}
    if identity["demo_id"] != source["demo_id"] or pipeline.get("demo_id") != source["demo_id"] or pipeline.get("clip_id") != alignment["clip_id"]:
        raise ValueError("Acceptance source identities disagree")
    for name, digest in alignment.get("files", {}).items():
        if Path(name).name != name or sha256_file(dataset / "aligned" / name) != digest:
            raise ValueError("Aligned file hash mismatch")
    if any(name not in alignment.get("files", {}) for name in ("frame_alignment.parquet", "aligned_commands.parquet")):
        raise ValueError("Aligned artifact hashes are missing")
    if (alignment.get("source_usercmd_sha256") != source["files"]["usercmd.parquet"] or
            alignment.get("source_parsed_manifest_sha256") != sha256_file(parsed / "manifest.json")):
        raise ValueError("Canonical source provenance mismatch")
    validation = validation / "clip_validation.json" if validation.is_dir() else validation
    proof = load_validation(validation, parsed, dataset, state_context=state_context)
    if not same_identity(proof, identity) or proof.get("clip_id") != alignment["clip_id"]:
        raise ValueError("Validation identity disagrees with aligned source")
    frames = pq.read_table(dataset / "aligned/frame_alignment.parquet").to_pylist()
    aligned = pq.read_table(dataset / "aligned/aligned_commands.parquet").to_pylist()
    if not frames or len(frames) != alignment.get("num_frames") or len(aligned) != alignment.get("command_count"):
        raise ValueError("Aligned row counts disagree with manifest")
    by_id = {row["command_row_id"]: row for row in aligned}
    if len(by_id) != len(aligned):
        raise ValueError("Duplicated aligned command row ID")
    assigned = []
    for index, frame in enumerate(frames):
        if frame.get("frame_index") != index or not same_identity(frame, identity) or frame.get("clip_id") != alignment["clip_id"]:
            raise ValueError("Frame timeline/identity mismatch")
        for prefix in ("source_demo_tick", "action_window_demo_tick"):
            left, right = frame.get(prefix + "_start"), frame.get(prefix + "_end")
            if not finite(left) or not finite(right) or not 0 <= left < right:
                raise ValueError("Frame has invalid tick interval")
            if index and not math.isclose(left, frames[index-1][prefix + "_end"], rel_tol=0, abs_tol=1e-6):
                raise ValueError("Frame intervals have a gap or overlap")
        ids = frame.get("command_row_ids")
        if not isinstance(ids, list) or any(rid not in by_id or by_id[rid].get("frame_index") != index for rid in ids):
            raise ValueError("Frame command references disagree with aligned rows")
        assigned.extend(ids)
    if assigned != [row["command_row_id"] for row in aligned]:
        raise ValueError("Frame command references omit, repeat or reorder canonical inputs")
    predicate = (ds.field("demo_id") == identity["demo_id"]) & (ds.field("player_slot") == identity["player_slot"]) & (ds.field("round_id") == identity["round_id"])
    raw_rows = ds.dataset(parsed / "usercmd.parquet").to_table(filter=predicate).to_pylist()
    raw_rows = [r for r in raw_rows if str(r.get("steam_id")) == str(identity["steam_id"])]
    raw_rows.sort(key=lambda r: r["command_row_id"])
    raw_by_id = {row["command_row_id"]: row for row in raw_rows}
    if len(raw_by_id) != len(raw_rows):
        raise ValueError("Duplicate canonical command row ID")
    predecessors = {row["command_row_id"]: raw_rows[i-1] if i else None for i, row in enumerate(raw_rows)}
    issues = {}
    for rid, row in by_id.items():
        raw = raw_by_id.get(rid)
        if raw is None or not all(key in row and equal_values(value, row[key]) for key, value in raw.items()):
            raise ValueError("Aligned raw command differs from canonical source")
        issues[rid] = command_reasons(row, predecessors[rid], identity)
    rounds = {r["round_id"]: r for r in pq.read_table(parsed / "rounds.parquet").to_pylist()}
    round_info = rounds.get(identity["round_id"], {})
    clip = read_json(dataset / "timing/clip.json")
    if (sha256_file(dataset / "timing/clip.json") != alignment.get("source_clip_manifest_sha256") or
            sha256_file(dataset / "timing/frames.jsonl") != alignment.get("source_timing_sha256")):
        raise ValueError("Timing source provenance mismatch")
    phase_source = clip.get("source_job", {}).get("phase_evidence", {})
    phase_path = Path(phase_source["source_phase_path"]) if phase_source.get("source_phase_path") else None
    if phase_path is not None and sha256_file(phase_path) != phase_source.get("source_phase_sha256"):
        raise ValueError("Competitive phase source hash mismatch")
    phase = phase_evidence(phase_path, source, rounds).get(identity["round_id"], {})
    state_rows = ds.dataset(parsed / "player_state.parquet").to_table(filter=predicate).to_pylist()
    states = {row["demo_tick"]: row for row in state_rows}
    if len(states) != len(state_rows):
        raise ValueError("Duplicate player-state tick")
    context = None
    if state_context is not None:
        from .validation import load_state_context
        context_report = load_state_context(state_context, source)
        context_digest = sha256_file(state_context)
        if not any(item.get("role") == "state_context" and item.get("sha256") == context_digest and
                   Path(item.get("path", "")).resolve() == state_context.resolve()
                   for item in proof.get("source_files", [])):
            raise ValueError("State context is not bound to this validation report")
        context = [dict(segment, _pause_evidence_verified=context_report.get("pause_evidence_verified") is True)
                   for segment in context_report["segments"]]
    configuration = {"history_frames": history_frames, "target_horizon_frames": target_horizon_frames,
                     "include_previous_actions": include_previous_actions, "action_interval_convention": "(start,end]"}
    dataset_hash, validation_hash = sha256_file(dataset / "pipeline_manifest.json"), sha256_file(validation)
    destinations = [out / name for name in ("accepted_samples.jsonl", "rejected_samples.jsonl", "acceptance_manifest.json")]
    temporary = staging_paths(destinations)
    counts = Counter()
    accepted = rejected = eligible = 0
    try:
        with temporary[0].open("x", encoding="utf8") as good, temporary[1].open("x", encoding="utf8") as bad:
            for sample in sample_candidates(frames, by_id, issues, states, round_info, phase, proof, identity,
                                            history_frames, target_horizon_frames, include_previous_actions, context):
                if alignment.get("action_interval_convention") != "(start,end]":
                    sample["reason_codes"].append("future_action_convention_unverified")
                if alignment.get("normalization_included") is not True:
                    sample["reason_codes"].append("normalization_not_included")
                sample["reason_codes"] = sorted(set(sample["reason_codes"]))
                sample["training_ready"] = not sample["reason_codes"]
                sample["window_complete"] = not BOUNDARY_REASONS.intersection(sample["reason_codes"])
                sample["normalization_predecessor_command_row_ids"] = sorted({
                    predecessors[rid]["command_row_id"] for rid in sample["checked_command_row_ids"]
                    if predecessors[rid] is not None})
                key = json.dumps([1, dataset_hash, validation_hash, configuration, sample["observation_frame_index"]], sort_keys=True)
                sample.update(schema_version=1, sample_id=hashlib.sha256(key.encode()).hexdigest()[:32],
                              clip_id=alignment["clip_id"], configuration=configuration)
                json.dump(sample, good if sample["training_ready"] else bad, allow_nan=False)
                (good if sample["training_ready"] else bad).write("\n")
                accepted += sample["training_ready"]
                rejected += not sample["training_ready"]
                eligible += sample["window_complete"]
                counts.update(sample["reason_codes"])
        report = {"schema_version": 1, "acceptance_version": 1, "status": "complete", **identity,
                  "clip_id": alignment["clip_id"], "configuration": configuration,
                  "candidate_count": accepted+rejected, "eligible_window_count": eligible,
                  "eligible_window_definition": "All configured history images, previous action intervals, and future target intervals exist; quality and validation may still reject.",
                  "accepted_count": accepted, "rejected_count": rejected,
                  "training_ready_sample_count": accepted, "reason_counts": dict(sorted(counts.items())),
                  "source_dataset": str(dataset.resolve()), "source_dataset_sha256": dataset_hash,
                  "source_validation": str(validation.resolve()), "source_validation_sha256": validation_hash,
                  "source_state_context": str(state_context.resolve()) if state_context is not None else None,
                  "source_state_context_sha256": sha256_file(state_context) if state_context is not None else None,
                  "source_parsed_manifest_sha256": sha256_file(parsed / "manifest.json"),
                  "phase_evidence": phase, "files": {destinations[i].name: sha256_file(temporary[i]) for i in (0, 1)},
                  "training_ready_scope": "Only records in accepted_samples.jsonl are training_ready; source artifacts retain their original status."}
        write_json(temporary[2], report)
        publish(temporary, destinations)
    finally:
        for path in temporary:
            if path.exists():
                path.unlink()
    return report
