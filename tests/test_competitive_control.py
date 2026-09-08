"""Core fixtures replace only the independent replay-proof dependency."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import competitive_control as acceptance
from cs2_data.control_labels import BUTTON_MASKS, BUTTON_FIELDS, EXACT_CHANNELS
from cs2_data.causal_acceptance import protobuf_fields
from test_control_labels import command, with_client_ticks
from test_causal_acceptance import embedded, floating, integer


@pytest.fixture
def evidence(tmp_path):
    identity = {"demo_id": "a"*64, "round_id": 3, "steam_id": "76561198000000001", "player_slot": 4}
    commands = [command(n) for n in range(100, 131)]
    frames = []
    for n in range(10):
        image = tmp_path/f"frame-{n}.tga"
        image.write_bytes(b"Independent pixel verifier is replaced only in this core test " + str(n).encode())
        frames.append({"frame_index": n, "path": str(image.resolve()), "sha256": acceptance.sha256_file(image),
            "verified": True, "reason_codes": [], "clock_segment_id": "process-1",
            "observation_upper_execution_tick": 100+2*n, "source_demo_tick_start": 50+2*n,
            "source_demo_tick_end": 51+2*n, "upper_source_demo_tick": 50+2*n})
    source = tmp_path/"proof-source.json"
    source.write_text("unchanged independently audited fixture")
    return {"identity": identity, "clip_id": "fixture-clip", "frames": frames, "commands": commands,
        "states": [{**identity, "demo_tick": n, "alive": True, "is_warmup": False, "is_freeze_time": False, "is_paused": False} for n in range(40, 100)],
        "round_info": {"round_id": 3, "freeze_end_tick": 40, "end_tick": 100},
        "context_segments": [{"round_id": 3, "start_demo_tick": 40, "end_demo_tick": 100, "ambiguous_tick": False,
            "is_paused": False, "is_freeze_time": False, "is_warmup": False, "_pause_evidence_verified": True,
            "match_started": True, "game_phase": 2}],
        "phase": {"phase": "competitive", "phase_verified": True},
        "source_support": {"status": "verified", "support_interval": "[E-1,E]", "source_patch": 14178},
        "command_sources": {r["command_row_id"]: {"status": "verified", "source_envelope": {"demo_command_kind": 7,
            "command_number": r["command_number"], "server_tick_executed": r["server_tick_executed"]}} for r in commands},
        "semantic_buttons": {}, "provenance": {"test_fixture_only": True, "demo_id": identity["demo_id"]},
        "source_files": {str(source.resolve()): acceptance.sha256_file(source)}}


def accepted(evidence):
    return [s for s in acceptance._build_samples(evidence) if s["training_ready"]]


def semantics(evidence):
    evidence["semantic_buttons"] = {"status": "verified", "demo_id": evidence["identity"]["demo_id"], "source_patch": 14178,
        "proof_sha256": "b"*64, "supported_button_masks": dict(BUTTON_MASKS),
        "supported_fields": {name: [*BUTTON_FIELDS, "per_command_net_changed"] for name in BUTTON_MASKS},
        "row_proofs": {row["command_row_id"]: {"status": "verified",
            "canonical_row_sha256": acceptance.canonical_row_sha256(row),
            "supported_fields": {name: [*BUTTON_FIELDS, "per_command_net_changed"] for name in BUTTON_MASKS}}
            for row in evidence["commands"]}}


def test_eight_history_images_predecessor_and_two_targets_are_strictly_future(evidence):
    samples = accepted(evidence)
    assert len(samples) == 3
    sample = samples[0]
    assert [i["frame_index"] for i in sample["images"]] == list(range(8))
    assert sample["observation_upper_execution_tick"] == 114
    assert [s["role"] for s in sample["command_support"]] == ["predecessor", "target0", "target1"]
    assert [s["server_tick_executed"] for s in sample["command_support"]] == [116, 117, 118]
    assert [s["support_start_tick"] for s in sample["command_support"]] == [115, 116, 117]
    assert sample["label"]["aim_delta_deg"]["yaw"]["value"] == 2.
    assert not sample["previous_action_features_included"] and not sample["exact_input_timing_verified"]
    assert all(not sample["label"][key]["valid"] for key in EXACT_CHANNELS)


def test_local_calibration_does_not_silently_authorize_competitive_buttons(evidence):
    sample = accepted(evidence)[0]
    assert sample["training_ready"] and not sample["label"]["training_ready"]
    assert sample["label"]["label_valid"] and not sample["label"]["fully_observed"]
    assert sample["label"]["semantic_button_proof"] is None
    for button in sample["label"]["buttons"].values():
        assert button["held_end"]["value"] is None and not button["held_end"]["valid"]
        assert "competitive_button_semantics_unverified" in button["held_end"]["reason_codes"]


def test_separately_bound_semantics_unmask_only_supported_controls(evidence):
    semantics(evidence)
    del evidence["semantic_buttons"]["supported_button_masks"]["reload"]
    sample = accepted(evidence)[0]
    assert sample["label"]["buttons"]["forward"]["held_end"]["value"] is True
    assert not sample["label"]["buttons"]["reload"]["held_end"]["valid"]


def test_known_bit_mapping_does_not_authorize_unreviewed_plane_fields(evidence):
    semantics(evidence)
    evidence["semantic_buttons"]["supported_fields"]["forward"] = ["held_start", "held_mid", "held_end"]
    button = accepted(evidence)[0]["label"]["buttons"]["forward"]
    assert button["held_end"]["valid"]
    for field in ("net_changed", "net_pressed", "net_released", "recorded_activity_present", "unresolved_rapid_activity"):
        assert button[field]["value"] is None and not button[field]["valid"]
    assert not any(cell["valid"] for cell in button["per_command_net_changed"])


@pytest.mark.parametrize("fields", [None, "held_end", {"held_end": True}, [True], []])
def test_missing_or_malformed_field_allowlist_masks_button(evidence, fields):
    semantics(evidence)
    evidence["semantic_buttons"]["supported_fields"]["forward"] = fields
    sample = accepted(evidence)[0]
    assert sample["label"]["aim_delta_deg"]["yaw"]["valid"]
    assert not sample["label"]["buttons"]["forward"]["held_end"]["valid"]


def test_mapping_without_field_proof_keeps_all_buttons_masked(evidence):
    semantics(evidence)
    del evidence["semantic_buttons"]["supported_fields"]
    label = accepted(evidence)[0]["label"]
    assert all(not button["held_end"]["valid"] for button in label["buttons"].values())


def test_row_proof_does_not_authorize_other_endpoints_or_fields(evidence):
    semantics(evidence)
    del evidence["semantic_buttons"]["row_proofs"][117]["supported_fields"]["forward"]
    button = accepted(evidence)[0]["label"]["buttons"]["forward"]
    assert button["held_start"]["valid"] and button["held_end"]["valid"]
    assert not button["held_mid"]["valid"] and not button["net_changed"]["valid"]
    assert not button["per_command_net_changed"][0]["valid"]
    assert button["per_command_net_changed"][1]["valid"]


@pytest.mark.parametrize("change", ["absent", "digest", "status"])
def test_row_semantics_must_bind_the_exact_command(evidence, change):
    semantics(evidence)
    proofs = evidence["semantic_buttons"]["row_proofs"]
    if change == "absent": del proofs[118]
    elif change == "digest": proofs[118]["canonical_row_sha256"] = "c"*64
    else: proofs[118]["status"] = "unknown"
    label = accepted(evidence)[0]["label"]
    assert label["aim_delta_deg"]["yaw"]["valid"]
    assert not label["buttons"]["forward"]["held_end"]["valid"]
    assert label["buttons"]["forward"]["held_start"]["valid"]


@pytest.mark.parametrize("key,value", [("source_patch", 14180), ("demo_id", "c"*64), ("proof_sha256", "bad"), ("status", "unknown")])
def test_semantic_build_or_demo_mismatch_masks_buttons_without_discarding_angles(evidence, key, value):
    semantics(evidence)
    evidence["semantic_buttons"][key] = value
    sample = accepted(evidence)[0]
    assert sample["label"]["aim_delta_deg"]["yaw"]["valid"]
    assert not sample["label"]["buttons"]["forward"]["held_end"]["valid"]


def test_absent_button_parent_preserves_raw_null_and_masks_only_affected_channels(evidence):
    semantics(evidence)
    evidence["commands"] = [command(n, None) for n in range(100, 131)]
    sample = accepted(evidence)[0]
    assert sample["label"]["aim_delta_deg"]["yaw"]["valid"]
    assert sample["label"]["raw_command_provenance"][1]["button_planes_hex"] == [None]*3
    assert not sample["label"]["buttons"]["forward"]["held_end"]["valid"]


def test_same_end_state_tap_retains_activity_without_fabricated_exact_events(evidence):
    semantics(evidence)
    replacements = {116:command(116,(0,0,0)),117:command(117,(8,8,0)),118:command(118,(0,8,0))}
    evidence["commands"] = [replacements.get(r["command_number"],r) for r in evidence["commands"]]
    semantics(evidence)
    sample = accepted(evidence)[0]
    button = sample["label"]["buttons"]["forward"]
    assert button["net_changed"]["value"] is False and button["recorded_activity_present"]["value"] is True
    assert not sample["label"]["exact_button_event_count"]["valid"]


def test_competitive_client_generation_clock_can_advance_more_than_local_32fps_pattern(evidence):
    evidence["commands"] = with_client_ticks(evidence["commands"], [n*5 for n in range(len(evidence["commands"]))])
    assert len(accepted(evidence)) == 3


@pytest.mark.parametrize("envelope", [None, {}, {"demo_command_kind": 13, "command_number": 117, "server_tick_executed": 117},
    {"demo_command_kind": 7, "command_number": 118, "server_tick_executed": 117},
    {"demo_command_kind": 7, "command_number": 117, "server_tick_executed": 118},
    {"demo_command_kind": 7, "command_number": 117., "server_tick_executed": 117}])
def test_reconstructed_row_alone_does_not_prove_live_packet_origin(evidence, envelope):
    evidence["command_sources"][117]["source_envelope"] = envelope
    sample = acceptance._build_samples(evidence)[7]
    assert not sample["training_ready"]
    assert "original_live_command_payload_binding_unverified" in sample["reason_codes"]


@pytest.mark.parametrize("change,reason", [
    ("support", "recorded_source_command_support_unverified"), ("phase", "competitive_phase_unverified"),
    ("image", "history_image_proof_unverified"), ("segment", "history_clock_segment_changed"),
    ("bound", "history_information_bound_regressed"), ("command_source", "original_live_command_payload_binding_unverified"),
    ("duplicate", "missing_or_ambiguous_future_command"), ("gap", "missing_or_ambiguous_future_command"),
    ("reset", "client_generation_clock_reset"), ("pawn", "command_pawn_changed"),
    ("dead", "state_not_known_alive"), ("pause", "is_paused_active"),
    ("context", "context_coverage_missing"), ("round", "outside_live_round"),
    ("projection", "canonical_action_disagrees_with_protobuf")])
def test_negative_evidence_rejects_affected_history_without_shifting_targets(evidence, change, reason):
    if change == "support": evidence["source_support"]["status"] = "unknown"
    elif change == "phase": evidence["phase"]["phase_verified"] = False
    elif change == "image": evidence["frames"][4]["verified"] = False
    elif change == "segment": evidence["frames"][4]["clock_segment_id"] = "new-process"
    elif change == "bound": evidence["frames"][6]["observation_upper_execution_tick"] = 140
    elif change == "command_source": evidence["command_sources"][117]["status"] = "unknown"
    elif change == "duplicate": evidence["commands"].append(deepcopy(evidence["commands"][17]))
    elif change == "gap": evidence["commands"].pop(17)
    elif change == "reset":
        values = [c["client_tick"] for c in evidence["commands"]]; values[17] -= 5
        with_client_ticks(evidence["commands"], values)
    elif change == "pawn":
        row=evidence["commands"][17]; base=protobuf_fields(row["command_protobuf"])[1][0][1]
        row.update(pawn_entity_handle=99,command_protobuf=embedded(1,base.replace(integer(14,42),integer(14,99))))
    elif change == "dead": evidence["states"][20]["alive"] = False
    elif change == "pause":
        evidence["states"][20]["is_paused"] = True
        evidence["context_segments"][0]["is_paused"] = None
    elif change == "context": evidence["context_segments"] = []
    elif change == "round": evidence["round_info"]["end_tick"] = 65
    elif change == "projection": evidence["commands"][17]["view_yaw"] = 0.
    sample = acceptance._build_samples(evidence)[7]
    assert not sample["training_ready"] and reason in sample["reason_codes"]


def test_angular_sum_wraps_adjacent_differences_not_final_total(evidence):
    replacements = {116:command(116,yaw=0.),117:command(117,yaw=170.),118:command(118,yaw=-20.)}
    evidence["commands"] = [replacements.get(r["command_number"],r) for r in evidence["commands"]]
    assert accepted(evidence)[0]["label"]["aim_delta_deg"]["yaw"]["value"] == 340.


@pytest.fixture
def pipeline(evidence, tmp_path, monkeypatch):
    calls=[]
    def load(*args):
        calls.append(args)
        return deepcopy(evidence)
    monkeypatch.setattr(acceptance,"_load_proof",load)
    inputs=tuple(tmp_path/name for name in ("parsed","dataset","network.json","context.json"))
    out=tmp_path/"acceptance"
    return evidence, inputs, out, calls


def test_public_loader_recomputes_and_compares_exact_partition_bytes(pipeline):
    _, inputs, out, calls=pipeline
    report=acceptance.accept_competitive_controls(*inputs,out)
    assert report["accepted_count"] == 3 and report["rejected_count"] == 7
    assert acceptance.load_competitive_acceptance(out) == report
    assert len(calls) == 2
    assert all(Path(path).is_absolute() for path in report["source_files"])


def test_changed_image_bytes_are_rejected_even_before_partition_comparison(pipeline):
    evidence, inputs, out, _=pipeline
    acceptance.accept_competitive_controls(*inputs,out)
    Path(evidence["frames"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError,match="image source hash"):
        acceptance.load_competitive_acceptance(out)


def test_edited_and_rehashed_sample_cannot_grant_new_field_or_acceptance(pipeline):
    _, inputs, out, _=pipeline
    acceptance.accept_competitive_controls(*inputs,out)
    path=out/"accepted_samples.jsonl"
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["label"]["buttons"]["forward"]["held_end"]={"value":True,"valid":True,"reason_codes":[]}
    path.write_bytes(b"".join(acceptance._bytes(r) for r in rows))
    manifest=out/acceptance.MANIFEST
    report=json.loads(manifest.read_text()); report["files"][path.name]=acceptance.sha256_file(path)
    manifest.write_text(json.dumps(report))
    with pytest.raises(ValueError,match="independently recomputed"):
        acceptance.load_competitive_acceptance(out)


def test_partition_whitespace_changes_cannot_hide_behind_equivalent_json(pipeline):
    _, inputs, out, _=pipeline
    acceptance.accept_competitive_controls(*inputs,out)
    path=out/"accepted_samples.jsonl"
    path.write_text(path.read_text()+"\n")
    with pytest.raises(ValueError,match="partition differs"):
        acceptance.load_competitive_acceptance(out)


def test_unknown_recording_build_publishes_rejections_and_never_promotes_old_profiles(pipeline):
    evidence, inputs, out, _=pipeline
    evidence["source_support"].update(status="unknown",reason_codes=["recorded_patch_compatibility_unavailable"])
    report=acceptance.accept_competitive_controls(*inputs,out)
    assert report["accepted_count"] == 0 and report["rejected_count"] == 10
    assert report["reason_counts"]["recorded_patch_compatibility_unavailable"] == 10
    assert (out/"accepted_samples.jsonl").read_bytes() == b""


def test_outputs_are_immutable_and_sources_remain_unchanged(pipeline):
    evidence, inputs, out, _=pipeline
    before=deepcopy(evidence)
    acceptance.accept_competitive_controls(*inputs,out)
    assert evidence == before
    with pytest.raises(ValueError,match="fresh output"):
        acceptance.accept_competitive_controls(*inputs,out)
