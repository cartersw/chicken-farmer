"""Launcher orchestration must preserve failures, source identity and game guards."""
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from cs2_data import launcher_backend as app


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(document).encode())


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "project with spaces"
    for name in ("cs2-extract", "cs2-phases", "cs2-context", "cs2-clocks"):
        tool = project / "bin" / (name + (".exe" if os.name == "nt" else ""))
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_bytes(b"fixture")
    return project


@pytest.fixture
def source(project):
    demo = project / "demos" / "match & sample.DEM"
    demo.parent.mkdir()
    demo.write_bytes(b"fixture source")
    demo_id = app.hash_file(demo)
    parsed = project / "data/parsed/current" / demo_id
    parsed.mkdir(parents=True)
    for name in app.TABLES:
        (parsed / name).write_bytes(name.encode())
    manifest = {"demo_id": demo_id, "sha256": demo_id, "source_path": str(demo), "file_size": demo.stat().st_size,
                "parse_status": "complete", "partial": False, "parser_schema_version": "2",
                "extractor_version": "0.1.2", "match_id": "series-a", "map": "de_dust2",
                "files": {name: app.hash_file(parsed / name) for name in app.TABLES}}
    write(parsed / "manifest.json", manifest)
    write(parsed / "validation.json", {"demo_id": demo_id, "passed": False, "errors": ["missing baseline"]})
    for kind in ("phases", "context"):
        write(project / "data" / kind / "retained.json", {
            "demo_id": demo_id, "source_demo_sha256": demo_id, "parse_status": "complete", "partial": False,
            "schema_version": 1 if kind == "phases" else 2, "producer": "cs2-context-v2"})
    return SimpleNamespace(demo=demo, id=demo_id, parsed=parsed, manifest=manifest)


def task(project, tmp_path):
    events = []
    runner = app.TaskRunner(tmp_path / "output", "test", lambda *args: events.append(args), threading.Event(), project=project)
    runner.events = events
    return runner


def test_scan_recursion_case_and_recorded_status(project, source, tmp_path):
    nested = source.demo.parent / "sub" / "other.dem"
    nested.parent.mkdir()
    nested.write_bytes(b"second")
    (source.demo.parent / "ignore.txt").write_text("x")
    flat = app.scan_demos(source.demo.parent, tmp_path / "out", recursive=False, project=project)
    assert len(flat) == 1 and flat[0].parsed == source.parsed
    assert flat[0].recorded_status == "Recorded parse"
    assert len(app.scan_demos(source.demo.parent, tmp_path / "out", project=project)) == 2
    source.demo.write_bytes(b"changed size")
    assert app.scan_demos(source.demo.parent, tmp_path / "out", recursive=False, project=project)[0].parsed is None


def test_prepare_reuses_verified_inputs_and_retains_quality_issues(project, source, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    monkeypatch.setattr(runner, "command", lambda *args: pytest.fail("Existing inputs should not be re-extracted"))
    before = {path: path.read_bytes() for path in source.parsed.iterdir()}
    result = app.read_object(app.prepare_sources(runner, [source.demo, source.demo]))
    assert len(result["sources"]) == 1
    assert result["sources"][0]["parsed"] == str(source.parsed)
    assert result["profile"] == app.SOURCE_PROFILE
    assert any(kind == "prepared" and value["quality_passed"] is False for kind, value in runner.events)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert runner.journal["training_ready"] is False


def test_reused_table_corruption_cannot_be_skipped(project, source, tmp_path):
    (source.parsed / "usercmd.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Parsed source changed"):
        app.prepare_sources(task(project, tmp_path), [source.demo])


def test_preserve_existing_series_identity(project, source, tmp_path):
    with pytest.raises(ValueError, match="series-a"):
        app.prepare_sources(task(project, tmp_path), [source.demo], "different-series")


def test_unrelated_broken_manifest_does_not_block_source(project, source, tmp_path):
    broken = project / "data/parsed/zzbroken/other/manifest.json"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"{")
    assert app.prepare_sources(task(project, tmp_path), [source.demo]).is_file()


def test_old_extractor_is_not_reused(project, source, tmp_path, monkeypatch):
    source.manifest["extractor_version"] = "0.1.1"
    write(source.parsed / "manifest.json", source.manifest)
    runner = task(project, tmp_path)
    calls = []

    def extract(label, args):
        calls.append(args)
        raise RuntimeError("fresh extraction requested")

    monkeypatch.setattr(runner, "command", extract)
    with pytest.raises(RuntimeError, match="fresh extraction"):
        app.prepare_sources(runner, [source.demo])
    assert calls[0][1] == "extract"
    assert "--max-demo-tick" not in calls[0] and "--execute" not in calls[0]


@pytest.mark.parametrize("passed,code,expected", [(False, 1, True), (True, 0, True), (True, 1, False)])
def test_extract_exit_code_is_interpreted_with_complete_quality_report(project, source, tmp_path, monkeypatch, passed, code, expected):
    # Hide the canonical candidate, while retaining fixture bytes for a producer.
    original = source.parsed / "manifest.json"
    original.unlink()
    runner = task(project, tmp_path)

    def extract(label, args):
        parsed = Path(args[args.index("--out") + 1]) / source.id
        parsed.mkdir(parents=True)
        for name in app.TABLES:
            (parsed / name).write_bytes((source.parsed / name).read_bytes())
        write(parsed / "manifest.json", source.manifest)
        write(parsed / "validation.json", {"demo_id": source.id, "passed": passed, "errors": [] if passed else ["gap"]})
        return code

    monkeypatch.setattr(runner, "command", extract)
    if expected:
        assert app.prepare_sources(runner, [source.demo]).is_file()
    else:
        with pytest.raises(ValueError, match="beyond its retained quality"):
            app.prepare_sources(runner, [source.demo])


def test_partial_output_is_never_promoted(project, source, tmp_path, monkeypatch):
    (source.parsed / "manifest.json").unlink()
    runner = task(project, tmp_path)
    monkeypatch.setattr(runner, "command", lambda *args: 1)
    with pytest.raises(ValueError, match="Parsing did not complete"):
        app.prepare_sources(runner, [source.demo])
    assert not (runner.run_dir / "sources.json").exists()


def test_demo_changed_during_preparation_is_rejected(project, source, tmp_path, monkeypatch):
    original = app._sidecar

    def sidecar(task, demo, demo_id, kind):
        path = original(task, demo, demo_id, kind)
        if kind == "context":
            demo.write_bytes(b"changed after source hashing")
        return path

    monkeypatch.setattr(app, "_sidecar", sidecar)
    runner = task(project, tmp_path)
    with pytest.raises(ValueError, match="changed during preparation"):
        app.prepare_sources(runner, [source.demo])
    assert not (runner.run_dir / "sources.json").exists()


def test_stop_prevents_new_process_without_killing_current_one(project, tmp_path):
    runner = task(project, tmp_path)
    # A stop requested while output is arriving must still wait for process exit.
    original_emit = runner.emit

    def emit(kind, message):
        original_emit(kind, message)
        if kind == "log" and message == "started":
            runner.stop.set()

    runner.emit = emit
    result = runner.command("fixture", [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(.1); print('finished')"])
    assert result == 0 and "finished" in (runner.run_dir / "activity.log").read_text()
    with pytest.raises(app.StopRequested):
        runner.command("must not run", ["not-an-executable"])
    assert len(runner.journal["commands"]) == 1


def test_process_arguments_are_literal_and_failures_retained(project, tmp_path):
    runner = task(project, tmp_path)
    payload = 'name & echo invalid; $(whoami) "quoted"'
    result = runner.command("literal", [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(7)", payload])
    assert result == 7
    assert payload in (runner.run_dir / "activity.log").read_text()
    assert app.read_object(runner.run_dir / "run.json")["commands"][0]["exit_code"] == 7


def batch(project, tmp_path):
    path = tmp_path / "batch/batch_plan.json"
    write(path, {"profile": "cs2-bounded-competitive-batch-v1", "clip_ticks": 640,
                 "jobs": [{"job_id": "test", "source_id": "test", "job": {
                     "round_id": 1, "steam_id": 123, "start_demo_tick": 6000, "end_demo_tick": 6640}}]})
    for name in ("ffmpeg", "ffprobe"):
        file = project / f".tools/ffmpeg/bundled/bin/{name}.exe"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"fixture")
    plugin = project / "tools/renderer/build/plugin-windows/Release/server.dll"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"fixture")
    return path


def test_display_ignores_summary_for_different_plan(project, tmp_path):
    path = batch(project, tmp_path)
    write(path.parent / "batch_summary.json", {"plan_sha256": "wrong", "accepted_sample_count": 500})
    assert app.read_batch_display(path)[2] == {}


def test_capture_uses_existing_protected_runner_one_job_no_overrides(project, tmp_path, monkeypatch):
    path = batch(project, tmp_path)
    monkeypatch.setattr(app.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))
    _, args = app.capture_arguments(task(project, tmp_path), path)
    assert args[1:4] == ["-m", "cs2_data.competitive_batch", "run"]
    assert args[args.index("--max-jobs") + 1] == "1"
    assert "--execute" in args and "--retry-failed" not in args
    assert not any("allow" in arg or "insecure" in arg for arg in args)


def test_capture_requires_working_space_on_batch_drive(project, tmp_path, monkeypatch):
    path = batch(project, tmp_path)
    checked = []
    monkeypatch.setattr(app.shutil, "disk_usage", lambda p: checked.append(p) or SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="working space"):
        app.capture_arguments(task(project, tmp_path), path)
    assert checked == [path.parent]


def test_zero_exit_with_failed_batch_is_reported_as_failure(project, tmp_path, monkeypatch):
    path = batch(project, tmp_path)
    runner = task(project, tmp_path)
    write(path.parent / "batch_summary.json", {"plan_sha256": app.hash_file(path),
        "jobs": [{"job_id": "test", "status": "recovery_required", "stage": "render", "error": "restore first"}]})
    monkeypatch.setattr(app, "capture_arguments", lambda *args: (path, ["fixture"]))
    monkeypatch.setattr(runner, "command", lambda *args: 0)
    with pytest.raises(ValueError, match="restore first"):
        app.run_next_capture(runner, path)


def test_planning_never_launches_game_and_publishes_only_real_plan(project, source, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    calls = []

    def command(label, args):
        calls.append(args)
        write(Path(args[args.index("--out") + 1]) / "batch/batch_plan.json", {"fixture": True})
        return 0

    monkeypatch.setattr(runner, "command", command)
    assert app.plan_captures(runner, [source.demo]).is_file()
    assert len(calls) == 1 and "cs2_data.competitive_collection" in calls[0]
    assert "--execute" not in calls[0] and "cs2_data.competitive_batch" not in calls[0]


def test_interrupted_launcher_settings_do_not_leave_partial_json(tmp_path):
    path = tmp_path / "settings.json"
    app.save_settings(path, {"folder": "first"})
    app.save_settings(path, {"folder": "second"})
    assert app.read_object(path) == {"folder": "second"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("change", [
    lambda plan: plan["jobs"].append(plan["jobs"][0].copy()),
    lambda plan: plan["jobs"][0].update(job_id="../other"),
    lambda plan: plan["jobs"][0].update(job=None),
    lambda plan: plan["jobs"][0]["job"].update(start_demo_tick="6000"),
    lambda plan: plan["jobs"][0]["job"].update(end_demo_tick=5999),
    lambda plan: plan["jobs"][0]["job"].update(steam_id=[]),
    lambda plan: plan["jobs"][0]["job"].update(steam_id=2**64),
    lambda plan: plan["jobs"][0]["job"].update(steam_id=str(2**64)),
    lambda plan: plan["jobs"][0]["job"].update(start_demo_tick=2147483000, end_demo_tick=2147483640),
    lambda plan: plan["jobs"][0]["job"].update(round_id=True),
    lambda plan: plan["jobs"][0]["job"].update(map={}),
])
def test_display_rejects_malformed_plan_before_ui_changes(project, tmp_path, change):
    path = batch(project, tmp_path)
    plan = app.read_object(path)
    change(plan)
    write(path, plan)
    with pytest.raises(ValueError):
        app.read_batch_display(path)


@pytest.mark.parametrize("fields", [
    {"jobs": None},
    {"jobs": [{"job_id": "test", "status": None}]},
    {"jobs": [{"job_id": [], "status": "planned"}]},
    {"jobs": [{"job_id": "unknown", "status": "planned"}]},
    {"jobs": [{"job_id": "test", "status": "planned"}] * 2},
    {"accepted_sample_count": "500"},
    {"accepted_sample_count": -1},
    {"job_status_counts": {"accepted": True}},
])
def test_display_ignores_malformed_bound_summary(project, tmp_path, fields):
    path = batch(project, tmp_path)
    write(path.parent / "batch_summary.json", {"plan_sha256": app.hash_file(path), **fields})
    assert app.read_batch_display(path)[2] == {}


def test_display_opens_valid_plan_when_summary_json_is_incomplete(project, tmp_path):
    path = batch(project, tmp_path)
    (path.parent / "batch_summary.json").write_text("{")
    assert app.read_batch_display(path)[2] == {}
