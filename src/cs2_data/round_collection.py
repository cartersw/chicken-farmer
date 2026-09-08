"""Plan a bounded chronological player-round recording with existing workers.

This is an action-blind collection plan, not a full-demo coordinator. Adjacent
captures retain independent histories and acceptance; every omitted source tick
is listed. Planning never starts CS2.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

from . import competitive_batch as batch
from . import competitive_collection as collection
from .competitive_coverage import normalize_steam_id
from .io import parsed_manifest, read_json, sha256_file, write_json
from .jobs import render_jobs

PROFILE = "cs2-round-progression-collection-v1"
PROJECT = Path(__file__).resolve().parents[2]
BYTES_PER_SOURCE_SECOND = 230_000_000
SECONDS_PER_SOURCE_SECOND = 25
SECONDS_PER_LAUNCH = 30


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _positive_budget(value, name):
    _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
             name+" must be a positive finite number")
    return value


def _window_plan(job, clip_ticks):
    start, end = job["start_demo_tick"], job["end_demo_tick"]
    _require(type(start) is int and type(end) is int and 199 <= start < end,
             "Round interval exceeds the protected capture domain")
    even_end = end - (end-start) % 2
    intervals, exclusions = [], []
    cursor = start
    while even_end-cursor >= clip_ticks:
        intervals.append([cursor, cursor+clip_ticks]); cursor += clip_ticks
    if even_end-cursor >= 32:
        intervals.append([cursor, even_end]); cursor = even_end
    if cursor < even_end:
        exclusions.append({"start_demo_tick": cursor, "end_demo_tick": even_end,
                           "reason": "remaining_span_below_32_tick_batch_minimum"})
    if even_end < end:
        exclusions.append({"start_demo_tick": even_end, "end_demo_tick": end,
                           "reason": "odd_final_tick_outside_32fps_capture_grid"})
    return {"round_id": job["round_id"], "player_slot": job["player_slot"],
            "start_demo_tick": start, "end_demo_tick": end, "eligible_ticks": end-start,
            "planned_intervals": intervals, "planned_ticks": sum(b-a for a, b in intervals),
            "excluded_intervals": exclusions}


def plan_round_collection(sources_manifest: Path, out: Path, *, steam_id: str,
                          round_ids: list[int], clip_ticks=640, storage_budget_gb=60,
                          time_budget_minutes=90, free_space_floor_gb=100):
    """Partition selected alive windows into ordered fixed-length batch groups."""
    steam_id = normalize_steam_id(steam_id)
    _require(steam_id is not None, "Round collection requires a specific Steam ID")
    _require(isinstance(round_ids, list) and 1 <= len(round_ids) <= batch.MAX_JOBS and
             all(type(value) is int and value > 0 for value in round_ids) and
             len(set(round_ids)) == len(round_ids), "Round IDs must be unique positive integers")
    _require(type(clip_ticks) is int and 32 <= clip_ticks <= batch.MAX_CLIP_TICKS and clip_ticks % 2 == 0,
             "Round collection needs even 32..1280 ticks per clip")
    storage_budget_bytes = int(_positive_budget(storage_budget_gb, "Storage budget")*1_000_000_000)
    free_space_floor_bytes = int(_positive_budget(free_space_floor_gb, "Free-space floor")*1_000_000_000)
    time_budget_seconds = _positive_budget(time_budget_minutes, "Time budget")*60
    sources_manifest, out = Path(sources_manifest).resolve(), Path(out).resolve()
    _require(not out.exists(), "Round collection requires a fresh output directory")
    inputs = read_json(sources_manifest)
    _require(isinstance(inputs, dict) and inputs.get("schema_version") == 1 and
             inputs.get("profile") == collection.SOURCE_PROFILE, "Unsupported round collection source manifest")
    entries = inputs.get("sources")
    _require(isinstance(entries, list) and len(entries) == 1, "Round collection requires exactly one source")
    entry = entries[0]
    _require(isinstance(entry, dict) and set(entry) == {"source_id", *collection.SOURCE_PATHS},
             "Unexpected round collection source fields")
    source_id = entry["source_id"]
    _require(isinstance(source_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", source_id),
             "Invalid round collection source_id")
    source = {"source_id": source_id}
    for key in collection.SOURCE_PATHS:
        value = entry[key]
        _require(isinstance(value, str) and value, "Missing collection source path: "+key)
        path = Path(value)
        source[key] = str((path if path.is_absolute() else sources_manifest.parent/path).resolve())
    canonical = parsed_manifest(Path(source["parsed"]), ("rounds.parquet", "player_state.parquet", "usercmd.parquet"))
    _require(canonical.get("tick_rate") == 64, "Round collection requires a 64 Hz source demo")
    source["demo_id"] = canonical["demo_id"]
    watched = {}
    batch._watch(watched, sources_manifest)
    batch._watch(watched, source["demo"], canonical["demo_id"])
    for key in ("phase_manifest", "state_context"):
        batch._watch(watched, source[key])
    batch._watch(watched, Path(source["parsed"])/"manifest.json")
    for filename in ("rounds.parquet", "player_state.parquet", "usercmd.parquet"):
        batch._watch(watched, Path(source["parsed"])/filename, canonical["files"][filename])
    from .validation import load_state_context
    load_state_context(Path(source["state_context"]), canonical)
    executable = PROJECT/"bin/cs2-clocks.exe"
    for path in (executable, PROJECT/"tools/usercmd-extractor/cmd/cs2-clocks/main.go",
                 Path(__file__), Path(batch.__file__), Path(collection.__file__),
                 Path(__file__).with_name("jobs.py"), Path(__file__).with_name("io.py")):
        batch._watch(watched, path)
    out.mkdir(parents=True, exist_ok=False)
    discovery = out/"eligible_player_windows.jsonl"
    render_jobs(parsed=Path(source["parsed"]), demo=Path(source["demo"]), out=discovery,
                phase_manifest=Path(source["phase_manifest"]), steam_id=int(steam_id),
                fps=32, width=1280, height=720, min_ticks=1)
    batch._watch(watched, discovery)
    candidates = [json.loads(line) for line in discovery.read_text().splitlines() if line]
    candidates = sorted((row for row in candidates if row["round_id"] in round_ids),
                        key=lambda row: (row["start_demo_tick"], row["round_id"], row["player_slot"]))
    missing = set(round_ids)-{row["round_id"] for row in candidates}
    _require(not missing, "No eligible competitive alive window for requested round IDs: "+str(sorted(missing)))
    for index, row in enumerate(candidates):
        _require(row.get("demo_id") == canonical["demo_id"] and str(row.get("steam_id")) == steam_id and
                 row.get("phase_evidence", {}).get("phase_verified") is True and
                 row.get("phase_evidence", {}).get("phase") == "competitive" and
                 row.get("command_coverage", {}).get("complete_within_gap_limit") is True,
                 "Round candidate lost source, player, phase or command eligibility")
        _require(index == 0 or candidates[index-1]["end_demo_tick"] <= row["start_demo_tick"],
                 "Eligible player windows overlap")
    windows = [_window_plan(row, clip_ticks) for row in candidates]
    job_count = sum(len(row["planned_intervals"]) for row in windows)
    _require(1 <= job_count <= batch.MAX_JOBS, "Round collection requires 1..24 total capture jobs; request fewer rounds")
    eligible_ticks = sum(row["eligible_ticks"] for row in windows)
    planned_ticks = sum(row["planned_ticks"] for row in windows)
    planned_seconds = planned_ticks/64
    projected_bytes = math.ceil(planned_seconds*BYTES_PER_SOURCE_SECOND + job_count*Path(source["demo"]).stat().st_size)
    projected_seconds = planned_seconds*SECONDS_PER_SOURCE_SECOND + job_count*SECONDS_PER_LAUNCH
    _require(projected_bytes <= storage_budget_bytes, "Projected recording storage exceeds the collection budget")
    _require(projected_seconds <= time_budget_seconds, "Projected recording processing time exceeds the collection budget")
    clock_path = out/"clock.json"
    clock_intervals = [{"start_demo_tick": a, "end_demo_tick": b}
                       for window in windows for a, b in window["planned_intervals"]]
    clock_windows = collection._clock(source, clock_intervals, clock_path, executable)
    batch._watch(watched, clock_path)
    batches, actual, expected = [], [], []
    for window in windows:
        intervals = window["planned_intervals"]
        groups = []
        for start, end in intervals:
            if groups and groups[-1]["clip_ticks"] == end-start:
                groups[-1]["end_demo_tick"] = end; groups[-1]["job_count"] += 1
            else:
                groups.append({"start_demo_tick": start, "end_demo_tick": end,
                               "clip_ticks": end-start, "job_count": 1})
        expected.extend((window["round_id"], a, b) for a, b in intervals)
        for group in groups:
            group_root = out/"batches"/f"{len(batches):03d}"
            manifest_path = group_root/"sources.json"
            batch_source = {key: source[key] for key in ("source_id", *collection.SOURCE_PATHS)}
            batch_source.update(network_clock=str(clock_path), selections=[{
                "steam_id": int(steam_id), "round_id": window["round_id"],
                "start_demo_tick": group["start_demo_tick"], "end_demo_tick": group["end_demo_tick"]}])
            write_json(manifest_path, {"schema_version": 1, "profile": batch.SOURCE_PROFILE, "sources": [batch_source]})
            plan = batch.plan_batch(manifest_path, group_root/"batch", clip_ticks=group["clip_ticks"], max_jobs=group["job_count"])
            for item in plan["jobs"]:
                job = item["job"]
                _require(job["demo_id"] == canonical["demo_id"] and str(job["steam_id"]) == steam_id,
                         "Batch changed round collection source or player")
                actual.append((job["round_id"], job["start_demo_tick"], job["end_demo_tick"]))
            plan_path = group_root/"batch/batch_plan.json"
            for filename, digest in plan["files"].items():
                batch._watch(watched, filename, digest)
            digest = batch._watch(watched, plan_path)
            batches.append({"plan": str(plan_path), "plan_sha256": digest,
                            "clip_ticks": group["clip_ticks"], "job_count": len(plan["jobs"]),
                            "planned_source_seconds": sum(item["job"]["end_demo_tick"]-item["job"]["start_demo_tick"] for item in plan["jobs"])/64})
    _require(actual == expected and len(set(actual)) == len(actual),
             "Batch plans changed chronological coverage or duplicated source intervals")
    batch._verify_hashes(watched)
    result = {"schema_version": 1, "profile": PROFILE, "status": "planned_not_rendered",
              "sources_manifest": str(sources_manifest), "source_files": watched, "source": source,
              "steam_id": steam_id, "round_ids": list(round_ids), "clip_ticks": clip_ticks,
              "windows": windows, "batches": batches, "job_count": job_count,
              "clock_evidence": {"path": str(clock_path), "windows": clock_windows},
              "coverage": {"eligible_ticks": eligible_ticks, "planned_ticks": planned_ticks,
                  "excluded_ticks": eligible_ticks-planned_ticks,
                  "complete_source_coverage": eligible_ticks == planned_ticks,
                  "history_images": 8, "missing_initial_history_positions_per_clip": 7,
                  "cross_clip_history_stitching": False},
              "exclusions": [{"round_id": row["round_id"], **excluded}
                             for row in windows for excluded in row["excluded_intervals"]],
              "planned_source_seconds": planned_seconds, "projected_bytes": projected_bytes,
              "projected_processing_seconds": projected_seconds,
              "storage_budget_bytes": storage_budget_bytes, "free_space_floor_bytes": free_space_floor_bytes,
              "time_budget_seconds": time_budget_seconds,
              "projection": {"bytes_per_source_second": BYTES_PER_SOURCE_SECOND,
                  "additional_source_copy_bytes_per_launch": Path(source["demo"]).stat().st_size,
                  "processing_seconds_per_source_second": SECONDS_PER_SOURCE_SECOND,
                  "additional_processing_seconds_per_launch": SECONDS_PER_LAUNCH,
                  "scope": "Conservative engineering estimate for capture through pending visual review; not a measured bound or final acceptance time."},
              "training_ready": False, "model_training_performed": False,
              "limits": ["Selection covers ordinary competitive alive progression without action scoring; freeze, pause, dead-player and post-round time are ineligible.",
                         "Every excluded tick is listed; captures are independent and the first seven image positions lack full history.",
                         "No cross-clip history stitching or full-round training-target coverage is claimed.",
                         "Every original image needs independent visual review before sample acceptance."]}
    write_json(out/"round_collection.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steam-id", required=True)
    parser.add_argument("--round-ids", type=int, nargs="+", default=[3, 4])
    parser.add_argument("--clip-ticks", type=int, default=640)
    parser.add_argument("--storage-budget-gb", type=float, default=60)
    parser.add_argument("--time-budget-minutes", type=float, default=90)
    parser.add_argument("--free-space-floor-gb", type=float, default=100)
    args = parser.parse_args(argv)
    options = vars(args); options["sources_manifest"] = options.pop("sources")
    result = plan_round_collection(**options)
    print(json.dumps({key: result[key] for key in ("status", "job_count", "planned_source_seconds", "projected_bytes")}))


if __name__ == "__main__":
    main()
