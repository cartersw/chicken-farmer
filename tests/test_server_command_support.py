"""Static native evidence never grants runtime or historical compatibility."""
import hashlib
import json
from pathlib import Path
import struct

import pytest

from cs2_data import server_command_support as support


@pytest.fixture
def binary(tmp_path, monkeypatch):
    # Synthetic PE fixture, never evidence for a real Valve binary. Production
    # requires the exact full-file cb593652 digest and 13 inspected ranges.
    data = bytearray(0x600)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<Q", data, 0x98+24, support.IMAGE_BASE)
    section = 0x98+0xF0
    data[section:section+5] = b".text"
    struct.pack_into("<IIII", data, section+8, 0x400, 0x1000, 0x400, 0x200)
    code = b"\x48\x8b\x41\x10\xc3"
    data[0x200:0x205] = code
    struct.pack_into("<Q", data, 0x280, support.IMAGE_BASE+0x1000)
    path = tmp_path / "installed-server.dll"
    path.write_bytes(data)
    metadata = tmp_path / "steam.inf"
    metadata.write_text("PatchVersion=1.41.8.0\nProductName=cs2\nappID=730\nServerVersion=2000905\n")
    monkeypatch.setattr(support, "SERVER_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(support, "RANGES", (("test_function", 0x1000, len(code), hashlib.sha256(code).hexdigest()),))
    monkeypatch.setattr(support, "VTABLE_SLOTS", (("Test.Function", 0x1080, 0, 0x1000),))
    return path, metadata


def test_static_binary_audit_preserves_scoped_unverified_runtime_obligations(binary):
    report = support.inspect_server_command_support(binary[0])
    assert report["static_binary_audit_verified"] is True
    assert report["enclosing_processing_interval"] == "[E-1,E]"
    for name in ("runtime_command_clock_verified", "recording_server_identity_verified", "observation_clock_verified",
                 "training_ready", "semantic_button_mapping_verified", "historical_14178_acceptance_compatible"):
        assert report[name] is False
    assert len(report["binary_ranges"]) == 1
    assert report["vtable_slots"][0]["target_rva"] == 0x1000


def test_unknown_binary_never_reaches_loose_signature_matching(tmp_path):
    path = tmp_path / "unknown.dll"
    path.write_bytes(b"not the inspected binary")
    with pytest.raises(ValueError, match="independently inspected"):
        support.inspect_server_command_support(path)


@pytest.mark.parametrize("offset,reason", [(0x200, "code range mismatch"), (0x280, "vtable mismatch")])
def test_updating_a_full_hash_alone_cannot_hide_changed_audited_code_or_dispatch(binary, monkeypatch, offset, reason):
    data = bytearray(binary[0].read_bytes())
    data[offset] ^= 1
    binary[0].write_bytes(data)
    monkeypatch.setattr(support, "SERVER_SHA256", hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match=reason):
        support.inspect_server_command_support(binary[0])


@pytest.mark.parametrize("corruption", ["dos", "pe", "optional", "section", "ambiguous"])
def test_malformed_pe_layout_is_rejected(binary, monkeypatch, corruption):
    data = bytearray(binary[0].read_bytes())
    if corruption == "dos":
        data[0] = 0
    elif corruption == "pe":
        data[0x80] = 0
    elif corruption == "optional":
        struct.pack_into("<H", data, 0x98, 0x10B)
    elif corruption == "section":
        struct.pack_into("<I", data, 0x98+0xF0+16, len(data))
    else:
        struct.pack_into("<H", data, 0x86, 2)
        section = 0x98+0xF0
        data[section+40:section+80] = data[section:section+40]
    binary[0].write_bytes(data)
    monkeypatch.setattr(support, "SERVER_SHA256", hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError):
        support.inspect_server_command_support(binary[0])


def test_archived_full_binary_remains_recheckable_after_installed_binary_changes(binary, tmp_path):
    destination = tmp_path / "archive"
    report = support.archive_server_command_support(*binary, destination)
    assert (destination / "server.dll").read_bytes() == binary[0].read_bytes()
    assert report["installation_metadata_is_recording_proof"] is False
    binary[0].write_bytes(b"a later installed update")
    binary[1].write_text("PatchVersion=1.42.0.0\n")
    assert support.load_server_command_support(destination) == report


@pytest.mark.parametrize("defect", ["ready", "runtime", "old_patch", "range"])
def test_edited_archive_report_cannot_promote_static_evidence(binary, tmp_path, defect):
    destination = tmp_path / "archive"
    report = support.archive_server_command_support(*binary, destination)
    if defect == "range":
        report["binary_ranges"][0]["rva"] += 1
    else:
        name = {"ready": "training_ready", "runtime": "runtime_command_clock_verified",
                "old_patch": "historical_14178_acceptance_compatible"}[defect]
        report[name] = True
    (destination / "server_command_support.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="recomputed evidence"):
        support.load_server_command_support(destination)


@pytest.mark.parametrize("text", ["PatchVersion=1.41.7.8\nProductName=cs2\nappID=730\n",
                                  "PatchVersion=1.41.8.0\nPatchVersion=1.41.8.0\nProductName=cs2\nappID=730\n"])
def test_wrong_or_ambiguous_installation_metadata_cannot_be_archived(binary, tmp_path, text):
    binary[1].write_text(text)
    destination = tmp_path / "archive"
    with pytest.raises(ValueError):
        support.archive_server_command_support(*binary, destination)
    assert not (destination / "server_command_support.json").exists()


def test_existing_archived_evidence_is_never_overwritten(binary, tmp_path):
    destination = tmp_path / "archive"
    support.archive_server_command_support(*binary, destination)
    before = (destination / "server_command_support.json").read_bytes()
    with pytest.raises(ValueError, match="fresh output"):
        support.archive_server_command_support(*binary, destination)
    assert (destination / "server_command_support.json").read_bytes() == before
