import pytest

from test_calibration_windows import worker, completion_events, settled_completion_events, ledger


def test_old_recordings_remain_readable_but_do_not_pass_new_recording_requirement(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    path = ledger(tmp_path / "old.jsonl", completion_events(plan))
    worker.verify_native_completion(path, plan)
    with pytest.raises(ValueError, match="settling"):
        worker.verify_native_completion(path, plan, require_settle=True)


def test_continuous_window_is_recomputed(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    result = worker.verify_native_completion(ledger(tmp_path / "settled.jsonl", events), plan, require_settle=True)
    assert result["local_readiness"]["startup_settle"]["sample_count"] == 65
    assert result["calibration_accuracy_verified"] is False


@pytest.mark.parametrize("change", [
    lambda samples: samples[30].update(eligible=False, controller_tick_base=-1, pawn_handle=2**32-1),
    lambda samples: samples[30].update(pawn_handle=999),
    lambda samples: samples[30].update(controller_tick_base=0),
    lambda samples: samples[30].update(callback_gap_qpc=1000000),
    lambda samples: samples[30].update(qpc=samples[29]["qpc"]-1),
    lambda samples: samples[30].update(eligible=1),
    lambda samples: samples[30].update(controller_tick_base=True),
    lambda samples: [s.update(controller_tick_base=128) for s in samples],
])
def test_discontinuity_or_invalid_progress_cannot_be_hidden_by_summary(tmp_path, change):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    change([e for e in events if e["event"] == "startup_settle_sample"])
    with pytest.raises(ValueError, match="settling"):
        worker.verify_native_completion(ledger(tmp_path / "bad.jsonl", events), plan, require_settle=True)


def test_two_isolated_callbacks_do_not_establish_settling(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    first = True
    reduced = []
    for event in events:
        if event["event"] == "startup_settle_sample":
            if event["qpc"] not in (7000000, 9000000):
                continue
            if not first:
                event["callback_gap_qpc"] = 2000000
            first = False
        reduced.append(event)
    with pytest.raises(ValueError, match="settling"):
        worker.verify_native_completion(ledger(tmp_path / "sparse.jsonl", reduced), plan, require_settle=True)


def test_claimed_window_or_limits_cannot_override_measurements(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    for event in events:
        if event["event"] == "startup_settle_complete":
            event["evidence"]["maximum_gap_ms"] = 10000
        if event["event"] == "calibration_ready":
            event["startup_settle"]["maximum_gap_ms"] = 10000
    with pytest.raises(ValueError, match="settling"):
        worker.verify_native_completion(ledger(tmp_path / "override.jsonl", events), plan)


def test_an_earlier_gap_can_be_followed_by_a_new_complete_settling_window(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    first_index = next(i for i, r in enumerate(events) if r["event"] == "startup_settle_sample")
    earlier = {"schema_version": 1, "event": "startup_settle_sample", "qpc": 6500000,
               "callback_gap_qpc": 31250, "eligible": True, "controller_tick_base": 100, "pawn_handle": 456}
    events[first_index]["callback_gap_qpc"] = 500000
    events.insert(first_index, earlier)
    for event in events:
        if event["event"] == "startup_settle_complete":
            event["evidence"]["reset_count"] = 1
        if event["event"] == "calibration_ready":
            event["startup_settle"]["reset_count"] = 1
    result = worker.verify_native_completion(ledger(tmp_path / "recovered.jsonl", events), plan, require_settle=True)
    assert result["local_readiness"]["startup_settle"]["reset_count"] == 1


@pytest.mark.parametrize("replacement", [None, True, 999])
def test_settled_pawn_must_remain_the_capture_pawn(tmp_path, replacement):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    ready = next(r for r in events if r["event"] == "calibration_ready")
    ready["local_player"]["pawn_handle"] = replacement
    with pytest.raises(ValueError, match="settling pawn"):
        worker.verify_native_completion(ledger(tmp_path / "replacement.jsonl", events), plan)


def test_clock_reset_after_settling_does_not_pass_readiness(tmp_path):
    plan = worker.native_plan(worker.default_plan(), tmp_path, "a" * 32)
    events = settled_completion_events(plan)
    ready = next(r for r in events if r["event"] == "calibration_ready")
    ready["local_player"]["controller_tick_base"] = ready["start_tick_base"] = 100
    with pytest.raises(ValueError, match="settling pawn or clock"):
        worker.verify_native_completion(ledger(tmp_path / "reset.jsonl", events), plan)
