"""Profile authority and label-response diagnostics, using bounded source fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import runpy

import pytest

from cs2_data import control_label_audit as audit
from cs2_data.io import sha256_file
from test_control_labels import command


def issued(rows=(), source_files=None, pixel_stats=None):
    return audit._issue_profile({"provenance_sha256": "a" * 64, "binary_profile": {"server": "b" * 64},
        "degrees_per_mouse_count": [[-.022, 0.], [0., .022]],
        "mouse_counts_per_degree": [[-1/.022, 0.], [0., 1/.022]], "sensitivity": 1.,
        "ready_requirements": {"sensitivity": "1"}, "provenance": {"nested": [{"a": 1}]},
        "supported_button_masks": audit.BUTTON_MASKS},
        [audit.canonical_row_sha256(row) for row in rows], source_files or {}, pixel_stats or {})


def test_profile_cannot_be_constructed_from_a_user_verification_flag():
    with pytest.raises(TypeError, match="issued"):
        audit.MeasuredControlProfile(verified=True)
    with pytest.raises(ValueError, match="not issued"):
        object.__new__(audit.MeasuredControlProfile).require_checked()


def test_module_cli_dispatches_through_canonical_sealed_class_namespace(monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "main", lambda: calls.append("canonical_main"))
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module(audit.__name__, run_name="__main__")
    assert calls == ["canonical_main"]


def test_profile_snapshot_is_deeply_immutable_and_detached_from_serialization():
    profile = issued()
    with pytest.raises(AttributeError):
        profile.sensitivity = 99
    with pytest.raises(TypeError):
        profile.binary_profile["server"] = "c" * 64
    with pytest.raises(TypeError):
        profile.provenance["nested"][0]["a"] = 2
    with pytest.raises(TypeError):
        profile.degrees_per_mouse_count[0][0] = 0
    description = profile.to_dict()
    description["provenance"]["nested"][0]["a"] = 2
    assert profile.provenance["nested"][0]["a"] == 1
    object.__setattr__(profile, "_row_digests", frozenset({"forged"}))
    with pytest.raises(ValueError, match="not issued"):
        profile.require_checked()


@pytest.mark.parametrize("old,new", [(None, 0), (False, 0), (1, 1.), (b"1", "1"), (0., -0.)])
def test_complete_row_digest_preserves_raw_scalar_types_and_presence(old, new):
    assert audit.canonical_row_sha256({"value": old}) != audit.canonical_row_sha256({"value": new})


def test_source_hash_and_pixel_metadata_recheck_detect_changes(tmp_path):
    data, pixel = tmp_path / "commands", tmp_path / "frame.tga"
    data.write_bytes(b"canonical"); pixel.write_bytes(b"pixels")
    stat = pixel.stat()
    profile = issued(source_files={str(data): sha256_file(data)}, pixel_stats={str(pixel): (stat.st_size, stat.st_mtime_ns)})
    profile.verify_sources_unchanged()
    data.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed"):
        profile.verify_sources_unchanged()
    data.write_bytes(b"canonical"); pixel.write_bytes(b"different pixels")
    with pytest.raises(ValueError, match="pixel file changed"):
        profile.verify_sources_unchanged()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def source_loader_fixture(tmp_path, monkeypatch):
    mouse, mouse_parsed, keyboard, keyboard_parsed = [tmp_path / name for name in ("mouse", "mouse-parsed", "keyboard", "keyboard-parsed")]
    protocol = tmp_path / "keyboard-protocol.json"
    _write_json(protocol, {"protocol": "frozen-test-fixture"})
    command_rows = [command(n) for n in (100, 101, 102)]
    for run, parsed in ((mouse, mouse_parsed), (keyboard, keyboard_parsed)):
        run.mkdir(); parsed.mkdir()
        plugin = run / "renderer-sandbox/bin/win64/server.dll"
        plugin.parent.mkdir(parents=True); plugin.write_bytes(b"native-observation-plugin")
        worker = {"binary_profile": dict(audit.MEASURED_BINARY_PROFILE), "plugin_sha256": sha256_file(plugin)}
        _write_json(run / "calibration.json", worker)
        config = {"event": "synthetic_input_configuration", "binding_setup_commands": ["bind w +forward", "bind a +left",
            "bind s +back", "bind d +right", "bind SPACE +jump", "bind CTRL +duck", "bind r +reload", "bind MOUSE1 +attack", "sensitivity 1"],
            "observed_convars": {"sensitivity": "1.096426", "m_pitch": "0.022000", "m_yaw": "0.022000"}}
        (run / "calibration_ledger.jsonl").write_text(json.dumps(config) + "\n", encoding="utf-8")
        cfg = run / "replay-settings/cfg/cs2_user_convars_0_slot0.vcfg"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('"sensitivity" "1"\n"m_yaw" "0.022"\n"m_pitch" "0.022"\n"sensitivity_y_scale" "1"\n')
        frame = run / "frames/one.tga"; frame.parent.mkdir(); frame.write_bytes(b"RGB")
        _write_json(run / "capture_frame_files.json", {"frames": [{"archived_name": "one.tga"}]})
        _write_json(parsed / "manifest.json", {"demo_id": command_rows[0]["demo_id"]})
        (parsed / "usercmd.parquet").write_bytes(b"fixture-canonical-command-table")
    def hashes(base, names):
        return {name: sha256_file(base / name) for name in names}
    mouse_report = {"status": "bounded_mouse_gain_calibrated", "issues": [],
        "source_hashes": hashes(mouse, ("calibration.json", "calibration_ledger.jsonl")),
        "parsed_hashes": hashes(mouse_parsed, ("manifest.json", "usercmd.parquet")),
        "source_pixel_audit": {"frames": [{}], "source_hashes": hashes(mouse, ("capture_frame_files.json",))},
        "gain_matrix": {"degrees_per_mouse_count": [[-.022, 0.], [0., .022]], "mouse_counts_per_degree": [[-1/.022, 0.], [0., 1/.022]]},
        "frozen_plan_sha256": "f" * 64,
        "cases": [{"recorded_command_diagnostics": {"status": "measured_raw_command_neighborhood", "mouse_count_sums": {
            "mousedx_raw": {"sum_matches_injected_count": True}, "mousedy_raw": {"sum_matches_injected_count": True}}}}]}
    key_report = {"status": "response_evidence_observed_for_all_phases", "edge_status_counts": {"response_record_observed": 20},
        "phase_status_counts": {"observed_response_evidence": 10}, "frozen_protocol": {"sha256": sha256_file(protocol)},
        "sources": {str(path): {"path": str(path), "sha256": sha256_file(path)} for path in
                    (protocol, keyboard_parsed / "manifest.json", keyboard_parsed / "usercmd.parquet", keyboard / "calibration.json")}}
    pixel_report = {"status": "all_archived_pixels_match_readback", "frames": [{}],
        "source_hashes": hashes(keyboard, ("calibration.json", "calibration_ledger.jsonl", "capture_frame_files.json"))}
    calls = []
    def mouse_analyzer(run, parsed, output):
        assert (run, parsed) == (mouse, mouse_parsed)
        calls.append("mouse"); _write_json(output, mouse_report); output.with_suffix(".md").write_text("Recomputed mouse proof")
        return deepcopy(mouse_report)
    def keyboard_analyzer(run, parsed, proto, output):
        assert (run, parsed, proto) == (keyboard, keyboard_parsed, protocol)
        calls.append("keyboard"); _write_json(output, key_report); return deepcopy(key_report)
    def pixel_analyzer(run, output):
        assert run == keyboard
        calls.append("pixels"); _write_json(output, pixel_report); return deepcopy(pixel_report)
    monkeypatch.setattr(audit, "analyze_synthetic_input", mouse_analyzer)
    monkeypatch.setattr(audit, "analyze_synthetic_keyboard", keyboard_analyzer)
    monkeypatch.setattr(audit, "audit_calibration_pixels", pixel_analyzer)
    monkeypatch.setattr(audit, "batches", lambda path: [deepcopy(command_rows)])
    return {"args": (mouse, mouse_parsed, keyboard, keyboard_parsed, protocol), "output": tmp_path / "proof",
            "calls": calls, "mouse": mouse_report, "keyboard": key_report, "pixels": pixel_report, "rows": command_rows}


def test_loader_recomputes_evidence_and_keeps_early_sensitivity_observation(source_loader_fixture):
    f = source_loader_fixture
    profile = audit.load_measured_control_profile(*f["args"], evidence_output=f["output"])
    assert f["calls"] == ["mouse", "keyboard", "pixels"]
    profile.require_command_rows(f["rows"])
    profile.verify_sources_unchanged()
    assert profile.sensitivity == 1.
    assert profile.ready_requirements["sensitivity_y_scale"] == "1"
    assert profile.provenance["configuration_evidence"][0]["early_setup_observed_convars"]["sensitivity"] == "1.096426"
    assert profile.provenance["historical_ready_sensitivity_directly_observed"] is False
    assert (f["output"] / "profile.json").is_file()
    assert profile.to_dict()["training_ready"] is False
    f["rows"][1]["alive"] = False
    with pytest.raises(ValueError, match="outside"):
        profile.require_command_rows(f["rows"])


@pytest.mark.parametrize("kind", ["mouse", "keyboard", "pixels", "edge_count", "raw_count"])
def test_loader_refuses_failed_or_incomplete_recomputed_proof(source_loader_fixture, kind):
    f = source_loader_fixture
    if kind == "edge_count":
        f["keyboard"]["edge_status_counts"] = {"response_record_observed": 19}
    elif kind == "raw_count":
        f["mouse"]["cases"][0]["recorded_command_diagnostics"]["mouse_count_sums"]["mousedx_raw"]["sum_matches_injected_count"] = False
    else:
        f[kind]["status"] = "incomplete"
    with pytest.raises(ValueError, match="did not pass|disagree"):
        audit.load_measured_control_profile(*f["args"], evidence_output=f["output"])
    assert not (f["output"] / "profile.json").exists()


def test_loader_refuses_existing_output_and_changed_archived_plugin(source_loader_fixture):
    f = source_loader_fixture
    with pytest.raises(ValueError, match="fresh"):
        audit.load_measured_control_profile(*f["args"], evidence_output=f["args"][0])
    plugin = f["args"][0] / "renderer-sandbox/bin/win64/server.dll"
    plugin.write_bytes(b"different-plugin")
    with pytest.raises(ValueError, match="plugin bytes"):
        audit.load_measured_control_profile(*f["args"], evidence_output=f["output"])
    assert f["calls"] == []


def test_matching_but_unmeasured_game_builds_cannot_issue_a_profile(source_loader_fixture):
    f = source_loader_fixture
    for run in (f["args"][0], f["args"][2]):
        path = run / "calibration.json"
        worker = json.loads(path.read_text())
        worker["binary_profile"]["bin/win64/tier0.dll"] = "0" * 64
        _write_json(path, worker)
    with pytest.raises(ValueError, match="builds/plugins"):
        audit.load_measured_control_profile(*f["args"], evidence_output=f["output"])
    assert not f["output"].exists() and f["calls"] == []


def test_changed_persisted_sensitivity_is_not_hidden_by_requested_setup(source_loader_fixture):
    f = source_loader_fixture
    path = f["args"][0] / "replay-settings/cfg/cs2_user_convars_0_slot0.vcfg"
    path.write_text(path.read_text().replace('"sensitivity" "1"', '"sensitivity" "2"'))
    with pytest.raises(ValueError, match="mouse setting changed"):
        audit.load_measured_control_profile(*f["args"], evidence_output=f["output"])
    assert f["calls"] == []


def test_label_audit_preserves_gaps_as_masks_and_does_not_reuse_target_commands():
    rows = [command(n) for n in (100, 101, 102, 104, 105, 106)]
    labels, targets, stats = audit.audit_label_rows(rows, issued(rows))
    assert len(labels) == 2 and len(targets) == 4 and stats["unused_tail_rows"] == 1
    assert labels[0]["label_valid"] and not labels[1]["label_valid"]
    assert stats["field_counts"]["aim_delta_deg.yaw"] == {"valid": 1, "masked": 1}
    assert stats["field_counts"]["exact_button_event_order"] == {"masked": 2}


def test_keyboard_response_checks_do_not_treat_masked_edge_or_old_processed_record_as_verified():
    rows = [command(100, None), command(101, (8, 8, None)), command(102, (None, 8, None))]
    _, targets, _ = audit.audit_label_rows(rows, issued(rows))
    edge = {"id": "press", "control": "W", "pressed": True, "plane_change_consistency_command_numbers": [101],
            "observed_already_processed_before_insertion": 100}
    release = {**edge, "id": "release", "pressed": False, "plane_change_consistency_command_numbers": [102]}
    report = {"edges": [edge, release], "phases": [{"press_id": "press", "release_id": "release", "status": "observed_response_evidence",
                                                   "command_number_window_inclusive": [100, 104]}]}
    result = audit._keyboard_label_checks(report, targets)[0]
    assert result["status"] == "observed_response_and_labels_agree"
    # A same-window press/release has no net change but does retain both observed edges.
    assert targets[101][0]["buttons"]["forward"]["net_changed"]["value"] is False
    edge["observed_already_processed_before_insertion"] = 101
    assert audit._keyboard_label_checks(report, targets)[0]["status"] == "incomplete_or_inconsistent"


def test_mouse_cross_check_uses_recorded_degrees_without_scaling_targets_again():
    rows = [command(100, yaw=10, pitch=0), command(101, yaw=9.125, pitch=0), command(102, yaw=9.125, pitch=0)]
    profile = issued(rows)
    _, targets, _ = audit.audit_label_rows(rows, profile)
    report = {"gain_matrix": {"absolute_tolerance_degrees": .05, "relative_tolerance": .05}, "cases": [{
        "id": "fit-x", "split": "fit", "dx": 40, "dy": 0, "recorded_command_diagnostics": {
            "before_last_processed_command": 100, "after_last_processed_command": 102,
            "commands": [{"command_number": 101, "base_present": True, "mousedx_raw": 40, "mousedy_raw": None},
                         {"command_number": 102, "base_present": True, "mousedx_raw": None, "mousedy_raw": None}]}}]}
    result = audit._mouse_label_checks(report, targets, profile)[0]
    assert result["status"] == "recorded_label_and_measured_gain_agree"
    assert result["sum_recorded_label_yaw_pitch_degrees"] == pytest.approx([-.875, 0])
    report["cases"][0]["recorded_command_diagnostics"]["commands"][0]["mousedx_raw"] = -40
    assert audit._mouse_label_checks(report, targets, profile)[0]["status"] == "incomplete_or_inconsistent"
