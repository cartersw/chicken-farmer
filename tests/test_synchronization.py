"""Native handler transcripts with independent counters and temporal failures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from cs2_data.clock_evidence import NetworkClockEvidence
from cs2_data import synchronization as sync


def evidence(*ticks):
    rows = []
    for tick in ticks:
        common = {"demo_tick": tick+10, "demo_frame": tick+13}
        rows.extend([
            {**common, "clock_event_index": len(rows)+1, "message_type": "CNETMsg_Tick", "network_tick": tick},
            {**common, "clock_event_index": len(rows)+2, "message_type": "CSVCMsg_PacketEntities", "snapshot_server_tick": tick},
            {**common, "clock_event_index": len(rows)+3, "message_type": "CMsgServerUserCmd",
             "server_tick_executed": tick, "client_tick": tick-3, "player_slot": 4, "command_number": tick+100}])
    return NetworkClockEvidence(Path("fixture.json"), "a"*64, {"demo_id": "b"*64}, rows)


def header():
    return {"event": "header", "schema_version": 1, "engine_sha256": sync.ENGINE_SHA,
            "native_observation": {"client_sha256": sync.CLIENT_SHA},
            "native_clock": {"schema_version": 1, "scope": "delivered_net_tick_packet_entities_and_user_command_envelopes",
                "net_tick_slot": 88, "packet_entities_dispatch_slot": 111,
                "packet_entities_apply_slot": 129, "user_commands_slot": 128}}


def segment(tick=100, *, generation=1, previous_id=0, nested_apply=True):
    """NET return, entity dispatch (optionally nested apply), then command return."""
    address = 4096*generation
    epoch = {"event": "network_clock_epoch", "schema_version": 1, "client_generation": generation,
             "client_address": address, "handlers_in_flight": 0, "last_completed_invocation": previous_id,
             "reason": "test_epoch"}

    def entry(kind, invocation, in_flight=1):
        user = kind == "user_commands"
        row = {"event": "network_message", "schema_version": 1, "phase": "entry", "kind": kind,
               "invocation_index": previous_id+invocation, "client_generation": generation,
               "client_address": address, "active_client": True, "message_layout_verified": True,
               "clock_on_engine_thread": True, "has_explicit_tick": not user,
               "raw_message_tick": None if user else tick, "effective_server_tick": None if user else tick,
               "server_tick_before": tick-1 if kind == "net_tick" else tick,
               "prior_delivered_net_tick": None if kind == "net_tick" else tick,
               "replay_demo_tick": 10, "handlers_in_flight": in_flight}
        if user:
            row.update(user_commands_target_is_noop=True, commands=[{
                "envelope_index": 0, "command_number": tick+100, "player_slot": 4,
                "server_tick_executed": tick, "client_tick": tick-3}])
        return row

    def returned(row, in_flight=0):
        result = {**deepcopy(row), "phase": "return", "generation_after": generation,
                  "handlers_in_flight": in_flight, "healthy": True, "error": "",
                  "original_result": True, "server_tick_after": tick, "replay_demo_tick_after": 10}
        if row["kind"] == "user_commands":
            result["user_commands_target_is_noop_after"] = True
        return result

    net, dispatch = entry("net_tick", 1), entry("packet_entities_dispatch", 2)
    result = [epoch, net, returned(net), dispatch]
    if nested_apply:
        apply = entry("packet_entities_apply", 3, in_flight=2)
        result += [apply, returned(apply, in_flight=1)]
    result.append(returned(dispatch))
    last_id = 4 if nested_apply else 3
    commands = entry("user_commands", last_id)
    result += [commands, returned(commands)]
    snapshot = {"schema_version": 1, "status": "observed_message_clock", "phase": "movie_submission",
                "client_generation": generation, "client_address": address,
                "replay_demo_tick": 10, "server_tick": tick, "client_tick": tick,
                "last_delivered_net_tick": tick, "max_delivered_net_tick": tick,
                "max_observed_entity_tick": tick, "max_observed_user_command_execution_tick": tick,
                "max_observed_user_command_execution_ticks_by_slot": {"4": tick},
                "last_completed_invocation": previous_id+last_id, "handlers_in_flight": 0,
                "net_tick_returns": 1, "entity_dispatch_returns": 1,
                "entity_apply_returns": int(nested_apply), "user_command_returns": 1,
                "healthy": True, "reason": "", "user_commands_target_is_noop": True,
                "execution_clock_verified": False, "causality_bound_verified": False}
    result += [{"event": "movie_frame", "capture_index": generation-1, "native_clock": snapshot}]
    return result


def transcript(**kwargs):
    return [header(), *segment(**kwargs)]


def messages(rows, kind=None, phase=None):
    return [row for row in rows if row.get("event") == "network_message"
            and (kind is None or row["kind"] == kind) and (phase is None or row["phase"] == phase)]


def test_nested_handlers_are_recounted_without_claiming_execution_or_pixel_phase():
    report = sync.audit_native_messages(transcript(), evidence(100))
    assert report["status"] == "matched_message_clocks"
    assert report["message_entries"] == {"net_tick": 1, "packet_entities_dispatch": 1,
                                         "packet_entities_apply": 1, "user_commands": 1}
    frame = report["frames"][0]
    assert frame["source_records"]["net_tick"] == {"clock_event_index": 1, "demo_tick": 110, "server_tick": 100}
    assert frame["observed_message_maximum_process_lifetime"] == 100
    assert frame["causality_bound_verified"] is False
    assert frame["training_ready"] is report["training_ready"] is False


def test_installed_apply_hook_may_legitimately_receive_zero_calls():
    report = sync.audit_native_messages(transcript(nested_apply=False), evidence(100))
    assert report["status"] == "matched_message_clocks"
    assert report["message_entries"].get("packet_entities_apply", 0) == 0
    assert report["frames"][0]["max_observed_entity_tick"] == 100


def test_seek_resets_generation_counters_but_preserves_process_lifetime_maximum():
    rows = [header(), *segment(100), *segment(50, generation=2, previous_id=4)]
    report = sync.audit_native_messages(rows, evidence(100, 50))
    assert report["status"] == "matched_message_clocks"
    assert [row["server_tick"] for row in report["frames"]] == [100, 50]
    assert [row["observed_message_maximum_process_lifetime"] for row in report["frames"]] == [100, 100]
    assert report["client_epochs"][1]["process_lifetime_maximum_retained"] == 100


@pytest.mark.parametrize("key,value", [("schema_version", True), ("net_tick_slot", 88.0),
                                       ("packet_entities_apply_slot", 128), ("scope", "trusted_execution")])
def test_unsupported_or_type_aliased_header_cannot_enable_native_audit(key, value):
    rows = transcript()
    rows[0]["native_clock"][key] = value
    result = sync.audit_native_messages(rows, evidence(100))
    assert result["status"] == "unavailable"
    assert result["frames"] == []


@pytest.mark.parametrize("change", [{"native_clock": None}, {"native_clock": []},
                                   {"native_observation": None}, {"schema_version": True}, {"event": "movie_frame"}])
def test_malformed_header_does_not_raise_attribute_errors(change):
    rows = transcript()
    rows[0].update(change)
    assert sync.audit_native_messages(rows, evidence(100))["status"] == "unavailable"


@pytest.mark.parametrize("rows", [None, [None], [1], {}])
def test_invalid_ledger_container_is_rejected(rows):
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


def test_missing_snapshot_or_unavailable_native_observation_stays_unknown():
    rows = transcript()
    rows[-1]["native_clock"] = {"schema_version": 1, "status": "unavailable", "reason": "not_active_engine_thread"}
    report = sync.audit_native_messages(rows, evidence(100))
    assert report["status"] == "unavailable"
    assert report["frames"][0]["reason_codes"] == ["not_active_engine_thread"]
    rows[-1]["native_clock"]["schema_version"] = True
    assert sync.audit_native_messages(rows, evidence(100))["frames"][0]["reason_codes"] == ["missing_native_clock_snapshot"]


@pytest.mark.parametrize("key,value", [("schema_version", True), ("client_generation", True),
    ("client_address", 4096.0), ("handlers_in_flight", False), ("last_completed_invocation", False)])
def test_epoch_identity_schema_and_counts_are_type_strict(key, value):
    rows = transcript()
    rows[1][key] = value
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


@pytest.mark.parametrize("key,value", [("client_generation", True), ("client_address", 4096.0),
    ("handlers_in_flight", True), ("invocation_index", True), ("active_client", 1),
    ("clock_on_engine_thread", 1), ("message_layout_verified", 1), ("has_explicit_tick", 1),
    ("raw_message_tick", 100.0), ("prior_delivered_net_tick", False)])
def test_native_entry_cannot_use_boolean_or_float_aliases(key, value):
    rows = transcript()
    messages(rows, "net_tick", "entry")[0][key] = value
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


@pytest.mark.parametrize("key,value", [("last_completed_invocation", 4.0), ("handlers_in_flight", False),
    ("client_generation", True), ("client_address", 4096.0), ("net_tick_returns", True),
    ("max_observed_user_command_execution_ticks_by_slot", {"4": 100.0}), ("server_tick", 100.0)])
def test_snapshot_recount_checks_nested_types_and_not_only_equality(key, value):
    rows = transcript()
    rows[-1]["native_clock"][key] = value
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


def test_active_client_flag_must_agree_with_epoch_pointer():
    rows = transcript()
    for row in messages(rows, "net_tick"):
        row["client_address"] = 8192
    with pytest.raises(ValueError, match="active-client claim"):
        sync.audit_native_messages(rows, evidence(100))


def test_declared_effective_tick_cannot_shift_the_retained_raw_tick():
    rows = transcript()
    for row in messages(rows, "net_tick"):
        row["effective_server_tick"] = 101
    with pytest.raises(ValueError, match="Effective native tick"):
        sync.audit_native_messages(rows, evidence(100, 101))


def test_entity_tick_without_presence_uses_observed_server_before_not_backing_member():
    rows = transcript()
    for row in messages(rows):
        if row["kind"].startswith("packet_entities"):
            row.update(has_explicit_tick=False, raw_message_tick=999)
    assert sync.audit_native_messages(rows, evidence(100))["status"] == "matched_message_clocks"
    for row in messages(rows):
        if row["kind"].startswith("packet_entities"):
            row["effective_server_tick"] = 999
    with pytest.raises(ValueError, match="Effective native tick"):
        sync.audit_native_messages(rows, evidence(100, 999))


@pytest.mark.parametrize("change", [{"client_tick": 97.0}, {"player_slot": True},
                                    {"envelope_index": True}, {"server_tick_executed": 2**31}])
def test_native_command_fields_have_exact_int32_types_and_envelope_identity(change):
    rows = transcript()
    messages(rows, "user_commands", "entry")[0]["commands"][0].update(change)
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


@pytest.mark.parametrize("commands", [[None], None, {}, [dict(envelope_index=1)]])
def test_missing_or_malformed_envelopes_cannot_be_recounted(commands):
    rows = transcript()
    messages(rows, "user_commands", "entry")[0]["commands"] = commands
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


def test_return_cannot_change_nested_command_values_even_when_python_equality_holds():
    rows = transcript()
    messages(rows, "user_commands", "return")[0]["commands"][0]["server_tick_executed"] = 100.0
    with pytest.raises(ValueError, match="mutated between entry/return"):
        sync.audit_native_messages(rows, evidence(100))


@pytest.mark.parametrize("field,value", [("generation_after", True), ("handlers_in_flight", False),
                                         ("original_result", 1), ("server_tick_after", 100.0)])
def test_return_generation_and_result_types_are_strict(field, value):
    rows = transcript()
    messages(rows, "net_tick", "return")[0][field] = value
    with pytest.raises(ValueError):
        sync.audit_native_messages(rows, evidence(100))


def test_missing_handler_return_does_not_disappear_behind_snapshot_counters():
    rows = transcript()
    rows.remove(messages(rows, "user_commands", "return")[0])
    rows.pop()  # No frame is required for detecting an incomplete final handler.
    with pytest.raises(ValueError, match="incomplete message handlers"):
        sync.audit_native_messages(rows, evidence(100))


def test_missing_entire_handler_pair_leaves_detectable_invocation_gap():
    rows = transcript()
    for row in messages(rows, "packet_entities_apply"):
        rows.remove(row)
    with pytest.raises(ValueError, match="Missing, reordered, or repeated"):
        sync.audit_native_messages(rows, evidence(100))


def test_return_without_entry_and_repeated_return_are_rejected():
    rows = transcript()
    rows.remove(messages(rows, "net_tick", "entry")[0])
    with pytest.raises(ValueError, match="return lacks its entry"):
        sync.audit_native_messages(rows, evidence(100))
    rows = transcript()
    rows.insert(4, deepcopy(messages(rows, "net_tick", "return")[0]))
    with pytest.raises(ValueError, match="return lacks its entry"):
        sync.audit_native_messages(rows, evidence(100))


@pytest.mark.parametrize("mutation,reason", [("net_commit", "native_net_tick_not_committed"),
    ("off_thread", "native_handler_layout_or_thread_unverified"),
    ("consumer_after", "user_commands_consumer_unverified_after_return")])
def test_observed_native_failures_remain_unavailable_despite_matching_payloads(mutation, reason):
    rows = transcript()
    if mutation == "net_commit":
        messages(rows, "net_tick", "return")[0]["original_result"] = False
    elif mutation == "off_thread":
        for row in messages(rows, "packet_entities_dispatch"):
            row["clock_on_engine_thread"] = False
    else:
        messages(rows, "user_commands", "return")[0]["user_commands_target_is_noop_after"] = False
    result = sync.audit_native_messages(rows, evidence(100))
    assert result["status"] == "unavailable"
    assert reason in result["frames"][0]["reason_codes"]


def test_duplicate_source_clock_cannot_be_joined_by_choosing_an_arbitrary_row():
    source = evidence(100)
    source.records.append({**source.records[0], "clock_event_index": 99, "demo_tick": 111})
    result = sync.audit_native_messages(transcript(), source)
    assert result["status"] == "unavailable"
    assert "net_tick_source_unavailable_or_ambiguous" in result["frames"][0]["reason_codes"]
    assert result["frames"][0]["source_records"]["net_tick"] is None


def test_source_window_missing_the_native_clock_is_unknown_not_offset_fitted():
    result = sync.audit_native_messages(transcript(), evidence(101))
    assert result["status"] == "unavailable"
    assert result["frames"][0]["reason_codes"] == ["net_tick_source_unavailable_or_ambiguous",
                                                      "packet_entities_source_unavailable_or_ambiguous"]


def test_endpoint_clock_is_recounted_as_an_observation():
    rows = transcript()
    rows.append({"event": "movie_end", "next_capture_index": 1,
                 "native_clock": {**rows[-1]["native_clock"], "phase": "movie_endpoint"}})
    result = sync.audit_native_messages(rows, evidence(100))
    assert [(row["event"], row["frame_index"]) for row in result["frames"]] == [("movie_frame", 0), ("movie_end", 1)]


def test_loader_recomputes_sources_and_rejects_rehashed_edited_report(tmp_path, monkeypatch):
    # A caller-supplied output SHA is not an approval token: the loader compares
    # the complete diagnostic report against a fresh source computation.
    source = tmp_path / "source.json"
    source.write_text('{"tick":100}')
    path = tmp_path / "audit.json"

    def recompute(parsed, dataset, network_clock):
        return {"schema_version": 1, "training_ready": False,
                "inputs": {"parsed": str(parsed), "dataset": str(dataset), "network_clock": str(network_clock)},
                "source_sha256": hashlib.sha256(network_clock.read_bytes()).hexdigest(),
                "source_tick": json.loads(network_clock.read_text())["tick"]}

    monkeypatch.setattr(sync, "recompute_synchronization", recompute)
    report = recompute(tmp_path / "parsed", tmp_path / "dataset", source)
    path.write_text(json.dumps(report))
    assert sync.load_synchronization(path) == report
    report["training_ready"] = True
    path.write_text(json.dumps(report))
    assert hashlib.sha256(path.read_bytes()).hexdigest()  # Rehashing does not authorize edited fields.
    with pytest.raises(ValueError, match="recomputed source evidence"):
        sync.load_synchronization(path)
    report["training_ready"] = False
    source.write_text('{"tick":101}')
    report["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(report))  # Updated source hash but stale clock association.
    with pytest.raises(ValueError, match="recomputed source evidence"):
        sync.load_synchronization(path)
    report = recompute(tmp_path / "parsed", tmp_path / "dataset", source)
    report["schema_version"] = True
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="recomputed source evidence"):
        sync.load_synchronization(path)
