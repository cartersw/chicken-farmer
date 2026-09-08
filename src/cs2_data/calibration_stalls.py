"""Diagnose observed callback/capture cadence without inferring freeze causality.

QPC intervals are wall time. Controller/demo ticks are simulation observations;
movie recording intentionally decouples the two. Sparse startup milestones are
never treated as frame samples. This module does not certify training readiness.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from .calibration_analysis import _artifact, _finite, _integer, _read_ledger


LIMITATIONS = [
    "An observed gap is between instrumented callbacks, not proof of a visible freeze or its cause.",
    "QPC wall time and simulation advancement remain separate under movie recording.",
    "Sparse startup milestone intervals include intentional waits/loading; they are not frame-stall measurements.",
    "Old captures have no continuous startup cadence evidence before calibration_ready.",
    "Pixel readback call duration excludes work elsewhere in the renderer and GPU presentation latency.",
    "Action proximity is temporal association, not measured input consumption or physical-device latency.",
    "No observations before the first or after the last sample can be inferred from capture cadence.",
]
MILESTONES = {"header", "local_map_dispatched", "local_setup_dispatched", "startup_settle_complete", "recording_options",
              "calibration_ready", "recording_stop_dispatched", "calibration_complete"}


def _stats(values):
    if not values:
        return {"count": 0, "minimum_ms": None, "median_ms": None, "p95_ms": None, "maximum_ms": None}
    ordered = sorted(values)
    return {"count": len(values), "minimum_ms": ordered[0], "median_ms": statistics.median(values),
            "p95_ms": ordered[math.ceil(.95 * len(values)) - 1], "maximum_ms": ordered[-1]}


def _header(rows, filename, issues):
    headers = [row for row in rows if row["event"] == "header"]
    if len(headers) != 1 or not _integer(headers[0].get("qpc_frequency"), 1):
        issues.append({"code": "missing_or_invalid_qpc_header", "ledger": filename})
        return {}, None
    return headers[0], headers[0]["qpc_frequency"]


def _local_clock(player):
    if (not isinstance(player, dict) or player.get("status") not in ("observed", "observed_local_player")
            or not _integer(player.get("controller_tick_base")) or not _integer(player.get("pawn_handle"), 1)
            or type(player.get("steam_id")) not in (str, int) or not str(player["steam_id"]).isdigit()):
        return None, None
    return player["controller_tick_base"], (str(player["steam_id"]), player["pawn_handle"])


def _sample(row, source_index, event_index):
    event = row["event"]
    observation = row.get("native_observation") or {}
    if not isinstance(observation, dict):
        observation = {}
    native_clock = row.get("native_clock") or {}
    if not isinstance(native_clock, dict):
        native_clock = {}
    tick, identity = _local_clock(row.get("local_player", observation.get("local_player")))
    clock, value = "local_controller_tick_base", tick
    if _integer(row.get("replay_demo_tick")):
        clock, value, identity = "replay_demo_tick", row["replay_demo_tick"], None
    if event == "startup_settle_sample":
        value = row.get("controller_tick_base") if row.get("eligible") is True else None
        value = value if _integer(value) else None
        identity = (row.get("pawn_handle"),) if value is not None else None
    if value is None:
        clock = None
    qpc = row.get("qpc") if event in ("frame_sample", "startup_settle_sample") else row.get("qpc_before")
    return {"source_record_index": source_index, "event_index": event_index,
            "capture_index": row.get("capture_index"), "readback_index": row.get("readback_index"),
            "qpc": qpc if _integer(qpc) else None, "thread_id": row.get("thread_id"),
            "simulation_clock": clock, "simulation_value": value,
            "elapsed_ms": row.get("elapsed_ms") if _finite(row.get("elapsed_ms")) else None,
            "identity": identity, "client_generation": native_clock.get("client_generation"),
            "movie_name": row.get("movie_name"), "qpc_after": row.get("qpc_after")}


def _simulation_gap(before, after):
    if (before["simulation_clock"] is None or before["simulation_clock"] != after["simulation_clock"]
            or before["identity"] != after["identity"]):
        return {"clock": None, "delta_ticks": None, "advancement": "unavailable"}
    delta = after["simulation_value"] - before["simulation_value"]
    return {"clock": before["simulation_clock"], "delta_ticks": delta,
            "advancement": "positive" if delta > 0 else "zero" if delta == 0 else "backwards"}


def _endpoint(sample):
    return {key: sample[key] for key in ("source_record_index", "event_index", "thread_id", "capture_index", "readback_index",
                                        "qpc", "simulation_clock", "simulation_value", "elapsed_ms")}


def _series(rows, event, filename, frequency, threshold, issues):
    samples = [_sample(row, index, event_index) for event_index, (index, row) in
               enumerate((pair for pair in enumerate(rows) if pair[1]["event"] == event))]
    intervals, durations, skipped = [], [], 0
    for before, after in zip(samples, samples[1:]):
        reason = None
        if frequency is None or before["qpc"] is None or after["qpc"] is None:
            reason = "missing_qpc_or_frequency"
        elif not _integer(before["thread_id"], 1) or before["thread_id"] != after["thread_id"]:
            reason = "thread_unavailable_or_changed"
        elif before["movie_name"] != after["movie_name"] or before["client_generation"] != after["client_generation"]:
            reason = "capture_or_client_epoch_changed"
        elif after["qpc"] < before["qpc"]:
            reason = "backwards_qpc"
        if reason:
            skipped += 1
            issues.append({"code": reason, "ledger": filename, "event": event,
                           "before_record_index": before["source_record_index"],
                           "after_record_index": after["source_record_index"]})
            continue
        gap = (after["qpc"] - before["qpc"]) * 1000 / frequency
        intervals.append({"before": _endpoint(before), "after": _endpoint(after), "wall_gap_ms": gap,
                          "above_diagnostic_threshold": gap > threshold,
                          "simulation": _simulation_gap(before, after)})
    for sample in samples:
        start, end = sample["qpc"], sample["qpc_after"]
        if frequency is not None and start is not None and _integer(end) and end >= start:
            durations.append((end - start) * 1000 / frequency)
        elif end is not None:
            issues.append({"code": "invalid_call_qpc_interval", "ledger": filename, "event": event,
                           "source_record_index": sample["source_record_index"]})
    groups = {state: _stats([gap["wall_gap_ms"] for gap in intervals if gap["simulation"]["advancement"] == state])
              for state in ("zero", "positive", "backwards", "unavailable")}
    return {"ledger": filename, "event": event, "sample_count": len(samples), "qpc_frequency": frequency,
            "start_to_start_wall_gaps": _stats([gap["wall_gap_ms"] for gap in intervals]),
            "call_durations": _stats(durations), "wall_gaps_by_simulation_advancement": groups,
            "above_threshold_count": sum(gap["above_diagnostic_threshold"] for gap in intervals),
            "skipped_adjacent_pairs": skipped, "adjacent_intervals": intervals,
            "largest_intervals": sorted(intervals, key=lambda gap: gap["wall_gap_ms"], reverse=True)[:10]}


def _startup(rows, frequency, threshold, issues):
    milestones, prior = [], None
    for index, row in enumerate(rows):
        if row["event"] not in MILESTONES or not _integer(row.get("qpc")):
            continue
        qpc = row["qpc"]
        elapsed = None
        if prior is not None and frequency is not None and qpc >= prior:
            elapsed = (qpc - prior) * 1000 / frequency
        milestones.append({"event": row["event"], "qpc": qpc, "source_record_index": index,
                           "wall_ms_since_previous_milestone": elapsed,
                           "interpretation": "sparse_milestone_interval_not_frame_cadence"})
        prior = qpc
    cadence = [(index, row) for index, row in enumerate(rows) if row["event"] == "startup_cadence_sample"]
    previous_summary = {index: cadence[position - 1][1].get("qpc") if position else None
                        for position, (index, _) in enumerate(cadence)}
    stages = {}
    for stage in (0, 1, 2):
        stage_rows = [(index, row) for index, row in cadence if type(row.get("stage")) is int and row["stage"] == stage]
        observations = [{"source_record_index": index, "qpc": row["qpc"],
                         "previous_summary_qpc": previous_summary[index],
                         "maximum_callback_gap_ms": row["max_callback_gap_qpc_since_previous_sample"] * 1000 / frequency,
                         "previous_milestone": next((point for point in reversed(milestones)
                                                     if point["source_record_index"] < index), None),
                         "next_milestone": next((point for point in milestones
                                                 if point["source_record_index"] > index), None)}
                        for index, row in stage_rows if frequency is not None and _integer(row.get("qpc"))
                        and _integer(row.get("max_callback_gap_qpc_since_previous_sample"))]
        maxima = [sample["maximum_callback_gap_ms"] for sample in observations]
        if stage_rows:
            stages[str(stage)] = {"sample_count": len(stage_rows), "valid_maximum_count": len(maxima),
                                  "positive_callback_gap_count": sum(value > 0 for value in maxima),
                                  "observed_callback_gap_maxima": _stats(maxima),
                                  "above_threshold_count": sum(value > threshold for value in maxima),
                                  "largest_callback_gap_summaries": sorted(observations,
                                      key=lambda sample: sample["maximum_callback_gap_ms"], reverse=True)[:10]}
    valid_count = sum(stage["valid_maximum_count"] for stage in stages.values())
    observed_count = sum(stage["positive_callback_gap_count"] for stage in stages.values())
    if valid_count != len(cadence):
        issues.append({"code": "invalid_startup_cadence_evidence", "record_count": len(cadence),
                       "valid_record_count": valid_count})
    return {"cadence_available": observed_count > 0, "cadence_source": "native_maximum_of_consecutive_callbacks"
            if observed_count else None, "stages": stages, "milestones": milestones,
            "milestone_gaps_are_frame_stalls": False,
            "sampling_note": "Throttled startup sample spacing is not callback spacing; only the recorded callback maxima measure it.",
            "stage_note": "Stage labels belong to the emitting callback. An aggregated maximum can include work in the preceding stage; its exact endpoints are not recorded."}


def _actions(rows, frame_series, frequency, window_ms):
    contexts = []
    if frequency is None:
        return contexts
    for index, row in enumerate(rows):
        if (row["event"] != "action_dispatch" or not _integer(row.get("qpc_before"))
                or not _integer(row.get("thread_id"), 1)):
            continue
        qpc = row["qpc_before"]
        intervals = [gap for gap in frame_series["adjacent_intervals"]
                     if gap["before"]["thread_id"] == row["thread_id"]
                     and gap["before"]["qpc"] <= qpc + window_ms * frequency / 1000
                     and gap["after"]["qpc"] >= qpc - window_ms * frequency / 1000]
        contexts.append({"id": row.get("id"), "command": row.get("command"), "source_record_index": index,
                         "qpc_before": qpc, "actual_elapsed_ms": row.get("actual_elapsed_ms"),
                         "wall_window_each_side_ms": window_ms,
                         "interpretation": "same_ledger_wall_time_proximity_not_causation",
                         "wall_gaps": _stats([gap["wall_gap_ms"] for gap in intervals]),
                         "above_threshold_count": sum(gap["above_diagnostic_threshold"] for gap in intervals),
                         "largest_interval": max(intervals, key=lambda gap: gap["wall_gap_ms"], default=None)})
    return contexts


def analyze_stalls(run_dir, output_dir, *, gap_threshold_ms=100.0, capture_indices=(), action_window_ms=250.0):
    """Write fresh, immutable-evidence diagnostics; source runs are never modified."""
    if not _finite(gap_threshold_ms) or gap_threshold_ms <= 0:
        raise ValueError("Diagnostic gap threshold must be finite and positive")
    if not _finite(action_window_ms) or action_window_ms < 0:
        raise ValueError("Action context window must be finite and nonnegative")
    indices = list(capture_indices)
    if any(not _integer(index) for index in indices):
        raise ValueError("Capture indices must be nonnegative integers")
    run, output = Path(run_dir).resolve(), Path(output_dir).resolve()
    if not run.is_dir():
        raise ValueError("Calibration run directory does not exist")
    if output.exists() or output.is_relative_to(run):
        raise ValueError("Diagnostic output must be fresh and outside the source run")
    names = ("calibration_ledger.jsonl", "capture_ledger.jsonl", "calibration.json", "native-plan.json", "job.json")
    artifacts = {name: _artifact(run, name) for name in names}
    ledgers = {name: _read_ledger(run / name) for name in names[:2]}
    if not any(ledgers.values()):
        raise ValueError("No calibration or capture ledger is available")
    issues, series, headers = [], {}, {}
    for filename, rows in ledgers.items():
        if not rows:
            continue
        header, frequency = _header(rows, filename, issues)
        headers[filename] = header
        events = ("frame_sample", "startup_settle_sample") if filename == names[0] else ("movie_frame", "pixel_readback")
        for event in events:
            if any(row["event"] == event for row in rows):
                series[event] = _series(rows, event, filename, frequency, gap_threshold_ms, issues)
    calibration_frequency = headers.get(names[0], {}).get("qpc_frequency")
    if not _integer(calibration_frequency, 1):
        calibration_frequency = None
    contexts = []
    movie_intervals = series.get("movie_frame", {}).get("adjacent_intervals", [])
    for index in indices:
        contexts.append({"capture_index": index, "adjacent_intervals": [gap for gap in movie_intervals
                         if index in (gap["before"]["capture_index"], gap["after"]["capture_index"])]})
    limitations = list(LIMITATIONS)
    startup = _startup(ledgers[names[0]], calibration_frequency, gap_threshold_ms, issues)
    if startup["cadence_available"]:
        limitations.remove("Old captures have no continuous startup cadence evidence before calibration_ready.")
    if not startup["cadence_available"] and not any(value["adjacent_intervals"] for value in series.values()):
        issues.append({"code": "no_observed_cadence_intervals"})
    report = {"schema_version": 1, "producer": "cs2-calibration-cadence-diagnostics-v1", "run_dir": str(run),
              "status": "measured_cadence_diagnostics" if not issues else "incomplete_or_inconsistent",
              "training_ready": False, "live_control_ready": False, "exact_input_timing_verified": False,
              "diagnostic_gap_threshold_ms": gap_threshold_ms,
              "threshold_policy": "strictly_greater_than_threshold; diagnostic_only_not_freeze_causality_or_acceptance",
              "source_artifacts": artifacts, "series": series, "startup": startup,
              "action_contexts": _actions(ledgers[names[0]], series.get("frame_sample", {"adjacent_intervals": []}),
                                           calibration_frequency, action_window_ms),
              "capture_index_contexts": contexts, "issues": issues, "limitations": limitations}
    if artifacts != {name: _artifact(run, name) for name in names}:
        raise ValueError("Source artifacts changed while measuring cadence")
    output.mkdir(parents=True, exist_ok=False)
    (output / "stalls.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "stalls.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report):
    lines = ["# Observed calibration cadence", "", f"Source: `{report['run_dir']}`", "",
             f"Status: `{report['status']}`. Diagnostic threshold: **>{report['diagnostic_gap_threshold_ms']:g} ms**.", "",
             "| Observation stream | Samples | Median gap (ms) | Maximum gap (ms) | Above threshold |",
             "| --- | ---: | ---: | ---: | ---: |"]
    def number(value):
        return "unavailable" if value is None else f"{value:.3f}"
    for event, series in report["series"].items():
        stats = series["start_to_start_wall_gaps"]
        lines.append(f"| {event} | {series['sample_count']} | {number(stats['median_ms'])} | "
                     f"{number(stats['maximum_ms'])} | {series['above_threshold_count']} |")
    lines += ["", "These are adjacent observations on the same thread and capture epoch. JSON preserves each interval, "
              "its source indices and whether observed simulation ticks advanced, stayed equal, or moved backwards.", "",
              "## Startup evidence", ""]
    if not report["startup"]["cadence_available"]:
        lines.append("Continuous startup callback cadence was not recorded. Startup freezes cannot be confirmed or excluded.")
    else:
        for stage, evidence in report["startup"]["stages"].items():
            lines.append(f"Stage {stage}: maximum directly recorded callback gap "
                         f"{number(evidence['observed_callback_gap_maxima']['maximum_ms'])} ms "
                         f"across {evidence['sample_count']} emitted summaries.")
        lines += ["", report["startup"]["stage_note"]]
    lines += ["", "Startup and completion milestones below are sparse; their spacing includes intended waits and loading.", ""]
    for milestone in report["startup"]["milestones"]:
        lines.append(f"- `{milestone['event']}`: {number(milestone['wall_ms_since_previous_milestone'])} ms since previous milestone.")
    if report["action_contexts"]:
        lines += ["", "## Action proximity", "", "These windows describe QPC proximity to dispatch, without assigning cause.", ""]
        for context in report["action_contexts"]:
            lines.append(f"- `{context['id']}` ({context['command']}): maximum observed FRAME_START gap "
                         f"{number(context['wall_gaps']['maximum_ms'])} ms within ±{context['wall_window_each_side_ms']:g} ms wall time.")
    lines += ["", "## Limits", ""] + ["- " + limit for limit in report["limitations"]]
    lines += ["", "Training readiness, live-control readiness, and exact input timing remain unverified.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gap-threshold-ms", type=float, default=100.0)
    parser.add_argument("--capture-index", type=int, action="append", default=[])
    parser.add_argument("--action-window-ms", type=float, default=250.0)
    args = parser.parse_args(argv)
    try:
        report = analyze_stalls(args.run_dir, args.output, gap_threshold_ms=args.gap_threshold_ms,
                                capture_indices=args.capture_index, action_window_ms=args.action_window_ms)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Cadence diagnostics failed: {error}\n")
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve()),
                      "training_ready": False}, allow_nan=False))
    return 0 if not report["issues"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
