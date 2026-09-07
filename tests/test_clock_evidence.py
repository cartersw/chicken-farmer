"""Independent wire fixtures and corruption cases for retained clock evidence."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest

from cs2_data import clock_evidence as clock


def varint(value):
    if value < 0:
        value += 2**64
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def field(number, value):
    return varint(number << 3) + varint(value)


def raw_row(kind, raw, **advertised):
    return {"clock_event_index": 1, "demo_tick": 10, "demo_frame": 13,
            "message_type": kind, "protobuf_reencoded": base64.b64encode(raw).decode(),
            "protobuf_sha256": hashlib.sha256(raw).hexdigest(), **advertised}


def command_row(**changes):
    row = raw_row("CMsgServerUserCmd", field(2, 30)+field(3, 4)+field(4, 100)+field(5, 97),
                  command_number=30, player_slot=4, server_tick_executed=100, client_tick=97)
    row.update(changes)
    return row


@pytest.fixture
def source_report(tmp_path):
    source = tmp_path / "source.dem"
    source.write_bytes(b"immutable test source; no claim of actual demo decoding")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = [raw_row("CNETMsg_Tick", b"\x08\x64", network_tick=100),
            command_row(clock_event_index=2),
            raw_row("CSVCMsg_PacketEntities", b"\x60\x64", snapshot_server_tick=100,
                    clock_event_index=3)]
    report = {"schema_version": 1, "producer": "cs2-clocks-v1", "status": "complete", "demo_id": digest,
              "source_demo_path": str(source.resolve()), "source_demo_sha256": digest,
              "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0",
              "scope": "complete_requested_windows_only", "windows": [{"start_demo_tick": 10, "end_demo_tick": 12}],
              "parsed_through_demo_tick": 12, "tick_rate": 64, "warnings": {},
              "warning_policy": "network-clock-envelope-v1", "evidence_loss_warnings": 0,
              "protobuf_encoding": "deterministic_reserialization_of_decoded_messages_not_original_wire_offsets",
              "demo_tick_source": "demo_command_header_via_parser_ingame_tick",
              "event_order": "parser_dispatch_order_for_tick_and_command_envelopes",
              "network_tick_records": 1, "snapshot_records": 1, "command_envelope_records": 1,
              "records": rows, "training_ready": False, "execution_render_epoch_verified": False}
    return tmp_path / "clocks.json", report, source


def publish(fixture):
    path, report, _ = fixture
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def load(fixture, **kwargs):
    return clock.load_network_clock(publish(fixture), fixture[1]["demo_id"], **kwargs)


def test_actual_wire_field_numbers_keep_three_clocks_distinct():
    net = raw_row("CNETMsg_Tick", b"\x08\x64\x20\x01", network_tick=100)
    snapshot = raw_row("CSVCMsg_PacketEntities", b"\x60\x63", snapshot_server_tick=99)
    command = command_row()
    assert clock.decode_clock_record(net)["network_tick"] == 100
    assert clock.decode_clock_record(snapshot)["snapshot_server_tick"] == 99
    decoded = clock.decode_clock_record(command)
    assert (decoded["server_tick_executed"], decoded["client_tick"]) == (100, 97)
    assert "network_tick" not in decoded


@pytest.mark.parametrize("value", [-2**31, -1, 0, 2**31-1])
def test_signed_int32_uses_sign_extended_varints(value):
    row = raw_row("CMsgServerUserCmd", field(3, value), player_slot=value)
    result = clock.decode_clock_record(row)
    assert result["player_slot"] == value
    assert result["server_tick_executed"] is None


@pytest.mark.parametrize("value", [2**31, 2**32-1, 2**32+100, 2**63, 2**64-2**31-1])
def test_oversized_signed_values_cannot_be_truncated_into_a_plausible_clock(value):
    row = raw_row("CMsgServerUserCmd", field(4, value), server_tick_executed=value & 0xffffffff)
    with pytest.raises(ValueError, match="int32"):
        clock.decode_clock_record(row)


def test_uint32_limits_and_zero_differ_from_absent_field():
    row = raw_row("CNETMsg_Tick", field(1, 2**32-1), network_tick=2**32-1)
    assert clock.decode_clock_record(row)["network_tick"] == 2**32-1
    with pytest.raises(ValueError, match="uint32"):
        clock.decode_clock_record(raw_row("CNETMsg_Tick", field(1, 2**32), network_tick=0))
    assert clock.decode_clock_record(raw_row("CNETMsg_Tick", b"\x08\x00", network_tick=0))["network_tick"] == 0
    assert clock.decode_clock_record(raw_row("CNETMsg_Tick", b""))["network_tick"] is None
    nil = raw_row("CNETMsg_Tick", b"")
    nil["protobuf_reencoded"] = None  # nil []byte in the Go producer
    assert clock.decode_clock_record(nil)["network_tick"] is None
    with pytest.raises(ValueError, match="disagrees"):
        clock.decode_clock_record(raw_row("CNETMsg_Tick", b"", network_tick=0))


@pytest.mark.parametrize("raw", [b"\x80", b"\x08\x80", b"\x08"+b"\xff"*10,
                                  b"\x00", b"\x0b", b"\x0e", b"\x0f", b"\x0a\x04ab",
                                  b"\x09abc", b"\x0dabc", varint(2**29 << 3)])
def test_truncated_unsupported_and_overflowed_wire_records_are_rejected(raw):
    with pytest.raises(ValueError):
        clock.protobuf_fields(raw)


def test_unknown_bytes_fixed_fields_and_repeated_nonclock_fields_are_retained():
    raw = b"\x08\x64\x12\x02ab\x12\x01c\x19abcdefgh\x25abcd"
    fields = clock.protobuf_fields(raw)
    assert fields[2] == [(2, b"ab"), (2, b"c")]
    assert fields[3] == [(1, b"abcdefgh")]
    assert fields[4] == [(5, b"abcd")]
    assert clock.scalar(fields, 1) == 100


@pytest.mark.parametrize("raw", [b"\x08\x64\x08\x64", b"\x0a\x01d", b"\x0d\x64\x00\x00\x00"])
def test_duplicate_or_wrong_wire_clock_is_not_an_unambiguous_measurement(raw):
    with pytest.raises(ValueError, match="unambiguous"):
        clock.decode_clock_record(raw_row("CNETMsg_Tick", raw, network_tick=100))


@pytest.mark.parametrize("field_name,value", [("server_tick_executed", 101), ("client_tick", 100),
                                               ("player_slot", True), ("command_number", 30.0)])
def test_advertised_or_shifted_command_clock_must_match_protobuf(field_name, value):
    with pytest.raises(ValueError, match="disagrees"):
        clock.decode_clock_record(command_row(**{field_name: value}))


@pytest.mark.parametrize("change", [
    {"protobuf_sha256": "0"*64}, {"protobuf_sha256": True}, {"protobuf_reencoded": "!"},
    {"protobuf_reencoded": []}, {"protobuf_reencoded": "CGR="},
    {"message_type": []}, {"message_type": "Unknown"}, {"network_tick": 100},
])
def test_invalid_hash_encoding_or_cross_stream_fields_are_rejected(change):
    with pytest.raises(ValueError):
        clock.decode_clock_record(command_row(**change))


def test_message_size_is_limited_before_base64_allocation(monkeypatch):
    monkeypatch.setattr(clock, "MAX_MESSAGE_BYTES", 2)
    with pytest.raises(ValueError, match="Oversized encoded"):
        clock.decode_clock_record(command_row(protobuf_reencoded="A"*8))
    with pytest.raises(ValueError, match="oversized protobuf"):
        clock.protobuf_fields(b"\x08\x01\x00")


def test_valid_report_is_bound_to_exact_read_bytes_and_source_file(source_report):
    evidence = load(source_report)
    assert evidence.source_demo_verified is True
    assert evidence.sha256 == hashlib.sha256(source_report[0].read_bytes()).hexdigest()
    assert evidence.by_demo_tick("CNETMsg_Tick")[10][0]["network_tick"] == 100
    assert evidence.document["training_ready"] is False
    assert evidence.document["execution_render_epoch_verified"] is False


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("evidence_loss_warnings", False), ("training_ready", 0),
    ("training_ready", True), ("execution_render_epoch_verified", True), ("event_order", "sorted_by_clock"),
    ("tick_rate", True), ("tick_rate", 0), ("tick_rate", -64), ("tick_rate", "64"), ("tick_rate", 10**400),
    ("parser_version", "other"), ("source_demo_sha256", "0"*64), ("source_demo_path", "relative.dem"),
    ("source_demo_path", None), ("parsed_through_demo_tick", True), ("parsed_through_demo_tick", 11),
    ("network_tick_records", True), ("command_envelope_records", 2), ("snapshot_records", 0),
])
def test_report_metadata_and_counts_have_strict_types_and_expected_values(source_report, key, value):
    source_report[1][key] = value
    with pytest.raises(ValueError):
        load(source_report)


@pytest.mark.parametrize("warnings", [{"1": True}, {"1": 0}, {"13": -1}, {"2": 1}, [], None])
def test_unaudited_warning_counts_are_rejected(source_report, warnings):
    source_report[1]["warnings"] = warnings
    with pytest.raises(ValueError, match="warnings"):
        load(source_report)


def test_pinned_non_loss_warnings_are_accepted_without_validation_dependency(source_report):
    source_report[1]["warnings"] = {"1": 2, "13": 1}
    assert load(source_report).source_demo_verified


@pytest.mark.parametrize("windows", [None, [], [None], [{"start_demo_tick": True, "end_demo_tick": 12}],
    [{"start_demo_tick": -1, "end_demo_tick": 12}], [{"start_demo_tick": 12, "end_demo_tick": 12}],
    [{"start_demo_tick": 10, "end_demo_tick": 10000001}], [{"start_demo_tick": 0, "end_demo_tick": 20001}],
    [{"start_demo_tick": 10, "end_demo_tick": 12}, {"start_demo_tick": 11, "end_demo_tick": 13}]])
def test_invalid_window_schema_boundaries_and_budgets(source_report, windows):
    source_report[1]["windows"] = windows
    with pytest.raises(ValueError):
        load(source_report)


@pytest.mark.parametrize("change", [{"clock_event_index": 0}, {"clock_event_index": True},
    {"demo_tick": True}, {"demo_tick": -1}, {"demo_tick": 12}, {"demo_frame": -1}, {"demo_frame": 13.0}])
def test_record_position_has_strict_identity_and_window_membership(source_report, change):
    source_report[1]["records"][0].update(change)
    with pytest.raises(ValueError):
        load(source_report)


@pytest.mark.parametrize("records", [None, [], [None], [1]])
def test_malformed_record_containers_raise_validation_error(source_report, records):
    source_report[1]["records"] = records
    with pytest.raises(ValueError):
        load(source_report)


def test_removing_an_interior_record_is_detected_even_with_updated_counts(source_report):
    report = source_report[1]
    # A forged recount alone cannot conceal the missing event index 2.
    report["records"].insert(2, command_row(clock_event_index=3, command_number=31))
    report["records"][-1]["clock_event_index"] = 4
    del report["records"][2]
    with pytest.raises(ValueError, match="omits an event"):
        load(source_report)


def test_gap_between_separate_requested_windows_is_legitimate(source_report):
    report = source_report[1]
    report["windows"].append({"start_demo_tick": 20, "end_demo_tick": 22})
    report["parsed_through_demo_tick"] = 22
    report["records"].append(raw_row("CNETMsg_Tick", b"\x08\x6e", network_tick=110,
                                     demo_tick=20, demo_frame=23, clock_event_index=80))
    report["network_tick_records"] += 1
    assert len(load(source_report).records) == 4
    # Adjacent windows do not create an unobserved gap in which events can hide.
    report["windows"][0]["end_demo_tick"] = 20
    with pytest.raises(ValueError, match="omits an event"):
        load(source_report)


def test_snapshot_stream_can_be_absent_without_inventing_measured_snapshot_clock(source_report):
    report = source_report[1]
    report["records"].pop()
    report["snapshot_records"] = 0
    evidence = load(source_report)
    assert evidence.by_demo_tick("CSVCMsg_PacketEntities") == {}


def test_aggregate_protobuf_and_report_byte_limits(source_report, monkeypatch):
    monkeypatch.setattr(clock, "MAX_RETAINED_BYTES", 10)
    with pytest.raises(ValueError, match="retained protobuf byte budget"):
        load(source_report)
    monkeypatch.setattr(clock, "MAX_REPORT_BYTES", 10)
    with pytest.raises(ValueError, match="Oversized clock evidence report"):
        load(source_report)


@pytest.mark.parametrize("payload", [b"[]", b"null", b'{"x":1,"x":2}', b'{"tick_rate":NaN}', b"\xff"])
def test_invalid_json_duplicate_keys_nonfinite_literals_and_encoding(source_report, payload):
    path, report, _ = source_report
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        clock.load_network_clock(path, report["demo_id"])


def test_changed_source_is_rejected_but_explicit_read_only_diagnostic_skip_is_labeled(source_report):
    source_report[2].write_bytes(b"changed source")
    with pytest.raises(ValueError, match="source demo changed"):
        load(source_report)
    evidence = load(source_report, verify_demo=False)
    assert evidence.source_demo_verified is False
    with pytest.raises(ValueError, match="verification option"):
        load(source_report, verify_demo=0)


def test_report_changed_during_source_hashing_is_not_bound_to_later_bytes(source_report, monkeypatch):
    path = publish(source_report)
    actual_hash = clock.sha256_file

    def edit_report_while_hashing_source(candidate):
        if candidate == source_report[2]:
            path.write_bytes(path.read_bytes()+b"\n")
        return actual_hash(candidate)

    monkeypatch.setattr(clock, "sha256_file", edit_report_while_hashing_source)
    with pytest.raises(ValueError, match="report changed while loading"):
        clock.load_network_clock(path, source_report[1]["demo_id"])


def canonical(evidence, **changes):
    return {"demo_id": evidence.document["demo_id"], "command_row_id": 42, "demo_tick": 10,
            "player_slot": 4, "command_number": 30, "server_tick_executed": 100, "client_tick": 97, **changes}


def test_command_join_checks_recorded_clock_fields_without_certifying_action_support(source_report):
    evidence = load(source_report)
    rows = clock.command_envelope_matches([canonical(evidence)], evidence)
    assert rows == [{"command_row_id": 42, "status": "matched", "clock_event_index": 2}]
    assert evidence.document["execution_render_epoch_verified"] is False


@pytest.mark.parametrize("change", [{"server_tick_executed": 101}, {"client_tick": 98},
    {"command_number": 31}, {"player_slot": 3}, {"demo_tick": 11}, {"server_tick_executed": None},
    {"client_tick": -1}, {"player_slot": None}])
def test_shifted_unknown_or_sentinel_command_cannot_match(source_report, change):
    evidence = load(source_report)
    assert clock.command_envelope_matches([canonical(evidence, **change)], evidence)[0]["status"] == "unavailable_or_ambiguous"


@pytest.mark.parametrize("change", [{"demo_id": "0"*64}, {"command_row_id": True},
    {"command_row_id": None}, {"server_tick_executed": 100.0}, {"player_slot": True}])
def test_foreign_identity_and_type_aliases_are_rejected(source_report, change):
    evidence = load(source_report)
    with pytest.raises(ValueError):
        clock.command_envelope_matches([canonical(evidence, **change)], evidence)


def test_ambiguous_envelopes_and_duplicate_canonical_ids_are_not_matched(source_report):
    evidence = load(source_report)
    command = canonical(evidence)
    with pytest.raises(ValueError, match="duplicated"):
        clock.command_envelope_matches([command, deepcopy(command)], evidence)
    evidence.records.append({**evidence.records[1], "clock_event_index": 4})
    row = clock.command_envelope_matches([command], evidence)[0]
    assert row["status"] == "unavailable_or_ambiguous"
    assert row["clock_event_index"] is None
