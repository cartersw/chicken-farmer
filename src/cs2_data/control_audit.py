"""Reverify accepted observations before auditing two-command control candidates.

Existing single-command acceptance remains immutable. A successful audit is
diagnostic evidence for an angular aggregate, never calibrated live controls or
a new training approval. Semantic button channels remain masked.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .acceptance import state_reasons
from .causal_acceptance import (_command_source, _same, _source_envelopes,
                                load_causal_acceptance)
from .causal_targets import IDENTITY
from .control_contract import (CANDIDATE_PROFILE, CONTRACT_ID, DECISION_PERIOD_NS,
                               build_control_candidate, control_contract_schema,
                               validate_control_action)
from .io import (exclusive_output, parsed_manifest, publish, read_json, sha256_file,
                 staging_paths)
from .timing import read_ledger
from .validation import load_state_context

PRODUCER = "cs2-control-audit-v1"
FILES = ("control_contract.schema.json", "control_candidates.jsonl")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))+"\n").encode("utf-8")


@exclusive_output(file_output=True)
def write_control_contract(out: Path) -> dict[str, Any]:
    """Publish the proposed action schema without replacing any existing file."""
    payload = _json_bytes(control_contract_schema())
    temporary = staging_paths([out])
    try:
        temporary[0].write_bytes(payload)
        publish(temporary, [out])
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return {"contract_id": CONTRACT_ID, "schema_path": str(out.resolve()),
            "schema_sha256": hashlib.sha256(payload).hexdigest(),
            "decision_period_ns": DECISION_PERIOD_NS, "calibration_status": "unmeasured",
            "training_ready": False, "live_control_ready": False}


def _recompute(acceptance: Path) -> tuple[dict[str, Any], list[bytes]]:
    acceptance = acceptance / "causal_acceptance.json" if acceptance.is_dir() else acceptance
    acceptance = acceptance.resolve()
    watched: dict[Path, str] = {}

    def watch(path: Path, expected: str | None = None) -> str:
        path = path.resolve()
        # The same historical image appears in several overlapping samples.
        # Hash it once here and once at completion, rather than per occurrence.
        if path in watched:
            if expected is not None and watched[path] != expected:
                raise ValueError("Control audit input hash mismatch: "+str(path))
            return watched[path]
        digest = sha256_file(path)
        if expected is not None and digest != expected:
            raise ValueError("Control audit input hash mismatch: "+str(path))
        watched[path] = digest
        return digest

    acceptance_hash = watch(acceptance)
    # This performs the fresh demo scan, native packet and message checks, pixel
    # hashes, exact sample comparison and server-command support verification.
    source_report = load_causal_acceptance(acceptance)
    if source_report.get("status") != "complete":
        raise ValueError("Control audit requires a completed recomputed causal acceptance")
    identity = {name: source_report[name] for name in IDENTITY}
    parsed = Path(source_report["inputs"]["parsed"])
    context_path = Path(source_report["inputs"]["state_context"])
    source_dir = acceptance.parent
    for name, digest in source_report["files"].items():
        watch(source_dir / name, digest)
    watch(parsed / "manifest.json", source_report["input_hashes"]["canonical_manifest"])
    watch(context_path, source_report["input_hashes"]["state_context"])
    canonical = parsed_manifest(parsed, ("usercmd.parquet", "player_state.parquet", "rounds.parquet"))
    if type(canonical.get("tick_rate")) not in (int, float) or canonical["tick_rate"] != 64:
        raise ValueError("Control candidate profile requires a 64 Hz canonical demo")
    for name in ("usercmd.parquet", "player_state.parquet", "rounds.parquet"):
        watch(parsed / name, canonical["files"][name])
    samples = read_ledger(source_dir / "accepted_samples.jsonl")
    if len(samples) != source_report["accepted_count"]:
        raise ValueError("Control audit source sample count disagrees")
    support = source_report["proof"]["support"]
    if support.get("status") != "verified" or support.get("support_interval") != "[N-1,N]":
        raise ValueError("Control audit requires reverified enclosing command support")
    packet_bounds = source_report["proof"]["packet_bounds"]
    bounds = {row["capture_index"]: row for row in packet_bounds["frames"]}
    if len(bounds) != len(packet_bounds["frames"]):
        raise ValueError("Control audit source frame bounds contain duplicate identities")
    packet_source = read_json(source_dir / "packet_source_evidence.json")
    source_envelopes = _source_envelopes(packet_source)
    if packet_source.get("source_demo_sha256") != identity["demo_id"]:
        raise ValueError("Control audit packet source identity disagrees")
    condition = ((ds.field("demo_id") == identity["demo_id"]) & (ds.field("round_id") == identity["round_id"])
                 & (ds.field("steam_id") == int(identity["steam_id"])) & (ds.field("player_slot") == identity["player_slot"]))
    commands = ds.dataset(parsed / "usercmd.parquet").to_table(filter=condition).to_pylist()
    by_id = {row["command_row_id"]: row for row in commands}
    if len(by_id) != len(commands):
        raise ValueError("Control audit canonical command identities are duplicated")
    state_rows = ds.dataset(parsed / "player_state.parquet").to_table(filter=condition).to_pylist()
    states = {row["demo_tick"]: row for row in state_rows}
    if len(states) != len(state_rows):
        raise ValueError("Control audit canonical state ticks are duplicated")
    round_rows = pq.read_table(parsed / "rounds.parquet").to_pylist()
    rounds = {row["round_id"]: row for row in round_rows}
    if len(rounds) != len(round_rows):
        raise ValueError("Control audit canonical round identities are duplicated")
    context = load_state_context(context_path, canonical)
    segments = [dict(row, _pause_evidence_verified=context["pause_evidence_verified"] is True)
                for row in context["segments"]]
    candidates = []
    seen_samples: set[str] = set()
    for sample in samples:
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in seen_samples:
            raise ValueError("Control audit source sample identity is missing or duplicated")
        seen_samples.add(sample_id)
        if sample.get("training_ready") is not True or sample.get("contract_sha256") != source_report["contract_sha256"]:
            raise ValueError("Control audit source sample is not in the recomputed acceptance contract")
        for name in IDENTITY:
            if str(sample.get(name)) != str(identity[name]):
                raise ValueError("Control audit source sample identity disagrees")
        index = sample["observation_frame_index"]
        bound = bounds.get(index, {})
        upper = bound.get("upper_server_tick")
        if (bound.get("status") != "verified" or bound.get("verified") is not True
                or type(upper) is not int or sample.get("observation_upper_execution_tick") != upper):
            raise ValueError("Control audit source observation bound is unavailable or inconsistent")
        value = build_control_candidate(commands, observation_upper_execution_tick=upper,
                                        identity=identity, observation_frame_index=index)
        value.update(source_sample_id=sample_id, source_acceptance_contract_sha256=source_report["contract_sha256"],
                     source_acceptance_sha256=acceptance_hash, images=sample["images"],
                     local_command_quality_valid=value["candidate_valid"],
                     frame_bound_verified_by_audit=True)
        reasons = set(value["reason_codes"])
        if (value["normalization_predecessor_command_row_id"] is not None
                and value["normalization_predecessor_command_row_id"] not in sample["normalization_predecessor_command_row_ids"]):
            reasons.add("source_normalization_predecessor_disagrees")
        if value["target_command_row_ids"] and value["target_command_row_ids"][:1] != sample["target_command_row_ids"]:
            reasons.add("source_first_target_disagrees")
        cutoff = sample.get("target_cutoff_execution_tick")
        if type(cutoff) is not int or upper+4 > cutoff:
            reasons.add("control_window_exceeds_verified_source_horizon")
        for image in sample["images"]:
            watch(Path(image["path"]), image["sha256"])
        required = [value["normalization_predecessor_command_row_id"], *value["target_command_row_ids"]]
        selected = [by_id[rid] for rid in required if rid is not None and rid in by_id]
        source_associations = []
        for row in selected:
            association = _command_source(row, source_envelopes)
            if association is None:
                reasons.add("contributing_live_source_envelope_unavailable_or_ambiguous")
            source_associations.append({"command_row_id": row["command_row_id"], "source_envelope": association})
        value["source_command_associations"] = source_associations
        if len(selected) != 3:
            reasons.add("complete_control_window_unavailable")
        first_tick = sample["checked_state_tick_start"]
        last_tick = max([sample["checked_state_tick_end_exclusive"]-1, *(row["demo_tick"] for row in selected)])
        if type(first_tick) is not int or type(last_tick) is not int or not 0 <= last_tick-first_tick <= 20000:
            reasons.add("control_state_window_out_of_supported_range")
        else:
            reasons |= state_reasons(states, range(first_tick, last_tick+1), identity,
                                     rounds.get(identity["round_id"], {}), segments)
        value.update(checked_state_tick_start=first_tick, checked_state_tick_end_exclusive=last_tick+1)
        if reasons:
            # Keep raw provenance and diagnostic increments, but a rejected
            # evidence window cannot expose a usable action-valued channel.
            value["action"]["angular_delta_deg"] = None
            value["action"]["channel_status"]["angular_delta_deg"]["available"] = False
        action_errors = validate_control_action(value["action"])
        if action_errors:
            raise ValueError("Generated control action violates contract: "+", ".join(action_errors))
        value.update(candidate_valid=not reasons, reason_codes=sorted(reasons))
        candidates.append(value)
    payloads = [_json_bytes(control_contract_schema()), b"".join(_json_bytes(row) for row in candidates)]
    eligible = sum(row["candidate_valid"] for row in candidates)
    counts = Counter(reason for row in candidates for reason in row["reason_codes"])
    report = {"schema_version": 1, "producer": PRODUCER, "profile": CANDIDATE_PROFILE,
              "contract_id": CONTRACT_ID, "status": "complete", **identity, "clip_id": source_report["clip_id"],
              "inputs": {"acceptance": str(acceptance)}, "input_hashes": {"acceptance": acceptance_hash},
              "source_acceptance_contract_sha256": source_report["contract_sha256"],
              "source_acceptance_candidate_count": source_report["candidate_count"],
              "source_accepted_sample_count": source_report["accepted_count"],
              "candidate_count": len(candidates), "eligible_candidate_count": eligible,
              "rejected_candidate_count": len(candidates)-eligible, "reason_counts": dict(sorted(counts.items())),
              "decision_period_ns": DECISION_PERIOD_NS, "calibration_status": "unmeasured",
              "training_ready": False, "training_ready_sample_count": 0,
              "live_control_ready": False, "semantic_button_label_count": 0,
              "proof": {"source_acceptance_recomputed": True,
                        "all_contributing_source_envelopes_checked": True,
                        "complete_control_state_windows_checked": True,
                        "canonical_usercmd_sha256": canonical["files"]["usercmd.parquet"],
                        "source_packet_evidence_sha256": source_report["files"]["packet_source_evidence.json"]},
              "limits": ["diagnostic_angular_aggregate_not_calibrated_controls",
                         "semantic_button_channels_remain_masked", "exact_subtick_event_times_unknown",
                         "original_acceptance_artifacts_unchanged", "only_previously_accepted_observation_histories_audited"],
              "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in zip(FILES, payloads)}}
    for path, digest in watched.items():
        if sha256_file(path) != digest:
            raise ValueError("Control audit input changed during verification: "+str(path))
    return report, payloads


@exclusive_output()
def audit_control_candidates(acceptance: Path, out: Path) -> dict[str, Any]:
    """Publish a fresh immutable diagnostic audit beside the original acceptance."""
    if any(path.name != ".cs2-data.lock" for path in out.iterdir()):
        raise ValueError("Control audit requires a fresh output directory")
    report, payloads = _recompute(acceptance)
    destinations = [out / name for name in (*FILES, "control_audit.json")]
    temporary = staging_paths(destinations)
    try:
        for path, payload in zip(temporary, [*payloads, _json_bytes(report)]):
            with path.open("xb") as handle:
                handle.write(payload)
        publish(temporary, destinations)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return report


def load_control_audit(path: Path) -> dict[str, Any]:
    """Recompute original acceptance, candidate evidence and exact output bytes."""
    path = path / "control_audit.json" if path.is_dir() else path
    report = read_json(path)
    if report.get("producer") != PRODUCER or report.get("profile") != CANDIDATE_PROFILE:
        raise ValueError("Unsupported control candidate audit")
    try:
        expected, payloads = _recompute(Path(report["inputs"]["acceptance"]))
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid control audit input identity") from exc
    if not _same(report, expected):
        raise ValueError("Control audit report disagrees with recomputed evidence")
    for name, payload in zip(FILES, payloads):
        if (path.parent / name).read_bytes() != payload:
            raise ValueError("Control candidate file disagrees with recomputed evidence: "+name)
    return report
