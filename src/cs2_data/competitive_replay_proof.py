"""Recompute the current renderer / original 14178 command proof independently.

Native replay and recording-source contracts are separate fixed profiles. Full
original commands are reconstructed again from the beginning of the demo. The
HUD criterion uses the explicit user-approved capture-setup policy, without
recurring manual or automatic visual inspection. This assumption does not
establish per-image HUD correctness or replace the remaining capture proofs.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import struct

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .causal_acceptance import _command_source, _same, _scan_through, _source_envelopes
from .competitive_source_verification import reverify_competitive_source
from .control_label_audit import canonical_row_sha256
from .hud_policy import policy_allows_capture, trusted_hud_policy
from .io import parsed_manifest, read_json, sha256_file
from .jobs import phase_evidence
from .native_replay_profile import CURRENT_PROFILE, get_native_replay_profile, header_matches_profile
from .packet_bounds import audit_packet_bounds
from .packet_evidence import scan_demo_packets
from .server_command_support import IMAGE_BASE, _pe_sections, _slice
from .synchronization import recompute_synchronization
from .timing import read_ledger
from .validation import load_state_context

PROFILE = "cs2-competitive-replay-source-proof-v3"
PROJECT = Path(__file__).resolve().parents[2]
SOURCE_PATCH = 14178
SOURCE_SERVER_SHA256 = "9e5749d77dcb68883477feae751a3f28068d119ec145edcb0e4d48d15b538d36"
SOURCE_SERVER = PROJECT/"data/native-profiles/recovered-14178-v1/game/csgo/bin/win64/server.dll"
RENDER_BINARIES = PROJECT/"data/native-profiles/calibration-14180-v1"
PROFILE_COMPARISON = PROJECT/"data/native-profiles/current-competitive-replay-v1/binary_comparison.json"
PROFILE_COMPARISON_SHA256 = "6f5b3484bb48fee81ec9aae0a3df0d1c786e66827413d95687c62e047f7c8cc0"
PLUGIN_SHA256 = "aa553ed98fba652e3b8b13dce0db7eba4225503dd3fafc90ba7e3517c896b84f"
RENDERER_PROFILE = "windows-14180-competitive-hud-v1"
HUD_ARCHIVE_SEARCH_PROFILE = "competitive-hud-archive-search-v2"
IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot")

# Original 14178 RVAs documented in SERVER_COMMAND_SUPPORT.md. These are checked
# only inside the exact recovered 9e57 complete binary, never as loose signatures
# for a different build. The delta exporter starts at the original +0xDE9EAA.
SOURCE_RANGES = (
    ("set_globals", 0xD3D250, 49, "d42f54532bb230ec735879095063f3f4108c1755f324aa7aea39472d9fbb6640"),
    ("live_export_tick_full", 0xDE9D11, 101, "0640fe04d6e2e86401ddb44c5ff4e06a7a6fe499398466395a55a53c49be110c"),
    ("live_export_tick_delta", 0xDE9EAA, 101, "27e951489f7354c8f3779becd76be44963cf534ae856eadd68fbd61d9d3bfa9c"),
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
SOURCE_SLOTS = ((0x18077B8, 12, 0xD3D250), (0x18077B8, 83, 0xD3F450), (0x17A16C0, 25, 0xAA4350))

# Historical diagnostic registry, no longer used by production recomputation.
# Entries were added only after inspecting the complete archived pilot sequence.
# Key: exact capture-ledger SHA256. Value: fixed review path and review SHA256.
# There is deliberately no caller-supplied review path or approval boolean.
HUD_REVIEWS = {
    "4dcf8531eec5803b0de6e1b70741ddbc6a20e23d4da51dc5250c5986f3cf9448": (
        Path("data/validation/dust2-hud-006-v1/hud_review.json"),
        "85181b5e0cec1b79166d0cdda90ec3201f8658242930b25c954bb7300de64d66"),
    "ea3919e7304aa171199b4160157e37cf714851d618eed48f9a642a72cac50f9d": (
        Path("data/validation/competitive-expansion-001/dust2-hud-review.json"),
        "e989cfbff96fa40b9c7464ccb1adfab7744c85875bf5a3a0368be8d5d7ba36f6"),
    "bd18ad0b9759ea8e96350ee3f74cba8b01d7ea7b56da3fd94dd25d800fca80de": (
        Path("data/validation/competitive-expansion-001/nuke-hud-review.json"),
        "8cdaafbaaafe99406b4c1d0cdedae98b38ce3fefaf54cfdf6d749014e448a818"),
    "f05092dbe96114b0632ffcdeb16f38609b470f6d6adc09a8d861860935470efa": (
        Path("data/validation/action-coverage-001/dust2-round8-hud-review.json"),
        "91aa189d7264df08113d43105e2fb47d86f0e2265f2e8f42bda8fef8e35425c3"),
    "dbe2930405b7d2598d9e8447d252d2421c9e08ba711e3a8da03c3fd2e7c07f59": (
        Path("data/validation/action-coverage-001/nuke-round7-hud-review.json"),
        "3afc5082d172e62b09df6cfee84c923711e8533d02ab0079c1bad1a6a86c3422"),
    "3cf7089f5ea09d504ed8d2dcfbd643db3c05c750052d48f92fd1117033b8386d": (
        Path("data/validation/action-coverage-001/dust2-round24-hud-review.json"),
        "d25f0e2d69b769c56d61dd4c5182afb7b649bac278e6881a24976eb4d8ef9e15"),
    "ef7b616935b22409c1214e8f25d1e695a7e713dcdccc3b8510c818abc32d7079": (
        Path("data/validation/action-coverage-001/nuke-round21-hud-review.json"),
        "b0d9a13f8b910d63cf0722143761700ff98cb339ec3f96e4a9127a38cd778ec0"),
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class _Sources:
    def __init__(self):
        self.files = {}

    def watch(self, path, expected=None):
        path = Path(path).resolve()
        digest = sha256_file(path)
        _require(expected is None or digest == expected, "Competitive source hash mismatch: " + str(path))
        _require(str(path) not in self.files or self.files[str(path)] == digest, "Competitive source changed during verification")
        self.files[str(path)] = digest
        return digest

    def finish(self):
        _require(all(sha256_file(Path(path)) == digest for path, digest in self.files.items()),
            "Competitive source changed during verification")


def _source_eligibility(parsed, canonical, phase_path, state_context, commands, watch):
    """Replace pilot digest pins with fresh full original-source reconstruction."""
    _require(phase_path is not None, "Original-source competitive phase evidence is required")
    reconstructed = reverify_competitive_source(Path(canonical["source_path"]), parsed, phase_path,
        state_context, command_rows=commands)
    for path, expected in reconstructed["source_files"].items():
        watch(Path(path), expected)
    return reconstructed


def _source_support(source, watch):
    digest = watch(SOURCE_SERVER, SOURCE_SERVER_SHA256)
    data = SOURCE_SERVER.read_bytes()
    _require(hashlib.sha256(data).hexdigest() == digest, "Recovered recording-profile binary changed")
    base, sections = _pe_sections(data)
    _require(base == IMAGE_BASE, "Recovered recording-profile image base disagrees")
    ranges = []
    for name, rva, size, expected in SOURCE_RANGES:
        _require(hashlib.sha256(_slice(data, sections, rva, size)).hexdigest() == expected,
            "Recovered original 14178 command range changed: " + name)
        ranges.append({"name": name, "rva": rva, "size": size, "sha256": expected})
    for table, slot, target in SOURCE_SLOTS:
        _require(struct.unpack("<Q", _slice(data, sections, table+8*slot, 8))[0]-base == target,
            "Recovered original 14178 command vtable changed")
    header = source.get("file_header") or {}
    reasons = [] if type(header.get("patch_version")) is int and header["patch_version"] == SOURCE_PATCH else ["source_server_patch_unsupported"]
    return {"profile": "server-command-support-14178-recovered-v1", "status": "unknown" if reasons else "verified",
        "reason_codes": reasons, "source_patch": header.get("patch_version"), "support_interval": "[E-1,E]",
        "support_meaning": "enclosing_recorded_server_command_processing_step",
        "source_file_header_sha256": header.get("protobuf_sha256"), "inspected_server_path": str(SOURCE_SERVER.resolve()),
        "inspected_server_sha256": digest, "binary_ranges": ranges,
        "recording_server_binary_identity_verified": False,
        "scope": "original_source_patch_14178_with_recovered_exact_inspected_processing_binary",
        "limits": ["Recording metadata does not reveal the original FACEIT server DLL hash.",
            "This preserves the reviewed 14178 producer-profile scope; current 14180 is only the renderer.",
            "Checkpoints, unsupported flags and out-of-range fractions cannot establish eligible command support."]}


def _render_contract(render, render_path, records, watch):
    profile = get_native_replay_profile(CURRENT_PROFILE)
    _require(render.get("renderer_profile") == RENDERER_PROFILE and render.get("fps") == 32 and
        render.get("capture_method") == "native-windows-cs2-startmovie-tga", "Unsupported competitive render contract")
    _require(render.get("binary_profile") == dict(profile["binary_profile"]), "Render binary profile disagrees with the inspected current build")
    for relative, expected in profile["binary_profile"].items():
        watch(RENDER_BINARIES/relative, expected)
    _require(records and header_matches_profile(records[0], native_profile=CURRENT_PROFILE), "Native ledger profile disagrees with current replay contract")
    for key in ("plugin_sha256", "plugin_source_sha256", "plugin_staged_sha256"):
        _require(render.get(key) == PLUGIN_SHA256, "Unreviewed native competitive plugin bytes")
    sandbox = Path(render.get("archived_game_mod_dir", "")).resolve()
    _require(sandbox.is_relative_to(render_path.parent.resolve()), "Native plugin archive escapes its render run")
    watch(sandbox/"bin/win64/server.dll", PLUGIN_SHA256)
    watch(PROFILE_COMPARISON, PROFILE_COMPARISON_SHA256)
    comparison = read_json(PROFILE_COMPARISON)
    for module in comparison["modules"].values():
        watch(Path(module["path"]), module["sha256"])
    _require(type(render.get("cs2_exit_code")) is int and render["cs2_exit_code"] == 0 and
        all(render.get(key) is True for key in ("settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")) and
        "-insecure" in render.get("launch_arguments", []) and "-chicken-competitive-replay" in render["launch_arguments"],
        "Protected competitive capture did not finish its scoped lifecycle")
    protection = _protected_archive(render, render_path.parent, watch)
    override = render.get("hud_override", {})
    relative = Path(override.get("staged_relative_path", ""))
    resource = (sandbox/relative).resolve()
    _require(not relative.is_absolute() and relative.parts and resource.is_relative_to(sandbox) and resource != sandbox,
        "HUD resource archive path escapes its run")
    override_digest = watch(resource, override.get("override_resource_sha256"))
    delivery_files = {str(relative): override_digest}
    _require(override.get("delivery") == "identical_loose_and_chunked_vpk_resource", "Current competitive HUD requires its explicit private archive delivery")
    for prefix, filename in (("private_archive", "pakchicken_hud_dir.vpk"), ("private_chunk", "pakchicken_hud_000.vpk")):
        _require(override.get(prefix+"_relative_path") == filename, "Unexpected private HUD archive path")
        delivery_files[filename] = watch(sandbox/filename, override.get(prefix+"_sha256"))
    _require(delivery_files["pakchicken_hud_000.vpk"] == override_digest, "Private HUD chunk differs from its staged resource")
    _require(override.get("installed_resources_modified") is False and override.get("raw_frames_masked") is False and
        render.get("hud_profile", {}).get("raw_frames_masked") is False, "Competitive raw-frame HUD provenance is unsupported")
    return {"native_profile": CURRENT_PROFILE, "binary_profile": dict(profile["binary_profile"]),
        "plugin_sha256": PLUGIN_SHA256, "renderer_profile": RENDERER_PROFILE,
        "profile_comparison_sha256": PROFILE_COMPARISON_SHA256, "hud_override_sha256": override_digest,
        "hud_delivery_files": delivery_files,
        "protected_capture_policy": protection,
        "hud_resource_load_verified_by_staging": False, "protected_capture_archive_verified": True}


def _protected_archive(render, run, watch):
    settings_path, gameinfo_path = Path(render["settings_recovery_journal"]), Path(render["recovery_journal"])
    for path in (settings_path, gameinfo_path):
        _require(path.resolve().parent == run.resolve(), "Protected capture journal belongs to another run")
        watch(path)
    settings, gameinfo = read_json(settings_path), read_json(gameinfo_path)
    _require(all(value.get("state") == "restored" and value.get("run_id") == render.get("run_id")
        for value in (settings, gameinfo)), "Protected capture journals do not confirm the completed run")
    _require(_same(render.get("settings_restore_verified"), settings), "Render settings proof disagrees with its archived journal")
    original, post = settings.get("original", {}).get("files"), settings.get("post", {}).get("files")
    _require(isinstance(original, list) and original and isinstance(post, list) and
        settings.get("active_operation") is None and settings.get("completed_operations") == settings.get("restored_operations"),
        "Incomplete protected settings snapshots or restoration operations")
    for label, entries in (("settings-backup", original), ("settings-post", post)):
        indices = [entry.get("backup_index") for entry in entries]
        _require(all(type(index) is int for index in indices) and indices == list(range(len(entries))),
            "Protected settings snapshot indices are ambiguous")
        for entry in entries:
            watch(run/label/f"{entry['backup_index']:06d}.bin", entry["sha256"])
    watch(Path(gameinfo["backup_path"]), gameinfo["original_sha256"])
    mounting = _gameinfo_mount_proof(Path(gameinfo["backup_path"]).read_bytes(), gameinfo, render["run_id"])
    isolation = render.get("settings_isolation", {})
    watch(Path(isolation["proof_path"]), isolation["proof_sha256"])
    saved = read_json(Path(isolation["proof_path"]))
    _require(all(_same(value, saved.get(key)) for key, value in isolation.items() if key not in ("proof_path", "proof_sha256")) and
        saved.get("startup_guard_passed") is True and saved.get("local_path_verified") is True and
        saved.get("pid") == render.get("owned_cs2_pid"), "Protected settings isolation proof disagrees")
    return {"hud_archive_mount": mounting, "settings_journal_sha256": watch(settings_path),
        "gameinfo_journal_sha256": watch(gameinfo_path), "archived_original_settings_files": len(original),
        "archived_post_settings_files": len(post), "runtime_cleanup_scope": "retained_completed_run_journals_and_snapshot_bytes"}


def _gameinfo_mount_proof(original, journal, run_id):
    _require(isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id) and
        journal.get("mod_name") == "chicken-render-"+run_id and
        journal.get("hud_archive_profile") == HUD_ARCHIVE_SEARCH_PROFILE,
        "Protected HUD archive mount profile or owned directory disagrees")
    _require(hashlib.sha256(original).hexdigest() == journal.get("original_sha256") and b"csgo/chicken-render-" not in original,
        "Protected original GameInfo bytes disagree or contain prior staging")
    matches = list(re.finditer(rb'(?m)^([ \t]*)(?:"SearchPaths"|SearchPaths)[ \t]*(?:\r?\n[ \t]*)?\{[ \t]*(\r?\n)', original))
    _require(len(matches) == 1, "Protected GameInfo has an ambiguous SearchPaths block")
    match = matches[0]
    directory = b"csgo/"+journal["mod_name"].encode("ascii")
    # The first real Game directory owns write/GAMEBIN selection. Archive
    # entries follow it, with their resource precedence checked separately.
    lines = [(b"Game", directory), (b"Game", directory+b"/pakchicken_hud.vpk"), (b"Mod", directory+b"/pakchicken_hud.vpk")]
    insertion = b"".join(match.group(1)+b"\t"+kind+b"\t"+path+match.group(2) for kind, path in lines)
    patched = original[:match.end()]+insertion+original[match.end():]
    digest = hashlib.sha256(patched).hexdigest()
    _require(journal.get("patched_sha256") == digest, "Protected GameInfo patch differs from the fixed HUD mount policy")
    return {"profile": HUD_ARCHIVE_SEARCH_PROFILE, "patched_sha256": digest,
        "archive_relative_path": "pakchicken_hud.vpk", "archive_search_paths": ["Game", "Mod"],
        "mounting_alone_does_not_prove_resource_use": True}


def _hud_review(render, inventory, watch):
    """Read a historical review for optional diagnostics, never as a live gate."""
    registered = HUD_REVIEWS.get(render["capture_ledger_sha256"])
    if registered is None:
        return {"status": "unknown", "reason_codes": ["competitive_hud_visual_review_unavailable"],
            "verified_frame_indices": [], "resource_staging_is_not_visual_proof": True}
    path, expected = registered
    path = PROJECT/path
    watch(path, expected)
    review = read_json(path)
    _require(review.get("profile") == "cs2-competitive-pilot-hud-review-v1" and
        review.get("capture_ledger_sha256") == render["capture_ledger_sha256"] and
        review.get("capture_frame_files_sha256") == render["capture_frame_files_sha256"] and
        review.get("hud_override_sha256") == render["hud_override"]["override_resource_sha256"] and
        review.get("plugin_sha256") == PLUGIN_SHA256, "Fixed HUD review refers to another capture or resource")
    expected_frames = [{"frame_index": n, "sha256": frame["sha256"]} for n, frame in enumerate(inventory)]
    _require(_same(review.get("frames"), expected_frames) and review.get("all_frames_reviewed") is True and
        review.get("spectator_only_information_absent") is True and review.get("player_visible_hud_preserved") is True,
        "Fixed HUD review does not cover the complete original image sequence")
    return {"status": "verified", "reason_codes": [], "review_path": str(path.resolve()), "review_sha256": expected,
        "verified_frame_indices": list(range(len(inventory))), "scope": "fixed_manually_reviewed_original_pilot_images_only"}


def _indexed(rows, key, count, description):
    result = {}
    for row in rows:
        index = row.get(key)
        _require(type(index) is int and 0 <= index < count and index not in result, "Duplicate or invalid " + description)
        result[index] = row
    return result


def _frame_proofs(frames, inventory, records, synchronization, bounds, render, hud):
    count = len(frames)
    hud_setup_allowed = policy_allows_capture(hud)
    movies = _indexed([r for r in records if r.get("event") == "movie_frame"], "capture_index", count, "native movie identity")
    native = _indexed([r for r in synchronization["native_message_clock_audit"]["frames"] if r.get("event") == "movie_frame"],
        "frame_index", count, "native clock identity")
    pov = _indexed(synchronization["pov_evidence"], "frame_index", count, "native POV identity")
    packets = _indexed(bounds.get("frames", []), "capture_index", count, "native packet identity")
    result = []
    for index, frame in enumerate(frames):
        reasons = set()
        movie, message, observed, packet = movies.get(index, {}), native.get(index, {}), pov.get(index, {}), packets.get(index, {})
        if synchronization["pixel_correspondence"].get("verified") is not True:
            reasons.add("pixel_correspondence_unverified")
        if message.get("status") != "matched_message_clocks":
            reasons.add("native_message_clock_association_unverified")
        if observed.get("status") != "passed":
            reasons.add("native_first_person_pov_unverified")
            reasons.update(observed.get("reason_codes", []))
        if packet.get("status") != "verified" or packet.get("verified") is not True or any(
            type(packet.get(key)) is not int or packet[key] < 0 for key in ("upper_server_tick", "upper_source_demo_tick")):
            reasons.add("packet_information_bound_unknown")
            reasons.update(packet.get("reasons", []))
        native_observation = movie.get("native_observation", {})
        native_clock = movie.get("native_clock", {})
        native_after = movie.get("native_clock_after", {})
        native_pov = native_observation.get("observed_pov", {})
        if native_observation.get("demo_paused") is not False:
            reasons.add("native_demo_pause_unknown_or_active")
        values = [native_clock.get("client_address"), native_clock.get("client_generation"), movie.get("demo_start_tick"),
            movie.get("thread_id"), native_pov.get("controller_handle"), native_pov.get("pawn_handle")]
        if any(type(value) is not int for value in values):
            reasons.add("native_clock_or_pawn_segment_unavailable")
        if any(not _same(native_clock.get(key), native_after.get(key)) for key in ("client_address", "client_generation")):
            reasons.add("native_clock_segment_changed_during_submission")
        segment = _digest([render["run_id"], str(native_pov.get("steam_id")), *values])
        start, end = frame.get("render_time_seconds_start"), frame.get("render_time_seconds_end")
        if (type(start) not in (int, float) or type(end) not in (int, float) or
            not math.isfinite(start) or not math.isfinite(end) or
            not 0 <= start < end or abs((end-start)-.03125) > .0001):
            reasons.add("observed_32hz_render_cadence_unverified")
        if not hud_setup_allowed:
            reasons.add("competitive_hud_capture_setup_unsupported")
        image = inventory[index]
        _require(type(image.get("capture_index")) is int and image["capture_index"] == index,
            "Competitive image inventory index mismatch")
        result.append({"frame_index": index, "path": image["path"], "sha256": image["sha256"],
            "verified": not reasons, "reason_codes": sorted(reasons), "clock_segment_id": segment,
            "source_demo_tick_start": frame["source_demo_tick_start"], "source_demo_tick_end": frame["source_demo_tick_end"],
            "upper_source_demo_tick": packet.get("upper_source_demo_tick"),
            "observation_upper_execution_tick": packet.get("upper_server_tick")})
    return result


def _command_proofs(commands, source, reconstructed, *, source_envelopes=None):
    envelopes = _source_envelopes(source) if source_envelopes is None else source_envelopes
    result = {}
    for row in commands:
        row_id = row["command_row_id"]
        _require(type(row_id) is int and row_id not in result, "Duplicate or invalid canonical command identity")
        reasons = []
        digest = canonical_row_sha256(row)
        if reconstructed["command_row_digests"].get(row_id) != digest:
            reasons.append("original_command_reconstruction_unavailable_or_different")
        envelope = _command_source(row, envelopes)
        if envelope is None:
            reasons.append("live_source_command_envelope_unavailable_or_ambiguous")
        result[row_id] = {"status": "unknown" if reasons else "verified", "reason_codes": reasons,
            "canonical_row_sha256": digest, "reconstruction_proof_sha256": reconstructed["proof_sha256"],
            "source_envelope": envelope}
    return result


def recompute_competitive_replay_proof(parsed: Path, dataset: Path, network_clock: Path, state_context: Path):
    parsed, dataset, network_clock, state_context = (Path(path).resolve() for path in (parsed, dataset, network_clock, state_context))
    sources = _Sources(); watch = sources.watch
    # Revalidation depends on these implementations as well as retained data.
    for name in ("competitive_replay_proof", "competitive_control", "competitive_source_verification", "competitive_buttons", "command_reverification", "control_labels", "control_label_audit",
        "causal_acceptance", "acceptance", "clock_evidence", "packet_evidence", "packet_bounds", "native_replay_profile",
        "synchronization", "timing", "validation", "jobs", "io", "normalize", "server_command_support", "hud_policy"):
        watch(Path(__file__).with_name(name+".py"))
    for path in (parsed/"manifest.json", dataset/"timing/clip.json", dataset/"timing/frames.jsonl", network_clock, state_context):
        watch(path)
    canonical = parsed_manifest(parsed, ("usercmd.parquet", "player_state.parquet", "rounds.parquet"))
    _require(canonical.get("tick_rate") == 64, "Competitive source requires the inspected 64 Hz processing profile")
    for name in ("usercmd.parquet", "player_state.parquet", "rounds.parquet"):
        watch(parsed/name, canonical["files"][name])
    demo_path = Path(canonical["source_path"])
    watch(demo_path, canonical["demo_id"])
    clip, frames = read_json(dataset/"timing/clip.json"), read_ledger(dataset/"timing/frames.jsonl")
    _require(1 <= len(frames) <= 10000 and all(type(frame.get("frame_index")) is int and frame["frame_index"] == index and
        all(type(frame.get(key)) is int for key in ("source_demo_tick_start", "source_demo_tick_end")) for index, frame in enumerate(frames)),
        "Competitive frame indices or observed ticks are invalid")
    identity = {key: clip[key] for key in IDENTITY}
    _require(identity["demo_id"] == canonical["demo_id"], "Competitive source and capture demo identity disagree")
    claim = clip.get("source_job", {}).get("phase_evidence", {})
    phase_path = Path(claim["source_phase_path"]) if claim.get("source_phase_path") else None
    capture = clip["capture_evidence"]
    for name in ("ledger", "render_manifest", "frame_inventory"):
        watch(Path(capture[name+"_path"]), capture[name+"_sha256"])
    render_path = Path(capture["render_manifest_path"])
    render, records = read_json(render_path), read_ledger(Path(capture["ledger_path"]))
    inventory = read_json(Path(capture["frame_inventory_path"]))["frames"]
    _require(len(inventory) == len(frames), "Competitive image inventory count mismatch")
    for image in inventory:
        watch(Path(image["path"]), image["sha256"])
    archive_path = Path(render["capture_frame_files"])
    watch(archive_path if archive_path.is_absolute() else render_path.parent/archive_path, render["capture_frame_files_sha256"])
    render_contract = _render_contract(render, render_path, records, watch)
    hud = trusted_hud_policy(render)
    synchronization = recompute_synchronization(parsed, dataset, network_clock, native_profile=CURRENT_PROFILE)
    through = _scan_through(records, frames)
    _require(type(through) is int and 1 <= through <= 2_147_483_647, "Competitive source packet prefix is outside the demo tick domain")
    source = scan_demo_packets(demo_path, expected_sha256=canonical["demo_id"], through_demo_tick=through, native_profile=CURRENT_PROFILE)
    bounds = audit_packet_bounds(records, source, native_profile=CURRENT_PROFILE)
    _require(bounds.get("source_demo_sha256") == canonical["demo_id"], "Native packet bound does not identify its original source")
    support = _source_support(source, watch)
    context = load_state_context(state_context, canonical)
    segments = [dict(segment, _pause_evidence_verified=context["pause_evidence_verified"] is True) for segment in context["segments"]]
    round_rows = pq.read_table(parsed/"rounds.parquet").to_pylist()
    rounds = {row["round_id"]: row for row in round_rows}
    _require(len(rounds) == len(round_rows), "Duplicate canonical round identity")
    if phase_path is not None:
        watch(phase_path, claim.get("source_phase_sha256"))
    phase = phase_evidence(phase_path, canonical, rounds).get(identity["round_id"], {})
    condition = ((ds.field("demo_id") == identity["demo_id"]) & (ds.field("round_id") == identity["round_id"]) &
        (ds.field("player_slot") == identity["player_slot"]) & (ds.field("steam_id") == int(identity["steam_id"])))
    commands = ds.dataset(parsed/"usercmd.parquet").to_table(filter=condition).to_pylist()
    commands.sort(key=lambda row: row["command_row_id"])
    states = ds.dataset(parsed/"player_state.parquet").to_table(filter=condition).to_pylist()
    _require(len({row["demo_tick"] for row in states}) == len(states), "Duplicate canonical player-state tick")
    reconstructed = _source_eligibility(parsed, canonical, phase_path, state_context, commands, watch)
    envelopes = _source_envelopes(source)
    command_sources = _command_proofs(commands, source, reconstructed, source_envelopes=envelopes)
    from .competitive_buttons import _audit_bound_commands
    semantic_buttons = _audit_bound_commands(source, commands, reconstructed, watch, source_envelopes=envelopes)
    proofs = _frame_proofs(frames, inventory, records, synchronization, bounds, render, hud)
    provenance = {"profile": PROFILE, "render_contract": render_contract, "source_support": support, "hud": hud,
        "reviewed_source_eligibility": {"profile": reconstructed["profile"], "proof_sha256": reconstructed["proof_sha256"],
            "scope": "full_original_demo_commands_states_rounds_events_phase_and_context",
            "source_reconstruction_is_not_image_or_button_acceptance": True},
        "packet_source_sha256": _digest(source), "packet_bounds": bounds, "synchronization_sha256": _digest(synchronization),
        "pixel_correspondence": synchronization["pixel_correspondence"], "command_reconstruction": reconstructed["provenance"],
        "command_reconstruction_sha256": reconstructed["proof_sha256"], "phase": phase,
        "clock_segments": sorted({frame["clock_segment_id"] for frame in proofs}),
        "recorded_semantic_buttons_verified": semantic_buttons.get("status") == "verified", "exact_input_timing_verified": False,
        "recording_server_binary_identity_verified": False}
    sources.finish()
    return {"identity": identity, "clip_id": clip["clip_id"], "frames": proofs, "commands": commands,
        "command_sources": command_sources, "states": states, "round_info": rounds.get(identity["round_id"], {}),
        "context_segments": segments, "phase": phase, "source_support": support,
        "semantic_buttons": semantic_buttons, "provenance": provenance, "source_files": sources.files}
