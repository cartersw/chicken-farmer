"""Lossless RGB bytes, rolling reuse, evidence recovery and guarded cleanup."""
import json
from pathlib import Path
import zipfile

import pytest

from cs2_data import competitive_batch as batch
from cs2_data import training_archive as archive
from cs2_data.io import read_json, sha256_file, write_json
from test_competitive_control import evidence
from test_training_dataset import pipeline, tga


@pytest.fixture
def completed(pipeline, tmp_path):
    work = tmp_path/"queue/work/segment"
    render, acceptance = work/"render", work/"acceptance"
    (render/"frames").mkdir(parents=True)
    inventory = []
    for i, frame in enumerate(pipeline["evidence"]["frames"]):
        path = render/"frames"/f"frame-{i}.tga"
        path.write_bytes(tga(640, 360, (i, 40, 200)))
        frame.update(path=str(path.resolve()), sha256=sha256_file(path))
        inventory.append({"capture_index": i, "archived_name": path.name, "sha256": frame["sha256"]})
    pipeline["publish"]()
    import shutil
    shutil.copytree(pipeline["out"], acceptance)
    write_json(render/"capture_frame_files.json", {"frames": inventory})
    write_json(render/"one.render.json", {"width": 640, "height": 360, "fps": 32, "num_frames": 10})
    plan = {"jobs": [{"job_id": "segment", "job": {"steam_id": pipeline["evidence"]["identity"]["steam_id"]}}], "stage_order": list(batch.STAGES), "files": {},
            "sources": [{"demo": str(tmp_path/"original.dem"), "demo_id": "a"*64}]}
    write_json(work/"batch/batch_plan.json", plan)
    stages = {stage: [{"status": "completed", "out": str(render if stage == "render" else acceptance),
                      "files": {}, "result": {"status": "accepted_partition_verified"}}] for stage in batch.STAGES}
    write_json(work/"batch/batch_state.json", {"plan_sha256": sha256_file(work/"batch/batch_plan.json"),
               "jobs": {"segment": {"stages": stages}}})
    return work, tmp_path/"queue/packages/segment"


def test_archive_round_trip_preserves_exact_rgb_and_reuses_overlapping_histories(completed):
    work, destination = completed
    receipt = archive.pack_segment(work, destination, "segment", evidence_retention="full")
    assert receipt["sample_count"] == 3 and receipt["frame_count"] == 10
    assert receipt["format"] == archive.FORMAT and receipt["compressed_bytes"] < receipt["uncompressed_work_bytes"]
    reader = archive.RGBFrameCache(destination/"receipt.json", max_bytes=8*archive.FORMAT["frame_bytes"])
    first, targets = reader.history(0)
    assert [value[:3] for value in first] == [bytes((i, 40, 200)) for i in range(8)]
    assert all(len(value) == 640*360*3 for value in first)
    assert reader.decodes == 8
    second, _ = reader.history(1)
    assert second[0] is first[1] and reader.decodes == 9
    assert reader.bytes == reader.max_bytes
    with pytest.raises(ValueError, match="Sample index"):
        reader.history(-1)
    reader.close()
    # Every original working byte is recoverable from the evidence archive.
    with zipfile.ZipFile(receipt["evidence_archive"]["path"]) as evidence_zip:
        for path in work.rglob("*"):
            if path.is_file():
                assert evidence_zip.read(path.relative_to(work).as_posix()) == path.read_bytes()
    archive.release_work(work, receipt, work.parents[1])
    assert not work.exists() and (destination/"training.zip").is_file()
    reader = archive.RGBFrameCache(destination/"receipt.json")
    assert reader.history(0)[0] == first
    reader.close()


def test_duplicate_action_targets_are_removed_across_packages(completed):
    work, destination = completed
    seen = set()
    first = archive.pack_segment(work, destination, "segment", seen=seen)
    second = archive.pack_segment(work, destination.with_name("overlap"), "overlap", seen=seen)
    assert first["sample_count"] == 3 and len(seen) == 3
    assert second["sample_count"] == 0 and second["duplicate_count"] == 3


def test_wrong_resolution_and_mutated_source_pixels_fail_closed(completed):
    work, destination = completed
    frame = next((work/"render/frames").glob("*.tga"))
    frame.write_bytes(tga(320, 180))
    with pytest.raises(ValueError, match="identity changed"):
        archive.pack_segment(work, destination, "segment")
    assert work.exists() and not (destination/"receipt.json").exists()


def test_changed_archive_blocks_loading_and_raw_release(completed):
    work, destination = completed
    receipt = archive.pack_segment(work, destination, "segment")
    with (destination/"training.zip").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="archive changed"):
        archive.RGBFrameCache(destination/"receipt.json")
    with pytest.raises(ValueError, match="Archive changed"):
        archive.release_work(work, receipt, work.parents[1])
    assert work.exists()


def test_cleanup_cannot_escape_queue_root(completed):
    work, destination = completed
    receipt = archive.pack_segment(work, destination, "segment")
    with pytest.raises(ValueError, match="escapes"):
        archive.release_work(work, receipt, destination)
    assert work.exists()


def test_session_frames_are_archived_once_and_shared_archive_guards_cleanup(completed):
    from cs2_data.session_profile import PROFILE
    work, destination = completed
    common = work.parents[1]/"session-packages/session"
    common.mkdir(parents=True)
    shared_zip = common/"evidence.zip"
    members, references = {}, {}
    state = read_json(work/"batch/batch_state.json")
    with zipfile.ZipFile(shared_zip, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in (work/"render/frames").glob("*.tga"):
            member = "raw/run/"+path.name
            digest = sha256_file(path)
            output.write(path, member)
            members[member] = {"sha256": digest}
            references[path.relative_to(work).as_posix()] = {"member": member, "sha256": digest}
            state["jobs"]["segment"]["stages"]["render"][-1]["files"][str(path)] = digest
        output.writestr("archive_index.json", json.dumps({"members": members}))
    archive.save_settings(work/"batch/batch_state.json", state)
    receipt_path = common/"receipt.json"
    write_json(receipt_path, {"profile": PROFILE, "status": "compressed",
        "archive": {"path": str(shared_zip), "sha256": sha256_file(shared_zip)}})
    write_json(work/"shared-session.json", {"receipt": str(receipt_path),
        "receipt_sha256": sha256_file(receipt_path), "frames": references})
    receipt = archive.pack_segment(work, destination, "segment", evidence_retention="full")
    with zipfile.ZipFile(receipt["evidence_archive"]["path"]) as output:
        assert not any(name.endswith('.tga') for name in output.namelist())
        index = json.loads(output.read("archive_index.json"))
        assert index['shared_session']['frames'] == references
    assert receipt['sample_count'] == 3
    with shared_zip.open('ab') as output: output.write(b'changed')
    with pytest.raises(ValueError, match='Shared recording archive changed'):
        archive.release_work(work, receipt, work.parents[1])
    assert work.exists()


def test_lean_default_keeps_identical_training_bytes_and_labels_after_cleanup(completed):
    work, destination = completed
    full = archive.pack_segment(work, destination.with_name("debug"), "segment", evidence_retention="full")
    lean = archive.pack_segment(work, destination, "segment")
    assert lean["evidence_retention"] == "lean" and "evidence_archive" not in lean
    assert set(p.name for p in destination.iterdir()) == {"training.zip", "receipt.json"}
    assert lean["compressed_bytes"] == (destination/"training.zip").stat().st_size
    assert lean["compressed_bytes"] < full["compressed_bytes"]
    with zipfile.ZipFile(full["training_archive"]["path"]) as a, zipfile.ZipFile(destination/"training.zip") as b:
        assert a.namelist() == b.namelist()
        for name in a.namelist():
            assert a.read(name) == b.read(name)
        summary = json.loads(b.read("manifest.json"))["acceptance_summary"]
        assert summary["accepted_count"] == 3 and summary["proof_sha256"]
    archive.release_work(work, lean, work.parents[1])
    assert not work.exists()
    reader = archive.RGBFrameCache(destination/"receipt.json")
    try:
        assert [frame[:3] for frame in reader.history(2)[0]] == [bytes((i, 40, 200)) for i in range(2, 10)]
    finally:
        reader.close()


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_lean_packaging_still_checks_temporary_validation_evidence(completed, mutation):
    work, destination = completed
    evidence = work/"acceptance/check.txt"
    evidence.write_text("verified")
    state_path = work/"batch/batch_state.json"
    state = read_json(state_path)
    state["jobs"]["segment"]["stages"]["acceptance"][-1]["files"][str(evidence)] = sha256_file(evidence)
    archive.save_settings(state_path, state)
    if mutation == "changed":
        evidence.write_text("changed")
    else:
        evidence.unlink()
    with pytest.raises(ValueError, match="Verified evidence"):
        archive.pack_segment(work, destination, "segment")
    assert work.exists() and not (destination/"receipt.json").exists()


def test_legacy_receipt_cannot_silently_drop_evidence(completed):
    work, destination = completed
    receipt = archive.pack_segment(work, destination, "segment", evidence_retention="full")
    receipt.pop("evidence_retention")
    receipt["schema_version"] = 1
    (destination/"evidence.zip").unlink()
    with pytest.raises(FileNotFoundError):
        archive.release_work(work, receipt, work.parents[1])
    assert work.exists()
