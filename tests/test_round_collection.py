"""Chronological source coverage and protected-batch planning invariants."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import round_collection as rounds
from test_competitive_batch import sources, write


@pytest.fixture
def round_source(sources, monkeypatch, tmp_path):
    entry = {key: value for key, value in sources["entries"][0].items() if key != "network_clock"}
    document = tmp_path/"round-sources.json"
    write(document, {"schema_version": 1, "profile": rounds.collection.SOURCE_PROFILE, "sources": [entry]})
    project = tmp_path/"project"
    for relative in ("bin/cs2-clocks.exe", "tools/usercmd-extractor/cmd/cs2-clocks/main.go"):
        path = project/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"clock fixture")
    monkeypatch.setattr(rounds, "PROJECT", project)
    state = {"windows": [(3, 4906, 10459), (4, 12187, 18716)], "discovery_calls": [], "clocks": []}

    def jobs(parsed, demo, out, **options):
        state["discovery_calls"].append(deepcopy(options))
        rows = []
        for round_id, start, end in state["windows"]:
            if options.get("round_id", round_id) != round_id or options.get("steam_id", 101) != 101:
                continue
            start = max(start, options.get("start_demo_tick", start))
            end = min(end, options.get("end_demo_tick", end))
            if end-start < options.get("min_ticks", 1):
                continue
            rows.append({"schema_version": 1, "timing_clock": "demo_tick", "demo_id": rounds.sha256_file(demo),
                "demo_path": str(demo), "clip_id": f"source-{round_id}-{start}", "round_id": round_id,
                "steam_id": "101", "player_slot": 1, "spectator_user_id": 1,
                "start_demo_tick": start, "end_demo_tick": end, "fps": 32, "width": 1280, "height": 720,
                "phase_evidence": {"phase": "competitive", "phase_verified": True},
                "command_coverage": {"complete_within_gap_limit": True}})
        if state.get("corrupt_player"):
            for row in rows: row["steam_id"] = "102"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(row)+"\n" for row in rows))
        return {"job_count": len(rows)}

    def clock(source, selected, out, executable):
        windows = rounds.collection.clock_windows(selected)
        state["clocks"].append(deepcopy(windows))
        write(out, {"windows": windows})
        if state.get("mutate_source"):
            Path(source["state_context"]).write_text("changed")
        return windows

    monkeypatch.setattr(rounds, "render_jobs", jobs)
    monkeypatch.setattr(rounds.batch, "render_jobs", jobs)
    monkeypatch.setattr(rounds.collection, "_clock", clock)
    return {**sources, "document": document, "out": tmp_path/"round-collection", "state": state}


def plan(fixture, **options):
    return rounds.plan_round_collection(fixture["document"], fixture["out"],
        **{"steam_id": "101", "round_ids": [3, 4], **options})


@pytest.mark.parametrize("clip_ticks,job_count,batch_counts", [(640, 20, [8, 1, 10, 1]), (1280, 11, [4, 1, 5, 1])])
def test_first_two_rounds_are_chronological_and_tails_are_accounted(round_source, clip_ticks, job_count, batch_counts):
    report = plan(round_source, clip_ticks=clip_ticks)
    assert report["job_count"] == job_count
    assert report["planned_source_seconds"] == 188.75
    assert report["coverage"] == {"eligible_ticks": 12082, "planned_ticks": 12080, "excluded_ticks": 2,
        "complete_source_coverage": False, "history_images": 8,
        "missing_initial_history_positions_per_clip": 7, "cross_clip_history_stitching": False}
    assert [(row["start_demo_tick"], row["end_demo_tick"], row["reason"]) for row in report["exclusions"]] == [
        (10458, 10459, "odd_final_tick_outside_32fps_capture_grid"),
        (18715, 18716, "odd_final_tick_outside_32fps_capture_grid")]
    assert [row["job_count"] for row in report["batches"]] == batch_counts
    assert [row["clip_ticks"] for row in report["batches"]] == [clip_ticks, 432, clip_ticks, 128]
    assert report["projected_bytes"] > 40_000_000_000
    assert report["projected_bytes"] < report["storage_budget_bytes"]
    assert report["projected_processing_seconds"] < report["time_budget_seconds"]
    assert report["training_ready"] is False and report["model_training_performed"] is False
    assert len([call for call in round_source["state"]["discovery_calls"] if call["min_ticks"] == 1]) == 1
    assert round_source["state"]["clocks"] == [[
        {"start_demo_tick": 4874, "end_demo_tick": 10490},
        {"start_demo_tick": 12155, "end_demo_tick": 18747}]]
    actual = []
    for entry in report["batches"]:
        path, loaded, digest = rounds.batch.load_batch_plan(Path(entry["plan"]))
        assert digest == entry["plan_sha256"] == report["source_files"][str(path)]
        assert len(loaded["jobs"]) == entry["job_count"]
        for row in loaded["jobs"]:
            job = row["job"]
            assert job["end_demo_tick"]-job["start_demo_tick"] == entry["clip_ticks"]
            actual.append([job["start_demo_tick"], job["end_demo_tick"]])
    assert actual == [interval for row in report["windows"] for interval in row["planned_intervals"]]
    assert not list(round_source["out"].rglob("runs"))
    assert rounds.read_json(round_source["out"]/"round_collection.json") == report
    rounds.batch._verify_hashes(report["source_files"])


def test_short_remainders_and_odd_grid_tick_are_separate_exclusions(round_source):
    round_source["state"]["windows"] = [(3, 3000, 3657), (4, 5000, 5017)]
    report = plan(round_source)
    assert report["job_count"] == 1 and report["coverage"]["excluded_ticks"] == 34
    assert report["windows"][0]["planned_intervals"] == [[3000, 3640]]
    assert report["windows"][1]["planned_intervals"] == []
    assert [(row["start_demo_tick"], row["end_demo_tick"]) for row in report["exclusions"]] == [
        (3640, 3656), (3656, 3657), (5000, 5016), (5016, 5017)]


def test_multiple_alive_windows_keep_their_gap_and_exact_source_order(round_source):
    round_source["state"]["windows"] = [(4, 9000, 9500), (3, 3000, 3700), (3, 4000, 4500)]
    report = plan(round_source, round_ids=[4, 3])
    assert [(row["round_id"], row["start_demo_tick"]) for row in report["windows"]] == [(3, 3000), (3, 4000), (4, 9000)]
    assert [row["planned_intervals"] for row in report["windows"]] == [[[3000, 3640], [3640, 3700]], [[4000, 4500]], [[9000, 9500]]]
    assert report["coverage"]["complete_source_coverage"] is True


@pytest.mark.parametrize("options,error", [
    ({"round_ids": [3, 5]}, "No eligible.*5"),
    ({"storage_budget_gb": 1}, "storage exceeds"),
    ({"time_budget_minutes": 1}, "processing time exceeds"),
])
def test_selection_or_projection_failure_cannot_create_clocks_or_final_plan(round_source, options, error):
    with pytest.raises(ValueError, match=error): plan(round_source, **options)
    assert not round_source["state"]["clocks"]
    assert not (round_source["out"]/"round_collection.json").exists()
    assert not (round_source["out"]/"batches").exists()


def test_more_than_twenty_four_jobs_is_rejected_before_clock_extraction(round_source):
    round_source["state"]["windows"] = [(3, 3000, 3000+25*640), (4, 30000, 30640)]
    with pytest.raises(ValueError, match="1..24 total capture jobs"): plan(round_source)
    assert not round_source["state"]["clocks"]


@pytest.mark.parametrize("options", [
    {"steam_id": None}, {"steam_id": "0"}, {"round_ids": []}, {"round_ids": [True]},
    {"round_ids": [3, 3]}, {"round_ids": "3,4"}, {"clip_ticks": 1282}, {"clip_ticks": 319},
    {"storage_budget_gb": float("nan")}, {"time_budget_minutes": -1}, {"free_space_floor_gb": True},
])
def test_invalid_configuration_is_rejected_before_writing(round_source, options):
    with pytest.raises(ValueError): plan(round_source, **options)
    assert not round_source["out"].exists()


@pytest.mark.parametrize("condition,error", [("corrupt_player", "lost source, player"), ("mutate_source", "hash mismatch|bytes changed|Conflicting batch source versions")])
def test_source_or_player_drift_cannot_publish_plan(round_source, condition, error):
    round_source["state"][condition] = True
    with pytest.raises(ValueError, match=error): plan(round_source)
    assert not (round_source["out"]/"round_collection.json").exists()


def test_batch_omission_cannot_publish_partial_coverage(round_source, monkeypatch):
    original = rounds.batch.plan_batch
    def drop(*args, **kwargs):
        report = original(*args, **kwargs)
        report["jobs"] = report["jobs"][1:]
        return report
    monkeypatch.setattr(rounds.batch, "plan_batch", drop)
    with pytest.raises(ValueError, match="changed chronological coverage"): plan(round_source)
    assert not (round_source["out"]/"round_collection.json").exists()


def test_changed_retained_plan_is_detected_by_collection_source_hashes(round_source):
    report = plan(round_source)
    Path(report["batches"][0]["plan"]).write_text("{}")
    with pytest.raises(ValueError, match="bytes changed"):
        rounds.batch._verify_hashes(report["source_files"])


def test_multiple_sources_are_rejected_before_discovery(round_source):
    document = rounds.read_json(round_source["document"])
    document["sources"].append(deepcopy(document["sources"][0]))
    write(round_source["document"], document)
    with pytest.raises(ValueError, match="exactly one source"): plan(round_source)
    assert not round_source["state"]["discovery_calls"] and not round_source["out"].exists()


def test_cli_defaults_to_first_two_rounds_and_forwards_budgets(monkeypatch, capsys):
    captured = []
    def prepare(**kwargs):
        captured.append(kwargs)
        return {"status": "planned_not_rendered", "job_count": 11, "planned_source_seconds": 188.75, "projected_bytes": 44_000_000_000}
    monkeypatch.setattr(rounds, "plan_round_collection", prepare)
    rounds.main(["--sources", "sources.json", "--out", "out", "--steam-id", "101", "--clip-ticks", "1280"])
    assert captured[0] == {"sources_manifest": Path("sources.json"), "out": Path("out"), "steam_id": "101",
        "round_ids": [3, 4], "clip_ticks": 1280, "storage_budget_gb": 60,
        "time_budget_minutes": 90, "free_space_floor_gb": 100}
    assert json.loads(capsys.readouterr().out)["job_count"] == 11
