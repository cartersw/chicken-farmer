"""Phase gating exercises captured ESL evidence and synthetic rollback edge cases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data.jobs import classify_round_phases, phase_evidence, render_jobs


def event(kind, tick, round_id=7, total=0, **changes):
    return dict(kind=kind, demo_tick=tick, round_id=round_id, total_rounds_played=total,
                game_phase=2, is_match_started=True, is_warmup=False, score_ct=total,
                score_t=0, **changes)


def scored_round(round_id=7, tick=10, total=0):
    info = dict(round_id=round_id, start_tick=tick, freeze_end_tick=tick+10, end_tick=tick+20)
    events = [event("round_start", tick, round_id, total),
              event("freeze_end", tick+10, round_id, total),
              event("round_end", tick+20, round_id, total+1, detail={"reason": 8}),
              event("raw_cs_pre_restart", tick+25, round_id, total+1),
              event("round_end_official", tick+26, round_id, total+1)]
    return info, events


def test_actual_esl_knife_warmup_and_live_evidence_is_not_round_ordinal():
    fixture = json.loads((Path(__file__).parent / "fixtures/dust2-phase-v1.json").read_text())
    rounds = {r["round_id"]: r for r in fixture["rounds"]}
    phases = classify_round_phases(fixture["events"], rounds)
    assert [phases[i]["phase"] for i in (1, 2, 3)] == ["setup", "setup", "competitive"]
    knife = next(e for e in fixture["events"] if e["kind"] == "freeze_end" and e["round_id"] == 1)
    assert knife["is_match_started"] is True and knife["is_warmup"] is False
    assert knife["game_phase"] == 1
    assert phases[3]["competitive_round_number"] == 1
    # Moving the same rounds to completely different IDs must not change eligibility.
    for e in fixture["events"]:
        e["round_id"] += 40
    shifted = {i+40: {**row, "round_id": i+40} for i, row in rounds.items()}
    again = classify_round_phases(fixture["events"], shifted)
    assert [again[i+40]["phase"] for i in (1, 2, 3)] == ["setup", "setup", "competitive"]


def test_score_backup_rollback_discards_only_lost_results():
    first, a = scored_round(7, 10, 0)
    second, b = scored_round(8, 40, 1)
    restored = event("score_updated", 70, 8, 1, detail={"old": 2, "new": 1})
    phases = classify_round_phases(a+b+[restored], {7: first, 8: second})
    assert phases[7]["phase"] == "competitive"
    assert phases[8]["phase"] == "restarted"
    assert phases[8]["reason_codes"] == ["scored_result_later_rolled_back"]
    assert phases[8]["evidence"][-1]["demo_tick"] == 70


@pytest.mark.parametrize("mutation,expected", [
    ("unknown_phase", "unknown"), ("missing_score", "unknown"),
    ("missing_freeze", "unknown"), ("game_start_end", "restarted"),
    ("warmup_midround", "unknown"), ("match_stopped", "unknown"),
])
def test_ambiguous_and_restarted_rounds_fail_closed(mutation, expected):
    info, events = scored_round()
    if mutation == "unknown_phase":
        events[1]["game_phase"] = 6
    elif mutation == "missing_score":
        for e in events:
            e["total_rounds_played"] = e["score_ct"] = 0
    elif mutation == "missing_freeze":
        events.pop(1)
    elif mutation == "game_start_end":
        events[2]["detail"]["reason"] = 16
    elif mutation == "warmup_midround":
        events.insert(2, {**event("warmup_changed", 25), "is_warmup": True})
    elif mutation == "match_stopped":
        events.insert(2, {**event("match_started_changed", 25), "is_match_started": False})
    phase = classify_round_phases(events, {7: info})[7]
    assert phase["phase"] == expected and phase["phase_verified"] is False


def test_inconsistent_phase_timeline_rejected():
    info, events = scored_round()
    with pytest.raises(ValueError, match="canonical"):
        classify_round_phases(events, {7: {**info, "start_tick": 11}})
    with pytest.raises(ValueError, match="reverses"):
        classify_round_phases(events[::-1], {7: info})
    events[1]["is_match_started"] = None
    with pytest.raises(ValueError, match="rule state"):
        classify_round_phases(events, {7: info})


def write_sources(tmp_path):
    demo = tmp_path / "test.dem"
    demo.write_bytes(b"synthetic phase test demo")
    digest = hashlib.sha256(demo.read_bytes()).hexdigest()
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    info, events = scored_round()
    info["demo_id"] = digest
    states = [dict(demo_id=digest, round_id=7, demo_tick=tick, player_slot=2,
                   steam_id=76561198000000001, spectator_user_id=2, alive=True,
                   is_warmup=False, is_freeze_time=False, is_paused=False)
              for tick in range(20, 30)]
    for name, rows in (("rounds.parquet", [info]), ("player_state.parquet", states), ("usercmd.parquet", states)):
        pq.write_table(pa.Table.from_pylist(rows), parsed/name)
    manifest = dict(demo_id=digest, sha256=digest, demo_path=str(demo), parse_status="complete", partial=False,
                    parser_schema_version="2", files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in parsed.glob("*.parquet")})
    (parsed/"manifest.json").write_text(json.dumps(manifest))
    audit = dict(schema_version=1, demo_id=digest, source_demo_sha256=digest, parser="demoinfocs-golang/v6",
                 parser_version="v6.0.0-alpha.0", parse_status="complete", partial=False, timing_clock="demo_tick",
                 events=events+[event("demo_end", 40, total=1)])
    phase_path = tmp_path/"phase.json"
    phase_path.write_text(json.dumps(audit))
    return parsed, phase_path, manifest, info


def test_default_jobs_gate_phase_and_bound_trim_with_fresh_coverage(tmp_path):
    parsed, phase_path, manifest, _ = write_sources(tmp_path)
    out = tmp_path/"job.jsonl"
    before = {p: p.read_bytes() for p in parsed.iterdir()}
    with pytest.raises(ValueError, match="phase-manifest"):
        render_jobs(parsed, out, min_ticks=1)
    report = render_jobs(parsed, out, min_ticks=1, phase_manifest=phase_path, start_demo_tick=23, end_demo_tick=27)
    job = json.loads(out.read_text())
    assert (job["start_demo_tick"], job["end_demo_tick"]) == (23, 27)
    assert job["command_coverage"]["command_count"] == 4
    assert job["command_coverage"]["observed_tick_fraction"] == 1
    assert job["phase_evidence"]["phase_verified"] is True
    assert job["phase_evidence"]["source_phase_sha256"] == hashlib.sha256(phase_path.read_bytes()).hexdigest()
    assert job["training_ready"] is False and report["unverified_phase_jobs"] == 0
    assert all(p.read_bytes() == data for p, data in before.items())
    with pytest.raises(ValueError, match="overwrite"):
        render_jobs(parsed, out, min_ticks=1, phase_manifest=phase_path)


def test_foreign_or_partial_sidecar_rejected_even_diagnostic(tmp_path):
    parsed, phase_path, manifest, info = write_sources(tmp_path)
    original = json.loads(phase_path.read_text())
    for changes in ({"source_demo_sha256": "0"*64}, {"partial": True}, {"schema_version": 2}):
        phase_path.write_text(json.dumps({**original, **changes}))
        with pytest.raises(ValueError, match="Phase sidecar"):
            render_jobs(parsed, tmp_path/"job.jsonl", min_ticks=1, phase_manifest=phase_path, allow_unverified_phase=True)
    unknown = phase_evidence(None, manifest, {7: info})[7]
    assert unknown["phase"] == "unknown" and unknown["phase_verified"] is False
