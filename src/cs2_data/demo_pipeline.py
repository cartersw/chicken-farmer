"""One recorder, bounded independent validators, and ordered archive publication."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import multiprocessing
from pathlib import Path
import queue
import shutil
import uuid

from . import competitive_batch as batch
from .io import read_json, sha256_file
from .launcher_backend import save_settings

DEFAULT_VALIDATION_WORKERS = 2
MAX_VALIDATION_WORKERS = 4
MIN_FREE_BYTES = 15_000_000_000
STAGE_NAMES = {"render": "capture", "process": "frame/action processing", "synchronization": "timing alignment",
               "hud_review": "approved setup receipt", "acceptance": "training acceptance"}
_events = None


def worker_count(value):
    if type(value) is not int or not 1 <= value <= MAX_VALIDATION_WORKERS:
        raise ValueError("Choose between 1 and 4 validation workers")
    return value


def _initialize_worker(events):
    global _events
    _events = events
    # Each process owns its PATH and proof state. Keep Arrow's internal pools
    # small so several validators do not each consume every logical CPU.
    import pyarrow as pa
    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)


@contextmanager
def create_workers(count):
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    try:
        with ProcessPoolExecutor(1, mp_context=context, initializer=_initialize_worker, initargs=(events,)) as recorder, \
             ProcessPoolExecutor(count, mp_context=context, initializer=_initialize_worker, initargs=(events,)) as validators, \
             ThreadPoolExecutor(1, thread_name_prefix="training-archive") as archiver:
            yield recorder, validators, archiver, events
    finally:
        events.close()
        events.join_thread()


def record_segment(root, plan, segment, tools):
    from .full_demo import prepare_segment
    work = prepare_segment(Path(root), plan, segment)
    _run_stage(work, segment["id"], tools, "record", "verified_render_artifacts")
    return str(work)


def validate_segment(work, segment_id, tools, session_job=None):
    from .session_processing import index_files, retained_work_index
    from .immutable_evidence import retain_files
    index = session_job["index"] if session_job is not None else retained_work_index(work)
    if index is not None:
        retain_files(index_files(index))
    if session_job is not None:
        from .session_processing import materialize_segment
        root = Path(session_job["root"])
        work = materialize_segment(root, read_json(root/"demo_plan.json"), session_job["segment"],
            session_job["index"], tools, Path(session_job["receipt"]))
    _run_stage(Path(work), segment_id, tools, "process", "accepted_partition_verified")


def _run_stage(work, segment_id, tools, mode, expected):
    def progress(stage, operation):
        if _events is not None:
            _events.put((segment_id, mode, stage, operation))
    report = batch.run_batch(work/"batch", execute=True, max_jobs=1, retry_failed=True,
                             mode=mode, progress=progress, **tools)
    outcomes = report["jobs"]
    if len(outcomes) != 1 or outcomes[0]["status"] != expected:
        raise ValueError(f"Segment {mode} needs attention: {outcomes}")


def captured_work(root, segment_id):
    """A journal routes work; process mode independently verifies its evidence."""
    work = root/"work"/segment_id
    path = work/"batch/batch_state.json"
    if path.is_file():
        state = read_json(path)
        attempts = state.get("jobs", {}).get(segment_id, {}).get("stages", {}).get("render", [])
        if attempts and attempts[-1].get("status") == "completed":
            return work
    return None


def run_pipeline(root, plan, progress, tools, seen, *, stop, emit, validation_workers, max_segments=None, session_jobs=None):
    from .full_demo import now, pack_segment, release_work, write_report
    count = worker_count(validation_workers)
    # Includes the recorder, validators, waiting clips, and the current archive.
    # Releasing a slot requires durable archives AND working-file cleanup.
    limit = count + 1
    pending = [s for s in plan["segments"] if progress["segments"].get(s["id"], {}).get("status") != "complete"]
    if max_segments is not None:
        if type(max_segments) is not int or max_segments < 0:
            raise ValueError("Invalid segment limit")
        pending = pending[:max_segments]
    indices = {s["id"]: i for i, s in enumerate(plan["segments"], 1)}
    active, futures = {}, {}
    failure = None
    low_disk = False
    progress["pipeline"] = {"validation_workers": count, "max_in_flight": limit}

    def persist(message=None):
        states = [progress["segments"][key]["status"] for key in active]
        progress["pipeline"].update(recording=states.count("recording"), validating=states.count("validating"),
            waiting=states.count("recorded") + states.count("validated"), compressing=states.count("compressing"),
            in_flight=len(active), capture_waiting_for_space=low_disk)
        save_settings(root/"progress.json", progress)
        write_report(root, plan, progress)
        if message:
            emit(message)

    def describe(key, message):
        return f"Segment {indices[key]}/{len(plan['segments'])}: {message}"

    with create_workers(count) as (recorder, validators, archiver, events):
        while True:
            # Only this coordinator writes shared progress, reports and queue
            # state. Workers own separate batch directories and send messages.
            while True:
                try:
                    key, mode, stage, operation = events.get_nowait()
                except queue.Empty:
                    break
                state = progress["segments"].get(key, {})
                if key in active and state.get("status") == ("recording" if mode == "record" else "validating"):
                    state["stage"] = stage
                    persist(describe(key, f"{STAGE_NAMES[stage]} ({'verification' if operation == 'verify' else 'running'})"))

            for future in list(futures):
                if not future.done():
                    continue
                kind, key = futures.pop(future)
                item, state = active[key], progress["segments"][key]
                try:
                    result = future.result()
                    state[kind+"_finished_at"] = now()
                    if kind == "record":
                        item["work"] = Path(result)
                        state["status"] = "recorded"
                        persist(describe(key, "recorded; queued for validation"))
                    elif kind == "validate":
                        state["status"] = "validated"
                        persist(describe(key, "validation complete; queued for compression"))
                    else:
                        receipt = result
                        state.update(status="complete", receipt=str(item["package"]/"receipt.json"),
                            receipt_sha256=sha256_file(item["package"]/"receipt.json"), finished_at=now(),
                            **{k: receipt[k] for k in ("sample_count", "rejected_count", "duplicate_count", "compressed_bytes", "reason_counts")})
                        progress["accepted_samples"] = sum(v.get("sample_count", 0) for v in progress["segments"].values() if v["status"] == "complete")
                        # Receipt precedes deletion. A cleanup failure preserves
                        # completion so resume cannot recapture or repack it.
                        persist()
                        release_work(item["work"], receipt, root)
                        del active[key]
                        persist(describe(key, f"complete: {receipt['sample_count']:,} examples, {receipt['compressed_bytes']/1e9:.2f} GB compressed"))
                except Exception as error:
                    if state["status"] != "complete":
                        state["status"] = "needs_attention"
                    state["error"] = str(error)
                    failure = failure or error
                    progress["status"] = "needs_attention"
                    persist(describe(key, "needs attention; finishing active workers and retaining captured clips"))

            if not failure:
                validating = sum(kind == "validate" for kind, _ in futures.values())
                for key, item in active.items():
                    state = progress["segments"][key]
                    if state["status"] == "recorded" and validating < count:
                        state.update(status="validating", validate_started_at=now())
                        args = (str(item["work"]), key, tools)
                        if session_jobs is not None:
                            args += (session_jobs.get(key),)
                        futures[validators.submit(validate_segment, *args)] = ("validate", key)
                        validating += 1
                        persist(describe(key, "validating frames, timing and action targets"))
                # One archiver consumes the earliest outstanding segment. Later
                # validations may finish first; deduplication order never changes.
                if active and not any(kind == "pack" for kind, _ in futures.values()):
                    key = next(iter(active))
                    item, state = active[key], progress["segments"][key]
                    if state["status"] == "validated":
                        item["package"] = root/"packages"/key/uuid.uuid4().hex[:12]
                        state.update(status="compressing", pack_started_at=now())
                        futures[archiver.submit(pack_segment, item["work"], item["package"], key, seen=seen)] = ("pack", key)
                        persist(describe(key, "compressing RGB training frames and evidence"))

                low_disk = False
                while pending and len(active) < limit and not stop.is_set():
                    if any(kind == "record" for kind, _ in futures.values()):
                        break
                    segment = pending[0]
                    key = segment["id"]
                    work = captured_work(root, key)
                    if session_jobs is not None and work is None:
                        if key not in session_jobs:
                            raise ValueError("Recording barrier missing a segment; refusing a per-clip game launch")
                        work = root/"work"/key
                    if work is None and shutil.disk_usage(root).free < MIN_FREE_BYTES:
                        low_disk = True
                        break
                    pending.pop(0)
                    active[key] = {"work": work}
                    state = progress["segments"].setdefault(key, {})
                    state.pop("error", None)
                    state.update(status="recorded" if work else "recording", started_at=now())
                    progress["status"] = "processing"
                    if work is None:
                        state["record_started_at"] = now()
                        futures[recorder.submit(record_segment, str(root), plan, segment, tools)] = ("record", key)
                    persist(describe(key, "resuming saved capture" if work else "recording (validation runs in background)"))

            if failure and not futures:
                persist()
                raise failure
            if not active and (not pending or stop.is_set() or low_disk):
                all_complete = all(progress["segments"].get(s["id"], {}).get("status") == "complete" for s in plan["segments"])
                progress["status"] = "complete" if all_complete else ("paused_low_disk" if low_disk and not stop.is_set() else "stopped")
                persist()
                return progress
            if futures:
                wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
