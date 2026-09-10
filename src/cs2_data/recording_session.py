"""One game lifecycle for a forward-only demo/player recording schedule."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid

from . import competitive_batch as batch
from .io import read_json, sha256_file
from .launcher_backend import PROJECT, save_settings
from .training_archive import FORMAT, retention_mode

from .session_profile import PROFILE, PLUGIN_SHA256, require

PLUGIN = PROJECT/"tools/renderer/build/plugin-session/Release/server.dll"
RESERVE = 15_000_000_000
BRIDGE_TICKS = 256


def plan_session(plan, segments=None):
    require(plan.get("format") == FORMAT, "Recording session requires the decided RGB8 format")
    mode = retention_mode(plan.get("evidence_retention", "full"))
    segments = plan["segments"] if segments is None else segments
    require(segments and len(segments) <= 4096, "Recording session needs 1-4096 logical shards")
    runs = []
    for segment in segments:
        start, end = segment["start_demo_tick"], segment["end_demo_tick"]
        source = segment["source_job"]
        require(type(start) is int and type(end) is int and 199 <= start < end and (end-start) % 2 == 0,
                "Invalid session shard interval")
        require(str(source["steam_id"]) == str(plan["source"]["steam_id"]) and source["demo_id"] == plan["source"]["demo_id"] and
                (source["width"], source["height"], source["fps"]) == (640, 360, 32), "Session shard source or format mismatch")
        require(source["start_demo_tick"] <= start < end <= source["end_demo_tick"], "Session interval leaves its eligible source window")
        require(not runs or start >= runs[-1]["eligible"][-1]["start"], "Session shards must advance through the demo")
        # Padding supplies a real subsequent frame at every logical shard end.
        # Physical recording never stops at an overlapping training-file cut.
        physical_start, physical_end = max(199, start-2), end+2
        if runs and physical_start-runs[-1]["end_tick"] < BRIDGE_TICKS:
            require(source["spectator_user_id"] == runs[-1]["spectator_user_id"], "Identity changes need a separated recording run")
            run = runs[-1]
            run["end_tick"] = max(run["end_tick"], physical_end)
        else:
            run = {"start_tick": physical_start, "end_tick": physical_end,
                   "spectator_user_id": source["spectator_user_id"], "eligible": [], "segments": []}
            runs.append(run)
        run["eligible"].append({"start": start, "end": end, "round_id": segment["round_id"]})
        run["segments"].append(segment["id"])
    for run in runs:
        run["id"] = batch._digest([PROFILE, plan["source"]["demo_id"], plan["source"]["steam_id"], run])[:24]
        # Tick cadence can have the opposite parity after a round boundary.
        run["min_frames"] = (run["end_tick"]-run["start_tick"])//2
        run["max_frames"] = run["min_frames"]+1
    nominal = sum(run["max_frames"] for run in runs)
    raw_bytes = nominal*(640*360*4+18)
    replay_seconds = (runs[-1]["end_tick"]-max(0, runs[0]["start_tick"]-256))/64
    # Native message and readback transcripts have explicit per-session ceilings.
    # Conservative initial allowance, refined only after measuring real sessions.
    log_bytes = int(replay_seconds*12_000_000)+512_000_000
    staging_bytes = Path(plan["source"]["demo"]).stat().st_size
    # Budget raw evidence, the index, training output and the optional debug
    # archive together. Do not assume favorable compression to admit a session.
    index_bytes = log_bytes+512_000_000
    archive_bytes = raw_bytes+log_bytes+index_bytes+512_000_000 if mode == "full" else 0
    training_bytes = nominal*FORMAT["frame_bytes"]+2_000_000_000
    return {"schema_version": 1, "profile": PROFILE, "demo_id": plan["source"]["demo_id"],
            "steam_id": str(plan["source"]["steam_id"]), "format": FORMAT, "runs": runs, "evidence_retention": mode,
            "logical_segments": len(segments), "max_frames": nominal,
            "storage": {"raw_frame_bytes": raw_bytes, "max_log_bytes": log_bytes,
                "max_index_bytes": index_bytes, "max_shared_archive_bytes": archive_bytes,
                "max_training_archive_bytes": training_bytes,
                "staged_demo_bytes": staging_bytes, "reserve_bytes": RESERVE,
                "required_free_bytes": raw_bytes+log_bytes+index_bytes+archive_bytes+training_bytes+staging_bytes+RESERVE+12_000_000_000},
            "timeout_seconds": max(600, int(replay_seconds*12+300))}


def stage_schedule(schedule, out, run_id, worker):
    schedule = json.loads(json.dumps(schedule))
    schedule["session_id"] = run_id
    schedule["setup_commands"] = ["sv_cheats 1", "demo_timescale 1", "demo_ui_mode 0", "volume 0",
        *worker.HUD_COMMANDS, "cl_demo_predict 0", "host_framerate 32"]
    for run in schedule["runs"]:
        run["prefix"] = run["id"]+"-"+run_id[:12]
    save_settings(out/"session-plan.json", schedule)
    return schedule


def wait_for_session(process, roots, schedule, out, worker, stop, emit):
    started, last_progress, last_change = time.monotonic(), None, time.monotonic()
    progress_path = out/"session-progress.json"
    while process.poll() is None:
        if stop is not None and stop.is_set():
            (out/"stop.request").touch(exist_ok=True)
        if progress_path.exists():
            try:
                current = read_json(progress_path)
            except (OSError, ValueError):
                current = None
            if current is not None and current != last_progress:
                require(current.get("profile") == PROFILE and current.get("session_id") == schedule["session_id"] and
                        current.get("pid") == process.pid, "Recording progress belongs to another process")
                last_progress, last_change = current, time.monotonic()
                if emit:
                    emit(f"Recording demo: run {min(current['run_index']+1, current['run_count'])}/{current['run_count']} · "
                         f"{current['phase'].replace('_', ' ')} · tick {current['demo_tick']:,}")
        free = shutil.disk_usage(out).free
        logs = sum(p.stat().st_size for p in out.iterdir() if p.suffix in (".jsonl", ".log"))
        if free < RESERVE or logs > schedule["storage"]["max_log_bytes"]:
            (out/"stop.request").touch(exist_ok=True)
            save_settings(out/"storage-stop.json", {"free_bytes": free, "log_bytes": logs})
        if free < 1_000_000_000 or time.monotonic()-started > schedule["timeout_seconds"] or time.monotonic()-last_change > 180:
            worker.stop_owned_process(process)
            raise RuntimeError("Recording session exceeded its storage/time/progress bound; retained for recovery")
        time.sleep(0.5)
    return process.returncode


def finish_capture(out, report, schedule, roots, worker):
    progress = read_json(out/"session-progress.json")
    require(progress["phase"] in ("complete", "stopped"), "Native recording failed: "+progress.get("error", "missing completion"))
    report.update(recording_session={"profile": PROFILE, "session_id": schedule["session_id"],
        "plan": str(out/"session-plan.json"), "plan_sha256": sha256_file(out/"session-plan.json"),
        "status": progress["phase"], "completed_runs": progress["run_index"]})
    report["settings_isolation"] = worker.verify_settings_isolation(out, expected_pid=report["owned_cs2_pid"])
    report["capture_ledger"] = "capture_ledger.jsonl"
    report["capture_ledger_sha256"] = sha256_file(out/"capture_ledger.jsonl")
    report["recording_roots"] = [str(p) for p in roots]
    # The game is closed and its output directory is now exclusively owned by
    # this immutable attempt. Index/verify and move frames only in phase two.
    report["render_status"] = "session_captured_timing_unverified"
    return report


def capture_session(root, plan, schedule, tools, *, stop=None, emit=None, output=None):
    worker = batch._worker()
    require(PLUGIN.is_file() and sha256_file(PLUGIN) == PLUGIN_SHA256, "Session recorder plugin is missing or changed")
    require(shutil.disk_usage(root).free >= schedule["storage"]["required_free_bytes"],
            f"Recording this demo needs about {schedule['storage']['required_free_bytes']/1e9:.1f} GB free, including logs and workspace")
    out = Path(output) if output else root/"sessions"/uuid.uuid4().hex
    require(out.resolve().parent == (root/"sessions").resolve(), "Session output escapes its owned directory")
    original = dict(plan["segments"][0]["source_job"])
    original.update(clip_id=plan["segments"][0]["id"], full_demo_profile="cs2-full-player-demo-v1",
        competitive_replay_profile=batch.CURRENT_PROFILE, start_demo_tick=plan["segments"][0]["start_demo_tick"],
        end_demo_tick=plan["segments"][0]["end_demo_tick"])
    job = worker.validate_job(original, max_ticks=7680)
    args = worker.argument_parser().parse_args([])
    args.output, args.plugin = out, PLUGIN
    args.ffmpeg, args.ffprobe = tools["ffmpeg"], tools["ffprobe"]
    args.allow_version_mismatch = True
    args.session_plan, args.session_stop, args.session_emit = schedule, stop, emit
    worker.run_capture(args, job, original)
    return out


def prepare_recordings(root, plan, progress, tools, *, stop, emit, max_segments=None):
    """Finish all game lifecycles before admitting any validation worker."""
    from .demo_pipeline import captured_work
    from .session_processing import index_session, pack_session
    from .full_demo import write_report
    state_path = root/"session-state.json"
    digest = sha256_file(root/"demo_plan.json")
    state = read_json(state_path) if state_path.exists() else {"profile": PROFILE, "plan_sha256": digest, "attempts": []}
    require(state.get("profile") == PROFILE and state.get("plan_sha256") == digest, "Recording journal belongs to another demo plan")
    pending = [s for s in plan["segments"] if progress["segments"].get(s["id"], {}).get("status") != "complete"]
    if max_segments is not None:
        require(type(max_segments) is int and max_segments >= 0, "Invalid segment limit")
        pending = pending[:max_segments]

    def persist(phase, message):
        progress["status"] = phase
        progress["pipeline"] = {"phase": phase, "recording": int(phase == "recording"),
            "validating": 0, "compressing": int(phase == "archiving_session"),
            "recorded_segments": len(assigned), "total_segments": len(plan["segments"])}
        save_settings(root/"progress.json", progress)
        save_settings(state_path, state)
        write_report(root, plan, progress)
        emit(message)

    assigned = {}
    for attempt in state["attempts"]:
        out = Path(attempt["out"]).resolve()
        require(out.parent == (root/"sessions").resolve(), "Recording journal path escapes its sessions directory")
        if attempt.get("status") == "released":
            continue
        manifests = list(out.glob("*.render.json"))
        render = read_json(manifests[0]) if len(manifests) == 1 else {}
        if render.get("render_status") != "session_captured_timing_unverified":
            if manifests:
                batch._retry_is_safe("render", out)
            require(attempt.get("status") in ("failed", "recording"), "Completed session evidence is missing")
            attempt["status"] = "failed"
            continue
        schedule = read_json(out/"session-plan.json")
        require(sha256_file(out/"session-plan.json") == render["recording_session"]["plan_sha256"], "Recorded schedule changed")
        attempt["status"] = "captured"
        for run in schedule["runs"][:render["recording_session"]["completed_runs"]]:
            for key in run["segments"]:
                require(key not in assigned, "A logical interval was captured twice")
                assigned[key] = attempt
    missing = [s for s in pending if s["id"] not in assigned and captured_work(root, s["id"]) is None]
    if missing and not stop.is_set():
        schedule = plan_session(plan, missing)
        out = root/"sessions"/uuid.uuid4().hex
        attempt = {"out": str(out), "status": "recording"}
        state["attempts"].append(attempt)
        progress["recording_storage"] = schedule["storage"]
        persist("recording", f"Recording {len(missing)} segments in {len(schedule['runs'])} intervals with one CS2 launch; "
                f"estimated free space needed {schedule['storage']['required_free_bytes']/1e9:.1f} GB")
        def capture_notice(message):
            # Small native checkpoint only; this is progress reporting, not validation.
            checkpoint = out/"session-progress.json"
            try:
                current = read_json(checkpoint)
            except (OSError, ValueError):
                current = None
            if current is not None:
                completed = sum(len(r["segments"]) for r in schedule["runs"][:current["run_index"]])
                progress["pipeline"].update(recorded_segments=len(assigned)+completed,
                    current_run=current["run_index"], run_count=len(schedule["runs"]), demo_tick=current["demo_tick"])
                save_settings(root/"progress.json", progress)
                write_report(root, plan, progress)
            emit(message)
        try:
            capture_session(root, {**plan, "segments": missing}, schedule, tools, stop=stop,
                emit=capture_notice, output=out)
            attempt["status"] = "captured"
            render = read_json(next(out.glob("*.render.json")))
            saved = read_json(out/"session-plan.json")
            for run in saved["runs"][:render["recording_session"]["completed_runs"]]:
                for key in run["segments"]:
                    assigned[key] = attempt
            save_settings(state_path, state)
            if render["recording_session"]["status"] == "stopped":
                persist("stopped", "Capture stopped cleanly; recordings retained for the next queue run")
                return None
        except Exception:
            attempt["status"] = "failed"
            persist("needs_attention", "Recording stopped with an error; retained evidence needs attention")
            raise
    if stop.is_set():
        persist("stopped", "Recordings retained; validation will resume on the next queue run")
        return None
    jobs, prepared = {}, {}
    for segment in pending:
        if captured_work(root, segment["id"]) is not None:
            continue
        attempt = assigned.get(segment["id"])
        require(attempt is not None, "Capture barrier is incomplete; no validation may start")
        out = Path(attempt["out"])
        if str(out) not in prepared:
            persist("indexing_session", "All recording finished; checking shared native timing and frame coverage")
            index = index_session(out, emit=emit)
            mode = retention_mode(plan.get("evidence_retention", "full"))
            persist("archiving_session", "Compressing shared debug evidence" if mode == "full" else "Saving the session receipt; temporary evidence stays until training packages are verified")
            receipt = pack_session(index, root/"session-packages"/out.name, emit=emit, evidence_retention=mode)
            prepared[str(out)] = (index, receipt)
        index, receipt = prepared[str(out)]
        jobs[segment["id"]] = {"root": str(root), "segment": segment, "index": index, "receipt": str(receipt)}
        if stop.is_set():
            persist("stopped", "Shared evidence saved; validation will resume on the next queue run")
            return None
    persist("validating", "Recording is complete; starting parallel validation and training compression")
    return jobs
