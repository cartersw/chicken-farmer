"""Bounded competitive replay campaigns with protected, resumable stages.

Planning writes only workspace files. Running is read-only unless execute=True.
Rendering always enters the existing protected Windows worker. The approved
HUD setup needs no recurring visual review; numerical acceptance stays separate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from .io import parsed_manifest, read_json, sha256_file, write_json
from .jobs import render_jobs
from .native_replay_profile import CURRENT_PROFILE, header_matches_profile

PROFILE = "cs2-bounded-competitive-batch-v1"
SOURCE_PROFILE = "cs2-competitive-batch-sources-v1"
STAGES = ("render", "process", "synchronization", "hud_review", "acceptance")
HUD_POLICY_RECEIPT_PROFILE = "cs2-batch-hud-setup-policy-v1"
PROJECT = Path(__file__).resolve().parents[2]
MAX_JOBS = 24
MAX_SOURCES = 8
MAX_SELECTIONS = 16
MAX_CLIP_TICKS = 1280
FULL_DEMO_PROFILE = "cs2-full-player-demo-v1"
MAX_FULL_DEMO_TICKS = 7680
PATH_FIELDS = ("parsed", "demo", "phase_manifest", "network_clock", "state_context")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode()


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _atomic(path, value):
    temporary = path.with_name("."+path.name+"."+uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(_bytes(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _within(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    _require(path.is_relative_to(root) and path != root, "Batch artifact path escapes its owned directory")
    return path


def _watch(files, path, expected=None):
    path = Path(path).resolve(); digest = sha256_file(path)
    _require(expected is None or digest == expected, "Batch source hash mismatch: "+str(path))
    _require(str(path) not in files or files[str(path)] == digest, "Conflicting batch source versions: "+str(path))
    files[str(path)] = digest
    return digest


def _verify_hashes(files):
    _require(isinstance(files, dict) and files, "Batch evidence requires nonempty file hashes")
    for filename, expected in files.items():
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) and
                 sha256_file(Path(filename)) == expected, "Batch artifact or source bytes changed: "+filename)


def _selection(value):
    _require(isinstance(value, dict) and not set(value)-{
        "start_demo_tick", "end_demo_tick", "round_id", "steam_id"}, "Unsupported batch selection fields")
    start, end = value.get("start_demo_tick"), value.get("end_demo_tick")
    _require((start is None) == (end is None), "Selection needs both start and exclusive end ticks")
    if start is not None:
        _require(type(start) is int and type(end) is int and 199 <= start < end <= 2147483500,
                 "Invalid bounded selection interval")
    for name in ("round_id", "steam_id"):
        if name in value:
            _require(type(value[name]) is int and value[name] > 0, "Selection identity must be a positive integer")
    return dict(value)


def _split_jobs(jobs, source_id, clip_ticks):
    """Keep complete chunks and rotate rounds/POVs before another chunk."""
    groups = defaultdict(lambda: defaultdict(deque))
    for original in jobs:
        _require(original.get("phase_evidence", {}).get("phase_verified") is True and
                 original.get("phase_evidence", {}).get("phase") == "competitive" and
                 original.get("command_coverage", {}).get("complete_within_gap_limit") is True,
                 "Batch candidates must retain verified phase and command coverage")
        start = max(199, original["start_demo_tick"])
        for begin in range(start, original["end_demo_tick"]-clip_ticks+1, clip_ticks):
            job = {**deepcopy(original), "start_demo_tick": begin, "end_demo_tick": begin+clip_ticks,
                   "competitive_replay_profile": CURRENT_PROFILE, "training_ready": False}
            # Full-window coverage is scheduling evidence, not acceptance of a
            # clipped interval's labels. Preserve its original scope explicitly.
            job["source_job_interval"] = [original["start_demo_tick"], original["end_demo_tick"]]
            job["source_job_command_coverage"] = job.pop("command_coverage")
            job["clip_id"] = _digest([PROFILE, source_id, job["demo_id"], job["round_id"],
                str(job["steam_id"]), job["player_slot"], begin, begin+clip_ticks, 32, 1280, 720])[:24]
            groups[job["round_id"]][(str(job["steam_id"]), job["player_slot"])].append(job)
    per_round = {}
    for round_id, players in sorted(groups.items()):
        queues = [players[key] for key in sorted(players)]
        interleaved = deque()
        while any(queues):
            for queue in queues:
                if queue:
                    interleaved.append(queue.popleft())
        per_round[round_id] = interleaved
    result = []
    while any(per_round.values()):
        for queue in per_round.values():
            if queue:
                result.append(queue.popleft())
    return result


def plan_batch(sources_manifest: Path, out: Path, *, clip_ticks=320, max_jobs=3):
    _require(type(clip_ticks) is int and 32 <= clip_ticks <= MAX_CLIP_TICKS and clip_ticks % 2 == 0,
             "Batch clips require an even 32..1280 demo ticks")
    _require(type(max_jobs) is int and 1 <= max_jobs <= MAX_JOBS, "Batch plan limit must be1..24 jobs")
    sources_manifest, out = Path(sources_manifest).resolve(), Path(out).resolve()
    _require(not out.exists(), "Batch planning requires a fresh output directory")
    raw = sources_manifest.read_bytes(); inputs = json.loads(raw.decode("utf-8-sig"))
    _require(isinstance(inputs, dict) and inputs.get("schema_version") == 1 and inputs.get("profile") == SOURCE_PROFILE,
             "Unsupported batch source manifest")
    entries = inputs.get("sources")
    _require(isinstance(entries, list) and 1 <= len(entries) <= MAX_SOURCES, "Batch needs1..8 sources")
    sources, files = [], {str(sources_manifest): hashlib.sha256(raw).hexdigest()}
    for entry in entries:
        _require(isinstance(entry, dict) and not set(entry)-{"source_id", "selections", *PATH_FIELDS},
                 "Unsupported source fields")
        source_id = entry.get("source_id")
        _require(isinstance(source_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", source_id) and
                 source_id not in {source["source_id"] for source in sources}, "Duplicate or invalid source_id")
        source = {"source_id": source_id}
        for key in PATH_FIELDS:
            value = entry.get(key)
            _require(isinstance(value, str) and value, "Missing batch source path: "+key)
            path = Path(value); path = path if path.is_absolute() else sources_manifest.parent/path
            source[key] = str(path.resolve())
        canonical = parsed_manifest(Path(source["parsed"]), ("rounds.parquet", "player_state.parquet", "usercmd.parquet"))
        _require(canonical.get("tick_rate") == 64, "Batch requires64 Hz source demos")
        source["demo_id"] = canonical["demo_id"]
        _watch(files, Path(source["parsed"])/"manifest.json")
        for filename in ("rounds.parquet", "player_state.parquet", "usercmd.parquet"):
            _watch(files, Path(source["parsed"])/filename, canonical["files"][filename])
        _watch(files, source["demo"], canonical["demo_id"])
        for key in ("phase_manifest", "network_clock", "state_context"):
            _watch(files, source[key])
        from .clock_evidence import load_network_clock
        from .validation import load_state_context
        clock = load_network_clock(Path(source["network_clock"]), canonical["demo_id"])
        load_state_context(Path(source["state_context"]), canonical)
        source["clock_windows"] = deepcopy(clock.document["windows"])
        selections = entry.get("selections", [{}])
        _require(isinstance(selections, list) and 1 <= len(selections) <= MAX_SELECTIONS, "Source needs1..16 selections")
        source["selections"] = [_selection(value) for value in selections]
        sources.append(source)
    # Keep incomplete planning products for diagnosis if candidate creation fails.
    out.mkdir(parents=True, exist_ok=False)
    queues, excluded_clock, unique = [], 0, set()
    for source in sources:
        candidates = []
        for index, selection in enumerate(source["selections"]):
            artifact = out/"candidates"/f"{source['source_id']}-{index:03d}.jsonl"
            render_jobs(parsed=Path(source["parsed"]), demo=Path(source["demo"]), out=artifact,
                fps=32, width=1280, height=720, min_ticks=clip_ticks,
                phase_manifest=Path(source["phase_manifest"]), **selection)
            files[str(artifact)] = sha256_file(artifact)
            candidates.extend(json.loads(line) for line in artifact.read_text().splitlines() if line)
        selected = []
        for job in _split_jobs(candidates, source["source_id"], clip_ticks):
            key = (job["demo_id"], str(job["steam_id"]), job["start_demo_tick"], job["end_demo_tick"])
            if key in unique:
                continue
            unique.add(key)
            if not any(window["start_demo_tick"] <= job["start_demo_tick"]-16 and
                       job["end_demo_tick"]+16 <= window["end_demo_tick"] for window in source["clock_windows"]):
                excluded_clock += 1; continue
            selected.append(job)
        queues.append((source, deque(selected)))
    chosen = []
    while len(chosen) < max_jobs and any(queue for _, queue in queues):
        for source, queue in queues:
            if queue and len(chosen) < max_jobs:
                job = queue.popleft(); job_path = out/"jobs"/(job["clip_id"]+".json")
                write_json(job_path, job); files[str(job_path)] = sha256_file(job_path)
                chosen.append({"job_id": job["clip_id"], "source_id": source["source_id"],
                               "spec": str(job_path), "spec_sha256": files[str(job_path)], "job": job})
    _require(chosen, "No bounded jobs have both eligible source windows and retained network-clock coverage")
    _verify_hashes(files)
    plan = {"schema_version": 1, "profile": PROFILE, "clip_ticks": clip_ticks, "max_jobs": max_jobs,
            "sources": sources, "jobs": chosen, "files": files, "excluded_clock_chunks": excluded_clock,
            "stage_order": list(STAGES), "training_ready": False,
            "limits": ["Planning is not source/POV/HUD/label acceptance.",
                       "Only full bounded chunks are selected; tails are left for later planning.",
                       "The approved capture setup does not require recurring visual HUD review."]}
    write_json(out/"batch_plan.json", plan)
    return plan


def load_batch_plan(path):
    path = Path(path).resolve(); path = path/"batch_plan.json" if path.is_dir() else path
    raw = path.read_bytes(); plan = json.loads(raw.decode("utf-8-sig"))
    _require(plan.get("schema_version") == 1 and plan.get("profile") == PROFILE and plan.get("stage_order") == list(STAGES),
             "Unsupported competitive batch plan")
    full_demo = plan.get("full_demo_profile") == FULL_DEMO_PROFILE
    _require("full_demo_profile" not in plan or full_demo, "Unsupported full-demo profile")
    clip_limit = MAX_FULL_DEMO_TICKS if full_demo else MAX_CLIP_TICKS
    _require(type(plan.get("clip_ticks")) is int and 32 <= plan["clip_ticks"] <= clip_limit and plan["clip_ticks"] % 2 == 0,
             "Invalid batch clip bound")
    _require(isinstance(plan.get("jobs"), list) and 1 <= len(plan["jobs"]) <= MAX_JOBS, "Invalid batch job count")
    _verify_hashes(plan["files"])
    source_ids = [s["source_id"] for s in plan["sources"]]
    _require(len(set(source_ids)) == len(source_ids), "Duplicate source IDs")
    ids, intervals = set(), defaultdict(list)
    for item in plan["jobs"]:
        job = item["job"]; job_id = item["job_id"]
        _require(isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{24}", job_id) and job_id not in ids and
                 job.get("clip_id") == job_id and item["source_id"] in source_ids, "Invalid planned job identity")
        ids.add(job_id)
        spec = _within(path.parent, item["spec"])
        _require(sha256_file(spec) == item["spec_sha256"] == plan["files"].get(str(spec)) and
                 _bytes(read_json(spec)) == _bytes(job), "Planned job differs from its retained spec")
        _require(job.get("competitive_replay_profile") == CURRENT_PROFILE and "calibration_replay_profile" not in job and
                 job.get("fps") == 32 and (job.get("width"), job.get("height")) == ((640, 360) if full_demo else (1280, 720)) and
                 (job.get("full_demo_profile") == FULL_DEMO_PROFILE if full_demo else "full_demo_profile" not in job) and
                 type(job.get("start_demo_tick")) is int and job["start_demo_tick"] >= 199 and
                 type(job.get("end_demo_tick")) is int and job["end_demo_tick"]-job["start_demo_tick"] == plan["clip_ticks"],
                 "Planned job bypasses bounded competitive rendering")
        source = next(s for s in plan["sources"] if s["source_id"] == item["source_id"])
        _require(job.get("demo_id") == source["demo_id"] and Path(job["demo_path"]).resolve() == Path(source["demo"]).resolve(),
                 "Planned job source mismatch")
        key = job["demo_id"], str(job["steam_id"])
        begin, end = job["start_demo_tick"], job["end_demo_tick"]
        _require(not any(begin < b and a < end for a, b in intervals[key]), "Overlapping source/POV clips in one batch")
        intervals[key].append((begin, end))
    return path, plan, hashlib.sha256(raw).hexdigest()


@contextmanager
def _lock(root):
    """Kernel-held lock releases after a crash; retained file is not a stale lock."""
    stream = (root/".batch.lock").open("a+b")
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0"); stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def _worker():
    name = "_cs2_competitive_batch_windows"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PROJECT/"tools/renderer/windows.py")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return sys.modules[name]


def _snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink(), "Stage evidence may not use symlinks")
        if path.is_file() and path.name not in (".cs2-data.lock",):
            _watch(result, _within(root, path))
    _require(result, "Stage produced no retained evidence")
    return result


def _render_manifest(out):
    paths = list(out.glob("*.render.json"))
    _require(len(paths) == 1, "Expected exactly one retained render manifest")
    return paths[0], read_json(paths[0])


def _verify_render(out, item):
    from .timing import archive_provenance, capture_boundaries, read_ledger, verify_readback_pixels
    path, render = _render_manifest(out); job = item["job"]
    _require(render.get("render_status") == "video_ready_timing_unverified" and
             _bytes(render.get("source_job")) == _bytes(job), "Render is incomplete or belongs to another planned job")
    for key in ("demo_id", "round_id", "steam_id", "player_slot", "fps", "width", "height"):
        _require(str(render.get(key)) == str(job[key]), "Render identity mismatch: "+key)
    _require(render.get("requested_start_demo_tick") == job["start_demo_tick"] and
             render.get("requested_end_demo_tick") == job["end_demo_tick"] and
             type(render.get("cs2_exit_code")) is int and render["cs2_exit_code"] == 0 and
             all(render.get(key) is True for key in ("settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")),
             "Protected render did not complete and restore its settings")
    _require(sha256_file(out/"input.dem") == job["demo_id"], "Captured input.demo bytes differ from the planned source")
    ledger = _within(out, out/render["capture_ledger"])
    _require(sha256_file(ledger) == render["capture_ledger_sha256"], "Render ledger hash mismatch")
    records = read_ledger(ledger); capture_boundaries(records, render)
    _require(header_matches_profile(records[0], native_profile=CURRENT_PROFILE), "Wrong native competitive replay profile")
    archive = archive_provenance(render, path)
    inventory = []
    for index, frame in enumerate(archive["frames"]):
        image = _within(out/"frames", out/"frames"/frame["archived_name"])
        _require(frame["capture_index"] == index and sha256_file(image) == frame["sha256"], "Archived image bytes/index mismatch")
        inventory.append({"path": str(image), "sha256": frame["sha256"]})
    _require(verify_readback_pixels(records, inventory)["verified"] is True, "Native pixel correspondence missing")
    worker = _worker()
    from .session_evidence import lifecycle_root
    run = lifecycle_root(render, records, out)
    isolation = worker.verify_settings_isolation(run, expected_pid=render["owned_cs2_pid"])
    _require(_bytes(isolation) == _bytes(render["settings_isolation"]), "Settings isolation manifest mismatch")
    from .competitive_replay_proof import _protected_archive
    watched = {}; _protected_archive(render, run, lambda p, expected=None: _watch(watched, p, expected))
    _verify_hashes(watched)
    return {"status": "verified_render_artifacts", "render_manifest": str(path), "clip_id": render["clip_id"],
            "frame_count": render["num_frames"], "training_ready": False}


def _hud_policy_receipt(render_dir):
    from .hud_policy import policy_allows_capture, trusted_hud_policy
    path, render = _render_manifest(render_dir)
    policy = trusted_hud_policy(render)
    _require(policy_allows_capture(policy), "Capture setup is outside the approved HUD policy")
    return {"schema_version": 1, "profile": HUD_POLICY_RECEIPT_PROFILE,
            "render_manifest": str(path.resolve()), "render_manifest_sha256": sha256_file(path),
            "capture_ledger_sha256": render["capture_ledger_sha256"],
            "capture_frame_files_sha256": render["capture_frame_files_sha256"],
            "clip_id": render["clip_id"], "policy": policy, "training_ready": False}


def _verify_stage(stage, out, source, item, parents):
    if stage == "render":
        return _verify_render(out, item)
    dataset = parents.get("process")
    if stage == "process":
        from .timing import read_ledger, verify_capture_evidence
        report = read_json(out/"pipeline_manifest.json")
        render_path, render = _render_manifest(parents["render"])
        _require(report.get("status") == "complete" and report.get("demo_id") == source["demo_id"] and
                 report.get("source_render_sha256") == sha256_file(render_path), "Incomplete or mismatched ingestion")
        clip = read_json(out/"timing/clip.json"); frames = read_ledger(out/"timing/frames.jsonl")
        _require(clip.get("clip_id") == render["clip_id"] and clip.get("demo_id") == source["demo_id"], "Ingestion identity mismatch")
        verify_capture_evidence(clip, frames)
        alignment = read_json(out/"aligned/alignment_manifest.json")
        for name, digest in alignment["files"].items():
            _require(sha256_file(_within(out/"aligned", out/"aligned"/name)) == digest, "Alignment file changed")
        for name in ("calibration/execution_calibration.json", "viewer/inspect.html"):
            _require((out/name).is_file(), "Incomplete ingestion output: "+name)
        return {"status": "verified_ingestion_artifacts", "frame_count": len(frames), "training_ready": False}
    if stage == "synchronization":
        from .synchronization import load_synchronization
        report = load_synchronization(out/"synchronization_audit.json")
        _require(report.get("native_profile") == CURRENT_PROFILE and report.get("demo_id") == source["demo_id"] and
                 Path(report["inputs"]["dataset"]).resolve() == dataset.resolve(), "Synchronization source mismatch")
        return {"status": "verified_synchronization_artifacts", "frame_count": report["num_frames"],
                "native_clock_status": report["native_message_clock_audit"]["status"], "training_ready": False}
    if stage == "hud_review":
        receipt = _hud_policy_receipt(parents["render"])
        policy_path = out/"hud_policy.json"
        result = {"status": "hud_setup_trusted", "hud_policy": receipt["policy"],
                  "visual_review_performed": False, "training_ready": False}
        if policy_path.is_file():
            _require(read_json(policy_path) == receipt, "HUD policy receipt belongs to another capture or policy")
            result["hud_policy_receipt"] = str(policy_path)
        else:
            # Old batches already have optional review material. Check its
            # capture binding only; it no longer supplies the HUD decision.
            bundle_path = out/"hud_review_bundle.json"
            bundle = read_json(bundle_path)
            _require(Path(bundle["render_dir"]).resolve() == parents["render"].resolve() and
                     all(bundle.get(key) == receipt[key] for key in
                         ("clip_id", "capture_ledger_sha256", "capture_frame_files_sha256")),
                     "Historical HUD bundle belongs to another capture")
            result["bundle"] = str(bundle_path)
        return result
    from .competitive_control import load_competitive_acceptance
    report = load_competitive_acceptance(out)
    _require(report["demo_id"] == source["demo_id"] and Path(report["inputs"]["dataset"]).resolve() == dataset.resolve(),
             "Acceptance belongs to another job/source")
    pending = report["accepted_count"] == 0 and report["reason_counts"].get("competitive_hud_visual_review_unavailable", 0) > 0
    return {"status": "pending_visual_review" if pending else "accepted_partition_verified",
            "accepted_count": report["accepted_count"], "rejected_count": report["rejected_count"],
            "reason_counts": report["reason_counts"], "acceptance": str(out/"competitive_acceptance.json"),
            "training_ready": report["accepted_count"] > 0}


def _perform_stage(stage, out, source, item, parents, options):
    if stage == "render":
        worker = _worker()
        _require(item["job"].get("competitive_replay_profile") == CURRENT_PROFILE and
                 "calibration_replay_profile" not in item["job"], "Renderer requires the explicit current competitive profile")
        # The standalone worker's default patch is historical. This fixed
        # selection still enforces its current eight-DLL hashes before launch.
        capture_ticks = item["job"]["end_demo_tick"] - item["job"]["start_demo_tick"]
        clip_limit = MAX_FULL_DEMO_TICKS if item["job"].get("full_demo_profile") == FULL_DEMO_PROFILE else MAX_CLIP_TICKS
        _require(type(capture_ticks) is int and 32 <= capture_ticks <= clip_limit and capture_ticks % 2 == 0,
                 "Protected batch capture exceeds its bounded tick interval")
        arguments = ["--spec", item["spec"], "--output", str(out), "--execute", "--max-ticks", str(capture_ticks), "--allow-version-mismatch"]
        if item["job"].get("full_demo_profile") == FULL_DEMO_PROFILE:
            arguments += ["--timeout", str(max(600, capture_ticks / 64 * 10 + 300))]
        for name in ("game_dir", "plugin", "ffmpeg", "ffprobe", "steam_dir", "steam_user_id"):
            if options.get(name) is not None:
                arguments.extend(("--"+name.replace("_", "-"), str(options[name])))
        args = worker.argument_parser().parse_args(arguments)
        effective = worker.validate_job(item["job"], max_ticks=capture_ticks)
        worker.run_capture(args, effective, item["job"])
    elif stage == "process":
        from .pipeline import process_render
        process_render(Path(source["parsed"]), parents["render"], out)
    elif stage == "synchronization":
        from .synchronization import audit_synchronization
        audit_synchronization(Path(source["parsed"]), parents["process"], Path(source["network_clock"]), out,
                              native_profile=CURRENT_PROFILE)
    elif stage == "hud_review":
        receipt = _hud_policy_receipt(parents["render"])
        out.mkdir(parents=True, exist_ok=False)
        with (out/"hud_policy.json").open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, allow_nan=False)
            handle.write("\n")
    else:
        from .competitive_control import accept_competitive_controls
        accept_competitive_controls(Path(source["parsed"]), parents["process"], Path(source["network_clock"]),
                                    Path(source["state_context"]), out)


def _retry_is_safe(stage, out):
    if stage != "render" or not out.exists():
        return
    _worker().require_cs2_idle()
    for name in ("gameinfo-recovery.json", "settings-recovery.json"):
        path = out/name
        if path.is_file():
            _require(read_json(path).get("state") in ("restored", "cancelled_before_launch"),
                     "Recovery required before another capture: "+str(path))
    manifests = list(out.glob("*.render.json"))
    for path in manifests:
        report = read_json(path)
        if report.get("staged_plugin_removed_from_game") is not True:
            # Explicit worker recovery updates journals and archives the mod; it
            # intentionally preserves the failed render manifest unchanged.
            journal_path = out/"gameinfo-recovery.json"
            journal = read_json(journal_path) if journal_path.is_file() else {}
            mod = Path(journal.get("game_dir", ""))/"csgo"/journal.get("mod_name", "")
            _require(journal.get("state") == "restored" and journal.get("run_id") == report.get("run_id") and
                     re.fullmatch(r"chicken-render-[0-9a-f]{32}", journal.get("mod_name", "")) and not mod.exists(),
                     "Owned renderer cleanup must be completed before retry: "+str(path))


def _load_state(root, plan, digest):
    path = root/"batch_state.json"
    if not path.exists():
        return {"schema_version": 1, "profile": PROFILE, "plan_sha256": digest,
                "jobs": {job["job_id"]: {"stages": {name: [] for name in STAGES}} for job in plan["jobs"]}}
    state = read_json(path)
    _require(state.get("schema_version") == 1 and state.get("profile") == PROFILE and state.get("plan_sha256") == digest and
             set(state.get("jobs", {})) == {job["job_id"] for job in plan["jobs"]}, "Batch journal does not bind this exact plan")
    for job_id, job in state["jobs"].items():
        _require(set(job["stages"]) == set(STAGES), "Batch journal stage set changed")
        for stage, attempts in job["stages"].items():
            _require(isinstance(attempts, list), "Invalid stage attempt history")
            for index, attempt in enumerate(attempts, 1):
                expected = root/"runs"/job_id/stage/f"attempt-{index:03d}"
                _require(attempt.get("attempt") == index and _within(root, attempt["out"]) == expected.resolve() and
                         attempt.get("status") in ("running", "completed", "failed", "interrupted", "superseded"),
                         "Invalid stage attempt path/order/status")
    return state


def _run(root, plan, digest, *, execute, max_jobs, retry_failed, options):
    state = _load_state(root, plan, digest); state_path = root/"batch_state.json"
    measurements = {stage: {"perform_seconds": 0.0, "verify_seconds": 0.0,
                            "perform_calls": 0, "verify_calls": 0} for stage in STAGES}

    def measured(operation, stage, *args):
        if options.get("progress"):
            options["progress"](stage, operation)
        before = time.perf_counter()
        try:
            return (_perform_stage if operation == "perform" else _verify_stage)(stage, *args)
        finally:
            measurements[stage][operation+"_seconds"] += time.perf_counter()-before
            measurements[stage][operation+"_calls"] += 1
    if execute and options["mode"] != "process":
        for stored in state["jobs"].values():
            # Check every prior renderer, including a later job in plan order,
            # before allowing any new launch in this invocation.
            for attempt in stored["stages"]["render"]:
                _retry_is_safe("render", Path(attempt["out"]))
    statuses, budget = [], max_jobs
    for item in plan["jobs"]:
        source = next(s for s in plan["sources"] if s["source_id"] == item["source_id"])
        stored = state["jobs"][item["job_id"]]; parents = {}; outcome = {"job_id": item["job_id"], "source_id": source["source_id"]}
        operated = False
        for stage in (STAGES[:1] if options["mode"] == "record" else STAGES):
            attempts = stored["stages"][stage]; latest = attempts[-1] if attempts else None
            # Background processing consumes a completed, immutable capture. It
            # must never recover an in-progress renderer or launch another game.
            if stage == "render" and options["mode"] == "process" and (not latest or latest["status"] != "completed"):
                outcome.update(status="capture_required", stage=stage); break
            if latest and latest["status"] in ("completed", "running"):
                try:
                    if latest.get("files"):
                        _verify_hashes(latest["files"])
                except (OSError, ValueError) as error:
                    outcome.update(status="artifact_changed", stage=stage, error=str(error)); break
                try:
                    result = measured("verify", stage, Path(latest["out"]), source, item, parents)
                except (OSError, ValueError, RuntimeError, KeyError) as error:
                    if latest["status"] == "completed" and stage not in ("synchronization", "acceptance"):
                        outcome.update(status="evidence_invalid", stage=stage, error=str(error)); break
                    if not execute:
                        outcome.update(status="revalidation_required", stage=stage, error=str(error)); break
                    latest["status"] = "superseded" if latest["status"] == "completed" else "interrupted"
                    latest["revalidation_error"] = str(error); _atomic(state_path, state)
                else:
                    parents[stage] = Path(latest["out"])
                    if execute and latest["status"] == "running":
                        latest.update(status="completed", result=result, files=_snapshot(parents[stage]), recovered_after_interruption=True)
                        _atomic(state_path, state)
                    outcome.update(status=result["status"], stage=stage, **{k: v for k, v in result.items() if k != "status"})
                    if stage == "hud_review" and result["status"] == "pending_visual_review":
                        break
                    continue
            if latest and latest["status"] in ("failed", "interrupted") and not retry_failed:
                outcome.update(status="retry_required", stage=stage, error=latest.get("error", latest.get("revalidation_error"))); break
            if not execute or (budget <= 0 and not operated):
                outcome.update(status="planned" if not execute else "execution_budget_reached", stage=stage); break
            if not operated:
                operated = True; budget -= 1
            if latest:
                try:
                    _retry_is_safe(stage, Path(latest["out"]))
                except (OSError, ValueError, RuntimeError) as error:
                    outcome.update(status="recovery_required", stage=stage, error=str(error)); break
            attempt_number = len(attempts)+1
            out = root/"runs"/item["job_id"]/stage/f"attempt-{attempt_number:03d}"
            _require(not out.exists(), "Unjournaled stage output exists; preserve and investigate: "+str(out))
            out.parent.mkdir(parents=True, exist_ok=True)
            attempt = {"attempt": attempt_number, "out": str(out), "status": "running", "started_at_unix": time.time()}
            attempts.append(attempt); _atomic(state_path, state)
            try:
                measured("perform", stage, out, source, item, parents, options)
                result = measured("verify", stage, out, source, item, parents)
                attempt.update(status="completed", result=result, files=_snapshot(out), finished_at_unix=time.time())
                parents[stage] = out
                outcome.update(stage=stage, **result)
            except Exception as error:
                attempt.update(status="failed", error=str(error), finished_at_unix=time.time())
                outcome.update(status="failed", stage=stage, error=str(error))
                _atomic(state_path, state); break
            _atomic(state_path, state)
            if stage == "hud_review" and result["status"] == "pending_visual_review":
                break
        statuses.append(outcome)
        # A failed/uncertain renderer can require recovery even when its process
        # exited. Do not start another job until that lifecycle is resolved.
        if outcome.get("stage") == "render" and outcome.get("status") in (
                "failed", "retry_required", "recovery_required", "evidence_invalid", "artifact_changed", "revalidation_required"):
            budget = 0
    summary = {"schema_version": 1, "profile": PROFILE, "plan_sha256": digest, "execute": execute,
               "mode": options["mode"], "jobs": statuses, "job_status_counts": dict(Counter(row["status"] for row in statuses)),
               "accepted_sample_count": sum(row.get("accepted_count", 0) for row in statuses),
               "rejected_sample_count": sum(row.get("rejected_count", 0) for row in statuses),
               "performance": {"scope": "diagnostic_this_invocation_not_acceptance_evidence",
                   "planned_source_seconds": len(plan["jobs"])*plan["clip_ticks"]/64,
                   "planned_capture_launches": len(plan["jobs"]),
                   "equivalent_five_second_launches": len(plan["jobs"])*((plan["clip_ticks"]+319)//320),
                   "stage_measurements": measurements},
               "model_training_performed": False}
    if execute:
        _atomic(root/"batch_summary.json", summary)
    return summary


def run_batch(plan_dir: Path, *, execute=False, max_jobs=3, retry_failed=False,
              game_dir=None, plugin=None, ffmpeg=None, ffprobe=None, steam_dir=None, steam_user_id=None, progress=None,
              mode="all"):
    _require(type(execute) is bool and type(retry_failed) is bool and type(max_jobs) is int and 1 <= max_jobs <= MAX_JOBS,
             "Invalid bounded batch execution options")
    path, plan, digest = load_batch_plan(plan_dir); root = path.parent
    _require(progress is None or callable(progress), "Batch progress callback must be callable")
    _require(mode in ("all", "record", "process"), "Unknown batch execution mode")
    options = dict(game_dir=game_dir, plugin=plugin, ffmpeg=ffmpeg, ffprobe=ffprobe, steam_dir=steam_dir, steam_user_id=steam_user_id, progress=progress, mode=mode)
    previous_path = os.environ.get("PATH", "")
    try:
        if ffmpeg:
            os.environ["PATH"] = str(Path(ffmpeg).resolve().parent)+os.pathsep+previous_path
        if execute:
            with _lock(root):
                return _run(root, plan, digest, execute=True, max_jobs=max_jobs, retry_failed=retry_failed, options=options)
        return _run(root, plan, digest, execute=False, max_jobs=max_jobs, retry_failed=False, options=options)
    finally:
        os.environ["PATH"] = previous_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Prepare bounded source-bound jobs; never launch the game")
    plan.add_argument("--sources", type=Path, required=True); plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--clip-ticks", type=int, default=320); plan.add_argument("--max-jobs", type=int, default=3)
    run = sub.add_parser("run", help="Inspect/resume; read-only unless --execute")
    run.add_argument("--plan", type=Path, required=True); run.add_argument("--execute", action="store_true")
    run.add_argument("--retry-failed", action="store_true"); run.add_argument("--max-jobs", type=int, default=3)
    for name in ("game-dir", "plugin", "ffmpeg", "ffprobe", "steam-dir"):
        run.add_argument("--"+name, type=Path)
    run.add_argument("--steam-user-id")
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = plan_batch(args.sources, args.out, clip_ticks=args.clip_ticks, max_jobs=args.max_jobs)
        print(json.dumps({"plan": str(args.out/"batch_plan.json"), "job_count": len(result["jobs"])}))
    else:
        values = vars(args); values.pop("command"); values["plan_dir"] = values.pop("plan")
        print(json.dumps(run_batch(**values), indent=2))


if __name__ == "__main__":
    main()
