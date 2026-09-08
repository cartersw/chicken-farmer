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
            for player in (101, 102)]
        implementation = Path(collection.coverage.__file__).resolve()
        dependencies = [source_demo, phase_manifest, *(parsed/name for name in
            ("manifest.json", "rounds.parquet", "player_state.parquet", "usercmd.parquet")), implementation,
            *(implementation.with_name(name+".py") for name in
              ("competitive_buttons", "clock_evidence", "causal_acceptance", "jobs", "io"))]
        report = {"schema_version": 1, "profile": collection.coverage.PROFILE, "status": "complete",
            "demo_id": demo_id, "training_ready": False,
            "configuration": {"clip_ticks": options["clip_ticks"]}, "candidate_pool": candidates,
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
    assert len(fixture["discoveries"]) == len(fixture["clocks"]) == 2
    for source, selected, windows in fixture["clocks"]:
        assert len(selected) == 2 and windows == [{"start_demo_tick": 2968, "end_demo_tick": 3672}]
    assert not (fixture["collection_out"]/"batch/runs").exists()
    retained = collection.batch.load_batch_plan(Path(report["batch_plan"]))[1]
    assert len(retained["jobs"]) == 4
    assert all(job["job"]["end_demo_tick"]-job["job"]["start_demo_tick"] == 640 for job in retained["jobs"])


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
