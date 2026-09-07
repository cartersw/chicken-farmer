"""Recomputed acceptance for bounded future server-command targets.

The public boundary scans the immutable demo again and audits native packet
returns. Caller-authored clock dictionaries and edited acceptance flags cannot
authorize samples. This profile predicts recorded command values, not physical
mouse timing, replay input execution, or a reconstructed subtick trajectory.
"""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .acceptance import command_reasons, state_reasons
from .causal_targets import IDENTITY, select_causal_targets
from .clock_evidence import protobuf_fields, scalar
from .io import exclusive_output, parsed_manifest, publish, read_json, sha256_file, staging_paths
from .jobs import phase_evidence
from .normalize import effective_scalar, transition
from .packet_evidence import scan_demo_packets
from .synchronization import recompute_synchronization
from .timing import read_ledger
from .validation import load_state_context

PROFILE = "recorded_future_server_command_v1"
SUPPORT_PROFILE = "server-command-support-v1"
SERVER_SHA256 = "9e5749d77dcb68883477feae751a3f28068d119ec145edcb0e4d48d15b538d36"
SOURCE_PATCH = 14178
FILES = ("accepted_samples.jsonl", "rejected_samples.jsonl", "packet_source_evidence.json")
MAX_FRAMES = 10000


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))+"\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _same(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _one(fields, number, wire):
    values = fields.get(number, [])
    if not values:
        return None
    if len(values) != 1 or values[0][0] != wire:
        raise ValueError("Ambiguous canonical command protobuf field")
    return values[0][1]


def _action_protobuf(row: dict[str, Any]) -> tuple[set[str], int | None]:
    """Recheck action/presence columns and flags against the canonical raw PB."""
    reasons: set[str] = set()
    try:
        outer = protobuf_fields(row.get("command_protobuf"))
        raw_base = _one(outer, 1, 2)
        if row.get("base_present") is not (raw_base is not None):
            reasons.add("canonical_base_presence_disagrees_with_protobuf")
        if raw_base is None:
            return reasons | {"missing_base_present"}, None
        fields = protobuf_fields(raw_base)
        raw_buttons, raw_angles = _one(fields, 3, 2), _one(fields, 4, 2)
        for name, value in (("buttons_present", raw_buttons), ("viewangles_present", raw_angles)):
            if row.get(name) is not (value is not None):
                reasons.add("canonical_"+name+"_disagrees_with_protobuf")
        decoded = {}
        for name, number in (("forwardmove", 5), ("leftmove", 6), ("upmove", 7)):
            value = _one(fields, number, 5)
            decoded[name] = struct.unpack("<f", value)[0] if value is not None else None
        for name, number in (("impulse", 8), ("weaponselect", 9), ("mousedx_raw", 11), ("mousedy_raw", 12)):
            decoded[name] = scalar(fields, number, signed=True)
        if raw_buttons is not None:
            buttons = protobuf_fields(raw_buttons)
            for number in (1, 2, 3):
                decoded["buttonstate"+str(number)] = _one(buttons, number, 0)
        if raw_angles is not None:
            angles = protobuf_fields(raw_angles)
            for number, name in enumerate(("view_pitch", "view_yaw", "view_roll"), 1):
                value = _one(angles, number, 5)
                decoded[name] = struct.unpack("<f", value)[0] if value is not None else None
        for name, expected in decoded.items():
            actual = row.get(name)
            # Parquet retains the original float32 value as a Python float.
            if not _same(actual, expected):
                reasons.add("canonical_action_disagrees_with_protobuf")
        flags = scalar(fields, 21, signed=True)
        flags = 0 if flags is None else flags  # Known present base, defined PB default.
        if flags != 0:
            reasons.add("unsupported_cmd_flags")
        return reasons, flags
    except (ValueError, TypeError, struct.error):
        return reasons | {"canonical_action_protobuf_invalid"}, None


def _command_quality(row, previous, identity, *, normalization_predecessor=False):
    """Reuse intrinsic input checks without requiring a third delta contributor."""
    yaw, pitch, reset = transition(previous, row, max_gap_ticks=1) if previous is not None else (None, None, None)
    derived = {**row, "delta_yaw_deg": yaw, "delta_pitch_deg": pitch,
               "aim_valid": reset is None, "reset_reason": reset,
               "mousedx_effective": effective_scalar(row, "mousedx_raw", "base_present"),
               "mousedy_effective": effective_scalar(row, "mousedy_raw", "base_present")}
    reasons = command_reasons(derived, previous, identity)
    if normalization_predecessor:
        # Only this row's orientation contributes to the target difference;
        # its own previous difference is neither a feature nor a target.
        reasons.discard("missing_command_predecessor")
    raw_reasons, flags = _action_protobuf(row)
    reasons |= raw_reasons
    for name in ("weaponselect", "impulse", "mousedx_raw", "mousedy_raw"):
        value = effective_scalar(row, name, "base_present")
        if type(value) is not int or not -2**31 <= value < 2**31:
            reasons.add("invalid_integer_action")
    return reasons, derived, flags


def _source_envelopes(source):
    by_key = defaultdict(list)
    for packet in source.get("packets", []):
        if type(packet.get("demo_command_kind")) is not int or packet["demo_command_kind"] != 7:
            continue  # Signon and full checkpoints cannot establish live command support.
        for envelope in packet.get("command_envelopes", []):
            fields = (packet.get("demo_tick"), *(envelope.get(key) for key in
                      ("server_tick_executed", "player_slot", "command_number", "client_tick")))
            if any(type(value) is not int for value in fields):
                continue
            by_key[fields].append({"demo_command_kind": 7, "demo_tick": packet["demo_tick"],
                "source_command_index": packet["source_command_index"], "command_offset": packet["command_offset"],
                "packet_data_sha256": packet["packet_data_sha256"],
                "wire_index": envelope["wire_index"], "envelope_index": envelope["envelope_index"],
                "envelope_protobuf_sha256": envelope["protobuf_sha256"],
                **{key: envelope[key] for key in ("server_tick_executed", "player_slot", "command_number", "client_tick")}})
    return by_key


def _command_source(row, by_key):
    key = tuple(row.get(name) for name in ("demo_tick", "server_tick_executed", "player_slot", "command_number", "client_tick"))
    matches = by_key.get(key, [])
    return matches[0] if len(matches) == 1 else None


def _scan_through(records, frames):
    ticks = [frame["source_demo_tick_end"] for frame in frames]
    for row in records:
        if row.get("event") == "demo_packet_read" and row.get("phase") == "return" and row.get("source_demo_tick") is not None:
            ticks.append(row["source_demo_tick"])
        for snapshot in (row.get("packet_trace"), row.get("native_clock", {}).get("packet_trace"),
                         row.get("native_clock_after", {}).get("packet_trace")):
            if isinstance(snapshot, dict) and snapshot.get("process_lifetime_max_returned_source_demo_tick") is not None:
                ticks.append(snapshot["process_lifetime_max_returned_source_demo_tick"])
    if any(type(tick) is not int for tick in ticks):
        raise ValueError("Packet source coverage requires integer observed ticks")
    return max(ticks)+8


def _profile(source, clip, canonical):
    header = source.get("file_header") or {}
    server = Path(clip.get("game_dir", "")) / "csgo/bin/win64/server.dll"
    digest = sha256_file(server) if server.is_file() else None
    reasons = set()
    if type(header.get("patch_version")) is not int or header["patch_version"] != SOURCE_PATCH:
        reasons.add("source_server_patch_unsupported")
    if digest != SERVER_SHA256:
        reasons.add("inspected_server_binary_unavailable_or_changed")
    if (canonical.get("parser_version") != "v6.0.0-alpha.0" or str(canonical.get("parser_schema_version")) != "2"
            or canonical.get("extractor_version") != "0.1.2"):
        reasons.add("canonical_decoder_profile_unsupported")
    return {"profile": SUPPORT_PROFILE, "status": "verified" if not reasons else "unknown",
            "reason_codes": sorted(reasons), "source_patch": header.get("patch_version"),
            "source_file_header_sha256": header.get("protobuf_sha256"),
            "inspected_server_path": str(server.resolve()), "inspected_server_sha256": digest,
            "required_server_sha256": SERVER_SHA256, "support_interval": "[N-1,N]",
            "support_meaning": "enclosing_recorded_server_command_processing_step",
            "recording_server_binary_identity_verified": False,
            "scope": "source_patch_14178_with_inspected_command_protocol_and_processing_profile"}


def _empty_candidates(frames, identity, history_frames, horizon, reasons):
    for index in range(len(frames)):
        local = set(reasons)
        if index+1 < history_frames:
            local.add("insufficient_history_frames")
        if index+horizon > len(frames):
            local.add("insufficient_future_intervals")
        yield {**identity, "observation_frame_index": index,
            "history_frame_indices": list(range(max(0, index-history_frames+1), index+1)),
            "target_horizon_frame_indices": list(range(index, min(len(frames), index+horizon))),
            "target_command_row_ids": [], "normalization_predecessor_command_row_ids": [],
            "target_support": [], "normalization_predecessor_support": [],
            "previous_action_features_included": False, "previous_action_command_row_ids": [],
            "temporal_eligible": False, "training_ready": False, "reason_codes": sorted(local)}


def _recompute(parsed: Path, dataset: Path, network_clock: Path, state_context: Path,
               history_frames=8, target_horizon_frames=2):
    for name, value in (("history_frames", history_frames), ("target_horizon_frames", target_horizon_frames)):
        if type(value) is not int or not 1 <= value <= 1024:
            raise ValueError(name+" must be an integer between 1 and 1024")
    watched: dict[Path, str] = {}

    def watch(path: Path, expected: str | None = None):
        path = path.resolve()
        digest = sha256_file(path)
        if expected is not None and digest != expected:
            raise ValueError("Causal source hash mismatch: "+str(path))
        if path in watched and watched[path] != digest:
            raise ValueError("Causal source changed during verification: "+str(path))
        watched[path] = digest
        return digest

    for path in (parsed / "manifest.json", dataset / "timing/clip.json", dataset / "timing/frames.jsonl",
                 network_clock, state_context):
        watch(path)
    canonical = parsed_manifest(parsed, ("usercmd.parquet", "player_state.parquet", "rounds.parquet"))
    for name in ("usercmd.parquet", "player_state.parquet", "rounds.parquet"):
        watch(parsed / name, canonical["files"][name])
    watch(Path(canonical["source_path"]), canonical["demo_id"])
    clip = read_json(dataset / "timing/clip.json")
    frames = read_ledger(dataset / "timing/frames.jsonl")
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("Causal acceptance requires a bounded nonempty capture")
    if any(type(frame.get("frame_index")) is not int or frame["frame_index"] != index
           or any(type(frame.get(key)) is not int for key in ("source_demo_tick_start", "source_demo_tick_end"))
           for index, frame in enumerate(frames)):
        raise ValueError("Causal frame identities and observed ticks must be exact integers")
    identity = {key: clip[key] for key in IDENTITY}
    if identity["demo_id"] != canonical["demo_id"]:
        raise ValueError("Causal capture and canonical source disagree")
    synchronization = recompute_synchronization(parsed, dataset, network_clock)
    ledger_path = Path(clip["capture_evidence"]["ledger_path"])
    watch(ledger_path, clip["capture_evidence"].get("ledger_sha256"))
    records = read_ledger(ledger_path)
    inventory_path = Path(clip["capture_evidence"]["frame_inventory_path"])
    watch(inventory_path, clip["capture_evidence"].get("frame_inventory_sha256"))
    inventory = read_json(inventory_path)["frames"]
    if len(inventory) != len(frames):
        raise ValueError("Causal image inventory count mismatch")
    for item in inventory:
        watch(Path(item["path"]), item["sha256"])
    through = _scan_through(records, frames)
    if 0 <= through <= 20000:
        source = scan_demo_packets(Path(canonical["source_path"]), expected_sha256=canonical["demo_id"],
                                   through_demo_tick=through)
        from .packet_bounds import audit_packet_bounds
        packet_bounds = audit_packet_bounds(records, source)
    else:
        source = {"schema_version": 2, "source_demo_sha256": canonical["demo_id"], "status": "unavailable",
                  "reason_codes": ["packet_prefix_out_of_supported_range"], "through_demo_tick": through}
        packet_bounds = {"status": "unknown", "source_demo_sha256": canonical["demo_id"],
                         "reasons": ["packet_prefix_out_of_supported_range"], "frames": [], "endpoint": None}
    support = _profile(source, clip, canonical)
    if support["inspected_server_sha256"] is not None:
        watch(Path(support["inspected_server_path"]), support["inspected_server_sha256"])
    context = load_state_context(state_context, canonical)
    segments = [dict(row, _pause_evidence_verified=context["pause_evidence_verified"] is True) for row in context["segments"]]
    rounds_list = pq.read_table(parsed / "rounds.parquet").to_pylist()
    rounds = {row["round_id"]: row for row in rounds_list}
    if len(rounds) != len(rounds_list):
        raise ValueError("Duplicate canonical round identity")
    phase_claim = clip.get("source_job", {}).get("phase_evidence", {})
    phase_path = Path(phase_claim["source_phase_path"]) if phase_claim.get("source_phase_path") else None
    if phase_path is not None and sha256_file(phase_path) != phase_claim.get("source_phase_sha256"):
        raise ValueError("Causal acceptance phase provenance changed")
    if phase_path is not None:
        watch(phase_path, phase_claim["source_phase_sha256"])
    phase = phase_evidence(phase_path, canonical, rounds).get(identity["round_id"], {})
    condition = ((ds.field("demo_id") == identity["demo_id"]) & (ds.field("round_id") == identity["round_id"])
                 & (ds.field("player_slot") == identity["player_slot"]) & (ds.field("steam_id") == int(identity["steam_id"])))
    commands = ds.dataset(parsed / "usercmd.parquet").to_table(filter=condition).to_pylist()
    commands.sort(key=lambda row: row["command_row_id"])
    by_id = {row["command_row_id"]: row for row in commands}
    if len(by_id) != len(commands):
        raise ValueError("Duplicate canonical command identity")
    state_rows = ds.dataset(parsed / "player_state.parquet").to_table(filter=condition).to_pylist()
    states = {row["demo_tick"]: row for row in state_rows}
    if len(states) != len(state_rows):
        raise ValueError("Duplicate canonical player-state tick")
    packet_by_index = {}
    for row in packet_bounds.get("frames", []):
        index = row.get("capture_index")
        if type(index) is not int or index in packet_by_index or not 0 <= index < len(frames):
            raise ValueError("Packet bound frame identity is invalid or duplicated")
        packet_by_index[index] = row
    endpoint = packet_bounds.get("endpoint")
    if endpoint is not None:
        if type(endpoint.get("capture_index")) is not int or endpoint["capture_index"] != len(frames):
            raise ValueError("Packet bound endpoint identity disagrees with capture")
        packet_by_index[len(frames)] = endpoint
    if packet_bounds.get("source_demo_sha256") != canonical["demo_id"]:
        raise ValueError("Packet bound source identity mismatch")
    proof = {"support": support, "packet_bounds": packet_bounds,
             "packet_source_sha256": _digest(source), "synchronization_sha256": _digest(synchronization),
             "canonical_manifest_sha256": sha256_file(parsed / "manifest.json"),
             "context_sha256": sha256_file(state_context), "phase": phase}
    contract_hash = _digest(proof)
    bounds = []
    for index in range(len(frames)):
        current, following = packet_by_index.get(index, {}), packet_by_index.get(index+1, {})
        verified = all(row.get("status") == "verified" and row.get("verified") is True and
                       type(row.get("upper_server_tick")) is int and type(row.get("upper_source_demo_tick")) is int
                       for row in (current, following))
        start, end = current.get("upper_server_tick"), following.get("upper_server_tick")
        if verified and not 0 <= start < end:
            verified = False  # Stationary/regressed ceilings do not establish this profile's intervals.
        bounds.append({**identity, "frame_index": index, "contract_sha256": contract_hash,
            "clock_segment_id": "packet_information_process_lifetime",
            "observation_upper_execution_tick": start, "next_observation_upper_execution_tick": end,
            "bound_status": "verified" if verified else "unknown"})
    if support["status"] == "verified":
        selections = select_causal_targets(commands, bounds, identity=identity,
            support_contract={**identity, "schema_version": 1, "status": "verified", "support_interval": "[N-1,N]",
                              "execution_clock": "server_tick_executed", "contract_sha256": contract_hash},
            history_frames=history_frames, target_horizon_frames=target_horizon_frames, require_future_predecessor=True)
    else:
        selections = list(_empty_candidates(frames, identity, history_frames, target_horizon_frames, support["reason_codes"]))
    native = {(row.get("event"), row.get("frame_index")): row for row in synchronization["native_message_clock_audit"].get("frames", [])}
    pov = {row["frame_index"]: row for row in synchronization.get("pov_evidence", [])}
    envelopes = _source_envelopes(source)
    accepted, rejected = [], []
    configuration = {"history_frames": history_frames, "target_horizon_frames": target_horizon_frames,
                     "require_future_predecessor": True, "include_previous_actions": False,
                     "target_profile": PROFILE, "subtick_and_input_history_features": False}
    for selected in selections:
        sample = {**selected, "schema_version": 1, "profile": PROFILE, "clip_id": clip["clip_id"],
                  "contract_sha256": contract_hash, "images": [], "targets": [], "command_provenance": []}
        reasons = set(sample["reason_codes"])
        needed = sorted(set(sample["history_frame_indices"]+sample["target_horizon_frame_indices"]))
        if phase.get("phase") != "competitive" or phase.get("phase_verified") is not True:
            reasons.add("competitive_phase_unverified")
        if synchronization["pixel_correspondence"].get("verified") is not True:
            reasons.add("pixel_correspondence_unverified")
        for index in needed:
            if bounds[index]["bound_status"] != "verified":
                reasons.add("packet_information_bound_unknown")
                reasons.update(packet_by_index.get(index, {}).get("reasons", []))
            if native.get(("movie_frame", index), {}).get("status") != "matched_message_clocks":
                reasons.add("native_message_clock_association_unverified")
        for index in sample["history_frame_indices"]:
            if pov.get(index, {}).get("status") != "passed":
                reasons.add("native_first_person_pov_unverified")
            item = inventory[index]
            if type(item.get("capture_index")) is not int or item["capture_index"] != index:
                raise ValueError("Causal image inventory index mismatch")
            sample["images"].append({"frame_index": index, "path": item["path"], "sha256": item["sha256"]})
        required = sample["normalization_predecessor_command_row_ids"]+sample["target_command_row_ids"]
        current = predecessor = derived = None
        if len(sample["target_command_row_ids"]) == 1 and len(sample["normalization_predecessor_command_row_ids"]) == 1:
            current, predecessor = (by_id[sample["target_command_row_ids"][0]], by_id[sample["normalization_predecessor_command_row_ids"][0]])
            target_reasons, derived, _ = _command_quality(current, predecessor, identity)
            previous_reasons, _, _ = _command_quality(predecessor, None, identity, normalization_predecessor=True)
            reasons |= target_reasons | {"normalization_predecessor_"+reason for reason in previous_reasons}
        else:
            reasons.add("no_supported_future_normalization_pair")
        for rid in required:
            row = by_id[rid]
            association = _command_source(row, envelopes)
            if association is None:
                reasons.add("live_source_command_envelope_unavailable_or_ambiguous")
            raw = row.get("command_protobuf")
            sample["command_provenance"].append({"command_row_id": rid, "demo_tick": row["demo_tick"],
                "canonical_path": str((parsed / "usercmd.parquet").resolve()),
                "canonical_sha256": canonical["files"]["usercmd.parquet"],
                "command_protobuf_sha256": hashlib.sha256(raw).hexdigest() if isinstance(raw, bytes) else None,
                "command_protobuf_base64": base64.b64encode(raw).decode() if isinstance(raw, bytes) else None,
                "source_envelope": association})
        state_ticks = [int(frames[i][key]) for i in needed for key in ("source_demo_tick_start", "source_demo_tick_end")]
        state_ticks += [packet_by_index[i]["upper_source_demo_tick"] for i in needed
                        if type(packet_by_index.get(i, {}).get("upper_source_demo_tick")) is int]
        state_ticks += [by_id[rid]["demo_tick"] for rid in required]
        first_tick, last_tick = min(state_ticks), max(state_ticks)
        if last_tick-first_tick > 20000:
            reasons.add("state_window_out_of_supported_range")
        else:
            reasons |= state_reasons(states, range(first_tick, last_tick+1), identity,
                                     rounds.get(identity["round_id"], {}), segments)
        sample.update(checked_state_tick_start=first_tick, checked_state_tick_end_exclusive=last_tick+1)
        if not reasons:
            sample["targets"] = [{"command_row_id": current["command_row_id"],
                "server_tick_executed": current["server_tick_executed"],
                "delta_yaw_deg": derived["delta_yaw_deg"], "delta_pitch_deg": derived["delta_pitch_deg"],
                **{name: effective_scalar(current, name, "base_present") for name in
                   ("forwardmove", "leftmove", "upmove", "weaponselect", "impulse")},
                "mousedx": derived["mousedx_effective"], "mousedy": derived["mousedy_effective"],
                "raw_button_planes": {name: f"0x{effective_scalar(current, name, 'buttons_present'):016x}"
                                      for name in ("buttonstate1", "buttonstate2", "buttonstate3")}}]
        sample.update(reason_codes=sorted(reasons), training_ready=not reasons,
                      sample_id=_digest([PROFILE, contract_hash, configuration, sample["observation_frame_index"]])[:32])
        (rejected if reasons else accepted).append(sample)
    counts = Counter(reason for sample in rejected for reason in sample["reason_codes"])
    report = {"schema_version": 1, "producer": "cs2-causal-acceptance-v1", "profile": PROFILE, "status": "complete",
              **identity, "clip_id": clip["clip_id"], "configuration": configuration,
              "candidate_count": len(frames), "accepted_count": len(accepted), "rejected_count": len(rejected),
              "training_ready_sample_count": len(accepted), "reason_counts": dict(sorted(counts.items())),
              "training_ready_scope": "Only accepted sample records; recorded server-command values with both normalization supports strictly future.",
              "contract_sha256": contract_hash, "proof": proof,
              "inputs": {"parsed": str(parsed.resolve()), "dataset": str(dataset.resolve()),
                         "network_clock": str(network_clock.resolve()), "state_context": str(state_context.resolve())},
              "input_hashes": {"canonical_manifest": sha256_file(parsed / "manifest.json"),
                  "clip": sha256_file(dataset / "timing/clip.json"), "frames": sha256_file(dataset / "timing/frames.jsonl"),
                  "network_clock": sha256_file(network_clock), "state_context": sha256_file(state_context)},
              "limits": ["recorded_server_command_values_not_physical_mouse_sample_times",
                         "subticks_and_input_history_are_provenance_only", "recording_server_dll_hash_not_available"]}
    payloads = [b"".join(_json_bytes(row) for row in accepted), b"".join(_json_bytes(row) for row in rejected), _json_bytes(source)]
    report["files"] = {name: hashlib.sha256(payload).hexdigest() for name, payload in zip(FILES, payloads)}
    # Bind exactly the versions used above. A concurrent edit cannot make the
    # completion manifest hash newer source bytes than those actually checked.
    for path, digest in watched.items():
        if sha256_file(path) != digest:
            raise ValueError("Causal source changed during verification: "+str(path))
    return report, payloads


@exclusive_output()
def accept_causal_samples(parsed: Path, dataset: Path, network_clock: Path, state_context: Path, out: Path,
                          history_frames: int = 8, target_horizon_frames: int = 2) -> dict[str, Any]:
    """Recompute evidence and atomically publish a fresh accepted/rejected dataset."""
    if any(path.name != ".cs2-data.lock" for path in out.iterdir()):
        raise ValueError("Causal acceptance requires a fresh output directory")
    report, payloads = _recompute(parsed, dataset, network_clock, state_context, history_frames, target_horizon_frames)
    destinations = [out / name for name in (*FILES, "causal_acceptance.json")]
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


def load_causal_acceptance(path: Path) -> dict[str, Any]:
    """Recompute all raw sources and exact sample contents; hashes alone do not approve."""
    path = path / "causal_acceptance.json" if path.is_dir() else path
    report = read_json(path)
    inputs, configuration = report.get("inputs", {}), report.get("configuration", {})
    if (report.get("producer") != "cs2-causal-acceptance-v1" or report.get("profile") != PROFILE
            or not isinstance(inputs, dict) or not isinstance(configuration, dict)):
        raise ValueError("Unsupported causal acceptance report")
    try:
        paths = [Path(inputs[name]) for name in ("parsed", "dataset", "network_clock", "state_context")]
        expected, payloads = _recompute(*paths, configuration["history_frames"], configuration["target_horizon_frames"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid causal acceptance input/configuration identity") from exc
    if not _same(report, expected):
        raise ValueError("Causal acceptance report disagrees with recomputed evidence")
    for name, payload in zip(FILES, payloads):
        if (path.parent / name).read_bytes() != payload:
            raise ValueError("Causal sample/proof file disagrees with recomputed evidence: "+name)
    return report
