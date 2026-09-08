"""Freshly verified competitive image histories with masked 32 Hz targets.

This is separate from both historical single-command acceptance and local-only
calibration labels. Production input is recomputed by the competitive replay
proof loader; a caller cannot supply a verified dictionary to this public API.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .acceptance import same_identity, state_reasons
from .causal_acceptance import _action_protobuf, _same
from .control_label_audit import canonical_row_sha256
from .control_labels import (_aim, _button, _cell, _finite, _raw, BUTTON_FIELDS,
                             BUTTON_MASKS, EXACT_CHANNELS)
from .io import exclusive_output, publish, read_json, sha256_file, staging_paths

PROFILE = "cs2-competitive-masked-control-acceptance-v2"
LABEL_PROFILE = "cs2-competitive-recorded-control-label-32hz-v2"
HISTORY_FRAMES = 8
DECISION_PERIOD_NS = 31_250_000
FILES = ("accepted_samples.jsonl", "rejected_samples.jsonl")
MANIFEST = "competitive_acceptance.json"
IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _proof_identity(evidence):
    return {"source_provenance": deepcopy(evidence["provenance"]),
        "source_support": deepcopy(evidence.get("source_support", {})),
        "competitive_phase": deepcopy(evidence.get("phase", {})),
        "semantic_buttons": json.loads(json.dumps(evidence.get("semantic_buttons", {}), allow_nan=False)),
        "source_files": deepcopy(evidence["source_files"])}


def _load_proof(parsed, dataset, network_clock, state_context):
    # The production dependency is a fixed independent verifier. No callback,
    # supplied proof object, trust flag or imported JSON profile is accepted.
    try:
        from .competitive_replay_proof import recompute_competitive_replay_proof
    except ImportError as error:
        raise ValueError("Current competitive replay source-proof loader is unavailable") from error
    return recompute_competitive_replay_proof(parsed, dataset, network_clock, state_context)


def _command_reasons(rows, identity):
    reasons = set()
    for index, row in enumerate(rows):
        if not same_identity(row, identity):
            reasons.add("command_identity_mismatch")
        for name in ("command_number", "command_row_id", "client_tick", "demo_tick", "server_tick_executed", "pawn_entity_handle"):
            if not _integer(row.get(name)):
                reasons.add("command_" + name + "_unavailable")
        if row.get("server_tick_executed") == 0:
            reasons.add("execution_tick_unavailable")
        if row.get("alive") is not True:
            reasons.add("command_not_known_alive")
        for name in ("is_warmup", "is_freeze_time"):
            if row.get(name) is not False:
                reasons.add("command_" + name + "_not_false")
        if row.get("is_paused") is True:
            reasons.add("command_paused")
        reasons |= _action_protobuf(row)[0]
        for name in ("subtick_moves", "input_history"):
            collection = row.get(name)
            if not isinstance(collection, list):
                reasons.add("missing_" + name)
                continue
            for item in collection:
                if not isinstance(item, dict):
                    reasons.add("invalid_" + name)
                    continue
                for field in (("when",) if name == "subtick_moves" else ("render_tick_fraction", "player_tick_fraction")):
                    value = item.get(field)
                    if value is not None and (not _finite(value) or not 0 <= value < 1):
                        reasons.add("unsupported_raw_fraction")
                for field in ("analog_forward_delta", "analog_left_delta", "pitch_delta", "yaw_delta", "view_yaw", "view_pitch"):
                    if item.get(field) is not None and not _finite(item[field]):
                        reasons.add("nonfinite_nested_action")
        if index:
            previous = rows[index-1]
            for name in ("command_number", "demo_tick", "server_tick_executed"):
                if not _integer(previous.get(name)) or not _integer(row.get(name)) or row[name] != previous[name] + 1:
                    reasons.add(name + "_discontinuity")
            if not _integer(previous.get("command_row_id")) or not _integer(row.get("command_row_id")) or row["command_row_id"] <= previous["command_row_id"]:
                reasons.add("canonical_source_order_not_strictly_increasing")
            # Generation time is not used to define the 64->32 Hz interval.
            # Competitive sources may have different client/render cadences;
            # only a reversal is a reset, with no local 0..2 delta assumption.
            if not _integer(previous.get("client_tick")) or not _integer(row.get("client_tick")) or row["client_tick"] < previous["client_tick"]:
                reasons.add("client_generation_clock_reset")
            if row.get("pawn_entity_handle") != previous.get("pawn_entity_handle"):
                reasons.add("command_pawn_changed")
    return reasons


def _label(rows, identity, semantics, support):
    reasons = _command_reasons(rows, identity)
    semantic_reason = "competitive_button_semantics_unverified"
    masks, supported_fields = {}, {}
    if (semantics.get("status") == "verified" and semantics.get("demo_id") == identity["demo_id"] and
        semantics.get("source_patch") == support.get("source_patch") and
        isinstance(semantics.get("proof_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", semantics["proof_sha256"])):
        masks = semantics.get("supported_button_masks", {})
        supported_fields = semantics.get("supported_fields", {})
        if not isinstance(masks, dict) or not isinstance(supported_fields, dict):
            masks, supported_fields = {}, {}
    row_proofs = semantics.get("row_proofs", {})
    if not isinstance(row_proofs, dict):
        row_proofs = {}
    selected_proofs = [row_proofs.get(row["command_row_id"], {}) for row in rows]
    row_bound = [isinstance(proof, dict) and proof.get("status") == "verified" and
                 proof.get("canonical_row_sha256") == canonical_row_sha256(row)
                 for row, proof in zip(rows, selected_proofs)]
    def row_supports(index, name, field):
        proof = selected_proofs[index]
        fields = proof.get("supported_fields", {}) if isinstance(proof, dict) else {}
        allowed = fields.get(name, []) if isinstance(fields, dict) else []
        return row_bound[index] and isinstance(allowed, list) and field in allowed
    compact_semantics = {key: deepcopy(semantics.get(key)) for key in
        ("profile", "status", "demo_id", "source_patch", "proof_sha256", "supported_button_masks", "supported_fields")}
    compact_semantics["row_proofs"] = {str(row["command_row_id"]): deepcopy(proof)
                                     for row, proof in zip(rows, selected_proofs)}
    result = {"schema_version": 1, "profile": LABEL_PROFILE, "decision_period_ns": DECISION_PERIOD_NS,
        "target_command_count": 2, "server_tick_period_ns": 15_625_000, "aim_delta_deg": {},
        "per_command_aim_delta_deg": {}, "buttons": {}, "raw_command_provenance": [_raw(row) for row in rows],
        "command_interval": {"predecessor_execution_tick": rows[0].get("server_tick_executed"),
            "target_execution_ticks": [r.get("server_tick_executed") for r in rows[1:]],
            "predecessor_command_row_id": rows[0].get("command_row_id"),
            "target_command_row_ids": [r.get("command_row_id") for r in rows[1:]],
            "basis": "two_consecutive_recorded_command_boundaries_not_physical_event_times"},
        "reason_codes": sorted(reasons), "label_valid": False, "fully_observed": False,
        "exact_input_timing_verified": False, "training_ready": False,
        "standalone_label_readiness_scope": "image_and_source_acceptance_is_on_the_enclosing_sample",
        "semantic_button_proof": compact_semantics if masks else None,
        **{name: _cell(reasons=["exact_event_count_order_and_time_unavailable"]) for name in EXACT_CHANNELS}}
    for axis in ("yaw", "pitch"):
        cell, deltas = (_cell(reasons=reasons), []) if reasons else _aim(rows, axis)
        result["aim_delta_deg"][axis] = cell
        result["per_command_aim_delta_deg"][axis] = deltas
    for name, mask in BUTTON_MASKS.items():
        local = reasons | ({semantic_reason} if type(masks.get(name)) is not int or masks.get(name) != mask else set())
        fields = supported_fields.get(name, [])
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            fields = []
        button = ({**{field: _cell(reasons=local) for field in BUTTON_FIELDS},
            "per_command_net_changed": [_cell(reasons=local), _cell(reasons=local)]} if local else _button(rows, mask))
        # A known button bit does not establish every plane/activity meaning.
        # Only the individually reviewed fields may contribute to training.
        for field in (*BUTTON_FIELDS, "per_command_net_changed"):
            if field not in fields:
                unknown = local | {"competitive_button_field_semantics_unverified"}
                button[field] = ([_cell(reasons=unknown), _cell(reasons=unknown)]
                    if field == "per_command_net_changed" else _cell(reasons=unknown))
            elif field == "per_command_net_changed":
                for index in (1, 2):
                    if not row_supports(index, name, field):
                        button[field][index-1] = _cell(reasons=local | {"competitive_button_row_semantics_unverified"})
            else:
                indices = {"held_start": (0,), "held_mid": (1,), "held_end": (2,)}.get(field, (1, 2))
                if not all(row_supports(index, name, field) for index in indices):
                    button[field] = _cell(reasons=local | {"competitive_button_row_semantics_unverified"})
        result["buttons"][name] = button
    cells = [*result["aim_delta_deg"].values()]
    for button in result["buttons"].values():
        for key, value in button.items():
            cells.extend(value if key == "per_command_net_changed" else [value])
    result["label_valid"] = not reasons and any(c["valid"] for c in cells)
    result["fully_observed"] = result["label_valid"] and all(c["valid"] for c in cells)
    return result


def _build_samples(evidence):
    """Private selection core; only the fixed loader supplies production evidence."""
    identity = evidence["identity"]
    _require(set(identity) == set(IDENTITY) and isinstance(identity.get("demo_id"), str) and
        re.fullmatch(r"[0-9a-f]{64}", identity["demo_id"]) and _integer(identity.get("round_id"), 1) and
        str(identity.get("steam_id", "")).isdigit() and int(identity["steam_id"]) > 0 and _integer(identity.get("player_slot")),
        "Invalid competitive source identity")
    frames, commands = evidence["frames"], evidence["commands"]
    _require(isinstance(frames, list) and 0 < len(frames) <= 10000 and isinstance(commands, list), "Invalid bounded competitive clip")
    _require([f.get("frame_index") for f in frames] == list(range(len(frames))) and
        all(type(f.get("frame_index")) is int for f in frames), "Competitive frame indices are not contiguous")
    support = evidence.get("source_support", {})
    global_reasons = set(evidence.get("reason_codes", []))
    if support.get("status") != "verified" or support.get("support_interval") != "[E-1,E]":
        global_reasons.add("recorded_source_command_support_unverified")
        global_reasons.update(support.get("reason_codes", []))
    phase = evidence.get("phase", {})
    if phase.get("phase") != "competitive" or phase.get("phase_verified") is not True:
        global_reasons.add("competitive_phase_unverified")
    by_execution = defaultdict(list)
    for row in commands:
        if same_identity(row, identity) and _integer(row.get("server_tick_executed")):
            by_execution[row["server_tick_executed"]].append(row)
    states = {}
    for row in evidence["states"]:
        tick = row.get("demo_tick")
        _require(_integer(tick) and tick not in states, "Duplicate or invalid canonical competitive state tick")
        states[tick] = row
    proof_hash = _digest(_proof_identity(evidence))
    candidates = []
    for index, frame in enumerate(frames):
        first = max(0, index-HISTORY_FRAMES+1)
        history = frames[first:index+1]
        reasons = set(global_reasons)
        if len(history) != HISTORY_FRAMES:
            reasons.add("insufficient_history_frames")
        for image in history:
            if image.get("verified") is not True:
                reasons.add("history_image_proof_unverified")
                reasons.update(image.get("reason_codes", []))
            if not _integer(image.get("observation_upper_execution_tick")):
                reasons.add("history_information_bound_unavailable")
            if not isinstance(image.get("clock_segment_id"), str) or not image["clock_segment_id"]:
                reasons.add("history_clock_segment_unavailable")
        segments = {image.get("clock_segment_id") for image in history}
        if len(segments) != 1:
            reasons.add("history_clock_segment_changed")
        bounds = [image.get("observation_upper_execution_tick") for image in history]
        upper = max(bounds) if all(_integer(v) for v in bounds) else None
        if upper is not None and any(b < a for a, b in zip(bounds, bounds[1:])):
            reasons.add("history_information_bound_regressed")
        chosen = []
        if upper is not None:
            for tick in (upper+2, upper+3, upper+4):
                hits = by_execution[tick]
                if len(hits) != 1:
                    reasons.add("missing_or_ambiguous_future_command")
                else:
                    chosen.append(hits[0])
        else:
            reasons.add("future_command_window_unavailable")
        selected_support = []
        label = None
        if len(chosen) == 3:
            label = _label(chosen, identity, evidence.get("semantic_buttons", {}), support)
            reasons.update(label["reason_codes"])
            for role, row in zip(("predecessor", "target0", "target1"), chosen):
                source = evidence.get("command_sources", {}).get(row["command_row_id"], {})
                envelope = source.get("source_envelope")
                if (source.get("status") != "verified" or not isinstance(envelope, dict) or
                    type(envelope.get("demo_command_kind")) is not int or envelope["demo_command_kind"] != 7 or
                    any(type(envelope.get(key)) is not int or envelope[key] != row[key]
                        for key in ("command_number", "server_tick_executed"))):
                    reasons.add("original_live_command_payload_binding_unverified")
                    reasons.update(source.get("reason_codes", []))
                tick = row["server_tick_executed"]
                selected_support.append({"role": role, "command_row_id": row["command_row_id"],
                    "command_number": row["command_number"], "server_tick_executed": tick,
                    "support_start_tick": tick-1, "support_end_tick": tick,
                    "source_envelope": deepcopy(source.get("source_envelope"))})
            if any(s["support_start_tick"] <= upper for s in selected_support):
                reasons.add("command_support_not_strictly_after_image_history")
            if not label["label_valid"]:
                reasons.add("no_available_scoped_target_fields")
        state_ticks = [row["demo_tick"] for row in chosen]
        for image in history:
            for key in ("source_demo_tick_start", "source_demo_tick_end", "upper_source_demo_tick"):
                if not _integer(image.get(key)):
                    reasons.add("history_source_state_tick_unavailable")
                else:
                    state_ticks.append(image[key])
        first_tick, last_tick = (min(state_ticks), max(state_ticks)) if state_ticks else (None, None)
        if first_tick is None or last_tick-first_tick > 20000:
            reasons.add("competitive_state_window_unavailable_or_unbounded")
        else:
            reasons |= state_reasons(states, range(first_tick, last_tick+1), identity, evidence["round_info"], evidence["context_segments"])
        sample = {"schema_version": 1, "profile": PROFILE, **identity, "steam_id": str(identity["steam_id"]),
            "clip_id": evidence["clip_id"], "sample_id": _digest([PROFILE, proof_hash, index])[:32],
            "observation_frame_index": index, "decision_period_ns": DECISION_PERIOD_NS,
            "images": [{key: image[key] for key in ("frame_index", "path", "sha256", "observation_upper_execution_tick") if key in image} for image in history],
            "observation_upper_execution_tick": upper, "command_support": selected_support, "label": label,
            "checked_state_tick_start": first_tick, "checked_state_tick_end_inclusive": last_tick,
            "previous_action_features_included": False, "exact_input_timing_verified": False,
            "proof_sha256": proof_hash, "reason_codes": sorted(reasons), "training_ready": not reasons}
        candidates.append(sample)
    return candidates


def _recompute(parsed, dataset, network_clock, state_context):
    evidence = _load_proof(parsed, dataset, network_clock, state_context)
    watched = {}
    for filename, expected in evidence["source_files"].items():
        path = Path(filename).resolve()
        _require(isinstance(expected, str) and sha256_file(path) == expected, "Competitive proof source hash mismatch: " + str(path))
        watched[str(path)] = expected
    for frame in evidence["frames"]:
        path = Path(frame["path"])
        _require(path.is_absolute() and isinstance(frame.get("sha256"), str) and sha256_file(path) == frame["sha256"], "Competitive image source hash mismatch")
        watched[str(path)] = frame["sha256"]
    implementation = str(Path(__file__).resolve())
    watched[implementation] = sha256_file(Path(implementation))
    samples = _build_samples(evidence)
    accepted = [s for s in samples if s["training_ready"]]
    rejected = [s for s in samples if not s["training_ready"]]
    payloads = [b"".join(_bytes(s) for s in partition) for partition in (accepted, rejected)]
    report = {"schema_version": 1, "producer": PROFILE, "profile": PROFILE, "status": "complete",
        **evidence["identity"], "clip_id": evidence["clip_id"],
        "configuration": {"history_frames": HISTORY_FRAMES, "decision_period_ns": DECISION_PERIOD_NS,
            "target_commands": 2, "normalization_predecessors": 1, "previous_action_features": False,
            "target_execution_offsets_from_history_bound": [3, 4], "predecessor_execution_offset_from_history_bound": 2},
        "candidate_count": len(samples), "accepted_count": len(accepted), "rejected_count": len(rejected),
        "training_ready_sample_count": len(accepted), "reason_counts": dict(sorted(Counter(r for s in rejected for r in s["reason_codes"]).items())),
        "inputs": {name: str(Path(value).resolve()) for name, value in zip(("parsed", "dataset", "network_clock", "state_context"),
            (parsed, dataset, network_clock, state_context))}, "source_files": watched,
        "proof": _proof_identity(evidence), "proof_sha256": _digest(_proof_identity(evidence)),
        "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in zip(FILES, payloads)},
        "training_ready_scope": "Only accepted sample fields whose validity masks are true; bounded future recorded-command targets.",
        "limits": ["No local-only profile or historical acceptance partition is promoted or rewritten.",
            "Unknown button semantics and absent protobuf parents remain masked; exact events/count/times are unavailable.",
            "Client generation ticks must not reverse; their spacing does not define decision duration.",
            "Wrapped adjacent angular differences are degree targets, independent of raw mouse-count conservation.",
            "No physical input-consumption timestamp or real-time controller latency is established."]}
    _require(all(sha256_file(Path(path)) == digest for path, digest in watched.items()), "Competitive source changed during acceptance")
    return report, payloads


@exclusive_output()
def accept_competitive_controls(parsed: Path, dataset: Path, network_clock: Path, state_context: Path, out: Path):
    _require(not any(out.iterdir()) or {p.name for p in out.iterdir()} == {".cs2-data.lock"}, "Competitive acceptance requires a fresh output directory")
    report, payloads = _recompute(parsed, dataset, network_clock, state_context)
    destinations = [out/name for name in (*FILES, MANIFEST)]
    staged = staging_paths(destinations)
    try:
        for path, payload in zip(staged, [*payloads, _bytes(report)]):
            path.write_bytes(payload)
        publish(staged, destinations)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return report


def load_competitive_acceptance(path: Path):
    path = Path(path)
    path = path/MANIFEST if path.is_dir() else path
    report = read_json(path)
    _require(report.get("producer") == PROFILE and report.get("profile") == PROFILE, "Unsupported competitive control acceptance")
    inputs = report.get("inputs", {})
    expected, payloads = _recompute(*(Path(inputs[name]) for name in ("parsed", "dataset", "network_clock", "state_context")))
    _require(_same(report, expected), "Competitive acceptance disagrees with independently recomputed source evidence")
    for name, payload in zip(FILES, payloads):
        _require((path.parent/name).read_bytes() == payload, "Competitive sample partition differs from recomputed records")
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("parsed", "dataset", "network-clock", "state-context", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = accept_competitive_controls(args.parsed, args.dataset, args.network_clock, args.state_context, args.out)
    print(json.dumps({"accepted_count": result["accepted_count"], "rejected_count": result["rejected_count"], "out": str(args.out)}))


if __name__ == "__main__":
    main()
