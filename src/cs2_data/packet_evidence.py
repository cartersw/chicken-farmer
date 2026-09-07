"""Read exact Source 2 packet bytes from an immutable local .dem.

Unlike a self-declared sidecar hash, this reader derives payloads directly from
the source file. Only framing, Snappy blocks, protobuf fields and message clocks
are decoded here; canonical command reconstruction stays with demoinfocs.
"""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

from .clock_evidence import protobuf_fields, scalar
from .io import sha256_file

MAX_BLOCK = 4 * 1024 * 1024
NATIVE_SEEK_FILTER_POLICY = "cs2-14178-seek-message-filter-v1"
NATIVE_SEEK_FILTER_ENGINE_SHA256 = "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac"
NATIVE_SEEK_FILTER_CLIENT_SHA256 = "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4"
# engine2.dll 0x29BD0: switch table 0x2A040, drop branch 0x29DB4.
_ENGINE_SEEK_DROP = frozenset({8, 9, 11, 12, 13, 16, 17, 18, 47, 48, 49, 50, 53, 58})
# Other IDs call Source2Client002 slot71 -> GameConfigClientV001 slot30 ->
# CCSGameConfiguration 0xA715A0 -> 0x6CE310. Its table at0x6CE344 selects
# true/drop for these IDs; all other IDs return false/keep. This is a fixed
# installed-binary policy, not caller-supplied message editing instructions.
_CLIENT_SEEK_DROP = frozenset({101, 104, 105, 106, 107, 110, 111, 113, 114, 115,
    116, 117, 118, 119, 120, 121, 122, 124, 125, 128, 130, 131, 132, 134, 135,
    142, 143, 144, 145, 146, 148, 149, 150, 151, 153, 154, 155, 156, 157, 158,
    159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 200})


def uvarint(stream) -> int:
    value = 0
    for shift in range(0, 35, 7):
        data = stream.read(1)
        if len(data) != 1 or shift == 28 and data[0] > 15:
            raise ValueError("Truncated or oversized demo uint32 varint")
        value |= (data[0] & 127) << shift
        if not data[0] & 128:
            return value
    raise ValueError("Unterminated demo uint32 varint")


def snappy_block(data: bytes) -> bytes:
    """Bounded raw Snappy block decoder; copy overlaps repeat prior bytes."""
    stream = io.BytesIO(data)
    expected = uvarint(stream)
    if expected > MAX_BLOCK or len(data) > MAX_BLOCK:
        raise ValueError("Demo compression block exceeds bounded size")
    output = bytearray()

    def exact(length):
        value = stream.read(length)
        if len(value) != length:
            raise ValueError("Truncated Snappy block")
        return value

    while stream.tell() < len(data):
        tag = exact(1)[0]
        kind = tag & 3
        if kind == 0:
            size = tag >> 2
            length = size + 1 if size < 60 else int.from_bytes(exact(size - 59), "little") + 1
            if length > expected - len(output):
                raise ValueError("Snappy literal exceeds declared output")
            output.extend(exact(length))
        else:
            if kind == 1:
                length, offset = 4 + ((tag >> 2) & 7), ((tag & 224) << 3) | exact(1)[0]
            else:
                length, offset = 1 + (tag >> 2), int.from_bytes(exact(2 if kind == 2 else 4), "little")
            if not 0 < offset <= len(output) or length > expected - len(output):
                raise ValueError("Invalid Snappy copy offset or length")
            # A copy tag emits at most 64 bytes. Do not copy an arbitrarily
            # large history window merely because its backward offset is large.
            # Only short overlapping copies need to repeat their initial bytes.
            start = len(output) - offset
            pattern = bytes(output[start:start + min(offset, length)])
            output.extend((pattern * ((length + len(pattern) - 1) // len(pattern)))[:length])
    if len(output) != expected:
        raise ValueError("Snappy output length mismatch")
    return bytes(output)


def embedded(fields, number, *, optional=False):
    values = fields.get(number, [])
    if not values and optional:
        return b""
    if len(values) != 1 or values[0][0] != 2:
        raise ValueError("Packet requires one unambiguous embedded protobuf field")
    return values[0][1]


def packet_messages(data: bytes) -> list[dict]:
    """Source 2 UBitInt ID + varint byte length, in original wire order."""
    if len(data) > MAX_BLOCK:
        raise ValueError("Oversized native packet")
    position = 0

    def bits(count):
        nonlocal position
        if count < 0 or position + count > len(data) * 8:
            raise ValueError("Truncated packet bitstream")
        start, shift = divmod(position, 8)
        size = (shift + count + 7) // 8
        result = (int.from_bytes(data[start:start + size], "little") >> shift) & ((1 << count) - 1)
        position += count
        return result

    def integer():
        value = 0
        for shift in range(0, 35, 7):
            byte = bits(8)
            if shift == 28 and byte > 15:
                raise ValueError("Oversized packet length varint")
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Unterminated packet length")

    result = []
    while len(data) * 8 - position > 7:
        start = position
        message_id = bits(6)
        extra = {0: 0, 16: 4, 32: 8, 48: 28}[message_id & 48]
        if extra:
            message_id = (message_id & 15) | (bits(extra) << 4)
        size = integer()
        if size > MAX_BLOCK:
            raise ValueError("Oversized protobuf in packet")
        payload = bits(size * 8).to_bytes(size, "little")
        result.append({"wire_index": len(result), "message_id": message_id,
                       "start_bit": start, "end_bit": position, "protobuf": payload,
                       "protobuf_sha256": hashlib.sha256(payload).hexdigest()})
        if len(result) > 20000:
            raise ValueError("Too many messages in one packet")
    # Source readers allow up to seven padding bits; preserve their value.
    return result


def packet_clocks(data: bytes) -> dict:
    messages = packet_messages(data)
    ticks, snapshots, commands = [], [], []
    for message in messages:
        kind = message["message_id"]
        if kind not in (4, 55, 76):
            continue
        fields = protobuf_fields(message["protobuf"])
        if kind == 4:
            ticks.append({"wire_index": message["wire_index"], "network_tick": scalar(fields, 1)})
        elif kind == 55:
            snapshots.append({"wire_index": message["wire_index"], "server_tick": scalar(fields, 12)})
        else:
            for index, (wire, payload) in enumerate(fields.get(1, [])):
                if wire != 2:
                    raise ValueError("Invalid user-command envelope wire type")
                envelope = protobuf_fields(payload)
                commands.append({"wire_index": message["wire_index"], "envelope_index": index,
                                 "protobuf_sha256": hashlib.sha256(payload).hexdigest(),
                                 **{name: scalar(envelope, field, signed=True) for name, field in
                                    (("command_number", 2), ("player_slot", 3), ("server_tick_executed", 4), ("client_tick", 5))}})
    return {"message_ids": [m["message_id"] for m in messages], "network_ticks": ticks,
            "snapshot_ticks": snapshots, "command_envelopes": commands}


def derive_native_seek_filter(data: bytes) -> bytes:
    """Reproduce the pinned native seek filter by copying original wire spans.

    Kept message IDs, length varints, and protobuf bytes remain bit-exact and in
    original order. Native code copies those whole spans into zero-initialized
    storage, rounds the used bit count up to bytes, and does not copy source
    padding. This transformation can remove data; it cannot insert future data.
    Applying it is not itself evidence that a native capture used this policy.
    """
    output = bytearray()
    output_bits = 0
    for message in packet_messages(data):
        if message["message_id"] in _ENGINE_SEEK_DROP | _CLIENT_SEEK_DROP:
            continue
        start, end = message["start_bit"], message["end_bit"]
        length = end - start
        chunk = int.from_bytes(data[start // 8:(end + 7) // 8], "little")
        chunk = (chunk >> (start % 8)) & ((1 << length) - 1)
        shift = output_bits % 8
        encoded = (chunk << shift).to_bytes((length + shift + 7) // 8, "little")
        if shift:
            output[-1] |= encoded[0]
            output.extend(encoded[1:])
        else:
            output.extend(encoded)
        output_bits += length
    return bytes(output)


def _file_header(payload: bytes, source_row: dict) -> dict:
    fields = protobuf_fields(payload)
    try:
        stamp = embedded(fields, 1).decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Invalid demo file header stamp") from exc
    # The supplied Valve header retains the eight-byte C stamp including NUL.
    # Keep the exact protobuf string rather than stripping source bytes.
    if stamp not in ("PBDEMS2", "PBDEMS2\x00"):
        raise ValueError("Unsupported demo file header stamp")
    return {**source_row, "demo_file_stamp": stamp,
            "protobuf_base64": base64.b64encode(payload).decode("ascii"),
            "protobuf_sha256": hashlib.sha256(payload).hexdigest(),
            **{name: scalar(fields, number, signed=True) for name, number in
               (("patch_version", 2), ("build_num", 13), ("server_start_tick", 15))}}


def scan_demo_packets(path: Path, *, expected_sha256: str, through_demo_tick: int) -> dict:
    """Scan a complete source prefix, hashing exact CDemoPacket.data bytes.

    All packet types, including seek checkpoints, retain their source type and
    file offset. This scan does not replay checkpoints as live input commands.
    The first later header establishes coverage without decoding later payloads.
    """
    if type(through_demo_tick) is not int or not 0 <= through_demo_tick <= 20000:
        raise ValueError("Packet audit currently supports source prefixes through tick 20000")
    if sha256_file(path) != expected_sha256:
        raise ValueError("Packet audit source demo hash mismatch")
    packets, commands = [], []
    file_header = None
    prior_tick = -1
    with path.open("rb") as stream:
        if stream.read(8) != b"PBDEMS2\x00" or len(stream.read(8)) != 8:
            raise ValueError("Packet audit requires a standard Source 2 .dem")
        for command_index in range(100000):
            offset = stream.tell()
            raw_kind, unsigned_tick = uvarint(stream), uvarint(stream)
            kind, compressed = raw_kind & ~64, bool(raw_kind & 64)
            # The frame header is an int32 carried as a uint32 varint. Treating
            # another negative value as a large future tick could falsely prove
            # prefix coverage before reaching the requested positive tick.
            tick = unsigned_tick if unsigned_tick < 2**31 else unsigned_tick - 2**32
            if not 0 <= kind <= 18 or tick < prior_tick:
                raise ValueError("Unknown command or source prefix tick reversal")
            if tick > through_demo_tick:
                break
            if kind == 0:
                raise ValueError("Demo stopped before complete requested prefix")
            size = uvarint(stream)
            if size > MAX_BLOCK:
                raise ValueError("Oversized demo command payload")
            payload_offset = stream.tell()
            raw = stream.read(size)
            if len(raw) != size:
                raise ValueError("Truncated demo command payload")
            row = {"source_command_index": command_index, "demo_tick": tick, "demo_command_kind": kind,
                   "command_offset": offset, "payload_offset": payload_offset, "payload_size": size,
                   "compressed": compressed, "source_payload_sha256": hashlib.sha256(raw).hexdigest()}
            commands.append(row)
            if kind == 1:
                if file_header is not None or command_index != 0:
                    raise ValueError("Duplicate or misplaced demo file header")
                file_header = _file_header(snappy_block(raw) if compressed else raw, row)
            if kind in (7, 8, 13):
                decoded = snappy_block(raw) if compressed else raw
                fields = protobuf_fields(decoded)
                if kind == 13:
                    fields = protobuf_fields(embedded(fields, 2))
                data = embedded(fields, 3, optional=True)
                filtered = derive_native_seek_filter(data)
                packets.append({**row, "packet_data_size": len(data),
                                "packet_data_sha256": hashlib.sha256(data).hexdigest(),
                                "native_seek_filter_policy": NATIVE_SEEK_FILTER_POLICY,
                                "native_seek_filtered_data_size": len(filtered),
                                "native_seek_filtered_data_sha256": hashlib.sha256(filtered).hexdigest(),
                                **packet_clocks(data)})
            prior_tick = tick
        else:
            raise ValueError("Source prefix exceeds bounded command count")
    if sha256_file(path) != expected_sha256:
        raise ValueError("Source demo changed during packet audit")
    return {"schema_version": 2, "source_demo_sha256": expected_sha256, "source_demo_path": str(path.resolve()),
            "through_demo_tick": through_demo_tick, "coverage_next_header_tick": tick,
            "file_header": file_header,
            "native_seek_filter_profile": {"policy_id": NATIVE_SEEK_FILTER_POLICY,
                "engine_sha256": NATIVE_SEEK_FILTER_ENGINE_SHA256,
                "client_sha256": NATIVE_SEEK_FILTER_CLIENT_SHA256,
                "transformation": "ordered_original_message_bit_spans_then_zero_padding"},
            "packets": packets, "demo_commands": commands, "training_ready": False}
