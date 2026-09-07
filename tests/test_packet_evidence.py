import hashlib
import io
import base64

import pytest

from cs2_data.packet_evidence import (derive_native_seek_filter, packet_clocks, packet_messages,
                                    scan_demo_packets, snappy_block, uvarint)


def vi(value):
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    return bytes(output + bytes([value]))


def packet(messages):
    output, bit = 0, 0

    def put(value, count):
        nonlocal output, bit
        output |= value << bit
        bit += count

    for kind, payload in messages:
        extra = 0 if kind < 16 else 4 if kind < 256 else 8 if kind < 4096 else 28
        put(kind if not extra else (kind & 15) | {4: 16, 8: 32, 28: 48}[extra], 6)
        if extra:
            put(kind >> 4, extra)
        length = vi(len(payload))
        for byte in length + payload:
            put(byte, 8)
    return output.to_bytes((bit + 7) // 8, "little")


def embedded(number, value):
    return vi(number * 8 + 2) + vi(len(value)) + value


def command(kind, tick, body=b""):
    return vi(kind) + vi(tick) + (b"" if kind == 0 else vi(len(body)) + body)


@pytest.mark.parametrize("value", [0, 1, 127, 128, 16384, 2**32 - 1])
def test_varint(value):
    assert uvarint(io.BytesIO(vi(value))) == value


@pytest.mark.parametrize("raw", [b"", b"\x80", b"\xff\xff\xff\xff\x10", b"\x80" * 6])
def test_bad_varint(raw):
    with pytest.raises(ValueError):
        uvarint(io.BytesIO(raw))


def test_snappy_overlapping_copy_and_literal():
    # "ab", copy from two bytes back for six bytes, then literal "x".
    assert snappy_block(b"\x09\x04ab\x16\x02\x00\x00x") == b"ababababx"
    assert snappy_block(b"\x05\x00z\x01\x01") == b"zzzzz"
    assert snappy_block(b"\x00") == b""


def test_snappy_extended_literal_and_four_byte_backward_copy():
    literal = bytes(range(252))
    # One-byte extended literal length (251 + 1), then an eight-byte COPY_4
    # whose history offset is much larger than the number of emitted bytes.
    raw = vi(260) + b"\xf0\xfb" + literal + b"\x1f\xfc\x00\x00\x00"
    assert snappy_block(raw) == literal + literal[:8]


@pytest.mark.parametrize("length_bytes", [1, 2, 3, 4])
def test_snappy_extended_literal_length_widths(length_bytes):
    literal = b"an explicitly encoded literal"
    tag = bytes([(59 + length_bytes) << 2])
    raw = vi(len(literal)) + tag + (len(literal) - 1).to_bytes(length_bytes, "little") + literal
    assert snappy_block(raw) == literal


@pytest.mark.parametrize("raw", [b"", b"\x01", b"\x01\x04ab", b"\x04\x01\x00", b"\x05\x00z\x01\x02",
                                  b"\x02\x04a", b"\x05\xff\x00", vi(4 * 1024 * 1024 + 1)])
def test_corrupt_snappy(raw):
    with pytest.raises(ValueError):
        snappy_block(raw)


def test_packet_wire_order_and_long_ids():
    wanted = [(76, b"raw command"), (4, b"\x08\x9f\x82\x01"), (55, b""), (500, b"test"), (1 << 20, b"x" * 200)]
    rows = packet_messages(packet(wanted))
    assert [(row["message_id"], row["protobuf"]) for row in rows] == wanted
    assert [row["wire_index"] for row in rows] == list(range(len(wanted)))
    assert all(row["protobuf_sha256"] == hashlib.sha256(row["protobuf"]).hexdigest() for row in rows)


def test_packet_truncation():
    with pytest.raises(ValueError):
        packet_messages(packet([(55, b"long payload")])[:-2])


def test_native_seek_filter_drops_only_profile_messages_and_preserves_order():
    # Native engine switch excludes47; game configuration excludes118.
    original = [(76, b"raw command"), (47, b"sound"), (4, b"clock"),
                (118, b"text"), (207, b"event"), (55, b"snapshot")]
    expected = [original[i] for i in (0, 2, 4, 5)]
    filtered = derive_native_seek_filter(packet(original))
    assert filtered == packet(expected)
    assert [(r["message_id"], r["protobuf"]) for r in packet_messages(filtered)] == expected


@pytest.mark.parametrize("message_id,dropped", [(8, True), (7, False), (58, True), (59, False),
    (101, True), (102, False), (168, True), (169, False), (199, False), (200, True), (201, False)])
def test_native_seek_filter_switch_boundaries(message_id, dropped):
    original = packet([(message_id, b"unmodified")])
    assert derive_native_seek_filter(original) == (b"" if dropped else original)


def test_native_seek_filter_copies_nonminimal_wire_length_and_zeros_padding():
    # UBitInt76 is ten bits. Preserve an original two-byte encoding of length0,
    # followed by six nonzero padding bits that the native writer zeroes.
    bits = (0x1C | (4 << 6)) | (0x80 << 10)
    original = (bits | (0x3F << 26)).to_bytes(4, "little")
    assert packet_messages(original)[0]["protobuf"] == b""
    assert derive_native_seek_filter(original) == bits.to_bytes(4, "little")
    assert derive_native_seek_filter(original) != packet([(76, b"")])


def test_independent_clocks_and_missing_fields():
    envelope = b"\x10\x01\x18\x09\x20\x64\x28\x60"
    data = packet([(76, embedded(1, envelope)), (4, b"\x08\x65"), (55, b"\x60\x66")])
    result = packet_clocks(data)
    assert result["message_ids"] == [76, 4, 55]
    assert result["network_ticks"][0]["network_tick"] == 101
    assert result["snapshot_ticks"][0]["server_tick"] == 102
    assert result["command_envelopes"][0]["server_tick_executed"] == 100
    assert packet_clocks(packet([(4, b"")]))["network_ticks"][0]["network_tick"] is None


def test_source_prefix_binds_real_bytes_and_distinguishes_checkpoints(tmp_path):
    data = packet([(4, b"\x08\x64")])
    pb = embedded(3, data)
    demo = b"PBDEMS2\x00" + b"\x00" * 8 + command(7, 10, pb) + command(13, 11, embedded(2, pb)) + command(7, 12, pb)
    path = tmp_path / "source.dem"
    path.write_bytes(demo)
    digest = hashlib.sha256(demo).hexdigest()
    result = scan_demo_packets(path, expected_sha256=digest, through_demo_tick=11)
    assert result["coverage_next_header_tick"] == 12
    assert [row["demo_command_kind"] for row in result["packets"]] == [7, 13]
    assert all(row["packet_data_sha256"] == hashlib.sha256(data).hexdigest() for row in result["packets"])
    assert result["training_ready"] is False
    path.write_bytes(demo + b"changed")
    with pytest.raises(ValueError, match="hash"):
        scan_demo_packets(path, expected_sha256=digest, through_demo_tick=11)


@pytest.mark.parametrize("suffix", [command(7, 10, b"bad") + command(7, 12), command(0, 10),
                                    command(7, 10) + command(7, 9), command(20, 10)])
def test_source_prefix_rejects_malformed_or_incomplete(tmp_path, suffix):
    path = tmp_path / "source.dem"
    data = b"PBDEMS2\x00" + b"\x00" * 8 + suffix
    path.write_bytes(data)
    with pytest.raises(ValueError):
        scan_demo_packets(path, expected_sha256=hashlib.sha256(data).hexdigest(), through_demo_tick=11)


@pytest.mark.parametrize("negative_tick", [0x80000000, 0xFFFFFFFE])
def test_negative_header_cannot_falsely_prove_complete_positive_prefix(tmp_path, negative_tick):
    data = b"PBDEMS2\x00" + b"\x00" * 8 + command(7, 10) + command(7, negative_tick)
    path = tmp_path / "negative-header.dem"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="tick reversal"):
        scan_demo_packets(path, expected_sha256=hashlib.sha256(data).hexdigest(), through_demo_tick=11)


def test_compressed_checkpoint_embeds_the_same_packet_bytes(tmp_path):
    data = packet([(4, b"\x08\x64")])
    checkpoint = embedded(2, embedded(3, data))
    compressed = vi(len(checkpoint)) + bytes([(len(checkpoint) - 1) << 2]) + checkpoint
    demo = b"PBDEMS2\x00" + b"\x00" * 8 + command(13 | 64, 10, compressed) + command(7, 12)
    path = tmp_path / "checkpoint.dem"
    path.write_bytes(demo)
    result = scan_demo_packets(path, expected_sha256=hashlib.sha256(demo).hexdigest(), through_demo_tick=11)
    row, = result["packets"]
    assert row["compressed"] is True
    assert row["demo_command_kind"] == 13
    assert row["packet_data_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["network_ticks"] == [{"wire_index": 0, "network_tick": 100}]


@pytest.mark.parametrize("stamp", [b"PBDEMS2", b"PBDEMS2\x00"])
def test_file_header_binds_original_protobuf_and_independent_version_fields(tmp_path, stamp):
    header = embedded(1, stamp) + b"\x10" + vi(14178) + b"\x68" + vi(10896) + b"\x78" + vi(10703)
    demo = (b"PBDEMS2\x00" + b"\x00" * 8 + command(1, 0xFFFFFFFF, header)
            + command(7, 10) + command(7, 12))
    path = tmp_path / "header.dem"
    path.write_bytes(demo)
    result = scan_demo_packets(path, expected_sha256=hashlib.sha256(demo).hexdigest(), through_demo_tick=11)
    parsed = result["file_header"]
    assert result["schema_version"] == 2
    assert parsed["demo_file_stamp"] == stamp.decode()
    assert (parsed["patch_version"], parsed["build_num"], parsed["server_start_tick"]) == (14178, 10896, 10703)
    assert parsed["source_command_index"] == 0 and parsed["demo_tick"] == -1
    assert base64.b64decode(parsed["protobuf_base64"]) == header
    assert parsed["protobuf_sha256"] == hashlib.sha256(header).hexdigest()
    assert result["packets"][0]["native_seek_filtered_data_sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("header", [embedded(1, b"PBDEMS2") + b"\x10\x01\x10\x02",
                                    embedded(1, b"different"), embedded(1, b"\xff")])
def test_file_header_rejects_ambiguous_or_invalid_evidence(tmp_path, header):
    demo = b"PBDEMS2\x00" + b"\x00" * 8 + command(1, 0xFFFFFFFF, header) + command(7, 12)
    path = tmp_path / "header.dem"
    path.write_bytes(demo)
    with pytest.raises(ValueError):
        scan_demo_packets(path, expected_sha256=hashlib.sha256(demo).hexdigest(), through_demo_tick=11)
