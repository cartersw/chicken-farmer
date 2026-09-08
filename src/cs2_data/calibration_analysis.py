"""Measured diagnostics for controlled local captures; never certify input latency.

The scheduling clock is the observed local controller tick base at 64 Hz. QPC
measures wall-clock intervals separately, even while movie recording changes
the rate at which simulation advances. Engine dispatch is not input consumption.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

CONTROL_SOURCE = "dispatched_engine_controls_not_physical_device_latency"
CLOCK_BASIS = "local_controller_tick_base_64hz"
CONTROLS = frozenset(("forward", "back", "left", "right", "attack", "attack2", "duck", "jump",
                      "sprint", "reload", "turnleft", "turnright"))
LIMITS = ["engine_dispatch_is_not_measured_input_consumption",
          "physical_keyboard_and_mouse_latency_unmeasured",
          "button_and_analog_semantics_unverified",
          "recorded_demo_command_coverage_unverified",
          "original_client_to_replay_pixel_comparison_unmeasured"]


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value < 2**63


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON evidence key: " + key)
            result[key] = value
        return result

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Nonfinite number in calibration evidence")
        return parsed

    return json.loads(text, object_pairs_hook=pairs, parse_float=number, parse_constant=number)


def _read_object(path):
    if not path.is_file():
        return None
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Calibration JSON exceeds bounded size: " + path.name)
    value = _json(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected calibration JSON object: " + path.name)
    return value


def _read_ledger(path):
    if not path.is_file():
        return []
    if path.stat().st_size > 512 * 1024**2:
        raise ValueError("Calibration ledger exceeds bounded size: " + path.name)
    result = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number > 100000 or len(line) > 4 * 1024**2:
                raise ValueError("Calibration ledger exceeds bounded record size/count")
            if not line.strip():
                continue
            value = _json(line)
            if not isinstance(value, dict) or not isinstance(value.get("event"), str):
                raise ValueError(f"Invalid calibration ledger record {path.name}:{line_number}")
            result.append(value)
    return result


def _stats(values):
    if not values:
        return {"count": 0, "minimum_ms": None, "median_ms": None, "maximum_ms": None}
    return {"count": len(values), "minimum_ms": min(values), "median_ms": statistics.median(values),
            "maximum_ms": max(values)}


def _identity(player):
    if (not isinstance(player, dict) or player.get("status") not in ("observed", "observed_local_player")
            or not _integer(player.get("pawn_handle"), 1) or not isinstance(player.get("steam_id"), (str, int))
            or isinstance(player.get("steam_id"), bool) or not str(player["steam_id"]).isdigit()):
        return None
    return str(player["steam_id"]), player["pawn_handle"]


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_finite(v) for v in value)


def _state_change(first, last):
    result = {"association": "observed_state_difference_not_input_causation", "identity_stable": False}
    if not _identity(first) or _identity(first) != _identity(last):
        return result
    result["identity_stable"] = True
    before, after = first.get("pawn_state", {}), last.get("pawn_state", {})
    if not isinstance(before, dict) or not isinstance(after, dict):
        return result
    for key in ("origin", "eye_angles"):
        a, b = before.get(key), after.get(key)
        if _vector(a) and _vector(b):
            result[key + "_before"], result[key + "_after"] = a, b
            if key == "origin":
                result["origin_net_displacement_engine_units"] = math.dist(a, b)
            else:
                result["eye_angle_net_delta_degrees_wrapped"] = [(y - x + 180) % 360 - 180 for x, y in zip(a, b)]
    for key in ("ammo_clip", "last_shot_time", "duck_amount"):
        a, b = before.get(key), after.get(key)
        if _finite(a) and _finite(b):
            result[key + "_before"], result[key + "_after"] = a, b
            result[key + "_change"] = b - a
    for key in ("on_ground", "ducked"):
        if type(before.get(key)) is bool and type(after.get(key)) is bool:
            result[key + "_before"], result[key + "_after"] = before[key], after[key]
    return result


def _artifact(run, relative):
    path = run / relative
    if not path.resolve().is_relative_to(run):
        raise ValueError("Evidence path escapes calibration run: " + relative)
    if not path.is_file():
        return {"path": relative, "available": False}
    return {"path": relative, "available": True, "size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _analyze(run):
    issues = []

    def issue(code, **detail):
        issues.append({"code": code, **detail})

    names = ("native-plan.json", "calibration.json", "calibration_ledger.jsonl", "capture_ledger.jsonl",
             "capture_frame_files.json", "controlled.dem")
    artifacts = {name: _artifact(run, name) for name in names}
    plan = _read_object(run / "native-plan.json") or {}
    worker = _read_object(run / "calibration.json") or {}
    rows = _read_ledger(run / "calibration_ledger.jsonl")
    captures = _read_ledger(run / "capture_ledger.jsonl")
    mapping = _read_object(run / "capture_frame_files.json") or {}
    for name, artifact in artifacts.items():
        if not artifact["available"]:
            issue("missing_evidence_file", path=name)
    if worker.get("status") == "failed":
        issue("worker_reported_failure", error=str(worker.get("error", "")))

    def declared_digest(name):
        value = worker.get(name)
        if value is None:
            return None
        if not isinstance(value, dict):
            issue("invalid_worker_artifact_descriptor", artifact=name)
            return None
        return value.get("sha256")

    for filename, declared in (("native-plan.json", worker.get("native_plan_sha256")),
                               ("capture_frame_files.json", worker.get("capture_frame_files_sha256")),
                               ("controlled.dem", declared_digest("demo")),
                               ("calibration_ledger.jsonl", declared_digest("calibration_ledger")),
                               ("capture_ledger.jsonl", declared_digest("capture_ledger"))):
        if declared is not None and declared != artifacts[filename].get("sha256"):
            issue("worker_artifact_hash_mismatch", path=filename)

    headers = [r for r in rows if r["event"] == "header"]
    ready_rows = [r for r in rows if r["event"] == "calibration_ready"]
    complete_rows = [r for r in rows if r["event"] == "calibration_complete"]
    header = headers[0] if len(headers) == 1 else {}
    ready = ready_rows[0] if len(ready_rows) == 1 else {}
    frequency, start_tick = header.get("qpc_frequency"), ready.get("start_tick_base")
    clock_valid = ready.get("clock_basis") == CLOCK_BASIS and _integer(start_tick)
    if len(headers) != 1:
        issue("missing_or_duplicate_calibration_header")
    if len(ready_rows) != 1:
        issue("missing_or_duplicate_calibration_ready")
    if len(complete_rows) != 1:
        issue("missing_or_duplicate_calibration_complete")
    if not clock_valid:
        issue("scheduling_clock_unverified")
    if not _integer(frequency, 1):
        issue("invalid_qpc_frequency")
        frequency = None
    if header.get("control_source") not in ("native_engine_console_dispatch", CONTROL_SOURCE):
        issue("unsupported_or_missing_control_source")
    planned = plan.get("actions", [])
    if (type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1
            or plan.get("producer") != "cs2-controlled-calibration-plan-v1" or not isinstance(planned, list)
            or not 1 <= len(planned) <= 128):
        issue("missing_or_invalid_action_plan")
        planned = []
    valid_plan = []
    seen = set()
    for index, action in enumerate(planned):
        if (not isinstance(action, dict) or not isinstance(action.get("id"), str) or not action["id"]
                or action["id"] in seen or not isinstance(action.get("command"), str)
                or not _integer(action.get("at_ms"))
                or valid_plan and action["at_ms"] < valid_plan[-1]["at_ms"]):
            issue("invalid_or_duplicate_planned_action", action_index=index)
            continue
        seen.add(action["id"])
        valid_plan.append(action)
    expected = {a["id"]: a for a in valid_plan}
    dispatches, samples, done, is_ready = [], [], False, False
    seen_dispatch, held, pairs = set(), {}, []
    prior_elapsed, prior_qpc = None, None
    for row_index, row in enumerate(rows):
        event = row["event"]
        if event == "calibration_ready":
            is_ready = True
            continue
        if event == "calibration_complete":
            done = True
            continue
        if event not in ("action_dispatch", "frame_sample"):
            continue
        if not is_ready or done:
            issue("observation_outside_ready_complete_interval", record_index=row_index)
        elapsed = row.get("actual_elapsed_ms" if event == "action_dispatch" else "elapsed_ms")
        player = row.get("local_player_before" if event == "action_dispatch" else "local_player")
        tick = player.get("controller_tick_base") if isinstance(player, dict) else None
        derived = (tick - start_tick) * 1000 / 64 if clock_valid and _integer(tick) else None
        elapsed_valid = _finite(elapsed) and elapsed >= 0
        if not elapsed_valid:
            issue("invalid_scheduling_elapsed_ms", record_index=row_index)
        elif prior_elapsed is not None and elapsed < prior_elapsed:
            issue("scheduling_elapsed_regressed", record_index=row_index)
        if elapsed_valid:
            prior_elapsed = elapsed
        matches_tick = elapsed_valid and derived is not None and abs(elapsed - derived) <= 1e-6
        if not matches_tick:
            issue("elapsed_does_not_match_observed_tick_base", record_index=row_index)
        if not _identity(player):
            issue("local_player_observation_unavailable", record_index=row_index)
        qpc_before = row.get("qpc_before" if event == "action_dispatch" else "qpc")
        qpc_after = row.get("qpc_after") if event == "action_dispatch" else qpc_before
        qpc_valid = _integer(qpc_before) and _integer(qpc_after) and qpc_after >= qpc_before
        if not qpc_valid:
            issue("invalid_qpc_interval", record_index=row_index)
        elif prior_qpc is not None and qpc_before < prior_qpc:
            issue("qpc_observation_regressed", record_index=row_index)
        if qpc_valid:
            prior_qpc = qpc_after
        if event == "frame_sample":
            samples.append({"elapsed_ms": elapsed, "qpc": qpc_before, "local_player": player})
            continue
        name, command = row.get("id"), row.get("command")
        planned_action = expected.get(name) if isinstance(name, str) else None
        matched = (planned_action is not None and name not in seen_dispatch and command == planned_action["command"]
                   and type(row.get("at_ms")) is int and row["at_ms"] == planned_action["at_ms"])
        if not matched:
            issue("unexpected_duplicate_or_mismatched_dispatch", record_index=row_index, action_id=name)
        if isinstance(name, str):
            seen_dispatch.add(name)
        lateness = elapsed - row["at_ms"] if matched and matches_tick else None
        if lateness is not None and lateness < 0:
            issue("action_dispatched_before_scheduled_simulation_time", action_id=name)
        observed = {"id": name, "command": command, "planned_at_ms": row.get("at_ms"),
                    "actual_elapsed_ms": elapsed, "tick_base_elapsed_ms": derived,
                    "matches_plan": matched, "scheduling_clock_consistent": matches_tick,
                    "dispatch_lateness_simulation_ms": lateness,
                    "qpc_before": qpc_before, "qpc_after": qpc_after,
                    "dispatch_call_wall_ms": (qpc_after - qpc_before) * 1000 / frequency if qpc_valid and frequency else None,
                    "state_change_during_dispatch_call": _state_change(player, row.get("local_player_after"))}
        dispatches.append(observed)
        if isinstance(command, str) and command[:1] in ("+", "-") and command[1:] in CONTROLS:
            control = command[1:]
            if command[0] == "+":
                if control in held:
                    issue("duplicate_control_press", control=control)
                else:
                    held[control] = observed
            elif control not in held:
                issue("control_release_without_press", control=control)
            else:
                press = held.pop(control)
                pairs.append({"control": control, "press_id": press["id"], "release_id": name,
                              "dispatched_hold_simulation_ms": elapsed - press["actual_elapsed_ms"]
                              if _finite(elapsed) and _finite(press["actual_elapsed_ms"]) else None,
                              "input_consumption_verified": False})
    if held:
        issue("controls_without_dispatched_release", controls=sorted(held))
    missing = [a["id"] for a in valid_plan if a["id"] not in seen_dispatch]
    if missing:
        issue("planned_actions_not_dispatched", action_ids=missing)
    if not dispatches:
        issue("no_action_dispatches_observed")
    if [d["id"] for d in dispatches] != [a["id"] for a in valid_plan]:
        issue("dispatch_order_disagrees_with_plan")
    if not samples:
        issue("no_frame_start_samples")
    identities = {_identity(s["local_player"]) for s in samples}
    if len(identities) > 1:
        issue("local_player_identity_changed_during_samples")

    movie_rows = [r for r in captures if r["event"] == "movie_frame"]
    frame_entries = mapping.get("frames", [])
    if not isinstance(frame_entries, list) or len(frame_entries) > 2048:
        raise ValueError("Invalid or oversized calibration frame archive mapping")
    archives = []
    for index, frame in enumerate(frame_entries):
        name = frame.get("archived_name") if isinstance(frame, dict) else None
        if (not isinstance(name, str) or not name.endswith(".tga") or any(c in name for c in "/\\:")
                or name in (".", "..") or type(frame.get("capture_index")) is not int or frame["capture_index"] != index):
            issue("invalid_frame_archive_entry", entry_index=index)
            continue
        artifact = _artifact(run, "frames/" + name)
        artifact["capture_index"] = index
        artifact["matches_archive_sha256"] = artifact.get("sha256") == frame.get("sha256") and artifact["available"]
        if not artifact["matches_archive_sha256"]:
            issue("missing_or_changed_archived_frame", capture_index=index)
        archives.append(artifact)
    if not movie_rows or len(movie_rows) != len(frame_entries):
        issue("movie_and_archived_frame_counts_missing_or_different")
    if [r.get("capture_index") for r in movie_rows] != list(range(len(movie_rows))):
        issue("movie_capture_indices_not_contiguous")
    for movie, frame in zip(movie_rows, frame_entries):
        if not isinstance(frame, dict) or movie.get("tga_filename") != frame.get("source_name"):
            issue("movie_source_filename_disagrees_with_archive", capture_index=movie.get("capture_index"))
    demo = artifacts["controlled.dem"]
    if demo["available"]:
        with (run / "controlled.dem").open("rb") as handle:
            demo["source2_header_present"] = handle.read(8) == b"PBDEMS2\x00" and demo["size_bytes"] >= 16
        demo["command_stream_extracted_or_verified"] = False
        if not demo["source2_header_present"]:
            issue("invalid_recorded_demo_header")
    videos = [_artifact(run, path.name) for path in sorted(run.glob("*.mp4")) if path.is_file()]
    for name, artifact in artifacts.items():
        if artifact["available"] and _sha(run / name) != artifact["sha256"]:
            raise ValueError("Evidence changed during calibration analysis: " + name)
    return {"schema_version": 1, "status": "measured_dispatch_diagnostics" if not issues else "incomplete_or_inconsistent",
            "run_dir": str(run), "training_ready": False, "live_control_ready": False,
            "control_source": CONTROL_SOURCE, "physical_input_timestamps": False,
            "input_consumption_timing_verified": False, "button_semantics_verified": False,
            "replay_comparison_verified": False, "worker_reported_status": worker.get("status"),
            "clocks": {"scheduling_clock_basis": ready.get("clock_basis"), "tick_rate_hz": 64 if clock_valid else None,
                       "start_tick_base": start_tick, "qpc_frequency": frequency,
                       "wall_clock_and_simulation_clock_equated": False},
            "counts": {"planned_actions": len(valid_plan), "dispatched_actions": len(dispatches),
                       "matched_actions": sum(d["matches_plan"] for d in dispatches),
                       "frame_start_samples": len(samples), "movie_frames": len(movie_rows),
                       "archived_frames": len(archives), "archive_hash_matches": sum(a["matches_archive_sha256"] for a in archives),
                       "complete_events": len(complete_rows), "paired_controls": len(pairs)},
            "dispatch_lateness_simulation": _stats([d["dispatch_lateness_simulation_ms"] for d in dispatches if d["dispatch_lateness_simulation_ms"] is not None]),
            "dispatch_call_wall_duration": _stats([d["dispatch_call_wall_ms"] for d in dispatches if d["dispatch_call_wall_ms"] is not None]),
            "actions": dispatches, "control_pairs": pairs,
            "observed_sample_state_change": _state_change(samples[0]["local_player"], samples[-1]["local_player"]) if samples else {},
            "artifacts": artifacts, "archived_frames": archives, "videos": videos,
            "issues": issues, "limitations": LIMITS}


def _markdown(report):
    count = report["counts"]
    lines = ["# Controlled calibration diagnostic report", "", f"Status: `{report['status']}`.", "",
             "Training ready: **false**. Live control ready: **false**.", "",
             "Evidence comes from dispatched engine controls, not physical keyboard or mouse latency. "
             "Dispatch timestamps do not establish when the game consumed an input.", "",
             f"Actions: {count['matched_actions']} matched / {count['planned_actions']} planned; "
             f"{count['dispatched_actions']} dispatch records. Completion events: {count['complete_events']}.", "",
             f"Frames: {count['movie_frames']} movie records; {count['archive_hash_matches']} archived hashes matched. "
             f"Frame-start observations: {count['frame_start_samples']}.", "",
             f"Scheduling clock: `{report['clocks']['scheduling_clock_basis']}`. "
             f"QPC frequency: `{report['clocks']['qpc_frequency']}`. Simulation and wall time remain separate.", "",
             "| Action | Command | Scheduled ms | Observed simulation ms | Dispatch lateness ms | Wall call ms |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]

    def cell(value):
        if value is None:
            return "unavailable"
        return str(round(value, 6) if type(value) is float else value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    for action in report["actions"]:
        lines.append("| " + " | ".join(cell(action.get(k)) for k in
            ("id", "command", "planned_at_ms", "actual_elapsed_ms", "dispatch_lateness_simulation_ms", "dispatch_call_wall_ms")) + " |")
    lines += ["", "Observed state differences are descriptive; they are not proof of input causation. "
              "The JSON report preserves artifact hashes, control pairs, and available state measurements.", "",
              "Replay comparison, demo command recovery, input-consumption phase, and button semantics remain unverified."]
    if report["issues"]:
        lines += ["", "Evidence issues:", ""]
        for code, count in Counter(i["code"] for i in report["issues"]).items():
            lines.append(f"- `{code}` ({count})")
    return "\n".join(lines) + "\n"


def analyze_calibration(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Read one run without changing its files; publish into a fresh directory.

    Missing/contradictory evidence is reported explicitly. Malformed JSON,
    changing evidence, path escapes, and existing output paths raise ValueError.
    """
    run, output = Path(run_dir).resolve(), Path(output_dir).resolve()
    if not run.is_dir():
        raise ValueError("Calibration run directory does not exist")
    if output.exists():
        raise ValueError("Calibration report output must be a fresh directory")
    report = _analyze(run)
    markdown = _markdown(report)
    serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
    output.mkdir(parents=True, exist_ok=False)
    with (output / "report.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
    with (output / "report.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = analyze_calibration(args.run_dir, args.output)
    except (ValueError, OSError) as exc:
        print(f"Calibration analysis error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "report": str(args.output / "report.json"),
                      "training_ready": False, "live_control_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
