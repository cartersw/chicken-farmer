"""Reconstruct a bounded original demo prefix and compare immutable command rows.

The inspected Go executable owns full/delta baseline reconstruction. A second
invocation starts at the beginning of the original demo; existing Parquet hashes
or matching envelope clocks alone are never treated as proof of payload origin.
This verifies decoder output, not recording-build support or physical input time.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pyarrow.dataset as ds

from .control_label_audit import canonical_row_sha256
from .io import batches, parsed_manifest, read_json, sha256_file

PROFILE = "cs2-original-prefix-command-reconstruction-v1"
PROJECT = Path(__file__).resolve().parents[2]
EXTRACTOR_SHA256 = "94fbee3859970e846f679e816de0a133aaa9856f3f1539f209cd6d0dc1013199"
SOURCE_SHA256 = {
    "go.mod": "c1c27c9df4a6070864497f471436c363c54b545b56d81c94d6ffcf6e33be3073",
    "go.sum": "697af9a4d16f491e1e801bea4558b94454a7b9ea344ab62caea120e4d77e4efa",
    "cmd/cs2-extract/main.go": "e0b4c485efa3e4505e7a7b94d38434b28bcd46547e96ea6cffc1ef97ad170f67",
    "internal/demo/extract.go": "0df9a822a65ce8da1c24b1b0bdbc2fef1ae5ae2316e250613fd9a990210e22d7",
    "internal/schema/schema.go": "7e94d8726a01fdd06d758e43fdcf3f0dad2434465e6f52b8b0f9c7ac17465c73",
    "internal/validate/validate.go": "20c32949634237258f1a3b930cc8b627e28935b2c94046e8eae7f3f15a5acf9c",
}
DECODER_FIELDS = {"parser_version": "v6.0.0-alpha.0", "parser_schema_version": "2", "extractor_version": "0.1.2"}
# The demo wire clock is signed int32. This is a format bound, not a pilot limit.
MAX_PREFIX_TICK = 2**31 - 1
MAX_PREFIX_COMMANDS = 20_000_000


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _decoder_files():
    paths = {PROJECT/"bin/cs2-extract.exe": EXTRACTOR_SHA256,
        **{PROJECT/"tools/usercmd-extractor"/name: digest for name, digest in SOURCE_SHA256.items()}}
    _require(all(path.is_file() and sha256_file(path) == digest for path, digest in paths.items()),
        "Inspected extractor executable or Go/module source bytes changed")
    return {str(path.resolve()): digest for path, digest in paths.items()}


@contextmanager
def _temporary_prefix():
    root = (PROJECT/".cache/command-reverification").resolve()
    _require(root.is_relative_to(PROJECT.resolve()), "Command verification cache escapes the workspace")
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="prefix-", dir=root)).resolve()
    _require(temporary.is_relative_to(root) and temporary != root, "Unsafe temporary prefix path")
    try:
        yield temporary
    finally:
        # Recursive cleanup is restricted to this exact newly created directory.
        resolved = temporary.resolve()
        _require(resolved == temporary and resolved.is_relative_to(root) and resolved != root,
            "Temporary prefix path changed before cleanup")
        shutil.rmtree(resolved)


def _extract(source, match_id, through, output):
    arguments = [str(PROJECT/"bin/cs2-extract.exe"), "extract", "--input", str(source),
        "--out", str(output), "--match-id", match_id, "--max-demo-tick", str(through)]
    result = subprocess.run(arguments, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=3600 if through == 0 or through > 20000 else 300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    _require(result.returncode in (0, 1), "Fresh source extraction did not complete its bounded diagnostic invocation")
    return {"exit_code": result.returncode,
        "exit_code_meaning": "1 may report expected partial/global quality rejection; prefix files and rows are checked independently"}


def compare_command_rows(original, reconstructed):
    """Exact complete-row comparison; no ignored raw, clock or state columns."""
    _require(0 < len(reconstructed) <= MAX_PREFIX_COMMANDS and len(original) == len(reconstructed),
        "Original and reconstructed command-prefix row counts disagree")
    original_ids = [row.get("command_row_id") for row in original]
    fresh_ids = [row.get("command_row_id") for row in reconstructed]
    _require(all(type(value) is int for value in original_ids + fresh_ids) and
        original_ids == fresh_ids == list(range(len(reconstructed))),
        "Command-prefix row IDs are not complete and sequential from the source beginning")
    hashes = {}
    for original_row, fresh_row in zip(original, reconstructed):
        digest = canonical_row_sha256(fresh_row)
        _require(canonical_row_sha256(original_row) == digest,
            "Canonical command differs from fresh original-demo reconstruction at row " + str(fresh_row["command_row_id"]))
        hashes[fresh_row["command_row_id"]] = digest
    return hashes


def _check_prefix(parsed, fresh, canonical, through):
    manifest = read_json(fresh/"manifest.json")
    _require(manifest.get("demo_id") == canonical["demo_id"] and manifest.get("sha256") == canonical["demo_id"] and
        manifest.get("match_id") == canonical.get("match_id") and manifest.get("tick_rate") == 64,
        "Fresh prefix source or match identity differs")
    for name, expected in DECODER_FIELDS.items():
        _require(str(manifest.get(name)) == expected, "Fresh prefix uses an unsupported decoder profile")
    _require((manifest.get("parse_status"), manifest.get("partial")) in (("partial", True), ("complete", False)),
        "Fresh extraction did not publish a complete bounded prefix")
    for name in ("usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"):
        _require(sha256_file(fresh/name) == manifest.get("files", {}).get(name), "Fresh prefix file hash disagrees with its manifest")
    ticks = [row["demo_tick"] for batch in batches(fresh/"player_state.parquet", columns=["demo_tick"]) for row in batch]
    _require(ticks and all(type(tick) is int for tick in ticks), "Fresh prefix has no observed state coverage")
    last_tick = max(ticks)
    _require(last_tick >= through, "Fresh source prefix stopped before the requested tick")
    reconstructed = [row for batch in batches(fresh/"usercmd.parquet") for row in batch]
    _require(len(reconstructed) == manifest.get("command_count") and 0 < len(reconstructed) <= MAX_PREFIX_COMMANDS,
        "Fresh prefix command count disagrees with manifest or exceeds bounded scope")
    # Cancellation occurs after FrameDone. A packet can share a demo tick with
    # its successor, so the final observed tick may be incomplete as a prefix
    # of the FULL parse. Keep rows strictly before that boundary for proof.
    compared = [row for row in reconstructed if type(row.get("demo_tick")) is int and row["demo_tick"] < through]
    _require(compared and [r["command_row_id"] for r in compared] == list(range(len(compared))),
        "Fresh command prefix contains reordered or regressed delivery ticks")
    original = ds.dataset(parsed/"usercmd.parquet").to_table(filter=ds.field("demo_tick") < through).to_pylist()
    digests = compare_command_rows(original, compared)
    return {"command_row_digests": digests, "compared_command_count": len(compared), "fresh_command_count": len(reconstructed),
        "source_demo_tick_end_exclusive": through, "fresh_last_observed_state_tick": last_tick,
        "fresh_parse_status": manifest["parse_status"], "fresh_partial": manifest["partial"],
        "fresh_warning_counts": manifest.get("warnings", {}), "fresh_payload_counts": {key: manifest.get(key) for key in
            ("full_payload_count", "delta_payload_count", "eligible_payload_count", "ineligible_payload_count")},
        "compared_row_digest_sha256": _digest([[key, value] for key, value in digests.items()])}


def reverify_command_prefix(source_demo: Path, parsed: Path, through_demo_tick: int):
    """Freshly decode through a bound, verifying all original rows before it.

Only ``demo_tick < through_demo_tick`` receives proof, because cancellation can
occur between packets with the same tick. Choose a small margin after all needed
commands. Temporary decoder artifacts are never published as training data.
"""
    source, parsed = Path(source_demo).resolve(), Path(parsed).resolve()
    _require(type(through_demo_tick) is int and 1 <= through_demo_tick <= MAX_PREFIX_TICK,
        "Command reconstruction prefix must end within positive signed-int32 demo ticks")
    watched = _decoder_files()
    canonical = parsed_manifest(parsed, ("usercmd.parquet",))
    _require(canonical.get("tick_rate") == 64 and isinstance(canonical.get("match_id"), str),
        "Command reconstruction needs a 64 Hz source with explicit match ID")
    for name, expected in DECODER_FIELDS.items():
        _require(str(canonical.get(name)) == expected, "Canonical commands use an unsupported decoder profile")
    _require(source.is_file() and sha256_file(source) == canonical["demo_id"], "Original demo bytes do not match canonical source")
    watched.update({str(source): canonical["demo_id"], str(parsed/"manifest.json"): sha256_file(parsed/"manifest.json"),
        str(parsed/"usercmd.parquet"): canonical["files"]["usercmd.parquet"], str(Path(__file__).resolve()): sha256_file(Path(__file__))})
    with _temporary_prefix() as temporary:
        result = _extract(source, canonical["match_id"], through_demo_tick, temporary)
        fresh = temporary/canonical["demo_id"]
        _require(fresh.is_dir() and (fresh/"manifest.json").is_file(), "Fresh extractor did not publish its bounded prefix")
        compared = _check_prefix(parsed, fresh, canonical, through_demo_tick)
    _require(all(sha256_file(Path(path)) == digest for path, digest in watched.items()), "Source/decoder changed during prefix reconstruction")
    provenance = {"profile": PROFILE, "source_demo_sha256": canonical["demo_id"], "match_id": canonical["match_id"],
        "source_demo_tick_end_exclusive": through_demo_tick, "decoder_profile": DECODER_FIELDS,
        "extractor_sha256": EXTRACTOR_SHA256, "decoder_source_sha256": SOURCE_SHA256,
        "comparison": {key: value for key, value in compared.items() if key != "command_row_digests"},
        "command_row_comparison": "all_columns_all_scalar_types_nullable_presence_raw_protobuf_and_source_row_order",
        "ignored_command_columns": [], "temporary_manifest_fields_not_compared": ["ingested_at", "parse_status", "partial", "files", "aggregate_counts_and_warnings"],
        "partial_prefix_scope": "strictly_before_requested_stop_tick; this is source reconstruction evidence, not a complete production parse",
        **result}
    return {"schema_version": 1, "profile": PROFILE, "status": "original_command_prefix_reconstructed_exactly",
        "provenance": provenance, "proof_sha256": _digest(provenance), "source_files": watched,
        "command_row_digests": compared["command_row_digests"],
        "command_sources": {row_id: {"status": "verified", "reason_codes": [], "canonical_row_sha256": digest,
            "reconstruction_proof_sha256": _digest(provenance), "live_packet_envelope_association_required": True}
            for row_id, digest in compared["command_row_digests"].items()},
        "recorded_server_build_support_verified": False, "training_ready": False}
