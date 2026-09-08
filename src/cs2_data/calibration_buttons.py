"""Test button-plane hypotheses against controlled dispatches and raw protobufs.

All outputs are diagnostics. A protobuf getter's default zero is a numeric API
value, not evidence that an omitted message proves a physical button released.
No result from this module authorizes semantic training targets or live input.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import pyarrow.parquet as pq

from .calibration_analysis import _json, _read_ledger, _sha
from .calibration_commands import analyze_calibration_commands
from .causal_acceptance import _action_protobuf
from .clock_evidence import protobuf_fields, scalar
from .io import exclusive_output, publish, staging_paths
from .packet_evidence import embedded, packet_messages, snappy_block, uvarint, MAX_BLOCK

PROFILE = "controlled_button_hypotheses_v1"
PLAN_PROFILE = "cs2-controlled-calibration-plan-v1"
PLAN_CONTROLS = frozenset(("forward", "back", "left", "right", "attack", "attack2", "duck", "jump",
                           "sprint", "reload", "turnleft", "turnright"))
UINT64_MAX = 2**64 - 1
FROZEN_RULES = {"plane1": "previous_plane1 updated by ordered raw subtick pressed/released bits",
                "plane2": "previous_plane1 XOR current_plane1",
                "plane3": "OR(raw pressed masks) AND OR(raw released masks)"}


def _full_payload_evidence(demo, commands):
    """Read wire payloads afresh; a manifest's full-payload count is insufficient."""
    before = _sha(demo)
    envelopes = defaultdict(list)
    full, delta, eligible, stopped, prior_tick = 0, 0, 0, False, -1
    with demo.open("rb") as stream:
        if stream.read(8) != b"PBDEMS2\0" or len(stream.read(8)) != 8:
            raise ValueError("Button wire audit requires a Source 2 demo")
        for source_index in range(100000):
            source_offset = stream.tell()
            kind, unsigned_tick = uvarint(stream), uvarint(stream)
            compressed, kind = bool(kind & 64), kind & ~64
            tick = unsigned_tick if unsigned_tick < 2**31 else unsigned_tick - 2**32
            if not 0 <= kind <= 18 or tick < prior_tick:
                raise ValueError("Button wire audit encountered an unsupported or reversed demo command")
            if kind == 0:
                stopped = True
                break
            size = uvarint(stream)
            if size > MAX_BLOCK:
                raise ValueError("Button wire audit demo block exceeds bound")
            data = stream.read(size)
            if len(data) != size:
                raise ValueError("Truncated button wire audit demo block")
            prior_tick = tick
            if kind not in (7, 8, 13):
                continue
            fields = protobuf_fields(snappy_block(data) if compressed else data)
            if kind == 13:
                fields = protobuf_fields(embedded(fields, 2))
            for message in packet_messages(embedded(fields, 3, optional=True)):
                if message["message_id"] != 76:
                    continue
                for envelope_index, (wire, raw) in enumerate(protobuf_fields(message["protobuf"]).get(1, [])):
                    if wire != 2:
                        raise ValueError("Invalid UserCmd envelope wire type")
                    value = protobuf_fields(raw)
                    complete, difference = embedded(value, 1, optional=True), embedded(value, 6, optional=True)
                    number, slot, execution, client = [scalar(value, field, signed=True) for field in (2, 3, 4, 5)]
                    full += bool(complete); delta += bool(difference)
                    if type(number) is not int or type(slot) is not int or slot < 0 or not (complete or difference):
                        continue
                    eligible += 1
                    key = tick, number, slot, execution, client
                    envelopes[key].append({"source_command_index": source_index, "source_offset": source_offset,
                        "demo_command_kind": kind, "message_wire_index": message["wire_index"],
                        "envelope_index": envelope_index, "envelope_sha256": hashlib.sha256(raw).hexdigest(),
                        "data_sha256": hashlib.sha256(complete).hexdigest() if complete else None,
                        "delta_data_present": bool(difference)})
        if not stopped:
            raise ValueError("Button wire audit requires a complete bounded demo")
    associations, used = [], set()
    for row in commands:
        key = tuple(row.get(name) for name in ("demo_tick", "command_number", "player_slot", "server_tick_executed", "client_tick"))
        hits = envelopes.get(key, [])
        if len(hits) != 1 or key in used:
            raise ValueError("Canonical button command has missing or ambiguous wire envelope")
        source = hits[0]
        used.add(key)
        if source["data_sha256"] is not None and not source["delta_data_present"]:
            if source["data_sha256"] != hashlib.sha256(row["command_protobuf"]).hexdigest():
                raise ValueError("Canonical button protobuf differs from full wire payload")
        associations.append({"command_row_id": row["command_row_id"], **source})
    if _sha(demo) != before:
        raise ValueError("Demo changed during button wire audit")
    extras = [{"demo_tick": key[0], "command_number": key[1], "player_slot": key[2],
               "server_tick_executed": key[3], "client_tick": key[4], **source}
              for key, values in envelopes.items() if key not in used for source in values]
    live = [source for values in envelopes.values() for source in values if source["demo_command_kind"] == 7]
    live_full = sum(source["data_sha256"] is not None for source in live)
    live_delta = sum(source["delta_data_present"] for source in live)
    return {"source_demo_sha256": before, "complete_demo_scanned": True,
            "all_packet_full_payload_count": full, "all_packet_delta_payload_count": delta,
            "all_packet_eligible_payload_count": eligible,
            "live_full_payload_count": live_full, "live_delta_payload_count": live_delta,
            "live_eligible_payload_count": len(live),
            "canonical_commands_bound": len(associations), "command_associations": associations,
            "unmatched_source_envelopes": extras,
            "standalone_full_payload_bytes_match": bool(commands) and live_full == len(live) == len(commands) and
                live_delta == 0 and all(source["demo_command_kind"] == 7 and source["data_sha256"] is not None and
                                       not source["delta_data_present"] for source in associations)}


def _hex(value):
    return f"0x{value:016x}" if type(value) is int and 0 <= value <= UINT64_MAX else None


def _identity(row):
    return tuple(str(row.get(k)) for k in ("demo_id", "round_id", "steam_id", "player_slot", "pawn_entity_handle"))


def _record(row, standalone):
    reasons, _ = _action_protobuf(row)
    steps = row.get("subtick_moves", [])
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        reasons.add("invalid_projected_subtick_collection")
        steps = []
    if any(type(row.get(key)) is not int or row[key] < 0 for key in
           ("command_row_id", "command_number", "demo_tick", "client_tick", "server_tick_executed")):
        reasons.add("invalid_canonical_command_clock")
    for step in steps:
        when = step.get("when")
        if (type(step.get("button")) is not int or not 0 < step["button"] <= UINT64_MAX
                or type(step.get("pressed")) is not bool or type(when) not in (int, float)
                or not math.isfinite(when) or not 0 <= when < 1):
            reasons.add("subtick_button_event_incomplete_or_fraction_unsupported")
    raw = [row.get("buttonstate" + str(n)) for n in (1, 2, 3)]
    if any(value is not None and _hex(value) is None for value in raw):
        reasons.add("invalid_raw_button_plane")
    # Full standalone payloads are the first scoped diagnostic. Delta recovery
    # remains outside this experiment even if another parser reconstructed it.
    numeric = [0 if value is None else value for value in raw] if standalone and not reasons else None
    return {"command_row_id": row.get("command_row_id"), "command_number": row.get("command_number"),
        "demo_tick": row.get("demo_tick"), "client_tick": row.get("client_tick"),
        "server_tick_executed": row.get("server_tick_executed"), "steam_id": str(row.get("steam_id")),
        "identity": list(_identity(row)), "buttons_parent_present": row.get("buttons_present") is True,
        "raw_planes_hex": [_hex(value) for value in raw],
        "scalar_presence": [value is not None for value in raw],
        "protobuf_getter_default_view_hex": [_hex(value) for value in numeric] if numeric is not None else None,
        "numeric_default_view_basis": "standalone_full_protobuf_getter_defaults_not_gameplay_semantics" if numeric is not None else "unavailable",
        "reason_codes": sorted(reasons), "raw_subtick_moves": steps,
        "command_protobuf_sha256": hashlib.sha256(row["command_protobuf"]).hexdigest()
            if isinstance(row.get("command_protobuf"), bytes) else None,
        "semantic_held_label_available": False, "semantic_event_label_available": False,
        "_numeric": numeric}


def _native_key(player):
    if not isinstance(player, dict):
        return None
    state = player.get("pawn_state", {})
    if not isinstance(state, dict):
        return None
    values = (player.get("controller_tick_base"), player.get("pawn_handle"),
              player.get("controller_handle"), state.get("movement_last_command_number_processed"))
    if (player.get("status") != "observed" or player.get("life_state") != 0
            or not str(player.get("steam_id", "")).isdigit()
            or any(type(value) is not int or value < 0 for value in values)):
        return None
    return (str(player["steam_id"]), *values)


def summarize_button_hypotheses(commands, ledger, manifest, *, comparison_mask=None):
    """Compare explicit hypotheses without fitting offsets or declaring semantics."""
    count = len(commands)
    standalone = (count > 0 and manifest.get("command_count") == manifest.get("eligible_payload_count") ==
                  manifest.get("full_payload_count") == count and manifest.get("delta_payload_count") == 0 and
                  not manifest.get("warnings") and manifest.get("parse_status") == "complete" and not manifest.get("partial"))
    records = [_record(row, standalone) for row in commands]
    index = defaultdict(list)
    for row in records:
        index[(row["steam_id"], row["command_number"])].append(row)
    batches = []
    for ledger_index, row in enumerate(ledger):
        if row.get("event") != "action_dispatch":
            continue
        key = _native_key(row.get("local_player_before"))
        after_key = _native_key(row.get("local_player_after"))
        if key is None or key != after_key:
            batches.append({"status": "native_dispatch_boundary_unavailable_or_changed", "actions": [row.get("id")]})
            continue
        if not batches or batches[-1].get("_key") != key:
            batches.append({"_key": key, "actions": [], "commands": [], "dispatch_timing": [], "status": "pending"})
        batch = batches[-1]
        batch["actions"].append(row.get("id")); batch["commands"].append(row.get("command"))
        batch["dispatch_timing"].append({"ledger_row_index": ledger_index, "action_id": row.get("id"),
            "scheduled_at_ms": row.get("at_ms"), "observed_elapsed_ms": row.get("actual_elapsed_ms"),
            "qpc_before": row.get("qpc_before"), "qpc_after": row.get("qpc_after"),
            "physical_event_time_ns": None})
    inferred = defaultdict(set)
    mapping_evidence = defaultdict(list)
    for batch in batches:
        key = batch.get("_key")
        if key is None:
            continue
        steam, controller_tick, pawn, controller, number = key
        before = index[(steam, number)]; after = index[(steam, number + 1)]
        batch.update(native_last_processed_command_number=number, native_controller_tick_base=controller_tick,
                     native_pawn_handle=pawn, native_controller_handle=controller,
                     association="hypothesis_next_numeric_command_after_observed_last_processed",
                     input_consumption_mapping_verified=False)
        if len(before) != 1 or len(after) != 1:
            batch["status"] = "missing_or_ambiguous_numeric_command_pair"
            continue
        a, b = before[0], after[0]
        if (a["identity"] != b["identity"] or a["server_tick_executed"] != controller_tick or
                b["server_tick_executed"] != controller_tick + 1 or b["demo_tick"] != a["demo_tick"] + 1 or
                a["_numeric"] is None or b["_numeric"] is None):
            batch["status"] = "numeric_pair_clock_identity_or_raw_quality_mismatch"
            continue
        batch.update(status="numeric_pair_observed", candidate_command_row_id=b["command_row_id"],
                     candidate_command_number=b["command_number"], candidate_demo_tick=b["demo_tick"],
                     candidate_raw_planes_hex=b["raw_planes_hex"], candidate_raw_subticks=b["raw_subtick_moves"])
        if len(batch["commands"]) == 1:
            command = batch["commands"][0]
            steps = b["raw_subtick_moves"]
            if (isinstance(command, str) and command[:1] in ("+", "-") and command[1:] in PLAN_CONTROLS and
                    len(steps) == 1 and steps[0]["pressed"] is (command[0] == "+")):
                mask = steps[0]["button"]
                if mask & (mask - 1) == 0:
                    inferred[command[1:]].add(mask)
                    mapping_evidence[command[1:]].append({"action_id": batch["actions"][0],
                        "command_number": b["command_number"], "mask_hex": _hex(mask), "pressed": steps[0]["pressed"]})
    mapping = {name: {"status": "consistent_single_bit_hypothesis" if len(masks) == 1 else "conflicting_bit_hypotheses",
                     "candidate_masks_hex": [_hex(mask) for mask in sorted(masks)], "evidence": mapping_evidence[name],
                     "semantic_training_label_available": False} for name, masks in sorted(inferred.items())}
    for batch in batches:
        batch.pop("_key", None)
        if batch["status"] != "numeric_pair_observed":
            continue
        expected = []
        for command in batch["commands"]:
            masks = inferred.get(command[1:], set()) if isinstance(command, str) else set()
            if len(masks) != 1 or command[:1] not in ("+", "-"):
                expected = None
                break
            expected.append({"button_hex": _hex(next(iter(masks))), "pressed": command[0] == "+"})
        actual = [{"button_hex": _hex(step["button"]), "pressed": step["pressed"]} for step in batch["candidate_raw_subticks"]]
        batch["expected_subtick_sequence_under_inferred_masks"] = expected
        batch["raw_subtick_sequence_matches_hypothesis"] = actual == expected if expected is not None else None

    tested_mask = 0
    for row in records:
        if row["_numeric"] is not None:
            for step in row["raw_subtick_moves"]:
                tested_mask |= step["button"]
    if comparison_mask is not None:
        if type(comparison_mask) is not int or not 0 < comparison_mask <= UINT64_MAX:
            raise ValueError("Invalid frozen comparison mask")
        tested_mask = comparison_mask
    hypotheses = {name: Counter() for name in ("plane1_end_state_after_raw_subticks", "plane2_any_raw_transition",
                  "plane2_boundary_xor", "plane3_both_press_and_release", "plane3_any_press", "plane3_any_release")}
    trials = []
    for previous, current in zip(records, records[1:]):
        a, b = previous["_numeric"], current["_numeric"]
        if (not tested_mask or a is None or b is None or previous["identity"] != current["identity"] or
                current["command_number"] != previous["command_number"] + 1 or
                current["server_tick_executed"] != previous["server_tick_executed"] + 1):
            continue
        held, pressed, released = a[0], 0, 0
        for step in current["raw_subtick_moves"]:
            bit = step["button"]
            if step["pressed"]:
                held |= bit; pressed |= bit
            else:
                held &= UINT64_MAX ^ bit; released |= bit
        expected = {"plane1_end_state_after_raw_subticks": (0, held),
            "plane2_any_raw_transition": (1, pressed | released), "plane2_boundary_xor": (1, a[0] ^ b[0]),
            "plane3_both_press_and_release": (2, pressed & released),
            "plane3_any_press": (2, pressed), "plane3_any_release": (2, released)}
        result = {"command_number": current["command_number"], "demo_tick": current["demo_tick"], "hypotheses": {}}
        for name, (plane, value) in expected.items():
            observed, predicted = b[plane] & tested_mask, value & tested_mask
            stats = hypotheses[name]
            stats["tested_commands"] += 1; stats["matches"] += observed == predicted
            stats["mismatches"] += observed != predicted; stats["expected_nonzero"] += predicted != 0
            stats["observed_nonzero"] += observed != 0
            result["hypotheses"][name] = {"observed_hex": _hex(observed), "predicted_hex": _hex(predicted), "matches": observed == predicted}
        if current["raw_subtick_moves"] or any(not test["matches"] for test in result["hypotheses"].values()):
            trials.append(result)
    for row in records:
        row.pop("_numeric")
    return {"schema_version": 1, "profile": PROFILE, "training_ready": False, "live_control_ready": False,
        "button_semantics_verified": False, "exact_input_timing_verified": False,
        "control_source": "native_engine_console_dispatch", "physical_input_timestamps": False,
        "keyboard_mouse_actuator_verified": False,
        "dispatch_consumption_mapping_verified": False, "demo_id": manifest.get("demo_id"),
        "standalone_full_payload_coverage": standalone, "command_count": count,
        "tested_subtick_mask_hex": _hex(tested_mask), "control_mask_hypotheses": mapping,
        "dispatch_batches": batches, "hypothesis_matrix": {name: dict(stats) for name, stats in hypotheses.items()},
        "discriminating_or_nonzero_trials": trials, "raw_commands": records,
        "absence_policy": {"raw_absence_preserved": True, "missing_base_never_defaulted": True,
            "numeric_default_view": "Only raw-checked standalone full protobufs; optional uint64 accessors default to zero, including nil button-message getter chains.",
            "semantic_absence_interpretation": "Unverified: numeric API defaults do not establish gameplay held/event semantics.",
            "schema_source": "demoinfocs-golang/v6@v6.0.0-alpha.0/pkg/demoinfocs/msg/usercmd.pb.go CInButtonStatePB getters",
            "protobuf_reference": "https://protobuf.dev/programming-guides/field_presence/"},
        "limits": ["Single-run hypotheses never enable semantic labels.",
            "N+1 is an explicit numeric association hypothesis, not an exact input-consumption timestamp.",
            "Masks inferred from isolated dispatch/subtick pairs are evaluated hypotheses, not externally calibrated constants.",
            "Plane comparisons apply only to tested_subtick_mask; untested controls and plane bits remain unknown.",
            "All-zero agreement is not discriminating evidence for plane 3.",
            "Raw subtick fractions are retained without conversion into physical event times.",
            "Keyboard bindings, scan codes, OS input delivery and relative-mouse actuation are not inferred."]}


def _frozen_hypotheses(path, current_plan, current_demo):
    path = Path(path).resolve()
    digest = _sha(path)
    value = _json(path.read_text(encoding="utf-8-sig"))
    if (value.get("schema_version") != 1 or value.get("profile") != "frozen_button_hypotheses_v1" or
            value.get("rules") != FROZEN_RULES or value.get("required_rule_mismatches") != 0 or
            value.get("require_exact_ordered_subtick_sequence_for_each_dispatch_batch") is not True or
            value.get("no_retuning_from_confirmation_data") is not True):
        raise ValueError("Unsupported frozen button hypotheses")
    source_path, plan_path = Path(value["source_analysis_path"]), Path(value["confirmation_plan_path"])
    if (_sha(source_path) != value.get("source_analysis_sha256") or
            _sha(plan_path) != value.get("confirmation_plan_sha256")):
        raise ValueError("Frozen button source or plan hash mismatch")
    source = _json(source_path.read_text(encoding="utf-8-sig"))
    plan = _json(plan_path.read_text(encoding="utf-8-sig"))
    if (source.get("profile") != PROFILE or source.get("demo_id") != value.get("source_demo_sha256") or
            source.get("demo_id") == current_demo or
            {key: item for key, item in current_plan.items() if key not in ("movie_name", "demo_path")} != plan):
        raise ValueError("Confirmation must use its frozen plan and a different recording")
    required = ("plane1_end_state_after_raw_subticks", "plane2_boundary_xor", "plane3_both_press_and_release")
    for rule in required:
        stats = source.get("hypothesis_matrix", {}).get(rule, {})
        if not stats.get("tested_commands") or stats.get("mismatches") != 0:
            raise ValueError("Frozen rule lacks supporting source trials")
    if source["hypothesis_matrix"][required[2]].get("expected_nonzero", 0) < 1:
        raise ValueError("Frozen plane3 rule lacks nonzero source trials")
    controls = value.get("confirmation_controls", {})
    if not isinstance(controls, dict) or not controls:
        raise ValueError("Frozen control masks are missing")
    mask = 0
    for name, encoded in controls.items():
        if name not in PLAN_CONTROLS or not isinstance(encoded, str):
            raise ValueError("Invalid frozen control mask")
        bit = int(encoded, 16)
        candidate = source.get("control_mask_hypotheses", {}).get(name, {})
        if (_hex(bit) != encoded or bit == 0 or bit & (bit - 1) or
                candidate.get("status") != "consistent_single_bit_hypothesis" or
                candidate.get("candidate_masks_hex") != [encoded]):
            raise ValueError("Frozen mask differs from source control hypothesis")
        mask |= bit
    if _sha(path) != digest:
        raise ValueError("Frozen button hypotheses changed during reading")
    return value, mask, {"frozen_hypotheses": {"path": str(path), "sha256": digest},
        "frozen_source_analysis": {"path": str(source_path), "sha256": value["source_analysis_sha256"]},
        "frozen_confirmation_plan": {"path": str(plan_path), "sha256": value["confirmation_plan_sha256"]}}


def _confirmation(report, frozen):
    checks = []
    for batch in report["dispatch_batches"]:
        expected = []
        for command in batch.get("commands", []):
            if command[1:] not in frozen["confirmation_controls"] or command[:1] not in ("+", "-"):
                expected = None
                break
            expected.append({"button_hex": frozen["confirmation_controls"][command[1:]], "pressed": command[0] == "+"})
        actual = [{"button_hex": _hex(step["button"]), "pressed": step["pressed"]}
                  for step in batch.get("candidate_raw_subticks", [])]
        checks.append({"actions": batch["actions"], "candidate_command_number": batch.get("candidate_command_number"),
                       "expected_ordered_subticks": expected, "observed_ordered_subticks": actual,
                       "matches_frozen_sequence": batch["status"] == "numeric_pair_observed" and
                                                  expected is not None and bool(expected) and expected == actual})
    rules = {key: report["hypothesis_matrix"][key] for key in (
        "plane1_end_state_after_raw_subticks", "plane2_boundary_xor", "plane3_both_press_and_release")}
    passed = bool(checks) and all(check["matches_frozen_sequence"] for check in checks) and all(
        values.get("tested_commands", 0) > 0 and values.get("mismatches") == 0 for values in rules.values())
    return {"status": "supported_within_frozen_scope" if passed else "contradicted_or_incomplete",
            "confirmation_passed": passed, "scope": "fixed_rules_and_control_masks_from_separate_prior_probe",
            "rules_retuned_from_confirmation": False, "semantic_training_labels_enabled": False,
            "exact_input_timing_verified": False, "frozen_rules": frozen["rules"],
            "frozen_control_masks": frozen["confirmation_controls"], "rule_results": rules, "dispatch_sequence_checks": checks,
            "chronology": "Freeze-before-run is recorded operationally; a self-authored timestamp is not cryptographic proof."}


@exclusive_output(file_output=True)
def analyze_calibration_buttons(run: Path, parsed: Path, out: Path, frozen_hypotheses: Path | None = None):
    run, parsed = Path(run).resolve(), Path(parsed).resolve()
    initial_sources = {name: {"path": str(path), "sha256": _sha(path)} for name, path in (
        ("manifest.json", parsed / "manifest.json"), ("native-plan.json", run / "native-plan.json"),
        ("calibration.json", run / "calibration.json"))}
    baseline = analyze_calibration_commands(run, parsed)
    manifest = _json((parsed / "manifest.json").read_text(encoding="utf-8-sig"))
    worker = _json((run / "calibration.json").read_text(encoding="utf-8-sig"))
    plan_path = run / "native-plan.json"
    if worker.get("native_plan_sha256") != _sha(plan_path):
        raise ValueError("Native plan hash mismatch")
    plan = _json(plan_path.read_text(encoding="utf-8-sig"))
    ledger = _read_ledger(run / "calibration_ledger.jsonl")
    headers = [r for r in ledger if r["event"] == "header"]
    ready = [r for r in ledger if r["event"] == "calibration_ready"]
    done = [r for r in ledger if r["event"] == "calibration_complete"]
    actions = [r for r in ledger if r["event"] == "action_dispatch"]
    if (len(headers) != 1 or headers[0].get("plan") != plan or plan.get("producer") != PLAN_PROFILE or
            len(ready) != 1 or len(done) != 1 or ledger.index(ready[0]) >= ledger.index(done[0]) or
            [{"id": r.get("id"), "command": r.get("command"), "at_ms": r.get("at_ms")} for r in actions] != plan.get("actions") or
            any(not ledger.index(ready[0]) < ledger.index(action) < ledger.index(done[0]) for action in actions)):
        raise ValueError("Native action plan/order/completion binding mismatch")
    commands = pq.read_table(parsed / "usercmd.parquet").to_pylist()
    wire = _full_payload_evidence(run / "controlled.dem", commands)
    for declared, measured in (("full_payload_count", "live_full_payload_count"),
                              ("delta_payload_count", "live_delta_payload_count"),
                              ("eligible_payload_count", "live_eligible_payload_count")):
        if manifest.get(declared) != wire[measured]:
            raise ValueError("Declared command coverage disagrees with live wire payloads")
    if commands and manifest.get("delta_payload_count") == 0 and not wire["standalone_full_payload_bytes_match"]:
        raise ValueError("Standalone canonical commands require matching full live wire payloads")
    frozen, comparison_mask, frozen_sources = None, None, {}
    if frozen_hypotheses is not None:
        frozen, comparison_mask, frozen_sources = _frozen_hypotheses(frozen_hypotheses, plan, manifest["demo_id"])
    report = summarize_button_hypotheses(commands, ledger, manifest, comparison_mask=comparison_mask)
    report["wire_payload_evidence"] = wire
    report["sources"] = {**baseline["sources"], **initial_sources, **frozen_sources}
    report["coverage"] = baseline["coverage"]
    if frozen is not None:
        report["frozen_confirmation"] = _confirmation(report, frozen)
    for source in report["sources"].values():
        if _sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Button calibration input changed during analysis")
    temporary = staging_paths([out])[0]
    try:
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        publish([temporary], [out])
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--parsed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frozen-hypotheses", type=Path)
    args = parser.parse_args(argv)
    report = analyze_calibration_buttons(args.run, args.parsed, args.out, args.frozen_hypotheses)
    print(json.dumps({"out": str(args.out), "full_payload_coverage": report["standalone_full_payload_coverage"],
                      "tested_mask": report["tested_subtick_mask_hex"], "training_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
