"""Explicit current-build routing cannot silently reuse a legacy/calibration proof."""
from copy import deepcopy
import hashlib
import json

import pytest

from cs2_data.native_replay_profile import (CURRENT_PROFILE, LEGACY_PROFILE, PROFILES,
                                           get_native_replay_profile, header_matches_profile)
from cs2_data.packet_bounds import audit_packet_bounds
from cs2_data.packet_evidence import derive_native_seek_filter, scan_demo_packets
from cs2_data import synchronization as sync
from test_packet_bounds import fixture as bounds_fixture
from test_packet_evidence import packet, embedded, command
from test_synchronization import transcript, evidence


def test_current_packet_profile_scans_later_rounds_without_changing_legacy_scope(tmp_path):
    payload = embedded(3, packet([(4, b"\x08\x64")]))
    demo = b"PBDEMS2\x00" + b"\x00"*8 + command(7, 22300, payload) + command(7, 22695, payload)
    path = tmp_path/"later-round.dem"
    path.write_bytes(demo)
    digest = hashlib.sha256(demo).hexdigest()
    result = scan_demo_packets(path, expected_sha256=digest, through_demo_tick=22694, native_profile=CURRENT_PROFILE)
    assert result["coverage_next_header_tick"] == 22695
    assert result["packets"][0]["demo_tick"] == 22300
    with pytest.raises(ValueError, match="20000"):
        scan_demo_packets(path, expected_sha256=digest, through_demo_tick=22694)


@pytest.mark.parametrize("tick", [True, -1, 2**31, 22000.0])
def test_current_packet_prefix_still_requires_an_int32_nonnegative_tick(tmp_path, tick):
    with pytest.raises(ValueError, match="prefix"):
        scan_demo_packets(tmp_path/"missing.dem", expected_sha256="a"*64, through_demo_tick=tick,
                          native_profile=CURRENT_PROFILE)


def current_header(header):
    profile = get_native_replay_profile(CURRENT_PROFILE)
    header.update(native_profile=CURRENT_PROFILE, engine_sha256=profile["engine_sha256"])
    header["native_observation"]["client_sha256"] = profile["client_sha256"]
    if "packet_trace" in header:
        header["packet_trace"]["engine_sha256"] = profile["engine_sha256"]
    if "native_clock" in header:
        header["native_clock"].update(engine_sha256=profile["engine_sha256"], status="prepared",
            client_vtable_rva="0x5335d8", client_server_tick_offset=892,
            net_tick_field_offset=80, packet_entities_tick_field_offset=192)
    return header


def current_bounds_fixture():
    rows, source = bounds_fixture()
    profile = get_native_replay_profile(CURRENT_PROFILE)
    current_header(rows[0])
    source["native_profile"] = CURRENT_PROFILE
    source["native_seek_filter_profile"].update(policy_id=profile["seek_filter_policy"],
        engine_sha256=profile["engine_sha256"], client_sha256=profile["client_sha256"])
    for item in source["packets"]:
        item["native_seek_filter_policy"] = profile["seek_filter_policy"]
    return rows, source


def test_registry_and_nested_binary_metadata_are_immutable():
    profile = get_native_replay_profile(CURRENT_PROFILE)
    assert len(profile["binary_profile"]) == 8
    with pytest.raises(TypeError):
        PROFILES[CURRENT_PROFILE] = {}
    with pytest.raises(TypeError):
        profile["engine_sha256"] = "a" * 64
    with pytest.raises(TypeError):
        profile["binary_profile"]["bin/win64/engine2.dll"] = "a" * 64


@pytest.mark.parametrize("selection", [None, True, {}, "cs2-14180-calibration-v1", "current",
                                       get_native_replay_profile(CURRENT_PROFILE)])
def test_caller_cannot_supply_an_override_profile(selection):
    for action in (lambda: get_native_replay_profile(selection),
                   lambda: derive_native_seek_filter(b"", native_profile=selection),
                   lambda: audit_packet_bounds([], {}, native_profile=selection),
                   lambda: sync.audit_native_messages([], evidence(100), native_profile=selection)):
        with pytest.raises(ValueError, match="fixed native replay profile"):
            action()


def test_source_scanner_profiles_are_explicit_and_keep_identical_proven_wire_transform(tmp_path):
    data = packet([(4, b"\x08\x64"), (8, b"drop"), (101, b"drop"), (55, b""), (76, b"")])
    path = tmp_path / "source.dem"
    path.write_bytes(b"PBDEMS2\0" + b"\0" * 8 + command(7, 10, embedded(3, data)) + command(7, 11))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    old = scan_demo_packets(path, expected_sha256=digest, through_demo_tick=10)
    new = scan_demo_packets(path, expected_sha256=digest, through_demo_tick=10, native_profile=CURRENT_PROFILE)
    assert "native_profile" not in old  # Historical default serialization stays stable.
    assert new["native_profile"] == CURRENT_PROFILE
    assert new["native_seek_filter_profile"]["policy_id"] == "cs2-14180-seek-message-filter-v1"
    assert old["packets"][0]["native_seek_filtered_data_sha256"] == new["packets"][0]["native_seek_filtered_data_sha256"]
    assert old["packets"][0]["packet_data_sha256"] == new["packets"][0]["packet_data_sha256"]
    assert derive_native_seek_filter(data, native_profile=CURRENT_PROFILE) == packet([(4, b"\x08\x64"), (55, b""), (76, b"")])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_current_bounds_require_explicit_route_and_preserve_ceiling_not_training_approval():
    rows, source = current_bounds_fixture()
    result = audit_packet_bounds(rows, source, native_profile=CURRENT_PROFILE)
    assert result["status"] == "verified"
    assert result["profile"] == "engine_1fcf2920_ordinary_returned_packet_prefix_v1"
    assert result["frames"][0]["upper_server_tick"] == 110
    assert result["endpoint"]["upper_server_tick"] == 111
    assert "training_ready" not in result
    assert audit_packet_bounds(rows, source)["status"] == "unknown"
    assert audit_packet_bounds(*bounds_fixture(), native_profile=CURRENT_PROFILE)["status"] == "unknown"


@pytest.mark.parametrize("change", [
    lambda h: h.pop("native_profile"),
    lambda h: h.update(native_profile="cs2-14180-calibration-v1"),
    lambda h: h.update(native_profile=LEGACY_PROFILE),
    lambda h: h.update(engine_sha256="a" * 64),
    lambda h: h["native_observation"].update(client_sha256="b" * 64),
    lambda h: h["packet_trace"].update(engine_sha256="a" * 64),
    lambda h: h["packet_trace"].update(read_packet_slot=23),
])
def test_current_bounds_reject_changed_native_header(change):
    rows, source = current_bounds_fixture()
    change(rows[0])
    report = audit_packet_bounds(rows, source, native_profile=CURRENT_PROFILE)
    assert report["status"] == "unknown"
    assert report["frames"][0]["verified"] is False


@pytest.mark.parametrize("change", [
    lambda s: s.pop("native_profile"),
    lambda s: s.update(native_profile=LEGACY_PROFILE),
    lambda s: s["native_seek_filter_profile"].update(policy_id="cs2-14178-seek-message-filter-v1"),
    lambda s: s["native_seek_filter_profile"].update(engine_sha256="a" * 64),
    lambda s: s["native_seek_filter_profile"].update(client_sha256="a" * 64),
    lambda s: s["packets"][0].update(native_seek_filter_policy="cs2-14178-seek-message-filter-v1"),
])
def test_current_bounds_reject_another_source_policy_even_for_exact_returns(change):
    rows, source = current_bounds_fixture()
    change(source)
    assert audit_packet_bounds(rows, source, native_profile=CURRENT_PROFILE)["status"] == "unknown"


def test_current_message_clock_recounts_only_on_explicit_current_route():
    rows = transcript()
    current_header(rows[0])
    result = sync.audit_native_messages(rows, evidence(100), native_profile=CURRENT_PROFILE)
    assert result["status"] == "matched_message_clocks"
    assert result["training_ready"] is False
    assert result["frames"][0]["causality_bound_verified"] is False
    assert sync.audit_native_messages(rows, evidence(100))["status"] == "unavailable"
    assert sync.audit_native_messages(transcript(), evidence(100), native_profile=CURRENT_PROFILE)["status"] == "unavailable"


@pytest.mark.parametrize("change", [
    lambda h: h.pop("native_profile"),
    lambda h: h.update(native_profile="cs2-14180-calibration-v1"),
    lambda h: h.update(engine_sha256="a" * 64),
    lambda h: h["native_observation"].update(client_sha256="b" * 64),
    lambda h: h["native_clock"].update(engine_sha256="a" * 64),
    lambda h: h["native_clock"].update(net_tick_field_offset=80.0),
    lambda h: h["native_clock"].update(packet_entities_tick_field_offset=80),
])
def test_current_message_contract_refuses_edited_profile_and_layout(change):
    rows = transcript()
    current_header(rows[0])
    change(rows[0])
    assert sync.audit_native_messages(rows, evidence(100), native_profile=CURRENT_PROFILE)["status"] == "unavailable"


def test_legacy_header_remains_accepted_with_absent_or_explicit_legacy_marker():
    header = transcript()[0]
    assert header_matches_profile(header)
    header["native_profile"] = LEGACY_PROFILE
    assert header_matches_profile(header)
    header["native_profile"] = CURRENT_PROFILE
    assert not header_matches_profile(header)


def test_sync_loader_preserves_explicit_profile_and_rejects_relabeling(tmp_path, monkeypatch):
    calls = []
    def recompute(parsed, dataset, network_clock, *, native_profile=LEGACY_PROFILE):
        get_native_replay_profile(native_profile)
        calls.append(native_profile)
        return {"schema_version": 1, "native_profile": CURRENT_PROFILE,
                "inputs": {"parsed": str(parsed), "dataset": str(dataset), "network_clock": str(network_clock)},
                "training_ready": False}
    monkeypatch.setattr(sync, "recompute_synchronization", recompute)
    report = recompute(tmp_path / "parsed", tmp_path / "dataset", tmp_path / "clocks")
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(report))
    assert sync.load_synchronization(path) == report
    assert calls[-1] == CURRENT_PROFILE
    report["native_profile"] = "cs2-14180-calibration-v1"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="fixed native replay profile"):
        sync.load_synchronization(path)
