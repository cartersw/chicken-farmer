"""Display reports bind small metadata while keeping training evidence untouched."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import competitive_batch as batch
from cs2_data import round_report as report
from cs2_data import hud_policy
from cs2_data.io import sha256_file


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return sha256_file(path)


@pytest.fixture
def collection(tmp_path):
    root = tmp_path/"ordinary progression"
    root.mkdir()
    batches, jobs = [], []
    for number, rows in enumerate(([(4, 2000)], [(3, 1000), (3, 1032)])):
        folder = root/"batches"/f"{number:03d}"/"batch"
        items = []
        for round_id, start in rows:
            job_id = f"{start:024x}"
            job = {"clip_id": job_id, "demo_id": "d"*64, "round_id": round_id, "steam_id": "123",
                   "player_slot": 1, "start_demo_tick": start, "end_demo_tick": start+32,
                   "fps": 32, "width": 1280, "height": 720, "competitive_replay_profile": batch.CURRENT_PROFILE}
            spec = folder/"jobs"/(job_id+".json")
            digest = write(spec, job)
            items.append({"job_id": job_id, "job": job, "source_id": "demo", "spec": str(spec), "spec_sha256": digest})
            jobs.append((folder, items[-1]))
        plan = {"schema_version": 1, "profile": batch.PROFILE, "stage_order": list(batch.STAGES),
                "clip_ticks": 32, "jobs": items}
        path = folder/"batch_plan.json"
        digest = write(path, plan)
        batches.append({"plan": str(path), "plan_sha256": digest, "clip_ticks": 32,
                        "job_count": len(items), "planned_source_seconds": len(items)/2})
        write(folder/"batch_state.json", {"schema_version": 1, "profile": batch.PROFILE,
              "plan_sha256": digest, "jobs": {item["job_id"]: {"stages": {stage: [] for stage in batch.STAGES}} for item in items}})
    windows = [{"round_id": 3, "start_demo_tick": 1000, "end_demo_tick": 1065, "eligible_ticks": 65,
                "planned_intervals": [[1000, 1032], [1032, 1064]], "planned_ticks": 64,
                "excluded_intervals": [{"start_demo_tick": 1064, "end_demo_tick": 1065, "reason": "odd_final_tick"}]},
               {"round_id": 4, "start_demo_tick": 2000, "end_demo_tick": 2032, "eligible_ticks": 32,
                "planned_intervals": [[2000, 2032]], "planned_ticks": 32, "excluded_intervals": []}]
    manifest = {"schema_version": 1, "profile": report.COLLECTION_PROFILE, "steam_id": "123", "round_ids": [3, 4],
                "source": {"demo_id": "d"*64}, "batches": batches, "windows": windows, "job_count": 3,
                "planned_source_seconds": 1.5, "coverage": {"eligible_ticks": 97, "planned_ticks": 96,
                    "excluded_ticks": 1, "history_images": 8, "missing_initial_history_positions_per_clip": 7,
                    "cross_clip_history_stitching": False}}
    write(root/"round_collection.json", manifest)
    return {"root": root, "manifest": manifest, "jobs": jobs}


def capture(collection, index=1, *, hud=True):
    folder, item = collection["jobs"][index]
    job = item["job"]
    state = json.loads((folder/"batch_state.json").read_text())
    stages = state["jobs"][item["job_id"]]["stages"]
    out = folder/"runs"/item["job_id"]/"render"/"attempt-001"
    out.mkdir(parents=True)
    video = out/(item["job_id"]+"-actual capture & 1.mp4")
    video.write_bytes(b"fixture video")
    frames = []
    for number in range(16):
        path = out/"frames"/f"actual-{number:03d}.tga"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"fixture frame")
        frames.append({"capture_index": number, "archived_name": path.name, "sha256": sha256_file(path)})
    inventory = out/"capture_frame_files.json"
    inventory_hash = write(inventory, {"frames": frames})
    render = {**hud_policy._expected_setup(), "render_status": "video_ready_timing_unverified", "source_job": deepcopy(job),
              **{key: job[key] for key in ("demo_id", "round_id", "steam_id", "player_slot", "fps", "width", "height")},
              "requested_start_demo_tick": job["start_demo_tick"], "requested_end_demo_tick": job["end_demo_tick"],
              "clip_id": video.stem, "num_frames": 16, "capture_frame_files": inventory.name,
              "capture_frame_files_sha256": inventory_hash, "video_uri": video.name, "video_sha256": sha256_file(video),
              "settings_restored": True, "gameinfo_restored": True, "staged_plugin_removed_from_game": True}
    render_path = out/(video.stem+".render.json")
    write(render_path, render)
    write(out/"settings-recovery.json", {"state": "restored"})
    write(out/"gameinfo-recovery.json", {"state": "restored"})
    attempt = {"attempt": 1, "status": "completed", "out": str(out),
               "result": {"status": "verified_render_artifacts", "render_manifest": str(render_path), "frame_count": 16},
               "files": {str(path): sha256_file(path) for path in out.rglob("*") if path.is_file()}}
    stages["render"].append(attempt)
    stages["process"].append({"attempt": 1, "status": "completed",
                              "out": str(folder/"runs"/item["job_id"]/"process"/"attempt-001")})
    if hud:
        hud_out = folder/"runs"/item["job_id"]/"hud_review"/"attempt-001"
        hud_out.mkdir(parents=True)
        index_path = hud_out/"index.html"
        index_path.write_text("<html>Review all original frames</html>")
        bundle_path = hud_out/"hud_review_bundle.json"
        write(bundle_path, {"profile": "cs2-competitive-hud-review-bundle-v1", "render_dir": str(out),
              "clip_id": video.stem, "review_index": {"profile": "cs2-hud-static-review-index-v1",
              "path": str(index_path), "sha256": sha256_file(index_path)}})
        stages["hud_review"].append({"attempt": 1, "status": "completed", "out": str(hud_out),
              "result": {"status": "pending_visual_review", "bundle": str(bundle_path)},
              "files": {str(path): sha256_file(path) for path in hud_out.iterdir()}})
    write(folder/"batch_state.json", state)
    fixture = {"folder": folder, "item": item, "state": state, "render": render, "render_path": render_path,
               "attempt": attempt, "out": out, "video": video, "inventory": inventory}
    synchronization(fixture)
    return fixture


def synchronization(fixture, *, unavailable=None):
    out = fixture["out"].parents[1]/"synchronization"/"attempt-001"
    path = out/"synchronization_audit.json"
    frames = [{"event": "movie_frame", "frame_index": index,
               "status": "unavailable" if index == unavailable else "matched_message_clocks",
               "reason_codes": ["net_tick_source_unavailable_or_ambiguous"] if index == unavailable else []}
              for index in range(16)]
    frames.append({"event": "movie_end", "frame_index": 16, "status": "matched_message_clocks", "reason_codes": []})
    status = "unavailable" if unavailable is not None else "matched_message_clocks"
    digest = write(path, {"producer": "cs2-synchronization-audit-v1", "status": "complete",
        "clip_id": fixture["render"]["clip_id"], "num_frames": 16, "native_profile": batch.CURRENT_PROFILE,
        "inputs": {"capture_evidence": {"render_manifest_path": str(fixture["render_path"]),
                   "render_manifest_sha256": sha256_file(fixture["render_path"])}},
        "native_message_clock_audit": {"status": status, "frames": frames, "reason_codes": []}})
    attempt = {"attempt": 1, "out": str(out), "status": "completed", "files": {str(path): digest},
               "result": {"status": "verified_synchronization_artifacts", "native_clock_status": status}}
    fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]["synchronization"] = [attempt]
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    return path, attempt


def test_report_orders_round_chronology_and_exposes_every_gap(collection):
    capture(collection)
    result = report.summarize_round_collection(collection["root"])
    assert [row["start_demo_tick"] for row in result["clips"]] == [1000, 1032, 2000]
    assert [row["round_id"] for row in result["rounds"]] == [3, 4]
    assert result["planned_source_seconds"] == 1.5 and result["captured_source_seconds"] == .5
    assert result["planned_frames"] == 48 and result["captured_frames"] == 16
    assert result["job_status_counts"] == {"ready_for_acceptance": 1, "unrecorded": 2}
    assert [(row["start_demo_tick"], row["end_demo_tick"]) for row in result["source_gaps"]] == [(1064, 1065), (1065, 2000)]
    clip = result["clips"][0]
    assert clip["video_filename"].endswith("-actual capture & 1.mp4")
    assert "actual%20capture%20%26%201.mp4" in clip["video_url"]
    assert clip["review_url"].endswith("/hud_review/attempt-001/index.html")
    assert clip["restoration_claims_recorded"]["settings_journal_state"] == "restored"
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "actual%20capture%20%26%201.mp4" in page and "Cross-clip history is not stitched" in page
    assert "Source round 3 · 0:00–0:00.5" in page and "Restoration recorded" in page
    assert "settings_journal_state" not in page and "Report generated" in page


def test_report_never_accepts_or_modifies_any_evidence_or_hashes_media(collection, monkeypatch):
    fixture = capture(collection)
    stages = fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]
    stages["acceptance"] = [{"attempt": 1, "status": "completed",
         "out": str(fixture["folder"]/"runs"/fixture["item"]["job_id"]/"acceptance"/"attempt-001"),
         "result": {"training_ready": True, "accepted_count": 900}}]
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    before = {path: path.read_bytes() for path in collection["root"].rglob("*") if path.is_file()}
    original = report.sha256_file
    def small_hash(path):
        assert Path(path).suffix not in (".mp4", ".tga", ".dem", ".parquet")
        return original(path)
    monkeypatch.setattr(report, "sha256_file", small_hash)
    result = report.summarize_round_collection(collection["root"], include_disk_usage=True)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert result["acceptance_evaluated"] is False and result["training_ready"] is False
    assert result["model_training_performed"] is False
    assert result["clips"][0]["training_ready"] is False and result["clips"][0]["status"] == "ready_for_acceptance"
    assert result["accepted_sample_count_recorded"] == 0
    assert result["collection_disk_bytes"] > 0
    report.summarize_round_collection(collection["root"])


@pytest.mark.parametrize("change", ["plan", "journal", "collection_profile", "batch_escape", "journal_escape", "coverage"])
def test_stale_or_escaping_collection_evidence_is_rejected(collection, change, tmp_path):
    root = collection["root"]
    manifest = collection["manifest"]
    folder, item = collection["jobs"][0]
    if change == "plan":
        (folder/"batch_plan.json").write_text("{}")
    elif change == "journal":
        state = json.loads((folder/"batch_state.json").read_text())
        state["plan_sha256"] = "0"*64
        write(folder/"batch_state.json", state)
    elif change == "collection_profile":
        manifest["profile"] = "unrelated"
        write(root/"round_collection.json", manifest)
    elif change == "batch_escape":
        escaped = tmp_path/"batch_plan.json"
        escaped.write_bytes((folder/"batch_plan.json").read_bytes())
        manifest["batches"][0]["plan"] = str(escaped)
        write(root/"round_collection.json", manifest)
    elif change == "journal_escape":
        state = json.loads((folder/"batch_state.json").read_text())
        state["jobs"][item["job_id"]]["stages"]["render"] = [{"attempt": 1, "out": str(tmp_path/"outside"), "status": "completed"}]
        write(folder/"batch_state.json", state)
    else:
        manifest["coverage"]["planned_ticks"] += 2
        write(root/"round_collection.json", manifest)
    with pytest.raises(ValueError):
        report.summarize_round_collection(root)
    assert not (root/"round_report.json").exists()


@pytest.mark.parametrize("change", ["running", "failed_retry", "stale_manifest", "video_escape", "inventory_escape", "missing_video", "missing_frame"])
def test_only_completed_bound_render_is_counted(collection, change, tmp_path):
    fixture = capture(collection, hud=False)
    if change == "running":
        fixture["attempt"]["status"] = "running"
    elif change == "failed_retry":
        fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]["render"].append({
            "attempt": 2, "status": "failed", "out": str(fixture["out"].parent/"attempt-002"), "error": "fixture failure"})
    elif change == "stale_manifest":
        fixture["render_path"].write_text("{}")
    elif change in ("video_escape", "inventory_escape"):
        key = "video_uri" if change == "video_escape" else "capture_frame_files"
        fixture["render"][key] = str(tmp_path/"outside.mp4")
        fixture["attempt"]["files"][str(fixture["render_path"])] = write(fixture["render_path"], fixture["render"])
    elif change == "missing_video":
        fixture["video"].unlink()
    else:
        next((fixture["out"]/"frames").glob("*.tga")).unlink()
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    result = report.summarize_round_collection(collection["root"])
    assert result["captured_frames"] == 0 and result["captured_source_seconds"] == 0
    assert not any(row.get("video_url") for row in result["clips"])


def test_stale_review_index_is_not_linked_and_error_is_escaped(collection):
    fixture = capture(collection)
    stages = fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]
    hud = stages["hud_review"][-1]
    (Path(hud["out"])/"index.html").write_text("changed")
    result = report.summarize_round_collection(collection["root"])
    assert result["captured_frames"] == 16  # Independently valid render remains counted.
    assert result["clips"][0]["status"] == "ready_for_acceptance" and "review_url" not in result["clips"][0]
    assert result["clips"][0]["optional_review_unavailable_reason"]
    stages["render"][-1].update(status="failed", error="<script>unsafe & wrong</script>")
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    report.summarize_round_collection(collection["root"])
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "<script>unsafe" not in page and "&lt;script&gt;unsafe &amp; wrong&lt;/script&gt;" in page


def test_report_output_cannot_overwrite_other_evidence(collection):
    with pytest.raises(ValueError, match="collection directory"):
        report.summarize_round_collection(collection["root"], out=collection["jobs"][0][0])


def test_saved_clock_exception_is_visible_without_discarding_capture_or_accepting_it(collection):
    fixture = capture(collection)
    synchronization(fixture, unavailable=5)
    result = report.summarize_round_collection(collection["root"])
    clip = result["clips"][0]
    assert result["captured_frames"] == 16 and clip["status"] == "ready_for_acceptance"
    assert result["recorded_timing_exception_clips"] == 1 and result["recorded_timing_exception_frames"] == 1
    assert result["synchronization_status_counts_recorded"] == {"unavailable": 1, "unrecorded": 2}
    assert clip["synchronization_recorded"]["matched_captured_frames"] == 15
    assert clip["synchronization_recorded"]["unavailable_frame_indices"] == [5]
    assert clip["synchronization_recorded"]["reason_codes"] == ["net_tick_source_unavailable_or_ambiguous"]
    assert clip["training_ready"] is False and result["acceptance_evaluated"] is False
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "Timing review needed:" in page and "Unavailable for 1 frame(s)" in page


def test_saved_matched_clock_is_labeled_as_recorded_evidence(collection):
    fixture = capture(collection)
    synchronization(fixture)
    result = report.summarize_round_collection(collection["root"])
    assert result["recorded_timing_exception_clips"] == 0
    assert result["clips"][0]["synchronization_recorded"]["matched_captured_frames"] == 16
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "Matched in saved audit" in page and "does not verify training acceptance" in page


@pytest.mark.parametrize("change", ["stale_audit", "different_render", "false_matched_summary"])
def test_clock_details_require_bound_consistent_current_metadata(collection, change):
    fixture = capture(collection)
    path, attempt = synchronization(fixture, unavailable=5)
    audit = json.loads(path.read_text())
    if change == "different_render":
        audit["inputs"]["capture_evidence"]["render_manifest_sha256"] = "0"*64
    elif change == "false_matched_summary":
        audit["native_message_clock_audit"]["status"] = "matched_message_clocks"
        attempt["result"]["native_clock_status"] = "matched_message_clocks"
    else:
        audit["num_frames"] = 900
    digest = write(path, audit)
    if change != "stale_audit":
        attempt["files"][str(path)] = digest
        write(fixture["folder"]/"batch_state.json", fixture["state"])
    result = report.summarize_round_collection(collection["root"])
    assert result["clips"][0]["status"] == "failed" and "synchronization_recorded" not in result["clips"][0]
    assert result["captured_frames"] == 16 and result["training_ready"] is False


def test_trusted_setup_preserves_historical_review_status_without_image_checks(collection, monkeypatch):
    capture(collection)
    monkeypatch.setattr(batch, "_verify_render", lambda *args: pytest.fail("Report must not revalidate image bytes"))
    result = report.summarize_round_collection(collection["root"])
    clip = result["clips"][0]
    assert clip["hud_stage_result_recorded"] == "pending_visual_review"
    assert clip["hud_policy_current"]["status"] == "trusted_capture_setup"
    assert clip["hud_policy_current"]["visual_review_performed"] is False
    assert clip["status"] == "ready_for_acceptance" and clip["training_ready"] is False
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "Ready for acceptance" in page and "Optional frame viewer" in page
    assert "no manual HUD review or automated overlay scan" in page


def test_unknown_setup_is_not_assumed_trusted_from_legacy_review(collection):
    fixture = capture(collection)
    fixture["render"]["renderer_profile"] = "different"
    fixture["attempt"]["files"][str(fixture["render_path"])] = write(fixture["render_path"], fixture["render"])
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    synchronization(fixture)
    clip = report.summarize_round_collection(collection["root"])["clips"][0]
    assert clip["status"] == "unsupported_capture_setup" and clip["captured_frames"] == 16
    assert clip["hud_policy_current"]["status"] == "unsupported_capture_setup"


@pytest.mark.parametrize("tampered", [False, True])
def test_new_setup_receipt_needs_no_review_bundle(collection, tampered):
    fixture = capture(collection, hud=False)
    out = fixture["out"].parents[1]/"hud_review"/"attempt-001"
    path = out/"hud_policy.json"
    receipt = {"schema_version": 1, "profile": "cs2-batch-hud-setup-policy-v1",
        "render_manifest": str(fixture["render_path"]), "render_manifest_sha256": sha256_file(fixture["render_path"]),
        "capture_ledger_sha256": fixture["render"].get("capture_ledger_sha256"),
        "capture_frame_files_sha256": fixture["render"]["capture_frame_files_sha256"],
        "clip_id": "different" if tampered else fixture["render"]["clip_id"],
        "policy": hud_policy.trusted_hud_policy(fixture["render"]), "training_ready": False}
    digest = write(path, receipt)
    stages = fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]
    stages["hud_review"] = [{"attempt": 1, "status": "completed", "out": str(out), "files": {str(path): digest},
                            "result": {"status": "hud_setup_trusted", "hud_policy_receipt": str(path)}}]
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    clip = report.summarize_round_collection(collection["root"])["clips"][0]
    assert clip["status"] == ("failed" if tampered else "ready_for_acceptance")
    assert "review_url" not in clip and clip["captured_frames"] == 16
    assert ("hud_policy_receipt_recorded" in clip) is not tampered


def test_actual_recorded_acceptance_counts_are_displayed_without_reevaluation(collection):
    fixture = capture(collection)
    out = fixture["out"].parents[1]/"acceptance"/"attempt-001"
    path = out/"competitive_acceptance.json"
    digest = write(path, {"schema_version": 1, "profile": "cs2-competitive-masked-control-acceptance-v2",
                          **{key: fixture["render"][key] for key in ("demo_id", "round_id", "steam_id", "player_slot")},
                          "status": "complete", "clip_id": fixture["render"]["clip_id"],
                          "candidate_count": 16, "accepted_count": 8, "rejected_count": 8})
    stages = fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]
    stages["acceptance"] = [{"attempt": 1, "status": "completed", "out": str(out), "files": {str(path): digest},
        "result": {"status": "accepted_partition_verified", "acceptance": str(path), "accepted_count": 8, "rejected_count": 8}}]
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    result = report.summarize_round_collection(collection["root"])
    assert result["clips"][0]["status"] == "accepted_recorded"
    assert result["accepted_sample_count_recorded"] == result["rejected_sample_count_recorded"] == 8
    assert result["acceptance_evaluated"] is False and result["training_ready"] is False


@pytest.mark.parametrize("stage", ["process", "synchronization", "hud_review"])
@pytest.mark.parametrize("state", ["unrecorded", "running"])
def test_trusted_capture_is_processing_until_all_acceptance_prerequisites_complete(collection, stage, state):
    fixture = capture(collection)
    stages = fixture["state"]["jobs"][fixture["item"]["job_id"]]["stages"]
    if state == "unrecorded":
        stages[stage] = []
    else:
        stages[stage][-1]["status"] = "running"
    # A completed acceptance claim cannot override incomplete prerequisites or
    # cause the report to read/count a saved partition out of stage order.
    stages["acceptance"] = [{"attempt": 1, "status": "completed",
        "out": str(fixture["out"].parents[1]/"acceptance"/"attempt-001"),
        "result": {"status": "accepted_partition_verified", "accepted_count": 100}}]
    write(fixture["folder"]/"batch_state.json", fixture["state"])
    result = report.summarize_round_collection(collection["root"])
    clip = result["clips"][0]
    assert clip["status"] == "processing" and clip["captured_frames"] == 16
    assert clip["hud_policy_current"]["status"] == "trusted_capture_setup"
    assert clip["stage_statuses_recorded"][stage] == state
    assert "acceptance_recorded" not in clip and result["accepted_sample_count_recorded"] == 0
    page = (collection["root"]/"index.html").read_text(encoding="utf-8")
    assert "Processing" in page and "Ready for acceptance" not in page
