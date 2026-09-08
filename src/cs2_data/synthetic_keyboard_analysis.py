"""Bounded keyboard-response observations; never certify exact input consumption."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

import pyarrow.parquet as pq

from .calibration_analysis import _json, _read_ledger, _sha
from .calibration_buttons import _full_payload_evidence, _record, _hex
from .calibration_commands import analyze_calibration_commands
from .io import exclusive_output, publish, staging_paths
from .synthetic_input_analysis import verify_injection_evidence
from .synthetic_input_plan import validate_synthetic_input_plan

PROFILE = "cs2-synthetic-keyboard-response-v1"
PROTOCOL_SHA256 = "aa063c2a8f6d80eb9fdc4555b42b251602770fc35b3640a13e3fbbc5fcb2025d"
KEY_MASKS = {"W": 8, "S": 16, "A": 512, "D": 1024, "LCTRL": 4, "SPACE": 2, "R": 8192}
EDGE_WINDOW = (-2, 16)
TAIL = {"SPACE": 96, "R": 144}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)


def _player_identity(player):
    if not isinstance(player, dict):
        return None
    steam, pawn, controller = player.get("steam_id"), player.get("pawn_handle"), player.get("controller_handle")
    state = player.get("pawn_state", {})
    if (not isinstance(steam, str) or not steam.isdigit() or int(steam) <= 0 or
        any(type(v) is not int or not 0 < v < 2**32 - 1 for v in (pawn, controller)) or
        player.get("status") != "observed" or type(player.get("life_state")) is not int or player["life_state"] != 0 or
        not isinstance(state, dict) or type(state.get("health")) is not int or state["health"] <= 0):
        return None
    return steam, pawn, controller


def _anchor(batch, frames, index):
    rows = [row for row in frames if row.get("qpc") == batch.get("native_frame_qpc")]
    if len(rows) != 1 or rows[0].get("elapsed_ms") != batch.get("actual_elapsed_ms"):
        return None, "missing_or_ambiguous_native_injection_boundary"
    frame = rows[0]
    player = frame.get("local_player", {})
    identity = _player_identity(player)
    if identity is None:
        return None, "native_injection_identity_or_clock_unavailable"
    number = player["pawn_state"].get("movement_last_command_number_processed")
    tick = player.get("controller_tick_base")
    if any(type(v) is not int or v < 0 for v in (number, tick)):
        return None, "native_injection_identity_or_clock_unavailable"
    hits = index[(identity[0], number)]
    if len(hits) != 1 or hits[0]["server_tick_executed"] != tick or hits[0]["_numeric"] is None:
        return None, "native_last_processed_has_no_unique_raw_checked_canonical_match"
    receipt = batch.get("receipt", {})
    before, after = receipt.get("qpc_before"), receipt.get("qpc_after")
    if type(before) is not int or type(after) is not int or not frame["qpc"] <= before <= after:
        return None, "insertion_qpc_bracket_unavailable_or_reversed"
    before_call = [row for row in frames if type(row.get("qpc")) is int and frame["qpc"] <= row["qpc"] <= before]
    if any(_player_identity(row.get("local_player")) != identity for row in before_call):
        return None, "player_changed_between_scheduling_and_insertion"
    before_call = sorted(before_call, key=lambda row: row["qpc"])
    latest = before_call[-1]
    processed = latest["local_player"]["pawn_state"].get("movement_last_command_number_processed")
    if type(processed) is not int or processed < number:
        return None, "pre_insertion_processing_clock_unavailable_or_reversed"
    return {"frame": frame, "identity": identity, "number": number, "execution_tick": tick,
            "insertion_qpc_before": before, "insertion_qpc_after": after,
            "latest_pre_insertion_frame": latest, "already_processed_before_insertion": processed,
            "canonical_identity": hits[0]["identity"], "canonical_row": hits[0]}, None


def _command_window(anchor, index, first, last):
    rows, reasons = [], set()
    for number in range(first, last + 1):
        hits = index[(anchor["identity"][0], number)]
        if len(hits) != 1:
            reasons.add("command_window_missing_or_duplicate_number")
            continue
        row = hits[0]
        if (row["identity"] != anchor["canonical_identity"] or row.get("alive") is not True or row["_numeric"] is None or
            row["server_tick_executed"] != anchor["execution_tick"] + number - anchor["number"] or
            row["demo_tick"] != anchor["canonical_row"]["demo_tick"] + number - anchor["number"]):
            reasons.add("command_window_identity_raw_quality_or_clock_changed")
        rows.append(row)
    return rows, reasons


def _edge(event, anchor, index):
    control = event.get("key") if event["kind"] == "key" else "mouse_" + event.get("button", "")
    mask = KEY_MASKS.get(control, 1 if control == "mouse_left" else None)
    start, end = anchor["number"] + EDGE_WINDOW[0], anchor["number"] + EDGE_WINDOW[1]
    rows, reasons = _command_window(anchor, index, start, end)
    if mask is None:
        reasons.add("control_outside_frozen_response_protocol")
    steps = [{"command_number": row["command_number"], "demo_tick": row["demo_tick"], "subtick_index": i,
              "raw": step} for row in rows for i, step in enumerate(row["raw_subtick_moves"])
             if mask is not None and type(step.get("button")) is int and step["button"] & mask]
    matches = [step for step in steps if step["raw"].get("pressed") is event.get("pressed") and
               step["command_number"] > anchor["already_processed_before_insertion"]]
    planes = [{"command_number": row["command_number"], "raw_planes_hex": row["raw_planes_hex"],
               "buttons_parent_present": row["buttons_parent_present"], "scalar_presence": row["scalar_presence"],
               "protobuf_getter_default_view_hex": row["protobuf_getter_default_view_hex"]} for row in rows]
    # Reload has no button subticks in the earlier scoped probe. Keep this
    # separately named plane-consistency observation, never manufacture edges.
    plane_matches = [row["command_number"] for row in rows if mask is not None and row["_numeric"] is not None and
                     row["command_number"] > anchor["already_processed_before_insertion"] and
                     row["_numeric"][1] & mask and bool(row["_numeric"][0] & mask) is event.get("pressed")]
    return {"id": event["id"], "control": control, "pressed": event.get("pressed"),
        "native_boundary_qpc": anchor["frame"]["qpc"], "native_boundary_elapsed_ms": anchor["frame"]["elapsed_ms"],
        "observed_last_processed_command": anchor["number"], "observed_controller_tick_base": anchor["execution_tick"],
        "insertion_qpc_bracket": [anchor["insertion_qpc_before"], anchor["insertion_qpc_after"]],
        "observed_already_processed_before_insertion": anchor["already_processed_before_insertion"],
        "command_number_window_inclusive": [start, end], "fixed_mask_hypothesis_hex": _hex(mask),
        "status": "unknown" if reasons else "response_record_observed" if matches or control == "R" and plane_matches else "missing_response_record",
        "reason_codes": sorted(reasons), "matching_raw_edge_count": len(matches), "raw_subtick_records": steps,
        "plane_change_consistency_command_numbers": plane_matches, "raw_plane_observations": planes,
        "one_to_one_edge_assignment_verified": False, "exact_input_time_ns": None}


def _native_window(press, release, frames, index, tail):
    first, last = press["number"], release["number"] + tail
    selected, reasons = [], set()
    for frame in frames:
        player = frame.get("local_player", {})
        state = player.get("pawn_state", {}) if isinstance(player, dict) else {}
        number = state.get("movement_last_command_number_processed") if isinstance(state, dict) else None
        qpc = frame.get("qpc")
        if type(qpc) is not int:
            reasons.add("native_response_qpc_unavailable")
            continue
        if qpc < press["frame"]["qpc"]:
            continue
        # Use the controller clock to include identity-invalid observations too;
        # selecting only matching pawn states could silently bridge a respawn.
        tick = player.get("controller_tick_base") if isinstance(player, dict) else None
        if type(tick) is not int:
            reasons.add("native_response_clock_unavailable")
            continue
        if type(tick) is not int or tick > press["execution_tick"] + last - first:
            continue
        if tick < press["execution_tick"]:
            reasons.add("native_response_clock_reversed")
        if _player_identity(player) != press["identity"] or type(number) is not int:
            reasons.add("native_response_player_unavailable_or_changed")
            continue
        hits = index[(press["identity"][0], number)]
        if (len(hits) != 1 or hits[0]["identity"] != press["canonical_identity"] or
            hits[0]["server_tick_executed"] != tick or hits[0]["_numeric"] is None):
            reasons.add("native_response_snapshot_has_no_unique_canonical_clock_match")
        selected.append(frame)
    numbers = [row["local_player"]["pawn_state"]["movement_last_command_number_processed"] for row in selected]
    qpcs = [row["qpc"] for row in selected]
    if (not numbers or numbers[0] != first or numbers[-1] < last - 2 or
        any(b < a or b - a > 4 for a, b in zip(numbers, numbers[1:])) or
        any(b <= a for a, b in zip(qpcs, qpcs[1:]))):
        reasons.add("native_response_window_incomplete_or_clock_gap")
    if not qpcs or max(press["insertion_qpc_after"], release["insertion_qpc_after"]) >= qpcs[-1]:
        reasons.add("insertion_not_before_end_of_fixed_response_window")
    return selected, reasons, [first, last]


def _effect(control, samples, command_rows, game_events):
    states = [row["local_player"]["pawn_state"] for row in samples]
    if not states:
        return {"status": "unknown", "reason_codes": ["native_state_unavailable"]}
    result = {"status": "missing_observed_response", "reason_codes": [],
              "native_observations": len(states)}
    if control in ("W", "S", "A", "D"):
        origins, velocities = [s.get("origin") for s in states], [s.get("velocity") for s in states]
        if not all(_vector(v) for v in origins + velocities):
            return {**result, "status": "unknown", "reason_codes": ["movement_state_unavailable"]}
        displacement = max(math.hypot(v[0] - origins[0][0], v[1] - origins[0][1]) for v in origins)
        speed = max(math.hypot(v[0], v[1]) for v in velocities)
        field, sign = ("forwardmove", 1 if control == "W" else -1) if control in ("W", "S") else ("leftmove", 1 if control == "A" else -1)
        matching = [r["command_number"] for r in command_rows if _number(r.get(field)) and r[field] * sign > 0]
        result.update(maximum_horizontal_displacement_units=displacement, maximum_horizontal_speed=speed,
                      analog_field_hypothesis=field, analog_sign_hypothesis=sign, matching_analog_command_numbers=matching)
        observed = bool(matching) and displacement > .25 and speed > 1.0
    elif control == "LCTRL":
        values = [s.get("duck_amount") for s in states]
        if not all(_number(v) for v in values):
            return {**result, "status": "unknown", "reason_codes": ["duck_state_unavailable"]}
        result.update(duck_amount_before=values[0], maximum_duck_amount=max(values),
                      ducked_true_observed=any(s.get("ducked") is True for s in states))
        observed = max(values) - values[0] > .05 and result["ducked_true_observed"]
    elif control == "SPACE":
        velocity = [s.get("velocity") for s in states]
        if not all(_vector(v) for v in velocity) or any(type(s.get("on_ground")) is not bool for s in states):
            return {**result, "status": "unknown", "reason_codes": ["jump_state_unavailable"]}
        result.update(initially_on_ground=states[0]["on_ground"], airborne_observed=any(not s["on_ground"] for s in states),
                      maximum_upward_speed=max(v[2] for v in velocity), finally_on_ground=states[-1]["on_ground"])
        observed = result["initially_on_ground"] and result["airborne_observed"] and result["maximum_upward_speed"] > 1.0
    elif control in ("mouse_left", "R"):
        clips = [s.get("ammo_clip") for s in states]
        weapons = [s.get("active_weapon_handle") for s in states]
        if (any(type(v) is not int or v < 0 for v in clips) or
            any(type(v) is not int or not 0 < v < 2**32 - 1 for v in weapons) or len(set(weapons)) != 1):
            return {**result, "status": "unknown", "reason_codes": ["ammo_or_weapon_identity_unavailable_or_changed"]}
        result.update(initial_ammo_clip=clips[0], minimum_ammo_clip=min(clips), maximum_ammo_clip=max(clips))
        if control == "R":
            observed = max(clips) > clips[0]
            result["reload_observation"] = "same_weapon_clip_increase_not_exact_reload_timestamp"
        else:
            shot_times = [s.get("last_shot_time") for s in states]
            if not all(_number(v) for v in shot_times):
                return {**result, "status": "unknown", "reason_codes": ["native_last_shot_time_unavailable"]}
            result.update(last_shot_time_before=shot_times[0], maximum_last_shot_time=max(shot_times),
                          canonical_weapon_fire_events=game_events)
            observed = min(clips) < clips[0] and max(shot_times) > shot_times[0] and bool(game_events)
    else:
        return {**result, "status": "unknown", "reason_codes": ["control_outside_frozen_response_protocol"]}
    if observed:
        result["status"] = "observed_response_evidence"
    return result


def summarize_synthetic_keyboard(plan, verified_batches, native_rows, commands, states, game_events, manifest):
    """Compare fixed windows after externally verified insertion receipts.

    The artifact reader performs receipt and provenance checks before calling
    this core. This function alone cannot authenticate caller-supplied records.
    """
    plan = validate_synthetic_input_plan(plan)
    if any(e["kind"] == "mouse_move" for e in plan["events"]):
        raise ValueError("Keyboard response protocol does not accept relative-mouse events")
    if [e for batch in verified_batches for e in batch["events"]] != plan["events"]:
        raise ValueError("Verified input batches differ from the keyboard plan")
    standalone = (manifest.get("parse_status") == "complete" and not manifest.get("partial") and not manifest.get("warnings") and
        manifest.get("command_count") == manifest.get("eligible_payload_count") == manifest.get("full_payload_count") == len(commands) and
        len(commands) > 0 and manifest.get("delta_payload_count") == 0)
    records, index = [], defaultdict(list)
    for row in commands:
        record = _record(row, standalone)
        record.update(forwardmove=row.get("forwardmove"), leftmove=row.get("leftmove"), alive=row.get("alive"))
        records.append(record); index[(str(row.get("steam_id")), row.get("command_number"))].append(record)
    frames = [row for row in native_rows if row.get("event") == "frame_sample"]
    edges, anchors, held, pairs = [], {}, {}, []
    for batch in verified_batches:
        anchor, reason = _anchor(batch, frames, index)
        for event in batch["events"]:
            if anchor is None:
                edge = {"id": event["id"], "status": "unknown", "reason_codes": [reason]}
            else:
                edge = _edge(event, anchor, index)
                anchors[event["id"]] = anchor
            edges.append(edge)
            key = event["kind"], event.get("key", event.get("button"))
            if event["pressed"]:
                held[key] = event
            else:
                pairs.append((held.pop(key), event))
    by_id = {row["id"]: row for row in edges}
    phases = []
    for press_event, release_event in pairs:
        control = press_event.get("key", "mouse_" + press_event.get("button", ""))
        result = {"press_id": press_event["id"], "release_id": release_event["id"], "control": control,
                  "status": "unknown", "reason_codes": []}
        press, release = anchors.get(press_event["id"]), anchors.get(release_event["id"])
        if press is None or release is None:
            result["reason_codes"].append("press_or_release_anchor_unavailable")
        elif press["identity"] != release["identity"] or press["canonical_identity"] != release["canonical_identity"] or release["number"] < press["number"]:
            result["reason_codes"].append("press_release_identity_or_clock_changed")
        else:
            selected, reasons, bounds = _native_window(press, release, frames, index, TAIL.get(control, 16))
            window, command_reasons = _command_window(press, index, *bounds)
            reasons |= command_reasons
            if any(bounds[0] <= other["number"] <= bounds[1] for name, other in anchors.items()
                   if name not in (press_event["id"], release_event["id"])):
                reasons.add("other_planned_input_overlaps_response_window")
            demo_bounds = [press["canonical_row"]["demo_tick"], press["canonical_row"]["demo_tick"] + bounds[1] - bounds[0]]
            # Canonical identity stores strings; retain the original row types
            # when selecting independently extracted states and game events.
            def observed(row):
                return (str(row.get("steam_id")) == press["identity"][0] and str(row.get("round_id")) == press["canonical_identity"][1] and
                        type(row.get("demo_tick")) is int and demo_bounds[0] <= row["demo_tick"] <= demo_bounds[1])
            window_shots = [r for r in game_events if observed(r) and r.get("kind") == "weapon_fire"]
            already_processed_demo_tick = (press["canonical_row"]["demo_tick"] +
                press["already_processed_before_insertion"] - press["number"])
            shots = [row for row in window_shots if row["demo_tick"] > already_processed_demo_tick]
            # The anchor is the pre-insertion baseline. Observations made before
            # the actual insertion call ended cannot supply the response.
            effect_samples = [press["latest_pre_insertion_frame"]] + [row for row in selected if row["qpc"] > press["insertion_qpc_after"]]
            effect = _effect(control, effect_samples, [row for row in window if row["command_number"] >
                press["already_processed_before_insertion"]], shots)
            response_edges = [by_id[press_event["id"]], by_id[release_event["id"]]]
            result.update(command_number_window_inclusive=bounds, demo_tick_window_inclusive=demo_bounds,
                          canonical_weapon_fire_events_in_full_window=window_shots,
                          already_processed_demo_tick_before_insertion=already_processed_demo_tick,
                          native_state_observations=[{"qpc": r["qpc"], "elapsed_ms": r.get("elapsed_ms"), "local_player": r["local_player"]} for r in selected],
                          canonical_state_observations=[r for r in states if observed(r)], effect=effect)
            result["reason_codes"] = sorted(reasons)
            if not reasons and effect["status"] != "unknown" and all(e["status"] != "unknown" for e in response_edges):
                result["status"] = "observed_response_evidence" if effect["status"] == "observed_response_evidence" and all(
                    e["status"] == "response_record_observed" for e in response_edges) else "missing_observed_response"
        phases.append(result)
    for row in records:
        row.pop("_numeric")
    return {"schema_version": 1, "profile": PROFILE, "demo_id": manifest.get("demo_id"),
        "status": "response_evidence_observed_for_all_phases" if phases and all(p["status"] == "observed_response_evidence" for p in phases) else "incomplete_or_missing_response",
        "training_ready": False, "live_control_ready": False, "button_semantics_verified": False,
        "exact_input_timing_verified": False, "input_consumption_mapping_verified": False,
        "standalone_full_payload_coverage": standalone, "edge_status_counts": dict(Counter(e["status"] for e in edges)),
        "phase_status_counts": dict(Counter(p["status"] for p in phases)), "edges": edges, "phases": phases,
        "raw_commands": records, "source_provenance_verified_by_core": False,
        "limits": ["Successful Windows insertion does not prove CS2 consumption.",
            "Native controller/last-processed observations define numeric windows, not exact input assignment.",
            "Short press/release windows can overlap; matching edges are not assigned one-to-one.",
            "Raw plane masks and analog signs are scoped hypotheses from prior console probes.",
            "Raw protobuf presence and event order remain authoritative; absent fields are not invented edges.",
            "Observed movement can be affected by environment collision; absent movement is reported without assuming why.",
            "Canonical state and event rows are extracted corroboration; only UserCmd bytes are independently rebound here."]}


@exclusive_output(file_output=True)
def analyze_synthetic_keyboard(run: Path, parsed: Path, protocol: Path, out: Path):
    run, parsed, protocol = Path(run).resolve(), Path(parsed).resolve(), Path(protocol).resolve()
    if _sha(protocol) != PROTOCOL_SHA256:
        raise ValueError("Keyboard response protocol differs from the preregistered bytes")
    policy = _json(protocol.read_text(encoding="utf-8-sig"))
    paths = {"calibration.json": run / "calibration.json", "native-plan.json": run / "native-plan.json",
             "input_ledger.jsonl": run / "input_ledger.jsonl", "manifest.json": parsed / "manifest.json", "protocol": protocol}
    sources = {name: {"path": str(path), "sha256": _sha(path)} for name, path in paths.items()}
    baseline = analyze_calibration_commands(run, parsed)
    worker = _json(paths["calibration.json"].read_text(encoding="utf-8-sig"))
    if (worker.get("native_plan_sha256") != sources["native-plan.json"]["sha256"] or
        worker.get("input_ledger", {}).get("path") != "input_ledger.jsonl" or
        worker["input_ledger"].get("sha256") != sources["input_ledger.jsonl"]["sha256"]):
        raise ValueError("Worker plan or input-ledger file binding mismatch")
    native_plan = _json(paths["native-plan.json"].read_text(encoding="utf-8-sig"))
    manifest = _json(paths["manifest.json"].read_text(encoding="utf-8-sig"))
    if manifest.get("tick_rate") != 64:
        raise ValueError("Keyboard response windows require the recorded 64 Hz demo clock")
    native_rows, input_rows = _read_ledger(run / "calibration_ledger.jsonl"), _read_ledger(paths["input_ledger.jsonl"])
    verified = verify_injection_evidence(input_rows, native_rows, native_plan, worker, sources["native-plan.json"]["sha256"])
    source_plan = verified["source_plan"]
    # The protocol binds the original plan bytes. Re-serializing a dictionary is
    # not a substitute for that file evidence.
    source_path = protocol.with_name("synthetic-keyboard-probe-012-v1.json")
    if not source_path.is_file() or _sha(source_path) != policy["plan_sha256"] or _json(source_path.read_text(encoding="utf-8-sig")) != source_plan:
        raise ValueError("Keyboard source plan lacks its preregistered file hash binding")
    sources["source_plan"] = {"path": str(source_path), "sha256": policy["plan_sha256"]}
    tables = {name: pq.read_table(parsed / (name + ".parquet")).to_pylist() for name in ("usercmd", "player_state", "events")}
    wire = _full_payload_evidence(run / "controlled.dem", tables["usercmd"])
    if not wire["standalone_full_payload_bytes_match"]:
        raise ValueError("Keyboard response analysis requires complete byte-matched full UserCmd payloads")
    for declared, measured in (("full_payload_count", "live_full_payload_count"), ("delta_payload_count", "live_delta_payload_count"),
                              ("eligible_payload_count", "live_eligible_payload_count")):
        if manifest.get(declared) != wire[measured]:
            raise ValueError("Keyboard command coverage differs from the original live envelopes")
    report = summarize_synthetic_keyboard(source_plan, verified["batches"], native_rows, tables["usercmd"], tables["player_state"], tables["events"], manifest)
    report.update(sources={**baseline["sources"], **sources}, wire_payload_evidence=wire,
                  input_receipt_provenance_verified=True, frozen_protocol=policy)
    for source in report["sources"].values():
        if _sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Synthetic keyboard source changed during analysis")
    temporary = staging_paths([out])[0]
    try:
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        publish([temporary], [out])
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "parsed", "protocol", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    report = analyze_synthetic_keyboard(args.run, args.parsed, args.protocol, args.out)
    print(json.dumps({"status": report["status"], "phase_status_counts": report["phase_status_counts"], "training_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
