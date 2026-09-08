"""Control audits extend, but never replace, recomputed source acceptance.

The shared synthetic acceptance fixture stubs native evidence acquisition. Raw
command, artifact, state, projection and loader checks still execute normally.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import causal_acceptance as causal
from cs2_data import control_audit as audit
from cs2_data.cli import main, parser
from test_causal_acceptance import fixture, command, rewrite_commands


def run(fixture):
    source = causal.accept_causal_samples(*fixture["arguments"])
    destination = fixture["tmp_path"] / "control"
    report = audit.audit_control_candidates(fixture["out"], destination)
    rows = [json.loads(line) for line in (destination / "control_candidates.jsonl").read_text().splitlines()]
    return source, report, rows, destination


def test_public_audit_rechecks_original_acceptance_and_all_three_commands(fixture):
    source, report, rows, destination = run(fixture)
    assert source["accepted_count"] == report["candidate_count"] == 2
    assert report["eligible_candidate_count"] == 2
    assert report["rejected_candidate_count"] == 0
    assert report["semantic_button_label_count"] == report["training_ready_sample_count"] == 0
    assert not report["training_ready"] and not report["live_control_ready"]
    assert len(fixture["calls"]) == 2  # Initial acceptance + independent audit recomputation.
    assert rows[0]["target_command_row_ids"] == [117, 118]
    assert rows[0]["normalization_predecessor_command_row_id"] == 116
    assert rows[0]["action"]["angular_delta_deg"] == {"yaw": 2.0, "pitch": 0.0}
    assert len(rows[0]["images"]) == 8
    assert len(rows[0]["source_command_associations"]) == 3
    assert all(item["source_envelope"]["demo_command_kind"] == 7 for item in rows[0]["source_command_associations"])
    assert rows[0]["action"]["button_events"] is None
    assert audit.load_control_audit(destination) == report
    assert len(fixture["calls"]) == 3
    assert causal.load_causal_acceptance(fixture["out"]) == source


def test_second_target_flags_can_reject_a_new_candidate_without_changing_old_acceptance(fixture):
    rewrite_commands(fixture, lambda values: values[20].update(command_protobuf=command(120, flags=128)["command_protobuf"]))
    source, report, rows, _ = run(fixture)
    assert source["accepted_count"] == 2
    assert report["eligible_candidate_count"] == 1
    assert "target_unsupported_cmd_flags" in rows[1]["reason_codes"]
    assert rows[1]["action"]["angular_delta_deg"] is None
    assert rows[1]["action"]["channel_status"]["angular_delta_deg"]["available"] is False
    assert rows[1]["target_command_row_ids"] == [119, 120]


@pytest.mark.parametrize("defect", ["checkpoint", "missing", "duplicate", "client"])
def test_second_target_requires_its_own_unique_live_source_envelope(fixture, defect):
    packet = fixture["source"]["packets"][20]
    if defect == "checkpoint":
        packet["demo_command_kind"] = 13
    elif defect == "missing":
        packet["command_envelopes"] = []
    elif defect == "duplicate":
        fixture["source"]["packets"].append(deepcopy(packet))
    else:
        packet["command_envelopes"][0]["client_tick"] -= 1
    source, report, rows, _ = run(fixture)
    assert source["accepted_count"] == 2
    assert report["eligible_candidate_count"] == 1
    assert rows[1]["local_command_quality_valid"]
    assert not rows[1]["candidate_valid"]
    assert "contributing_live_source_envelope_unavailable_or_ambiguous" in rows[1]["reason_codes"]
    assert rows[1]["action"]["angular_delta_deg"] is None


def test_original_acceptance_flags_and_rehashed_samples_cannot_authorize_the_new_audit(fixture):
    causal.accept_causal_samples(*fixture["arguments"])
    path = fixture["out"] / "accepted_samples.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["targets"][0]["delta_yaw_deg"] = 10
    path.write_bytes(b"".join(causal._json_bytes(row) for row in rows))
    manifest = fixture["out"] / "causal_acceptance.json"
    report = json.loads(manifest.read_text())
    report["files"][path.name] = causal.sha256_file(path)
    manifest.write_bytes(causal._json_bytes(report))
    destination = fixture["tmp_path"] / "control"
    with pytest.raises(ValueError, match="recomputed evidence"):
        audit.audit_control_candidates(fixture["out"], destination)
    assert not (destination / "control_audit.json").exists()


@pytest.mark.parametrize("defect", ["angle", "buttons", "ready", "count", "schema"])
def test_new_audit_loader_recomputes_contents_instead_of_trusting_edited_hashes(fixture, defect):
    _, report, rows, destination = run(fixture)
    if defect == "count":
        report["eligible_candidate_count"] += 1
    elif defect == "schema":
        path = destination / "control_contract.schema.json"
        schema = json.loads(path.read_text())
        schema["properties"]["decision_period_ns"]["const"] = 1
        path.write_bytes(audit._json_bytes(schema))
        report["files"][path.name] = causal.sha256_file(path)
    else:
        if defect == "angle":
            rows[0]["action"]["angular_delta_deg"]["yaw"] = 99.0
        elif defect == "buttons":
            rows[0]["action"]["button_events"] = []
            rows[0]["action"]["channel_status"]["button_events"]["available"] = True
        else:
            rows[0]["training_ready"] = True
        path = destination / "control_candidates.jsonl"
        path.write_bytes(b"".join(audit._json_bytes(row) for row in rows))
        report["files"][path.name] = causal.sha256_file(path)
    (destination / "control_audit.json").write_bytes(audit._json_bytes(report))
    with pytest.raises(ValueError, match="recomputed evidence"):
        audit.load_control_audit(destination)


def test_changed_input_during_audit_never_publishes_completion(fixture, monkeypatch):
    causal.accept_causal_samples(*fixture["arguments"])
    original = audit.build_control_candidate

    def corrupt(*args, **kwargs):
        value = original(*args, **kwargs)
        fixture["images"][0]["sha256"] = "a"*64
        Path(fixture["images"][0]["path"]).write_bytes(b"edited after acceptance recomputed")
        return value

    monkeypatch.setattr(audit, "build_control_candidate", corrupt)
    destination = fixture["tmp_path"] / "control"
    with pytest.raises(ValueError, match="input hash mismatch"):
        audit.audit_control_candidates(fixture["out"], destination)
    assert not (destination / "control_audit.json").exists()


def test_output_directories_are_fresh_and_existing_acceptance_is_unchanged(fixture):
    _, report, _, destination = run(fixture)
    before = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match="fresh output"):
        audit.audit_control_candidates(fixture["out"], destination)
    assert before == {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
    assert report["input_hashes"]["acceptance"] == causal.sha256_file(fixture["out"] / "causal_acceptance.json")


def test_cli_contract_writes_standalone_schema_and_refuses_to_replace_it(tmp_path, capsys):
    destination = tmp_path / "contract.schema.json"
    assert main(["control-contract", "--out", str(destination)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision_period_ns"] == 31_250_000
    assert output["calibration_status"] == "unmeasured"
    schema = json.loads(destination.read_text())
    assert schema["properties"]["decision_period_ns"]["const"] == 31_250_000
    assert main(["control-contract", "--out", str(destination)]) == 2
    assert "Refusing to overwrite" in capsys.readouterr().err


def test_cli_audit_routes_the_reverified_pipeline(fixture, capsys):
    causal.accept_causal_samples(*fixture["arguments"])
    destination = fixture["tmp_path"] / "control"
    assert main(["audit-control-candidates", "--acceptance", str(fixture["out"]), "--out", str(destination)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["eligible_candidate_count"] == 2
    assert report["stage"] == "audit-control-candidates"
    parsed = parser().parse_args(["control-contract", "--out", "example.json"])
    assert parsed.out == Path("example.json")
