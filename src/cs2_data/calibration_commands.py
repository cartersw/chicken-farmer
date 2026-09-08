"""Compare observed native dispatch boundaries with recorded demo command rows.

Numeric command-number neighborhoods are diagnostics, not a claim that a
dispatch became a particular command. No button semantics or timing approval
is produced by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from .calibration_analysis import _json, _read_ledger, _sha
from .causal_acceptance import _action_protobuf


def _range(values):
    values = [v for v in values if type(v) is int]
    return {"minimum": min(values), "maximum": max(values)} if values else None


def _identity(row):
    return str(row.get("steam_id")), row.get("player_slot"), row.get("round_id")


def _command(row):
    result = {key: row.get(key) for key in (
        "command_row_id", "command_number", "demo_tick", "client_tick", "server_tick_executed",
        "player_slot", "round_id", "alive", "pawn_entity_handle", "view_pitch", "view_yaw",
        "view_roll", "forwardmove", "leftmove", "upmove", "subtick_moves", "input_history")}
    result["steam_id"] = str(row.get("steam_id"))
    result["raw_button_planes_hex"] = [
        f"0x{row[key]:016x}" if type(row.get(key)) is int else None
        for key in ("buttonstate1", "buttonstate2", "buttonstate3")]
    reasons, _ = _action_protobuf(row)
    result["projection_reason_codes"] = sorted(reasons)
    raw = row.get("command_protobuf")
    result["command_protobuf_sha256"] = hashlib.sha256(raw).hexdigest() if isinstance(raw, bytes) else None
    return result


def summarize_calibration_commands(commands, states, events, ledger, manifest):
    """Summarize measured rows without inventing an input-consumption clock."""
    issues = []
    count = len(commands)
    eligible = manifest.get("eligible_payload_count", 0)
    if count != manifest.get("command_count"):
        issues.append("canonical_command_count_mismatch")
    if not count:
        issues.append("no_reconstructed_commands")
    if count != eligible:
        issues.append("incomplete_command_reconstruction")
    if manifest.get("warnings", {}).get("usercmd_baseline_missing", 0):
        issues.append("usercmd_baseline_missing")
    if manifest.get("partial") or manifest.get("parse_status") != "complete":
        issues.append("incomplete_demo_parse")
    identities = sorted({_identity(row) for row in states}, key=str)
    raw_rows = [_command(row) for row in commands]
    projection_reasons = Counter(reason for row in raw_rows for reason in row["projection_reason_codes"])
    if projection_reasons:
        issues.append("canonical_command_projection_requires_review")
    command_keys = Counter((str(r.get("steam_id")), r.get("command_number")) for r in commands)
    duplicate_keys = sum(value > 1 for value in command_keys.values())
    if duplicate_keys:
        issues.append("ambiguous_command_number_identity")

    raw_transitions, previous_command = [], {}
    for row in raw_rows:
        key = _identity(row) + (row.get("pawn_entity_handle"),)
        prior = previous_command.get(key)
        if prior is not None:
            changes = {field: {"before": prior.get(field), "after": row.get(field)}
                       for field in ("raw_button_planes_hex", "forwardmove", "leftmove", "upmove")
                       if prior.get(field) != row.get(field)}
            angles = prior.get("view_yaw"), row.get("view_yaw")
            if all(type(value) in (int, float) for value in angles):
                delta = (angles[1] - angles[0] + 180) % 360 - 180
                if abs(delta) > .1:
                    changes["view_yaw"] = {"before": angles[0], "after": angles[1], "wrapped_delta_deg": delta}
            if changes:
                raw_transitions.append({"steam_id": key[0], "player_slot": key[1], "round_id": key[2],
                    "previous_command_number": prior["command_number"], "command_number": row["command_number"],
                    "command_number_gap": row["command_number"] - prior["command_number"],
                    "demo_tick": row["demo_tick"], "client_tick": row["client_tick"],
                    "server_tick_executed": row["server_tick_executed"], "changes": changes,
                    "association": "raw_command_difference_not_calibrated_button_event"})
        previous_command[key] = row

    transitions, previous = [], {}
    for row in sorted(states, key=lambda r: r["demo_tick"]):
        key = _identity(row)
        prior = previous.get(key)
        if prior is not None:
            changes = {}
            for field in ("ammo_clip", "crouching", "on_ground"):
                if row.get(field) is not None and prior.get(field) is not None and row[field] != prior[field]:
                    changes[field] = {"before": prior[field], "after": row[field]}
            angles = (prior.get("view_yaw"), row.get("view_yaw"))
            if all(type(v) in (int, float) for v in angles):
                delta = (angles[1] - angles[0] + 180) % 360 - 180
                if abs(delta) > .1:
                    changes["view_yaw"] = {"before": angles[0], "after": angles[1], "wrapped_delta_deg": delta}
            if changes:
                transitions.append({"steam_id": key[0], "player_slot": key[1], "round_id": key[2],
                                    "previous_demo_tick": prior["demo_tick"], "demo_tick": row["demo_tick"],
                                    "changes": changes, "association": "observed_state_change_not_input_consumption"})
        previous[key] = row

    dispatches = []
    for row in ledger:
        if row.get("event") != "action_dispatch":
            continue
        player = row.get("local_player_before", {})
        state = player.get("pawn_state", {})
        number = state.get("movement_last_command_number_processed")
        steam = str(player.get("steam_id"))
        neighborhood = []
        if type(number) is int and number >= 0:
            neighborhood = [r for r in raw_rows if r["steam_id"] == steam and
                            type(r.get("command_number")) is int and number - 2 <= r["command_number"] <= number + 8]
        dispatches.append({"id": row.get("id"), "command": row.get("command"),
            "scheduled_at_ms": row.get("at_ms"), "observed_elapsed_ms": row.get("actual_elapsed_ms"),
            "qpc_before": row.get("qpc_before"), "qpc_after": row.get("qpc_after"),
            "steam_id": steam, "controller_tick_base_observed": player.get("controller_tick_base"),
            "pawn_simulation_tick_observed": state.get("simulation_tick"),
            "movement_last_command_number_processed_observed": number,
            "same_number_row_count": sum(r["command_number"] == number for r in neighborhood),
            "command_number_neighborhood": neighborhood,
            "association": "numeric_neighborhood_of_observed_last_processed_number_only",
            "input_consumption_command_number": None, "exact_input_time_ns": None})
    return {
        "schema_version": 1, "profile": "cs2-calibration-command-diagnostics-v1",
        "status": "diagnostic_command_coverage_observed" if not issues else "diagnostic_requires_review",
        "training_ready": False, "button_semantics_verified": False, "exact_input_timing_verified": False,
        "physical_input_timestamps": False, "dispatch_to_command_mapping_verified": False,
        "issue_codes": issues, "demo_id": manifest.get("demo_id"), "map": manifest.get("map"),
        "tick_rate": manifest.get("tick_rate"),
        "coverage": {"reconstructed_commands": count, "eligible_payloads": eligible,
            "full_payloads": manifest.get("full_payload_count"), "delta_payloads": manifest.get("delta_payload_count"),
            "reconstruction_fraction": count / eligible if type(eligible) is int and eligible > 0 else None,
            "parser_warnings": manifest.get("warnings", {}), "projection_reason_counts": dict(projection_reasons),
            "duplicate_command_number_identity_keys": duplicate_keys},
        "command_clocks": {key: _range(row.get(key) for row in commands)
                           for key in ("command_number", "client_tick", "server_tick_executed", "demo_tick")},
        "state_demo_ticks": _range(row.get("demo_tick") for row in states),
        "players": [{"steam_id": steam, "player_slot": slot, "round_id": round_id,
            "spectator_user_ids": sorted({r.get("spectator_user_id") for r in states
                                          if _identity(r) == (steam, slot, round_id)}, key=str)}
                    for steam, slot, round_id in identities],
        "events": [{**row, "steam_id": str(row.get("steam_id"))} for row in events],
        "state_transitions": transitions, "raw_command_transitions": raw_transitions, "dispatches": dispatches,
        "limitations": ["source_hash_checks_do_not_independently_reparse_demo_packets",
            "command_number_neighborhood_does_not_establish_input_consumption",
            "native_simulation_clock_and_demo_delivery_clock_are_distinct",
            "raw_button_planes_and_subtick_records_remain_uninterpreted",
            "state_change_ticks_do_not_establish_individual_input_subtick_timing",
            "physical_device_latency_and_replay_pixel_alignment_unmeasured"],
    }


def analyze_calibration_commands(run: Path, parsed: Path):
    """Read bounded calibration artifacts and check their declared source hashes."""
    run, parsed = Path(run).resolve(), Path(parsed).resolve()
    manifest_path = parsed / "manifest.json"
    manifest = _json(manifest_path.read_text(encoding="utf-8-sig"))
    worker = _json((run / "calibration.json").read_text(encoding="utf-8-sig"))
    sources = {}
    for name, declared in (("controlled.dem", worker.get("demo", {}).get("sha256")),
                            ("calibration_ledger.jsonl", worker.get("calibration_ledger", {}).get("sha256"))):
        path = run / name
        actual = _sha(path)
        if not declared or declared != actual:
            raise ValueError("Calibration source hash mismatch: " + name)
        sources[name] = {"path": str(path), "sha256": actual}
    demo_hash = sources["controlled.dem"]["sha256"]
    if manifest.get("demo_id") != demo_hash or manifest.get("sha256") != demo_hash:
        raise ValueError("Parsed demo identity differs from recorded calibration")
    tables = {}
    for name in ("usercmd", "player_state", "events"):
        path = parsed / (name + ".parquet")
        actual = _sha(path)
        if manifest.get("files", {}).get(path.name) != actual:
            raise ValueError("Canonical source hash mismatch: " + path.name)
        if pq.ParquetFile(path).metadata.num_rows > 100000:
            raise ValueError("Calibration table exceeds bounded 100000 rows")
        tables[name] = pq.read_table(path).to_pylist()
        if any(r.get("demo_id") != demo_hash for r in tables[name]):
            raise ValueError("Canonical row demo identity mismatch")
        sources[path.name] = {"path": str(path), "sha256": actual}
    report = summarize_calibration_commands(tables["usercmd"], tables["player_state"], tables["events"],
                                            _read_ledger(run / "calibration_ledger.jsonl"), manifest)
    sources["manifest.json"] = {"path": str(manifest_path), "sha256": _sha(manifest_path)}
    report["sources"] = sources
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--parsed", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    report = analyze_calibration_commands(args.run, args.parsed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": report["status"], "coverage": report["coverage"], "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
