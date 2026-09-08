"""Budget and resume checks never replace the protected batch lifecycle."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2_data import round_session as session
from cs2_data import desktop
from test_launcher_backend import write


@pytest.fixture
def collection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"source")
    plan = tmp_path / "batches/first/batch_plan.json"
    write(plan, {"fixture": True})
    document = {"schema_version": 1, "profile": session.PROFILE, "steam_id": "123",
        "planned_source_seconds": 40, "storage_budget_bytes": 60_000_000_000,
        "free_space_floor_bytes": 100_000_000_000, "time_budget_seconds": 5400,
        "source_files": {str(source): session.batch.sha256_file(source)},
        "batches": [{"plan": str(plan), "plan_sha256": session.batch.sha256_file(plan), "clip_ticks": 1280}]}
    write(tmp_path / "round_collection.json", document)
    monkeypatch.setattr(desktop, "application_lock", lambda _: nullcontext())
    monkeypatch.setattr(session.shutil, "disk_usage", lambda _: SimpleNamespace(free=1_000_000_000_000))
    return tmp_path, document


def result(done=False, failure=None):
    rows = [{"status": "pending_visual_review", "job_id": "1"},
            {"status": failure or ("pending_visual_review" if done else "execution_budget_reached"), "job_id": "2", "stage": "render"}]
    return {"jobs": rows, "job_status_counts": {}, "performance": {"stage_measurements": {
        key: {"perform_calls": 1 if key == "render" else 0} for key in session.batch.STAGES}}}


def test_inspection_is_read_only(collection, monkeypatch):
    root, doc = collection
    monkeypatch.setattr(session.batch, "run_batch", lambda *a, **kw: pytest.fail("Read-only inspection must not run"))
    before = set(root.rglob("*"))
    assert session.run_session(root)["status"] == "planned_not_executed"
    assert set(root.rglob("*")) == before


def test_single_trial_then_resume_preserves_budget_and_uses_protected_runner(collection, monkeypatch):
    root, doc = collection
    calls = []

    def run(path, **kw):
        calls.append(kw)
        return result(done=len(calls) == 2)

    monkeypatch.setattr(session.batch, "run_batch", run)
    first = session.run_session(root, execute=True, max_new_clips=1, plugin=Path("p.dll"))
    assert first["status"] == "paused" and first["invocations"][0]["new_captures"] == 1
    second = session.run_session(root, execute=True, max_new_clips=10, plugin=Path("p.dll"))
    assert second["status"] == "captured_pending_review" and second["training_ready"] is False
    assert len(second["invocations"]) == 2 and second["active_seconds"] >= first["active_seconds"]
    assert all(kw == {"execute": True, "max_jobs": 1, "plugin": Path("p.dll")} for kw in calls)


@pytest.mark.parametrize("reason", ["storage", "disk", "time"])
def test_budget_stops_before_another_capture(collection, monkeypatch, reason):
    root, doc = collection
    if reason == "storage":
        doc["storage_budget_bytes"] = 1
    elif reason == "disk":
        monkeypatch.setattr(session.shutil, "disk_usage", lambda _: SimpleNamespace(free=doc["free_space_floor_bytes"]))
    else:
        doc["time_budget_seconds"] = 1
        ticks = iter(range(100))
        monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks))
    write(root / "round_collection.json", doc)
    monkeypatch.setattr(session.batch, "run_batch", lambda *a, **kw: pytest.fail("Budget must prevent capture"))
    assert session.run_session(root, execute=True)["status"] == "budget_reached"


def test_stop_finishes_current_clip_and_never_starts_next(collection, monkeypatch):
    root, doc = collection
    completed = []

    def run(*args, **kw):
        session.request_stop(root)
        completed.append(True)
        return result()

    monkeypatch.setattr(session.batch, "run_batch", run)
    state = session.run_session(root, execute=True)
    assert state["status"] == "stopped" and completed == [True]
    assert session.run_session(root, execute=True)["status"] == "stopped"
    assert completed == [True]


def test_failure_never_automatically_retries_or_advances(collection, monkeypatch):
    root, _ = collection
    calls = []
    monkeypatch.setattr(session.batch, "run_batch", lambda *a, **kw: calls.append(True) or result(failure="recovery_required"))
    state = session.run_session(root, execute=True)
    assert state["status"] == "needs_attention" and calls == [True]


def test_changed_source_and_plan_cannot_be_resumed(collection, monkeypatch):
    root, doc = collection
    monkeypatch.setattr(session.batch, "run_batch", lambda *a, **kw: pytest.fail("Changed source must fail"))
    (root / "source").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        session.run_session(root, execute=True)
    (root / "source").write_bytes(b"source")
    Path(doc["batches"][0]["plan"]).write_bytes(b"changed plan")
    with pytest.raises(ValueError, match="plan changed"):
        session.run_session(root, execute=True)


def test_numerical_acceptance_finishes_without_requesting_visual_review(collection, monkeypatch):
    root, _ = collection
    summary = result(done=True)
    for row in summary["jobs"]:
        row["status"] = "accepted_partition_verified"
    monkeypatch.setattr(session.batch, "run_batch", lambda *a, **kw: summary)
    state = session.run_session(root, execute=True)
    assert state["status"] == "accepted_partitions_verified"
    assert state["training_ready"] is False
