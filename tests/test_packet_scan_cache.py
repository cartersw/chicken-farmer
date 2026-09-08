"""Acceleration preserves the exact cold scan, byte identity, and resource limits."""
from copy import deepcopy
import hashlib
import json
import os
import zlib

import pytest

from cs2_data import packet_evidence as packets
from cs2_data.native_replay_profile import CURRENT_PROFILE, LEGACY_PROFILE
from test_packet_evidence import command, embedded, packet, vi


@pytest.fixture(autouse=True)
def empty_cache():
    packets.clear_packet_scan_cache()
    yield
    packets.clear_packet_scan_cache()


@pytest.fixture
def source(tmp_path):
    payload = embedded(3, packet([(4, b"\x08\x64"), (47, b"dropped seek message")]))
    header = embedded(1, b"PBDEMS2\x00") + b"\x10" + vi(14178)
    data = (b"PBDEMS2\x00" + b"\x00" * 8 + command(1, 0xFFFFFFFF, header)
        + command(7, 10, payload) + command(2, 10, b"nonpacket")
        + command(13, 11, embedded(2, payload)) + command(8, 12, payload)
        + command(7, 20, payload) + command(0, 25))
    path = tmp_path/"source.dem"
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def scan(source, through=11, profile=CURRENT_PROFILE):
    return packets.scan_demo_packets(source[0], expected_sha256=source[1],
        through_demo_tick=through, native_profile=profile)


def test_identical_prefix_reuses_without_exposing_cache_metadata_or_mutable_state(source, monkeypatch):
    original = scan(source)
    wanted = deepcopy(original)
    original["packets"][0]["network_ticks"][0]["network_tick"] = 999
    original["demo_commands"].clear()
    original["file_header"]["patch_version"] = 999
    def unexpected(*args, **kwargs):
        pytest.fail("An identical byte-verified prefix should not decode again")
    monkeypatch.setattr(packets, "_scan_prefix", unexpected)
    repeated = scan(source)
    assert repeated == wanted and "cache" not in repeated
    info = packets.packet_scan_cache_info()
    assert info["hits"] == 1 and info["cold_scans"] == 1
    info["hits"] = 999
    assert packets.packet_scan_cache_info()["hits"] == 1


@pytest.mark.parametrize("through,coverage", [(0, 10), (10, 11), (11, 12), (12, 20), (19, 20), (20, 25)])
def test_trimmed_prefix_is_identical_to_cold_including_gaps_same_tick_and_checkpoints(source, through, coverage):
    cold = scan(source, through)
    packets.clear_packet_scan_cache()
    scan(source, 20)
    assert scan(source, through) == cold
    assert cold["coverage_next_header_tick"] == coverage
    assert packets.packet_scan_cache_info()["hits"] == 1


def test_extension_only_decodes_new_commands_and_keeps_absolute_offsets_and_indexes(source):
    first = scan(source, 10)
    assert packets.packet_scan_cache_info()["decoded_commands"] == 3
    extended = scan(source, 20)
    info = packets.packet_scan_cache_info()
    assert info["extensions"] == 1 and info["decoded_commands"] == 6
    assert extended["demo_commands"][:3] == first["demo_commands"]
    assert [row["source_command_index"] for row in extended["demo_commands"]] == list(range(6))
    packets.clear_packet_scan_cache()
    assert scan(source, 20) == extended


def test_extension_after_a_trim_still_uses_largest_completed_prefix(source):
    scan(source, 12)
    scan(source, 0)
    result = scan(source, 20)
    info = packets.packet_scan_cache_info()
    assert info["decoded_commands"] == 6 and info["hits"] == 1 and info["extensions"] == 1
    packets.clear_packet_scan_cache()
    assert result == scan(source, 20)


def test_same_size_same_mtime_source_changes_reject_on_cache_hit(source):
    scan(source)
    before = source[0].stat()
    data = source[0].read_bytes()
    source[0].write_bytes(data.replace(b"nonpacket", b"different"))
    os.utime(source[0], ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source[0].stat().st_size == before.st_size
    assert source[0].stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(ValueError, match="source demo hash mismatch"):
        scan(source)


def test_new_expected_hash_cannot_reuse_old_bytes(source):
    first = scan(source)
    source[0].write_bytes(source[0].read_bytes().replace(b"nonpacket", b"different"))
    changed = scan((source[0], hashlib.sha256(source[0].read_bytes()).hexdigest()))
    assert changed["demo_commands"][2]["source_payload_sha256"] != first["demo_commands"][2]["source_payload_sha256"]
    assert packets.packet_scan_cache_info()["cold_scans"] == 2


def test_source_hash_is_checked_after_a_cached_prefix_is_loaded(source, monkeypatch):
    scan(source)
    original = packets._trim_prefix
    def changed(*args):
        result = original(*args)
        source[0].write_bytes(source[0].read_bytes().replace(b"nonpacket", b"different"))
        return result
    monkeypatch.setattr(packets, "_trim_prefix", changed)
    with pytest.raises(ValueError, match="changed during packet audit"):
        scan(source)


def test_changed_source_during_fresh_scan_is_never_cached(source, monkeypatch):
    original = packets._scan_prefix
    def changed(*args):
        result = original(*args)
        source[0].write_bytes(source[0].read_bytes().replace(b"nonpacket", b"different"))
        return result
    monkeypatch.setattr(packets, "_scan_prefix", changed)
    with pytest.raises(ValueError, match="changed during packet audit"):
        scan(source)
    assert packets.packet_scan_cache_info()["entries"] == 0


def test_implementation_hash_changes_force_a_new_scan(source, monkeypatch):
    version = ["original"]
    monkeypatch.setattr(packets, "_implementation_digests", lambda: tuple(version))
    scan(source)
    version[0] = "changed same size"
    scan(source)
    assert packets.packet_scan_cache_info()["cold_scans"] == 2


def test_implementation_changed_during_scan_is_not_cached(source, monkeypatch):
    version = ["original"]
    monkeypatch.setattr(packets, "_implementation_digests", lambda: tuple(version))
    original = packets._scan_prefix
    def changed(*args):
        result = original(*args)
        version[0] = "different"
        return result
    monkeypatch.setattr(packets, "_scan_prefix", changed)
    with pytest.raises(ValueError, match="implementation changed"):
        scan(source)
    assert packets.packet_scan_cache_info()["entries"] == 0


def test_implementation_fingerprint_hashes_all_reader_dependencies(monkeypatch):
    names = []
    def hashed(path):
        names.append(path.name)
        return "digest for " + path.name
    monkeypatch.setattr(packets, "sha256_file", hashed)
    assert len(packets._implementation_digests()) == 4
    assert set(names) == {"packet_evidence.py", "clock_evidence.py", "native_replay_profile.py", "io.py"}


def test_native_profile_is_part_of_the_key_and_retains_legacy_bounds(source):
    current = scan(source, profile=CURRENT_PROFILE)
    legacy = scan(source, profile=LEGACY_PROFILE)
    assert current["native_seek_filter_profile"] != legacy["native_seek_filter_profile"]
    assert packets.packet_scan_cache_info()["cold_scans"] == 2
    with pytest.raises(ValueError, match="20000"):
        scan(source, 22300, LEGACY_PROFILE)


def test_profile_bytes_are_part_of_the_key(source, monkeypatch):
    first = scan(source)
    original = packets.get_native_replay_profile
    monkeypatch.setattr(packets, "get_native_replay_profile", lambda selected:
        {**original(selected), "engine_sha256": "changed native contract"})
    changed = scan(source)
    assert changed["native_seek_filter_profile"] != first["native_seek_filter_profile"]
    assert packets.packet_scan_cache_info()["cold_scans"] == 2


@pytest.mark.parametrize("limit,value", [("MAX_COMMANDS_CURRENT", 4), ("MAX_BLOCK", 2)])
def test_changed_resource_limits_do_not_reuse_larger_cached_proof(source, monkeypatch, limit, value):
    scan(source, 20)
    monkeypatch.setattr(packets, limit, value)
    with pytest.raises(ValueError, match="bounded|Oversized"):
        scan(source, 20)


def test_extension_obeys_total_command_limit_including_prior_prefix(source, monkeypatch):
    monkeypatch.setattr(packets, "MAX_COMMANDS_CURRENT", 5)
    scan(source, 10)
    with pytest.raises(ValueError, match="bounded command count"):
        scan(source, 20)
    assert packets.packet_scan_cache_info()["entries"] == 1
    assert scan(source, 10)["coverage_next_header_tick"] == 11


def test_failed_extension_never_poisoned_original_completed_prefix(source):
    original = scan(source, 10)
    for _ in range(2):
        with pytest.raises(ValueError, match="stopped before complete"):
            scan(source, 25)
    assert scan(source, 10) == original
    assert packets.packet_scan_cache_info()["extensions"] == 2
    assert packets.packet_scan_cache_info()["entries"] == 1


def test_incomplete_fresh_scan_is_retried_and_not_cached(source):
    for _ in range(2):
        with pytest.raises(ValueError, match="stopped before complete"):
            scan(source, 25)
    assert packets.packet_scan_cache_info()["entries"] == 0
    assert packets.packet_scan_cache_info()["cold_scans"] == 2


def test_lru_entry_limit_is_enforced_and_successful_hits_refresh_recency(source, monkeypatch, tmp_path):
    monkeypatch.setattr(packets, "MAX_CACHED_PREFIXES", 2)
    sources = []
    for index in range(3):
        path = tmp_path/(str(index) + ".dem")
        path.write_bytes(source[0].read_bytes())
        sources.append((path, source[1]))
    scan(sources[0]); scan(sources[1]); scan(sources[0]); scan(sources[2])
    assert packets.packet_scan_cache_info()["entries"] == 2
    assert packets.packet_scan_cache_info()["evictions"] == 1
    scan(sources[0])
    assert packets.packet_scan_cache_info()["hits"] == 2
    scan(sources[1])
    assert packets.packet_scan_cache_info()["cold_scans"] == 4


def test_serialized_byte_limit_evicts_old_prefixes(source, monkeypatch, tmp_path):
    scan(source)
    retained = packets.packet_scan_cache_info()["retained_bytes"]
    packets.clear_packet_scan_cache()
    monkeypatch.setattr(packets, "MAX_CACHED_PREFIX_BYTES", retained * 2 - 1)
    scan(source)
    another = tmp_path/"second.dem"; another.write_bytes(source[0].read_bytes())
    scan((another, source[1]))
    info = packets.packet_scan_cache_info()
    assert info["entries"] == 1 and info["evictions"] == 1
    assert info["retained_bytes"] <= info["max_bytes"]


def test_oversized_prefix_still_verifies_but_is_not_retained(source, monkeypatch):
    monkeypatch.setattr(packets, "MAX_CACHED_PREFIX_BYTES", 1)
    assert scan(source) == scan(source)
    info = packets.packet_scan_cache_info()
    assert info["entries"] == 0 and info["retained_bytes"] == 0 and info["oversized_skips"] == 2


def test_cold_reader_decodes_each_packet_wire_format_only_once(source, monkeypatch):
    original = packets.packet_messages
    calls = []
    def counted(data):
        calls.append(data)
        return original(data)
    monkeypatch.setattr(packets, "packet_messages", counted)
    result = scan(source, 20)
    assert len(calls) == len(result["packets"]) == 4


def encoded_cache_entry(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return (zlib.compress(encoded, level=1), 123, len(encoded), hashlib.sha256(encoded).hexdigest())


def test_compressed_entry_round_trips_exactly_at_uncompressed_size_limit(monkeypatch):
    expected = {"unicode": "\u2603", "nested": [None, False, 0, "x" * 1000]}
    entry = encoded_cache_entry(expected)
    monkeypatch.setattr(packets, "MAX_SERIALIZED_PREFIX_BYTES", entry[2])
    assert packets._restore_prefix(entry) == expected


@pytest.mark.parametrize("bad_size", [None, True, False, 1.0, "1", -1, 65])
def test_invalid_declared_decoded_sizes_reject_before_decompression(monkeypatch, bad_size):
    monkeypatch.setattr(packets, "MAX_SERIALIZED_PREFIX_BYTES", 64)
    def unexpected():
        pytest.fail("Invalid size metadata must be rejected before any decompression")
    monkeypatch.setattr(packets.zlib, "decompressobj", unexpected)
    with pytest.raises(ValueError, match="decoded byte bound"):
        packets._restore_prefix((b"unused", 0, bad_size, "unused"))


@pytest.mark.parametrize("declared_size", [0, 1, 32])
def test_compression_bomb_emits_at_most_declared_size_plus_one(monkeypatch, declared_size):
    payload = b"x" * (2 * 1024 * 1024)
    compressed = zlib.compress(payload, level=1)
    original = packets.zlib.decompressobj
    calls = []
    class BoundedDecoder:
        def __init__(self):
            self.decoder = original()

        def decompress(self, data, max_length):
            decoded = self.decoder.decompress(data, max_length)
            calls.append((max_length, len(decoded)))
            return decoded

        def __getattr__(self, name):
            return getattr(self.decoder, name)
    monkeypatch.setattr(packets.zlib, "decompressobj", BoundedDecoder)
    with pytest.raises(ValueError, match="incomplete, oversized or changed"):
        packets._restore_prefix((compressed, 0, declared_size, hashlib.sha256(payload).hexdigest()))
    assert calls == [(declared_size + 1, declared_size + 1)]


@pytest.mark.parametrize("damage", ["header", "checksum", "truncated", "trailing", "concatenated", "hash", "too_large_size"])
def test_changed_compressed_cache_rejects_in_public_scan_without_returning_proof(source, monkeypatch, damage):
    scan(source)
    key, entry = next(iter(packets._PREFIX_CACHE.items()))
    compressed, offset, size, digest = entry
    if damage == "header":
        compressed = b"not zlib" + compressed[8:]
    elif damage == "checksum":
        compressed = compressed[:-1] + bytes([compressed[-1] ^ 1])
    elif damage == "truncated":
        compressed = compressed[:-1]
    elif damage == "trailing":
        compressed += b"\x00trailing bytes"
    elif damage == "concatenated":
        compressed += zlib.compress(b"{}")
    elif damage == "hash":
        digest = "0" * 64
    else:
        size += 1
    packets._PREFIX_CACHE[key] = compressed, offset, size, digest
    def unexpected(*args, **kwargs):
        pytest.fail("A corrupt retained entry must fail closed, not silently grant a new proof")
    monkeypatch.setattr(packets, "_scan_prefix", unexpected)
    with pytest.raises(ValueError, match="Cached packet serialization"):
        scan(source)
    assert packets.packet_scan_cache_info()["hits"] == 0


def test_uncompressed_oversize_skips_compression_but_keeps_verified_scan(source, monkeypatch):
    expected = scan(source)
    serialized_size = next(iter(packets._PREFIX_CACHE.values()))[2]
    packets.clear_packet_scan_cache()
    monkeypatch.setattr(packets, "MAX_SERIALIZED_PREFIX_BYTES", serialized_size - 1)
    def unexpected(*args, **kwargs):
        pytest.fail("An oversized serialized prefix must be skipped before compression")
    monkeypatch.setattr(packets.zlib, "compress", unexpected)
    assert scan(source) == expected
    assert scan(source) == expected
    info = packets.packet_scan_cache_info()
    assert info["entries"] == info["retained_bytes"] == info["hits"] == 0
    assert info["oversized_skips"] == info["cold_scans"] == 2


def test_lowered_uncompressed_limit_cannot_reuse_a_larger_cached_entry(source, monkeypatch):
    original = scan(source)
    prior_size = next(iter(packets._PREFIX_CACHE.values()))[2]
    monkeypatch.setattr(packets, "MAX_SERIALIZED_PREFIX_BYTES", prior_size - 1)
    assert scan(source) == original
    info = packets.packet_scan_cache_info()
    assert info["cold_scans"] == 2 and info["hits"] == 0 and info["oversized_skips"] == 1
