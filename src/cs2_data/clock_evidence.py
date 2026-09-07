"""Decode clock evidence from its retained protobufs, without certifying causality.

NET_Tick, PacketEntities.server_tick, and command-envelope clocks remain distinct.
Their equality is an observed source fact, not a rendered-image phase contract.
"""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .io import sha256_file

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024 * 1024
MAX_RETAINED_BYTES = 128 * 1024 * 1024
# Pinned demoinfocs v6.0.0-alpha.0 ParserWarn values: BombsiteUnknown and
# UnknownGrenadeModel. Keep the clock producer's policy independent of validation.
ALLOWED_CLOCK_WARNING_TYPES = frozenset({"1", "13"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CLOCK_FIELDS = {
    "CNETMsg_Tick": {"network_tick": (1, False)},
    "CSVCMsg_PacketEntities": {"snapshot_server_tick": (12, False)},
    "CMsgServerUserCmd": {"command_number": (2, True), "player_slot": (3, True),
                          "server_tick_executed": (4, True), "client_tick": (5, True)},
}
_RECORD_FIELDS = {"clock_event_index", "demo_tick", "demo_frame", "message_type",
                  "protobuf_reencoded", "protobuf_sha256"}


def protobuf_fields(payload: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Bounded wire decoder for inspection; retain repeats and reject truncation."""
    if not isinstance(payload, bytes) or len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Invalid/oversized protobuf clock evidence")
    offset = 0

    def varint() -> int:
        nonlocal offset
        result = 0
        for shift in range(0, 70, 7):
            if offset >= len(payload):
                raise ValueError("Truncated protobuf varint")
            byte = payload[offset]
            offset += 1
            if shift == 63 and byte > 1:
                raise ValueError("Protobuf varint exceeds uint64")
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError("Unterminated protobuf varint")

    result = defaultdict(list)
    count = 0
    while offset < len(payload):
        count += 1
        if count > 100000:
            raise ValueError("Too many protobuf fields")
        tag = varint()
        field, wire = tag >> 3, tag & 7
        if not 0 < field < 2**29:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else 8 if wire == 1 else 4
            if length > len(payload) - offset:
                raise ValueError("Truncated protobuf field")
            value = payload[offset:offset + length]
            offset += length
        else:
            raise ValueError("Unsupported protobuf wire type in clock evidence")
        result[field].append((wire, value))
    return dict(result)


def scalar(fields: dict[int, list[tuple[int, Any]]], field: int, *, signed: bool = False) -> int | None:
    values = fields.get(field, [])
    if not values:
        return None
    if len(values) != 1 or values[0][0] != 0:
        raise ValueError("Clock evidence requires one unambiguous varint field")
    result = values[0][1]
    if signed:
        # int32 negatives are sign-extended uint64 varints, not arbitrary values
        # whose low 32 bits can be truncated into a plausible clock.
        if result < 2**31:
            return result
        if 2**64 - 2**31 <= result < 2**64:
            return result - 2**64
        raise ValueError("Clock field exceeds canonical int32 encoding")
    if result >= 2**32:
        raise ValueError("Clock field exceeds uint32")
    return result


def decode_clock_record(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Clock evidence record must be an object")
    kind = row.get("message_type")
    if not isinstance(kind, str) or kind not in _CLOCK_FIELDS:
        raise ValueError("Unsupported clock message type")
    if set(row) - (_RECORD_FIELDS | _CLOCK_FIELDS[kind].keys()):
        raise ValueError("Clock evidence record contains unsupported fields")
    encoded = row.get("protobuf_reencoded")
    # Go's []byte may serialize an empty protobuf as null. An empty message
    # supplies no measured clock; its scalar fields remain None below.
    if "protobuf_reencoded" not in row or encoded is not None and not isinstance(encoded, str):
        raise ValueError("Invalid encoded protobuf clock evidence")
    if encoded is not None and len(encoded) > 4 * ((MAX_MESSAGE_BYTES + 2) // 3):
        raise ValueError("Oversized encoded protobuf clock evidence")
    digest = row.get("protobuf_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("Invalid clock protobuf hash")
    try:
        raw = base64.b64decode(encoded or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid encoded protobuf clock evidence") from exc
    if encoded is not None and base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("Noncanonical base64 clock evidence")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Clock protobuf hash mismatch")
    fields = protobuf_fields(raw)
    decoded = {name: scalar(fields, index, signed=signed)
               for name, (index, signed) in _CLOCK_FIELDS[kind].items()}
    for name, value in decoded.items():
        reported = row.get(name)
        if reported != value or reported is not None and type(reported) is not int:
            raise ValueError(f"Clock field {name} disagrees with retained protobuf")
    return {**row, **decoded}


@dataclass
class NetworkClockEvidence:
    path: Path
    sha256: str
    document: dict[str, Any]
    records: list[dict[str, Any]]
    source_demo_verified: bool = False

    def by_demo_tick(self, kind: str) -> dict[int, list[dict[str, Any]]]:
        result = defaultdict(list)
        for row in self.records:
            if row["message_type"] == kind:
                result[row["demo_tick"]].append(row)
        return dict(result)


def load_network_clock(path: Path, demo_id: str, *, verify_demo: bool = True) -> NetworkClockEvidence:
    """Verify report consistency and optionally its source file's identity.

    Retained protobufs are decoded again; advertised clock fields are not trusted.
    A matching demo hash binds the named source, but does not authenticate the
    producer or reparse the demo to prove each retained message's original offset.
    This loader never certifies execution, pixel phase, or action support.
    """
    if not isinstance(demo_id, str) or not _SHA256.fullmatch(demo_id) or type(verify_demo) is not bool:
        raise ValueError("Invalid clock evidence source identity or verification option")
    path = Path(path)
    with path.open("rb") as stream:
        encoded_report = stream.read(MAX_REPORT_BYTES + 1)
    if len(encoded_report) > MAX_REPORT_BYTES:
        raise ValueError("Oversized clock evidence report")
    digest = hashlib.sha256(encoded_report).hexdigest()

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key in clock evidence")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise ValueError(f"Nonfinite JSON value in clock evidence: {value}")

    try:
        report = json.loads(encoded_report.decode("utf-8-sig"), object_pairs_hook=unique_object,
                            parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid clock evidence JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("Clock evidence report must be an object")
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("producer") != "cs2-clocks-v1"
            or report.get("status") != "complete" or report.get("scope") != "complete_requested_windows_only"
            or report.get("demo_id") != demo_id or report.get("source_demo_sha256") != demo_id
            or report.get("parser") != "demoinfocs-golang/v6" or report.get("parser_version") != "v6.0.0-alpha.0"
            or report.get("warning_policy") != "network-clock-envelope-v1"
            or type(report.get("evidence_loss_warnings")) is not int or report["evidence_loss_warnings"] != 0
            or report.get("protobuf_encoding") != "deterministic_reserialization_of_decoded_messages_not_original_wire_offsets"
            or report.get("demo_tick_source") != "demo_command_header_via_parser_ingame_tick"
            or report.get("event_order") != "parser_dispatch_order_for_tick_and_command_envelopes"
            or report.get("training_ready") is not False or report.get("execution_render_epoch_verified") is not False):
        raise ValueError("Unsupported, incomplete or incorrectly scoped network clock evidence")
    source = report.get("source_demo_path")
    if not isinstance(source, str) or not source or not Path(source).is_absolute():
        raise ValueError("Clock evidence requires an absolute source demo path")
    rate = report.get("tick_rate")
    try:
        valid_rate = type(rate) in (int, float) and math.isfinite(rate) and rate > 0
    except OverflowError:
        valid_rate = False
    if not valid_rate:
        raise ValueError("Network clock evidence requires a finite positive tick rate")
    # The producer rejects evidence-loss warnings; consumers also check the list.
    # These are the pinned parser's bombsite/grenade-model warning enum values.
    warnings = report.get("warnings")
    if not isinstance(warnings, dict) or any(key not in ALLOWED_CLOCK_WARNING_TYPES or type(value) is not int or value <= 0
                                           for key, value in warnings.items()):
        raise ValueError("Clock report contains unaudited parser warnings")
    windows = report.get("windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= 32:
        raise ValueError("Clock evidence requires bounded windows")
    previous_end = -1
    total = 0
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"start_demo_tick", "end_demo_tick"}:
            raise ValueError("Invalid clock window schema")
        start, end = window.get("start_demo_tick"), window.get("end_demo_tick")
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start
                or end > 10000000 or start < previous_end):
            raise ValueError("Clock windows overlap or have invalid boundaries")
        total += end - start
        previous_end = end
    if total > 20000 or type(report.get("parsed_through_demo_tick")) is not int or report["parsed_through_demo_tick"] < previous_end:
        raise ValueError("Clock extraction did not cover its bounded windows")
    rows = report.get("records")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200000:
        raise ValueError("Invalid clock evidence record count")
    decoded = []
    previous_index, previous_tick, previous_frame = -1, -1, -1
    previous_window = None
    retained_bytes = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Clock evidence record must be an object")
        index, tick, frame = (row.get(name) for name in ("clock_event_index", "demo_tick", "demo_frame"))
        if (any(type(value) is not int for value in (index, tick, frame)) or index < 1 or frame < 0
                or index <= previous_index or tick < previous_tick or frame < previous_frame):
            raise ValueError("Clock evidence order or window coverage is inconsistent")
        window_index = next((i for i, window in enumerate(windows)
                             if window["start_demo_tick"] <= tick < window["end_demo_tick"]), None)
        if window_index is None:
            raise ValueError("Clock evidence record lies outside requested windows")
        # The producer increments before filtering by window. Gaps between
        # separated windows are expected; missing rows inside coverage are not.
        continuous = previous_window is not None and (window_index == previous_window
            or all(windows[i-1]["end_demo_tick"] == windows[i]["start_demo_tick"]
                   for i in range(previous_window+1, window_index+1)))
        if continuous and index != previous_index + 1:
            raise ValueError("Clock evidence omits an event within a requested window")
        decoded.append(decode_clock_record(row))
        # Encoded length is already bounded and validated by decode_clock_record.
        encoded = row["protobuf_reencoded"] or ""
        retained_bytes += len(encoded) // 4 * 3 - (len(encoded) - len(encoded.rstrip("=")))
        if retained_bytes > MAX_RETAINED_BYTES:
            raise ValueError("Clock evidence exceeds retained protobuf byte budget")
        previous_index, previous_tick, previous_frame = index, tick, frame
        previous_window = window_index
    counts = Counter(row["message_type"] for row in decoded)
    for field, kind in (("network_tick_records", "CNETMsg_Tick"), ("snapshot_records", "CSVCMsg_PacketEntities"),
                        ("command_envelope_records", "CMsgServerUserCmd")):
        if (type(report.get(field)) is not int or report[field] != counts[kind]
                or kind != "CSVCMsg_PacketEntities" and counts[kind] == 0):
            raise ValueError("Clock evidence count mismatch or missing stream")
    if verify_demo and sha256_file(Path(source)) != demo_id:
        raise ValueError("Network clock evidence source demo changed")
    if sha256_file(path) != digest:
        raise ValueError("Network clock evidence report changed while loading")
    return NetworkClockEvidence(path.resolve(), digest, report, decoded, source_demo_verified=verify_demo)


def command_envelope_matches(commands: list[dict[str, Any]], evidence: NetworkClockEvidence) -> list[dict[str, Any]]:
    """Exact recorded clock joins; these rows do not establish action support."""
    by_key = defaultdict(list)
    for row in evidence.records:
        if row["message_type"] == "CMsgServerUserCmd":
            by_key[(row["demo_tick"], row.get("player_slot"), row.get("command_number"))].append(row)
    result = []
    seen_ids = set()
    for command in commands:
        if not isinstance(command, dict) or command.get("demo_id") != evidence.document["demo_id"]:
            raise ValueError("Canonical command source disagrees with network clock evidence")
        row_id = command.get("command_row_id")
        if type(row_id) is not int or row_id < 0 or row_id in seen_ids:
            raise ValueError("Canonical command row identity is invalid or duplicated")
        seen_ids.add(row_id)
        keys = ("demo_tick", "player_slot", "command_number", "server_tick_executed", "client_tick")
        if any(command.get(name) is not None and type(command[name]) is not int for name in keys):
            raise ValueError("Canonical command clock fields must be integers or unavailable")
        key = tuple(command.get(name) for name in keys[:3])
        rows = by_key.get(key, [])
        exact = (len(rows) == 1 and all(command.get(name) is not None and 0 <= command[name] < 2**31
                                      for name in keys)
                 and all(command[name] == rows[0].get(name) for name in ("server_tick_executed", "client_tick")))
        result.append({"command_row_id": command["command_row_id"], "status": "matched" if exact else "unavailable_or_ambiguous",
                       "clock_event_index": rows[0]["clock_event_index"] if exact else None})
    return result
