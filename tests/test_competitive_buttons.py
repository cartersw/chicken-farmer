"""Original-build semantics fixtures replace only inspected binary evidence."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path

import pytest

from cs2_data import competitive_buttons as buttons
from test_calibration_buttons import row


def planes(code, mask=8):
    return tuple(mask if code & (1 << index) else 0 for index in range(3))


@pytest.mark.parametrize("code", range(8))
@pytest.mark.parametrize("mask", buttons.BUTTON_MASKS.values())
def test_native_enum_decomposes_each_semantic_bit_without_cross_talk(code, mask):
    values = planes(code, mask)
    decoded = buttons.decode_native_button_state(values, mask)
    assert decoded["state_code"] == code
    assert decoded["native_state_name"] == buttons.STATE_NAMES[code]
    assert decoded["held_at_command_end"] == bool(code & 1)
    assert decoded["held_at_command_start"] == (bool(code & 1) ^ bool(code & 2))
    assert decoded["net_changed"] == bool(code & 2)
    assert decoded["recorded_activity_present"] == bool(code & 6)
    assert decoded["unresolved_rapid_activity"] == bool(code & 4)
    assert buttons.decode_native_button_state(tuple(v | (1 << 63) for v in values), mask) == decoded


@pytest.mark.parametrize("bad", [None, [], [0, 0], [0, 0, 0, 0], [True, 0, 0], [-1, 0, 0], [2**64, 0, 0], [0.0, 0, 0]])
def test_invalid_plane_input_cannot_be_decoded(bad):
    with pytest.raises(ValueError, match="unsigned64"):
        buttons.decode_native_button_state(bad, 8)


@pytest.mark.parametrize("mask", [0, 3, True, 8.0, 128, 1 << 63])
def test_unsupported_masks_cannot_become_semantic_controls(mask):
    with pytest.raises(ValueError, match="semantic button"):
        buttons.decode_native_button_state([0, 0, 0], mask)


def test_native_update_table_preserves_end_and_parity_but_loses_long_history_counts():
    collisions = {}
    for start in (False, True):
        for length in range(9):
            for updates in product((False, True), repeat=length):
                code, last, count = int(start), start, 0
                for value in updates:
                    count += value != last
                    last = value
                    code = buttons.UPDATE_TABLE[code][int(value)]
                state = buttons.decode_native_button_state(planes(code), 8)
                assert state["held_at_command_end"] == last
                assert state["held_at_command_start"] == start
                assert state["net_changed"] == (start != last)
                assert state["unresolved_rapid_activity"] == (count >= 2)
                collisions.setdefault((start, code), set()).add(count)
    assert collisions[(False, 4)] == {2, 4, 6, 8}
    assert collisions[(True, 5)] == {2, 4, 6, 8}


@pytest.fixture
def bound(monkeypatch):
    # The source-envelope routine and all raw protobuf decoding remain real.
    monkeypatch.setattr(buttons, "_inspect_binary", lambda watch: {"test_fixture_only": True})
    def audit(commands, *, patch=14178, edit_source=None, edit_reconstruction=None):
        source = {"source_demo_sha256": "a"*64, "file_header": {"patch_version": patch,
            "protobuf_sha256": "b"*64}, "packets": []}
        for r in commands:
            source["packets"].append({"demo_tick": r["demo_tick"], "demo_command_kind": 7,
                "source_command_index": r["command_row_id"], "command_offset": r["command_row_id"]*10,
                "packet_data_sha256": "c"*64, "command_envelopes": [{"wire_index": 0,
                "envelope_index": 0, "protobuf_sha256": "d"*64,
                **{k: r[k] for k in ("server_tick_executed", "player_slot", "command_number", "client_tick")}}]})
        reconstructed = {"proof_sha256": "e"*64, "provenance": {"source_demo_sha256": "a"*64},
            "source_files": {}, "command_row_digests": {r["command_row_id"]: buttons.canonical_row_sha256(r) for r in commands}}
        if edit_source: edit_source(source)
        if edit_reconstruction: edit_reconstruction(reconstructed)
        return buttons._audit_bound_commands(source, commands, reconstructed,
            lambda path, expected=None: buttons.sha256_file(path))
    return audit


def test_present_empty_parent_is_zero_but_absent_parent_remains_unknown(bound):
    report = bound([row(100), row(101, (None, None, None))])
    assert report["row_proofs"][100]["status"] == "unknown"
    assert report["row_proofs"][100]["raw_planes_hex"] == [None]*3
    known = report["row_proofs"][101]
    assert known["status"] == "verified" and known["plane_scalar_presence"] == [False]*3
    assert known["values"]["forward"]["held_at_command_end"] is False
    assert known["supported_fields"]["forward"] == list(buttons.FIELDS)


def test_retained_rapid_code_does_not_require_complete_subtick_history(bound):
    r = row(100, planes(7), [(8, True, .25)])
    report = bound([r])
    value = report["row_proofs"][100]["values"]["forward"]
    assert value["unresolved_rapid_activity"] and value["recorded_subtick_indices"] == [0]
    assert value["net_changed"] and value["net_pressed"]
    assert report["status"] == "verified"
    assert all(report[key] is False for key in ("exact_button_event_count_verified", "exact_button_event_order_verified",
        "exact_input_timing_verified", "physical_key_mapping_verified", "recording_server_binary_identity_verified",
        "training_ready", "live_control_ready"))


def test_retained_subtick_is_positive_activity_evidence_without_inventing_rapid_state(bound):
    report = bound([row(100, planes(1), [(8, True, .25)])])
    value = report["row_proofs"][100]["values"]["forward"]
    assert value["recorded_activity_present"] and not value["unresolved_rapid_activity"]
    assert not value["net_changed"]


def test_constant_held_state_alone_is_not_new_activity(bound):
    value = bound([row(100, planes(1))])["row_proofs"][100]["values"]["forward"]
    assert value["held_at_command_end"] and not value["recorded_activity_present"]


@pytest.mark.parametrize("patch", [14180, "14178", True, None])
def test_current_calibration_or_malformed_patch_never_promotes_original_semantics(bound, patch):
    report = bound([row(100, planes(3))], patch=patch)
    assert report["status"] == "unknown" and not report["supported_button_masks"]
    assert not any(report["row_proofs"][100]["supported_fields"].values())


@pytest.mark.parametrize("change", ["plane", "steps", "parent", "bytes", "flags"])
def test_retained_raw_protobuf_is_authority_even_when_reconstruction_digest_matches(bound, change):
    r = row(100, planes(3), [(8, True, .25)])
    if change == "plane": r["buttonstate2"] = 0
    elif change == "steps": r["subtick_moves"] = []
    elif change == "parent": r["buttons_present"] = False
    elif change == "bytes": r["command_protobuf"] = b"invalid"
    else:
        from test_causal_acceptance import embedded, integer
        from cs2_data.causal_acceptance import protobuf_fields
        base = protobuf_fields(r["command_protobuf"])[1][0][1]
        r["command_protobuf"] = embedded(1, base+integer(21, 1))
    report = bound([r])
    assert report["status"] == "unknown" and not any(report["row_proofs"][100]["supported_fields"].values())


@pytest.mark.parametrize("change", ["missing", "digest"])
def test_reconstruction_must_bind_each_unchanged_row(bound, change):
    def mutate(proof):
        if change == "missing": proof["command_row_digests"].clear()
        else: proof["command_row_digests"][100] = "f"*64
    r = bound([row(100, planes(3))], edit_reconstruction=mutate)["row_proofs"][100]
    assert r["status"] == "unknown"
    assert "original_command_reconstruction_unavailable_or_different" in r["reason_codes"]


def test_reconstruction_from_another_demo_is_not_accepted(bound):
    with pytest.raises(ValueError, match="another original demo"):
        bound([row(100, planes(3))], edit_reconstruction=lambda p: p["provenance"].update(source_demo_sha256="f"*64))


@pytest.mark.parametrize("change", ["absent", "ambiguous", "checkpoint", "wrong_clock"])
def test_live_envelope_is_required_independently_of_canonical_rows(bound, change):
    def mutate(source):
        if change == "absent": source["packets"] = []
        elif change == "ambiguous": source["packets"] += deepcopy(source["packets"])
        elif change == "checkpoint": source["packets"][0]["demo_command_kind"] = 13
        else: source["packets"][0]["command_envelopes"][0]["server_tick_executed"] += 1
    report = bound([row(100, planes(3))], edit_source=mutate)
    assert report["status"] == "unknown"
    assert "live_source_envelope_absent_or_ambiguous" in report["row_proofs"][100]["reason_codes"]


def test_contiguous_parity_disagreement_masks_only_the_affected_control(bound):
    report = bound([row(100, planes(1)), row(101, planes(3))])
    proof = report["row_proofs"][101]
    assert not proof["supported_fields"]["forward"]
    assert proof["supported_fields"]["jump"] == list(buttons.FIELDS)
    assert report["summary"]["known_boundary_mismatches"] == 1
    assert "plane2_disagrees_with_known_held_boundaries" in proof["field_reason_codes"]["forward"]


@pytest.mark.parametrize("change", ["gap", "round", "player"])
def test_noncontiguous_or_other_identity_does_not_supply_a_known_predecessor(bound, change):
    a, b = row(100, planes(1)), row(101 if change != "gap" else 102, planes(3))
    if change == "round": b["round_id"] += 1
    if change == "player": b["steam_id"] += 1
    report = bound([a, b])
    assert report["summary"].get("known_boundary_comparisons", 0) == 0
    assert report["row_proofs"][b["command_row_id"]]["supported_fields"]["forward"]


@pytest.mark.parametrize("change", ["duplicate", "reordered", "invalid"])
def test_row_order_and_uniqueness_are_not_silently_repaired(bound, change):
    rows = [row(100, planes(1)), row(101, planes(1))]
    if change == "duplicate": rows[1] = deepcopy(rows[0])
    elif change == "reordered": rows.reverse()
    else: rows[0]["command_row_id"] = True
    with pytest.raises(ValueError, match="row IDs"):
        bound(rows)


def test_fixed_original_binary_profile_rejects_other_bytes(tmp_path, monkeypatch):
    path = tmp_path/"server.dll"
    path.write_bytes(b"not the original server")
    monkeypatch.setattr(buttons, "SERVER", path)
    with pytest.raises(ValueError, match="Recovered14178 button binary changed"):
        buttons._inspect_binary(lambda path, expected: buttons.sha256_file(path))


def test_publication_is_immutable_and_completion_json_is_last(tmp_path, monkeypatch, bound):
    report = bound([row(100, planes(3))]); report["source_files"] = {}
    monkeypatch.setattr(buttons, "recompute_competitive_button_evidence", lambda *a, **k: report)
    original_publish = buttons.publish
    def publish(staged, destinations):
        assert destinations[-1].name == "report.json"
        original_publish(staged, destinations)
    monkeypatch.setattr(buttons, "publish", publish)
    out = tmp_path/"audit"
    buttons.write_competitive_button_evidence(tmp_path/"source.dem", tmp_path, through_demo_tick=100, out=out)
    before = (out/"report.json").read_bytes()
    assert json.loads(before)["row_proofs"]["100"]["status"] == "verified"
    with pytest.raises(ValueError, match="overwrite"):
        buttons.write_competitive_button_evidence(tmp_path/"source.dem", tmp_path, through_demo_tick=100, out=out)
    assert (out/"report.json").read_bytes() == before


def test_publication_refuses_changed_source_and_removes_partial_files(tmp_path, monkeypatch, bound):
    source = tmp_path/"source.dem"; source.write_bytes(b"old")
    report = bound([row(100, planes(3))]); report["source_files"] = {str(source): buttons.sha256_file(source)}
    def recompute(*a, **k):
        source.write_bytes(b"changed")
        return report
    monkeypatch.setattr(buttons, "recompute_competitive_button_evidence", recompute)
    out = tmp_path/"audit"
    with pytest.raises(ValueError, match="changed before publication"):
        buttons.write_competitive_button_evidence(source, tmp_path, through_demo_tick=100, out=out)
    assert list(out.iterdir()) == []
