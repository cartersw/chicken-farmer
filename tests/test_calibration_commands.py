import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import calibration_commands as audit
from test_causal_acceptance import command


def data():
    commands = [command(116), command(117), command(118)]
    states = [{"demo_id": "a" * 64, "steam_id": commands[0]["steam_id"], "player_slot": 4,
        "round_id": 3, "spectator_user_id": 7, "demo_tick": 66, "view_yaw": 179.0, "crouching": False,
        "ammo_clip": 20}, {"demo_id": "a" * 64, "steam_id": commands[0]["steam_id"], "player_slot": 4,
        "round_id": 3, "spectator_user_id": 7, "demo_tick": 68, "view_yaw": -179.0, "crouching": True,
        "ammo_clip": 19}]
    ledger = [{"event": "action_dispatch", "id": "shot", "command": "+attack", "at_ms": 1000,
        "actual_elapsed_ms": 1015.625, "local_player_before": {"steam_id": str(commands[0]["steam_id"]),
            "controller_tick_base": 999, "pawn_state": {"movement_last_command_number_processed": 117,
                                                       "simulation_tick": 998}}}]
    manifest = {"demo_id": "a" * 64, "command_count": 3, "eligible_payload_count": 3,
        "full_payload_count": 3, "delta_payload_count": 0, "warnings": {}, "parse_status": "complete",
        "partial": False, "map": "de_dust2", "tick_rate": 64}
    return commands, states, [], ledger, manifest


def test_observed_numeric_neighborhood_does_not_certify_clock_mapping_or_buttons():
    report = audit.summarize_calibration_commands(*data())
    assert report["coverage"]["reconstruction_fraction"] == 1
    assert report["issue_codes"] == []
    assert report["status"] == "diagnostic_command_coverage_observed"
    assert all(report[key] is False for key in ("training_ready", "button_semantics_verified",
        "exact_input_timing_verified", "dispatch_to_command_mapping_verified"))
    action = report["dispatches"][0]
    assert action["controller_tick_base_observed"] == 999
    assert action["same_number_row_count"] == 1
    assert [r["command_number"] for r in action["command_number_neighborhood"]] == [116, 117, 118]
    assert action["input_consumption_command_number"] is None
    assert action["exact_input_time_ns"] is None
    assert action["command_number_neighborhood"][0]["raw_button_planes_hex"] == [
        "0x0000000000000001", "0x0000000000000000", "0x0000000000000002"]


def test_baseline_failure_preserves_delta_coverage_without_fabricating_commands():
    _, states, events, ledger, manifest = data()
    manifest.update(command_count=0, eligible_payload_count=706, full_payload_count=0,
                    delta_payload_count=706, warnings={"usercmd_baseline_missing": 706})
    report = audit.summarize_calibration_commands([], states, events, ledger, manifest)
    assert report["coverage"]["reconstruction_fraction"] == 0
    assert report["coverage"]["delta_payloads"] == 706
    assert "usercmd_baseline_missing" in report["issue_codes"]
    assert report["command_clocks"]["client_tick"] is None
    assert report["dispatches"][0]["command_number_neighborhood"] == []


def test_state_changes_retain_observation_gap_and_wrap_yaw():
    report = audit.summarize_calibration_commands(*data())
    change = report["state_transitions"][0]
    assert (change["previous_demo_tick"], change["demo_tick"]) == (66, 68)
    assert change["changes"]["view_yaw"]["wrapped_delta_deg"] == 2
    assert change["changes"]["ammo_clip"] == {"before": 20, "after": 19}


def test_other_player_and_missing_last_processed_do_not_create_associations():
    commands, states, events, ledger, manifest = data()
    ledger[0]["local_player_before"]["steam_id"] = "999"
    report = audit.summarize_calibration_commands(commands, states, events, ledger, manifest)
    assert report["dispatches"][0]["command_number_neighborhood"] == []
    ledger[0]["local_player_before"]["pawn_state"]["movement_last_command_number_processed"] = -1
    assert audit.summarize_calibration_commands(commands, states, events, ledger, manifest)["dispatches"][0]["command_number_neighborhood"] == []


def test_duplicate_command_number_is_explicitly_ambiguous():
    commands, states, events, ledger, manifest = data()
    commands.append(commands[1].copy())
    manifest.update(command_count=4, eligible_payload_count=4, full_payload_count=4)
    report = audit.summarize_calibration_commands(commands, states, events, ledger, manifest)
    assert "ambiguous_command_number_identity" in report["issue_codes"]
    assert report["dispatches"][0]["same_number_row_count"] == 2


def test_changed_projection_is_visible_in_report():
    commands, *rest = data()
    commands[0]["buttonstate1"] = 8
    report = audit.summarize_calibration_commands(commands, *rest)
    assert report["coverage"]["projection_reason_counts"]["canonical_action_disagrees_with_protobuf"] == 1
    assert "canonical_command_projection_requires_review" in report["issue_codes"]


def test_raw_transition_reports_command_gaps_without_promoting_button_events():
    commands, *rest = data()
    report = audit.summarize_calibration_commands([commands[0], commands[2]], *rest)
    transition = report["raw_command_transitions"][0]
    assert transition["command_number_gap"] == 2
    assert transition["changes"]["view_yaw"]["wrapped_delta_deg"] == 2
    assert transition["association"] == "raw_command_difference_not_calibrated_button_event"


@pytest.fixture
def artifacts(tmp_path):
    run, parsed = tmp_path / "run", tmp_path / "parsed"
    run.mkdir(); parsed.mkdir()
    commands, states, events, ledger, manifest = data()
    (run / "controlled.dem").write_bytes(b"synthetic diagnostic demo")
    digest = audit._sha(run / "controlled.dem")
    for row in commands + states:
        row["demo_id"] = digest
    (run / "calibration_ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ledger))
    (run / "calibration.json").write_text(json.dumps({
        "demo": {"sha256": digest}, "calibration_ledger": {"sha256": audit._sha(run / "calibration_ledger.jsonl")}}))
    manifest.update(demo_id=digest, sha256=digest, files={})
    for name, rows in (("usercmd", commands), ("player_state", states), ("events", events)):
        path = parsed / (name + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows), path)
        manifest["files"][path.name] = audit._sha(path)
    (parsed / "manifest.json").write_text(json.dumps(manifest))
    return run, parsed


def test_reader_checks_source_hashes_and_writes_only_fresh_reports(artifacts, tmp_path):
    run, parsed = artifacts
    report = audit.analyze_calibration_commands(run, parsed)
    assert report["coverage"]["reconstructed_commands"] == 3
    out = tmp_path / "result.json"
    assert audit.main(["--run", str(run), "--parsed", str(parsed), "--out", str(out)]) == 0
    with pytest.raises(FileExistsError):
        audit.main(["--run", str(run), "--parsed", str(parsed), "--out", str(out)])


@pytest.mark.parametrize("filename", ["controlled.dem", "calibration_ledger.jsonl", "usercmd.parquet"])
def test_reader_rejects_mutated_source_artifacts(artifacts, filename):
    run, parsed = artifacts
    path = (parsed if filename.endswith("parquet") else run) / filename
    with path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit.analyze_calibration_commands(run, parsed)
