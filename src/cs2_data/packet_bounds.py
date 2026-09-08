"""Audit native returned-packet history against a freshly scanned source demo.

This pure function expects ``source`` from ``scan_demo_packets`` in the same
verification operation, not an authenticated-looking JSON sidecar. Its narrow
certificate covers a source packet prefix under the exact native reader profile.
Pixel identity, POV, message-clock auditing and command support are independent
caller obligations. No result grants training readiness.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_right
import hashlib
import json
from .session_evidence import SessionLedger, events, endpoints, audit_cached
from typing import Any
from .native_replay_profile import LEGACY_PROFILE, get_native_replay_profile, header_matches_profile

ENGINE_SHA = "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac"
CLIENT_SHA = "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4"
PROFILE = "engine_26dc9c5f_ordinary_returned_packet_prefix_v1"
FILTER_POLICY = "cs2-14178-seek-message-filter-v1"


def _same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value < 2**63


def _subset(row, wanted):
    return isinstance(row, dict) and all(_same(row.get(k), v) for k, v in wanted.items())


def _state_valid(state):
    return isinstance(state, dict) and state.get("layout_verified") is True and all(
        type(state.get(key)) is int and -2**31 <= state[key] < 2**31 for key in (
            "raw_selected_tick_0x22c", "playback_epoch_0x204", "seek_target_0x208", "pending_seek_0x18c0")) and all(
        type(state.get(key)) is int and state[key] in (0, 1) for key in (
            "seeking_flag_0x1230", "packet_filter_flag_0x18a1", "alternate_gate_flag_0x18a2"))


def _source(source, profile):
    """Validate scanner shape, prefix order, and independently derived clocks."""
    if (not isinstance(source, dict) or type(source.get("schema_version")) is not int
            or source["schema_version"] not in (1, 2) or not isinstance(source.get("source_demo_sha256"), str)):
        raise ValueError("invalid_source_scan")
    native_profile = profile["native_profile"]
    if (source.get("native_profile", LEGACY_PROFILE) != native_profile):
        raise ValueError("source_native_profile_mismatch")
    commands, packets = source.get("demo_commands"), source.get("packets")
    if not isinstance(commands, list) or not commands or not isinstance(packets, list):
        raise ValueError("incomplete_source_scan")
    through, next_tick = source.get("through_demo_tick"), source.get("coverage_next_header_tick")
    if not _integer(through) or not _integer(next_tick) or next_tick <= through:
        raise ValueError("incomplete_source_prefix_coverage")
    previous_tick, previous_offset = -1, -1
    by_command = {}
    for index, command in enumerate(commands):
        if (not isinstance(command, dict) or not _same(command.get("source_command_index"), index)
                or not _integer(command.get("demo_tick"), -1)
                or not _integer(command.get("command_offset"))
                or not _integer(command.get("demo_command_kind"))
                or command["demo_tick"] < previous_tick or command["demo_tick"] > through
                or command["command_offset"] <= previous_offset):
            raise ValueError("invalid_source_command_order")
        previous_tick, previous_offset = command["demo_tick"], command["command_offset"]
    index, filtered_index = defaultdict(list), defaultdict(list)
    filter_verified = source["schema_version"] == 2 and _subset(source.get("native_seek_filter_profile"), {
        "policy_id": profile["seek_filter_policy"], "engine_sha256": profile["engine_sha256"],
        "client_sha256": profile["client_sha256"],
        "transformation": "ordered_original_message_bit_spans_then_zero_padding"})
    if native_profile != LEGACY_PROFILE and not filter_verified:
        raise ValueError("unsupported_source_seek_filter_profile")
    for packet in packets:
        command_index = packet.get("source_command_index") if isinstance(packet, dict) else None
        if (native_profile != LEGACY_PROFILE and isinstance(packet, dict) and
                packet.get("native_seek_filter_policy") != profile["seek_filter_policy"]):
            raise ValueError("source_packet_filter_policy_mismatch")
        if (not _integer(command_index) or command_index >= len(commands) or command_index in by_command
                or not _subset(packet, {k: commands[command_index][k] for k in
                    ("source_command_index", "demo_tick", "demo_command_kind", "command_offset")})
                or packet["demo_command_kind"] not in (7, 8, 13)
                or not _integer(packet.get("packet_data_size"))
                or not isinstance(packet.get("packet_data_sha256"), str)):
            raise ValueError("invalid_source_packet_inventory")
        by_command[command_index] = packet
        index[(packet["packet_data_sha256"], packet["packet_data_size"], packet["demo_tick"])].append(packet)
        if (filter_verified and packet.get("native_seek_filter_policy") == profile["seek_filter_policy"]
                and isinstance(packet.get("native_seek_filtered_data_sha256"), str)
                and _integer(packet.get("native_seek_filtered_data_size"))):
            filtered_index[(packet["native_seek_filtered_data_sha256"], packet["native_seek_filtered_data_size"], packet["demo_tick"])].append(packet)
    if set(by_command) != {i for i, c in enumerate(commands) if c["demo_command_kind"] in (7, 8, 13)}:
        raise ValueError("missing_source_packet_inventory")
    # The prefix ceiling deliberately includes skipped/checkpoint packets too.
    # This can only increase the bound; it never forgets earlier seek information.
    ceiling, prefix_clocks = None, []
    for command in commands:
        packet = by_command.get(command["source_command_index"])
        if packet:
            for collection, field in (("network_ticks", "network_tick"), ("snapshot_ticks", "server_tick"),
                                      ("command_envelopes", "server_tick_executed")):
                values = packet.get(collection)
                if not isinstance(values, list):
                    raise ValueError("missing_source_packet_clocks")
                for row in values:
                    value = row.get(field) if isinstance(row, dict) else None
                    if value is not None and (type(value) is not int or not -2**31 <= value <= 2**32 - 1):
                        raise ValueError("invalid_source_packet_clock")
                    if type(value) is int and value >= 0:
                        ceiling = value if ceiling is None else max(ceiling, value)
        prefix_clocks.append(ceiling)
    return commands, by_command, index, filtered_index, prefix_clocks


class _Audit:
    def __init__(self, records, source, profile):
        self.records, self.source = records, source
        self.profile = profile
        self.global_reasons = set()
        self.commands, self.source_packets, self.source_index, self.filtered_index, self.prefix_clocks = _source(source, profile)
        self.command_ticks = [c["demo_tick"] for c in self.commands]
        self.nonordinary = [0]
        self.bad_prior = []
        bad = set()
        for c in self.commands:
            self.nonordinary.append(self.nonordinary[-1] + (c["demo_command_kind"] != 7))
            if c["demo_tick"] >= 0 and c["demo_command_kind"] not in (7, 13):
                bad.add("unsupported_prior_nonpacket_demo_command")
            elif c["demo_tick"] < 0 and c["demo_command_kind"] not in (1, 3, 4, 5, 6, 7, 8, 18):
                bad.add("unsupported_bootstrap_demo_command")
            self.bad_prior.append(frozenset(bad))
        self.states = [{"read_invocations": 0, "last_completed_read_invocation": 0,
                       "reads_in_flight": 0, "active_read_invocation": None, "returned_packets": 0,
                       "null_returns": 0, "latest_returned_packet": None,
                       "process_lifetime_max_returned_source_demo_tick": None}]
        self.prefix_reasons = [set()]
        self.max_indices = [-1]
        self.max_scheduling_ticks = [-1]
        self.latest_matches = [None]
        self.return_rows = [None]
        self.matches = Counter()
        self.unmatched = []
        self.thread = None
        self._header()
        self._reads()

    def _header(self):
        headers = list(events(self.records, ("header",)))
        if len(headers) != 1:
            self.global_reasons.add("missing_or_duplicate_native_header")
            return
        header = headers[0]
        profile = self.profile
        self.thread = header.get("thread_id")
        if (not _integer(self.thread, 1) or not _subset(header, {"schema_version": 1})
                or not header_matches_profile(header, native_profile=profile["native_profile"])
                or not _subset(header.get("packet_trace"), {"schema_version": 1, "status": "prepared",
                    "engine_sha256": profile["engine_sha256"], "demo_player_vtable_rva": "0x52db68", "read_packet_slot": 22,
                    "read_packet_function_rva": "0x2b820", "packet_data_offset": 80,
                    "packet_byte_count_offset": 116, "selected_source_tick_offset": 556,
                    "payload_hash": "SHA256_exact_returned_network_packet_bytes"})):
            self.global_reasons.add("unsupported_native_packet_contract")
        installs = list(events(self.records, ("demo_packet_hook_installed",)))
        if (len(installs) != 1 or not _same(installs[0].get("thread_id"), self.thread)
                or not _subset(installs[0].get("packet_trace"), self.states[0])
                or installs[0]["packet_trace"].get("healthy") is not True):
            self.global_reasons.add("packet_hook_installation_unverified")

    def _reads(self):
        pairs = defaultdict(dict)
        for row in events(self.records, ("demo_packet_read",)):
            if row.get("event") != "demo_packet_read":
                continue
            number, phase = row.get("read_invocation"), row.get("phase")
            if not _integer(number, 1) or phase not in ("entry", "return") or phase in pairs[number]:
                self.global_reasons.add("invalid_or_duplicate_packet_read_pair")
                continue
            pairs[number][phase] = row
        if not pairs or any(number != expected for expected, number in enumerate(sorted(pairs), 1)):
            self.global_reasons.add("missing_packet_read_invocations")
            return
        demo_address = pairs[1].get("entry", {}).get("demo_player_address")
        for number in sorted(pairs):
            pair = pairs[number]
            if set(pair) != {"entry", "return"}:
                self.global_reasons.add("incomplete_packet_read_pair")
                return
            before, after = pair["entry"], pair["return"]
            reasons = set(self.prefix_reasons[-1])
            state = dict(self.states[-1])
            entry_expected = {**state, "read_invocations": number, "reads_in_flight": 1,
                              "active_read_invocation": number}
            if not _subset(before.get("packet_trace"), entry_expected):
                reasons.add("packet_entry_counters_disagree")
            for row in (before, after):
                if (not _subset(row, {"schema_version": 1, "clock_on_engine_thread": True,
                                     "thread_id": self.thread}) or not _state_valid(row.get("player_state"))
                        or not isinstance(row.get("packet_trace"), dict)
                        or row["packet_trace"].get("healthy") is not True):
                    reasons.add("native_packet_read_unhealthy_or_unguarded")
            if (not _integer(before.get("demo_player_address"), 1)
                    or not _same(before.get("demo_player_address"), after.get("demo_player_address"))
                    or not _same(before.get("player_state"), after.get("player_state"))):
                reasons.add("packet_pair_identity_disagrees")
            if not _same(before.get("demo_player_address"), demo_address):
                reasons.add("demo_player_instance_changed")
            if not _state_valid(after.get("player_state_after")):
                reasons.add("packet_return_state_unreadable")
            max_index = self.max_indices[-1]
            latest_match = self.latest_matches[-1]
            returned = after.get("returned_packet")
            if type(returned) is not bool or after.get("payload_readable") is not True:
                reasons.add("returned_packet_payload_unreadable")
            if returned is True:
                packet = after.get("packet")
                if (not _subset(packet, {"read_invocation": number, "returned_packet_index": state["returned_packets"],
                                        "payload_readable": True})
                        or not _integer(packet.get("source_demo_tick"), -1)
                        or not _integer(packet.get("byte_count"))
                        or not isinstance(packet.get("payload_sha256"), str)
                        or not _same(packet.get("source_demo_tick"), after.get("player_state_after", {}).get("raw_selected_tick_0x22c"))):
                    reasons.add("returned_packet_identity_invalid")
                    packet = packet if isinstance(packet, dict) else {}
                key = (packet.get("payload_sha256"), packet.get("byte_count"), packet.get("source_demo_tick"))
                matches = self.source_index.get(key, [])
                mode = "exact"
                if (not matches and _same(before.get("player_state", {}).get("packet_filter_flag_0x18a1"), 1)
                        and _same(after.get("player_state_after", {}).get("packet_filter_flag_0x18a1"), 1)):
                    matches = self.filtered_index.get(key, [])
                    mode = "native_seek_filtered"
                if len(matches) != 1:
                    reasons.add("prior_returned_packet_source_unmatched" if not matches else "prior_returned_packet_source_ambiguous")
                    self.matches["unmatched" if not matches else "ambiguous"] += 1
                    self.unmatched.append({"read_invocation": number, "packet": packet})
                    latest_match = None
                else:
                    latest_match = matches[0]
                    self.matches[mode] += 1
                    max_index = max(max_index, latest_match["source_command_index"])
                    if state["returned_packets"] == 0 and latest_match["source_command_index"] != min(self.source_packets):
                        reasons.add("initial_source_packet_consumption_uncovered")
                state["returned_packets"] += 1
                state["latest_returned_packet"] = packet
                tick = packet.get("source_demo_tick")
                if _integer(tick):
                    state["process_lifetime_max_returned_source_demo_tick"] = max(
                        state["process_lifetime_max_returned_source_demo_tick"] or 0, tick)
            elif returned is False:
                if after.get("packet") is not None:
                    reasons.add("null_return_claims_packet")
                state["null_returns"] += 1
            state.update(read_invocations=number, last_completed_read_invocation=number)
            if not _subset(after.get("packet_trace"), state):
                reasons.add("packet_return_counters_disagree")
            self.states.append(state)
            self.prefix_reasons.append(reasons)
            self.max_indices.append(max_index)
            self.latest_matches.append(latest_match)
            scheduling = [s.get("raw_selected_tick_0x22c") for s in
                          (before.get("player_state", {}), after.get("player_state_after", {}))]
            self.max_scheduling_ticks.append(max([self.max_scheduling_ticks[-1]] + [t for t in scheduling if _integer(t, -1)]))
            self.return_rows.append(after)

    def snapshot(self, clock):
        reasons = set(self.global_reasons)
        trace = clock.get("packet_trace") if isinstance(clock, dict) else None
        number = trace.get("last_completed_read_invocation") if isinstance(trace, dict) else None
        if not _integer(number) or number >= len(self.states):
            return None, reasons | {"packet_snapshot_reference_unavailable"}
        reasons.update(self.prefix_reasons[number])
        if not _subset(trace, self.states[number]):
            reasons.add("packet_snapshot_counters_disagree")
        if not _subset(trace, {"schema_version": 1, "healthy": True, "status": "observed_returned_packet_bytes"}):
            reasons.add("packet_snapshot_unhealthy_or_in_flight")
        if not _subset(clock, {"healthy": True, "status": "observed_message_clock", "handlers_in_flight": 0}):
            reasons.add("native_message_clock_unavailable")
        return number, reasons

    def observation(self, movie, pixels=None):
        reasons = set(self.global_reasons)
        clocks = [movie.get("native_clock")]
        if movie.get("event") == "movie_frame":
            clocks.append(movie.get("native_clock_after"))
            if len(pixels or []) != 1:
                reasons.add("missing_or_duplicate_pixel_readback")
            else:
                pixel = pixels[0]
                if not _subset(pixel, {"success": True, "clock_on_engine_thread": True, "thread_id": self.thread}):
                    reasons.add("pixel_readback_failed_or_off_thread")
                candidate = pixel.get("submission_candidate", {})
                if (not _same(candidate.get("movie_name"), movie.get("movie_name"))
                        or not _same(candidate.get("capture_index"), movie.get("capture_index"))
                        or not _same(candidate.get("native_clock"), movie.get("native_clock"))):
                    reasons.add("pixel_submission_reference_disagrees")
                clocks.extend((pixel.get("native_clock"), pixel.get("native_clock_after")))
        if not _same(movie.get("thread_id"), self.thread):
            reasons.add("movie_observation_off_engine_thread")
        numbers = []
        for clock in clocks:
            number, errors = self.snapshot(clock)
            reasons.update(errors)
            if number is not None:
                numbers.append(number)
        if len(numbers) != len(clocks):
            return self.result(movie, reasons)
        # Row writes occur after observation. Reconstruct via explicit completed
        # invocation links; never use the row's physical position in the file.
        # Movie submission and pixel readback may be nested or deferred. Each
        # native before/after pair has a proved order; their writes do not prove
        # a total order between callbacks. Include every observed prefix.
        if (len(numbers) >= 2 and numbers[0] > numbers[1]) or (len(numbers) == 4 and (
                numbers[2] > numbers[3] or numbers[2] < numbers[0])):
            reasons.add("packet_observation_references_regressed")
        end = max(numbers)
        maximum = self.max_indices[end]
        selected = self.latest_matches[end]
        upper_tick = max(self.max_scheduling_ticks[end], self.states[end]["process_lifetime_max_returned_source_demo_tick"] or -1)
        prefix_index = bisect_right(self.command_ticks, upper_tick)-1
        maximum = max(maximum, prefix_index)
        if upper_tick > self.source["through_demo_tick"] or maximum < 0:
            reasons.add("source_prefix_does_not_cover_observed_history")
        # Restrict the active interval to a suffix containing ordinary packets.
        # Earlier bootstrap/checkpoint data is retained in the prefix ceiling.
        if selected is None or selected["demo_command_kind"] != 7:
            reasons.add("capture_latest_packet_is_not_ordinary_source_packet")
        if selected is not None:
            start = selected["source_command_index"]
            if self.nonordinary[min(maximum+2, len(self.commands))] > self.nonordinary[start+1]:
                reasons.add("nonordinary_demo_command_at_capture_boundary")
        if maximum >= 0:
            reasons.update(self.bad_prior[maximum])
        for number in numbers:
            state = self.return_rows[number].get("player_state_after", {}) if number else {}
            if not _subset(state, {"seek_target_0x208": -1, "pending_seek_0x18c0": -1, "alternate_gate_flag_0x18a2": 0}):
                reasons.add("capture_reader_not_in_ordinary_playback")
        upper_server = self.prefix_clocks[maximum] if 0 <= maximum < len(self.prefix_clocks) else None
        for clock in clocks:
            for key in ("server_tick", "max_delivered_net_tick", "max_observed_entity_tick", "max_observed_user_command_execution_tick"):
                value = clock.get(key)
                if value is not None and not _integer(value):
                    reasons.add("invalid_observed_server_clock")
                elif value is not None:
                    upper_server = value if upper_server is None else max(upper_server, value)
        if upper_server is None:
            reasons.add("source_server_clock_ceiling_unavailable")
        return self.result(movie, reasons, upper_source_demo_tick=upper_tick, upper_server_tick=upper_server,
                           read_invocation_start=min(numbers), read_invocation_end=end,
                           latest_source_command_index=selected["source_command_index"] if selected else None,
                           process_lifetime_max_source_command_index=maximum)

    @staticmethod
    def result(movie, reasons, **values):
        return {"capture_index": movie.get("capture_index", movie.get("next_capture_index")),
                "movie_name": movie.get("movie_name"), "status": "unknown" if reasons else "verified",
                "verified": not reasons, "reasons": sorted(reasons),
                "upper_source_demo_tick": None, "upper_server_tick": None, **values}


def audit_packet_bounds(records, source, *, native_profile=LEGACY_PROFILE):
    if not isinstance(records, SessionLedger):
        return _audit_packet_bounds(records, source, native_profile=native_profile)
    source_key = hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return audit_cached("packets", records, (source_key, native_profile),
                        lambda: _audit_packet_bounds(records, source, native_profile=native_profile))


def _audit_packet_bounds(records: list[dict[str, Any]], source: dict[str, Any], *,
                        native_profile=LEGACY_PROFILE) -> dict[str, Any]:
    """Recompute packet prefix bounds; the caller must freshly scan the .dem.

    Physical ordering of movie/readback writes is irrelevant. Paired native read
    IDs, their counters, and each sampled completed-ID reference establish order.
    An unknown row's numeric ceiling is diagnostic only and must not be accepted.
    """
    profile = get_native_replay_profile(native_profile)
    if not isinstance(records, SessionLedger) and (not isinstance(records, list) or len(records) > 1000000 or any(not isinstance(r, dict) for r in records)):
        raise ValueError("Packet audit exceeds bounded record count")
    movies = list(events(records, ("movie_frame",)))
    ends = endpoints(records)
    try:
        auditor = _Audit(records, source, profile)
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "malformed_packet_evidence"
        return {"schema_version": 1, "profile": profile["packet_bounds_profile"], "status": "unknown", "reasons": [reason],
                "frames": [_Audit.result(r, {reason}) for r in movies], "endpoint": None,
                "summary": {"verified_frames": 0, "unknown_frames": len(movies)}}
    pixels = defaultdict(list)
    for row in events(records, ("pixel_readback",)):
        if row.get("event") == "pixel_readback" and isinstance(row.get("submission_candidate"), dict):
            candidate = row["submission_candidate"]
            pixels[(candidate.get("movie_name"), candidate.get("capture_index"))].append(row)
    if (not movies or any(not _integer(r.get("capture_index")) for r in movies)
            or any(not _same(r.get("capture_index"), i) for i, r in enumerate(sorted(movies, key=lambda r: r["capture_index"])))
            or len({r.get("movie_name") for r in movies}) != 1):
        auditor.global_reasons.add("invalid_movie_frame_coverage")
    if (len(ends) != 1 or not _same(ends[0].get("next_capture_index"), len(movies))
            or not movies or not _same(ends[0].get("movie_name"), movies[0].get("movie_name"))):
        auditor.global_reasons.add("missing_or_invalid_movie_endpoint")
    frames = [auditor.observation(row, pixels[(row.get("movie_name"), row.get("capture_index"))]) for row in movies]
    frames.sort(key=lambda row: row["capture_index"] if _integer(row["capture_index"]) else -1)
    endpoint = auditor.observation(ends[0]) if len(ends) == 1 else None
    reasons = sorted(set(auditor.global_reasons).union(*(set(r["reasons"]) for r in frames), set(endpoint["reasons"]) if endpoint else set()))
    verified = sum(row["verified"] for row in frames)
    return {"schema_version": 1, "profile": profile["packet_bounds_profile"], "status": "verified" if frames and not reasons else "unknown",
            "reasons": reasons, "source_demo_sha256": source.get("source_demo_sha256"),
            "frames": frames, "endpoint": endpoint,
            "summary": {"verified_frames": verified, "unknown_frames": len(frames) - verified,
                        "read_invocations": len(auditor.states) - 1, **dict(auditor.matches)},
            "unmatched_packets": auditor.unmatched,
            "scope": "ordinary_source_packet_prefix_ceiling_with_process_lifetime_history",
            "required_companion_checks": ["fresh_original_demo_scan", "native_message_audit", "pixel_identity", "observed_pov", "whole_command_support"]}
