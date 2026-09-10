"""Post-capture indexing, shared archival, and logical validation jobs."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
import time
import uuid
import zipfile

from . import competitive_batch as batch
from .io import read_json, sha256_file
from .launcher_backend import save_settings
from .session_evidence import index_session, make_view, events, verify_index
from .session_profile import PROFILE, require
from .training_archive import package_artifacts, retention_mode


def index_files(index):
    require(index.get("profile") == PROFILE, "Unsupported shared index binding")
    root = Path(index["root"]).resolve()
    result = {}
    for name in ("database", "ledger"):
        path = Path(index[name]).resolve()
        require(path.is_relative_to(root), "Shared evidence binding escapes its session")
        result[str(path)] = index[name+"_sha256"]
    return result


def retained_work_index(work):
    state_path = Path(work)/"batch/batch_state.json"
    if not state_path.exists():
        return None
    state = read_json(state_path)
    for job in state["jobs"].values():
        attempts = job["stages"]["render"]
        if not attempts:
            continue
        out = Path(attempts[-1]["out"])
        require(out.resolve().is_relative_to(Path(work).resolve()), "Render journal escapes its workspace")
        views = list(out.glob("*.view.json"))
        if len(views) == 1:
            view = read_json(views[0])
            require(sha256_file(Path(view["session_index"])) == view["session_index_sha256"], "Shared view index changed")
            return read_json(Path(view["session_index"]))
    return None


def shared_data_files(root):
    """Data locks last until every validator exits; cleanup runs afterward."""
    path = root/"session-state.json"
    if not path.exists():
        return {}
    files = {}
    for attempt in read_json(path)["attempts"]:
        if attempt["status"] != "captured":
            continue
        out = Path(attempt["out"]).resolve()
        require(out.parent == (root/"sessions").resolve(), "Shared session journal path escaped")
        index_path = out/"session-index.json"
        if index_path.exists():
            files.update(index_files(read_json(index_path)))
        receipt_path = root/"session-packages"/out.name/"receipt.json"
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            if receipt.get("evidence_retention", "full") == "full":
                archive_path = Path(receipt["archive"]["path"]).resolve()
                require(archive_path.is_relative_to((root/"session-packages").resolve()), "Shared archive escaped its queue")
                files[str(archive_path)] = receipt["archive"]["sha256"]
    return files


def collect_frames(index, *, emit=None):
    root = Path(index["root"])
    destination = root/"session-frames.json"
    if destination.exists():
        return read_json(destination)
    render = read_json(Path(index["render_manifest"]))
    schedule = read_json(Path(index["schedule"]))
    worker = batch._worker()
    inventory = {}
    last_notice = time.monotonic()
    for run in schedule["runs"]:
        if run["id"] not in index["closed_runs"]:
            continue
        target = root/"raw"/run["prefix"]
        target.mkdir(parents=True, exist_ok=True)
        roots = [Path(p) for p in render["recording_roots"]]+[target]
        files = sorted(worker.capture_files(roots, run["prefix"]), key=lambda p: p.name)
        require(run["min_frames"] <= len(files) <= run["max_frames"], "Physical recording image count differs")
        for n, source in enumerate(files):
            require(source.name == f"{run['prefix']}_{n:08d}.tga", "Physical recording filenames are discontinuous")
            worker.inspect_tga(source, 640, 360)
            # The archived private mod is already owned by the session. Only
            # files in the shared game fallback need a physical move.
            output = source if source.resolve().is_relative_to(root) else target/source.name
            if source.resolve() != output.resolve():
                require(not output.exists() and output.resolve().is_relative_to(root), "Occupied session image destination")
                source.rename(output)
            inventory[source.name] = {"path": str(output), "sha256": sha256_file(output), "bytes": output.stat().st_size}
            if emit and time.monotonic()-last_notice > 15:
                emit(f"Checking native frame inventory: {len(inventory):,} frames")
                last_notice = time.monotonic()
    save_settings(destination, inventory)
    return inventory


def verify_archive(receipt):
    mode = retention_mode(receipt.get("evidence_retention", "full"))
    require(receipt.get("profile") == PROFILE, "Unsupported shared session archive")
    if mode == "full":
        require(receipt.get("status") == "compressed", "Unsupported shared session archive")
        item = receipt["archive"]
        require(sha256_file(Path(item["path"])) == item["sha256"], "Shared recording archive changed")
    else:
        require(receipt.get("schema_version") == 2 and receipt.get("status") == "indexed" and
                "archive" not in receipt and receipt["session_index"]["root"] == receipt["original_root"],
                "Unsupported lean session receipt")
    return receipt


def live_session_frames(receipt):
    """Verify temporary inputs while packaging; completed lean receipts stand alone."""
    index = receipt["session_index"]
    root = Path(receipt["original_root"])
    require(sha256_file(root/"session-index.json") == receipt["session_index_sha256"] and
            read_json(root/"session-index.json") == index, "Shared session index changed")
    verify_index(index)
    require(read_json(Path(index["schedule"])) == receipt["session_plan"], "Shared session schedule changed")
    require(sha256_file(root/"session-frames.json") == receipt["session_frames_sha256"], "Shared frame inventory changed")
    result = {}
    for frame in read_json(root/"session-frames.json").values():
        path = Path(frame["path"])
        require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), "Shared frame escaped its session")
        result[path.relative_to(root).as_posix()] = frame
    return result


def pack_session(index, destination, *, emit=None, evidence_retention="lean"):
    """Publish a compact session receipt, or a complete archive in debug mode."""
    mode = retention_mode(evidence_retention)
    destination = Path(destination)
    receipt_path = destination/"receipt.json"
    if receipt_path.exists():
        receipt = verify_archive(read_json(receipt_path))
        require(receipt.get("evidence_retention", "full") == mode, "Saved session retention differs from its plan")
        require(receipt["session_index_sha256"] == sha256_file(Path(index["root"])/"session-index.json"), "Archive belongs to another session index")
        if mode == "lean":
            live_session_frames(receipt)
        return receipt_path
    verify_index(index)
    collect_frames(index, emit=emit)
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(index["root"])
    if mode == "lean":
        render = read_json(Path(index["render_manifest"]))
        digest = sha256_file(root/"input.dem")
        require(digest == render["demo_id"], "Shared session demo bytes changed")
        source = render["source_job"].get("demo_path", render["source_job"].get("demo_uri"))
        require(isinstance(source, str) and sha256_file(Path(source)) == digest, "Original demo changed")
        save_settings(receipt_path, {"schema_version": 2, "profile": PROFILE, "status": "indexed",
            "evidence_retention": mode, "original_root": str(root), "session_index": index,
            "session_plan": read_json(Path(index["schedule"])),
            "session_index_sha256": sha256_file(root/"session-index.json"),
            "session_frames_sha256": sha256_file(root/"session-frames.json"),
            "shared_sources": {"input.dem": {"path": source, "sha256": digest}}, "compressed_bytes": 0})
        return receipt_path
    archive_path = destination/("evidence-"+uuid.uuid4().hex[:12]+".zip")
    storage = read_json(Path(index["schedule"])).get("storage", {})
    members, shared = {}, {}
    archived_bytes, last_notice = 0, time.monotonic()
    last_guard = time.monotonic()
    # Representative native frames compressed 2.3x faster at level 1 for ~3%
    # more space. Pixels stay lossless; archive time should not dominate capture.
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for path in sorted(root.rglob("*")):
            require(not path.is_symlink() and path.resolve().is_relative_to(root), "Unsafe session evidence path")
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if path.name == "input.dem":
                render = read_json(Path(index["render_manifest"]))
                digest = sha256_file(path)
                require(digest == render["demo_id"], "Shared session demo bytes changed")
                shared[name] = {"path": render["source_job"]["demo_path"] if "demo_path" in render["source_job"] else render["source_job"]["demo_uri"], "sha256": digest}
                continue
            digest = hashlib.sha256()
            before = path.stat()
            with path.open("rb") as stream, archive.open(name, "w", force_zip64=True) as output:
                for block in iter(lambda: stream.read(1024*1024), b""):
                    digest.update(block); output.write(block)
                    archived_bytes += len(block)
                    if storage and time.monotonic()-last_guard > 1:
                        require(shutil.disk_usage(destination).free >= storage["reserve_bytes"], "Not enough space to finish the shared evidence archive")
                        require(archive_path.stat().st_size <= storage.get("max_shared_archive_bytes", float("inf")), "Shared archive exceeded its storage bound")
                        last_guard = time.monotonic()
                    if emit and time.monotonic()-last_notice > 15:
                        emit(f"Compressing shared recording evidence: {archived_bytes/1e9:.2f} GB processed")
                        last_notice = time.monotonic()
            after = path.stat()
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "Session changed during compression")
            members[name] = {"sha256": digest.hexdigest(), "bytes": before.st_size}
        archive.writestr("archive_index.json", json.dumps({"original_root": str(root), "members": members, "shared_sources": shared}))
    with zipfile.ZipFile(archive_path) as archive:
        if emit:
            emit("Checking the shared archive compression round trip")
        require(archive.testzip() is None, "Shared session archive failed its compression round trip")
    save_settings(receipt_path, {"profile": PROFILE, "status": "compressed", "evidence_retention": mode, "original_root": str(root),
        "session_index_sha256": sha256_file(root/"session-index.json"),
        "archive": {"path": str(archive_path.resolve()), "sha256": sha256_file(archive_path)},
        "shared_sources": shared, "compressed_bytes": archive_path.stat().st_size})
    return receipt_path


def materialize_segment(root, plan, segment, index, tools, receipt_path):
    from .full_demo import prepare_segment
    from .demo_pipeline import captured_work
    existing = captured_work(root, segment["id"])
    if existing is not None:
        return existing
    work = prepare_segment(root, plan, segment)
    batch_root = work/"batch"
    batch_plan = read_json(batch_root/"batch_plan.json")
    state = batch._load_state(batch_root, batch_plan, sha256_file(batch_root/"batch_plan.json"))
    item = batch_plan["jobs"][0]
    attempts = state["jobs"][segment["id"]]["stages"]["render"]
    if attempts and attempts[-1]["status"] == "running":
        attempts[-1]["status"] = "interrupted"
    number = len(attempts)+1
    out = batch_root/"runs"/segment["id"]/"render"/f"attempt-{number:03d}"
    out.mkdir(parents=True, exist_ok=False)
    attempt = {"attempt": number, "out": str(out), "status": "running", "started_at_unix": time.time()}
    attempts.append(attempt); save_settings(batch_root/"batch_state.json", state)
    try:
        shared = read_json(Path(index["render_manifest"]))
        schedule = read_json(Path(index["schedule"]))
        run = next(r for r in schedule["runs"] if segment["id"] in r["segments"])
        records = make_view(index, run, segment, out/"capture.view.json")
        worker = batch._worker()
        job = worker.validate_job(item["job"], max_ticks=7680)
        render = dict(shared)
        render.update({k: job[k] for k in ("clip_id", "round_id", "player_slot", "renderer_profile_sha256")})
        render.update(source_job=item["job"], requested_start_demo_tick=segment["start_demo_tick"],
            requested_end_demo_tick=segment["end_demo_tick"], capture_prefix=run["prefix"],
            capture_ledger="capture.view.json", capture_ledger_sha256=sha256_file(out/"capture.view.json"))
        inventory = read_json(Path(index["root"])/"session-frames.json")
        provenance, references = [], {}
        (out/"frames").mkdir()
        for n, frame in enumerate(events(records, ("movie_frame",))):
            entry = inventory[frame["tga_filename"]]
            source = Path(entry["path"])
            require(source.resolve().is_relative_to(Path(index["root"]).resolve()) and not source.is_symlink() and
                sha256_file(source) == entry["sha256"], "Shared native frame changed or escaped its session")
            name = f"{job['clip_id']}_{n:08d}.tga"
            output = out/"frames"/name
            os.link(source, output)
            provenance.append({"capture_index": n, "source_name": source.name, "archived_name": name, "sha256": entry["sha256"]})
            references[str(output.relative_to(work).as_posix())] = {"member": source.relative_to(Path(index["root"])).as_posix(), "sha256": entry["sha256"]}
        os.link(Path(index["root"])/"input.dem", out/"input.dem")
        render["nominal_num_frames"] = len(provenance)
        save_settings(out/"capture_frame_files.json", {"schema_version": 1, "capture_prefix": run["prefix"],
            "archived_prefix": job["clip_id"], "frames": provenance})
        render.update(capture_frame_files="capture_frame_files.json", capture_frame_files_sha256=sha256_file(out/"capture_frame_files.json"))
        render.update(worker.encode_video(Path(tools["ffmpeg"]), Path(tools["ffprobe"]), out, job, len(provenance), "libx264"))
        render["render_status"] = "video_ready_timing_unverified"
        save_settings(out/(job["clip_id"]+".render.json"), render)
        save_settings(work/"shared-session.json", {"receipt": str(receipt_path), "receipt_sha256": sha256_file(receipt_path), "frames": references})
        result = batch._verify_render(out, item)
        attempt.update(status="completed", result=result, files=batch._snapshot(out), finished_at_unix=time.time())
    except Exception as error:
        attempt.update(status="failed", error=str(error), finished_at_unix=time.time())
        raise
    finally:
        save_settings(batch_root/"batch_state.json", state)
    return work


def release_completed_sessions(root, progress):
    """Release only session-owned files with durable archives and packaged shards."""
    state_path = root/"session-state.json"
    if not state_path.exists():
        return
    state = read_json(state_path)
    for attempt in state["attempts"]:
        if attempt["status"] != "captured":
            continue
        out = Path(attempt["out"]).resolve()
        require(out.parent == (root/"sessions").resolve(), "Unsafe shared session cleanup root")
        receipt_path = root/"session-packages"/out.name/"receipt.json"
        if not receipt_path.exists():
            continue
        receipt = verify_archive(read_json(receipt_path))
        require(receipt["original_root"] == str(out), "Shared archive belongs to another session")
        mode = receipt.get("evidence_retention", "full")
        if mode == "full":
            with zipfile.ZipFile(receipt["archive"]["path"]) as archive:
                index = json.loads(archive.read("session-index.json"))
                schedule = json.loads(archive.read("session-plan.json"))
        else:
            index, schedule = receipt["session_index"], receipt["session_plan"]
        keys = [key for run in schedule["runs"] if run["id"] in index["closed_runs"] for key in run["segments"]]
        require(keys, "Session has no dependent training segments")
        if not all(progress["segments"].get(key, {}).get("status") == "complete" for key in keys):
            continue
        for key in keys:
            record = progress["segments"][key]
            require(Path(record["receipt"]).resolve().is_relative_to((root/"packages").resolve()),
                    "Segment receipt escaped its queue")
            require(sha256_file(Path(record["receipt"])) == record["receipt_sha256"], "Segment receipt changed before session cleanup")
            package = read_json(Path(record["receipt"]))
            require(package.get("status") == "complete" and package.get("segment_id") == key and
                    Path(package["shared_session"]["receipt"]).resolve() == receipt_path.resolve() and
                    package["shared_session"]["receipt_sha256"] == sha256_file(receipt_path), "Segment has another session archive")
            require(package.get("evidence_retention", "full") == mode, "Segment and session retention differ")
            for name in package_artifacts(package):
                artifact = Path(package[name]["path"]).resolve()
                require(artifact.is_relative_to((root/"packages").resolve()) and not artifact.is_relative_to(out) and
                        sha256_file(artifact) == package[name]["sha256"], "Segment archive changed before shared cleanup")
        for source in receipt["shared_sources"].values():
            require(sha256_file(Path(source["path"])) == source["sha256"], "Original demo changed before shared cleanup")
        if out.exists():
            paths = sorted(out.rglob("*"), key=lambda p: len(p.parts), reverse=True)
            require(all(not p.is_symlink() and p.resolve().is_relative_to(out) for p in paths), "Unsafe shared session cleanup member")
            for path in paths:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            out.rmdir()
        attempt["status"] = "released"
        save_settings(state_path, state)
