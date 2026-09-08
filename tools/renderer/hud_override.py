"""Build a pinned spectator-only CSS override inside one protected replay mod.

No installed resource is edited. The caller owns the process/settings/GameInfo
lease and must stage this before activating its unique Game search path. Loose
resource precedence and rendered HUD preservation require a real pixel review.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import struct
import zlib

PROFILE = "spectator-strip-private-css-v4"
# `pak01` is a reserved manifest identity: the engine rejects a successfully
# loaded pack with that name in an unexpected directory. Use an ordinary custom
# `pak*_dir.vpk` name, without changing the engine's manifest or its checks.
PRIVATE_ARCHIVE = "pakchicken_hud_dir.vpk"
PRIVATE_CHUNK = "pakchicken_hud_000.vpk"
RESOURCE = "panorama/styles/hud/hudhealthammocenter.vcss_c"
DIRECTORY_SHA256 = "4cb1f01a132b3ff86af9d831e58ce5436a0a876639f16383588a647723f24e32"
RESOURCE_SHA256 = "080e80a8559ff5e66c2bed167cf4529765094dd455b1f4d4c30d1f4aa233463d"
ARCHIVE, ARCHIVE_OFFSET, RESOURCE_SIZE, RESOURCE_CRC32 = 463, 73694528, 60245, 0x61F1A955
# Minified source with SrMa has a known nonmatching stored CRC. See VRF's
# ResourceTypes/Panorama.cs: Read deliberately exempts SrMa resources.
STORED_CRC32, MINIFIED_CRC32 = 0x54D85505, 0xA937624B
SELECTOR = b".HudSpecplayer__Bg"
RULE = (SELECTOR + b"{visibility: collapse;horizontal-align: center;vertical-align: bottom;"
        b"width: spec-width;height: 72px;border-radius: 5px;world-blur: hudWorldBlur;"
        b"background-color: hud-blur-bg-color;}")
OLD_DECLARATION = b"world-blur: hudWorldBlur;"
NEW_DECLARATION = b"opacity: 0;".ljust(len(OLD_DECLARATION), b" ")
AVATAR_RULE = (b".HudSpecplayer__Avatar{visibility: collapse;vertical-align: center;"
               b"horizontal-align: center;width: 100%;height: 100%;}")
AVATAR_VISIBLE_RULE = b".HudSpecplayer__Avatar .HudSpecplayerRoot--visible{visibility: visible;}"
VISIBLE_RULE = (b".HudSpecplayerRoot--visible{visibility: visible;transition-property: transform, opacity;"
                b"transition-timing-function: ease-in-out;transition-duration: 0.2s;}")
# Equal-length replacements keep the compiled source map and all block offsets
# unchanged. The layout places the name/weapon under Bg but the avatar separately.
PATCHES = (
    (RULE, OLD_DECLARATION, NEW_DECLARATION),
    (AVATAR_RULE, b"width: 100%;", b"opacity: 0;".ljust(len(b"width: 100%;"), b" ")),
    (AVATAR_VISIBLE_RULE, b"visibility: visible;", b"visibility:collapse;"),
    (VISIBLE_RULE, b"visibility: visible;", b"visibility:collapse;"),
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _blocks(data):
    if len(data) < 16:
        raise ValueError("Truncated HUD resource header")
    size, header_version, resource_version, relative, count = struct.unpack_from("<IHHII", data)
    if (size != len(data) or header_version != 12 or resource_version != 3 or count != 4 or
            relative != 8 or 8 + relative + count * 12 > len(data)):
        raise ValueError("Unsupported compiled HUD resource layout")
    blocks, occupied = {}, []
    for index in range(count):
        position = 8 + relative + 12 * index
        kind, offset, length = struct.unpack_from("<4sII", data, position)
        start, end = position + 4 + offset, position + 4 + offset + length
        if kind in blocks or start < 8 + relative + count * 12 or not start < end <= len(data):
            raise ValueError("Invalid HUD resource block")
        if any(start < other_end and other_start < end for other_start, other_end in occupied):
            raise ValueError("Overlapping HUD resource blocks")
        occupied.append((start, end))
        blocks[kind] = (start, end)
    if set(blocks) != {b"RERL", b"RED2", b"DATA", b"SrMa"}:
        raise ValueError("Unexpected HUD resource blocks")
    return blocks


def _payload(data, bounds):
    start, end = bounds
    if end - start < 6:
        raise ValueError("Truncated Panorama DATA header")
    crc, image_count = struct.unpack_from("<IH", data, start)
    if image_count > 64:
        raise ValueError("Unexpected Panorama image count")
    cursor = start + 6
    for _ in range(image_count):
        terminator = data.find(b"\0", cursor, min(end, cursor + 1024))
        if terminator < 0 or terminator + 9 > end:
            raise ValueError("Truncated Panorama image entry")
        cursor = terminator + 9  # name terminator, two uint16 dimensions, uint32 image CRC.
    if cursor >= end:
        raise ValueError("Empty Panorama stylesheet")
    return crc, cursor, data[cursor:end]


def _patch_resource(data, expected_sha256, expected_stored_crc, expected_payload_crc):
    """Pure implementation; public entry always supplies the pinned profile."""
    if _sha(data) != expected_sha256:
        raise ValueError("Installed HUD resource differs from the inspected profile")
    blocks = _blocks(data)
    old_crc, text_start, payload = _payload(data, blocks[b"DATA"])
    if old_crc != expected_stored_crc or zlib.crc32(payload) != expected_payload_crc:
        raise ValueError("HUD source checksum profile changed")
    changed_payload = bytearray(payload)
    patches = []
    for rule, old, new in PATCHES:
        if payload.count(rule) != 1 or rule.count(old) != 1 or len(old) != len(new):
            raise ValueError("Spectator-only CSS rule is missing or ambiguous")
        offset = payload.index(rule) + rule.index(old)
        changed_payload[offset:offset + len(old)] = new
        patches.append({"selector": rule.split(b"{", 1)[0].decode(),
                        "declaration_file_offset": text_start + offset,
                        "old_declaration": old.decode(), "new_declaration": new.decode(),
                        "declaration_size": len(old)})
    changed_payload = bytes(changed_payload)
    changed_crc = zlib.crc32(changed_payload)
    result = bytearray(data)
    result[text_start:blocks[b"DATA"][1]] = changed_payload
    struct.pack_into("<I", result, blocks[b"DATA"][0], changed_crc)
    result = bytes(result)
    final_crc, final_start, final_payload = _payload(result, _blocks(result)[b"DATA"])
    if (len(result) != len(data) or final_start != text_start or
            final_payload != changed_payload or final_crc != zlib.crc32(final_payload)):
        raise ValueError("Patched HUD resource failed its checksum/layout verification")
    undone = bytearray(result)
    for patch in patches:
        offset = patch["declaration_file_offset"]
        undone[offset:offset + patch["declaration_size"]] = patch["old_declaration"].encode()
    struct.pack_into("<I", undone, blocks[b"DATA"][0], old_crc)
    if bytes(undone) != data:
        raise ValueError("HUD patch changed bytes outside the owned declaration/checksum")
    return result, {
        "schema_version": 2, "profile": PROFILE, "resource": RESOURCE,
        "source_resource_sha256": _sha(data), "override_resource_sha256": _sha(result),
        "resource_size": len(result), "selector": SELECTOR.decode(),
        "old_declaration": OLD_DECLARATION.decode(), "new_declaration": NEW_DECLARATION.decode(),
        "declaration_file_offset": patches[0]["declaration_file_offset"],
        "declaration_size": len(OLD_DECLARATION), "patches": patches,
        "data_crc_file_offset": blocks[b"DATA"][0],
        "source_stored_data_crc32": f"{old_crc:08x}",
        "source_minified_payload_crc32": f"{zlib.crc32(payload):08x}",
        "source_stored_crc_matches_minified_payload": old_crc == zlib.crc32(payload),
        "override_data_crc32": f"{changed_crc:08x}", "override_payload_crc_verified": True,
        "unchanged_bytes_roundtrip_verified": True,
        "resource_load_verified": False, "rendered_hud_preservation_verified": False,
        "raw_frames_masked": False, "training_ready": False,
    }


def build_private_archive(resource):
    """One VPK v2 entry in chunk zero, with the standard MD5 sections.

    The resource bytes themselves form the complete chunk zero file. This layout
    matches ValvePak's multiChunk writer, including its 48-byte footer.
    """
    folder, leaf = RESOURCE.rsplit("/", 1)
    name, extension = leaf.rsplit(".", 1)
    entry = struct.pack("<IHHIIH", zlib.crc32(resource), 0, 0, 0, len(resource), 0xFFFF)
    tree = (extension.encode() + b"\0" + folder.encode() + b"\0" + name.encode() +
            b"\0" + entry + b"\0\0\0")
    chunk_md5 = struct.pack("<III", 0, 0, len(resource)) + hashlib.md5(resource).digest()
    archive = struct.pack("<7I", 0x55AA1234, 2, len(tree), 0, 28, 48, 0) + tree + chunk_md5
    archive += hashlib.md5(tree).digest() + hashlib.md5(chunk_md5).digest()
    archive += hashlib.md5(archive).digest()
    # Independently check the published tree, chunk entry and every checksum.
    _verify_private_archive(archive, resource)
    return archive


def _verify_private_archive(archive, resource):
    if len(archive) < 28:
        raise ValueError("Truncated private HUD archive")
    magic, version, tree_size, size, md5, other, signature = struct.unpack_from("<7I", archive)
    if (magic, version, size, md5, other, signature) != (0x55AA1234, 2, 0, 28, 48, 0):
        raise ValueError("Private HUD archive header changed")
    cursor, names = 28, []
    for _ in range(3):
        end = archive.find(b"\0", cursor, 28 + tree_size)
        if end < cursor:
            raise ValueError("Private HUD archive path is truncated")
        names.append(archive[cursor:end].decode("utf-8"))
        cursor = end + 1
    if f"{names[1]}/{names[2]}.{names[0]}" != RESOURCE or cursor + 21 != 28 + tree_size:
        raise ValueError("Private HUD archive contains unexpected entries")
    fields = struct.unpack_from("<IHHIIH", archive, cursor)
    if fields != (zlib.crc32(resource), 0, 0, 0, len(resource), 0xFFFF):
        raise ValueError("Private HUD archive resource checksum/location changed")
    data_end = 28 + tree_size
    if archive[cursor + 18:cursor + 21] != b"\0\0\0" or len(archive) != data_end + 76:
        raise ValueError("Private HUD archive section lengths changed")
    chunk_metadata = archive[data_end:data_end + 28]
    if (struct.unpack_from("<III", chunk_metadata) != (0, 0, len(resource)) or
            chunk_metadata[12:] != hashlib.md5(resource).digest()):
        raise ValueError("Private HUD archive chunk checksum changed")
    hashes = archive[data_end + 28:]
    if (hashes[:16] != hashlib.md5(archive[28:data_end]).digest() or
            hashes[16:32] != hashlib.md5(chunk_metadata).digest() or
            hashes[32:] != hashlib.md5(archive[:-16]).digest()):
        raise ValueError("Private HUD archive footer checksum changed")


def build_hud_override(data):
    """Return overridden resource bytes and provenance; performs no writes."""
    return _patch_resource(data, RESOURCE_SHA256, STORED_CRC32, MINIFIED_CRC32)


def _directory_entry(data):
    if len(data) < 28 or len(data) > 16 * 1024**2 or _sha(data) != DIRECTORY_SHA256:
        raise ValueError("Installed VPK directory differs from the inspected HUD profile")
    signature, version, tree_size = struct.unpack_from("<III", data)
    end, cursor = 28 + tree_size, 28
    if signature != 0x55AA1234 or version != 2 or end > len(data):
        raise ValueError("Invalid VPK directory header")
    found = []
    def read_string():
        nonlocal cursor
        zero = data.find(b"\0", cursor, min(end, cursor + 4096))
        if zero < 0:
            raise ValueError("Truncated VPK directory string")
        value = data[cursor:zero].decode("utf-8")
        cursor = zero + 1
        return value
    while extension := read_string():
        while folder := read_string():
            while name := read_string():
                if cursor + 18 > end:
                    raise ValueError("Truncated VPK directory entry")
                crc, preload, archive, offset, length, terminator = struct.unpack_from("<IHHIIH", data, cursor)
                cursor += 18 + preload
                if terminator != 65535 or cursor > end:
                    raise ValueError("Invalid VPK directory entry")
                if f"{folder}/{name}.{extension}" == RESOURCE:
                    found.append((crc, preload, archive, offset, length))
    if cursor != end or found != [(RESOURCE_CRC32, 0, ARCHIVE, ARCHIVE_OFFSET, RESOURCE_SIZE)]:
        raise ValueError("Pinned spectator HUD VPK entry is missing or changed")


def _ordinary_path(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("HUD override paths must not contain reparse points")


def stage_hud_override(game_dir, mod_dir):
    """Stage only in game/csgo/chicken-render-<32hex>; never overwrite a file."""
    game, mod = Path(game_dir).absolute(), Path(mod_dir).absolute()
    if (game.resolve() != game or mod.resolve() != mod or mod.parent != game / "csgo" or
            re.fullmatch(r"chicken-render-[0-9a-f]{32}", mod.name) is None):
        raise ValueError("HUD override requires this run's isolated CS2 mod directory")
    destination = mod / RESOURCE
    private_archive = mod / PRIVATE_ARCHIVE
    private_chunk = mod / PRIVATE_CHUNK
    # Check all existing ancestors before mkdir/open; no source or installed
    # resource is modified. The outer process lease owns this path exclusively.
    for path in (destination, *destination.parents):
        if path.exists() or path.is_symlink():
            _ordinary_path(path)
    for package_path in (private_archive, private_chunk):
        if package_path.exists() or package_path.is_symlink():
            _ordinary_path(package_path)
            raise ValueError("HUD override destination already exists")
    if destination.exists():
        raise ValueError("HUD override destination already exists")
    directory = game / "csgo/pak01_dir.vpk"
    directory_bytes = directory.read_bytes()
    _directory_entry(directory_bytes)
    archive = game / "csgo" / f"pak01_{ARCHIVE:03}.vpk"
    with archive.open("rb") as source:
        source.seek(ARCHIVE_OFFSET)
        data = source.read(RESOURCE_SIZE)
    if len(data) != RESOURCE_SIZE or zlib.crc32(data) != RESOURCE_CRC32:
        raise ValueError("Pinned VPK resource byte checksum failed")
    changed, report = build_hud_override(data)
    packaged = build_private_archive(changed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as target:
        target.write(changed)
    if destination.read_bytes() != changed:
        raise ValueError("Staged HUD override failed readback verification")
    with private_archive.open("xb") as target:
        target.write(packaged)
    with private_chunk.open("xb") as target:
        target.write(changed)
    archived = private_archive.read_bytes()
    chunk_bytes = private_chunk.read_bytes()
    _verify_private_archive(archived, chunk_bytes)
    if chunk_bytes != changed:
        raise ValueError("Staged private HUD chunk failed readback verification")
    if archived != packaged:
        raise ValueError("Staged private HUD archive failed readback verification")
    return {**report, "source_directory_sha256": _sha(directory_bytes),
            "source_archive": archive.name, "source_archive_offset": ARCHIVE_OFFSET,
            "source_resource_crc32": f"{RESOURCE_CRC32:08x}",
            "staged_relative_path": RESOURCE, "staged_file": str(destination),
            "private_archive_relative_path": PRIVATE_ARCHIVE,
            "private_archive_sha256": _sha(packaged), "private_archive_size": len(packaged),
            "private_chunk_relative_path": PRIVATE_CHUNK,
            "private_chunk_sha256": _sha(changed), "private_chunk_size": len(changed),
            "private_archive_entry_count": 1, "private_archive_roundtrip_verified": True,
            "private_archive_md5_verified": True,
            "delivery": "identical_loose_and_chunked_vpk_resource",
            "installed_resources_modified": False}
