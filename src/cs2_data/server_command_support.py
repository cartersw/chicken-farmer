"""Static evidence for one newly inspected Valve server binary.

This profile deliberately does not broaden historical acceptance. It verifies
the current binary's inspected processing path and can archive the full binary
so future game updates do not erase that evidence. Runtime command clocks,
recording identity and image timing remain separate, unverified obligations.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

from .io import exclusive_output, publish, read_json, sha256_file, staging_paths

PROFILE = "server-command-support-cb593652-14180-v1"
SERVER_SHA256 = "cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c"
SOURCE_PATCH = 14180
STEAM_PATCH = "1.41.8.0"
IMAGE_BASE = 0x180000000
MAX_BINARY_BYTES = 128 * 1024 * 1024

# These ranges were independently disassembled in the cb593652 binary. They
# are retained alongside the complete binary hash, not used as loose signatures
# to approve arbitrary modules. Sizes need not equal entire function sizes.
RANGES = (
    ("set_globals", 0xD3D250, 49, "d42f54532bb230ec735879095063f3f4108c1755f324aa7aea39472d9fbb6640"),
    ("live_export_tick_full", 0xDE9D11, 101, "0640fe04d6e2e86401ddb44c5ff4e06a7a6fe499398466395a55a53c49be110c"),
    ("live_export_tick_delta", 0xDE9EA5, 106, "028b3b34bc50ebf3a977eae030561d5c1b0383c6745b856da76efffb091a9283"),
    ("movement_then_export", 0xDE3524, 267, "8a406ec6a74c571ca00d3bcba201c931d58c10c8e3c1ee7035129fa8f71d8395"),
    ("movement_entry", 0xAA4350, 63, "b755e13ef243e32de04d1613fa358ca704a99c911cec3ebd8ff13e18f6576026"),
    ("base_tick_processing", 0xC24760, 696, "ae650865af10aae2840041c04e3f4b50905ee8f08e67d913b9102f1d6892a263"),
    ("interval_constructor", 0xC07D40, 84, "9e44af2a3d3ce7d4c20983262463a3e35d399154f104b40ad7a3d66961ee171a"),
    ("interval_constructor_call", 0xC22E92, 55, "3b56ef5faefb5285ab510935724e18c35f430851e98dbd7140b6bae0a27ea105"),
    ("subtick_clock", 0xC170C6, 207, "b8621a663bbf1be5cb854587c9f57cf4b7b08c693500fe0a3f4f16cc8b2229ea"),
    ("checkpoint_export_tick", 0xD3F5AA, 59, "5f2c9080026693861a148fe1e1ae1e4bc4050e096184c6537879cb3d112b590b"),
    ("tickbase_getter", 0xB14B90, 16, "5fd82f95e49a50b445f435a1a0a3e8932a8360829ff36dddae7b05e473a664b4"),
    ("tickbase_setter", 0xB35850, 53, "4ef183c0083c8325925f0b2591feae11d9606fc6d25e951aa64f3a8c87fc7ef1"),
    ("tick_seconds_constant", 0x160CCBC, 4, "4efb856a85ce3cd60020d80d64475630ab731c6b6a31198ed4417c6dfd1829c2"),
)
VTABLE_SLOTS = (("CSource2Server.SetGlobals", 0x18077B8, 12, 0xD3D250),
                ("CSource2Server.CheckpointUserCommands", 0x18077B8, 83, 0xD3F450),
                ("CCSPlayer_MovementServices.PlayerRunCommand", 0x17A16C0, 25, 0xAA4350))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))+"\n").encode("utf-8")


def _pe_sections(data: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    try:
        if data[:2] != b"MZ":
            raise ValueError("Inspected server binary has no DOS header")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe+4] != b"PE\0\0":
            raise ValueError("Inspected server binary has no PE header")
        machine, count = struct.unpack_from("<HH", data, pe+4)
        optional_size = struct.unpack_from("<H", data, pe+20)[0]
        optional = pe+24
        if machine != 0x8664 or not 1 <= count <= 96 or optional_size < 32:
            raise ValueError("Inspected server binary has unsupported PE layout")
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise ValueError("Inspected server binary is not PE32+")
        image_base = struct.unpack_from("<Q", data, optional+24)[0]
        sections = []
        for index in range(count):
            entry = optional+optional_size+40*index
            rva, size, offset = struct.unpack_from("<III", data, entry+12)
            if offset+size > len(data):
                raise ValueError("Inspected server PE section exceeds file bounds")
            sections.append((rva, size, offset))
        return image_base, sections
    except struct.error as exc:
        raise ValueError("Inspected server PE metadata is truncated") from exc


def _slice(data: bytes, sections: list[tuple[int, int, int]], rva: int, size: int) -> bytes:
    matches = [(offset+rva-start) for start, length, offset in sections
               if start <= rva and rva+size <= start+length]
    if size < 1 or len(matches) != 1:
        raise ValueError("Inspected server range is absent or ambiguous")
    return data[matches[0]:matches[0]+size]


def _inspect(data: bytes) -> dict[str, Any]:
    if not 0 < len(data) <= MAX_BINARY_BYTES or _digest(data) != SERVER_SHA256:
        raise ValueError("Server binary does not match the independently inspected cb593652 profile")
    base, sections = _pe_sections(data)
    if base != IMAGE_BASE:
        raise ValueError("Inspected server image base mismatch")
    ranges = []
    for name, rva, size, expected in RANGES:
        raw = _slice(data, sections, rva, size)
        if _digest(raw) != expected:
            raise ValueError("Inspected server command code range mismatch: "+name)
        ranges.append({"name": name, "rva": rva, "size": size, "sha256": expected,
                       "bytes_base64": base64.b64encode(raw).decode("ascii")})
    slots = []
    for name, table_rva, slot, target in VTABLE_SLOTS:
        actual = struct.unpack("<Q", _slice(data, sections, table_rva+slot*8, 8))[0]-base
        if actual != target:
            raise ValueError("Inspected server command vtable mismatch: "+name)
        slots.append({"name": name, "vtable_rva": table_rva, "slot": slot, "target_rva": actual})
    return {"schema_version": 1, "profile": PROFILE, "status": "static_binary_audited",
            "server_sha256": SERVER_SHA256, "server_size_bytes": len(data), "preferred_image_base": base,
            "binary_ranges": ranges, "vtable_slots": slots, "source_patch_scope": SOURCE_PATCH,
            "enclosing_processing_interval": "[E-1,E]", "execution_clock": "server_tick_executed",
            "static_binary_audit_verified": True, "recording_server_identity_verified": False,
            "runtime_command_clock_verified": False, "observation_clock_verified": False,
            "semantic_button_mapping_verified": False, "training_ready": False,
            "historical_14178_acceptance_compatible": False,
            "limits": ["static_processing_path_only", "new_recording_and_runtime_identity_required",
                       "no_cross_build_compatibility_claim", "negative_or_greater_than_one_subticks_excluded",
                       "nonzero_command_flags_unmodeled", "full_checkpoints_cannot_establish_original_execution",
                       "not_physical_input_or_visual_effect_timing"]}


def inspect_server_command_support(server_binary: Path) -> dict[str, Any]:
    """Verify the exact inspected binary and code bytes without executing it."""
    if not 0 < server_binary.stat().st_size <= MAX_BINARY_BYTES:
        raise ValueError("Server binary size is outside the inspected profile limit")
    data = server_binary.read_bytes()
    report = _inspect(data)
    if sha256_file(server_binary) != report["server_sha256"]:
        raise ValueError("Server binary changed during static audit")
    return report


def _steam_metadata(data: bytes) -> dict[str, str]:
    if not 0 < len(data) <= 64*1024:
        raise ValueError("steam.inf exceeds expected metadata bounds")
    fields = {}
    for line in data.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise ValueError("steam.inf has malformed or duplicated metadata")
        fields[key] = value
    if fields.get("PatchVersion") != STEAM_PATCH or fields.get("ProductName") != "cs2" or fields.get("appID") != "730":
        raise ValueError("steam.inf is outside the newly inspected CS2 patch scope")
    return fields


@exclusive_output()
def archive_server_command_support(server_binary: Path, steam_inf: Path, out: Path) -> dict[str, Any]:
    """Archive complete inspected inputs; game updates cannot replace this copy.

    The adjacent steam.inf records installation metadata, not recording-server
    proof. Neither its fields nor this archive authorize old/new demo labels.
    """
    if any(path.name != ".cs2-data.lock" for path in out.iterdir()):
        raise ValueError("Server command support archive requires a fresh output directory")
    if not 0 < server_binary.stat().st_size <= MAX_BINARY_BYTES:
        raise ValueError("Server binary size is outside the inspected profile limit")
    binary = server_binary.read_bytes()
    metadata = steam_inf.read_bytes()
    report = _inspect(binary)
    report["installation_metadata"] = _steam_metadata(metadata)
    report["installation_metadata_is_recording_proof"] = False
    report["archive_files"] = {"server.dll": _digest(binary), "steam.inf": _digest(metadata)}
    report["archive_producer"] = "cs2-server-command-support-archive-v1"
    if sha256_file(server_binary) != _digest(binary) or sha256_file(steam_inf) != _digest(metadata):
        raise ValueError("Server command support inputs changed during archiving")
    destinations = [out / name for name in ("server.dll", "steam.inf", "server_command_support.json")]
    temporary = staging_paths(destinations)
    try:
        for path, payload in zip(temporary, (binary, metadata, _json_bytes(report))):
            with path.open("xb") as handle:
                handle.write(payload)
        publish(temporary, destinations)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return report


def load_server_command_support(path: Path) -> dict[str, Any]:
    """Recheck archived binary and exact report, independent of installed files."""
    path = path / "server_command_support.json" if path.is_dir() else path
    report = read_json(path)
    expected = inspect_server_command_support(path.parent / "server.dll")
    metadata = (path.parent / "steam.inf").read_bytes()
    expected["installation_metadata"] = _steam_metadata(metadata)
    expected["installation_metadata_is_recording_proof"] = False
    expected["archive_files"] = {"server.dll": expected["server_sha256"], "steam.inf": _digest(metadata)}
    expected["archive_producer"] = "cs2-server-command-support-archive-v1"
    if _json_bytes(report) != _json_bytes(expected):
        raise ValueError("Server command support archive disagrees with recomputed evidence")
    return report
