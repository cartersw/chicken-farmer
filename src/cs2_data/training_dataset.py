"""Provenance-checked image-only tensors from scoped competitive acceptance.

Opening a dataset independently revalidates acceptance. Model observations are
eight RGB images only; command/state provenance never enters the tensor inputs.
Torch is optional until a sample or batch is materialized. No training runs here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from .io import sha256_file
from .timing import tga_rgb24


PROFILE = "cs2-competitive-image-control-tensors-v1"
GROUP_PROFILE = "cs2-series-grouping-v1"
SPLIT_PROFILE = "cs2-series-split-v1"
SPLIT_SEED = "default-v1"
SPLITS = ("train", "validation", "test")
AXES = ("yaw", "pitch")
BUTTONS = ("forward", "back", "left", "right", "crouch", "jump", "attack1", "reload")
BUTTON_FIELDS = ("held_start", "held_mid", "held_end", "net_changed", "net_pressed", "net_released",
                 "recorded_activity_present", "unresolved_rapid_activity", "net_changed_tick0", "net_changed_tick1")
EXACT_FIELDS = ("exact_button_event_count", "exact_button_event_order", "exact_button_event_offsets_ns")
HISTORY_IMAGES = 8
DECISION_PERIOD_NS = 31_250_000


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _object_bytes(path):
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    _require(isinstance(value, dict), "Expected a JSON object: " + str(path))
    return value, hashlib.sha256(raw).hexdigest()


def series_split(series_id, *, seed=SPLIT_SEED):
    """Stable 80/10/10 bucket using the full SHA256 integer, never image/row IDs."""
    _require(isinstance(series_id, str) and 0 < len(series_id) <= 512, "Invalid series identity")
    _require(isinstance(seed, str) and 0 < len(seed) <= 256, "Invalid split seed")
    digest = hashlib.sha256((SPLIT_PROFILE + "\0" + seed + "\0" + series_id).encode()).hexdigest()
    bucket = int(digest, 16) % 10000
    return "train" if bucket < 8000 else "validation" if bucket < 9000 else "test"


def load_series_groups(path):
    """Read explicit source grouping; the series identity is curated metadata."""
    path = Path(path).resolve()
    value, digest = _object_bytes(path)
    _require(type(value.get("schema_version")) is int and value["schema_version"] == 1 and value.get("profile") == GROUP_PROFILE and
             isinstance(value.get("series"), list) and bool(value["series"]), "Unsupported or empty series grouping")
    by_demo, series, matches = {}, set(), set()
    for entry in value["series"]:
        _require(isinstance(entry, dict), "Invalid series grouping entry")
        series_id, match_id, demos = (entry.get(key) for key in ("series_id", "match_id", "demo_ids"))
        _require(isinstance(series_id, str) and 0 < len(series_id) <= 512 and series_id not in series,
                 "Duplicate or invalid series identity")
        _require(isinstance(match_id, str) and bool(match_id) and match_id not in matches,
                 "A match cannot be assigned to multiple series groups")
        _require(isinstance(demos, list) and bool(demos) and all(_sha(demo) for demo in demos), "Invalid grouped demo identities")
        series.add(series_id); matches.add(match_id)
        for demo in demos:
            _require(demo not in by_demo, "A demo cannot be assigned to multiple series groups or repeated")
            by_demo[demo] = {"series_id": series_id, "match_id": match_id}
    return by_demo, {"path": str(path), "sha256": digest, "series_count": len(series), "demo_count": len(by_demo)}


def _cell(cell, *, boolean=False, exact=False):
    _require(isinstance(cell, dict) and set(cell) == {"value", "valid", "reason_codes"}, "Invalid target cell schema")
    valid, value, reasons = (cell[name] for name in ("valid", "value", "reason_codes"))
    _require(type(valid) is bool and isinstance(reasons, list) and all(isinstance(reason, str) and reason for reason in reasons),
             "Target cell needs a boolean mask and explicit reason codes")
    if exact:
        _require(valid is False and value is None and bool(reasons), "Uncalibrated exact-event channels must remain masked")
    elif valid:
        _require(not reasons and (type(value) is bool if boolean else _number(value) and abs(value) <= 3.4028234663852886e38),
                 "Available target has an invalid value or contradictory reason codes")
    else:
        _require(value is None and bool(reasons), "Unavailable targets must be null with a reason, never inferred zero")
    # The zero is only a tensor placeholder; its mask is retained separately.
    return float(value) if valid else 0.0, valid


def project_targets(label):
    """Project only the measured scalar contract; preserve masks independently."""
    _require(isinstance(label, dict) and label.get("decision_period_ns") == DECISION_PERIOD_NS and
             label.get("target_command_count") == 2 and label.get("label_valid") is True,
             "Training targets require a valid scoped two-command 32 Hz label")
    _require(label.get("exact_input_timing_verified") is False, "Exact physical input timing is outside this target profile")
    for field in EXACT_FIELDS:
        _cell(label.get(field), exact=True)
    angles = label.get("aim_delta_deg", {})
    controls = label.get("buttons", {})
    _require(isinstance(angles, dict) and set(angles) == set(AXES) and isinstance(controls, dict) and set(controls) == set(BUTTONS),
             "Unsupported angular or button target channels")
    aim, aim_mask, buttons, button_mask = [], [], [], []
    for axis in AXES:
        value, valid = _cell(angles[axis])
        aim.append(value); aim_mask.append(valid)
    for name in BUTTONS:
        fields = controls[name]
        _require(isinstance(fields, dict) and set(fields) == set(BUTTON_FIELDS[:-2]) | {"per_command_net_changed"} and
                 isinstance(fields["per_command_net_changed"], list) and len(fields["per_command_net_changed"]) == 2,
                 "Unsupported button target layout")
        cells = [fields[field] for field in BUTTON_FIELDS[:-2]] + fields["per_command_net_changed"]
        pairs = [_cell(cell, boolean=True) for cell in cells]
        buttons.append([pair[0] for pair in pairs]); button_mask.append([pair[1] for pair in pairs])
    _require(any(aim_mask) or any(any(row) for row in button_mask), "Accepted sample has no available target fields")
    return {"aim_target": aim, "aim_mask": aim_mask, "button_target": buttons, "button_mask": button_mask}


def validate_sample(sample, *, expected_profile=None):
    """Defend the tensor boundary independently of the acceptance implementation."""
    _require(isinstance(sample, dict) and type(sample.get("schema_version")) is int and sample["schema_version"] == 1 and sample.get("training_ready") is True and
             sample.get("reason_codes") == [], "Tensor loader requires an accepted sample, not a candidate or rejection")
    if expected_profile is not None:
        _require(sample.get("profile") == expected_profile, "Sample profile disagrees with independently verified acceptance")
    _require(sample.get("decision_period_ns") == DECISION_PERIOD_NS and sample.get("previous_action_features_included") is False and
             sample.get("exact_input_timing_verified") is False, "Unsupported sample duration or observation/timing feature convention")
    _require(isinstance(sample.get("sample_id"), str) and bool(sample["sample_id"]) and _sha(sample.get("demo_id")) and
             isinstance(sample.get("clip_id"), str) and bool(sample["clip_id"]) and _sha(sample.get("proof_sha256")),
             "Missing accepted sample/proof identity")
    _require(_integer(sample.get("round_id")) and sample["round_id"] > 0 and _integer(sample.get("player_slot")) and
             isinstance(sample.get("steam_id"), str) and sample["steam_id"].isdigit() and 0 < int(sample["steam_id"]) < 2**64,
             "Missing accepted round/player identity")
    index, images, upper = (sample.get(key) for key in ("observation_frame_index", "images", "observation_upper_execution_tick"))
    _require(_integer(index) and index >= HISTORY_IMAGES-1 and isinstance(images, list) and len(images) == HISTORY_IMAGES and
             _number(upper) and upper >= 0, "Eight images and a finite observation bound are required")
    image_bounds, image_paths = [], set()
    for expected, image in zip(range(index-HISTORY_IMAGES+1, index+1), images):
        _require(isinstance(image, dict) and type(image.get("frame_index")) is int and image["frame_index"] == expected,
                 "Image history contains a future, repeated, missing or reordered frame")
        bound = image.get("observation_upper_execution_tick")
        _require(_number(bound) and 0 <= bound <= upper and (not image_bounds or bound >= image_bounds[-1]),
                 "Image bound is unknown, reversed or exceeds the declared observation")
        image_bounds.append(bound)
        path = image.get("path")
        _require(isinstance(path, str) and Path(path).is_absolute() and Path(path).suffix.lower() == ".tga" and
                 _sha(image.get("sha256")), "Model histories require absolute byte-bound original TGA paths")
        resolved = str(Path(path).resolve())
        _require(resolved not in image_paths, "Repeated image file masquerades as separate history frames")
        image_paths.add(resolved)
    _require(max(image_bounds) == upper, "Sample bound must equal the conservative bound of its complete history")
    supports = sample.get("command_support")
    _require(isinstance(supports, list) and len(supports) == 3, "A target requires its predecessor and exactly two commands")
    for role, support in zip(("predecessor", "target0", "target1"), supports):
        _require(isinstance(support, dict) and support.get("role") == role and all(_integer(support.get(key)) for key in
                 ("command_row_id", "command_number", "server_tick_executed", "support_start_tick", "support_end_tick")),
                 "Invalid command support provenance")
        execution = support["server_tick_executed"]
        _require(execution > 0 and support["support_start_tick"] == execution-1 and support["support_end_tick"] == execution and
                 support["support_start_tick"] > upper, "Target or normalization predecessor is not strictly future to every input image")
    for previous, current in zip(supports, supports[1:]):
        _require(current["server_tick_executed"] == previous["server_tick_executed"]+1 and
                 current["command_number"] == previous["command_number"]+1 and current["command_row_id"] > previous["command_row_id"],
                 "Target command support has a gap, reversal or duplicate")
    _require(supports[0]["server_tick_executed"] == math.floor(upper) + 2,
             "Sample shifted past the first complete future predecessor")
    label = sample.get("label", {})
    interval = label.get("command_interval", {})
    _require(interval.get("predecessor_execution_tick") == supports[0]["server_tick_executed"] and
             interval.get("target_execution_ticks") == [row["server_tick_executed"] for row in supports[1:]] and
             interval.get("predecessor_command_row_id") == supports[0]["command_row_id"] and
             interval.get("target_command_row_ids") == [row["command_row_id"] for row in supports[1:]],
             "Label target interval disagrees with causal support")
    raw = label.get("raw_command_provenance")
    _require(isinstance(raw, list) and len(raw) == 3, "Missing raw command label provenance")
    for command, support in zip(raw, supports):
        _require(isinstance(command, dict) and all(command.get(key) == support[key] for key in
                 ("command_row_id", "command_number", "server_tick_executed")) and
                 all(str(command.get(key)) == str(sample[key]) for key in ("demo_id", "round_id", "steam_id", "player_slot")) and
                 _sha(command.get("command_protobuf_sha256")), "Raw target provenance has another clock, player or source")
    return project_targets(label)


def _load_acceptance(path):
    # Import here so lightweight metadata/target tests do not require the native
    # verification stack, and torch remains optional for all verification work.
    from .competitive_control import load_competitive_acceptance
    return load_competitive_acceptance(path)


class _ByteSnapshot:
    def __init__(self, raw):
        self.raw = raw

    def read_bytes(self):
        return self.raw


def read_verified_rgb(image):
    """Hash and decode the very same bytes; a second file read cannot race them."""
    path = Path(image["path"])
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == image["sha256"], "Model image bytes changed: " + str(path))
    _require(len(raw) >= 18, "Incomplete model TGA header")
    width, height = struct.unpack_from("<HH", raw, 12)
    pixels = tga_rgb24(_ByteSnapshot(raw))
    _require(len(pixels) == width * height * 3, "Decoded model image dimensions disagree")
    return width, height, pixels


def _torch():
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("Tensor materialization requires the optional training dependency torch==2.8.0") from error
    return torch


class CompetitiveTrainingDataset:
    """Map-style torch DataLoader-compatible dataset with image-only inputs.

    The constructor performs expensive source revalidation once. Each access
    checks publication metadata and the exact bytes of its eight images. No
    caller-supplied verifier or accepted=True shortcut enables tensor loading.
    """
    def __init__(self, acceptance, *, series_groups, split="train", split_seed=SPLIT_SEED, image_size=None):
        _require(isinstance(acceptance, (list, tuple)) and bool(acceptance), "At least one acceptance artifact is required")
        _require(split in SPLITS, "Choose a grouped train, validation or test split")
        if image_size is not None:
            _require(isinstance(image_size, (list, tuple)) and len(image_size) == 2 and
                     all(type(value) is int and 1 <= value <= 4096 for value in image_size), "Invalid output image height/width")
            image_size = tuple(image_size)
        groups, grouping = load_series_groups(series_groups)
        self.split, self.split_seed, self.image_size = split, split_seed, image_size
        self._metadata_hashes = {grouping["path"]: grouping["sha256"]}
        for implementation in (Path(__file__), Path(__file__).with_name("timing.py")):
            self._metadata_hashes[str(implementation.resolve())] = sha256_file(implementation)
        self._bound_hashes = dict(self._metadata_hashes)
        self._source_stats, self._samples, self._targets, self._provenance = {}, [], [], []
        counts, series_counts = Counter(), {name: set() for name in SPLITS}
        sample_ids, target_keys, manifest_paths = set(), set(), set()
        publications = []
        for source in acceptance:
            path = Path(source).resolve()
            path = path / "competitive_acceptance.json" if path.is_dir() else path
            _require(path not in manifest_paths, "Duplicate acceptance path would duplicate training examples")
            manifest_paths.add(path)
            before = sha256_file(path)
            report = _load_acceptance(path)
            _require(sha256_file(path) == before, "Acceptance manifest changed during source verification")
            stored, stored_digest = _object_bytes(path)
            _require(stored == report and stored_digest == before, "Returned acceptance proof differs from the published manifest")
            filename = "accepted_samples.jsonl"
            payload_path = path.parent / filename
            payload = payload_path.read_bytes()
            _require(_sha(report.get("files", {}).get(filename)) and hashlib.sha256(payload).hexdigest() == report["files"][filename],
                     "Accepted sample bytes disagree with independently verified publication")
            self._remember_digest(path, before)
            self._metadata_hashes[str(path)] = before
            for name, digest in report["files"].items():
                file_path = (path.parent / name).resolve()
                _require(file_path.is_relative_to(path.parent) and _sha(digest) and sha256_file(file_path) == digest,
                         "Acceptance evidence path/hash disagrees")
                self._remember_digest(file_path, digest)
                self._metadata_hashes[str(file_path)] = digest
            # Complete evidence inventory from the acceptance verifier.
            # Initial revalidation hashes content; later checks detect source edits
            # without rescanning the multi-gigabyte match tables for every sample.
            source_files = report.get("source_files")
            _require(isinstance(source_files, dict) and bool(source_files), "Acceptance lacks its checked source file inventory")
            for name, digest in source_files.items():
                file_path = Path(name).resolve()
                self._remember_digest(file_path, digest)
                before_stat = file_path.stat()
                _require(_sha(digest) and sha256_file(file_path) == digest, "Accepted source changed before the loader snapshot")
                after_stat = file_path.stat()
                snapshot = (before_stat.st_size, before_stat.st_mtime_ns)
                _require(snapshot == (after_stat.st_size, after_stat.st_mtime_ns), "Accepted source changed during loader snapshot")
                _require(str(file_path) not in self._source_stats or self._source_stats[str(file_path)] == snapshot,
                         "Shared accepted source snapshot changed between publications")
                self._source_stats[str(file_path)] = snapshot
            parsed_path = Path(report.get("inputs", {}).get("parsed", "")).resolve() / "manifest.json"
            canonical_manifest, canonical_digest = _object_bytes(parsed_path)
            _require(source_files.get(str(parsed_path)) == canonical_digest and canonical_manifest.get("demo_id") == report.get("demo_id"),
                     "Source match identity must come from the checked canonical manifest")
            rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
            _require(type(report.get("accepted_count")) is int and len(rows) == report["accepted_count"], "Acceptance count disagrees with records")
            for sample in rows:
                targets = validate_sample(sample, expected_profile=report["profile"])
                group = groups.get(sample["demo_id"])
                _require(group is not None, "Accepted demo lacks explicit whole-series grouping")
                _require(group["match_id"] == canonical_manifest.get("match_id") and sample["demo_id"] == canonical_manifest["demo_id"],
                         "Curated grouping disagrees with the checked source match identity")
                _require(sample["sample_id"] not in sample_ids, "Duplicate accepted sample identity")
                sample_ids.add(sample["sample_id"])
                target_key = (sample["demo_id"], sample["steam_id"], tuple(row["command_row_id"] for row in sample["command_support"][1:]))
                _require(target_key not in target_keys, "Repeated canonical target pair would duplicate training examples")
                target_keys.add(target_key)
                assigned = series_split(group["series_id"], seed=split_seed)
                counts[assigned] += 1; series_counts[assigned].add(group["series_id"])
                if assigned == split:
                    self._samples.append(deepcopy(sample)); self._targets.append(targets)
                    self._provenance.append({"sample_id": sample["sample_id"], "demo_id": sample["demo_id"],
                        "clip_id": sample["clip_id"], "series_id": group["series_id"], "match_id": group["match_id"],
                        "split": assigned, "acceptance_manifest": str(path), "acceptance_manifest_sha256": before,
                        "proof_sha256": sample["proof_sha256"], "image_sha256": [image["sha256"] for image in sample["images"]]})
            publications.append({"path": str(path), "sha256": before, "accepted_count": len(rows)})
        self._description = {"schema_version": 1, "profile": PROFILE, "split": split, "split_profile": SPLIT_PROFILE,
            "split_seed": split_seed, "split_thresholds_per_10000": [8000, 9000, 10000],
            "split_counts": {name: counts[name] for name in SPLITS},
            "series_counts": {name: len(series_counts[name]) for name in SPLITS},
            "selected_sample_count": len(self._samples), "series_grouping": grouping, "acceptance": publications,
            "input_channels": ["images"], "history_images": HISTORY_IMAGES,
            "image_layout": "T,C,H,W", "image_dtype": "float32", "image_values": "RGB_0_to_1",
            "image_size_height_width": list(image_size) if image_size else None,
            "resize": "bilinear_align_corners_false_antialias_true" if image_size else "none",
            "resize_roundoff_clamp": [0., 1.] if image_size else None,
            "aim_axes": list(AXES), "button_axes": list(BUTTONS), "button_fields": list(BUTTON_FIELDS),
            "exact_event_targets_included": False, "private_state_features_included": False,
            "previous_action_features_included": False, "model_training_performed": False,
            "limits": ["Series grouping is explicit curated source metadata, never inferred from frame IDs.",
                "An empty validation or test split is reported; one BO3 cannot provide independent train/validation matches.",
                "Masked target zeros are placeholders; losses must use the corresponding masks.",
                "Source evidence is revalidated on open; later source checks use size/mtime and image checks hash exact bytes."]}
        self.verify_sources_unchanged()

    def __len__(self):
        return len(self._samples)

    def _remember_digest(self, path, digest):
        name = str(Path(path).resolve())
        _require(_sha(digest), "Invalid bound training source digest")
        _require(name not in self._bound_hashes or self._bound_hashes[name] == digest,
                 "Conflicting source digest across training publications: " + name)
        self._bound_hashes[name] = digest

    def verify_sources_unchanged(self):
        for name, digest in self._metadata_hashes.items():
            _require(sha256_file(Path(name)) == digest, "Training publication or grouping changed: " + name)
        for name, expected in self._source_stats.items():
            stat = Path(name).stat()
            _require((stat.st_size, stat.st_mtime_ns) == expected, "Accepted source evidence changed: " + name)

    def describe(self):
        return deepcopy(self._description)

    def sample_provenance(self, index):
        return deepcopy(self._provenance[index])

    def __getitem__(self, index):
        if type(index) is not int or not 0 <= index < len(self):
            raise IndexError("Training sample index outside selected split")
        self.verify_sources_unchanged()
        sample, targets, torch = self._samples[index], self._targets[index], _torch()
        images, shape = [], None
        for image in sample["images"]:
            width, height, rgb = read_verified_rgb(image)
            _require(shape in (None, (height, width)), "Image history mixes dimensions")
            shape = height, width
            tensor = torch.frombuffer(bytearray(rgb), dtype=torch.uint8).reshape(height, width, 3).permute(2, 0, 1)
            images.append(tensor)
        history = torch.stack(images).to(dtype=torch.float32).div_(255.)
        if self.image_size is not None and self.image_size != shape:
            history = torch.nn.functional.interpolate(history, size=self.image_size, mode="bilinear", align_corners=False, antialias=True)
            # Float32 filter weights can put saturated pixels one ULP above one.
            history.clamp_(0., 1.)
        return {"images": history.contiguous(), "aim_target": torch.tensor(targets["aim_target"], dtype=torch.float32),
            "aim_mask": torch.tensor(targets["aim_mask"], dtype=torch.bool),
            "button_target": torch.tensor(targets["button_target"], dtype=torch.float32),
            "button_mask": torch.tensor(targets["button_mask"], dtype=torch.bool)}


def write_first_batch(acceptance, *, series_groups, output, batch_size=2, split="train", image_size=None):
    output = Path(output).resolve()
    _require(not output.exists(), "Training batch diagnostic requires a fresh output directory")
    _require(type(batch_size) is int and 1 <= batch_size <= 32, "Batch size must be between 1 and 32")
    dataset = CompetitiveTrainingDataset(acceptance, series_groups=series_groups, split=split, image_size=image_size)
    _require(len(dataset) > 0, "Selected whole-series split has no accepted samples; no cross-split fallback is allowed")
    torch = _torch()
    count = min(batch_size, len(dataset))
    samples = [dataset[index] for index in range(count)]
    batch = {name: torch.stack([sample[name] for sample in samples]) for name in samples[0]}
    _require(torch.isfinite(batch["images"]).all().item() and all(torch.isfinite(batch[name]).all().item() for name in
             ("aim_target", "button_target")), "Nonfinite batch values")
    dataset.verify_sources_unchanged()
    output.mkdir(parents=True, exist_ok=False)
    artifact = output / "batch.pt"
    torch.save(batch, artifact)
    report = {**dataset.describe(), "status": "verified_tensor_batch_materialized", "batch_sample_count": count,
        "torch_version": str(torch.__version__), "batch_file": {"path": str(artifact), "sha256": sha256_file(artifact)},
        "tensor_shapes": {name: list(value.shape) for name, value in batch.items()},
        "tensor_dtypes": {name: str(value.dtype) for name, value in batch.items()},
        "valid_target_counts": {name: int(batch[name].sum().item()) for name in ("aim_mask", "button_mask")},
        "samples": [dataset.sample_provenance(index) for index in range(count)],
        "loader_source_sha256": sha256_file(Path(__file__))}
    with (output / "batch_report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False); handle.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="append", required=True, type=Path)
    parser.add_argument("--series-groups", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=SPLITS, default="train")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--height", type=int)
    parser.add_argument("--width", type=int)
    args = parser.parse_args(argv)
    _require((args.height is None) == (args.width is None), "Both resize height and width are required together")
    report = write_first_batch(args.acceptance, series_groups=args.series_groups, output=args.output, batch_size=args.batch_size,
        split=args.split, image_size=(args.height, args.width) if args.height is not None else None)
    print(json.dumps({"status": report["status"], "tensor_shapes": report["tensor_shapes"],
                      "valid_target_counts": report["valid_target_counts"], "split_counts": report["split_counts"]}))


if __name__ == "__main__":
    main()
