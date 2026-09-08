"""Read exact Source 2 packet bytes from an immutable local .dem.

Unlike a self-declared sidecar hash, this reader derives payloads directly from
the source file. Only framing, Snappy blocks, protobuf fields and message clocks
are decoded here; canonical command reconstruction stays with demoinfocs.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import io
import json
from pathlib import Path
import threading
import zlib

from .clock_evidence import protobuf_fields, scalar
from .io import sha256_file
from .native_replay_profile import LEGACY_PROFILE, get_native_replay_profile

MAX_BLOCK = 4 * 1024 * 1024
MAX_COMMANDS_LEGACY = 100000
MAX_COMMANDS_CURRENT = 1_000_000
MAX_CACHED_PREFIXES = 4
MAX_CACHED_PREFIX_BYTES = 384 * 1024 * 1024
MAX_SERIALIZED_PREFIX_BYTES = 1024 * 1024 * 1024
_PREFIX_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_STATS = {"hits": 0, "extensions": 0, "cold_scans": 0, "evictions": 0,
                "oversized_skips": 0, "decoded_commands": 0}
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
    return _packet_clocks(packet_messages(data))


def _packet_clocks(messages) -> dict:
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


def derive_native_seek_filter(data: bytes, *, native_profile=LEGACY_PROFILE) -> bytes:
    """Reproduce the pinned native seek filter by copying original wire spans.

    Kept message IDs, length varints, and protobuf bytes remain bit-exact and in
    original order. Native code copies those whole spans into zero-initialized
    storage, rounds the used bit count up to bytes, and does not copy source
    padding. This transformation can remove data; it cannot insert future data.
    Applying it is not itself evidence that a native capture used this policy.
    """
    get_native_replay_profile(native_profile)
    return _native_seek_filter(data, packet_messages(data))


def _native_seek_filter(data, messages):
    output = bytearray()
    output_bits = 0
    for message in messages:
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


def clear_packet_scan_cache():
    """Discard process-local acceleration state; no on-disk proof is loaded."""
    with _CACHE_LOCK:
        _PREFIX_CACHE.clear()
        for key in _CACHE_STATS:
            _CACHE_STATS[key] = 0


def packet_scan_cache_info():
    """Diagnostics only, deliberately excluded from deterministic proof data."""
    with _CACHE_LOCK:
        return {**_CACHE_STATS, "entries": len(_PREFIX_CACHE),
                "retained_bytes": sum(len(value[0]) for value in _PREFIX_CACHE.values()),
                "max_entries": MAX_CACHED_PREFIXES, "max_bytes": MAX_CACHED_PREFIX_BYTES,
                "codec": "zlib-json-v1", "max_serialized_prefix_bytes": MAX_SERIALIZED_PREFIX_BYTES}


def _implementation_digests():
    return tuple((name, sha256_file(Path(__file__).with_name(name + ".py"))) for name in
                 ("packet_evidence", "clock_evidence", "native_replay_profile", "io"))


def _remember_prefix(key, result, next_header_offset):
    # Immutable serialized values prevent callers from poisoning a later proof.
    encoded = json.dumps(result, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if MAX_CACHED_PREFIXES < 1 or len(encoded) > MAX_SERIALIZED_PREFIX_BYTES:
        _CACHE_STATS["oversized_skips"] += 1
        return
    compressed = zlib.compress(encoded, level=1)
    if len(compressed) > MAX_CACHED_PREFIX_BYTES:
        _CACHE_STATS["oversized_skips"] += 1
        return
    _PREFIX_CACHE.pop(key, None)
    while _PREFIX_CACHE and (len(_PREFIX_CACHE) >= MAX_CACHED_PREFIXES or
            sum(len(value[0]) for value in _PREFIX_CACHE.values()) + len(compressed) > MAX_CACHED_PREFIX_BYTES):
        _PREFIX_CACHE.popitem(last=False)
        _CACHE_STATS["evictions"] += 1
    _PREFIX_CACHE[key] = (compressed, next_header_offset, len(encoded), hashlib.sha256(encoded).hexdigest())


def _restore_prefix(cached):
    """Decode only a bounded, complete, unchanged in-process serialization."""
    compressed, _, expected_size, expected_hash = cached
    if type(expected_size) is not int or not 0 <= expected_size <= MAX_SERIALIZED_PREFIX_BYTES:
        raise ValueError("Cached packet serialization exceeds its decoded byte bound")
    decoder = zlib.decompressobj()
    try:
        encoded = decoder.decompress(compressed, expected_size+1)
    except zlib.error as error:
        raise ValueError("Cached packet serialization is corrupt") from error
    if (len(encoded) != expected_size or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data
            or hashlib.sha256(encoded).hexdigest() != expected_hash):
        raise ValueError("Cached packet serialization is incomplete, oversized or changed")
    return json.loads(encoded)


def _trim_prefix(result, through):
    # The larger verified prefix contains the exact first later header, even
    # when multiple commands share a tick or there is a gap in source ticks.
    later = next((row for row in result["demo_commands"] if row["demo_tick"] > through), None)
    result["demo_commands"] = [row for row in result["demo_commands"] if row["demo_tick"] <= through]
    result["packets"] = [row for row in result["packets"] if row["demo_tick"] <= through]
    if later is not None:
        result["coverage_next_header_tick"] = later["demo_tick"]
    header = result.get("file_header")
    if header is not None and header["demo_tick"] > through:
        result["file_header"] = None
    result["through_demo_tick"] = through
    return result


def scan_demo_packets(path: Path, *, expected_sha256: str, through_demo_tick: int,
                      native_profile=LEGACY_PROFILE) -> dict:
    """Read a byte-verified prefix, reusing only unchanged in-process evidence.

    Every call hashes the entire original demo and implementation bytes before
    and after inspection. Earlier prefixes are trimmed at their exact first
    later header; extensions resume at that header with original source indexes.
    Cached data never comes from a saved report and never grants acceptance.
    """
    path = Path(path).resolve()
    profile = get_native_replay_profile(native_profile)
    tick_limit = 20000 if native_profile == LEGACY_PROFILE else 2_147_483_647
    command_limit = MAX_COMMANDS_LEGACY if native_profile == LEGACY_PROFILE else MAX_COMMANDS_CURRENT
    if type(through_demo_tick) is not int or not 0 <= through_demo_tick <= tick_limit:
        raise ValueError(f"Packet audit source prefix must end within ticks 0..{tick_limit}")
    with _CACHE_LOCK:
        if sha256_file(path) != expected_sha256:
            raise ValueError("Packet audit source demo hash mismatch")
        implementation = _implementation_digests()
        key = (str(path), expected_sha256, native_profile, tick_limit, command_limit, MAX_BLOCK,
               profile["seek_filter_policy"], profile["engine_sha256"], profile["client_sha256"],
               MAX_SERIALIZED_PREFIX_BYTES, implementation)
        cached = _PREFIX_CACHE.get(key)
        previous = _restore_prefix(cached) if cached is not None else None
        if previous is not None and through_demo_tick <= previous["through_demo_tick"]:
            result = _trim_prefix(previous, through_demo_tick)
            _CACHE_STATS["hits"] += 1
        else:
            _CACHE_STATS["extensions" if previous is not None else "cold_scans"] += 1
            result, next_offset = _scan_prefix(path, expected_sha256, through_demo_tick, native_profile,
                profile, command_limit, previous, cached[1] if cached is not None else None)
        if sha256_file(path) != expected_sha256:
            raise ValueError("Source demo changed during packet audit")
        if _implementation_digests() != implementation:
            raise ValueError("Packet audit implementation changed during verification")
        if previous is None or through_demo_tick > previous["through_demo_tick"]:
            _remember_prefix(key, result, next_offset)
        elif cached is not None:
            _PREFIX_CACHE.move_to_end(key)
        return result


def _scan_prefix(path, expected_sha256, through_demo_tick, native_profile, profile,
                 command_limit, previous=None, next_header_offset=None):
    packets = previous["packets"] if previous is not None else []
    commands = previous["demo_commands"] if previous is not None else []
    file_header = previous["file_header"] if previous is not None else None
    prior_tick = commands[-1]["demo_tick"] if commands else -1
    with path.open("rb") as stream:
        if stream.read(8) != b"PBDEMS2\x00" or len(stream.read(8)) != 8:
            raise ValueError("Packet audit requires a standard Source 2 .dem")
        if next_header_offset is not None:
            stream.seek(next_header_offset)
        for command_index in range(len(commands), command_limit):
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
            _CACHE_STATS["decoded_commands"] += 1
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
                messages = packet_messages(data)
                filtered = _native_seek_filter(data, messages)
                packets.append({**row, "packet_data_size": len(data),
                                "packet_data_sha256": hashlib.sha256(data).hexdigest(),
                                "native_seek_filter_policy": profile["seek_filter_policy"],
                                "native_seek_filtered_data_size": len(filtered),
                                "native_seek_filtered_data_sha256": hashlib.sha256(filtered).hexdigest(),
                                **_packet_clocks(messages)})
            prior_tick = tick
        else:
            raise ValueError("Source prefix exceeds bounded command count")
    return {"schema_version": 2, "source_demo_sha256": expected_sha256, "source_demo_path": str(path.resolve()),
            "through_demo_tick": through_demo_tick, "coverage_next_header_tick": tick,
            "file_header": file_header,
            **({"native_profile": native_profile} if native_profile != LEGACY_PROFILE else {}),
            "native_seek_filter_profile": {"policy_id": profile["seek_filter_policy"],
                "engine_sha256": profile["engine_sha256"],
                "client_sha256": profile["client_sha256"],
                "transformation": "ordered_original_message_bit_spans_then_zero_padding"},
            "packets": packets, "demo_commands": commands, "training_ready": False}, offset
