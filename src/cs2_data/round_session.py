"""Run a bounded chronological collection through the existing protected batches."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

from . import competitive_batch as batch
from .launcher_backend import PROJECT, read_object, save_settings

PROFILE = "cs2-round-progression-collection-v1"
STATE_PROFILE = "cs2-round-progression-session-v1"
FINISHED = {"pending_visual_review", "accepted_partition_verified"}
FAILURES = {"failed", "retry_required", "recovery_required", "artifact_changed", "evidence_invalid", "revalidation_required"}


def load_collection(directory):
    root = Path(directory).resolve()
    path = root / "round_collection.json"
    doc = read_object(path)
    batch._require(doc.get("schema_version") == 1 and doc.get("profile") == PROFILE,
                   "Choose a supported round collection directory")
    entries = doc.get("batches")
    batch._require(isinstance(entries, list) and 1 <= len(entries) <= 24, "Invalid collection batches")
    for key in ("storage_budget_bytes", "free_space_floor_bytes", "time_budget_seconds"):
        batch._require(type(doc.get(key)) in (int, float) and 0 < doc[key] < 10**15, "Invalid session budget: " + key)
    seen = set()
    for entry in entries:
        plan_path = batch._within(root, entry["plan"])
        batch._require(plan_path.name == "batch_plan.json" and plan_path not in seen, "Invalid or duplicate batch path")
        batch._require(batch.sha256_file(plan_path) == entry["plan_sha256"], "Collection batch plan changed")
        seen.add(plan_path)
    batch._verify_hashes(doc["source_files"])
    return root, doc, batch.sha256_file(path)


def retained_bytes(root):
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def budget_reason(root, doc, elapsed, next_clip_ticks):
    used = retained_bytes(root)
    # Allow for originals, source copies, traces, review and temporary products.
    reserve = max(5_000_000_000, int(next_clip_ticks / 64 * 230_000_000))
    free = shutil.disk_usage(root).free
    if elapsed >= doc["time_budget_seconds"]:
        return "Time budget reached; current clip completed before stopping", used, free
    if used + reserve > doc["storage_budget_bytes"]:
        return "Storage budget cannot cover another clip's working reserve", used, free
    if free - reserve < doc["free_space_floor_bytes"]:
        return "Free-space floor would be crossed by another clip", used, free
    return None, used, free


def request_stop(directory):
    root, _, digest = load_collection(directory)
    save_settings(root / "stop_requested.json", {"collection_sha256": digest,
                  "requested_at": datetime.now(timezone.utc).isoformat()})
    return {"status": "stop_requested", "message": "The current clip and cleanup will finish before stopping."}


def run_session(directory, *, execute=False, max_new_clips=24, project=PROJECT, **options):
    batch._require(type(max_new_clips) is int and 1 <= max_new_clips <= 24, "Choose 1-24 new clips per session")
    root, doc, digest = load_collection(directory)
    if not execute:
        return {"status": "planned_not_executed", "collection": str(root), "steam_id": doc["steam_id"],
                "planned_source_seconds": doc["planned_source_seconds"], "budgets": {
                    key: doc[key] for key in ("storage_budget_bytes", "free_space_floor_bytes", "time_budget_seconds")}}
    from .desktop import application_lock

    with application_lock(Path(project) / "data/launcher/application.lock"), batch._lock(root):
        state_path = root / "session_state.json"
        state = read_object(state_path) if state_path.exists() else {
            "schema_version": 1, "profile": STATE_PROFILE, "collection_sha256": digest,
            "active_seconds": 0.0, "invocations": [], "training_ready": False}
        batch._require(state.get("profile") == STATE_PROFILE and state.get("collection_sha256") == digest,
                       "Session journal belongs to a different collection")
        previous_seconds = state["active_seconds"]
        batch._require(type(previous_seconds) in (int, float) and 0 <= previous_seconds < 10**12,
                       "Invalid recorded session time")
        invocation = {"started_at": datetime.now(timezone.utc).isoformat(), "max_new_clips": max_new_clips,
                      "status": "running", "new_captures": 0, "batches": []}
        state["invocations"].append(invocation)
        started = time.monotonic()
        stop_path = root / "stop_requested.json"
        # A stop is persistent and must be explicitly cleared with --resume-after-stop.
        resume_after_stop = options.pop("resume_after_stop", False)
        if resume_after_stop:
            stop_path.unlink(missing_ok=True)

        def save(status, message=None):
            state.update(status=status, active_seconds=previous_seconds + time.monotonic() - started,
                         retained_bytes=retained_bytes(root), updated_at=datetime.now(timezone.utc).isoformat())
            invocation.update(status=status, new_captures=invocation["new_captures"])
            if message:
                invocation["message"] = message
                print(message, flush=True)
            save_settings(state_path, state)

        try:
            save("running")
            completed_jobs = []
            for entry in doc["batches"]:
                while True:
                    if stop_path.exists():
                        request = read_object(stop_path)
                        batch._require(request.get("collection_sha256") == digest, "Stop request belongs to another collection")
                        save("stopped", "Stop requested; all current work and cleanup returned")
                        return state
                    if invocation["new_captures"] >= max_new_clips:
                        save("paused", "Requested capture count reached; resume the same collection to continue")
                        return state
                    reason, used, free = budget_reason(root, doc, previous_seconds + time.monotonic() - started,
                                                     entry["clip_ticks"])
                    if reason:
                        save("budget_reached", reason)
                        return state
                    print(f"Next protected clip: {Path(entry['plan']).parent.name}; "
                          f"{used / 1e9:.2f} GB retained, {free / 1e9:.1f} GB free", flush=True)
                    summary = batch.run_batch(Path(entry["plan"]), execute=True, max_jobs=1, **options)
                    measurements = summary["performance"]["stage_measurements"]
                    invocation["new_captures"] += measurements["render"]["perform_calls"]
                    invocation["batches"].append({"plan": entry["plan"], "summary": summary})
                    problems = [row for row in summary["jobs"] if row["status"] in FAILURES]
                    if problems:
                        save("needs_attention", "; ".join(f"{r.get('stage')}: {r.get('error') or r['status']}" for r in problems))
                        return state
                    save("running")
                    print(json.dumps({"new_captures": invocation["new_captures"], "statuses": summary["job_status_counts"]}), flush=True)
                    if all(row["status"] in FINISHED for row in summary["jobs"]):
                        completed_jobs.extend(summary["jobs"])
                        break
                    if not any(value["perform_calls"] for value in measurements.values()):
                        save("needs_attention", "The batch made no progress; inspect its retained journal")
                        return state
            accepted = completed_jobs and all(row["status"] == "accepted_partition_verified" for row in completed_jobs)
            save("accepted_partitions_verified" if accepted else "captured_pending_review",
                 "All planned clips completed numerical acceptance" if accepted else
                 "All planned clips finished; retained legacy review status is recorded")
            return state
        except Exception as error:
            save("needs_attention", str(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--collection", type=Path, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--max-new-clips", type=int, default=24)
    run.add_argument("--resume-after-stop", action="store_true")
    for name in ("plugin", "ffmpeg", "ffprobe", "game-dir", "steam-dir"):
        run.add_argument("--" + name, type=Path)
    run.add_argument("--steam-user-id")
    stop = sub.add_parser("stop")
    stop.add_argument("--collection", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    command, directory = args.pop("command"), args.pop("collection")
    result = request_stop(directory) if command == "stop" else run_session(directory, **args)
    print(json.dumps({key: result[key] for key in ("status", "active_seconds", "retained_bytes") if key in result}))
    return 1 if result["status"] == "needs_attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
