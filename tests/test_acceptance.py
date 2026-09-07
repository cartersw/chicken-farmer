from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import acceptance as a
from cs2_data.io import sha256_file
from cs2_data.validation import PAUSE_FLAGS, load_state_context


IDENTITY = dict(demo_id="demo", round_id=1, steam_id="76561198000000001", player_slot=2)
PHASE = dict(phase="competitive", phase_verified=True)
ROUND = dict(round_id=1, freeze_end_tick=99, end_tick=200)


def command(index, **changes):
    return {**IDENTITY, "steam_id": int(IDENTITY["steam_id"]), "command_row_id": 10+index,
            "demo_tick": 100+index, "command_number": 100+index, "client_tick": 1096+index,
            "server_tick_executed": 1100+index, "pawn_entity_handle": 42,
            "base_present": True, "buttons_present": True, "viewangles_present": True,
            "alive": True, "is_warmup": False, "is_freeze_time": False,
            "forwardmove": 0.0, "leftmove": None, "upmove": None,
            "mousedx_raw": None, "mousedy_raw": None,
            "view_yaw": 20.0+index, "view_pitch": 0.0, "view_roll": None,
            "buttonstate1": None, "buttonstate2": None, "buttonstate3": None,
            "subtick_moves": [], "input_history": [], "command_protobuf": b"\x08\x01",
            "delta_yaw_deg": 1.0, "delta_pitch_deg": 0.0, "aim_valid": True, "reset_reason": None,
            "mousedx_effective": 0, "mousedy_effective": 0, **changes}


def fixture_data(count=10):
    frames = [{**IDENTITY, "clip_id": "clip", "frame_index": index,
               "source_demo_tick_start": 100.0+2*index, "source_demo_tick_end": 102.0+2*index,
               "action_window_demo_tick_start": 100.0+2*index, "action_window_demo_tick_end": 102.0+2*index,
               "command_row_ids": [11+2*index, 12+2*index]} for index in range(count)]
    commands = {10+index: command(index, frame_index=(index-1)//2) for index in range(1, 2*count+1)}
    states = {tick: {**IDENTITY, "demo_tick": tick, "alive": True, "is_warmup": False,
                     "is_freeze_time": False, "is_paused": False} for tick in range(99, 2*count+103)}
    checks = {name: {"status": "passed", "method": "synthetic_test_evidence", "evidence_count": count,
                    "scope": {"frame_indices": list(range(count)), "command_row_ids": list(commands)}}
              for name in a.REQUIRED_CHECKS}
    proof = {**IDENTITY, "clip_id": "clip", "checks": checks, "source_files": []}
    return frames, commands, states, proof


def candidates(data, **kwargs):
    frames, commands, states, proof = data
    issues = {rid: a.command_reasons(row, command(rid-11), IDENTITY) for rid, row in commands.items()}
    return list(a.sample_candidates(frames, commands, issues, states, ROUND, PHASE, proof, IDENTITY, **kwargs))


def test_default_history_uses_only_images_up_to_observation_and_preceding_actions():
    samples = candidates(fixture_data())
    assert len(samples) == 10
    assert [s["observation_frame_index"] for s in samples if s["training_ready"]] == [8, 9]
    sample = samples[8]
    assert sample["history_frame_indices"] == list(range(1, 9))
    assert sample["previous_action_interval_frame_indices"] == list(range(8))
    assert sample["target_interval_frame_indices"] == [8]
    assert sample["previous_action_command_row_ids"] == [[11+2*i, 12+2*i] for i in range(8)]
    assert sample["target_command_row_ids"] == [27, 28]
    assert "missing_previous_action_context" in samples[7]["reason_codes"]


def test_parameterized_horizon_rejects_tail_and_no_previous_actions_ignores_unused_history_inputs():
    data = fixture_data()
    data[1][11]["subtick_moves"] = [{"when": -0.25}]
    samples = candidates(data, history_frames=3, target_horizon_frames=2, include_previous_actions=False)
    assert samples[2]["training_ready"]
    assert samples[2]["checked_command_row_ids"] == [15, 16, 17, 18]
    assert samples[2]["target_interval_frame_indices"] == [2, 3]
    assert samples[2]["previous_action_command_row_ids"] == []
    assert "insufficient_future_intervals" in samples[-1]["reason_codes"]


@pytest.mark.parametrize("changes,reason", [
    ({"subtick_moves": [{"when": -0.0001}]}, "invalid_subtick_fraction"),
    ({"input_history": [{"render_tick_fraction": float("nan")}]}, "invalid_history_fraction"),
    ({"subtick_moves": [{"yaw_delta": float("inf")}]}, "nonfinite_nested_action"),
    ({"base_present": False}, "missing_base_present"),
    ({"buttons_present": None}, "missing_buttons_present"),
    ({"viewangles_present": None}, "missing_viewangles_present"),
    ({"command_protobuf": b""}, "missing_raw_command_protobuf"),
    ({"command_number": 102}, "command_number_discontinuity"),
    ({"server_tick_executed": 1110}, "server_tick_executed_discontinuity"),
    ({"view_yaw": float("inf")}, "nonfinite_or_missing_viewangles"),
    ({"aim_valid": False}, "normalized_aim_invalid"),
    ({"delta_yaw_deg": 180.0}, "normalized_aim_mismatch"),
    ({"mousedx_effective": 77}, "normalized_mouse_mismatch"),
    ({"alive": None}, "command_not_known_alive"),
])
def test_invalid_inputs_are_rejected_without_repairing_canonical_values(changes, reason):
    row = command(1, **changes)
    assert reason in a.command_reasons(row, command(0), IDENTITY)


def test_known_parent_defaults_are_allowed_and_bad_input_contaminates_history_windows():
    assert a.command_reasons(command(1), command(0), IDENTITY) == set()
    data = fixture_data(20)
    data[1][15]["subtick_moves"] = [{"when": -0.25}]
    samples = candidates(data)
    assert "invalid_subtick_fraction" in samples[8]["reason_codes"]
    assert "invalid_subtick_fraction" in samples[10]["reason_codes"]
    assert samples[11]["training_ready"]


def test_unknown_or_unscoped_validation_never_accepts_and_negative_shot_evidence_wins():
    data = fixture_data()
    data[3]["checks"]["observation_clock"]["status"] = "unknown"
    assert "validation_observation_clock_unknown" in candidates(data)[8]["reason_codes"]
    data[3]["checks"]["observation_clock"]["status"] = "passed"
    data[3]["checks"]["pov_identity"]["scope"]["frame_indices"] = [8]
    assert "validation_pov_identity_scope_missing" in candidates(data)[8]["reason_codes"]
    data = fixture_data()
    data[3]["checks"]["weapon_shot_future"] = {"evidence": [{"command_row_id": 27, "status": "failed"}]}
    assert "validation_weapon_shot_future_failed" in candidates(data)[8]["reason_codes"]


def test_local_bad_pov_frame_rejects_only_intersecting_windows_but_clock_failure_is_global():
    data = fixture_data(15)
    check = data[3]["checks"]["pov_identity"]
    check["status"] = "failed"
    check["scope"]["frame_indices"].remove(6)
    check["evidence"] = [{"frame_index": index, "status": "failed" if index == 6 else "passed"}
                         for index in range(15)]
    samples = candidates(data, history_frames=3)
    assert samples[5]["training_ready"] and samples[10]["training_ready"]
    assert all("validation_pov_identity_failed" in samples[index]["reason_codes"] for index in range(6, 10))
    data[3]["checks"]["observation_clock"] = deepcopy(check)
    samples = candidates(data, history_frames=3)
    assert "validation_observation_clock_failed" in samples[5]["reason_codes"]
    assert "validation_observation_clock_failed" in samples[10]["reason_codes"]


@pytest.mark.parametrize("change,reason", [
    ({"is_paused": None}, "is_paused_unknown"),
    ({"is_paused": True}, "is_paused_active"),
    ({"alive": False}, "state_not_known_alive"),
    ({"steam_id": "other"}, "state_identity_mismatch"),
    ({"is_freeze_time": True}, "is_freeze_time_active"),
])
def test_entire_history_and_target_state_must_be_known_live(change, reason):
    data = fixture_data()
    data[2][104].update(change)
    assert reason in candidates(data)[8]["reason_codes"]


def test_unknown_phase_missing_state_and_unobserved_final_endpoint_fail_closed():
    data = fixture_data()
    frames, commands, states, proof = data
    issues = {rid: set() for rid in commands}
    samples = list(a.sample_candidates(frames, commands, issues, states, ROUND, {}, proof, IDENTITY))
    assert "competitive_phase_unverified" in samples[8]["reason_codes"]
    # The final endpoint is deliberately checked rather than extrapolated.
    states.pop(118)
    assert "missing_player_state" in candidates(data)[8]["reason_codes"]


def context_payload():
    return dict(schema_version=2, producer="cs2-context-v2", parse_status="complete", partial=False,
                parser="demoinfocs-golang/v6", parser_version="v6.0.0-alpha.0", property_prefix="m_pGameRules.",
                warning_policy="rule-and-shot-evidence-v1", evidence_loss_warnings=0, warnings={},
                demo_id="demo", source_demo_sha256="demo", tick_rate=64, timing_clock="demo_tick",
                required_pause_flags=list(PAUSE_FLAGS), segments=[dict(start_demo_tick=99, end_demo_tick=200,
                round_id=1, is_paused=False, is_warmup=False, is_freeze_time=False, match_started=True,
                game_phase=2, ambiguous_tick=False, pause_flags={name: False for name in PAUSE_FLAGS})])


def test_context_only_fills_unknown_pause_with_all_observed_flags_and_complete_coverage(tmp_path):
    context = context_payload()
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    checked = load_state_context(path, {"demo_id": "demo", "tick_rate": 64})
    checked["segments"] = [dict(row, _pause_evidence_verified=checked.get("pause_evidence_verified") is True)
                           for row in checked["segments"]]
    data = fixture_data()
    for row in data[2].values():
        row["is_paused"] = None
    assert candidates(data, context=checked["segments"])[8]["training_ready"]
    data[2][104]["is_paused"] = True
    assert "context_state_disagrees" in candidates(data, context=checked["segments"])[8]["reason_codes"]
    data[2][104]["is_paused"] = None
    checked["segments"][0]["start_demo_tick"] = 101
    assert "context_coverage_missing" in candidates(data, context=checked["segments"])[8]["reason_codes"]
    context["segments"][0]["pause_flags"].pop(PAUSE_FLAGS[0])
    path.write_text(json.dumps(context))
    with pytest.raises(ValueError, match="five observed flags"):
        load_state_context(path, {"demo_id": "demo", "tick_rate": 64})


def test_diagnostic_legacy_context_cannot_fill_unknown_canonical_pause():
    data = fixture_data()
    for row in data[2].values():
        row["is_paused"] = None
    context = context_payload()["segments"]
    context[0]["_pause_evidence_verified"] = False
    reasons = candidates(data, context=context)[8]["reason_codes"]
    assert "is_paused_unknown" in reasons and "context_pause_evidence_unverified" in reasons


def write_fixture(tmp_path, monkeypatch, *, context=False, bad_input=False):
    frames, commands, states, proof = fixture_data()
    if bad_input:
        commands[15]["subtick_moves"] = [{"when": -0.25}]
    parsed, dataset = tmp_path / "parsed", tmp_path / "dataset"
    parsed.mkdir()
    (dataset / "aligned").mkdir(parents=True)
    (dataset / "timing").mkdir()
    derived = {"frame_index", "delta_yaw_deg", "delta_pitch_deg", "aim_valid", "reset_reason",
               "mousedx_effective", "mousedy_effective"}
    raw = [{key: value for key, value in row.items() if key not in derived}
           for row in [command(0), *commands.values()]]
    if context:
        for row in states.values():
            row["is_paused"] = None
    for name, rows in (("usercmd", raw), ("player_state", list(states.values())), ("rounds", [ROUND])):
        pq.write_table(pa.Table.from_pylist(rows), parsed / f"{name}.parquet")
    source = dict(demo_id="demo", parse_status="complete", partial=False, parser_schema_version="2", tick_rate=64,
                  files={path.name: sha256_file(path) for path in parsed.glob("*.parquet")})
    (parsed / "manifest.json").write_text(json.dumps(source))
    for name, rows in (("frame_alignment", frames), ("aligned_commands", list(commands.values()))):
        pq.write_table(pa.Table.from_pylist(rows), dataset / "aligned" / f"{name}.parquet")
    (dataset / "timing/clip.json").write_text(json.dumps({**IDENTITY, "clip_id": "clip"}))
    (dataset / "timing/frames.jsonl").write_text("".join(json.dumps(row)+"\n" for row in frames))
    alignment = {**IDENTITY, "clip_id": "clip", "status": "complete", "alignment_version": 1,
                 "num_frames": len(frames), "command_count": len(commands), "normalization_included": True,
                 "action_interval_convention": "(start,end]", "source_usercmd_sha256": source["files"]["usercmd.parquet"],
                 "source_parsed_manifest_sha256": sha256_file(parsed / "manifest.json"),
                 "source_timing_sha256": sha256_file(dataset / "timing/frames.jsonl"),
                 "source_clip_manifest_sha256": sha256_file(dataset / "timing/clip.json"),
                 "files": {path.name: sha256_file(path) for path in (dataset / "aligned").glob("*.parquet")}}
    (dataset / "aligned/alignment_manifest.json").write_text(json.dumps(alignment))
    pipeline = dict(schema_version=1, demo_id="demo", clip_id="clip", status="complete", stages={"alignment": alignment})
    (dataset / "pipeline_manifest.json").write_text(json.dumps(pipeline))
    context_path = None
    if context:
        context_path = tmp_path / "context.json"
        context_path.write_text(json.dumps(context_payload()))
        proof["source_files"].append(dict(role="state_context", path=str(context_path.resolve()), sha256=sha256_file(context_path)))
    validation_path = tmp_path / "clip_validation.json"
    validation_path.write_text(json.dumps(proof))
    # Isolate acceptance policy from actual renderer/validator fixtures. The
    # production entry point always recomputes validation; its tampering tests
    # live in test_validation.py. No production option bypasses that recompute.
    def verify(path, parsed_arg, dataset_arg, state_context=None):
        assert parsed_arg == parsed and dataset_arg == dataset and state_context == context_path
        if json.loads(path.read_text()) != proof:
            raise ValueError("Validation report differs from recomputed source evidence")
        return deepcopy(proof)
    monkeypatch.setattr(a, "load_validation", verify)
    monkeypatch.setattr(a, "phase_evidence", lambda *args: {1: PHASE})
    return parsed, dataset, validation_path, context_path


def test_manifest_partition_preserves_originals_and_refuses_overwrite(tmp_path, monkeypatch):
    parsed, dataset, validation, context = write_fixture(tmp_path, monkeypatch, context=True)
    before = {str(path): sha256_file(path) for path in tmp_path.rglob("*") if path.is_file()}
    out = tmp_path / "accepted"
    report = a.accept_samples(parsed, dataset, validation, out, state_context=context)
    assert (report["candidate_count"], report["accepted_count"], report["rejected_count"]) == (10, 2, 8)
    assert report["eligible_window_count"] == 2
    accepted = [json.loads(line) for line in (out / "accepted_samples.jsonl").read_text().splitlines()]
    assert all(row["training_ready"] and not row["reason_codes"] for row in accepted)
    assert accepted[0]["normalization_predecessor_command_row_ids"] == list(range(10, 28))
    assert "training_ready" not in report
    assert report["source_state_context_sha256"] == sha256_file(context)
    assert all(sha256_file(Path(path)) == digest for path, digest in before.items())
    with pytest.raises(ValueError, match="fresh output"):
        a.accept_samples(parsed, dataset, validation, out, state_context=context)


def test_bad_input_keeps_all_rejected_without_promoting_report(tmp_path, monkeypatch):
    parsed, dataset, validation, _ = write_fixture(tmp_path, monkeypatch, bad_input=True)
    report = a.accept_samples(parsed, dataset, validation, tmp_path / "accepted")
    assert report["accepted_count"] == 0 and report["rejected_count"] == 10
    assert report["reason_counts"]["invalid_subtick_fraction"] > 0


@pytest.mark.parametrize("target", ["raw", "aligned", "validation", "context"])
def test_source_or_report_tampering_aborts_without_completed_manifest(tmp_path, monkeypatch, target):
    parsed, dataset, validation, context = write_fixture(tmp_path, monkeypatch, context=True)
    path = {"raw": parsed / "usercmd.parquet", "aligned": dataset / "aligned/aligned_commands.parquet",
            "validation": validation, "context": context}[target]
    path.write_bytes(path.read_bytes()+b" ")
    if target == "validation":
        proof = json.loads(path.read_text())
        proof["checks"]["pov_identity"]["status"] = "unknown"
        path.write_text(json.dumps(proof))
    with pytest.raises(ValueError):
        a.accept_samples(parsed, dataset, validation, tmp_path / "out", state_context=context)
    assert not (tmp_path / "out/acceptance_manifest.json").exists()


def test_failed_manifest_write_does_not_publish_partial_sample_lists(tmp_path, monkeypatch):
    parsed, dataset, validation, _ = write_fixture(tmp_path, monkeypatch)
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(a, "write_json", fail)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="disk failure"):
        a.accept_samples(parsed, dataset, validation, out)
    assert list(out.iterdir()) == []
