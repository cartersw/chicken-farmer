"""Lossless RGB training shards and recoverable compressed processing evidence."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import struct
import zipfile

from .io import read_json, sha256_file
from .launcher_backend import save_settings
from .timing import tga_rgb24

PROFILE = "cs2-rgb8-training-shard-v1"
FORMAT = {"width": 640, "height": 360, "channels": 3, "color": "RGB",
          "bits_per_channel": 8, "fps": 32, "history_frames": 8,
          "storage": "zip-deflate-rgb24", "frame_bytes": 640 * 360 * 3}


def require(value, message):
    if not value:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode()


def sample_key(sample):
    supports = sample["command_support"]
    require(len(supports) == 3, "Expected a predecessor and two target commands")
    return (sample["demo_id"], str(sample["steam_id"]),
            supports[1]["command_row_id"], supports[2]["command_row_id"])


def _owned(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    require(path != root and path.is_relative_to(root), "Archive path escapes its owned workspace")
    return path


def pack_segment(work, destination, segment_id, *, seen=None):
    """Pack an already verified one-job batch, keeping complete evidence recoverable.

    No deletion here. A separately verified receipt authorizes releasing the new
    working directory. Existing captures outside this directory are never touched.
    """
    work, destination = Path(work).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen = set() if seen is None else seen
    state = read_json(work/"batch/batch_state.json")
    plan = read_json(work/"batch/batch_plan.json")
    require(len(plan["jobs"]) == 1 and state["plan_sha256"] == sha256_file(work/"batch/batch_plan.json"),
            "Packaging requires this completed one-segment batch")
    item = plan["jobs"][0]
    stages = state["jobs"][item["job_id"]]["stages"]
    require(all(stages[s] and stages[s][-1]["status"] == "completed" for s in plan["stage_order"]),
            "Cannot package incomplete processing")
    acceptance_stage = stages["acceptance"][-1]
    require(acceptance_stage["result"]["status"] == "accepted_partition_verified",
            "Numerical acceptance has not completed")
    acceptance_dir = _owned(work, acceptance_stage["out"])
    acceptance = read_json(acceptance_dir/"competitive_acceptance.json")
    source = plan["sources"][0]
    require(acceptance["demo_id"] == source["demo_id"] and
            str(acceptance["steam_id"]) == str(item["job"]["steam_id"]), "Acceptance belongs to another queued player/demo")
    expected = dict(plan["files"])
    for stage in stages.values():
        expected.update(stage[-1]["files"])
    for name, digest in acceptance["files"].items():
        require(sha256_file(acceptance_dir/name) == digest, "Acceptance partition changed before packaging")
    samples = [json.loads(line) for line in (acceptance_dir/"accepted_samples.jsonl").read_text().splitlines() if line]
    require(len(samples) == acceptance["accepted_count"], "Acceptance sample count changed")
    render_dir = _owned(work, stages["render"][-1]["out"])
    manifests = list(render_dir.glob("*.render.json"))
    require(len(manifests) == 1, "Expected one renderer result")
    render = read_json(manifests[0])
    require((render["width"], render["height"], render["fps"]) == (640, 360, 32),
            "Renderer output differs from the decided trainer format")
    inventory = read_json(render_dir/"capture_frame_files.json")
    require(len(inventory["frames"]) == render["num_frames"], "Frame inventory count changed")
    rgb_path, evidence_path = destination/"training.zip", destination/"evidence.zip"
    require(not rgb_path.exists() and not evidence_path.exists(), "Use a fresh package attempt directory")
    frames, by_source, kept, duplicates, new_keys = [], {}, [], [], set()
    from .training_dataset import project_targets, validate_sample
    with zipfile.ZipFile(rgb_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for index, frame in enumerate(inventory["frames"]):
            path = _owned(render_dir/"frames", render_dir/"frames"/frame["archived_name"])
            raw = path.read_bytes()
            require(frame["capture_index"] == index and hashlib.sha256(raw).hexdigest() == frame["sha256"],
                    "Source frame identity changed during packaging")
            require(struct.unpack_from("<HH", raw, 12) == (640, 360) and raw[16] in (24, 32),
                    "Source frame layout differs from the RGB baseline")
            class Snapshot:
                def read_bytes(self):
                    return raw
            pixels = tga_rgb24(Snapshot())
            require(len(pixels) == FORMAT["frame_bytes"], "Decoded RGB byte count differs")
            name = f"frames/{index:08d}.rgb"
            archive.writestr(name, pixels)
            row = {"frame_index": index, "member": name, "rgb_sha256": hashlib.sha256(pixels).hexdigest(),
                   "source_path": str(path), "source_sha256": frame["sha256"]}
            frames.append(row); by_source[str(path)] = row
        for sample in samples:
            validate_sample(sample)
            require(sample["demo_id"] == acceptance["demo_id"] and str(sample["steam_id"]) == str(acceptance["steam_id"]),
                    "Sample belongs to another accepted player/demo")
            require(sample.get("training_ready") is True and not sample["reason_codes"] and len(sample["images"]) == 8,
                    "Only accepted eight-frame examples can enter the training shard")
            key = sample_key(sample)
            if key in seen or key in new_keys:
                duplicates.append({"sample_id": sample["sample_id"], "reason": "duplicate_target_commands_across_segments"})
                continue
            selected = [by_source[str(Path(image["path"]).resolve())] for image in sample["images"]]
            require(all(row["source_sha256"] == image["sha256"] for row, image in zip(selected, sample["images"])),
                    "Training image differs from its accepted reference")
            indices = [row["frame_index"] for row in selected]
            require(indices == list(range(indices[0], indices[0]+8)), "Training history is not consecutive")
            kept.append({"sample": sample, "frames": indices, "targets": project_targets(sample["label"])})
            new_keys.add(key)
        manifest = {"schema_version": 1, "profile": PROFILE, "format": FORMAT, "segment_id": segment_id,
                    "frames": frames, "sample_count": len(kept), "duplicate_count": len(duplicates),
                    "source_acceptance_sha256": sha256_file(acceptance_dir/"competitive_acceptance.json"),
                    "source_proof_sha256": acceptance["proof_sha256"],
                    "demo_id": acceptance["demo_id"], "steam_id": str(acceptance["steam_id"])}
        archive.writestr("manifest.json", encoded(manifest))
        archive.writestr("samples.jsonl", b"".join(encoded(row) for row in kept))
        archive.writestr("duplicates.jsonl", b"".join(encoded(row) for row in duplicates))
    with zipfile.ZipFile(rgb_path) as archive:
        require(archive.testzip() is None, "Training archive failed its compression round trip")
    members, shared, original_bytes = {}, {}, 0
    with zipfile.ZipFile(evidence_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(work.rglob("*")):
            require(not path.is_symlink(), "Working evidence cannot contain symlinks")
            if not path.is_file():
                continue
            path = _owned(work, path)
            name = path.relative_to(work).as_posix()
            before = path.stat()
            original_bytes += before.st_size
            digest = hashlib.sha256()
            if path.name == "input.dem":
                actual = sha256_file(path)
                require(actual == source["demo_id"], "Staged demo differs before shared-source archival")
                shared[name] = {"path": source["demo"], "sha256": actual}
                continue
            with path.open("rb") as stream, archive.open(name, "w", force_zip64=True) as output:
                for block in iter(lambda: stream.read(1024*1024), b""):
                    digest.update(block); output.write(block)
            after = path.stat()
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                    "Working evidence changed while it was being archived")
            actual = digest.hexdigest()
            require(str(path) not in expected or expected[str(path)] == actual,
                    "Verified evidence changed before archival: "+name)
            members[name] = {"sha256": actual, "bytes": before.st_size}
        archive.writestr("archive_index.json", encoded({"original_root": str(work), "members": members, "shared_sources": shared}))
    with zipfile.ZipFile(evidence_path) as archive:
        require(archive.testzip() is None, "Evidence archive failed its compression round trip")
    receipt = {"schema_version": 1, "profile": PROFILE, "status": "complete", "format": FORMAT,
               "segment_id": segment_id, "frame_count": len(frames), "sample_count": len(kept),
               "rejected_count": acceptance["rejected_count"], "reason_counts": acceptance["reason_counts"],
               "duplicate_count": len(duplicates), "original_work_root": str(work),
               "source_acceptance_sha256": manifest["source_acceptance_sha256"],
               "training_archive": {"path": str(rgb_path), "sha256": sha256_file(rgb_path)},
               "evidence_archive": {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
               "uncompressed_work_bytes": original_bytes,
               "compressed_bytes": rgb_path.stat().st_size+evidence_path.stat().st_size,
               "shared_sources": shared, "training_ready": bool(kept), "model_training_performed": False}
    save_settings(destination/"receipt.json", receipt)
    seen.update(new_keys)
    return receipt


def release_work(work, receipt, owned_root):
    """Release only a new queue-owned workspace after its archives are verified."""
    work = _owned(owned_root, work)
    require(str(work) == receipt["original_work_root"] and receipt["status"] == "complete", "Archive receipt has another workspace")
    for key in ("training_archive", "evidence_archive"):
        item = receipt[key]
        path = _owned(owned_root, item["path"])
        require(not path.is_relative_to(work) and sha256_file(path) == item["sha256"], "Archive changed before workspace release")
    for item in receipt.get("shared_sources", {}).values():
        require(sha256_file(Path(item["path"])) == item["sha256"], "Shared original demo changed")
    paths = sorted(work.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    require(all(not p.is_symlink() and p.resolve().is_relative_to(work) for p in paths), "Unsafe workspace release path")
    for path in paths:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    work.rmdir()


class RGBFrameCache:
    """Bounded lazy decompression of deterministic uint8 frames for a trainer worker.

    A background prefetcher may call history() before GPU consumption. This
    single-worker cache stores pixels only, never learned network features.
    """
    def __init__(self, receipt_path, *, max_bytes=256*1024**2):
        require(type(max_bytes) is int and max_bytes >= FORMAT["frame_bytes"]*8, "Cache must hold an eight-frame history")
        receipt = read_json(Path(receipt_path))
        require(receipt.get("status") == "complete" and receipt.get("profile") == PROFILE and receipt.get("format") == FORMAT,
                "Unsupported RGB shard receipt")
        item = receipt["training_archive"]
        path = Path(item["path"])
        require(sha256_file(path) == item["sha256"], "Training archive changed")
        self.archive = zipfile.ZipFile(path)
        try:
            self.manifest = json.loads(self.archive.read("manifest.json"))
            require(self.manifest["profile"] == PROFILE and self.manifest["format"] == FORMAT and
                    self.manifest["source_acceptance_sha256"] == receipt["source_acceptance_sha256"],
                    "Shard manifest differs from its receipt")
            self.samples = [json.loads(line) for line in self.archive.read("samples.jsonl").splitlines()]
            require(len(self.samples) == self.manifest["sample_count"] == receipt["sample_count"] and
                    len(self.manifest["frames"]) == receipt["frame_count"], "Shard count differs from its receipt")
        except Exception:
            self.archive.close()
            raise
        self.max_bytes, self.bytes, self.decodes = max_bytes, 0, 0
        self.cache = OrderedDict()

    def frame(self, index):
        require(type(index) is int and 0 <= index < len(self.manifest["frames"]), "Frame index outside shard")
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        row = self.manifest["frames"][index]
        require(row["frame_index"] == index and row["member"] == f"frames/{index:08d}.rgb", "RGB frame order changed")
        info = self.archive.getinfo(row["member"])
        require(info.file_size == FORMAT["frame_bytes"], "RGB member has unexpected size")
        value = self.archive.read(info)
        require(hashlib.sha256(value).hexdigest() == row["rgb_sha256"], "Decoded RGB bytes changed")
        while self.bytes+len(value) > self.max_bytes:
            _, previous = self.cache.popitem(last=False); self.bytes -= len(previous)
        self.cache[index] = value; self.bytes += len(value); self.decodes += 1
        return value

    def history(self, sample_index):
        from .training_dataset import project_targets, validate_sample
        require(type(sample_index) is int and 0 <= sample_index < len(self.samples), "Sample index outside shard")
        row = self.samples[sample_index]
        validate_sample(row["sample"])
        require(row["targets"] == project_targets(row["sample"]["label"]), "Prepared targets differ from their accepted labels")
        indices = row["frames"]
        require(len(indices) == 8 and indices == list(range(indices[0], indices[0]+8)), "Invalid prepared history")
        require(all(0 <= i < len(self.manifest["frames"]) and
                    self.manifest["frames"][i]["source_sha256"] == source["sha256"] and
                    self.manifest["frames"][i]["source_path"] == str(Path(source["path"]).resolve())
                    for i, source in zip(indices, row["sample"]["images"])), "Prepared history differs from accepted images")
        return [self.frame(index) for index in indices], row["targets"]

    def close(self):
        self.archive.close(); self.cache.clear(); self.bytes = 0
