from copy import deepcopy
import random

import pytest

from cs2_data.packet_bounds import audit_packet_bounds, CLIENT_SHA, ENGINE_SHA, FILTER_POLICY


def fixture():
    commands, packets = [], []
    for i, (kind, tick) in enumerate([(1, -1), (8, -1), (13, 0), (7, 10), (7, 11), (7, 12)]):
        command = {"source_command_index": i, "demo_command_kind": kind, "demo_tick": tick, "command_offset": 16 + i * 20}
        commands.append(command)
        if kind in (7, 8, 13):
            packets.append({**command, "packet_data_size": i, "packet_data_sha256": str(i) * 64,
                            "network_ticks": [{"network_tick": 100 + max(tick, 0)}],
                            "snapshot_ticks": [], "command_envelopes": [],
                            "native_seek_filter_policy": FILTER_POLICY,
                            "native_seek_filtered_data_size": i - 1, "native_seek_filtered_data_sha256": hex(i)[2:] * 64})
    source = {"schema_version": 2, "source_demo_sha256": "a" * 64, "through_demo_tick": 12,
              "coverage_next_header_tick": 13, "demo_commands": commands, "packets": packets,
              "native_seek_filter_profile": {"policy_id": FILTER_POLICY, "engine_sha256": ENGINE_SHA,
                  "client_sha256": CLIENT_SHA, "transformation": "ordered_original_message_bit_spans_then_zero_padding"}}
    rows = [{"event": "header", "schema_version": 1, "thread_id": 99, "engine_sha256": ENGINE_SHA,
             "native_observation": {"client_sha256": CLIENT_SHA},
             "packet_trace": {"schema_version": 1, "status": "prepared", "engine_sha256": ENGINE_SHA,
                              "demo_player_vtable_rva": "0x52db68", "read_packet_slot": 22,
                              "read_packet_function_rva": "0x2b820", "packet_data_offset": 80,
                              "packet_byte_count_offset": 116, "selected_source_tick_offset": 556,
                              "payload_hash": "SHA256_exact_returned_network_packet_bytes"}}]
    state = {"schema_version": 1, "healthy": True, "status": "unavailable", "reason": "",
             "read_invocations": 0, "last_completed_read_invocation": 0, "reads_in_flight": 0,
             "active_read_invocation": None, "returned_packets": 0, "null_returns": 0,
             "latest_returned_packet": None, "process_lifetime_max_returned_source_demo_tick": None}
    rows.append({"event": "demo_packet_hook_installed", "thread_id": 99, "packet_trace": deepcopy(state)})
    states = []
    for number, packet_index in enumerate([0, 1, 2, None, 3], 1):
        tick = packets[packet_index]["demo_tick"] if packet_index is not None else 10
        player = {"layout_verified": True, "raw_selected_tick_0x22c": tick, "playback_epoch_0x204": -5546,
                  "seek_target_0x208": -1, "pending_seek_0x18c0": -1, "seeking_flag_0x1230": 1,
                  "packet_filter_flag_0x18a1": 1, "alternate_gate_flag_0x18a2": 0}
        entry = {"event": "demo_packet_read", "schema_version": 1, "read_invocation": number,
                 "phase": "entry", "clock_on_engine_thread": True, "thread_id": 99,
                 "demo_player_address": 1234, "player_state": player,
                 "packet_trace": {**deepcopy(state), "read_invocations": number,
                                  "reads_in_flight": 1, "active_read_invocation": number}}
        returned = {**deepcopy(entry), "phase": "return", "player_state_after": deepcopy(player),
                    "returned_packet": packet_index is not None, "payload_readable": True, "packet": None}
        if packet_index is None:
            state["null_returns"] += 1
        else:
            source_packet = packets[packet_index]
            packet = {"returned_packet_index": state["returned_packets"], "read_invocation": number,
                      "source_demo_tick": tick, "byte_count": source_packet["packet_data_size"],
                      "payload_sha256": source_packet["packet_data_sha256"], "payload_readable": True}
            returned["packet"] = packet
            state["returned_packets"] += 1
            state["latest_returned_packet"] = deepcopy(packet)
            if tick >= 0:
                state["process_lifetime_max_returned_source_demo_tick"] = max(state["process_lifetime_max_returned_source_demo_tick"] or 0, tick)
        state.update(read_invocations=number, last_completed_read_invocation=number,
                     status="observed_returned_packet_bytes")
        returned["packet_trace"] = deepcopy(state)
        rows.extend((entry, returned))
        states.append(deepcopy(state))

    def clock(index):
        return {"healthy": True, "status": "observed_message_clock", "handlers_in_flight": 0,
                "server_tick": 110 if index == 3 else 111, "packet_trace": deepcopy(states[index])}

    movie = {"event": "movie_frame", "capture_index": 0, "movie_name": "test_", "thread_id": 99,
             "native_clock": clock(3), "native_clock_after": clock(3)}
    pixel = {"event": "pixel_readback", "thread_id": 99, "success": True, "clock_on_engine_thread": True,
             "submission_candidate": deepcopy(movie), "native_clock": clock(3), "native_clock_after": clock(3)}
    end = {"event": "movie_end", "movie_name": "test_", "next_capture_index": 1, "thread_id": 99, "native_clock": clock(4)}
    rows.extend((movie, pixel, end))
    return rows, source


def event(rows, name):
    return next(r for r in rows if r["event"] == name)


def read(rows, number, phase="return"):
    return next(r for r in rows if r["event"] == "demo_packet_read" and r["read_invocation"] == number and r["phase"] == phase)


def test_complete_history_including_checkpoint_and_null_read():
    rows, source = fixture()
    result = audit_packet_bounds(rows, source)
    assert result["status"] == "verified"
    assert result["frames"][0]["upper_source_demo_tick"] == 10
    assert result["frames"][0]["upper_server_tick"] == 110
    assert result["endpoint"]["upper_server_tick"] == 111
    assert result["summary"] == {"verified_frames": 1, "unknown_frames": 0, "read_invocations": 5, "exact": 4}
    assert "training_ready" not in result


def test_physical_write_order_is_not_observation_order():
    rows, source = fixture()
    expected = audit_packet_bounds(rows, source)
    random.Random(123).shuffle(rows)
    assert audit_packet_bounds(rows, source) == expected


def test_nested_pixel_callback_uses_maximum_without_inventing_total_order():
    rows, source = fixture()
    # A packet read completes after the pixel callback and before the outer
    # movie callback returns. All four linked observations remain accounted.
    event(rows, "movie_frame")["native_clock_after"] = deepcopy(event(rows, "movie_end")["native_clock"])
    result = audit_packet_bounds(rows, source)
    assert result["frames"][0]["verified"] is True
    assert result["frames"][0]["upper_server_tick"] == 111
    assert result["frames"][0]["read_invocation_end"] == 5


@pytest.mark.parametrize("key,value", [("returned_packets", 1), ("null_returns", 99),
                                      ("last_completed_read_invocation", True), ("read_invocations", 4.0),
                                      ("process_lifetime_max_returned_source_demo_tick", 0),
                                      ("reads_in_flight", 1), ("healthy", False)])
def test_forged_observation_snapshot_is_unknown(key, value):
    rows, source = fixture()
    event(rows, "movie_frame")["native_clock"]["packet_trace"][key] = value
    assert audit_packet_bounds(rows, source)["frames"][0]["status"] == "unknown"


@pytest.mark.parametrize("alter", ["remove_entry", "remove_return", "duplicate", "wrong_thread", "bad_layout", "bad_counter"])
def test_prior_read_corruption_cannot_be_hidden_by_valid_current_frame(alter):
    rows, source = fixture()
    row = read(rows, 2)
    if alter == "remove_entry":
        rows.remove(read(rows, 2, "entry"))
    elif alter == "remove_return":
        rows.remove(row)
    elif alter == "duplicate":
        rows.append(deepcopy(row))
    elif alter == "wrong_thread":
        row["thread_id"] = 100
    elif alter == "bad_layout":
        row["player_state_after"]["layout_verified"] = False
    else:
        row["packet_trace"]["returned_packets"] = 88
    assert audit_packet_bounds(rows, source)["frames"][0]["status"] == "unknown"


def test_prior_filtered_packet_requires_verified_transformation_and_native_flag():
    rows, source = fixture()
    # Replace all references to the checkpoint's bytes with the deterministic
    # filter output; its original source bytes retain their independent hash.
    source["packets"][1]["native_seek_filtered_data_sha256"] = "f" * 64
    source["packets"][1]["native_seek_filtered_data_size"] = 1

    def replace(value):
        if isinstance(value, dict):
            if value.get("returned_packet_index") == 1:
                value.update(payload_sha256="f" * 64, byte_count=1)
            for child in value.values():
                replace(child)
        elif isinstance(value, list):
            for child in value:
                replace(child)
    replace(rows)
    result = audit_packet_bounds(rows, source)
    assert result["status"] == "verified"
    assert result["summary"]["native_seek_filtered"] == 1
    source["native_seek_filter_profile"]["engine_sha256"] = "b" * 64
    assert "prior_returned_packet_source_unmatched" in audit_packet_bounds(rows, source)["reasons"]
    source["native_seek_filter_profile"]["engine_sha256"] = ENGINE_SHA
    read(rows, 2, "entry")["player_state"]["packet_filter_flag_0x18a1"] = 0
    read(rows, 2)["player_state"]["packet_filter_flag_0x18a1"] = 0
    assert "prior_returned_packet_source_unmatched" in audit_packet_bounds(rows, source)["reasons"]


def test_unmatched_bootstrap_packet_remains_unknown():
    rows, source = fixture()
    source["packets"][0]["packet_data_sha256"] = "f" * 64
    source["packets"][0]["native_seek_filtered_data_sha256"] = "e" * 64
    result = audit_packet_bounds(rows, source)
    assert result["frames"][0]["verified"] is False
    assert "prior_returned_packet_source_unmatched" in result["reasons"]


def test_huge_invocation_is_rejected_without_allocating_its_numeric_range():
    rows, source = fixture()
    read(rows, 1)["read_invocation"] = 2**62
    result = audit_packet_bounds(rows, source)
    assert "missing_packet_read_invocations" in result["reasons"]


def test_second_demo_player_instance_cannot_reuse_first_bootstrap_evidence():
    rows, source = fixture()
    for phase in ("entry", "return"):
        read(rows, 2, phase)["demo_player_address"] = 5678
    assert "demo_player_instance_changed" in audit_packet_bounds(rows, source)["reasons"]


@pytest.mark.parametrize("alter", ["no_install", "wrong_engine", "no_endpoint", "extra_pixel", "bad_candidate", "seek_active", "nonordinary_future", "unsupported_prior", "incomplete_source"])
def test_guard_and_boundary_failures(alter):
    rows, source = fixture()
    if alter == "no_install":
        rows.remove(event(rows, "demo_packet_hook_installed"))
    elif alter == "wrong_engine":
        event(rows, "header")["engine_sha256"] = "f" * 64
    elif alter == "no_endpoint":
        rows.remove(event(rows, "movie_end"))
    elif alter == "extra_pixel":
        rows.append(deepcopy(event(rows, "pixel_readback")))
    elif alter == "bad_candidate":
        event(rows, "pixel_readback")["submission_candidate"]["native_clock"]["server_tick"] = 99
    elif alter == "seek_active":
        read(rows, 4)["player_state_after"]["seek_target_0x208"] = 30
    elif alter == "nonordinary_future":
        source["demo_commands"][4]["demo_command_kind"] = 13
        source["packets"][3]["demo_command_kind"] = 13
    elif alter == "unsupported_prior":
        source["demo_commands"][0]["demo_command_kind"] = 9
    else:
        source["coverage_next_header_tick"] = source["through_demo_tick"]
    assert audit_packet_bounds(rows, source)["frames"][0]["verified"] is False


def test_ceiling_retains_all_source_prefix_clocks_including_skipped_data():
    rows, source = fixture()
    # A checkpoint before the current packet contains a later command clock.
    # This conservative bound must not reset to the latest delivered tick110.
    source["packets"][1]["command_envelopes"] = [{"server_tick_executed": 150}]
    result = audit_packet_bounds(rows, source)
    assert result["frames"][0]["upper_server_tick"] == 150
    assert result["endpoint"]["upper_server_tick"] == 150
