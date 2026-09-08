import json
import pytest

from cs2_data import calibration_stalls as stalls


def header(**values):
    return {"event": "header", "qpc_frequency": 1000, "qpc": 0, "thread_id": 12, **values}


def frame(qpc, tick, **values):
    return {"event": "frame_sample", "thread_id": 12, "qpc": qpc,
            "elapsed_ms": (tick - 100) * 15.625,
            "local_player": {"status": "observed", "controller_tick_base": tick,
                             "steam_id": "76561198845209628", "pawn_handle": 1001}, **values}


def movie(index, qpc, tick, **values):
    return {"event": "movie_frame", "thread_id": 12, "capture_index": index,
            "qpc_before": qpc, "qpc_after": qpc + 2, "replay_demo_tick": tick,
            "movie_name": "owned_", "native_clock": {"client_generation": 3}, **values}


def run(tmp_path, calibration=None, capture=None):
    directory = tmp_path / "run"
    directory.mkdir()
    for name, rows in (("calibration_ledger.jsonl", calibration), ("capture_ledger.jsonl", capture)):
        if rows is not None:
            (directory / name).write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return directory


def test_zero_and_positive_simulation_advance_remain_distinct(tmp_path):
    source = run(tmp_path, [header(), frame(0, 100), frame(300, 100), frame(350, 102), frame(450, 101)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    series = report["series"]["frame_sample"]
    assert series["above_threshold_count"] == 1
    assert series["wall_gaps_by_simulation_advancement"]["zero"]["maximum_ms"] == 300
    assert series["wall_gaps_by_simulation_advancement"]["positive"]["maximum_ms"] == 50
    assert series["wall_gaps_by_simulation_advancement"]["backwards"]["maximum_ms"] == 100
    assert series["adjacent_intervals"][0]["simulation"]["delta_ticks"] == 0
    assert report["training_ready"] is report["exact_input_timing_verified"] is False
    assert (tmp_path / "analysis" / "stalls.md").is_file()


def test_sparse_startup_wait_does_not_become_a_frame_stall(tmp_path):
    source = run(tmp_path, [header(), {"event": "waiting_for_map", "qpc": 1000},
                           {"event": "local_map_dispatched", "qpc": 8000},
                           {"event": "local_setup_dispatched", "qpc": 12000},
                           {"event": "calibration_ready", "qpc": 24000},
                           frame(24000, 100), frame(24030, 102)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["series"]["frame_sample"]["above_threshold_count"] == 0
    assert report["startup"]["cadence_available"] is False
    assert report["startup"]["milestone_gaps_are_frame_stalls"] is False
    assert report["startup"]["milestones"][-1]["wall_ms_since_previous_milestone"] == 12000


def test_replay_clock_and_call_duration_separate_from_wall_gap(tmp_path):
    source = run(tmp_path, capture=[header(), movie(0, 0, 258), movie(1, 300, 260), movie(2, 330, 262)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis", capture_indices=[1])
    series = report["series"]["movie_frame"]
    assert series["call_durations"]["maximum_ms"] == 2
    assert series["start_to_start_wall_gaps"]["maximum_ms"] == 300
    assert series["adjacent_intervals"][0]["simulation"] == {
        "clock": "replay_demo_tick", "delta_ticks": 2, "advancement": "positive"}
    assert len(report["capture_index_contexts"][0]["adjacent_intervals"]) == 2


@pytest.mark.parametrize("middle", [frame(None, 102), frame(50, 102, thread_id=13)])
def test_missing_sample_or_other_thread_is_not_bridged(tmp_path, middle):
    source = run(tmp_path, [header(), frame(0, 100), middle, frame(1000, 104)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["series"]["frame_sample"]["skipped_adjacent_pairs"] == 2
    assert report["series"]["frame_sample"]["above_threshold_count"] == 0
    assert report["status"] == "incomplete_or_inconsistent"


def test_backwards_qpc_never_becomes_positive_gap(tmp_path):
    source = run(tmp_path, [header(), frame(100, 100), frame(0, 102)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["series"]["frame_sample"]["adjacent_intervals"] == []
    assert "backwards_qpc" in [issue["code"] for issue in report["issues"]]


def test_client_epoch_change_does_not_span_loading(tmp_path):
    source = run(tmp_path, capture=[header(), movie(0, 0, 258),
                 movie(1, 12000, 260, native_clock={"client_generation": 4}), movie(2, 12030, 262,
                 native_clock={"client_generation": 4})])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    series = report["series"]["movie_frame"]
    assert series["above_threshold_count"] == 0
    assert series["start_to_start_wall_gaps"]["maximum_ms"] == 30


def test_unavailable_simulation_clock_does_not_infer_from_fps(tmp_path):
    source = run(tmp_path, capture=[header(), movie(0, 0, None), movie(1, 300, None)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    gap = report["series"]["movie_frame"]["adjacent_intervals"][0]
    assert gap["wall_gap_ms"] == 300
    assert gap["simulation"] == {"clock": None, "delta_ticks": None, "advancement": "unavailable"}


def test_frequency_missing_does_not_assume_qpc_rate(tmp_path):
    source = run(tmp_path, [header(qpc_frequency=None), frame(0, 100), frame(1000, 102)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["series"]["frame_sample"]["start_to_start_wall_gaps"]["maximum_ms"] is None
    assert report["status"] == "incomplete_or_inconsistent"


def test_turn_context_is_same_thread_wall_proximity_only(tmp_path):
    source = run(tmp_path, [header(), frame(0, 100), frame(30, 102),
                           {"event": "action_dispatch", "thread_id": 12, "id": "turn_start", "command": "+turnleft",
                            "qpc_before": 50, "actual_elapsed_ms": 6500}, frame(60, 104), frame(500, 106)])
    report = stalls.analyze_stalls(source, tmp_path / "analysis", action_window_ms=0)
    context = report["action_contexts"][0]
    assert context["id"] == "turn_start"
    assert context["wall_gaps"]["maximum_ms"] == 30
    assert context["largest_interval"]["before"]["event_index"] == 1
    assert context["interpretation"] == "same_ledger_wall_time_proximity_not_causation"


def test_startup_uses_direct_callback_maximum_not_throttled_sample_spacing(tmp_path):
    source = run(tmp_path, [header(),
        {"event": "startup_cadence_sample", "qpc": 1000, "stage": 0,
         "max_callback_gap_qpc_since_previous_sample": 10},
        {"event": "startup_cadence_sample", "qpc": 5000, "stage": 0,
         "max_callback_gap_qpc_since_previous_sample": 450},
        {"event": "startup_cadence_sample", "qpc": 6000, "stage": 1,
         "max_callback_gap_qpc_since_previous_sample": 20}])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["startup"]["cadence_available"] is True
    assert report["startup"]["stages"]["0"]["observed_callback_gap_maxima"]["maximum_ms"] == 450
    assert report["startup"]["stages"]["0"]["above_threshold_count"] == 1
    assert report["startup"]["stages"]["1"]["observed_callback_gap_maxima"]["maximum_ms"] == 20
    assert report["series"] == {}


def test_startup_invalid_maximum_never_reports_available(tmp_path):
    source = run(tmp_path, [header(), {"event": "startup_cadence_sample", "qpc": 1000, "stage": 0,
                                     "max_callback_gap_qpc_since_previous_sample": -1}])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["startup"]["cadence_available"] is False
    assert report["status"] == "incomplete_or_inconsistent"


def test_initial_zero_sentinel_is_not_startup_gap_evidence(tmp_path):
    source = run(tmp_path, [header(), {"event": "startup_cadence_sample", "qpc": 1000, "stage": 0,
                                     "max_callback_gap_qpc_since_previous_sample": 0}])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["startup"]["cadence_available"] is False
    assert report["status"] == "incomplete_or_inconsistent"


def test_settling_ineligible_tick_minus_one_remains_unknown(tmp_path):
    source = run(tmp_path, [header(), {"event": "startup_settle_sample", "qpc": 0, "thread_id": 12,
                                      "eligible": False, "controller_tick_base": -1, "pawn_handle": 4294967295},
                            {"event": "startup_settle_sample", "qpc": 300, "thread_id": 12,
                             "eligible": True, "controller_tick_base": 100, "pawn_handle": 1001}])
    report = stalls.analyze_stalls(source, tmp_path / "analysis")
    assert report["series"]["startup_settle_sample"]["adjacent_intervals"][0]["simulation"]["advancement"] == "unavailable"


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf"), True])
def test_threshold_must_be_explicit_valid_diagnostic_value(tmp_path, threshold):
    source = run(tmp_path, [header(), frame(0, 100), frame(30, 102)])
    with pytest.raises(ValueError, match="threshold"):
        stalls.analyze_stalls(source, tmp_path / "analysis", gap_threshold_ms=threshold)


def test_source_and_existing_outputs_cannot_be_overwritten(tmp_path):
    source = run(tmp_path, [header(), frame(0, 100), frame(30, 102)])
    before = (source / "calibration_ledger.jsonl").read_bytes()
    with pytest.raises(ValueError, match="fresh and outside"):
        stalls.analyze_stalls(source, source / "analysis")
    stalls.analyze_stalls(source, tmp_path / "analysis")
    with pytest.raises(ValueError, match="fresh and outside"):
        stalls.analyze_stalls(source, tmp_path / "analysis")
    assert (source / "calibration_ledger.jsonl").read_bytes() == before


def test_changed_source_prevents_diagnostic_publication(tmp_path, monkeypatch):
    source = run(tmp_path, [header(), frame(0, 100), frame(30, 102)])
    original = stalls._read_ledger

    def changing_read(path):
        rows = original(path)
        if path.name == "calibration_ledger.jsonl":
            path.write_text(path.read_text() + json.dumps(frame(60, 104)) + "\n")
        return rows

    monkeypatch.setattr(stalls, "_read_ledger", changing_read)
    with pytest.raises(ValueError, match="changed"):
        stalls.analyze_stalls(source, tmp_path / "analysis")
    assert not (tmp_path / "analysis").exists()


def test_duplicate_json_evidence_is_rejected(tmp_path):
    source = run(tmp_path)
    (source / "calibration_ledger.jsonl").write_text('{"event":"header","qpc_frequency":1000,"qpc_frequency":1}\n')
    with pytest.raises(ValueError, match="Duplicate"):
        stalls.analyze_stalls(source, tmp_path / "analysis")
