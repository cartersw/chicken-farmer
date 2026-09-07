"""Acceptance integration fixtures stub native proof, never certify real capture."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import causal_acceptance as causal
from cs2_data.validation import PAUSE_FLAGS


def vi(value):
    if value < 0:
        value += 2**64
    result = bytearray()
    while value > 127:
        result.append(value & 127 | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def integer(number, value):
    return vi(number << 3)+vi(value)


def embedded(number, payload):
    return vi(number << 3 | 2)+vi(len(payload))+payload


def floating(number, value):
    return vi(number << 3 | 5)+struct.pack("<f", value)


def command(end=117, flags=0, *, omitted=False):
    angles = b"" if omitted else floating(1, 1.0)+floating(2, float(end))+floating(3, 0.0)
    buttons = b"" if omitted else integer(1, 1)+integer(2, 0)+integer(3, 2)
    raw = embedded(3, buttons)+embedded(4, angles)
    if not omitted:
        raw += floating(5, 1.0)+floating(6, 0.0)+floating(7, 0.0)
        raw += integer(8, 0)+integer(9, 0)+integer(11, 3)+integer(12, -1)
    if flags:
        raw += integer(21, flags)
    return {"demo_id": "a"*64, "round_id": 3, "steam_id": 76561198000000001, "player_slot": 4,
        "command_row_id": end, "command_number": end, "client_tick": end-3,
        "server_tick_executed": end, "demo_tick": end-50, "pawn_entity_handle": 42,
        "alive": True, "is_warmup": False, "is_freeze_time": False,
        "base_present": True, "buttons_present": True, "viewangles_present": True,
        "forwardmove": None if omitted else 1.0, "leftmove": None if omitted else 0.0, "upmove": None if omitted else 0.0,
        "view_pitch": None if omitted else 1.0, "view_yaw": None if omitted else float(end), "view_roll": None if omitted else 0.0,
        "mousedx_raw": None if omitted else 3, "mousedy_raw": None if omitted else -1,
        "impulse": None if omitted else 0, "weaponselect": None if omitted else 0,
        "buttonstate1": None if omitted else 1, "buttonstate2": None if omitted else 0, "buttonstate3": None if omitted else 2,
        "subtick_moves": [], "input_history": [], "command_protobuf": embedded(1, raw)}


def test_action_columns_and_flags_are_decoded_from_retained_canonical_protobuf():
    row = command()
    assert causal._action_protobuf(row) == (set(), 0)
    row["view_yaw"] -= 1
    assert "canonical_action_disagrees_with_protobuf" in causal._action_protobuf(row)[0]
    assert causal._action_protobuf(command(flags=128)) == ({"unsupported_cmd_flags"}, 128)


def test_known_present_empty_submessages_supply_defined_scalar_defaults():
    row = command(omitted=True)
    assert causal._action_protobuf(row) == (set(), 0)
    reasons, derived, flags = causal._command_quality(row, command(116, omitted=True), row)
    assert not reasons and flags == 0
    assert derived["delta_yaw_deg"] == 0
    assert derived["mousedx_effective"] == 0
    row["buttons_present"] = False
    assert "canonical_buttons_present_disagrees_with_protobuf" in causal._action_protobuf(row)[0]


@pytest.mark.parametrize("payload", [b"", b"\x80", embedded(1, b"\xa8\x01\x80"), embedded(1, embedded(4, b"\x0d\x01"))])
def test_missing_or_corrupt_action_protobuf_rejects_eligibility(payload):
    row = command()
    row["command_protobuf"] = payload
    assert causal._action_protobuf(row)[0]


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    parsed, dataset, out = (tmp_path / name for name in ("parsed", "dataset", "accepted"))
    parsed.mkdir()
    timing = dataset / "timing"
    timing.mkdir(parents=True)
    source_demo = tmp_path / "source.dem"
    source_demo.write_bytes(b"test-only raw source; scanner dependency is independently tested")
    demo_id = hashlib.sha256(source_demo.read_bytes()).hexdigest()
    identity = {"demo_id": demo_id, "round_id": 3, "steam_id": 76561198000000001, "player_slot": 4}
    rows = [{**command(end), **identity} for end in range(100, 126)]
    states = [{**identity, "demo_tick": tick, "alive": True, "is_paused": False,
               "is_warmup": False, "is_freeze_time": False} for tick in range(40, 91)]
    rounds = [{"round_id": 3, "start_tick": 30, "freeze_end_tick": 40, "end_tick": 90}]
    for name, values in (("usercmd", rows), ("player_state", states), ("rounds", rounds)):
        pq.write_table(pa.Table.from_pylist(values), parsed / (name+".parquet"))
    canonical = {"demo_id": demo_id, "sha256": demo_id, "source_path": str(source_demo),
                 "parse_status": "complete", "partial": False, "parser_schema_version": "2",
                 "parser_version": "v6.0.0-alpha.0", "extractor_version": "0.1.2", "tick_rate": 64,
                 "files": {name+".parquet": causal.sha256_file(parsed / (name+".parquet")) for name in ("usercmd", "player_state", "rounds")}}
    (parsed / "manifest.json").write_text(json.dumps(canonical))
    server = tmp_path / "game/csgo/bin/win64/server.dll"
    server.parent.mkdir(parents=True)
    server.write_bytes(b"test-only inspected server fixture")
    monkeypatch.setattr(causal, "SERVER_SHA256", causal.sha256_file(server))
    native_ledger = tmp_path / "ledger.jsonl"
    native_ledger.write_text(json.dumps({"event": "header"})+"\n")
    frame_rows = [{**identity, "frame_index": index, "source_demo_tick_start": 52+2*index,
                   "source_demo_tick_end": 54+2*index} for index in range(10)]
    (timing / "frames.jsonl").write_text("".join(json.dumps(row)+"\n" for row in frame_rows))
    images = []
    for index in range(10):
        image = tmp_path / f"image-{index}.tga"
        image.write_bytes(f"test image {index}".encode())
        images.append({"capture_index": index, "path": str(image), "sha256": causal.sha256_file(image)})
    inventory = timing / "frame_inventory.json"
    inventory.write_text(json.dumps({"frames": images}))
    clip = {**identity, "clip_id": "clip", "game_dir": str(tmp_path / "game"),
            "capture_evidence": {"ledger_path": str(native_ledger), "frame_inventory_path": str(inventory)}}
    (timing / "clip.json").write_text(json.dumps(clip))
    state_context = tmp_path / "context.json"
    context = {"schema_version": 2, "producer": "cs2-context-v2", "parse_status": "complete", "partial": False,
        "demo_id": demo_id, "source_demo_sha256": demo_id, "tick_rate": 64, "parser": "demoinfocs-golang/v6",
        "parser_version": "v6.0.0-alpha.0", "property_prefix": "m_pGameRules.", "timing_clock": "demo_tick",
        "required_pause_flags": list(PAUSE_FLAGS), "warning_policy": "rule-and-shot-evidence-v1",
        "evidence_loss_warnings": 0, "warnings": {}, "segments": [{"round_id": 3, "start_demo_tick": 40,
            "end_demo_tick": 91, "ambiguous_tick": False, "is_paused": False, "is_freeze_time": False,
            "is_warmup": False, "pause_flags": {name: False for name in PAUSE_FLAGS}, "match_started": True, "game_phase": 2}]}
    state_context.write_text(json.dumps(context))
    network_clock = tmp_path / "network.json"
    network_clock.write_text("{}")
    sync = {"pixel_correspondence": {"verified": True}, "native_message_clock_audit": {"frames": [
        {"event": "movie_frame", "frame_index": index, "status": "matched_message_clocks"} for index in range(10)]},
        "pov_evidence": [{"frame_index": index, "status": "passed"} for index in range(10)]}
    source = {"schema_version": 2, "source_demo_sha256": demo_id, "file_header": {"patch_version": 14178, "protobuf_sha256": "b"*64},
              "packets": [{"demo_command_kind": 7, "demo_tick": row["demo_tick"], "source_command_index": index,
                  "command_offset": index*100, "packet_data_sha256": "c"*64,
                  "command_envelopes": [{"wire_index": 2, "envelope_index": 0, "protobuf_sha256": "d"*64,
                      **{key: row[key] for key in ("server_tick_executed", "player_slot", "command_number", "client_tick")}}]}
                  for index, row in enumerate(rows)]}
    bounds = {"schema_version": 1, "status": "verified", "source_demo_sha256": demo_id, "frames": [
        {"capture_index": index, "status": "verified", "verified": True, "upper_server_tick": 100+2*index,
         "upper_source_demo_tick": 50+2*index, "reasons": []} for index in range(10)],
        "endpoint": {"capture_index": 10, "status": "verified", "verified": True,
                     "upper_server_tick": 120, "upper_source_demo_tick": 70, "reasons": []}}
    calls = []

    def scan(path, *, expected_sha256, through_demo_tick):
        calls.append((path, expected_sha256, through_demo_tick))
        assert causal.sha256_file(path) == expected_sha256
        return deepcopy(source)

    monkeypatch.setattr(causal, "scan_demo_packets", scan)
    monkeypatch.setattr(causal, "recompute_synchronization", lambda *args: deepcopy(sync))
    monkeypatch.setattr(causal, "phase_evidence", lambda *args: {3: {"phase": "competitive", "phase_verified": True}})
    from cs2_data import packet_bounds
    monkeypatch.setattr(packet_bounds, "audit_packet_bounds", lambda *args: deepcopy(bounds))
    return {**locals(), "arguments": (parsed, dataset, network_clock, state_context, out)}


def rewrite_commands(fixture, mutate):
    rows = pq.read_table(fixture["parsed"] / "usercmd.parquet").to_pylist()
    mutate(rows)
    path = fixture["parsed"] / "usercmd.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    fixture["canonical"]["files"]["usercmd.parquet"] = causal.sha256_file(path)
    (fixture["parsed"] / "manifest.json").write_text(json.dumps(fixture["canonical"]))


def samples(fixture, accepted=True):
    path = fixture["out"] / ("accepted_samples.jsonl" if accepted else "rejected_samples.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_public_acceptance_selects_strict_future_delta_with_raw_provenance_and_eight_images(fixture):
    report = causal.accept_causal_samples(*fixture["arguments"])
    assert report["accepted_count"] == 2
    assert report["rejected_count"] == 8
    sample = samples(fixture)[0]
    assert sample["observation_frame_index"] == 7
    assert sample["target_command_row_ids"] == [117]
    assert sample["normalization_predecessor_command_row_ids"] == [116]
    assert sample["normalization_predecessor_support"][0]["support_start_execution_tick"] == 115
    assert sample["observation_upper_execution_tick"] == 114
    assert len(sample["images"]) == 8
    assert sample["targets"][0]["delta_yaw_deg"] == 1
    assert sample["targets"][0]["raw_button_planes"]["buttonstate3"] == "0x0000000000000002"
    assert sample["targets"][0]["mousedy"] == -1
    assert sample["previous_action_features_included"] is False
    assert sample["previous_action_command_row_ids"] == []
    assert len(sample["command_provenance"]) == 2
    assert all(row["source_envelope"]["demo_command_kind"] == 7 for row in sample["command_provenance"])
    assert base64.b64decode(sample["command_provenance"][1]["command_protobuf_base64"]) == fixture["rows"][17]["command_protobuf"]
    assert fixture["calls"] == [(fixture["source_demo"], fixture["demo_id"], 80)]
    assert causal.load_causal_acceptance(fixture["out"]) == report
    assert len(fixture["calls"]) == 2  # Cached source-proof JSON is never accepted as fresh scan input.


def test_unknown_numeric_packet_bounds_never_become_verified_by_claiming_training_ready(fixture):
    fixture["bounds"].update(status="unknown", training_ready=True)
    for bound in fixture["bounds"]["frames"]:
        bound.update(status="unknown", verified=False, training_ready=True)
    report = causal.accept_causal_samples(*fixture["arguments"])
    assert report["accepted_count"] == 0
    assert report["reason_counts"]["packet_information_bound_unknown"] == 10
    assert not any(row["training_ready"] for row in samples(fixture, False))


@pytest.mark.parametrize("failure", ["patch", "server", "pixel", "pov", "native", "pause", "phase"])
def test_missing_source_capture_state_or_phase_proof_rejects_samples(fixture, monkeypatch, failure):
    if failure == "patch": fixture["source"]["file_header"]["patch_version"] = 14177
    elif failure == "server": fixture["server"].write_bytes(b"different binary")
    elif failure == "pixel": fixture["sync"]["pixel_correspondence"]["verified"] = False
    elif failure == "pov": fixture["sync"]["pov_evidence"][7]["status"] = "failed"
    elif failure == "native": fixture["sync"]["native_message_clock_audit"]["frames"][7]["status"] = "unavailable"
    elif failure == "phase": monkeypatch.setattr(causal, "phase_evidence", lambda *args: {3: {"phase": "unknown", "phase_verified": False}})
    else:
        fixture["context"]["segments"][0].update(is_paused=True)
        fixture["context"]["segments"][0]["pause_flags"][PAUSE_FLAGS[0]] = True
        fixture["state_context"].write_text(json.dumps(fixture["context"]))
    assert causal.accept_causal_samples(*fixture["arguments"])["accepted_count"] == 0


@pytest.mark.parametrize("failure", ["checkpoint", "wrong_tick", "wrong_client", "duplicate"])
def test_selected_source_envelope_must_be_unique_and_from_live_packet7(fixture, failure):
    packet = fixture["source"]["packets"][17]
    if failure == "checkpoint": packet["demo_command_kind"] = 13
    elif failure == "wrong_tick": packet["demo_tick"] += 1
    elif failure == "wrong_client": packet["command_envelopes"][0]["client_tick"] -= 1
    else: fixture["source"]["packets"].append(deepcopy(packet))
    report = causal.accept_causal_samples(*fixture["arguments"])
    assert report["accepted_count"] == 1
    rejected = next(row for row in samples(fixture, False) if row["observation_frame_index"] == 7)
    assert "live_source_command_envelope_unavailable_or_ambiguous" in rejected["reason_codes"]


@pytest.mark.parametrize("which", [16, 17])
def test_unsupported_flags_in_either_delta_contributor_reject_without_later_replacement(fixture, which):
    def mutation(rows):
        rows[which]["command_protobuf"] = command(100+which, flags=128)["command_protobuf"]
    rewrite_commands(fixture, mutation)
    report = causal.accept_causal_samples(*fixture["arguments"])
    assert report["accepted_count"] == 1
    row = next(row for row in samples(fixture, False) if row["observation_frame_index"] == 7)
    assert row["target_command_row_ids"] == [117]
    assert any("unsupported_cmd_flags" in reason for reason in row["reason_codes"])


def test_modified_canonical_projection_cannot_be_hidden_by_rehashing_parquet_manifest(fixture):
    rewrite_commands(fixture, lambda rows: rows[17].update(view_yaw=116.0))
    report = causal.accept_causal_samples(*fixture["arguments"])
    assert report["accepted_count"] == 1
    assert report["reason_counts"]["canonical_action_disagrees_with_protobuf"] >= 1


@pytest.mark.parametrize("defect", ["target", "predecessor", "feature", "ready", "source_proof"])
def test_loader_rejects_rehashed_shifted_samples_and_reused_proof_flags(fixture, defect):
    causal.accept_causal_samples(*fixture["arguments"])
    good = fixture["out"] / "accepted_samples.jsonl"
    values = samples(fixture)
    if defect == "target": values[0]["targets"][0]["delta_yaw_deg"] = 2.0
    elif defect == "predecessor": values[0]["normalization_predecessor_command_row_ids"] = [115]
    elif defect == "feature": values[0]["previous_action_command_row_ids"] = [116]
    elif defect == "ready": values[0]["training_ready"] = False
    else:
        fixture["bounds"]["frames"][7].update(status="unknown", verified=False)
    good.write_bytes(b"".join(causal._json_bytes(row) for row in values))
    manifest = fixture["out"] / "causal_acceptance.json"
    report = json.loads(manifest.read_text())
    report["files"]["accepted_samples.jsonl"] = causal.sha256_file(good)
    manifest.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="recomputed evidence"):
        causal.load_causal_acceptance(fixture["out"])


def test_existing_output_is_never_overwritten_and_incomplete_publication_has_no_completion_manifest(fixture, monkeypatch):
    fixture["out"].mkdir()
    sentinel = fixture["out"] / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ValueError, match="fresh output"):
        causal.accept_causal_samples(*fixture["arguments"])
    assert sentinel.read_bytes() == b"keep"
    sentinel.unlink()

    def fail_publish(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(causal, "publish", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        causal.accept_causal_samples(*fixture["arguments"])
    assert list(fixture["out"].iterdir()) == []
