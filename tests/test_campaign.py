"""Campaign accounting tests use real acceptance partitions and mocked render proof."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from cs2_data import acceptance as a
from cs2_data.campaign import summarize_campaign
from cs2_data.io import sha256_file
from test_acceptance import write_fixture


def campaign_fixture(tmp_path, monkeypatch, count=1):
    proofs = {}
    paths = []
    for index in range(count):
        directory = tmp_path / str(index)
        directory.mkdir()
        parsed, dataset, validation, context = write_fixture(directory, monkeypatch, bad_input=index % 2 == 1)
        proof = json.loads(validation.read_text())
        proof.update(parsed_directory=str(parsed.resolve()), validation_version=2)
        proof["checks"]["native_transition_coverage"] = {
            "status": "unknown", "method": "synthetic_unmeasured_transition", "evidence_count": 0,
            "scope": {"frame_indices": [], "command_row_ids": []}, "metrics": {"transition_count": 0}}
        validation.write_text(json.dumps(proof))
        proofs[validation.resolve()] = (proof, parsed.resolve(), dataset.resolve(), context)
        def verify(path, parsed_arg, dataset_arg, state_context=None):
            expected, p, d, c = proofs[path.resolve()]
            if (json.loads(path.read_text()) != expected or parsed_arg.resolve() != p
                    or dataset_arg.resolve() != d or state_context != c):
                raise ValueError("Validation report differs from current evidence")
            return deepcopy(expected)
        monkeypatch.setattr(a, "load_validation", verify)
        out = directory / "acceptance"
        a.accept_samples(parsed, dataset, validation, out)
        paths.append(out)
    return paths, proofs


def test_campaign_recomputes_actual_counts_and_keeps_unproven_transitions_unknown(tmp_path, monkeypatch):
    paths, _ = campaign_fixture(tmp_path, monkeypatch, 2)
    before = {path: sha256_file(path) for path in tmp_path.rglob("*") if path.is_file()}
    out = tmp_path / "campaign"
    report = summarize_campaign(paths, out)
    assert (report["candidate_count"], report["accepted_count"], report["rejected_count"], report["eligible_window_count"]) == (20, 2, 18, 4)
    assert report["unique_demo_count"] == report["unique_round_count"] == report["unique_player_count"] == 1
    assert report["unique_demo_round_player_count"] == 1
    assert report["required_checks"]["pov_identity"] == {"passed": 2, "failed": 0, "unknown": 0}
    assert report["native_transition_coverage"]["clip_status_counts"]["unknown"] == 2
    assert report["training_ready"] is False and report["training_ready_sample_count"] == 2
    assert all(sha256_file(path) == digest for path, digest in before.items())
    assert [path.name for path in out.iterdir()] == ["campaign_manifest.json"]
    with pytest.raises(ValueError, match="fresh output"):
        summarize_campaign(paths, out)


@pytest.mark.parametrize("duplicate", ["same_path", "copied_artifact", "different_policy_same_dataset"])
def test_duplicate_inputs_cannot_inflate_campaign_counts(tmp_path, monkeypatch, duplicate):
    paths, proofs = campaign_fixture(tmp_path, monkeypatch)
    if duplicate == "same_path":
        other = paths[0] / "."
    elif duplicate == "copied_artifact":
        other = tmp_path / "copy"
        shutil.copytree(paths[0], other)
    else:
        other = tmp_path / "different-history"
        validation, (_, parsed, dataset, _) = next(iter(proofs.items()))
        a.accept_samples(parsed, dataset, validation, other, history_frames=2)
    with pytest.raises(ValueError, match="Duplicate"):
        summarize_campaign([paths[0], other], tmp_path / "campaign")
    assert not (tmp_path / "campaign/campaign_manifest.json").exists()


def test_recomputed_validator_rejects_stale_evidence_even_when_saved_hashes_match(tmp_path, monkeypatch):
    paths, _ = campaign_fixture(tmp_path, monkeypatch)
    def stale(*args, **kwargs):
        raise ValueError("Validation report differs from current evidence")
    monkeypatch.setattr(a, "load_validation", stale)
    with pytest.raises(ValueError, match="current evidence"):
        summarize_campaign(paths, tmp_path / "campaign")
    assert list((tmp_path / "campaign").iterdir()) == []


@pytest.mark.parametrize("target", ["partition", "validation", "manifest_counts"])
def test_hash_mismatch_or_edited_counts_abort_without_campaign_publication(tmp_path, monkeypatch, target):
    paths, proofs = campaign_fixture(tmp_path, monkeypatch)
    if target == "manifest_counts":
        path = paths[0] / "acceptance_manifest.json"
        report = json.loads(path.read_text())
        report["accepted_count"] += 1
        path.write_text(json.dumps(report))
    else:
        path = paths[0] / "accepted_samples.jsonl" if target == "partition" else next(iter(proofs))
        path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ValueError, match="hash mismatch|recomputation"):
        summarize_campaign(paths, tmp_path / "campaign")
    assert list((tmp_path / "campaign").iterdir()) == []


def test_self_consistent_forged_partition_is_rejected_by_current_acceptance_recomputation(tmp_path, monkeypatch):
    paths, _ = campaign_fixture(tmp_path, monkeypatch)
    directory = paths[0]
    accepted = directory / "accepted_samples.jsonl"
    rejected = directory / "rejected_samples.jsonl"
    good = [json.loads(row) for row in accepted.read_text().splitlines()]
    bad = [json.loads(row) for row in rejected.read_text().splitlines()]
    promoted = bad.pop(0)
    promoted.update(training_ready=True, reason_codes=[])
    good.append(promoted)
    accepted.write_text("".join(json.dumps(row)+"\n" for row in good))
    rejected.write_text("".join(json.dumps(row)+"\n" for row in bad))
    manifest = directory / "acceptance_manifest.json"
    report = json.loads(manifest.read_text())
    report.update(accepted_count=len(good), rejected_count=len(bad), training_ready_sample_count=len(good))
    report["files"] = {path.name: sha256_file(path) for path in (accepted, rejected)}
    manifest.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="current recomputation"):
        summarize_campaign(paths, tmp_path / "campaign")
    assert list((tmp_path / "campaign").iterdir()) == []
