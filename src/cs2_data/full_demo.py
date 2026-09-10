"""Persistent, unattended processing of complete demos for one selected player."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import shutil
import threading
import uuid
import zipfile

from . import competitive_batch as batch
from . import competitive_collection as collection
from .competitive_coverage import normalize_steam_id
from .io import parsed_manifest, read_json, sha256_file, write_json
from .jobs import render_jobs, phase_evidence
from .launcher_backend import PROJECT, StopRequested, prepare_sources, renderer_tools, save_settings
from .training_archive import FORMAT, pack_segment, release_work, sample_key, package_artifacts, retention_mode

PROFILE = "cs2-full-player-demo-v1"
QUEUE_PROFILE = "cs2-full-demo-queue-v1"
MAX_TICKS = 7680
HISTORY_OVERLAP_TICKS = 14


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def load_queue(path):
    path = Path(path).resolve()
    if not path.exists():
        return {"schema_version": 1, "profile": QUEUE_PROFILE, "jobs": []}
    doc = read_json(path)
    require(doc.get("schema_version") == 1 and doc.get("profile") == QUEUE_PROFILE and
            isinstance(doc.get("jobs"), list) and len(doc["jobs"]) <= 1000, "Unsupported demo queue")
    ids = set()
    for job in doc["jobs"]:
        key = job.get("id")
        require(isinstance(key, str) and len(key) == 32 and all(c in "0123456789abcdef" for c in key)
                and key not in ids, "Invalid or duplicate queue entry")
        ids.add(key)
        require(job.get("format") == FORMAT and normalize_steam_id(job.get("steam_id")) is not None,
                "Queue entry has an unsupported player or output format")
        retention_mode(job.get("evidence_retention", "lean"))
    return doc


def enqueue(path, demo, steam_id, player_name, *, match_id="", output_format=None, evidence_retention="lean"):
    path, demo = Path(path).resolve(), Path(demo).resolve()
    steam_id = normalize_steam_id(steam_id)
    require(steam_id is not None, "Select one named player before queueing an entire demo")
    require(demo.is_file() and demo.suffix.lower() == ".dem", "Select an existing demo")
    require(output_format in (None, FORMAT), "Full-demo output is 640x360 RGB8, 32 FPS, eight-frame histories")
    mode = retention_mode(evidence_retention)
    path.parent.mkdir(parents=True, exist_ok=True)
    with batch._lock(path.parent):
        doc = load_queue(path)
        require(not any(j["demo"] == str(demo) and j["steam_id"] == steam_id and j["status"] != "cancelled"
                        for j in doc["jobs"]), "This demo/player is already queued; resume its existing entry")
        job = {"id": uuid.uuid4().hex, "demo": str(demo), "steam_id": steam_id,
               "player_name": str(player_name)[:150], "match_id": match_id,
               "format": dict(FORMAT), "evidence_retention": mode, "status": "queued", "created_at": now(),
               "completed_segments": 0, "segment_count": 0, "accepted_samples": 0}
        doc["jobs"].append(job); save_settings(path, doc)
    return job


def split_window(start, end, *, max_ticks=MAX_TICKS):
    """Own every even-grid tick once; overlap histories without dropping short tails."""
    require(type(start) is int and type(end) is int and 0 <= start < end, "Invalid source interval")
    require(type(max_ticks) is int and 64 <= max_ticks <= MAX_TICKS and max_ticks % 2 == 0, "Invalid capture bound")
    original = start
    start = max(199, start)
    exclusions = []
    if original < start:
        exclusions.append({"start_demo_tick": original, "end_demo_tick": min(start, end), "reason": "before_protected_replay_start"})
    if start >= end:
        return [], exclusions
    even_end = end - (end-start) % 2
    if even_end < end:
        exclusions.append({"start_demo_tick": even_end, "end_demo_tick": end, "reason": "odd_final_tick_outside_32fps_grid"})
    if even_end-start < 32:
        if start < even_end:
            exclusions.append({"start_demo_tick": start, "end_demo_tick": even_end, "reason": "alive_window_below_capture_minimum"})
        return [], exclusions
    segments, cursor = [], start
    while cursor < even_end:
        capture_start = cursor if not segments else cursor-HISTORY_OVERLAP_TICKS
        capture_end = min(even_end, capture_start+max_ticks)
        remaining = even_end-capture_end
        # Redistribute a tiny tail so the next capture meets the 32-tick minimum.
        if 0 < remaining < 32-HISTORY_OVERLAP_TICKS:
            capture_end -= 32-HISTORY_OVERLAP_TICKS-remaining
        require(32 <= capture_end-capture_start <= max_ticks, "Invalid tail capture")
        segments.append({"start_demo_tick": capture_start, "end_demo_tick": capture_end,
                         "owned_start_demo_tick": cursor, "owned_end_demo_tick": capture_end,
                         "history_overlap_ticks": cursor-capture_start})
        cursor = capture_end
    return segments, exclusions


def exclusion_timeline(source, canonical, windows, segments, edge_exclusions):
    """Describe the complement of scheduled play, without guessing missing state."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    steam_id = int(source["steam_id"])
    rounds = {r["round_id"]: r for r in pq.read_table(Path(source["parsed"])/"rounds.parquet").to_pylist()}
    phases = phase_evidence(Path(source["phase_manifest"]), canonical, rounds)
    doc = read_json(Path(source["phase_manifest"]))
    end = doc["events"][-1]["demo_tick"]
    owned = sorted((s["owned_start_demo_tick"], s["owned_end_demo_tick"]) for s in segments)
    edges = sorted(edge_exclusions, key=lambda r: r["start_demo_tick"])
    bad_commands = [(w["start_demo_tick"], w["end_demo_tick"]) for w in windows
                    if not w["command_coverage"]["complete_within_gap_limit"]]
    rows, cursor, owner = [], 0, 0

    def add(a, b, reason):
        if b <= a:
            return
        if rows and rows[-1]["end_demo_tick"] == a and rows[-1]["reason"] == reason:
            rows[-1]["end_demo_tick"] = b
        else:
            rows.append({"start_demo_tick": a, "end_demo_tick": b, "reason": reason})

    fields = ("steam_id", "demo_tick", "round_id", "alive", "is_warmup", "is_freeze_time", "is_paused", "spectator_user_id")
    with pq.ParquetFile(Path(source["parsed"])/"player_state.parquet") as reader:
        columns = [f for f in fields if f in reader.schema_arrow.names]
        for part in reader.iter_batches(batch_size=65536, columns=columns):
            table = pa.Table.from_batches([part])
            table = table.filter(pc.equal(table["steam_id"], pa.scalar(steam_id, table["steam_id"].type)))
            for row in table.to_pylist():
                tick = row["demo_tick"]
                if tick >= end:
                    continue
                require(tick >= cursor, "Selected player state repeats or reverses")
                add(cursor, tick, "missing_player_state")
                cursor = tick+1
                while owner < len(owned) and owned[owner][1] <= tick:
                    owner += 1
                if owner < len(owned) and owned[owner][0] <= tick < owned[owner][1]:
                    continue
                edge = next((e["reason"] for e in edges if e["start_demo_tick"] <= tick < e["end_demo_tick"]), None)
                info = rounds.get(row["round_id"], {})
                if edge:
                    reason = edge
                elif not phases.get(row["round_id"], {}).get("phase_verified"):
                    reason = "setup_warmup_or_unverified_round"
                elif row.get("is_freeze_time") or tick < (info.get("freeze_end_tick") or end):
                    reason = "freeze_time"
                elif row.get("is_paused"):
                    reason = "pause"
                elif row.get("alive") is not True:
                    reason = "player_dead"
                elif tick >= (info.get("end_tick") or 0):
                    reason = "after_round_end"
                elif any(a <= tick < b for a, b in bad_commands):
                    reason = "incomplete_command_coverage"
                else:
                    reason = "unresolved_identity_or_state_continuity"
                add(tick, tick+1, reason)
    add(cursor, end, "missing_player_state")
    require(sum(b-a for a, b in owned)+sum(r["end_demo_tick"]-r["start_demo_tick"] for r in rows) == end,
            "Coverage accounting does not span the entire source timeline")
    return rows, end


def plan_demo(sources_path, out, *, steam_id, max_ticks=MAX_TICKS, evidence_retention="lean"):
    mode = retention_mode(evidence_retention)
    out = Path(out).resolve()
    require(not out.exists(), "Full-demo planning needs a fresh directory")
    steam_id = normalize_steam_id(steam_id)
    require(steam_id is not None, "A full-demo job requires one player")
    inputs = read_json(Path(sources_path))
    require(inputs.get("profile") == collection.SOURCE_PROFILE and len(inputs.get("sources", [])) == 1,
            "Full-demo processing requires exactly one prepared demo")
    source = dict(inputs["sources"][0])
    for key in collection.SOURCE_PATHS:
        path = Path(source[key]); source[key] = str((path if path.is_absolute() else Path(sources_path).parent/path).resolve())
    canonical = parsed_manifest(Path(source["parsed"]), ("rounds.parquet", "player_state.parquet", "usercmd.parquet"))
    require(canonical.get("tick_rate") == 64, "Full-demo processing requires 64 Hz source commands")
    source.update(demo_id=canonical["demo_id"], steam_id=steam_id)
    files = {}
    for path, digest in [(Path(source["demo"]), canonical["demo_id"]),
                         (Path(source["parsed"])/"manifest.json", None),
                         (Path(source["phase_manifest"]), None), (Path(source["state_context"]), None)]:
        batch._watch(files, path, digest)
    for filename in ("rounds.parquet", "player_state.parquet", "usercmd.parquet"):
        batch._watch(files, Path(source["parsed"])/filename, canonical["files"][filename])
    out.mkdir(parents=True, exist_ok=False)
    discovery = out/"player_windows.jsonl"
    render_jobs(parsed=Path(source["parsed"]), demo=Path(source["demo"]), out=discovery,
                fps=32, width=640, height=360, min_ticks=1, steam_id=int(steam_id),
                phase_manifest=Path(source["phase_manifest"]), allow_incomplete_commands=True)
    windows = sorted([json.loads(line) for line in discovery.read_text().splitlines() if line], key=lambda w: w["start_demo_tick"])
    segments, edges = [], []
    for window in windows:
        if not window["command_coverage"]["complete_within_gap_limit"]:
            continue
        require(window["steam_id"] == steam_id, "Planner substituted another player")
        split, omitted = split_window(window["start_demo_tick"], window["end_demo_tick"], max_ticks=max_ticks)
        edges += [{**e, "round_id": window["round_id"]} for e in omitted]
        for interval in split:
            key = batch._digest([PROFILE, source["demo_id"], steam_id, interval, FORMAT])[:24]
            segments.append({**interval, "id": key, "round_id": window["round_id"],
                             "competitive_round_number": window["phase_evidence"].get("competitive_round_number"),
                             "source_job": window})
    require(len(segments) <= 4096, "Full-demo segment count exceeds the queue safety bound")
    excluded, source_ticks = exclusion_timeline(source, canonical, windows, segments, edges)
    owned_ticks = sum(s["owned_end_demo_tick"]-s["owned_start_demo_tick"] for s in segments)
    capture_ticks = sum(s["end_demo_tick"]-s["start_demo_tick"] for s in segments)
    plan = {"schema_version": 1, "profile": PROFILE, "format": FORMAT, "source": source, "evidence_retention": mode,
            "source_files": files, "segments": segments, "exclusions": excluded,
            "source_ticks": source_ticks, "owned_ticks": owned_ticks, "capture_ticks": capture_ticks,
            "planned_seconds": owned_ticks/64, "history_overlap_ticks": capture_ticks-owned_ticks,
            "exclusion_seconds": dict(Counter()), "training_ready": False}
    for row in excluded:
        key = row["reason"]
        plan["exclusion_seconds"][key] = plan["exclusion_seconds"].get(key, 0)+(row["end_demo_tick"]-row["start_demo_tick"])/64
    write_json(out/"demo_plan.json", plan)
    save_settings(out/"progress.json", {"profile": PROFILE, "plan_sha256": sha256_file(out/"demo_plan.json"),
                  "segments": {}, "status": "planned", "accepted_samples": 0})
    write_report(out, plan, read_json(out/"progress.json"))
    return plan


def prepare_segment(root, plan, segment):
    work = root/"work"/segment["id"]
    plan_path = work/"batch/batch_plan.json"
    if plan_path.is_file():
        return work
    # Incomplete clock/planning output gets its own retained attempt directory.
    if work.exists():
        recovery = root/"incomplete-planning"/(segment["id"]+"-"+uuid.uuid4().hex[:8])
        recovery.parent.mkdir(exist_ok=True)
        require(work.resolve().is_relative_to(root.resolve()) and recovery.resolve().is_relative_to(root.resolve()), "Unsafe planning recovery")
        work.rename(recovery)
    work.mkdir(parents=True, exist_ok=False)
    source = {k: v for k, v in plan["source"].items() if k != "steam_id"}
    clock = work/"clock.json"
    collection._clock(source, [segment], clock, PROJECT/"bin/cs2-clocks.exe")
    source["network_clock"] = str(clock)
    source["clock_windows"] = collection.clock_windows([segment])
    source["selections"] = [{"steam_id": int(plan["source"]["steam_id"]), "round_id": segment["round_id"],
                             "start_demo_tick": segment["start_demo_tick"], "end_demo_tick": segment["end_demo_tick"]}]
    job = deepcopy(segment["source_job"])
    job.update(start_demo_tick=segment["start_demo_tick"], end_demo_tick=segment["end_demo_tick"],
               clip_id=segment["id"], full_demo_profile=PROFILE, competitive_replay_profile=batch.CURRENT_PROFILE)
    job["source_job_interval"] = [segment["source_job"]["start_demo_tick"], segment["source_job"]["end_demo_tick"]]
    job["source_job_command_coverage"] = job.pop("command_coverage")
    spec = work/"batch/jobs"/(job["clip_id"]+".json")
    write_json(spec, job)
    files = dict(plan["source_files"])
    batch._watch(files, clock); batch._watch(files, spec)
    ticks = job["end_demo_tick"]-job["start_demo_tick"]
    write_json(plan_path, {"schema_version": 1, "profile": batch.PROFILE, "full_demo_profile": PROFILE,
               "clip_ticks": ticks, "max_jobs": 1, "sources": [source], "files": files,
               "stage_order": list(batch.STAGES), "training_ready": False,
               "jobs": [{"job_id": job["clip_id"], "source_id": source["source_id"], "job": job,
                         "spec": str(spec), "spec_sha256": files[str(spec)]}]})
    return work


def write_report(root, plan, progress):
    e = lambda value: html.escape(str(value))
    statuses = progress.get("segments", {})
    rows = []
    for index, segment in enumerate(plan["segments"], 1):
        state = statuses.get(segment["id"], {})
        reasons = '; '.join(f"{key.replace('_', ' ')}: {count:,}" for key, count in state.get('reason_counts', {}).items())
        rows.append(f"<tr><td>{index}</td><td>{e(segment.get('competitive_round_number') or segment['round_id'])}</td>"
                    f"<td>{segment['owned_start_demo_tick']/64:.2f}–{segment['owned_end_demo_tick']/64:.2f}s</td>"
                    f"<td>{e(state.get('status', 'pending'))}</td><td>{state.get('sample_count', 0):,}</td>"
                    f"<td>{state.get('rejected_count', 0):,}</td><td>{state.get('duplicate_count', 0):,}</td>"
                    f"<td>{e(state.get('error', '') or reasons)}</td></tr>")
    exclusions = ''.join(f"<tr><td>{e(k.replace('_', ' '))}</td><td>{v:.2f}s</td></tr>" for k, v in plan["exclusion_seconds"].items())
    details = ''.join(f"<tr><td>{r['start_demo_tick']/64:.2f}–{r['end_demo_tick']/64:.2f}s</td><td>{e(r['reason'].replace('_',' '))}</td></tr>" for r in plan["exclusions"])
    pipeline = progress.get("pipeline", {})
    concurrency = (f"<p>Recording {pipeline.get('recording', 0)}/1 · "
        f"Validating {pipeline.get('validating', 0)}/{pipeline.get('validation_workers', 2)} · "
        f"Waiting {pipeline.get('waiting', 0)} · Compressing {pipeline.get('compressing', 0)}/1 · "
        f"In progress {pipeline.get('in_flight', 0)}/{pipeline.get('max_in_flight', 3)}</p>") if pipeline else ""
    body = f'''<!doctype html><html><head><meta charset="utf-8"><title>Full demo processing</title>
<style>body{{font:16px system-ui;background:#101725;color:#eef3fb;max-width:1150px;margin:40px auto;padding:0 24px}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #334155}}p{{color:#b9c8db}}a{{color:#83caff}}</style></head>
<body><h1>{e(Path(plan['source']['demo']).name)}</h1><p>Player {e(plan['source']['steam_id'])} · 640×360 · RGB · 8 bits/channel · 32 FPS · eight-frame histories</p>
<h2>{e(progress.get('status','planned').replace('_',' ').title())}</h2>
{concurrency}
<p>{len([v for v in statuses.values() if v.get('status')=='complete'])}/{len(plan['segments'])} segments compressed · {progress.get('accepted_samples',0):,} unique accepted examples · {plan['planned_seconds']/60:.2f} minutes of eligible play</p>
<p>The queue continues automatically. Capture boundaries include overlapping history; duplicate action targets are removed. Dead time, pauses, setup and unavailable source data are excluded. Accepted examples also require valid timing and labels.</p>
<table><tr><th>Segment</th><th>Round</th><th>Owned source interval</th><th>Status</th><th>Examples</th><th>Rejected</th><th>Duplicates</th><th>Details</th></tr>{''.join(rows)}</table>
<h2>Excluded source time</h2><table>{exclusions}</table><details><summary>All excluded intervals</summary><table>{details}</table></details>
<p>Training pixels are losslessly compressed. Evidence retention: {e(plan.get('evidence_retention', 'full'))}. Lean mode releases temporary evidence after all dependent training packages are verified; full mode retains debug archives. The original demo and parsed source remain external references. Model training has not run.</p></body></html>'''
    path = root/"index.html"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(body, encoding="utf-8"); temporary.replace(path)


def run_demo(root, *, stop=None, emit=None, max_segments=None, validation_workers=2):
    root = Path(root).resolve()
    stop, emit = stop or threading.Event(), emit or (lambda message: None)
    plan = read_json(root/"demo_plan.json")
    require(plan.get("profile") == PROFILE and plan.get("format") == FORMAT, "Unsupported full-demo plan")
    retention_mode(plan.get("evidence_retention", "full"))
    progress = read_json(root/"progress.json")
    require(progress["plan_sha256"] == sha256_file(root/"demo_plan.json"), "Full-demo plan changed")
    batch._verify_hashes(plan["source_files"])
    tools = renderer_tools(PROJECT)
    seen = set()
    shared_verified = set()
    for segment_id, record in progress["segments"].items():
        if record.get("status") != "complete":
            continue
        receipt = read_json(Path(record["receipt"]))
        require(sha256_file(Path(record["receipt"])) == record["receipt_sha256"], "Completed package receipt changed")
        for key in package_artifacts(receipt):
            require(sha256_file(Path(receipt[key]["path"])) == receipt[key]["sha256"], "Completed archive changed")
        shared = receipt.get("shared_session")
        if shared is not None:
            from .session_processing import verify_archive
            shared_path = Path(shared["receipt"])
            require(sha256_file(shared_path) == shared["receipt_sha256"], "Completed shared session receipt changed")
            identity = (str(shared_path.resolve()), shared["receipt_sha256"])
            if identity not in shared_verified:
                verify_archive(read_json(shared_path))
                shared_verified.add(identity)
        with zipfile.ZipFile(receipt["training_archive"]["path"]) as archive:
            for line in archive.read("samples.jsonl").splitlines():
                seen.add(sample_key(json.loads(line)["sample"]))
        work = root/"work"/segment_id
        if work.exists():
            release_work(work, receipt, root)
    from .demo_pipeline import run_pipeline, worker_count
    from .recording_session import prepare_recordings
    from .session_processing import release_completed_sessions
    release_completed_sessions(root, progress)
    worker_count(validation_workers)
    jobs = prepare_recordings(root, plan, progress, tools, stop=stop, emit=emit, max_segments=max_segments)
    if jobs is None:
        return progress
    from .immutable_evidence import hold_files
    from .session_processing import shared_data_files
    with hold_files(shared_data_files(root)):
        result = run_pipeline(root, plan, progress, tools, seen, stop=stop, emit=emit,
                            validation_workers=validation_workers, max_segments=max_segments, session_jobs=jobs)
    release_completed_sessions(root, progress)
    return result


def run_queue(task, queue_path, *, validation_workers=2, evidence_retention=None):
    from .demo_pipeline import worker_count
    worker_count(validation_workers)
    if evidence_retention is not None:
        retention_mode(evidence_retention)
    queue_path = Path(queue_path).resolve()
    (task.project/"data/launcher").mkdir(parents=True, exist_ok=True)
    with batch._lock(queue_path.parent), batch._lock(task.project/"data/launcher"):
        doc = load_queue(queue_path)
        for job in doc["jobs"]:
            if job["status"] in ("complete", "cancelled"):
                continue
            if task.stop.is_set():
                break
            root = queue_path.parent/"jobs"/job["id"]
            try:
                job.update(status="preprocessing", updated_at=now()); job.pop("error", None)
                save_settings(queue_path, doc); task.emit("queue", str(queue_path))
                task.log("Processing entire demo for "+job["player_name"]+": "+job["demo"])
                if not (root/"demo_plan.json").is_file():
                    sources = prepare_sources(task, [Path(job["demo"])], job.get("match_id", ""))
                    if root.exists():
                        old = root.with_name(root.name+"-incomplete-"+uuid.uuid4().hex[:8])
                        require(root.resolve().is_relative_to(queue_path.parent) and old.resolve().is_relative_to(queue_path.parent), "Unsafe queue recovery path")
                        root.rename(old)
                    plan_demo(sources, root, steam_id=job["steam_id"],
                              evidence_retention=evidence_retention or job.get("evidence_retention", "lean"))
                plan = read_json(root/"demo_plan.json")
                require(Path(plan["source"]["demo"]).resolve() == Path(job["demo"]).resolve() and
                        plan["source"]["steam_id"] == job["steam_id"], "Saved plan belongs to another queued demo/player")
                job.update(report=str(root/"index.html"), output=str(root), segment_count=len(plan["segments"]),
                           planned_seconds=plan["planned_seconds"], status="processing",
                           evidence_retention=plan.get("evidence_retention", "full"))
                save_settings(queue_path, doc)

                def update(message):
                    task.log(message); task.emit("status", message)
                    current = read_json(root/"progress.json")
                    job.update(completed_segments=sum(v["status"] == "complete" for v in current["segments"].values()),
                               accepted_samples=current["accepted_samples"], pipeline=current.get("pipeline", {}),
                               status=current.get("status", "processing"))
                    save_settings(queue_path, doc); task.emit("queue", str(queue_path))

                progress = run_demo(root, stop=task.stop, emit=update, validation_workers=validation_workers)
                job.update(status=progress["status"], accepted_samples=progress["accepted_samples"],
                           completed_segments=sum(v["status"] == "complete" for v in progress["segments"].values()), updated_at=now())
                save_settings(queue_path, doc); task.emit("queue", str(queue_path))
                if progress["status"] != "complete":
                    break
            except Exception as error:
                job.update(status="stopped" if isinstance(error, StopRequested) else "needs_attention", error=str(error), updated_at=now())
                save_settings(queue_path, doc); task.emit("queue", str(queue_path))
                raise
    remaining = [j for j in doc["jobs"] if j["status"] not in ("complete", "cancelled")]
    return {"queue_path": str(queue_path), "completed_demos": sum(j["status"] == "complete" for j in doc["jobs"]),
            "status": "stopped" if task.stop.is_set() else (remaining[0]["status"] if remaining else "complete")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--retain-evidence", action="store_true", help="Keep full debug evidence for newly planned demos")
    args = parser.parse_args(argv)
    from .launcher_backend import TaskRunner
    args.queue.parent.mkdir(parents=True, exist_ok=True)
    (PROJECT/"data/launcher").mkdir(parents=True, exist_ok=True)
    task = TaskRunner(args.output, "full-demo", lambda kind, value: print(value, flush=True), threading.Event())
    try:
        result = run_queue(task, args.queue, validation_workers=args.validation_workers,
                           evidence_retention="full" if args.retain_evidence else None)
        task.finish("finished", result=result)
    except Exception as error:
        task.finish("failed", error=str(error)); raise


if __name__ == "__main__":
    main()
