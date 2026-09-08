"""Prepare a bounded action-aware competitive batch without launching CS2.

Discovery remains scheduling evidence. Clock sidecars are freshly extracted
for the chosen intervals; the normal batch runner independently accepts each
capture after rendering, synchronization and complete visual review.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import time

from . import competitive_batch as batch
from . import competitive_coverage as coverage
from .clock_evidence import load_network_clock
from .io import read_json, sha256_file, write_json

PROFILE = "cs2-action-aware-competitive-collection-v1"
SOURCE_PROFILE = "cs2-competitive-collection-sources-v1"
SOURCE_PATHS = ("parsed", "demo", "phase_manifest", "state_context")
PROJECT = Path(__file__).resolve().parents[2]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def clock_windows(selected, *, margin=32):
    """Merge source intervals before asking the clock extractor for coverage."""
    _require(type(margin) is int and 16 <= margin <= 128, "Clock margin must be16..128 ticks")
    intervals = []
    for row in selected:
        start, end = row.get("start_demo_tick"), row.get("end_demo_tick")
        _require(type(start) is int and type(end) is int and 199 <= start < end <= 10000000-margin,
                 "Candidate exceeds the clock producer's tick domain")
        intervals.append((start-margin, end+margin))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1]["end_demo_tick"]:
            merged[-1]["end_demo_tick"] = max(end, merged[-1]["end_demo_tick"])
        else:
            merged.append({"start_demo_tick": start, "end_demo_tick": end})
    _require(0 < len(merged) <= 32 and sum(w["end_demo_tick"]-w["start_demo_tick"] for w in merged) <= 20000,
             "Selected source intervals exceed clock evidence bounds; use fewer clips")
    return merged


def _clock(source, selected, out, executable):
    windows = clock_windows(selected)
    arguments = [str(executable), "--input", source["demo"], "--out", str(out),
                 "--windows", ",".join(f"{w['start_demo_tick']}:{w['end_demo_tick']}" for w in windows)]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=1800,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _require(result.returncode == 0 and out.is_file(), "Clock extraction failed: "+result.stderr[-2000:])
    evidence = load_network_clock(out, source["demo_id"])
    _require(evidence.document["windows"] == windows, "Clock producer returned different selected windows")
    return windows


def _reuse_discovery(path, source, clip_ticks, steam_id=None):
    report = read_json(path)
    _require(report.get("profile") == coverage.PROFILE and report.get("status") == "complete" and
             report.get("training_ready") is False and report.get("demo_id") == source["demo_id"] and
             report.get("configuration", {}).get("clip_ticks") == clip_ticks,
             "Discovery report belongs to another source or clip policy")
    _require(all(Path(report["inputs"][key]).resolve() == Path(source[key]).resolve()
                 for key in ("parsed", "demo", "phase_manifest")), "Discovery source paths differ")
    implementation = Path(coverage.__file__).resolve()
    required = {str(Path(source["parsed"])/name) for name in
                ("manifest.json", "rounds.parquet", "player_state.parquet", "usercmd.parquet")}
    required.update(source[key] for key in ("demo", "phase_manifest"))
    required.update(str(p) for p in [implementation, *(implementation.with_name(name+".py") for name in
                    ("competitive_buttons", "clock_evidence", "causal_acceptance", "jobs", "io"))])
    _require(required <= set(report.get("source_files", {})), "Discovery report omits required source/implementation dependencies")
    _require(isinstance(report.get("candidate_pool"), list) and all(type(row.get("ordinary_pool_eligible")) is bool
                 for row in report["candidate_pool"]), "Discovery pool omits ordinary sampling provenance")
    batch._verify_hashes(report["source_files"])
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    _require(coverage._digest(body) == report.get("report_sha256"), "Discovery report checksum differs")
    configuration = report["configuration"]
    scope = "specific_player" if steam_id is not None else "all_players"
    _require(configuration.get("steam_id") == steam_id and
             configuration.get("selection_scope", "all_players") == scope,
             "Discovery player scope differs; run fresh discovery for the requested player selection")
    if steam_id is not None:
        _require(all(str(row.get("steam_id")) == steam_id for row in report["candidate_pool"]),
                 "Discovery pool contains players outside its declared selection scope")
    return report


def prepare_collection(sources_manifest: Path, out: Path, *, clip_ticks=640, max_jobs=4,
                       ordinary_fraction=0.5, reuse_discovery: Path | None = None,
                       steam_id: str | None = None):
    steam_id = coverage.normalize_steam_id(steam_id)
    _require(type(clip_ticks) is int and 32 <= clip_ticks <= batch.MAX_CLIP_TICKS and clip_ticks % 2 == 0,
             "Collection needs even32..1280 ticks per clip")
    _require(type(max_jobs) is int and 1 <= max_jobs <= batch.MAX_SELECTIONS,
             "Collection needs1..16 clips")
    coverage.choose_candidates([], max_clips=max_jobs, ordinary_fraction=ordinary_fraction)
    configuration = {"clip_ticks": clip_ticks, "max_jobs": max_jobs, "ordinary_fraction": ordinary_fraction,
        "steam_id": steam_id, "selection_scope": "specific_player" if steam_id is not None else "all_players"}
    sources_manifest, out = Path(sources_manifest).resolve(), Path(out).resolve()
    _require(not out.exists(), "Collection preparation requires a fresh output directory")
    inputs = read_json(sources_manifest)
    _require(inputs.get("schema_version") == 1 and inputs.get("profile") == SOURCE_PROFILE,
             "Unsupported collection source manifest")
    entries = inputs.get("sources")
    _require(isinstance(entries, list) and 1 <= len(entries) <= batch.MAX_SOURCES, "Collection needs1..8 sources")
    watched = {str(sources_manifest): sha256_file(sources_manifest)}
    sources, ids, demos = [], set(), set()
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == {"source_id", *SOURCE_PATHS}, "Unexpected collection source fields")
        source_id = entry["source_id"]
        _require(isinstance(source_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", source_id)
                 and source_id not in ids, "Invalid or duplicate source_id")
        ids.add(source_id)
        source = {"source_id": source_id}
        for key in SOURCE_PATHS:
            value = entry[key]
            _require(isinstance(value, str) and value, "Missing collection source path: "+key)
            path = Path(value)
            source[key] = str((path if path.is_absolute() else sources_manifest.parent/path).resolve())
        canonical = batch.parsed_manifest(Path(source["parsed"]), ("rounds.parquet", "player_state.parquet", "usercmd.parquet"))
        source["demo_id"] = canonical["demo_id"]
        _require(source["demo_id"] not in demos, "List each original demo once per collection")
        demos.add(source["demo_id"])
        batch._watch(watched, source["demo"], source["demo_id"])
        for key in ("phase_manifest", "state_context"):
            batch._watch(watched, source[key])
        sources.append(source)
    executable = PROJECT/"bin/cs2-clocks.exe"
    for path in (executable, PROJECT/"tools/usercmd-extractor/cmd/cs2-clocks/main.go", Path(__file__), Path(coverage.__file__)):
        batch._watch(watched, path)
    started = time.perf_counter()
    out.mkdir(parents=True, exist_ok=False)
    discovery_dir = out/"discovery"; discovery_dir.mkdir()
    reports, pool = {}, []
    for source in sources:
        report_path = discovery_dir/(source["source_id"]+".json")
        if reuse_discovery is None:
            report = coverage.discover_candidates(Path(source["parsed"]), Path(source["demo"]),
                Path(source["phase_manifest"]), report_path, clip_ticks=clip_ticks,
                max_clips=max_jobs, ordinary_fraction=ordinary_fraction, steam_id=steam_id)
        else:
            retained = Path(reuse_discovery).resolve()/(source["source_id"]+".json")
            report = _reuse_discovery(retained, source, clip_ticks, steam_id)
            batch._watch(watched, retained)
            write_json(report_path, report)
        for filename, expected in report["source_files"].items():
            batch._watch(watched, filename, expected)
        batch._watch(watched, report_path)
        reports[source["source_id"]] = str(report_path)
        pool.extend(row for row in report["candidate_pool"] if steam_id is None or str(row.get("steam_id")) == steam_id)
    selected = coverage.choose_candidates(pool, max_clips=max_jobs, ordinary_fraction=ordinary_fraction)
    if not selected:
        message = "No competitive candidates with sufficient command coverage"
        if steam_id is not None:
            message += " for Steam ID "+steam_id
        write_json(out/"collection_failure.json", {"schema_version": 1, "profile": PROFILE, "status": "failed",
            "failure_stage": "selection", "error": message, "sources_manifest": str(sources_manifest),
            "configuration": configuration, "source_files": watched, "discovery_reports": reports,
            "selected": [], "training_ready": False})
        raise ValueError(message)
    clock_dir = out/"clocks"; clock_dir.mkdir()
    batch_sources, clock_metadata = [], []
    for source in sources:
        chosen = [row for row in selected if row["demo_id"] == source["demo_id"]]
        if not chosen:
            continue
        clock_path = clock_dir/(source["source_id"]+".json")
        windows = _clock(source, chosen, clock_path, executable)
        batch._watch(watched, clock_path)
        batch_sources.append({key: source[key] for key in ("source_id", *SOURCE_PATHS)})
        batch_sources[-1].update(network_clock=str(clock_path), selections=coverage.source_selections(chosen))
        clock_metadata.append({"source_id": source["source_id"], "path": str(clock_path), "windows": windows})
    batch_source_path = out/"batch_sources.json"
    write_json(batch_source_path, {"schema_version": 1, "profile": batch.SOURCE_PROFILE, "sources": batch_sources})
    plan = batch.plan_batch(batch_source_path, out/"batch", clip_ticks=clip_ticks, max_jobs=len(selected))
    planned = {(r["job"]["demo_id"], str(r["job"]["steam_id"]), r["job"]["round_id"],
                r["job"]["start_demo_tick"], r["job"]["end_demo_tick"]) for r in plan["jobs"]}
    requested = {(r["demo_id"], str(r["steam_id"]), r["round_id"], r["start_demo_tick"], r["end_demo_tick"]) for r in selected}
    _require(planned == requested, "Batch eligibility changed the selected action/ordinary mixture; inspect retained candidates")
    batch._verify_hashes(watched)
    result = {"schema_version": 1, "profile": PROFILE, "status": "planned_not_rendered",
        "sources_manifest": str(sources_manifest), "source_files": watched, "discovery_reports": reports,
        "configuration": configuration,
        "selected": selected, "selection_purpose_counts": dict(Counter(r["selection_purpose"] for r in selected)),
        "clock_evidence": clock_metadata, "batch_plan": str(out/"batch/batch_plan.json"),
        "planned_source_seconds": len(selected)*clip_ticks/64,
        "preparation_seconds": time.perf_counter()-started, "training_ready": False,
        "limits": ["Action hints choose capture locations, not training labels.",
                   "Ordinary selection is action-blind within the bounded source pool, not a claim of population-uniform sampling.",
                   "Run the protected batch and review all original images before independent acceptance."]}
    write_json(out/"collection_plan.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clip-ticks", type=int, default=640)
    parser.add_argument("--max-jobs", type=int, default=4)
    parser.add_argument("--ordinary-fraction", type=float, default=0.5)
    parser.add_argument("--reuse-discovery", type=Path)
    parser.add_argument("--steam-id", type=coverage.normalize_steam_id,
                        help="Select only this player's positive uint64 Steam ID")
    args = parser.parse_args(argv)
    report = prepare_collection(args.sources, args.out, clip_ticks=args.clip_ticks, max_jobs=args.max_jobs,
                                ordinary_fraction=args.ordinary_fraction, reuse_discovery=args.reuse_discovery,
                                steam_id=args.steam_id)
    print(json.dumps({key: report[key] for key in ("status", "batch_plan", "selection_purpose_counts", "planned_source_seconds")}))


if __name__ == "__main__":
    main()
