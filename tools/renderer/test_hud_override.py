"""Synthetic resource fixtures only; never read or mutate installed CS2 assets."""
import hashlib
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest

SPEC = importlib.util.spec_from_file_location("hud_override_fixture", Path(__file__).with_name("hud_override.py"))
hud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hud)


def resource(payload=None, *, image_header=b"\0\0", stored_crc=0x12345678):
    if payload is None:
        payload = (b".own-health{opacity: 1;}" + b"".join(rule for rule, _, _ in hud.PATCHES) +
                   b".own-ammo{visibility: visible;}")
    bodies = [(b"RERL", b"refs"), (b"RED2", b"meta"),
              (b"DATA", struct.pack("<I", stored_crc) + image_header + payload), (b"SrMa", b"maps")]
    result = bytearray(64)
    for i, (kind, body) in enumerate(bodies):
        position = 16 + i * 12
        struct.pack_into("<4sII", result, position, kind, len(result) - position - 4, len(body))
        result += body
    struct.pack_into("<IHHII", result, 0, len(result), 12, 3, 8, 4)
    return bytes(result), payload


def patch(data, payload, *, stored=0x12345678):
    return hud._patch_resource(data, hashlib.sha256(data).hexdigest(), stored, zlib.crc32(payload))


def test_only_spectator_declaration_and_data_crc_change():
    original, payload = resource()
    output, proof = patch(original, payload)
    assert len(output) == len(original)
    assert output.count(hud.NEW_DECLARATION) == 1
    assert b".own-health{opacity: 1;}" in output
    assert b".own-ammo{visibility: visible;}" in output
    crc_at = proof["data_crc_file_offset"]
    changed = {i for i, (a, b) in enumerate(zip(original, output)) if a != b}
    allowed = set(range(crc_at, crc_at + 4))
    assert len(proof["patches"]) == 4
    for change in proof["patches"]:
        declaration_at = change["declaration_file_offset"]
        region = set(range(declaration_at, declaration_at + change["declaration_size"]))
        allowed |= region
        assert changed.intersection(region)
    assert changed <= allowed
    assert hud.AVATAR_VISIBLE_RULE.replace(b"visibility: visible;", b"visibility:collapse;") in output
    assert hud.VISIBLE_RULE.replace(b"visibility: visible;", b"visibility:collapse;") in output
    blocks = hud._blocks(output)
    checksum, _, text = hud._payload(output, blocks[b"DATA"])
    assert checksum == zlib.crc32(text) == int(proof["override_data_crc32"], 16)
    assert proof["source_stored_crc_matches_minified_payload"] is False
    assert proof["unchanged_bytes_roundtrip_verified"] is True
    assert proof["resource_load_verified"] is proof["rendered_hud_preservation_verified"] is False
    assert proof["training_ready"] is proof["raw_frames_masked"] is False


def test_panorama_image_table_is_preserved():
    entry = struct.pack("<H", 1) + b"panorama/images/test.vtex\0" + struct.pack("<HHI", 16, 32, 0xABCDEF01)
    original, payload = resource(image_header=entry)
    output, _ = patch(original, payload)
    assert output.count(entry) == 1


@pytest.mark.parametrize("payload", [b".own-health{opacity: 1;}", hud.RULE * 2,
    hud.RULE.replace(b"hudWorldBlur", b"changedBlur!")])
def test_missing_ambiguous_or_changed_selector_is_rejected(payload):
    original, payload = resource(payload)
    with pytest.raises(ValueError, match="missing or ambiguous"):
        patch(original, payload)


def test_public_builder_refuses_unknown_resource_before_patching():
    original, _ = resource()
    with pytest.raises(ValueError, match="differs"):
        hud.build_hud_override(original)


@pytest.mark.parametrize("which", ["stored", "payload"])
def test_profile_checks_both_original_crc_values(which):
    original, payload = resource()
    with pytest.raises(ValueError, match="checksum profile"):
        hud._patch_resource(original, hashlib.sha256(original).hexdigest(),
                            0 if which == "stored" else 0x12345678,
                            0 if which == "payload" else zlib.crc32(payload))


@pytest.mark.parametrize("mutation", [
    lambda b: struct.pack_into("<I", b, 0, len(b) - 1),
    lambda b: struct.pack_into("<H", b, 4, 13),
    lambda b: struct.pack_into("<I", b, 12, 0xFFFFFFFF),
    lambda b: struct.pack_into("<I", b, 20, 0),
    lambda b: struct.pack_into("<I", b, 24, 0xFFFFFFFF),
    lambda b: b.__setitem__(slice(52, 56), b"DATA"),
])
def test_malformed_compiled_blocks_fail_closed(mutation):
    original, payload = resource()
    data = bytearray(original)
    mutation(data)
    with pytest.raises(ValueError):
        patch(bytes(data), payload)


def test_truncated_panorama_image_entry_is_rejected():
    original, payload = resource(image_header=struct.pack("<H", 1))
    with pytest.raises(ValueError, match="image entry"):
        patch(original, payload)


@pytest.fixture
def installed_fixture(tmp_path, monkeypatch):
    game = tmp_path / "game"
    csgo = game / "csgo"
    csgo.mkdir(parents=True)
    mod = csgo / ("chicken-render-" + "a" * 32)
    mod.mkdir()
    data, payload = resource()
    crc = zlib.crc32(data)
    entry = struct.pack("<IHHIIH", crc, 0, 463, 7, len(data), 65535)
    tree = b"vcss_c\0panorama/styles/hud\0hudhealthammocenter\0" + entry + b"\0\0\0"
    directory = struct.pack("<7I", 0x55AA1234, 2, len(tree), 0, 0, 0, 0) + tree
    (csgo / "pak01_dir.vpk").write_bytes(directory)
    (csgo / "pak01_463.vpk").write_bytes(b"prefix!" + data + b"unrelated archive bytes")
    for name, value in {"DIRECTORY_SHA256": hashlib.sha256(directory).hexdigest(),
            "RESOURCE_SHA256": hashlib.sha256(data).hexdigest(), "ARCHIVE_OFFSET": 7,
            "RESOURCE_SIZE": len(data), "RESOURCE_CRC32": crc,
            "STORED_CRC32": 0x12345678, "MINIFIED_CRC32": zlib.crc32(payload)}.items():
        monkeypatch.setattr(hud, name, value)
    return game, mod, data


def test_stages_only_owned_mod_and_never_changes_archive(installed_fixture):
    game, mod, _ = installed_fixture
    originals = {p: p.read_bytes() for p in (game / "csgo").glob("*.vpk")}
    result = hud.stage_hud_override(game, mod)
    destination = mod / hud.RESOURCE
    archive = mod / hud.PRIVATE_ARCHIVE
    chunk = mod / hud.PRIVATE_CHUNK
    assert destination.is_file()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == result["override_resource_sha256"]
    assert result["installed_resources_modified"] is False
    hud._verify_private_archive(archive.read_bytes(), destination.read_bytes())
    assert chunk.read_bytes() == destination.read_bytes()
    assert result["private_archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["private_archive_entry_count"] == 1
    assert all(p.read_bytes() == data for p, data in originals.items())
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        hud.stage_hud_override(game, mod)
    assert destination.read_bytes() == before


@pytest.mark.parametrize("relative", ["csgo", "csgo/normal-mod", "csgo/" + "chicken-render-" + "a" * 31,
                                       "csgo/outside/" + "chicken-render-" + "a" * 32])
def test_staging_refuses_unowned_destinations(installed_fixture, relative):
    game, _, _ = installed_fixture
    with pytest.raises(ValueError, match="isolated"):
        hud.stage_hud_override(game, game / relative)


@pytest.mark.parametrize("name", ["pak01_dir.vpk", "pak01_463.vpk"])
def test_changed_source_fails_before_creating_override(installed_fixture, name):
    game, mod, _ = installed_fixture
    source = game / "csgo" / name
    source.write_bytes(b"changed")
    with pytest.raises(ValueError):
        hud.stage_hud_override(game, mod)
    assert list(mod.iterdir()) == []


def test_reparse_points_are_rejected_without_following_them():
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat_mode, st_file_attributes=0x400))
    stat_mode = 0o040755
    with pytest.raises(ValueError, match="reparse"):
        hud._ordinary_path(path)


@pytest.mark.parametrize("patch_index", range(4))
def test_every_spectator_rule_is_required_and_unique(patch_index):
    rules = [p[0] for p in hud.PATCHES]
    original, payload = resource(b"".join(rules[:patch_index] + rules[patch_index + 1:]))
    with pytest.raises(ValueError, match="missing or ambiguous"):
        patch(original, payload)
    original, payload = resource(b"".join(rules) + rules[patch_index])
    with pytest.raises(ValueError, match="missing or ambiguous"):
        patch(original, payload)


@pytest.mark.parametrize("mutation", [
    lambda b: struct.pack_into("<I", b, 12, 1),
    lambda b: struct.pack_into("<I", b, 16, 1),
    lambda b: b.__setitem__(28, ord("X")),
    lambda b: b.__setitem__(-1, b[-1] ^ 1),
    lambda b: b.extend(b"extra"),
])
def test_private_archive_roundtrip_rejects_changed_metadata_or_content(mutation):
    data, _ = resource()
    archive = bytearray(hud.build_private_archive(data))
    mutation(archive)
    with pytest.raises(ValueError):
        hud._verify_private_archive(bytes(archive), data)


@pytest.mark.parametrize("name", [hud.PRIVATE_ARCHIVE, hud.PRIVATE_CHUNK])
def test_preexisting_private_archive_prevents_all_staging(installed_fixture, name):
    game, mod, _ = installed_fixture
    (mod / name).write_bytes(b"owned by another operation")
    with pytest.raises(ValueError, match="already exists"):
        hud.stage_hud_override(game, mod)
    assert not (mod / hud.RESOURCE).exists()


def test_changed_policy_has_distinct_renderer_profile_identity():
    assert hud.PROFILE == "spectator-strip-private-css-v4"
    assert hud.PRIVATE_ARCHIVE == "pakchicken_hud_dir.vpk"
    assert hud.PRIVATE_CHUNK == "pakchicken_hud_000.vpk"
    assert not hud.PRIVATE_ARCHIVE[3:5].isdigit()


def test_external_chunk_and_all_three_footer_hashes_are_standard_v2():
    data, _ = resource()
    archive = hud.build_private_archive(data)
    header = struct.unpack_from("<7I", archive)
    assert header[3:] == (0, 28, 48, 0)
    tree = archive[28:28 + header[2]]
    chunk = archive[28 + header[2]:28 + header[2] + 28]
    assert struct.unpack_from("<III", chunk) == (0, 0, len(data))
    assert chunk[12:] == hashlib.md5(data).digest()
    assert archive[-48:-32] == hashlib.md5(tree).digest()
    assert archive[-32:-16] == hashlib.md5(chunk).digest()
    assert archive[-16:] == hashlib.md5(archive[:-16]).digest()
    with pytest.raises(ValueError, match="checksum"):
        hud._verify_private_archive(archive, data[:-1] + bytes([data[-1] ^ 1]))
