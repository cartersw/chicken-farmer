"""Full-source accounting, persistent queue and durable segment recovery."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from cs2_data import full_demo as full
from cs2_data.io import read_json, sha256_file, write_json
from cs2_data.launcher_backend import save_settings


@pytest.mark.parametrize("start", [0, 198, 199, 200, 301])
@pytest.mark.parametrize("length", [1, 15, 31, 32, 33, 63, 64, 65, 66, 78, 79, 80, 128, 129, 500, 7681, 15361])
def test_split_owns_every_available_tick_and_preserves_tails(start, length):
    segments, excluded = full.split_window(start, start+length, max_ticks=64)
    intervals = [(s["owned_start_demo_tick"], s["owned_end_demo_tick"]) for s in segments]
    intervals += [(s["start_demo_tick"], s["end_demo_tick"]) for s in excluded]
    intervals.sort()
    cursor = start
    for a, b in intervals:
        assert a == cursor and b > a
        cursor = b
    assert cursor == start+length
    for index, s in enumerate(segments):
        duration = s["end_demo_tick"]-s["start_demo_tick"]
        assert 32 <= duration <= 64 and duration % 2 == 0
        assert s["start_demo_tick"] >= 199
        assert s["history_overlap_ticks"] == (14 if index else 0)
        if index:
            assert s["start_demo_tick"] == segments[index-1]["end_demo_tick"]-14


def test_two_minute_capture_bound_and_redistributed_tail():
    segments, exclusions = full.split_window(1000, 1000+7682)
    assert not exclusions
    assert [s["end_demo_tick"]-s["start_demo_tick"] for s in segments] == [7664, 32]
    assert sum(s["owned_end_demo_tick"]-s["owned_start_demo_tick"] for s in segments) == 7682


def test_queue_survives_reload_and_rejects_duplicate_or_changed_format(tmp_path):
    path, demo = tmp_path/"queue/queue.json", tmp_path/"match.dem"
    demo.write_bytes(b"fixture demo")
    job = full.enqueue(path, demo, "76561198000000001", "Player")
    assert full.load_queue(path)["jobs"] == [job]
    with pytest.raises(ValueError, match="already queued"):
        full.enqueue(path, demo, job["steam_id"], "Renamed player")
    with pytest.raises(ValueError, match="640x360"):
        full.enqueue(path, demo, "76561198000000002", "Player 2", output_format={**full.FORMAT, "color": "gray"})


def test_exclusions_account_for_dead_freeze_pause_missing_and_bad_commands(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pylist([{"round_id": 1, "freeze_end_tick": 2, "end_tick": 13}]), tmp_path/"rounds.parquet")
    states = []
    for tick in range(14):
        if tick == 12:
            continue
        states.append({"steam_id": 123, "demo_tick": tick, "round_id": 1,
                       "is_freeze_time": tick < 2, "is_paused": tick == 4, "alive": tick != 5})
    pq.write_table(pa.Table.from_pylist(states), tmp_path/"player_state.parquet")
    write_json(tmp_path/"phases.json", {"events": [{"demo_tick": 14}]})
    monkeypatch.setattr(full, "phase_evidence", lambda *args: {1: {"phase_verified": True}})
    source = {"parsed": str(tmp_path), "steam_id": "123", "phase_manifest": str(tmp_path/"phases.json")}
    windows = [{"start_demo_tick": 6, "end_demo_tick": 8, "command_coverage": {"complete_within_gap_limit": False}}]
    segments = [{"owned_start_demo_tick": 2, "owned_end_demo_tick": 4}, {"owned_start_demo_tick": 8, "owned_end_demo_tick": 11}]
    edges = [{"start_demo_tick": 11, "end_demo_tick": 12, "reason": "odd_final_tick_outside_32fps_grid"}]
    rows, end = full.exclusion_timeline(source, {}, windows, segments, edges)
    assert end == 14
    assert [r["reason"] for r in rows] == ["freeze_time", "pause", "player_dead", "incomplete_command_coverage",
        "odd_final_tick_outside_32fps_grid", "missing_player_state", "after_round_end"]
    assert sum(r["end_demo_tick"]-r["start_demo_tick"] for r in rows) == 9


@pytest.fixture
def run_fixture(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    import queue
    from cs2_data import demo_pipeline
    from cs2_data import recording_session
    # These tests exercise the existing bounded coordinator in isolation. The
    # default session capture barrier is covered in test_recording_session.py.
    monkeypatch.setattr(recording_session, "prepare_recordings", lambda *a, **k: {})
    pipeline = demo_pipeline.run_pipeline
    def legacy_coordinator(*args, **kwargs):
        kwargs.pop("session_jobs", None)
        return pipeline(*args, **kwargs)
    monkeypatch.setattr(demo_pipeline, "run_pipeline", legacy_coordinator)
    @contextmanager
    def workers(count):
        with ThreadPoolExecutor(1) as recorder, ThreadPoolExecutor(count) as validators, ThreadPoolExecutor(1) as archiver:
            yield recorder, validators, archiver, queue.Queue()
    monkeypatch.setattr(demo_pipeline, "create_workers", workers)
    root = tmp_path/"job"
    root.mkdir()
    source = tmp_path/"source.dem"
    source.write_bytes(b"unchanged source")
    plan = {"profile": full.PROFILE, "format": full.FORMAT, "source_files": {str(source): sha256_file(source)}, "segments": [
        {"id": str(i), "round_id": i, "start_demo_tick": 1000, "end_demo_tick": 3000} for i in range(2)]}
    write_json(root/"demo_plan.json", plan)
    save_settings(root/"progress.json", {"plan_sha256": sha256_file(root/"demo_plan.json"), "segments": {}, "accepted_samples": 0})
    monkeypatch.setattr(full, "write_report", lambda *args: None)
    monkeypatch.setattr(full, "renderer_tools", lambda *args: {"ffmpeg": "bundled-ffmpeg", "ffprobe": "bundled-ffprobe"})
    monkeypatch.setattr(full.shutil, "disk_usage", lambda *args: SimpleNamespace(free=100_000_000_000))
    prepared, packed, released = [], [], []
    def prepare(root, plan, segment):
        prepared.append(segment["id"])
        work = root/"work"/segment["id"]
        work.mkdir(parents=True, exist_ok=True)
        return work
    monkeypatch.setattr(full, "prepare_segment", prepare)
    def run_batch(*args, **kwargs):
        assert kwargs["ffmpeg"] == "bundled-ffmpeg" and kwargs["ffprobe"] == "bundled-ffprobe"
        assert callable(kwargs["progress"])
        kwargs["progress"]("process", "perform")
        return {"jobs": [{"status": "verified_render_artifacts" if kwargs["mode"] == "record" else "accepted_partition_verified"}]}
    monkeypatch.setattr(full.batch, "run_batch", run_batch)
    def pack(work, package, segment_id, **kwargs):
        import zipfile
        packed.append(segment_id)
        package.mkdir(parents=True)
        archive = package/"training.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("samples.jsonl", "")
        receipt = {"sample_count": 3, "rejected_count": 1, "duplicate_count": 0,
                   "compressed_bytes": 10, "reason_counts": {"timing": 1},
                   "training_archive": {"path": str(archive), "sha256": sha256_file(archive)},
                   "evidence_archive": {"path": str(archive), "sha256": sha256_file(archive)}}
        save_settings(package/"receipt.json", receipt)
        return receipt
    monkeypatch.setattr(full, "pack_segment", pack)
    def release(work, *args):
        released.append(work.name)
        work.rmdir()
    monkeypatch.setattr(full, "release_work", release)
    monkeypatch.setattr(demo_pipeline, "captured_work", lambda root, key: root/"work"/key if (root/"work"/key).exists() else None)
    return root, prepared, packed, released


def test_unattended_run_continues_all_segments_and_resume_never_recaptures(run_fixture):
    root, prepared, packed, released = run_fixture
    progress = full.run_demo(root)
    assert progress["status"] == "complete" and progress["accepted_samples"] == 6
    assert prepared == packed == released == ["0", "1"]
    full.run_demo(root)
    assert prepared == ["0", "1"]


def test_completed_session_dependency_is_checked_on_resume(run_fixture):
    from cs2_data.session_profile import PROFILE
    root, prepared, _, _ = run_fixture
    progress = full.run_demo(root)
    shared_zip = root/'shared.zip'; shared_zip.write_bytes(b'original archive')
    shared_receipt = root/'shared-receipt.json'
    save_settings(shared_receipt, {'profile':PROFILE,'status':'compressed',
        'archive':{'path':str(shared_zip),'sha256':sha256_file(shared_zip)}})
    record = progress['segments']['0']
    receipt = read_json(Path(record['receipt']))
    receipt['shared_session']={'receipt':str(shared_receipt),'receipt_sha256':sha256_file(shared_receipt)}
    save_settings(Path(record['receipt']),receipt)
    record['receipt_sha256']=sha256_file(Path(record['receipt']))
    save_settings(root/'progress.json',progress)
    shared_zip.write_bytes(b'changed archive')
    with pytest.raises(ValueError,match='Shared recording archive changed'):
        full.run_demo(root)
    assert prepared == ['0','1']


def test_stop_finishes_packaging_current_segment_then_resume_continues(run_fixture):
    root, prepared, packed, released = run_fixture
    stop = threading.Event()
    def notify(message):
        if "recording (validation" in message:
            stop.set()
    progress = full.run_demo(root, stop=stop, emit=notify)
    assert progress["status"] == "stopped" and packed == released == ["0"]
    progress = full.run_demo(root)
    assert progress["status"] == "complete" and prepared == ["0", "1"]


def test_cleanup_failure_preserves_completed_receipt_for_resume(run_fixture, monkeypatch):
    root, prepared, packed, released = run_fixture
    original = full.release_work
    monkeypatch.setattr(full, "release_work", lambda *args: (_ for _ in ()).throw(OSError("file locked")))
    with pytest.raises(OSError, match="file locked"):
        full.run_demo(root)
    assert read_json(root/"progress.json")["segments"]["0"]["status"] == "complete"
    monkeypatch.setattr(full, "release_work", original)
    assert full.run_demo(root)["status"] == "complete"
    assert prepared == packed == released == ["0", "1"]


def test_low_disk_pauses_before_launching(run_fixture, monkeypatch):
    root, prepared, _, _ = run_fixture
    monkeypatch.setattr(full.shutil, "disk_usage", lambda *args: SimpleNamespace(free=10))
    assert full.run_demo(root)["status"] == "paused_low_disk"
    assert not prepared


def test_queue_preprocesses_all_entries_then_skips_completed_entries_on_resume(tmp_path, monkeypatch):
    queue = tmp_path/"queue/queue.json"
    jobs = []
    for name in ("first", "second"):
        demo = tmp_path/(name+".dem")
        demo.write_bytes(name.encode())
        jobs.append(full.enqueue(queue, demo, "76561198000000001", "Player"))
    events, prepared, processed = [], [], []
    task = SimpleNamespace(project=tmp_path, stop=threading.Event(),
                           emit=lambda *args: events.append(args), log=lambda message: None)
    def prepare(task, demos, match):
        path = tmp_path/(demos[0].stem+".json")
        write_json(path, {"demo": str(demos[0])})
        prepared.append(demos[0])
        return path
    def plan(sources, root, steam_id):
        write_json(root/"demo_plan.json", {"source": {"demo": read_json(sources)["demo"], "steam_id": steam_id},
                                         "segments": [{"id": "one"}], "planned_seconds": 80})
    def run(root, stop, emit, validation_workers):
        assert validation_workers == 2
        processed.append(root)
        progress = {"status": "complete", "accepted_samples": 20, "segments": {"one": {"status": "complete"}}}
        save_settings(root/"progress.json", progress)
        emit("Segment complete")
        return progress
    monkeypatch.setattr(full, "prepare_sources", prepare)
    monkeypatch.setattr(full, "plan_demo", plan)
    monkeypatch.setattr(full, "run_demo", run)
    result = full.run_queue(task, queue)
    assert result["status"] == "complete" and result["completed_demos"] == 2
    assert len(prepared) == len(processed) == 2
    assert all(j["completed_segments"] == 1 and j["accepted_samples"] == 20 for j in full.load_queue(queue)["jobs"])
    full.run_queue(task, queue)
    assert len(prepared) == len(processed) == 2
    assert any(kind == "queue" for kind, _ in events)


def test_saved_plan_cannot_substitute_a_different_player(tmp_path, monkeypatch):
    queue, demo = tmp_path/"queue/queue.json", tmp_path/"source.dem"
    demo.write_bytes(b"demo")
    job = full.enqueue(queue, demo, "123", "Selected player")
    root = queue.parent/"jobs"/job["id"]
    write_json(root/"demo_plan.json", {"source": {"demo": str(demo), "steam_id": "456"}})
    task = SimpleNamespace(project=tmp_path, stop=threading.Event(), emit=lambda *args: None, log=lambda *args: None)
    monkeypatch.setattr(full, "run_demo", lambda *args, **kwargs: pytest.fail("Must not capture another player"))
    with pytest.raises(ValueError, match="another queued demo/player"):
        full.run_queue(task, queue)
    assert full.load_queue(queue)["jobs"][0]["status"] == "needs_attention"



def extend_run(root, count):
    plan = read_json(root/"demo_plan.json")
    plan["segments"] = [{**plan["segments"][0], "id": str(i)} for i in range(count)]
    save_settings(root/"demo_plan.json", plan)
    progress = read_json(root/"progress.json")
    progress["plan_sha256"] = sha256_file(root/"demo_plan.json")
    save_settings(root/"progress.json", progress)


def test_two_validators_overlap_next_recording_and_bound_raw_backlog(run_fixture, monkeypatch):
    root, prepared, packed, released = run_fixture
    extend_run(root, 5)
    validators = [threading.Event(), threading.Event()]
    third_recording = threading.Event()
    original = full.batch.run_batch
    peak = 0
    def run(path, **kwargs):
        key = int(path.parent.name)
        if kwargs["mode"] == "record" and key == 2:
            assert all(event.wait(5) for event in validators), "Both validators must run alongside recording"
            third_recording.set()
        elif kwargs["mode"] == "process" and key < 2:
            validators[key].set()
            assert third_recording.wait(5), "Recording must not wait for validation"
        return original(path, **kwargs)
    monkeypatch.setattr(full.batch, "run_batch", run)
    def notify(message):
        nonlocal peak
        current = read_json(root/"progress.json")
        peak = max(peak, current["pipeline"]["in_flight"])
        assert current["pipeline"]["recording"] <= 1
        assert current["pipeline"]["validating"] <= 2
        assert len(prepared) - len(released) <= 3
    result = full.run_demo(root, emit=notify)
    assert result["status"] == "complete" and peak == 3
    assert packed == released == [str(i) for i in range(5)]


def test_validation_error_retains_later_captures_and_resume_does_not_record_again(run_fixture, monkeypatch):
    root, prepared, packed, released = run_fixture
    recorded_second = threading.Event()
    original = full.batch.run_batch
    def fail(path, **kwargs):
        if kwargs["mode"] == "record" and path.parent.name == "1":
            recorded_second.set()
        if kwargs["mode"] == "process" and path.parent.name == "0":
            assert recorded_second.wait(5)
            raise ValueError("invalid timing evidence")
        return original(path, **kwargs)
    monkeypatch.setattr(full.batch, "run_batch", fail)
    with pytest.raises(ValueError, match="invalid timing"):
        full.run_demo(root)
    assert prepared == ["0", "1"] and not packed and not released
    monkeypatch.setattr(full.batch, "run_batch", original)
    assert full.run_demo(root)["status"] == "complete"
    assert prepared == packed == released == ["0", "1"]


def test_stop_drains_all_already_recorded_clips(run_fixture):
    root, prepared, packed, released = run_fixture
    extend_run(root, 6)
    stop = threading.Event()
    def notify(message):
        if "Segment 3/6: recording" in message:
            stop.set()
    result = full.run_demo(root, stop=stop, emit=notify)
    assert result["status"] == "stopped"
    assert prepared == packed == released == ["0", "1", "2"]
    assert full.run_demo(root)["status"] == "complete"
    assert prepared == packed == released == [str(i) for i in range(6)]


def test_later_validation_finishes_first_but_packaging_preserves_dedup_order(run_fixture, monkeypatch):
    root, _, packed, _ = run_fixture
    second_validated = threading.Event()
    original = full.batch.run_batch
    def run(path, **kwargs):
        if kwargs["mode"] == "process":
            if path.parent.name == "0":
                assert second_validated.wait(5)
            else:
                second_validated.set()
        return original(path, **kwargs)
    monkeypatch.setattr(full.batch, "run_batch", run)
    original_pack = full.pack_segment
    def pack(work, package, key, *, seen):
        assert seen == (set() if key == "0" else {"earlier target"})
        result = original_pack(work, package, key, seen=seen)
        seen.add("earlier target")
        return result
    monkeypatch.setattr(full, "pack_segment", pack)
    assert full.run_demo(root)["status"] == "complete"
    assert packed == ["0", "1"]


def test_low_disk_drains_archives_before_admitting_more_captures(run_fixture, monkeypatch):
    root, prepared, packed, released = run_fixture
    available = [100_000_000_000]
    monkeypatch.setattr(full.shutil, "disk_usage", lambda *args: SimpleNamespace(free=available[0]))
    original = full.release_work
    def release(work, *args):
        assert prepared == (["0"] if work.name == "0" else ["0", "1"])
        original(work, *args)
        available[0] = 100_000_000_000
    monkeypatch.setattr(full, "release_work", release)
    def notify(message):
        if "Segment 1/2: recording" in message:
            available[0] = 10
    assert full.run_demo(root, emit=notify)["status"] == "complete"
    assert prepared == packed == released == ["0", "1"]
