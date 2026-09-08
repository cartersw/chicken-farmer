"""Opt-in real Tk smoke tests. No CS2 launch or real extraction is performed."""
import os
from pathlib import Path
import threading
import time

import pytest

if os.environ.get("CS2_TEST_GUI") != "1":
    pytest.skip("Set CS2_TEST_GUI=1 for a desktop session", allow_module_level=True)

from cs2_data import desktop
from cs2_data import launcher_backend as backend
from test_launcher_backend import write


def pump(root, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        root.update()
        if time.monotonic() >= deadline:
            raise AssertionError("UI operation timed out")
        time.sleep(.01)
    root.update_idletasks()


@pytest.fixture(scope="module")
def tk_root():
    # Match production: one Tcl/Tk interpreter for the process lifetime.
    root = desktop.tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tmp_path, monkeypatch, tk_root):
    root = tk_root
    project = tmp_path / "project"
    project.mkdir()
    app = desktop.DemoLauncher(root, project=project, auto_scan=False)
    errors = []
    monkeypatch.setattr(app, "error", errors.append)
    app.test_errors = errors
    yield app
    if app.busy:
        app.stop.set()
        pump(root, lambda: not app.busy)
    root.after_cancel(app._poll_token)
    for widget in root.winfo_children():
        widget.destroy()
    root.withdraw()


def populate(app):
    demo = app.project / "demos/match.dem"
    demo.parent.mkdir()
    demo.write_bytes(b"demo fixture")
    app.folder.set(str(demo.parent))
    app.scan()
    pump(app.root, lambda: not app.busy)
    app.demo_tree.selection_set("0")
    app.root.update()
    return demo


def make_plan(app):
    path = app.project / "batch/batch_plan.json"
    write(path, {"profile": "cs2-bounded-competitive-batch-v1", "clip_ticks": 640,
        "jobs": [{"job_id": "job", "source_id": "dust2", "job": {"map": "de_dust2", "round_id": 3,
                  "steam_id": 123, "start_demo_tick": 6000, "end_demo_tick": 6640}}]})
    return path


def test_scan_remains_responsive_and_populates_selection(ui):
    demo = populate(ui)
    assert len(ui.demo_tree.get_children()) == 1
    assert ui.selection_text.get() == str(demo.resolve())
    assert ui.status.get() == "Found 1 demos"
    assert not ui.test_errors
    assert backend.read_object(ui.settings_path)["demo_folder"] == str(demo.parent)
    ui.folder.set(str(demo.parent / "different"))
    assert not ui.demo_tree.get_children() and not ui.demos


def test_full_demo_requires_named_player_and_persists_decided_format(ui, monkeypatch):
    from cs2_data import full_demo
    demo = populate(ui)
    ui.enqueue_demo()
    assert ui.test_errors and not ui.queue_path().exists()
    ui.test_errors.clear()
    ui.players = {"Ckanic": "76561198323592528"}
    ui.player.set("Ckanic")
    ui.enqueue_demo()
    assert not ui.test_errors
    queued = full_demo.load_queue(ui.queue_path())["jobs"]
    assert len(queued) == 1 and queued[0]["demo"] == str(demo.resolve())
    assert queued[0]["format"] == full_demo.FORMAT
    assert ui.tabs.select() == str(ui.queue_tab)
    calls = []
    ui.validation_workers.set("3")
    def run(task, path, *, validation_workers):
        assert validation_workers == 3
        calls.append(path)
        task.emit("queue", str(path))
        return {"completed_demos": 0}
    monkeypatch.setattr(full_demo, "run_queue", run)
    ui.process_queue()
    assert ui.busy and ui.stop_button.instate(["!disabled"])
    pump(ui.root, lambda: not ui.busy)
    assert calls == [ui.queue_path()] and not ui.test_errors
    assert backend.read_object(ui.settings_path)["validation_workers"] == "3"
    ui.refresh_queue()
    assert len(ui.queue_tree.get_children()) == 1


def test_status_and_stop_stay_visible_at_minimum_window_size(ui):
    ui.root.geometry("1000x760")
    ui.root.deiconify()
    ui.root.update()
    for widget in (ui.stop_button, ui.progress):
        assert widget.winfo_ismapped()
        bottom = widget.winfo_rooty() + widget.winfo_height()
        assert bottom <= ui.root.winfo_rooty() + ui.root.winfo_height()
        assert widget.winfo_rootx() + widget.winfo_width() <= ui.root.winfo_rootx() + ui.root.winfo_width()
    ui.root.withdraw()


def test_queue_displays_concurrent_counts_and_rejects_invalid_worker_count(ui, monkeypatch):
    from cs2_data import full_demo
    demo = populate(ui)
    full_demo.enqueue(ui.queue_path(), demo, "76561198323592528", "Ckanic")
    doc = full_demo.load_queue(ui.queue_path())
    doc["jobs"][0].update(status="processing", pipeline={"recording": 1, "validating": 2,
        "validation_workers": 2, "waiting": 0, "compressing": 0, "in_flight": 3, "max_in_flight": 3})
    backend.save_settings(ui.queue_path(), doc)
    ui.refresh_queue()
    assert "Recording 1/1" in ui.queue_text.get() and "Validating 2/2" in ui.queue_text.get()
    assert "In progress 3/3" in ui.queue_text.get()
    monkeypatch.setattr(full_demo, "run_queue", lambda *a, **k: pytest.fail("Invalid worker count must not start a queue"))
    ui.validation_workers.set("99")
    ui.process_queue()
    assert "1 and 4" in ui.test_errors[-1] and not ui.busy


def test_prepare_dispatches_selected_files_and_preserves_no_training_status(ui, monkeypatch):
    demo = populate(ui)
    calls = []

    def prepare(task, demos, series):
        calls.append((demos, series))
        return task.run_dir / "sources.json"

    monkeypatch.setattr(backend, "prepare_sources", prepare)
    ui.series.set("one-series")
    ui.prepare(False)
    assert ui.busy and ui.stop_button.instate(["!disabled"])
    pump(ui.root, lambda: not ui.busy)
    assert calls == [([demo.resolve()], "one-series")]
    assert ui.status.get() == "Source preparation finished"
    assert backend.read_object(ui.run_dir / "run.json")["training_ready"] is False
    assert backend.read_object(ui.settings_path)["last_run"] == str(ui.run_dir)
    assert ui.progress["value"] == 0


def test_capture_pending_review_is_displayed_without_claiming_acceptance(ui, monkeypatch):
    path = make_plan(ui)
    ui.load_batch(path)

    def capture(task, plan):
        result = {"plan_sha256": backend.hash_file(plan), "job_status_counts": {"pending_visual_review": 1},
                  "jobs": [{"job_id": "job", "status": "pending_visual_review"}], "accepted_sample_count": 0}
        write(plan.parent / "batch_summary.json", result)
        return result

    monkeypatch.setattr(backend, "run_next_capture", capture)
    ui.capture()
    pump(ui.root, lambda: not ui.busy)
    assert "see recorded batch status" in ui.status.get()
    assert ui.batch_tree.item("job", "values")[-1] == "historical visual review pending"
    assert "accepted samples: 0" in ui.batch_text.get()


def test_close_waits_for_current_task_and_suppresses_second_operation(ui, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    destroyed = []
    real_destroy = ui.root.destroy
    monkeypatch.setattr(ui.root, "destroy", lambda: destroyed.append(True))

    def operation(_):
        entered.set()
        assert release.wait(5)
        return {}

    ui._start("test", operation, tracked=False)
    assert entered.wait(2)
    ui._start("second", lambda _: pytest.fail("Second task started"), tracked=False)
    ui.close()
    assert ui.busy and ui.stop.is_set() and not destroyed
    release.set()
    pump(ui.root, lambda: not ui.busy)
    assert destroyed == [True]
    monkeypatch.setattr(ui.root, "destroy", real_destroy)


def test_application_lock_rejects_duplicate_then_releases(tmp_path):
    path = tmp_path / "app.lock"
    with desktop.application_lock(path):
        with pytest.raises(RuntimeError, match="already open"):
            with desktop.application_lock(path):
                pytest.fail("Second launcher acquired the same lock")
    with desktop.application_lock(path):
        pass


def test_invalid_batch_preserves_loaded_batch_and_selection(ui):
    original = make_plan(ui)
    ui.load_batch(original)
    ui.batch_tree.selection_set("job")
    previous_text = ui.batch_text.get()
    invalid = ui.project / "invalid/batch_plan.json"
    plan = backend.read_object(original)
    plan["jobs"].append(plan["jobs"][0].copy())
    write(invalid, plan)
    with pytest.raises(ValueError, match="duplicate"):
        ui.load_batch(invalid)
    assert ui.batch == original
    assert ui.plan_path.get() == str(original)
    assert ui.batch_tree.get_children() == ("job",)
    assert ui.batch_tree.selection() == ("job",)
    assert ui.batch_text.get() == previous_text


def test_batch_refresh_keeps_selection_and_ignores_invalid_status(ui):
    path = make_plan(ui)
    ui.load_batch(path)
    ui.batch_tree.selection_set("job")
    write(path.parent / "batch_summary.json", {"plan_sha256": backend.hash_file(path),
        "jobs": [{"job_id": "job", "status": []}], "accepted_sample_count": "500"})
    ui.refresh_batch()
    assert not ui.test_errors
    assert ui.batch_tree.selection() == ("job",)
    assert ui.batch_tree.item("job", "values")[-1] == "planned"
    assert "accepted samples: 0" in ui.batch_text.get()
    ui.events.put(("status", "Still responsive"))
    pump(ui.root, lambda: ui.status.get() == "Still responsive")


def review_attempt(ui):
    path = make_plan(ui)
    ui.load_batch(path)
    ui.batch_tree.selection_set("job")
    directory = path.parent / "runs/job/hud_review/attempt-001"
    directory.mkdir(parents=True)
    index = directory / "index.html"
    index.write_text("<html>Current review</html>")
    state = {"schema_version": 1, "profile": "cs2-bounded-competitive-batch-v1",
        "plan_sha256": backend.hash_file(path), "jobs": {"job": {"stages": {"hud_review": [
            {"attempt": 1, "out": str(directory), "status": "completed",
             "files": {str(index): backend.hash_file(index)}}]}}}}
    write(path.parent / "batch_state.json", state)
    return path, directory, state


def test_open_review_uses_current_journal_attempt_not_unjournaled_folder(ui, monkeypatch):
    _, directory, _ = review_attempt(ui)
    extra = directory.parent / "attempt-999/index.html"
    extra.parent.mkdir()
    extra.write_text("Unjournaled page")
    opened = []
    monkeypatch.setattr(ui, "open_path", opened.append)
    ui.open_review()
    assert opened == [directory / "index.html"]
    assert not ui.test_errors


@pytest.mark.parametrize("change", [
    lambda state: state.update(plan_sha256="different"),
    lambda state: state["jobs"]["job"]["stages"]["hud_review"][-1].update(status="failed"),
    lambda state: state["jobs"]["job"]["stages"]["hud_review"][-1].update(out="../outside"),
    lambda state: state["jobs"]["job"]["stages"].update(hud_review=None),
])
def test_open_review_rejects_stale_or_invalid_journal(ui, monkeypatch, change):
    path, _, state = review_attempt(ui)
    change(state)
    write(path.parent / "batch_state.json", state)
    opened = []
    monkeypatch.setattr(ui, "open_path", opened.append)
    ui.open_review()
    assert not opened
    assert ui.test_errors


def test_open_review_rejects_changed_page(ui, monkeypatch):
    _, directory, _ = review_attempt(ui)
    (directory / "index.html").write_text("Changed page")
    opened = []
    monkeypatch.setattr(ui, "open_path", opened.append)
    ui.open_review()
    assert not opened
    assert "changed" in ui.test_errors[-1]


def test_open_review_supports_historical_bundle_without_html(ui, monkeypatch):
    _, directory, _ = review_attempt(ui)
    (directory / "index.html").unlink()
    write(directory / "hud_review_bundle.json", {"historical": True})
    opened = []
    monkeypatch.setattr(ui, "open_path", opened.append)
    ui.open_review()
    assert opened == [directory]
    assert not ui.test_errors


def test_open_review_supports_setup_receipt_without_generating_frame_sheets(ui, monkeypatch):
    path, directory, state = review_attempt(ui)
    (directory / "index.html").unlink()
    receipt = directory / "hud_policy.json"
    write(receipt, {"profile": "cs2-batch-hud-setup-policy-v1"})
    latest = state["jobs"]["job"]["stages"]["hud_review"][-1]
    latest.update(result={"status": "hud_setup_trusted"}, files={str(receipt): backend.hash_file(receipt)})
    write(path.parent / "batch_state.json", state)
    opened = []
    monkeypatch.setattr(ui, "open_path", opened.append)
    ui.open_review()
    assert opened == [receipt] and not ui.test_errors


def test_trusted_display_status_does_not_claim_sample_acceptance(ui, monkeypatch):
    path = make_plan(ui)
    plan = backend.read_object(path)
    summary = {"jobs": [{"job_id": "job", "status": "pending_visual_review", "display_status": "ready_for_acceptance"}],
               "accepted_sample_count": 0}
    monkeypatch.setattr(backend, "read_batch_display", lambda _: (path, plan, summary))
    ui.load_batch(path)
    assert ui.batch_tree.item("job", "values")[-1] == "ready for acceptance"
    assert "accepted samples: 0" in ui.batch_text.get()


@pytest.mark.parametrize("phase", ["recording", "indexing_session", "archiving_session"])
def test_queue_displays_capture_phase_before_validation_counts_exist(ui, monkeypatch, phase):
    from cs2_data import full_demo
    monkeypatch.setattr(full_demo, 'load_queue', lambda p: {'jobs': [{'id':'one','demo':'match.dem',
        'player_name':'Player','status':phase,'pipeline':{'phase':phase,'recorded_segments':2,'total_segments':8}}]})
    ui.refresh_queue()
    assert '2/8 segments captured' in ui.queue_text.get()
    assert 'validation starts after recording' in ui.queue_text.get()
    assert not ui.test_errors
