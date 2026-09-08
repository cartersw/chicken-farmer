"""Source-bound meanings of recorded 14178 button states, never physical input.

The recovered server's enum, state update table, and native/protobuf conversion
are the authority. Local 14180 calibration is deliberately not an input. Exact
event count, order, offsets, input consumption, and training readiness stay false.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

import pyarrow.dataset as ds

from .causal_acceptance import _action_protobuf, _command_source, _source_envelopes
from .command_reverification import reverify_command_prefix
from .control_label_audit import canonical_row_sha256
from .io import exclusive_output, parsed_manifest, publish, sha256_file, staging_paths
from .packet_evidence import scan_demo_packets
from .server_command_support import IMAGE_BASE, MAX_BINARY_BYTES, _pe_sections, _slice

PROFILE = "cs2-14178-recorded-button-semantics-v1"
SOURCE_PATCH = 14178
PROJECT = Path(__file__).resolve().parents[2]
SERVER = PROJECT/"data/native-profiles/recovered-14178-v1/game/csgo/bin/win64/server.dll"
SERVER_SHA256 = "9e5749d77dcb68883477feae751a3f28068d119ec145edcb0e4d48d15b538d36"
BUTTON_MASKS = {"attack1": 1, "jump": 2, "crouch": 4, "forward": 8, "back": 16,
                "left": 512, "right": 1024, "reload": 8192}
NATIVE_NAMES = {"attack1": "IN_ATTACK", "jump": "IN_JUMP", "crouch": "IN_DUCK",
                "forward": "IN_FORWARD", "back": "IN_BACK", "left": "IN_MOVELEFT",
                "right": "IN_MOVERIGHT", "reload": "IN_RELOAD"}
FIELDS = ("held_start", "held_mid", "held_end", "net_changed", "net_pressed", "net_released",
          "recorded_activity_present", "unresolved_rapid_activity", "per_command_net_changed")
STATE_NAMES = ("IN_BUTTON_UP", "IN_BUTTON_DOWN", "IN_BUTTON_DOWN_UP", "IN_BUTTON_UP_DOWN",
    "IN_BUTTON_UP_DOWN_UP", "IN_BUTTON_DOWN_UP_DOWN", "IN_BUTTON_DOWN_UP_DOWN_UP", "IN_BUTTON_UP_DOWN_UP_DOWN")
# Pairs are SetButtonState(false), SetButtonState(true), indexed by the old
# three-plane code. Long histories reduce to earlier codes: this is not a count.
UPDATE_TABLE = ((0, 3), (2, 1), (2, 5), (4, 3), (4, 7), (6, 5), (6, 5), (4, 7))
RANGES = (
    ("button_mask_enum", 0x16b49f0, 608, "e29ab803b4062439a44994f7457b1226e8c745b71991f14df381d3f373989ed1"),
    ("button_state_enum", 0x16b4e40, 256, "a5d813daa44527b75bbc3bf32096d8dbf24008777788e79cdbaf5d732005fe7c"),
    ("state_update_table", 0x1c45da0, 64, "62d74f386825b622eee326d5fce7d9119439da2dc20d5e9b30322070e2098805"),
    ("state_update", 0x664590, 179, "d7eaeb6ddc8f874917b8d7a9ccd81a6aff41783e020d62de5904d9302571779a"),
    ("button_pb_to_native", 0x2dd1af, 40, "d1927f82bf768ced0850b60bac98606e6289d8f645b588f627c3626f2f09e778"),
    ("button_native_to_pb", 0x2dd3be, 128, "12aa2c36ecbe004d229fec4557f725c41b96e096e0382b78f1a90c0f81a8a21d"),
    ("button_pb_parser", 0x6cbe60, 603, "834e5223b99a65b36a313d8d6c833c217e6c7dbf19e072b0085224774197e4eb"),
    ("new_press_predicate", 0x679160, 182, "887660a716de592f3bda292b0ae8887e9654ecfac9e5bac078a0aa6901803416"),
    ("new_release_predicate", 0x679220, 182, "b879ca673f6c84e97e3b4586ae9d35b694038bdf5ce3ed3a257a88110e095a58"),
    ("movement_predicate_calls", 0xc16af0, 60, "38895a7ece0b0c8ee4f7183d1460f2fc6d0d563febc556df0ebbf9e6a01dbd25"),
)
LIMITS = [
    "Recorded command semantics only; no original physical key or mouse identity is inferred.",
    "The source header identifies patch14178, not the recording server DLL hash.",
    "Held labels refer to consecutive recorded command boundaries, not rendered camera time.",
    "Plane2 is net boundary change, not any activity; plane3 retains repeated-change information.",
    "Repeated-change codes reduce longer histories; exported subtick lists need not contain all original transitions.",
    "Activity denotes evidence retained by the command, not complete physical input or measured wall-clock speed.",
    "Raw list order, fractions and absence remain provenance; exact count, order and event times are not targets.",
    "No world-effect, replay-resimulation, image-causality, online-Cloud or training approval follows from this audit.",
]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uint(value):
    return type(value) is int and 0 <= value < 2**64


def _inspect_binary(watch):
    digest = watch(SERVER, SERVER_SHA256)
    _require(0 < SERVER.stat().st_size <= MAX_BINARY_BYTES, "Recording button binary exceeds size bound")
    data = SERVER.read_bytes()
    _require(hashlib.sha256(data).hexdigest() == digest == SERVER_SHA256,
             "Recovered14178 button binary changed")
    base, sections = _pe_sections(data)
    _require(base == IMAGE_BASE, "Recording button binary image base mismatch")
    for name, rva, size, expected in RANGES:
        _require(hashlib.sha256(_slice(data, sections, rva, size)).hexdigest() == expected,
                 "Recovered14178 button range changed: " + name)
    def text_at(pointer):
        raw = _slice(data, sections, pointer-base, 96)
        _require(b"\0" in raw, "Native button enum name has no terminator")
        return raw.split(b"\0", 1)[0].decode("ascii")
    enum_masks = {}
    for index in range(19):
        pointer, value = struct.unpack("<QQ", _slice(data, sections, 0x16b49f0+32*index, 16))
        enum_masks[text_at(pointer)] = value
    _require(all(enum_masks.get(NATIVE_NAMES[name]) == mask for name, mask in BUTTON_MASKS.items()),
             "Native button masks disagree with declared semantic names")
    for index, expected in enumerate(STATE_NAMES):
        pointer, value = struct.unpack("<QQ", _slice(data, sections, 0x16b4e40+32*index, 16))
        _require(text_at(pointer) == expected and value == index, "Native button state enum changed")
    table = struct.unpack("<16I", _slice(data, sections, 0x1c45da0, 64))
    _require(table == tuple(value for pair in UPDATE_TABLE for value in pair), "Native state update table changed")
    return {"server_path": str(SERVER.resolve()), "server_sha256": digest,
        "ranges": [{"name": n, "rva": r, "size": s, "sha256": h} for n, r, s, h in RANGES],
        "native_button_names": NATIVE_NAMES, "native_button_masks": BUTTON_MASKS,
        "native_state_names": list(STATE_NAMES), "native_state_update_table": [list(x) for x in UPDATE_TABLE],
        "plane_encoding": "state_code=bool(plane1&mask)+2*bool(plane2&mask)+4*bool(plane3&mask)",
        "protobuf_copy": "PB+18/+20/+28 <-> command+60/+68/+70; field tags1/2/3 independently parsed",
        "native_single_bit_press_predicate": "state not in {0,1,2}",
        "native_single_bit_release_predicate": "state not in {0,1,3}",
        "recording_server_binary_identity_verified": False}


def decode_native_button_state(planes, mask):
    """Decode the fixed enum; does not attest where caller-supplied values came from."""
    _require(isinstance(planes, (list, tuple)) and len(planes) == 3 and all(_uint(v) for v in planes),
             "Button state needs three unsigned64 planes")
    _require(type(mask) is int and mask in BUTTON_MASKS.values(), "Unsupported single semantic button mask")
    bits = [bool(value & mask) for value in planes]
    code = sum(int(value) << index for index, value in enumerate(bits))
    return {"state_code": code, "native_state_name": STATE_NAMES[code],
        "held_at_command_end": bits[0], "held_at_command_start": bits[0] ^ bits[1],
        "net_changed": bits[1], "net_pressed": bits[1] and bits[0], "net_released": bits[1] and not bits[0],
        "recorded_activity_present": bits[1] or bits[2], "unresolved_rapid_activity": bits[2]}


def _row_observation(row):
    reasons, _ = _action_protobuf(row)
    if row.get("buttons_present") is not True:
        reasons.add("button_parent_absent")
    raw = [row.get("buttonstate"+str(index)) for index in (1, 2, 3)]
    if any(value is not None and not _uint(value) for value in raw):
        reasons.add("invalid_button_plane")
    # Optional absent scalar zero is legitimate only within the verified present
    # parent, never substituted for a missing buttons message.
    planes = [0 if value is None else value for value in raw] if not reasons else None
    values = {}
    if planes is not None:
        for name, mask in BUTTON_MASKS.items():
            value = decode_native_button_state(planes, mask)
            matching = [index for index, step in enumerate(row["subtick_moves"])
                        if _uint(step.get("button")) and step["button"] & mask]
            value["recorded_subtick_indices"] = matching
            value["recorded_activity_present"] |= bool(matching)
            values[name] = value
    return {"command_row_id": row.get("command_row_id"), "command_number": row.get("command_number"),
        "demo_tick": row.get("demo_tick"), "server_tick_executed": row.get("server_tick_executed"),
        "buttons_parent_present": row.get("buttons_present") is True,
        "plane_scalar_presence": [value is not None for value in raw],
        "raw_planes_hex": [f"0x{v:016x}" if _uint(v) else None for v in raw],
        "canonical_row_sha256": canonical_row_sha256(row),
        "command_protobuf_sha256": hashlib.sha256(row["command_protobuf"]).hexdigest()
            if isinstance(row.get("command_protobuf"), bytes) else None,
        "values": values, "reason_codes": sorted(reasons)}


def _audit_bound_commands(source, commands, reconstructed, watch, *, source_envelopes=None):
    """Private composition after independent source decoding; never a JSON trust API.

The public entry point below owns reconstruction. The competitive replay verifier
may compose this with its fresh full-source reconstruction and source watcher.
"""
    _require(isinstance(commands, list) and 0 < len(commands) <= 300000,
             "Button proof requires a bounded nonempty command selection")
    ids = [row.get("command_row_id") for row in commands]
    _require(all(type(value) is int and value >= 0 for value in ids) and len(set(ids)) == len(ids) and ids == sorted(ids),
             "Button source row IDs are duplicate, invalid or reordered")
    header = source.get("file_header") or {}
    demo_id = source.get("source_demo_sha256")
    _require(_sha(demo_id) and _sha(reconstructed.get("proof_sha256")), "Missing source/reconstruction button identity")
    _require(reconstructed.get("provenance", {}).get("source_demo_sha256") == demo_id,
             "Button reconstruction belongs to another original demo")
    binary = _inspect_binary(watch)
    for name in ("competitive_buttons.py", "causal_acceptance.py", "server_command_support.py", "control_label_audit.py"):
        watch(Path(__file__).with_name(name))
    for path, expected in reconstructed["source_files"].items():
        watch(Path(path), expected)
    source_ok = type(header.get("patch_version")) is int and header["patch_version"] == SOURCE_PATCH
    envelopes = _source_envelopes(source) if source_envelopes is None else source_envelopes
    row_proofs, state_counts, previous = {}, {name: Counter() for name in BUTTON_MASKS}, {}
    totals = Counter(); examples = []
    for row in commands:
        item = _row_observation(row)
        reasons = set(item["reason_codes"])
        if not source_ok:
            reasons.add("recording_source_patch_unsupported")
        if row.get("demo_id") != demo_id:
            reasons.add("command_demo_identity_mismatch")
        if reconstructed["command_row_digests"].get(row["command_row_id"]) != item["canonical_row_sha256"]:
            reasons.add("original_command_reconstruction_unavailable_or_different")
        origin = _command_source(row, envelopes)
        if origin is None:
            reasons.add("live_source_envelope_absent_or_ambiguous")
        fields = {name: list(FIELDS) if not reasons else [] for name in BUTTON_MASKS}
        field_reasons = {name: sorted(reasons) for name in BUTTON_MASKS}
        identity = tuple(row.get(name) for name in ("demo_id", "round_id", "steam_id", "player_slot", "pawn_entity_handle"))
        prior = previous.get(identity)
        contiguous = prior is not None and all(type(row.get(name)) is int and type(prior[0].get(name)) is int and
            row[name] == prior[0][name]+1 for name in ("command_number", "server_tick_executed", "demo_tick"))
        for name, value in item["values"].items():
            state_counts[name][str(value["state_code"])] += 1
            if contiguous and name in prior[1]["values"]:
                totals["known_boundary_comparisons"] += 1
                if prior[1]["values"][name]["held_at_command_end"] != value["held_at_command_start"]:
                    fields[name] = []
                    field_reasons[name] = sorted(set(field_reasons[name]) | {"plane2_disagrees_with_known_held_boundaries"})
                    totals["known_boundary_mismatches"] += 1
            if value["unresolved_rapid_activity"] and len(examples) < 24:
                examples.append({"command_row_id": row["command_row_id"], "control": name, **value})
        previous[identity] = row, item
        status = "verified" if any(fields.values()) else "unknown"
        totals[status+"_rows"] += 1
        totals["missing_button_parent_rows"] += not item["buttons_parent_present"]
        row_proofs[row["command_row_id"]] = {**item, "status": status,
            "supported_fields": fields, "field_reason_codes": field_reasons,
            "reason_codes": sorted(reasons), "live_source_envelope": origin}
    supported = {name: list(FIELDS) if source_ok else [] for name in BUTTON_MASKS}
    provenance = {"profile": PROFILE, "demo_id": demo_id, "source_patch": header.get("patch_version"),
        "source_file_header_sha256": header.get("protobuf_sha256"), "static_binary": binary,
        "source_packet_inventory_sha256": _digest(source), "reconstruction_proof_sha256": reconstructed["proof_sha256"],
        "reconstructed_source_row_count": len(reconstructed["command_row_digests"]),
        "selected_command_count": len(commands), "row_proofs_sha256": _digest(row_proofs),
        "supported_fields": supported, "limits": LIMITS}
    return {"schema_version": 1, "profile": PROFILE, "status": "verified" if source_ok and totals["verified_rows"] else "unknown",
        "demo_id": demo_id, "source_patch": header.get("patch_version"),
        "reason_codes": [] if source_ok else ["recording_source_patch_unsupported"],
        "supported_button_masks": dict(BUTTON_MASKS) if source_ok else {}, "supported_fields": supported,
        "row_proofs": row_proofs, "summary": {**totals, "state_code_counts": state_counts,
            "recorded_repeated_activity_examples": examples},
        "provenance": provenance, "proof_sha256": _digest(provenance),
        "recording_server_binary_identity_verified": False, "physical_key_mapping_verified": False,
        "exact_button_event_count_verified": False, "exact_button_event_order_verified": False,
        "exact_input_timing_verified": False, "training_ready": False, "live_control_ready": False}


def recompute_competitive_button_evidence(source_demo, parsed, *, through_demo_tick, start_demo_tick=0):
    """Reconstruct source bytes afresh and attest selected recorded button fields."""
    source_demo, parsed = Path(source_demo).resolve(), Path(parsed).resolve()
    _require(type(start_demo_tick) is int and type(through_demo_tick) is int and 0 <= start_demo_tick < through_demo_tick,
             "Button audit tick window must be nonempty and nonnegative")
    watched = {}
    def watch(path, expected=None):
        path = Path(path).resolve(); digest = sha256_file(path)
        _require(expected is None or expected == digest, "Button source hash mismatch: " + str(path))
        _require(str(path) not in watched or watched[str(path)] == digest, "Button source changed during verification")
        watched[str(path)] = digest
        return digest
    canonical = parsed_manifest(parsed, ("usercmd.parquet",))
    watch(source_demo, canonical["demo_id"])
    watch(parsed/"manifest.json")
    watch(parsed/"usercmd.parquet", canonical["files"]["usercmd.parquet"])
    reconstructed = reverify_command_prefix(source_demo, parsed, through_demo_tick)
    source = scan_demo_packets(source_demo, expected_sha256=canonical["demo_id"], through_demo_tick=through_demo_tick)
    commands = ds.dataset(parsed/"usercmd.parquet").to_table(filter=(ds.field("demo_tick") >= start_demo_tick) &
        (ds.field("demo_tick") < through_demo_tick)).to_pylist()
    report = _audit_bound_commands(source, commands, reconstructed, watch)
    _require(all(sha256_file(Path(path)) == digest for path, digest in watched.items()), "Button sources changed during audit")
    report["source_files"] = watched
    return report


@exclusive_output()
def write_competitive_button_evidence(source_demo: Path, parsed: Path, *, through_demo_tick: int,
                                     out: Path, start_demo_tick: int = 0):
    paths = [out/"report.md", out/"report.json"]
    staged = staging_paths(paths)
    try:
        report = recompute_competitive_button_evidence(source_demo, parsed,
            through_demo_tick=through_demo_tick, start_demo_tick=start_demo_tick)
        staged[1].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        summary = report["summary"]
        text = ("# Original14178 recorded button evidence\n\n"
            f"Status: **{report['status']}** within the recorded command profile. "
            "This is not physical input or training approval.\n\n"
            f"Demo: `{report['demo_id']}`. Selected ticks [{start_demo_tick}, {through_demo_tick}). "
            f"Rows: {report['provenance']['selected_command_count']}; "
            f"verified: {summary.get('verified_rows', 0)}; unknown: {summary.get('unknown_rows', 0)}.\n\n"
            f"Known per-button boundary comparisons: {summary.get('known_boundary_comparisons', 0)}; "
            f"mismatches: {summary.get('known_boundary_mismatches', 0)}. Missing button parents stay unknown.\n\n"
            "Native enum and code bytes prove field1 is final held state, field2 is net change, "
            "and field3 records repeated-change activity. The original14178 button masks are "
            "read from its own schema enum. The update table reduces longer histories, so "
            "exact event count and order cannot be recovered from these planes.\n\n"
            "| Code | Native state |\n| --- | --- |\n"+
            "".join(f"| {i} | `{name}` |\n" for i, name in enumerate(STATE_NAMES))+
            "\nThese are native state names, not a lossless transcript of every original transition.\n\n"+
            "\n".join("- "+limit for limit in LIMITS)+
            "\n\nThe report pins the recovered DLL, individual code/enum ranges, original "
            "demo, decoder, canonical rows and fresh live packet envelope associations. "
            "Per-row field allowlists are required; the global mask list alone is insufficient.\n")
        text += ("\nNative evidence uses the recovered original server, not the local calibration: "
            "mask enum RVA `0x16b49f0`, eight-state enum `0x16b4e40`, "
            "state setter `0x664590` and table `0x1c45da0`, "
            "protobuf/native copies `0x2dd1af` / `0x2dd3be`, and "
            "protobuf parser `0x6cbe60`. The parser independently identifies wire "
            "fields 1/2/3; the copies establish the same three planes used by the setter. "
            "Native press/release predicates `0x679160` / `0x679220` are called "
            "by movement code at `0xc16af0`. Every inspected byte range is hashed in report.json.\n")
        staged[0].write_text(text, encoding="utf-8")
        _require(all(sha256_file(Path(path)) == digest for path, digest in report["source_files"].items()),
                 "Button sources changed before publication")
        publish(staged, paths)
        return report
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, required=True)
    parser.add_argument("--parsed", type=Path, required=True)
    parser.add_argument("--through-demo-tick", type=int, required=True)
    parser.add_argument("--start-demo-tick", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = write_competitive_button_evidence(args.demo, args.parsed, through_demo_tick=args.through_demo_tick,
        start_demo_tick=args.start_demo_tick, out=args.out)
    print(json.dumps({"status": report["status"], "proof_sha256": report["proof_sha256"],
        "verified_rows": report["summary"].get("verified_rows", 0), "training_ready": False}))


if __name__ == "__main__":
    main()
