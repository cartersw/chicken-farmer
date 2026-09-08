"""Independent tensor boundary checks; only the upstream native proof is a fixture."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct

import pytest

from cs2_data import competitive_control as acceptance
from cs2_data import training_dataset as training
from test_competitive_control import evidence, semantics


SERIES = "esl-challenger-league-s52-europe-cup6-misa-vs-mouz-nxt-bo3"


def tga(width=3, height=2, rgb=(40, 80, 120), *, descriptor=32):
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HHBB", header, 12, width, height, 24, descriptor)
    return bytes(header) + bytes(reversed(rgb)) * width * height


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def pipeline(evidence, tmp_path, monkeypatch):
    parsed = tmp_path / "parsed"
    manifest = parsed / "manifest.json"
    write_json(manifest, {"demo_id": evidence["identity"]["demo_id"], "match_id": "fixture-match"})
    evidence["source_files"][str(manifest.resolve())] = acceptance.sha256_file(manifest)
    for index, frame in enumerate(evidence["frames"]):
        path = Path(frame["path"])
        path.write_bytes(tga(rgb=(index, 40, 200)))
        frame["sha256"] = acceptance.sha256_file(path)
    calls = []
    def proof(*args):
        calls.append(args)
        return deepcopy(evidence)
    monkeypatch.setattr(acceptance, "_load_proof", proof)
    inputs = (parsed, tmp_path / "dataset", tmp_path / "network.json", tmp_path / "context.json")
    out = tmp_path / "accepted"
    groups = tmp_path / "series.json"
    write_json(groups, {"schema_version": 1, "profile": training.GROUP_PROFILE,
        "series": [{"series_id": SERIES, "match_id": "fixture-match", "demo_ids": ["a"*64, "b"*64, "c"*64]}]})
    def publish():
        return acceptance.accept_competitive_controls(*inputs, out)
    return {"evidence": evidence, "inputs": inputs, "out": out, "groups": groups, "calls": calls, "publish": publish}


def dataset(pipeline, **kwargs):
    pipeline["publish"]()
    return training.CompetitiveTrainingDataset([pipeline["out"]], series_groups=pipeline["groups"], **kwargs)


def test_real_cpu_tensors_use_only_ordered_rgb_history_and_masked_targets(pipeline):
    torch = pytest.importorskip("torch")
    data = dataset(pipeline)
    assert len(data) == 3 and len(pipeline["calls"]) == 2  # publish + independent loader recomputation
    item = data[0]
    assert set(item) == {"images", "aim_target", "aim_mask", "button_target", "button_mask"}
    assert item["images"].shape == (8, 3, 2, 3)
    assert item["images"].dtype == torch.float32 and item["images"].device.type == "cpu"
    assert item["images"][:, 0, 0, 0].tolist() == pytest.approx([n/255 for n in range(8)])
    assert item["images"][:, 1, 0, 0].tolist() == pytest.approx([40/255]*8)
    assert item["images"][:, 2, 0, 0].tolist() == pytest.approx([200/255]*8)
    assert item["aim_target"].tolist() == [2., 0.] and item["aim_mask"].tolist() == [True, True]
    assert item["button_target"].shape == item["button_mask"].shape == (8, 10)
    assert not item["button_mask"].any().item() and not item["button_target"].any().item()
    assert item["button_mask"].dtype == item["aim_mask"].dtype == torch.bool
    assert data.describe()["private_state_features_included"] is False
    assert data.describe()["split_counts"] == {"train": 3, "validation": 0, "test": 0}
    # Diagnostics are separate copies, not features and not mutable dataset state.
    provenance = data.sample_provenance(0); provenance["demo_id"] = "forged"
    assert data.sample_provenance(0)["demo_id"] == "a"*64


def test_torch_dataloader_batches_images_without_future_or_private_state_channels(pipeline):
    torch = pytest.importorskip("torch")
    data = dataset(pipeline, image_size=(4, 6))
    batch = next(iter(torch.utils.data.DataLoader(data, batch_size=2, shuffle=False, num_workers=0)))
    assert batch["images"].shape == (2, 8, 3, 4, 6)
    assert batch["aim_target"].shape == (2, 2) and batch["button_target"].shape == (2, 8, 10)
    assert not any(name in batch for name in ("state", "position", "enemy", "steam_id", "commands", "input_history", "source"))


def test_downsampled_saturated_pixels_stay_in_exact_unit_interval(pipeline):
    pytest.importorskip("torch")
    for frame in pipeline["evidence"]["frames"]:
        path = Path(frame["path"])
        path.write_bytes(tga(width=8, height=8, rgb=(255, 255, 255)))
        frame["sha256"] = acceptance.sha256_file(path)
    data = dataset(pipeline, image_size=(2, 2))
    images = data[0]["images"]
    assert images.min().item() >= 0. and images.max().item() <= 1.
    assert images.min().item() > .99999  # Clipping must preserve saturation.
    assert data.describe()["resize_roundoff_clamp"] == [0., 1.]


def test_known_released_buttons_and_unavailable_buttons_have_distinct_masks(pipeline):
    pytest.importorskip("torch")
    semantics(pipeline["evidence"])
    del pipeline["evidence"]["semantic_buttons"]["supported_button_masks"]["reload"]
    item = dataset(pipeline)[0]
    back = training.BUTTONS.index("back")
    reload = training.BUTTONS.index("reload")
    held = training.BUTTON_FIELDS.index("held_end")
    assert item["button_target"][back, held].item() == item["button_target"][reload, held].item() == 0.
    assert item["button_mask"][back, held].item() is True
    assert item["button_mask"][reload, held].item() is False
    assert item["button_mask"].sum().item() == 70


@pytest.mark.parametrize("change,match", [
    ("future_image", "future"), ("reorder_images", "reordered"), ("missing_image", "Eight"),
    ("future_bound", "exceeds"), ("predecessor_overlap", "strictly future"),
    ("target_gap", "gap"), ("shifted_label", "interval"), ("wrong_player", "another clock"),
    ("exact_events", "must remain masked"), ("masked_zero", "must be null"), ("nonfinite", "invalid value"),
    ("rejected", "accepted sample"), ("duration", "sample duration"), ("later_pair", "first complete future")])
def test_boundary_rejects_leakage_shifted_provenance_and_mask_corruption(pipeline, change, match):
    pipeline["publish"]()
    sample = json.loads((pipeline["out"] / "accepted_samples.jsonl").read_text().splitlines()[0])
    if change == "future_image": sample["images"][-1]["frame_index"] += 1
    elif change == "reorder_images": sample["images"][0], sample["images"][1] = sample["images"][1], sample["images"][0]
    elif change == "missing_image": sample["images"].pop(0)
    elif change == "future_bound": sample["images"][-1]["observation_upper_execution_tick"] += 1
    elif change == "predecessor_overlap":
        support = sample["command_support"][0]
        support.update(server_tick_executed=115, support_start_tick=114, support_end_tick=115)
    elif change == "target_gap":
        support = sample["command_support"][-1]
        support.update(server_tick_executed=119, support_start_tick=118, support_end_tick=119)
    elif change == "shifted_label": sample["label"]["command_interval"]["target_execution_ticks"][0] += 1
    elif change == "wrong_player": sample["label"]["raw_command_provenance"][1]["steam_id"] = 12
    elif change == "exact_events": sample["label"]["exact_button_event_count"] = {"value": 1, "valid": True, "reason_codes": []}
    elif change == "masked_zero": sample["label"]["buttons"]["forward"]["held_end"]["value"] = False
    elif change == "nonfinite": sample["label"]["aim_delta_deg"]["yaw"]["value"] = float("inf")
    elif change == "rejected": sample["training_ready"] = False
    elif change == "duration": sample["decision_period_ns"] *= 2
    elif change == "later_pair":
        for support in sample["command_support"]:
            for key in ("server_tick_executed", "support_start_tick", "support_end_tick"):
                support[key] += 1
    with pytest.raises(ValueError, match=match):
        training.validate_sample(sample)


def test_rehashed_accepted_flag_cannot_replace_independent_source_verification(pipeline):
    pipeline["publish"]()
    path = pipeline["out"] / "accepted_samples.jsonl"
    samples = [json.loads(line) for line in path.read_text().splitlines()]
    samples[0]["label"]["buttons"]["forward"]["held_end"] = {"value": True, "valid": True, "reason_codes": []}
    path.write_bytes(b"".join(acceptance._bytes(row) for row in samples))
    manifest = pipeline["out"] / "competitive_acceptance.json"
    report = json.loads(manifest.read_text()); report["files"][path.name] = acceptance.sha256_file(path)
    write_json(manifest, report)
    with pytest.raises(ValueError, match="independently recomputed"):
        training.CompetitiveTrainingDataset([pipeline["out"]], series_groups=pipeline["groups"])


def test_same_length_image_corruption_with_restored_metadata_is_caught_by_byte_hash(pipeline):
    pytest.importorskip("torch")
    data = dataset(pipeline)
    path = Path(pipeline["evidence"]["frames"][0]["path"])
    stat = path.stat()
    raw = bytearray(path.read_bytes()); raw[-1] ^= 1
    path.write_bytes(raw); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="image bytes changed"):
        data[0]


@pytest.mark.parametrize("source", ["partition", "grouping", "source_proof"])
def test_post_open_publication_grouping_or_source_mutation_is_rejected(pipeline, source):
    pytest.importorskip("torch")
    data = dataset(pipeline)
    path = (pipeline["out"] / "accepted_samples.jsonl" if source == "partition" else pipeline["groups"] if source == "grouping"
            else Path(next(iter(pipeline["evidence"]["source_files"]))))
    with path.open("ab") as handle: handle.write(b" changed")
    with pytest.raises(ValueError, match="changed"):
        data[0]


def test_zero_accepted_rows_cannot_fall_back_to_rejected_or_another_split(pipeline, tmp_path):
    pipeline["publish"]()
    data = training.CompetitiveTrainingDataset([pipeline["out"]], series_groups=pipeline["groups"], split="validation")
    assert len(data) == 0
    with pytest.raises(ValueError, match="no cross-split fallback"):
        training.write_first_batch([pipeline["out"]], series_groups=pipeline["groups"], split="validation", output=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()


def test_all_bo3_maps_share_one_stable_split_and_new_series_do_not_reassign_them(pipeline):
    groups, _ = training.load_series_groups(pipeline["groups"])
    assert len(groups) == 3
    assigned = {demo: training.series_split(group["series_id"]) for demo, group in groups.items()}
    assert set(assigned.values()) == {"train"}
    assert training.series_split(SERIES) == "train"  # Frozen full-hash bucket3933.
    value = json.loads(pipeline["groups"].read_text())
    value["series"].append({"series_id": "another-series", "match_id": "another-match", "demo_ids": ["d"*64]})
    write_json(pipeline["groups"], value)
    expanded, _ = training.load_series_groups(pipeline["groups"])
    assert {demo: training.series_split(expanded[demo]["series_id"]) for demo in groups} == assigned
    assert {training.series_split(f"independent-series-{n}") for n in range(100)} == set(training.SPLITS)


@pytest.mark.parametrize("change", ["same_match_two_series", "same_demo_two_series", "unknown_demo", "wrong_match"])
def test_grouping_rejects_split_leakage_and_unmatched_source_identities(pipeline, change):
    pipeline["publish"]()
    value = json.loads(pipeline["groups"].read_text())
    if change.startswith("same"):
        value["series"].append({"series_id": "different-series", "match_id": "fixture-match" if change == "same_match_two_series" else "different-match",
                               "demo_ids": ["d"*64] if change == "same_match_two_series" else ["a"*64]})
    elif change == "unknown_demo": value["series"][0]["demo_ids"] = ["b"*64, "c"*64]
    else: value["series"][0]["match_id"] = "not-the-recorded-match"
    write_json(pipeline["groups"], value)
    with pytest.raises(ValueError, match="multiple|grouping"):
        training.CompetitiveTrainingDataset([pipeline["out"]], series_groups=pipeline["groups"])


def test_duplicate_partitions_do_not_inflate_batch_count(pipeline):
    pipeline["publish"]()
    with pytest.raises(ValueError, match="Duplicate acceptance"):
        training.CompetitiveTrainingDataset([pipeline["out"], pipeline["out"]], series_groups=pipeline["groups"])


def test_shared_source_version_cannot_be_overwritten_by_a_later_publication_snapshot(pipeline, tmp_path, monkeypatch):
    pipeline["publish"]()
    original = pipeline["evidence"]
    shared = Path(next(iter(original["source_files"])))
    old_bytes, new_bytes = shared.read_bytes(), b"a newer independently verified source version"
    second = deepcopy(original)
    second["identity"]["demo_id"] = "b"*64
    second["provenance"]["demo_id"] = "b"*64
    for row in second["commands"] + second["states"]:
        row["demo_id"] = "b"*64
    second["clip_id"] = "second-map-clip"
    parsed_b = tmp_path / "parsed-b"
    canonical_b = parsed_b / "manifest.json"
    write_json(canonical_b, {"demo_id": "b"*64, "match_id": "fixture-match"})
    second["source_files"].pop(str(pipeline["inputs"][0] / "manifest.json"))
    second["source_files"][str(canonical_b)] = acceptance.sha256_file(canonical_b)
    shared.write_bytes(new_bytes)
    second["source_files"][str(shared)] = acceptance.sha256_file(shared)
    def replay_proof(*args):
        return deepcopy(second if Path(args[0]) == parsed_b else original)
    monkeypatch.setattr(acceptance, "_load_proof", replay_proof)
    second_out = tmp_path / "accepted-b"
    acceptance.accept_competitive_controls(parsed_b, *pipeline["inputs"][1:], second_out)
    shared.write_bytes(old_bytes)
    original_loader = training._load_acceptance
    def load_with_source_version_change(path):
        if path.parent == second_out:
            shared.write_bytes(new_bytes)
        return original_loader(path)
    monkeypatch.setattr(training, "_load_acceptance", load_with_source_version_change)
    with pytest.raises(ValueError, match="Conflicting source digest"):
        training.CompetitiveTrainingDataset([pipeline["out"], second_out], series_groups=pipeline["groups"])


def test_batch_artifact_contains_only_safe_tensor_dictionary_and_explicit_masks(pipeline, tmp_path):
    torch = pytest.importorskip("torch")
    pipeline["publish"]()
    out = tmp_path / "first-batch"
    report = training.write_first_batch([pipeline["out"]], series_groups=pipeline["groups"], output=out, batch_size=2)
    batch = torch.load(out / "batch.pt", weights_only=True)
    assert report["status"] == "verified_tensor_batch_materialized" and report["model_training_performed"] is False
    assert report["valid_target_counts"] == {"aim_mask": 4, "button_mask": 0}
    assert report["tensor_shapes"]["images"] == [2, 8, 3, 2, 3]
    assert set(batch) == {"images", "aim_target", "aim_mask", "button_target", "button_mask"}
    assert all(torch.is_tensor(value) for value in batch.values())
    with pytest.raises(ValueError, match="fresh"):
        training.write_first_batch([pipeline["out"]], series_groups=pipeline["groups"], output=out)


def test_tga_decoder_uses_top_first_rgb_for_bottom_origin_and_rle(tmp_path):
    path = tmp_path / "colors.tga"
    header = bytearray(tga(width=1, height=2, descriptor=0)[:18])
    path.write_bytes(bytes(header) + bytes((0, 0, 255, 255, 0, 0)))  # Bottomred, topblue in BGR.
    image = {"path": str(path), "sha256": acceptance.sha256_file(path)}
    assert training.read_verified_rgb(image) == (1, 2, bytes((0, 0, 255, 255, 0, 0)))
    header[2] = 10; header[17] = 32
    path.write_bytes(bytes(header) + bytes((129, 30, 20, 10)))
    image["sha256"] = acceptance.sha256_file(path)
    assert training.read_verified_rgb(image) == (1, 2, bytes((10, 20, 30))*2)
