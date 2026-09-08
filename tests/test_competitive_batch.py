"""Resume and scheduling invariants; game/proof providers are bounded fixtures."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2_data import competitive_batch as batch


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def sources(tmp_path, monkeypatch):
    entries, canonical = [], {}
    for number, name in enumerate(("dust2", "nuke")):
        root = tmp_path/name; root.mkdir()
        demo = root/"source.dem"; demo.write_bytes(b"PBDEMS2\0"+name.encode())
        demo_id = batch.sha256_file(demo)
        manifest = {"demo_id": demo_id, "sha256": demo_id, "source_path": str(demo), "tick_rate": 64,
                    "parse_status": "complete", "partial": False, "parser_schema_version": "2", "files": {}}
        for filename in ("rounds.parquet", "player_state.parquet", "usercmd.parquet"):
            path = root/filename; path.write_bytes((name+filename).encode()); manifest["files"][filename] = batch.sha256_file(path)
        write(root/"manifest.json", manifest); canonical[str(root)] = manifest
        for kind in ("phase_manifest", "network_clock", "state_context"):
            write(root/(kind+".json"), {"fixture_source": name, "kind": kind})
        entries.append({"source_id": name, "parsed": str(root), "demo": str(demo),
                        **{kind: str(root/(kind+".json")) for kind in ("phase_manifest", "network_clock", "state_context")}})
    document = tmp_path/"sources.json"
    write(document, {"schema_version": 1, "profile": batch.SOURCE_PROFILE, "sources": entries})
    from cs2_data import clock_evidence, validation
    monkeypatch.setattr(clock_evidence, "load_network_clock", lambda *a: SimpleNamespace(document={"windows": [{"start_demo_tick": 0, "end_demo_tick": 100000}]}))
    monkeypatch.setattr(validation, "load_state_context", lambda *a: {})
    calls = []
    def jobs(parsed, demo, out, **options):
        calls.append(options)
        manifest = canonical[str(parsed)]; rows = []
        for round_id in (3, 4):
            for player in (101, 102):
                if options.get("round_id", round_id) != round_id or options.get("steam_id", player) != player:
                    continue
                start = options.get("start_demo_tick", 3000+(round_id-3)*2000)
                end = options.get("end_demo_tick", start+960)
                rows.append({"schema_version": 1, "timing_clock": "demo_tick", "demo_id": manifest["demo_id"],
                    "demo_path": str(demo), "clip_id": f"source-{round_id}-{player}", "round_id": round_id,
                    "steam_id": str(player), "player_slot": player-100, "spectator_user_id": player-100,
                    "start_demo_tick": start, "end_demo_tick": end, "fps": 32, "width": 1280, "height": 720,
                    "phase_evidence": {"phase": "competitive", "phase_verified": True},
                    "command_coverage": {"complete_within_gap_limit": True}})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(row)+"\n" for row in rows))
        return {"job_count": len(rows)}
    monkeypatch.setattr(batch, "render_jobs", jobs)
    return {"document": document, "entries": entries, "out": tmp_path/"campaign", "calls": calls}


def plan(sources, **options):
    return batch.plan_batch(sources["document"], sources["out"], **options)


def test_planning_balances_sources_rounds_then_players_without_launch(sources):
    result = plan(sources, max_jobs=8)
    assert [(r["source_id"], r["job"]["round_id"], r["job"]["steam_id"]) for r in result["jobs"]] == [
        (source, round_id, player) for player in ("101", "102") for round_id in (3, 4) for source in ("dust2", "nuke")]
    assert all(r["job"]["end_demo_tick"]-r["job"]["start_demo_tick"] == 320 for r in result["jobs"])
    assert all(r["job"]["competitive_replay_profile"] == batch.CURRENT_PROFILE for r in result["jobs"])
    assert not (sources["out"]/"runs").exists()
    batch.load_batch_plan(sources["out"])


def test_requested_later_ticks_and_identity_survive_planning(sources):
    document = json.loads(sources["document"].read_text())
    document["sources"] = [document["sources"][0]]
    document["sources"][0]["selections"] = [{"start_demo_tick": 22300, "end_demo_tick": 22620, "round_id": 3, "steam_id": 102}]
    write(sources["document"], document)
    job = plan(sources)["jobs"][0]["job"]
    assert (job["start_demo_tick"], job["end_demo_tick"], job["steam_id"]) == (22300, 22620, "102")


@pytest.mark.parametrize("change", ["unknown_selection", "partial_interval", "unbounded_count", "odd_ticks"])
def test_invalid_or_unbounded_plan_is_rejected(sources, change):
    options = {}
    if change in ("unknown_selection", "partial_interval"):
        doc = json.loads(sources["document"].read_text())
        doc["sources"][0]["selections"] = [{"exec": "connect host"} if change == "unknown_selection" else {"start_demo_tick": 22300}]
        write(sources["document"], doc)
    elif change == "unbounded_count": options["max_jobs"] = 25
    else: options["clip_ticks"] = 319
    with pytest.raises(ValueError): plan(sources, **options)
    assert not sources["out"].exists()


def test_source_and_job_changes_are_rejected_before_execution(sources):
    result = plan(sources)
    Path(result["jobs"][0]["spec"]).write_text("{}")
    with pytest.raises(ValueError, match="bytes changed"):
        batch.run_batch(sources["out"], execute=True)
    assert not (sources["out"]/".batch.lock").exists()


def test_uncovered_clock_window_cannot_schedule_a_capture(sources, monkeypatch):
    from cs2_data import clock_evidence
    monkeypatch.setattr(clock_evidence, "load_network_clock", lambda *a: SimpleNamespace(document={"windows": [{"start_demo_tick": 8000, "end_demo_tick": 9000}]}))
    with pytest.raises(ValueError, match="network-clock coverage"):
        plan(sources)
    assert not (sources["out"]/"batch_plan.json").exists()


@pytest.fixture
def runner(sources, monkeypatch):
    planned = plan(sources, max_jobs=2)
    control = {"approved": False, "proof_generation": 0, "fail": None, "crash": None, "unsafe": False}
    calls, verifies = [], []
    def perform(stage, out, source, item, parents, options):
        calls.append((item["job_id"], stage, str(out)))
        out.mkdir(parents=True)
        write(out/"evidence.json", {"stage": stage, "source": source["source_id"], "valid": control["fail"] != stage,
                                   "proof_generation": control["proof_generation"]})
        if control["crash"] == stage:
            raise KeyboardInterrupt("fixture abrupt termination")
        if control["fail"] == stage:
            raise RuntimeError("fixture stage failure")
    def verify(stage, out, source, item, parents):
        verifies.append(stage)
        value = batch.read_json(out/"evidence.json")
        if not value["valid"] or value["source"] != source["source_id"]:
            raise ValueError("independent semantic verification failed")
        if stage == "hud_review":
            if control.get("trusted_setup"):
                return {"status": "hud_setup_trusted", "visual_review_performed": False, "training_ready": False}
            return {"status": "visual_review_verified" if control["approved"] else "pending_visual_review", "training_ready": False}
        if stage == "acceptance":
            if value["proof_generation"] != control["proof_generation"]:
                raise ValueError("proof implementation changed")
            return {"status": "accepted_partition_verified", "accepted_count": 153, "rejected_count": 7, "training_ready": True}
        return {"status": "verified_"+stage, "training_ready": False}
    def safe(stage, out):
        if control["unsafe"] and stage == "render" and out.exists():
            raise ValueError("Recovery required")
    monkeypatch.setattr(batch, "_perform_stage", perform)
    monkeypatch.setattr(batch, "_verify_stage", verify)
    monkeypatch.setattr(batch, "_retry_is_safe", safe)
    return {**sources, "plan": planned, "control": control, "calls": calls, "verifies": verifies}


def test_default_run_is_read_only_and_does_not_create_journals(runner):
    before = {str(path): path.read_bytes() for path in runner["out"].rglob("*") if path.is_file()}
    result = batch.run_batch(runner["out"])
    after = {str(path): path.read_bytes() for path in runner["out"].rglob("*") if path.is_file()}
    assert before == after and not runner["calls"]
    assert result["job_status_counts"] == {"planned": 2}


def test_render_audit_and_bundle_pause_before_any_unreviewed_acceptance(runner):
    first = batch.run_batch(runner["out"], execute=True)
    assert first["job_status_counts"] == {"pending_visual_review": 2}
    assert not any(stage == "acceptance" for _, stage, _ in runner["calls"])
    calls = list(runner["calls"])
    second = batch.run_batch(runner["out"], execute=True)
    assert {k: v for k, v in second.items() if k != "performance"} == {k: v for k, v in first.items() if k != "performance"}
    assert runner["calls"] == calls  # Every existing stage reverified; no new execution.
    assert all(row["perform_calls"] == 0 for row in second["performance"]["stage_measurements"].values())
    assert second["performance"]["stage_measurements"]["render"]["verify_calls"] == 2
    runner["control"]["approved"] = True
    third = batch.run_batch(runner["out"], execute=True)
    assert third["accepted_sample_count"] == 306 and third["rejected_sample_count"] == 14
    assert [stage for _, stage, _ in runner["calls"][len(calls):]] == ["acceptance", "acceptance"]


def test_changed_artifact_is_not_overwritten_or_used_to_continue(runner):
    batch.run_batch(runner["out"], execute=True)
    first = Path(runner["calls"][0][2])/"evidence.json"
    first.write_text("corrupt")
    calls = list(runner["calls"])
    result = batch.run_batch(runner["out"], execute=True)
    assert result["jobs"][0]["status"] == "artifact_changed"
    assert runner["calls"] == calls and first.read_text() == "corrupt"


def test_completed_proof_is_reissued_when_verifier_changes_without_rerender(runner):
    runner["control"]["approved"] = True
    batch.run_batch(runner["out"], execute=True)
    calls = list(runner["calls"]); runner["control"]["proof_generation"] += 1
    result = batch.run_batch(runner["out"], execute=True)
    assert result["accepted_sample_count"] == 306
    assert [stage for _, stage, _ in runner["calls"][len(calls):]] == ["acceptance", "acceptance"]
    assert all(Path(out).name == "attempt-002" for _, _, out in runner["calls"][len(calls):])


def test_complete_artifacts_after_abrupt_exit_are_verified_and_adopted(runner):
    runner["control"]["crash"] = "process"
    with pytest.raises(KeyboardInterrupt): batch.run_batch(runner["out"], execute=True)
    state = batch.read_json(runner["out"]/"batch_state.json")
    first = runner["plan"]["jobs"][0]["job_id"]
    assert state["jobs"][first]["stages"]["process"][0]["status"] == "running"
    runner["control"]["crash"] = None
    batch.run_batch(runner["out"], execute=True)
    assert sum(job == first and stage == "process" for job, stage, _ in runner["calls"]) == 1
    state = batch.read_json(runner["out"]/"batch_state.json")
    assert state["jobs"][first]["stages"]["process"][0]["recovered_after_interruption"] is True


def test_failed_stage_needs_explicit_retry_and_gets_fresh_attempt(runner):
    runner["control"]["fail"] = "process"
    batch.run_batch(runner["out"], execute=True, max_jobs=1)
    first_calls = list(runner["calls"]); runner["control"]["fail"] = None
    result = batch.run_batch(runner["out"], execute=True, max_jobs=1)
    assert result["jobs"][0]["status"] == "retry_required"
    assert not any(stage == "render" and job == first_calls[0][0] for job, stage, _ in runner["calls"][len(first_calls):])
    batch.run_batch(runner["out"], execute=True, retry_failed=True)
    attempts = [out for job, stage, out in runner["calls"] if job == first_calls[0][0] and stage == "process"]
    assert [Path(path).name for path in attempts] == ["attempt-001", "attempt-002"]
    assert batch.read_json(Path(attempts[0])/"evidence.json")["valid"] is False


def test_renderer_failure_stops_further_launches_and_requires_recovery(runner):
    runner["control"]["fail"] = "render"
    result = batch.run_batch(runner["out"], execute=True)
    assert len(runner["calls"]) == 1 and result["jobs"][1]["status"] == "execution_budget_reached"
    runner["control"]["unsafe"] = True
    with pytest.raises(ValueError, match="Recovery required"):
        batch.run_batch(runner["out"], execute=True, retry_failed=True)
    assert len(runner["calls"]) == 1


def test_per_invocation_job_limit_is_enforced(runner):
    result = batch.run_batch(runner["out"], execute=True, max_jobs=1)
    assert result["job_status_counts"] == {"pending_visual_review": 1, "execution_budget_reached": 1}
    assert len({job for job, _, _ in runner["calls"]}) == 1


def test_journal_path_escape_cannot_resume(runner, tmp_path):
    batch.run_batch(runner["out"], execute=True)
    state = batch.read_json(runner["out"]/"batch_state.json")
    next(iter(state["jobs"].values()))["stages"]["render"][0]["out"] = str(tmp_path/"outside")
    write(runner["out"]/"batch_state.json", state)
    with pytest.raises(ValueError, match="escapes|attempt"):
        batch.run_batch(runner["out"], execute=True)


@pytest.mark.parametrize("clip_ticks", [320, 640])
def test_native_dispatch_uses_existing_protected_worker_and_scoped_profile(sources, monkeypatch, clip_ticks):
    item = plan(sources, clip_ticks=clip_ticks)["jobs"][0]; captured = {}
    class Parser:
        def parse_args(self, arguments):
            captured["arguments"] = arguments; return "protected namespace"
    def validate(job, max_ticks):
        captured["validated"] = (job, max_ticks); return {**job, "effective": True}
    def run(args, job, original): captured["run"] = (args, job, original)
    monkeypatch.setattr(batch, "_worker", lambda: SimpleNamespace(argument_parser=Parser, validate_job=validate, run_capture=run))
    batch._perform_stage("render", sources["out"]/"future-run", {}, item, {}, {})
    assert "--execute" in captured["arguments"] and "--allow-version-mismatch" in captured["arguments"]
    assert captured["validated"][1] == clip_ticks and captured["run"][2] == item["job"]
    assert captured["arguments"][captured["arguments"].index("--max-ticks")+1] == str(clip_ticks)
    item["job"]["calibration_replay_profile"] = "unsupported"
    with pytest.raises(ValueError, match="explicit current"):
        batch._perform_stage("render", sources["out"]/"future-run", {}, item, {}, {})


def test_long_clip_plan_retains_source_window_and_never_crosses_its_boundary(sources):
    result = plan(sources, clip_ticks=640, max_jobs=8)
    assert len(result["jobs"]) == 8
    for item in result["jobs"]:
        job = item["job"]
        assert job["end_demo_tick"] - job["start_demo_tick"] == 640
        assert job["source_job_interval"][0] <= job["start_demo_tick"] < job["end_demo_tick"] <= job["source_job_interval"][1]
    batch.load_batch_plan(sources["out"])


@pytest.mark.parametrize("ticks", [1281, 1282, True, 640.0])
def test_long_clip_plan_remains_bounded(sources, ticks):
    with pytest.raises(ValueError, match="even 32..1280"):
        plan(sources, clip_ticks=ticks)


def test_trusted_setup_continues_to_acceptance_without_manual_approval(runner):
    runner["control"]["trusted_setup"] = True
    assert runner["control"]["approved"] is False
    report = batch.run_batch(runner["out"], execute=True)
    assert report["job_status_counts"] == {"accepted_partition_verified": 2}
    assert report["accepted_sample_count"] == 306
    assert sum(stage == "acceptance" for _, stage, _ in runner["calls"]) == 2


@pytest.fixture
def policy_stage(tmp_path, monkeypatch):
    from cs2_data import hud_policy, hud_review
    render_dir = tmp_path/"render"
    render = {"clip_id": "capture", "capture_ledger_sha256": "a"*64,
              "capture_frame_files_sha256": "b"*64}
    write(render_dir/"capture.render.json", render)
    # Policy compatibility has its own tests. This fixture checks batch
    # orchestration never calls the old expensive visual-review producers.
    policy = {"status": "trusted_capture_setup", "visual_review_performed": False}
    monkeypatch.setattr(hud_policy, "trusted_hud_policy", lambda _: dict(policy))
    monkeypatch.setattr(hud_policy, "policy_allows_capture", lambda value: value == policy)
    monkeypatch.setattr(hud_review, "prepare_hud_review", lambda *a, **k: pytest.fail("No automatic contact sheets"))
    monkeypatch.setattr(hud_review, "validate_hud_review_bundle", lambda *a, **k: pytest.fail("No recurring visual review"))
    return tmp_path/"hud", {"render": render_dir}, render


def test_routine_hud_stage_only_writes_small_policy_receipt(policy_stage):
    out, parents, _ = policy_stage
    batch._perform_stage("hud_review", out, {}, {}, parents, {})
    assert [path.name for path in out.iterdir()] == ["hud_policy.json"]
    assert (out/"hud_policy.json").stat().st_size < 5000
    result = batch._verify_stage("hud_review", out, {}, {}, parents)
    assert result["status"] == "hud_setup_trusted"
    assert result["visual_review_performed"] is False
    assert result["training_ready"] is False


def test_historical_bundle_does_not_require_another_visual_review(policy_stage):
    out, parents, render = policy_stage
    write(out/"hud_review_bundle.json", {**render, "render_dir": str(parents["render"]),
                                       "all_frames_reviewed": False})
    result = batch._verify_stage("hud_review", out, {}, {}, parents)
    assert result["status"] == "hud_setup_trusted"
    assert result["bundle"].endswith("hud_review_bundle.json")
    assert not (out/"hud_policy.json").exists()


@pytest.mark.parametrize("historical", [False, True])
def test_hud_stage_still_binds_metadata_to_the_correct_capture(policy_stage, historical):
    out, parents, render = policy_stage
    if historical:
        path = out/"hud_review_bundle.json"
        write(path, {**render, "render_dir": str(parents["render"]), "clip_id": "another"})
    else:
        batch._perform_stage("hud_review", out, {}, {}, parents, {})
        path = out/"hud_policy.json"
        receipt = batch.read_json(path)
        receipt["capture_ledger_sha256"] = "c"*64
        write(path, receipt)
    with pytest.raises(ValueError, match="another capture"):
        batch._verify_stage("hud_review", out, {}, {}, parents)
