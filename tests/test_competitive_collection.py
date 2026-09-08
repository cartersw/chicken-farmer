"""Collection preserves selected coverage while preparing bounded clock evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2_data import competitive_collection as collection
from test_competitive_batch import sources, write


@pytest.fixture
def collection_source(sources, monkeypatch, tmp_path):
    entries = [{key: value for key, value in source.items() if key != "network_clock"} for source in sources["entries"]]
    source_path = tmp_path/"collection-sources.json"
    write(source_path, {"schema_version": 1, "profile": collection.SOURCE_PROFILE, "sources": entries})
    project = tmp_path/"project"
    for relative in ("bin/cs2-clocks.exe", "tools/usercmd-extractor/cmd/cs2-clocks/main.go"):
        path = project/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"fixture clock source")
    monkeypatch.setattr(collection, "PROJECT", project)
    discoveries, clocks = [], []

    def discover(parsed, source_demo, phase_manifest, out, **options):
        discoveries.append((parsed, options))
        demo_id = collection.sha256_file(source_demo)
        candidates = [{"candidate_id": collection.coverage._digest([demo_id, player])[:24],
            "demo_id": demo_id, "round_id": 3, "steam_id": str(player), "player_slot": player-100,
            "start_demo_tick": 3000, "end_demo_tick": 3000+options["clip_ticks"],
            "command_coverage_within_4_ticks": True, "ordinary_pool_eligible": True,
            "action_hints": {a: int(player == 102) for a in collection.coverage.ACTIONS}}
            for player in (101, 102) if options.get("steam_id") in (None, str(player))]
        implementation = Path(collection.coverage.__file__).resolve()
        dependencies = [source_demo, phase_manifest, *(parsed/name for name in
            ("manifest.json", "rounds.parquet", "player_state.parquet", "usercmd.parquet")), implementation,
            *(implementation.with_name(name+".py") for name in
              ("competitive_buttons", "clock_evidence", "causal_acceptance", "jobs", "io"))]
        report = {"schema_version": 1, "profile": collection.coverage.PROFILE, "status": "complete",
            "demo_id": demo_id, "training_ready": False,
            "configuration": {"clip_ticks": options["clip_ticks"], "steam_id": options.get("steam_id"),
                "selection_scope": "specific_player" if options.get("steam_id") is not None else "all_players"},
            "candidate_pool": candidates,
            "inputs": {"parsed": str(parsed), "demo": str(source_demo), "phase_manifest": str(phase_manifest)},
            "source_files": {str(p): collection.sha256_file(p) for p in dependencies}}
        report["report_sha256"] = collection.coverage._digest(report)
        write(out, report)
        return report

    def clock(source, selected, out, executable):
        windows = collection.clock_windows(selected)
        clocks.append((deepcopy(source), deepcopy(selected), windows))
        write(out, {"fixture_source": source["source_id"], "windows": windows})
        return windows

    monkeypatch.setattr(collection.coverage, "discover_candidates", discover)
    monkeypatch.setattr(collection, "_clock", clock)
    return {**sources, "source_path": source_path, "collection_out": tmp_path/"collection",
            "discoveries": discoveries, "clocks": clocks}


def test_collection_prepares_exact_selected_mixture_and_merged_clocks(collection_source):
    fixture = collection_source
    report = collection.prepare_collection(fixture["source_path"], fixture["collection_out"])
    assert report["status"] == "planned_not_rendered" and report["training_ready"] is False
    assert len(report["selected"]) == 4 and report["selection_purpose_counts"]["ordinary"] == 2
    assert report["planned_source_seconds"] == 40
    assert report["configuration"]["steam_id"] is None
    assert report["configuration"]["selection_scope"] == "all_players"
    assert len(fixture["discoveries"]) == len(fixture["clocks"]) == 2
    for source, selected, windows in fixture["clocks"]:
        assert len(selected) == 2 and windows == [{"start_demo_tick": 2968, "end_demo_tick": 3672}]
    assert not (fixture["collection_out"]/"batch/runs").exists()
    retained = collection.batch.load_batch_plan(Path(report["batch_plan"]))[1]
    assert len(retained["jobs"]) == 4
    assert all(job["job"]["end_demo_tick"]-job["job"]["start_demo_tick"] == 640 for job in retained["jobs"])


def test_specific_player_is_filtered_before_selection_clocks_and_batch(collection_source, monkeypatch):
    fixture = collection_source
    discover = collection.coverage.discover_candidates
    choose = collection.coverage.choose_candidates
    selected_pools = []

    def mixed_pool(*args, **kwargs):
        report = discover(*args, **kwargs)
        other = {**report["candidate_pool"][0], "candidate_id": report["demo_id"]+"-other", "steam_id": "102"}
        report["candidate_pool"].append(other)
        return report

    def inspect_pool(pool, **kwargs):
        selected_pools.append(deepcopy(pool))
        return choose(pool, **kwargs)

    monkeypatch.setattr(collection.coverage, "discover_candidates", mixed_pool)
    monkeypatch.setattr(collection.coverage, "choose_candidates", inspect_pool)
    report = collection.prepare_collection(fixture["source_path"], fixture["collection_out"], steam_id="00101")
    assert report["configuration"]["steam_id"] == "101"
    assert report["configuration"]["selection_scope"] == "specific_player"
    assert len(report["selected"]) == 2 and {row["steam_id"] for row in report["selected"]} == {"101"}
    assert all(row["steam_id"] == "101" for pool in selected_pools for row in pool)
    assert all(options["steam_id"] == "101" for _, options in fixture["discoveries"])
    assert all(row["steam_id"] == "101" for _, selected, _ in fixture["clocks"] for row in selected)
    retained = collection.batch.load_batch_plan(Path(report["batch_plan"]))[1]
    assert {str(job["job"]["steam_id"]) for job in retained["jobs"]} == {"101"}
    assert collection.read_json(fixture["collection_out"]/"collection_plan.json")["configuration"] == report["configuration"]


@pytest.mark.parametrize("steam_id", ["", "0", "-1", "18446744073709551616", "1.0", "1e2", " 101", "１０１", 101, True])
def test_invalid_player_identity_rejected_before_any_source_work(collection_source, steam_id):
    fixture = collection_source
    with pytest.raises(ValueError, match="positive uint64"):
        collection.prepare_collection(fixture["source_path"], fixture["collection_out"], steam_id=steam_id)
    assert not fixture["discoveries"] and not fixture["clocks"] and not fixture["collection_out"].exists()


def test_absent_player_retains_failed_discovery_without_clocks_or_batch(collection_source):
    fixture = collection_source
    with pytest.raises(ValueError, match="No competitive candidates.*Steam ID 999"):
        collection.prepare_collection(fixture["source_path"], fixture["collection_out"], steam_id="999")
    assert len(fixture["discoveries"]) == 2 and not fixture["clocks"]
    assert not (fixture["collection_out"]/"clocks").exists()
    assert not (fixture["collection_out"]/"batch").exists()
    assert not (fixture["collection_out"]/"collection_plan.json").exists()
    evidence = collection.read_json(fixture["collection_out"]/"collection_failure.json")
    assert evidence["status"] == "failed" and evidence["training_ready"] is False
    assert evidence["configuration"]["steam_id"] == "999" and evidence["selected"] == []
    assert len(evidence["discovery_reports"]) == 2
    assert all(Path(path).is_file() for path in evidence["discovery_reports"].values())


def test_matching_player_discovery_can_be_reused(collection_source):
    fixture = collection_source
    first = collection.prepare_collection(fixture["source_path"], fixture["collection_out"], steam_id="101")
    second = collection.prepare_collection(fixture["source_path"], fixture["collection_out"].with_name("reused"),
        steam_id="101", reuse_discovery=fixture["collection_out"]/"discovery")
    assert second["selected"] == first["selected"] and len(fixture["discoveries"]) == 2


@pytest.mark.parametrize("original_player,requested_player", [(None, "101"), ("101", "102"), ("101", None)])
def test_reused_bounded_discovery_must_match_player_scope(collection_source, original_player, requested_player):
    fixture = collection_source
    collection.prepare_collection(fixture["source_path"], fixture["collection_out"], steam_id=original_player)
    clock_count = len(fixture["clocks"])
    out = fixture["collection_out"].with_name("wrong-scope")
    with pytest.raises(ValueError, match="player scope differs.*fresh discovery"):
        collection.prepare_collection(fixture["source_path"], out, steam_id=requested_player,
            reuse_discovery=fixture["collection_out"]/"discovery")
    assert len(fixture["clocks"]) == clock_count and not (out/"clocks").exists()


def test_collection_cli_forwards_normalized_player_and_policy(monkeypatch, capsys):
    calls = []
    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "planned_not_rendered", "batch_plan": "batch.json",
            "selection_purpose_counts": {"ordinary": 1}, "planned_source_seconds": 10}
    monkeypatch.setattr(collection, "prepare_collection", prepare)
    collection.main(["--sources", "sources.json", "--out", "output", "--steam-id", "00101",
        "--max-jobs", "2", "--clip-ticks", "320", "--ordinary-fraction", "0.75"])
    assert calls == [((Path("sources.json"), Path("output")), {"steam_id": "101", "max_jobs": 2,
        "clip_ticks": 320, "ordinary_fraction": 0.75, "reuse_discovery": None})]
    assert json.loads(capsys.readouterr().out)["status"] == "planned_not_rendered"
    with pytest.raises(SystemExit) as error:
        collection.main(["--sources", "sources.json", "--out", "output", "--steam-id", "0"])
    assert error.value.code == 2 and len(calls) == 1


def test_reused_discovery_checks_source_and_checksum(collection_source):
    fixture = collection_source
    first = collection.prepare_collection(fixture["source_path"], fixture["collection_out"])
    second = collection.prepare_collection(fixture["source_path"], fixture["collection_out"].with_name("reused"),
                                            reuse_discovery=fixture["collection_out"]/"discovery")
    assert second["selected"] == first["selected"] and len(fixture["discoveries"]) == 2
    path = fixture["collection_out"]/"discovery/dust2.json"
    edited = collection.read_json(path)
    edited["candidate_pool"][0]["action_hints"]["reload"] = 999
    write(path, edited)
    with pytest.raises(ValueError, match="checksum"):
        collection.prepare_collection(fixture["source_path"], fixture["collection_out"].with_name("changed"),
                                      reuse_discovery=fixture["collection_out"]/"discovery")


def test_dropped_selected_clip_cannot_silently_change_action_share(collection_source, monkeypatch):
    original = collection.batch.plan_batch
    def dropped(*args, **kwargs):
        report = original(*args, **kwargs)
        report["jobs"] = report["jobs"][:-1]
        return report
    monkeypatch.setattr(collection.batch, "plan_batch", dropped)
    with pytest.raises(ValueError, match="mixture"):
        collection.prepare_collection(collection_source["source_path"], collection_source["collection_out"])
    assert not (collection_source["collection_out"]/"collection_plan.json").exists()


@pytest.mark.parametrize("change", ["missing_dependency", "missing_ordinary_marker"])
def test_rehashed_discovery_cannot_omit_required_provenance(collection_source, change):
    fixture = collection_source
    collection.prepare_collection(fixture["source_path"], fixture["collection_out"])
    path = fixture["collection_out"]/"discovery/dust2.json"
    edited = collection.read_json(path)
    if change == "missing_dependency":
        del edited["source_files"][str(Path(collection.coverage.__file__).resolve())]
    else:
        del edited["candidate_pool"][0]["ordinary_pool_eligible"]
    edited.pop("report_sha256")
    edited["report_sha256"] = collection.coverage._digest(edited)
    write(path, edited)
    with pytest.raises(ValueError, match="omits"):
        collection.prepare_collection(fixture["source_path"], fixture["collection_out"].with_name("missing"),
                                      reuse_discovery=fixture["collection_out"]/"discovery")


@pytest.mark.parametrize("change", ["duplicate_source", "duplicate_demo", "extra_fields", "missing_path"])
def test_collection_rejects_ambiguous_source_specs_before_discovery(collection_source, change):
    fixture = collection_source; document = collection.read_json(fixture["source_path"])
    if change == "duplicate_source":
        document["sources"][1]["source_id"] = document["sources"][0]["source_id"]
    elif change == "duplicate_demo":
        document["sources"][1] = {**document["sources"][0], "source_id": "second-name"}
    elif change == "extra_fields":
        document["sources"][0]["execute"] = True
    else:
        document["sources"][0]["demo"] = ""
    write(fixture["source_path"], document)
    with pytest.raises(ValueError):
        collection.prepare_collection(fixture["source_path"], fixture["collection_out"])
    assert not fixture["discoveries"] and not fixture["collection_out"].exists()


def test_clock_windows_merge_overlap_and_adjacency_without_losing_coverage():
    windows = collection.clock_windows([
        {"start_demo_tick": 1704, "end_demo_tick": 2344},
        {"start_demo_tick": 1000, "end_demo_tick": 1640},
        {"start_demo_tick": 1100, "end_demo_tick": 1740},
        {"start_demo_tick": 3000, "end_demo_tick": 3640},
    ])
    assert windows == [{"start_demo_tick": 968, "end_demo_tick": 2376},
                       {"start_demo_tick": 2968, "end_demo_tick": 3672}]


@pytest.mark.parametrize("selected", [[], [{"start_demo_tick": True, "end_demo_tick": 1000}],
    [{"start_demo_tick": 1000, "end_demo_tick": 10000000}],
    [{"start_demo_tick": 1000, "end_demo_tick": 22000}]])
def test_clock_windows_reject_invalid_or_excessive_domains(selected):
    with pytest.raises(ValueError):
        collection.clock_windows(selected)


def test_clock_process_uses_structured_arguments_and_checks_returned_windows(tmp_path, monkeypatch):
    calls = []; out = tmp_path/"clock evidence.json"
    selected = [{"start_demo_tick": 1000, "end_demo_tick": 1640}]
    source = {"demo": str(tmp_path/"demo with spaces.dem"), "demo_id": "f"*64}
    def run(arguments, **options):
        calls.append((arguments, options)); write(out, {})
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(collection.subprocess, "run", run)
    monkeypatch.setattr(collection, "load_network_clock", lambda *a: SimpleNamespace(document={"windows": []}))
    with pytest.raises(ValueError, match="different selected"):
        collection._clock(source, selected, out, tmp_path/"clock tool.exe")
    assert calls[0][0] == [str(tmp_path/"clock tool.exe"), "--input", source["demo"], "--out", str(out), "--windows", "968:1672"]
    assert "shell" not in calls[0][1]
