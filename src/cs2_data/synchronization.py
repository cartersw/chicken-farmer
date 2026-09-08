"""Recompute native/source clock associations; no inferred phase is certified.

This audit is intentionally separate from execution calibration and acceptance.
Matching received messages establishes clock provenance. It does not prove the
support of every command field or bound every source of information in pixels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

from .clock_evidence import NetworkClockEvidence, command_envelope_matches, load_network_clock
from .io import exclusive_output, parsed_manifest, publish, read_json, sha256_file, staging_paths, write_json
from .timing import read_ledger, verify_capture_evidence
from .session_evidence import SessionLedger, events, audit_cached
from .validation import pov_status
from .native_replay_profile import LEGACY_PROFILE, get_native_replay_profile, header_matches_profile

ENGINE_SHA = "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac"
CLIENT_SHA = "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4"
KINDS = ("net_tick", "packet_entities_dispatch", "packet_entities_apply", "user_commands")
COUNTERS = dict(zip(KINDS, ("net_tick_returns", "entity_dispatch_returns", "entity_apply_returns", "user_command_returns")))
LIMITS = ["complete_image_information_bound_not_established",
          "whole_command_support_not_established",
          "subtick_fields_do_not_reconstruct_complete_analog_or_aim_trajectory"]


def _integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid native clock " + name)
    return value


def _same(left, right):
    """JSON value equality without Python's bool/int or float/int aliases."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _nullable_int32(value, name):
    if value is not None and (type(value) is not int or not -2**31 <= value < 2**31):
        raise ValueError("Invalid native clock " + name)
    return value


def _source_indices(evidence):
    result = defaultdict(list)
    for row in evidence.records:
        kind = row["message_type"]
        if kind == "CNETMsg_Tick":
            key = (kind, row.get("network_tick"))
        elif kind == "CSVCMsg_PacketEntities":
            key = (kind, row.get("snapshot_server_tick"))
        else:
            key = (kind, *(row.get(k) for k in ("server_tick_executed", "player_slot", "command_number", "client_tick")))
        result[key].append(row)
    return result


def audit_native_messages(records, evidence, *, native_profile=LEGACY_PROFILE):
    return audit_cached("messages", records, (evidence.sha256, native_profile),
                        lambda: _audit_native_messages(records, evidence, native_profile=native_profile))


def _audit_native_messages(records: list[dict[str, Any]], evidence: NetworkClockEvidence, *,
                          native_profile=LEGACY_PROFILE) -> dict[str, Any]:
    """Check paired hooks and reproduce counters/maxima before each observation.

    The process-lifetime maximum deliberately survives seeks. Per-generation
    maxima describe native instrumentation; they are never promoted to an image
    bound. Raw source records are matched by explicit payload clocks, never by a
    fitted native playback offset. Source windows need only cover the capture.
    """
    profile = get_native_replay_profile(native_profile)
    if not isinstance(records, SessionLedger) and (not isinstance(records, list) or len(records) > 1000000 or any(not isinstance(row, dict) for row in records)):
        raise ValueError("Native clock audit exceeds bounded record count")
    header = records[0] if records else {}
    contract = header.get("native_clock", {})
    available = (isinstance(contract, dict) and type(contract.get("schema_version")) is int and contract["schema_version"] == 1
                 and header.get("event") == "header" and _same(header.get("schema_version"), 1)
                 and header_matches_profile(header, native_profile=native_profile)
                 and (native_profile == LEGACY_PROFILE or contract.get("engine_sha256") == profile["engine_sha256"])
                 and contract.get("scope") == "delivered_net_tick_packet_entities_and_user_command_envelopes"
                 and all(_same(contract.get(key), value) for key, value in
                         (("net_tick_slot", 88), ("packet_entities_dispatch_slot", 111),
                          ("packet_entities_apply_slot", 129), ("user_commands_slot", 128))))
    if native_profile != LEGACY_PROFILE:
        available = available and all(_same(contract.get(key), value) for key, value in (
            ("status", "prepared"), ("client_vtable_rva", "0x5335d8"),
            ("client_server_tick_offset", 892), ("net_tick_field_offset", 80),
            ("packet_entities_tick_field_offset", 192)))
    if not available:
        return {"status": "unavailable", "reason_codes": ["unsupported_or_missing_native_clock_contract"],
                "frames": [], "training_ready": False}
    source = _source_indices(evidence)
    pending, observations = {}, []
    previous_id = completed = generation = 0
    active_client = None
    counts, kind_counts, matches = Counter(), Counter(), Counter()
    last_tick = max_tick = max_entity = max_command = lifetime_max = None
    slot_max = {}
    persistent_reasons = set()
    epochs = []

    def max_valid(current, value):
        return max(current, value) if current is not None else value

    def observe(snapshot, row, ledger_index):
        reasons = set(persistent_reasons)
        if not isinstance(snapshot, dict) or not _same(snapshot.get("schema_version"), 1):
            return {"status": "unavailable", "reason_codes": ["missing_native_clock_snapshot"]}
        if snapshot.get("status") == "unavailable" and "client_generation" not in snapshot:
            return {"status": "unavailable", "reason_codes": [snapshot.get("reason") or "native_clock_unavailable"]}
        expected = {"client_generation": generation, "client_address": active_client,
                    "last_completed_invocation": completed, "handlers_in_flight": len(pending),
                    "last_delivered_net_tick": last_tick, "max_delivered_net_tick": max_tick,
                    "max_observed_entity_tick": max_entity,
                    "max_observed_user_command_execution_tick": max_command,
                    "max_observed_user_command_execution_ticks_by_slot": slot_max,
                    **{COUNTERS[kind]: counts[kind] for kind in KINDS}}
        for key, value in expected.items():
            if not _same(snapshot.get(key), value):
                raise ValueError("Native clock snapshot disagrees with preceding messages: " + key)
        for name in ("replay_demo_tick", "client_tick", "server_tick"):
            _nullable_int32(snapshot.get(name), name)
        if snapshot.get("healthy") is not True or snapshot.get("status") != "observed_message_clock":
            reasons.add(snapshot.get("reason") or "native_clock_unavailable")
        if pending:
            reasons.add("message_handler_in_flight")
        if snapshot.get("user_commands_target_is_noop") is not True:
            reasons.add("user_commands_consumer_unverified")
        if not _same(snapshot.get("server_tick"), last_tick):
            reasons.add("server_tick_not_last_delivered_net_tick")
        if not active_client or generation == 0:
            reasons.add("active_native_client_unavailable")
        # Exact installed-slot guards establish coverage of the selected hooks.
        # A valid generation need not invoke every kind (e.g. entity apply).
        joined = {}
        for field, kind, tick in (("net_tick", "CNETMsg_Tick", last_tick),
                                 ("packet_entities", "CSVCMsg_PacketEntities", max_entity)):
            rows = source.get((kind, tick), []) if tick is not None else []
            if len(rows) != 1:
                reasons.add(field + "_source_unavailable_or_ambiguous")
            joined[field] = {"clock_event_index": rows[0]["clock_event_index"],
                            "demo_tick": rows[0]["demo_tick"], "server_tick": tick} if len(rows) == 1 else None
        return {"status": "matched_message_clocks" if not reasons else "unavailable",
                "reason_codes": sorted(reasons), "ledger_record_index": ledger_index,
                "client_generation": generation, "replay_demo_tick": snapshot.get("replay_demo_tick"),
                "server_tick": snapshot.get("server_tick"), "client_tick": snapshot.get("client_tick"),
                "observed_message_maximum_process_lifetime": lifetime_max,
                "max_observed_entity_tick": max_entity, "max_observed_user_command_execution_tick": max_command,
                "source_records": joined, "causality_bound_verified": False, "training_ready": False}

    for ledger_index, row in enumerate(records.iter_events(("network_clock_epoch", "network_message", "movie_frame", "session_boundary")) if isinstance(records, SessionLedger) else records):
        event = row.get("event")
        if event == "network_clock_epoch":
            if not _same(row.get("schema_version"), 1):
                raise ValueError("Invalid native clock epoch schema")
            next_generation = _integer(row.get("client_generation"), "generation", minimum=1)
            if (next_generation != generation + 1 or not _same(row.get("handlers_in_flight"), len(pending))
                    or not _same(row.get("last_completed_invocation"), completed)):
                raise ValueError("Native clock epoch sequence or in-flight count changed")
            generation, active_client = next_generation, _integer(row.get("client_address"), "client_address")
            counts.clear()
            last_tick = max_tick = max_entity = max_command = None
            slot_max = {}
            epochs.append({"ledger_record_index": ledger_index, "client_generation": generation,
                           "reason": row.get("reason"), "process_lifetime_maximum_retained": lifetime_max})
        elif event == "network_message":
            kind, phase = row.get("kind"), row.get("phase")
            if type(row.get("schema_version")) is not int or row["schema_version"] != 1 or kind not in KINDS:
                raise ValueError("Unsupported native network message contract")
            invocation = _integer(row.get("invocation_index"), "invocation_index", minimum=1)
            _integer(row.get("client_generation"), "message generation", minimum=1)
            _integer(row.get("client_address"), "message client address")
            for name in ("active_client", "message_layout_verified", "clock_on_engine_thread", "has_explicit_tick"):
                if type(row.get(name)) is not bool:
                    raise ValueError("Invalid native message boolean: " + name)
            for name in ("raw_message_tick", "effective_server_tick", "server_tick_before", "prior_delivered_net_tick", "replay_demo_tick"):
                _nullable_int32(row.get(name), name)
            if phase == "entry":
                if invocation != previous_id + 1 or row.get("client_generation") != generation:
                    raise ValueError("Missing, reordered, or repeated native message entry")
                if row["active_client"] is not (bool(active_client) and row["client_address"] == active_client):
                    raise ValueError("Native active-client claim disagrees with epoch identity")
                if not _same(row.get("prior_delivered_net_tick"), last_tick):
                    raise ValueError("Native prior tick disagrees with committed messages")
                previous_id = invocation
                pending[invocation] = row
                kind_counts[kind] += 1
                if not _same(row.get("handlers_in_flight"), len(pending)):
                    raise ValueError("Native entry in-flight count disagrees with pairs")
                if row.get("message_layout_verified") is not True or row.get("clock_on_engine_thread") is not True:
                    persistent_reasons.add("native_handler_layout_or_thread_unverified")
                if row.get("message_layout_verified") is not True:
                    continue
                if kind != "user_commands":
                    expected_tick = row.get("raw_message_tick") if kind == "net_tick" or row["has_explicit_tick"] else row.get("server_tick_before")
                    if type(expected_tick) is not int or not _same(row.get("effective_server_tick"), expected_tick):
                        raise ValueError("Effective native tick disagrees with raw message and presence bit")
                if row.get("active_client") is not True:
                    continue
                if kind == "user_commands":
                    commands = row.get("commands")
                    if not isinstance(commands, list) or len(commands) > 4096:
                        raise ValueError("Invalid native command envelope collection")
                    if row.get("user_commands_target_is_noop") is not True:
                        persistent_reasons.add("user_commands_consumer_unverified")
                    for envelope_index, command in enumerate(commands):
                        if not isinstance(command, dict) or not _same(command.get("envelope_index"), envelope_index):
                            raise ValueError("Invalid native command envelope index or object")
                        for name in ("server_tick_executed", "player_slot", "command_number", "client_tick"):
                            _nullable_int32(command.get(name), name)
                        tick, slot = command.get("server_tick_executed"), command.get("player_slot")
                        if type(tick) is int and tick >= 0:
                            max_command, lifetime_max = max_valid(max_command, tick), max_valid(lifetime_max, tick)
                            if type(slot) is int and 0 <= slot < 64:
                                slot_max[str(slot)] = max(slot_max.get(str(slot), -1), tick)
                        key = ("CMsgServerUserCmd", *(command.get(k) for k in
                               ("server_tick_executed", "player_slot", "command_number", "client_tick")))
                        if key in source:
                            matches[kind] += len(source[key]) == 1
                else:
                    tick = row.get("effective_server_tick")
                    if type(tick) is not int:
                        raise ValueError("Missing effective native message tick")
                    if tick >= 0:
                        lifetime_max = max_valid(lifetime_max, tick)
                        if kind != "net_tick":
                            max_entity = max_valid(max_entity, tick)
                    source_kind = "CNETMsg_Tick" if kind == "net_tick" else "CSVCMsg_PacketEntities"
                    if (source_kind, tick) in source:
                        matches[kind] += len(source[(source_kind, tick)]) == 1
            elif phase == "return":
                before = pending.pop(invocation, None)
                if before is None:
                    raise ValueError("Native message return lacks its entry")
                for key in ("kind", "client_generation", "active_client", "client_address", "message_layout_verified",
                            "clock_on_engine_thread", "raw_message_tick", "effective_server_tick", "commands",
                            "has_explicit_tick", "server_tick_before", "prior_delivered_net_tick", "replay_demo_tick",
                            "user_commands_target_is_noop", "message_vtable_rva"):
                    if not _same(row.get(key), before.get(key)):
                        raise ValueError("Native message mutated between entry/return: " + key)
                if (not _same(row.get("handlers_in_flight"), len(pending))
                        or not _same(row.get("generation_after"), generation)):
                    raise ValueError("Native return generation or in-flight count mismatch")
                completed = max(completed, invocation)
                if row.get("healthy") is not True:
                    persistent_reasons.add(row.get("error") or "native_handler_unhealthy")
                if kind == "user_commands" and row.get("user_commands_target_is_noop_after") is not True:
                    persistent_reasons.add("user_commands_consumer_unverified_after_return")
                _nullable_int32(row.get("server_tick_after"), "server_tick_after")
                if type(row.get("original_result")) is not bool:
                    raise ValueError("Invalid native message original result")
                if before.get("active_client") is True and before["client_generation"] == generation and before.get("message_layout_verified") is True:
                    counts[kind] += 1
                    if kind == "net_tick" and type(before.get("raw_message_tick")) is int and before["raw_message_tick"] >= 0:
                        last_tick = before["raw_message_tick"]
                        max_tick = max_valid(max_tick, last_tick)
                        if row.get("original_result") is not True or not _same(row.get("server_tick_after"), last_tick):
                            persistent_reasons.add("native_net_tick_not_committed")
            else:
                raise ValueError("Unsupported native message phase")
        elif event in ("movie_frame", "movie_end", "session_boundary"):
            result = observe(row.get("native_clock"), row, ledger_index)
            result.update(event=event, frame_index=row.get("capture_index", row.get("next_capture_index")))
            observations.append(result)
    if pending:
        raise ValueError("Native ledger ended with incomplete message handlers")
    return {"status": "matched_message_clocks" if observations and all(r["status"] == "matched_message_clocks" for r in observations) else "unavailable",
            "reason_codes": sorted(persistent_reasons), "message_entries": dict(kind_counts),
            "scoped_source_matches": dict(matches), "client_epochs": epochs,
            "frames": observations, "training_ready": False}


def recompute_synchronization(parsed: Path, dataset: Path, network_clock: Path, *,
                              native_profile=LEGACY_PROFILE) -> dict[str, Any]:
    get_native_replay_profile(native_profile)
    canonical = parsed_manifest(parsed, ["usercmd.parquet"])
    clip_path, frame_path = dataset / "timing/clip.json", dataset / "timing/frames.jsonl"
    clip, frames = read_json(clip_path), read_ledger(frame_path)
    if clip.get("demo_id") != canonical["demo_id"]:
        raise ValueError("Clock audit canonical/capture demo mismatch")
    capture = verify_capture_evidence(clip, frames)
    evidence = load_network_clock(network_clock, canonical["demo_id"])
    if evidence.document["tick_rate"] != canonical["tick_rate"]:
        raise ValueError("Clock audit source tick rate mismatch")
    records = read_ledger(Path(clip["capture_evidence"]["ledger_path"]))
    start = int(min(frame["source_demo_tick_start"] for frame in frames)) - 8
    end = int(max(frame["source_demo_tick_end"] for frame in frames)) + 8
    if not any(w["start_demo_tick"] <= start and end <= w["end_demo_tick"] for w in evidence.document["windows"]):
        raise ValueError("Source clock window must cover the capture plus eight ticks on each side")
    condition = ((ds.field("steam_id") == int(clip["steam_id"])) & (ds.field("round_id") == clip["round_id"])
                 & (ds.field("player_slot") == clip["player_slot"]) & (ds.field("demo_tick") >= start) & (ds.field("demo_tick") < end))
    commands = ds.dataset(parsed / "usercmd.parquet").to_table(filter=condition).to_pylist()
    associations = command_envelope_matches(commands, evidence)
    native = audit_native_messages(records, evidence, native_profile=native_profile)
    readbacks = {row.get("submission_candidate", {}).get("capture_index"): row for row in events(records, ("pixel_readback",))}
    pov = []
    for row in events(records, ("movie_frame",)):
        if row.get("event") == "movie_frame":
            index = row["capture_index"]
            status, reasons = pov_status(row.get("native_observation", {}), readbacks.get(index), clip)
            pov.append({"frame_index": index, "status": status, "reason_codes": reasons})
    offsets = Counter(row["network_tick"] - row["demo_tick"] for row in evidence.records
                      if row["message_type"] == "CNETMsg_Tick" and row.get("network_tick") is not None)
    return {"schema_version": 1, "producer": "cs2-synchronization-audit-v1", "status": "complete",
            **({"native_profile": native_profile} if native_profile != LEGACY_PROFILE else {}),
            "demo_id": canonical["demo_id"], "clip_id": clip["clip_id"], "training_ready": False,
            "observation_bound_verified": False, "command_support_verified": False,
            "reason_codes": LIMITS, "num_frames": len(frames),
            "pixel_correspondence": capture["pixel_correspondence"], "pov_evidence": pov,
            "native_message_clock_audit": native, "canonical_envelope_associations": associations,
            "source_clock_diagnostics": {"net_tick_minus_demo_header_tick": {str(k): v for k, v in sorted(offsets.items())}},
            "inputs": {"parsed": str(parsed.resolve()), "dataset": str(dataset.resolve()),
                       "network_clock": str(network_clock.resolve()), "network_clock_sha256": evidence.sha256,
                       "source_usercmd_sha256": canonical["files"]["usercmd.parquet"],
                       "clip_sha256": sha256_file(clip_path), "frames_sha256": sha256_file(frame_path),
                       "capture_evidence": clip["capture_evidence"]}}


@exclusive_output()
def audit_synchronization(parsed: Path, dataset: Path, network_clock: Path, out: Path, *,
                          native_profile=LEGACY_PROFILE) -> dict[str, Any]:
    destination = out / "synchronization_audit.json"
    staged = staging_paths([destination])
    try:
        report = recompute_synchronization(parsed, dataset, network_clock, native_profile=native_profile)
        write_json(staged[0], report)
        publish(staged, [destination])
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return {"audit_path": str(destination.resolve()), "training_ready": False,
            "num_frames": report["num_frames"], "native_message_clocks": report["native_message_clock_audit"]["status"],
            "matched_command_envelopes": sum(r["status"] == "matched" for r in report["canonical_envelope_associations"]),
            "reason_codes": report["reason_codes"]}


def load_synchronization(path: Path) -> dict[str, Any]:
    report = read_json(path)
    inputs = report.get("inputs", {})
    selection = {"native_profile": report["native_profile"]} if "native_profile" in report else {}
    expected = recompute_synchronization(*(Path(inputs[name]) for name in ("parsed", "dataset", "network_clock")), **selection)
    # JSON output cannot carry NaNs; type-sensitive encoding rejects bool/int edits.
    import json
    if json.dumps(report, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("Synchronization audit disagrees with recomputed source evidence")
    return report
