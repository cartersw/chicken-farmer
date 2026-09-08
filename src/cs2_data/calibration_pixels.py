"""Reconcile completed local-calibration TGA pixels with native readback evidence.

This checks the explicit current calibration profile. It neither rewrites a
ledger header nor applies the historical replay's timing/acceptance profile.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct

from .calibration_analysis import _read_ledger, _read_object
from .io import exclusive_output, publish, sha256_file, staging_paths
from .timing import verify_readback_pixels

NATIVE_PROFILE = "cs2-14180-calibration-v1"
BINARIES = {
    "bin/win64/engine2.dll": "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07",
    "csgo/bin/win64/client.dll": "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae",
    "bin/win64/rendersystemdx11.dll": "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500",
    "bin/win64/schemasystem.dll": "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512",
    "bin/win64/filesystem_stdio.dll": "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _owned(run, relative):
    path = run / relative
    _require(path.resolve().is_relative_to(run), "Evidence path escapes the calibration run")
    _require(path.is_file(), "Missing calibration evidence: " + relative)
    return path


def _ticks(rows, movie=False):
    values = []
    for row in rows:
        observation = row.get("native_observation", {}) if movie else row
        value = observation.get("local_player", {}).get("controller_tick_base")
        _require(_integer(value), "Missing or invalid observed local controller tick base")
        values.append(value)
    _require(bool(values), "No observed controller ticks")
    return {"observations": len(values), "unique_values": len(set(values)),
            "first": values[0], "last": values[-1],
            "successive_tick_delta_counts": {str(k): v for k, v in
                sorted(Counter(b-a for a, b in zip(values, values[1:])).items())}}


def _audit(run):
    names = ("calibration.json", "calibration_ledger.jsonl", "capture_ledger.jsonl",
             "capture_frame_files.json", "controlled.dem")
    paths = {name: _owned(run, name) for name in names}
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    worker = _read_object(paths["calibration.json"])
    archive = _read_object(paths["capture_frame_files.json"])
    captures = _read_ledger(paths["capture_ledger.jsonl"])
    controls = _read_ledger(paths["calibration_ledger.jsonl"])
    _require(worker.get("status") == "recorded_pending_independent_calibration_audit" and
             type(worker.get("cs2_exit_code")) is int and worker["cs2_exit_code"] == 0,
             "Pixel audit requires a completed local recording and clean process exit")
    recorded_binaries = worker.get("binary_profile")
    _require(isinstance(recorded_binaries, dict) and
             all(recorded_binaries.get(name) == expected for name, expected in BINARIES.items()),
             "Unsupported calibration binary profile")
    for name in ("capture_ledger", "calibration_ledger"):
        evidence = worker.get(name, {})
        _require(isinstance(evidence, dict) and evidence.get("path") == name+".jsonl" and
                 evidence.get("sha256") == source_hashes[name+".jsonl"], "Worker ledger hash/path mismatch")
    _require(worker.get("capture_frame_files_sha256") == source_hashes["capture_frame_files.json"],
             "Worker frame archive hash mismatch")
    _require(worker.get("demo", {}).get("sha256") == source_hashes["controlled.dem"], "Worker demo hash mismatch")
    _require(sum(x["event"] == "calibration_complete" for x in controls) == 1 and
             not any(x["event"] == "calibration_failed" for x in controls), "Missing/failed calibration completion")
    headers = [x for x in captures if x["event"] == "header"]
    _require(len(headers) == 1 and captures[0] is headers[0], "Missing/duplicate capture header")
    header = headers[0]
    _require(header.get("schema_version") == 1 and header.get("native_profile") == NATIVE_PROFILE and
             header.get("hook") == "engine2.CMovieRecorder.movie_frame_submit+ReadTexturePixels" and
             header.get("engine_sha256") == BINARIES["bin/win64/engine2.dll"] and
             header.get("native_observation", {}).get("client_sha256") == BINARIES["csgo/bin/win64/client.dll"],
             "Native capture header disagrees with inspected calibration profile")
    movies = [x for x in captures if x["event"] == "movie_frame"]
    readbacks = [x for x in captures if x["event"] == "pixel_readback"]
    ends = [x for x in captures if x["event"] == "movie_end"]
    count, prefix = worker.get("num_frames"), worker.get("capture_prefix")
    _require(_integer(count) and 1 <= count <= 20000, "Invalid calibration frame count")
    _require(isinstance(prefix, str) and re.fullmatch(r"calibration-[0-9a-f]{32}", prefix), "Invalid capture prefix")
    frames = archive.get("frames")
    _require(isinstance(frames, list) and archive.get("schema_version") == 1 and
             archive.get("capture_prefix") == archive.get("archived_prefix") == prefix,
             "Frame archive identity mismatch")
    _require(len(movies) == len(readbacks) == len(frames) == count, "Frame/readback/archive count mismatch")
    _require(len(ends) == 1 and type(ends[0].get("next_capture_index")) is int and
             ends[0]["next_capture_index"] == count and ends[0].get("movie_name") == prefix+"_" and
             captures.index(ends[0]) > captures.index(movies[-1]), "Invalid native movie endpoint")
    by_index = {}
    for row in readbacks:
        candidate = row.get("submission_candidate")
        _require(isinstance(candidate, dict), "Missing readback submission candidate")
        index = candidate.get("capture_index")
        _require(_integer(index) and index < count and index not in by_index, "Duplicate/invalid readback association")
        by_index[index] = row
    inventory, provenance = [], []
    for index, (frame, movie) in enumerate(zip(frames, movies)):
        _require(isinstance(frame, dict), "Invalid archive frame")
        expected = f"{prefix}_{index:08d}.tga"
        _require(type(frame.get("capture_index")) is int and type(movie.get("capture_index")) is int and
                 frame["capture_index"] == movie["capture_index"] == index and
                 type(movie.get("counter_after")) is int and movie["counter_after"] == index+1,
                 "Native frame counters are not contiguous")
        _require(frame.get("source_name") == frame.get("archived_name") == movie.get("tga_filename") == expected and
                 movie.get("movie_name") == prefix+"_", "Native/archive frame filename mismatch")
        path = _owned(run, "frames/"+expected)
        digest = sha256_file(path)
        _require(digest == frame.get("sha256"), "Archived TGA file hash mismatch")
        pixel = by_index[index]
        candidate = pixel["submission_candidate"]
        _require(all(key in movie and movie[key] == value for key, value in candidate.items()),
                 "Readback candidate disagrees with actual movie record")
        with path.open("rb") as handle:
            tga_header = handle.read(18)
        _require(len(tga_header) == 18, "Incomplete TGA header")
        width, height = struct.unpack_from("<HH", tga_header, 12)
        _require(type(pixel.get("width")) is int and type(pixel.get("height")) is int and
                 (pixel["width"], pixel["height"]) == (width, height), "Native/TGA dimensions disagree")
        inventory.append({"path": str(path)})
        before = movie.get("native_observation", {}).get("local_player")
        stable = isinstance(before, dict) and before.get("status") == "observed" and before == (
            pixel.get("native_observation", {}).get("local_player")) == pixel.get("native_observation_after", {}).get("local_player")
        provenance.append({"capture_index": index, "tga_filename": expected, "file_sha256": digest,
            "native_rgb_sha256": pixel.get("rgb_sha256"), "native_rgb_sha256_flipped": pixel.get("rgb_sha256_flipped"),
            "local_player_stable_across_capture": stable})
    comparison = verify_readback_pixels(captures, inventory)
    for frame, item in zip(provenance, inventory):
        _require(sha256_file(Path(item["path"])) == frame["file_sha256"], "TGA changed during pixel audit")
    _require(all(sha256_file(paths[name]) == digest for name, digest in source_hashes.items()),
             "Source evidence changed during pixel audit")
    return {"schema_version": 1, "profile": "controlled_local_tga_readback_diagnostic_v1",
        "status": comparison["status"], "training_ready": False, "live_control_ready": False,
        "input_consumption_timing_verified": False, "pixel_camera_causal_phase_verified": False,
        "run_dir": str(run), "binary_profile": recorded_binaries,
        "pixel_contract_binary_requirements": dict(BINARIES), "plugin_sha256": worker.get("plugin_sha256"),
        "source_hashes": source_hashes, "verifier_source_sha256": sha256_file(Path(__file__)),
        "pixel_decoder_source_sha256": sha256_file(Path(__file__).with_name("timing.py")),
        "pixel_comparison": comparison, "frames": provenance,
        "native_local_player_stable_frames": sum(x["local_player_stable_across_capture"] for x in provenance),
        "frame_start_tick_base": _ticks([x for x in controls if x["event"] == "frame_sample"]),
        "movie_tick_base": _ticks(movies, movie=True),
        "limits": ["Pixel identity does not establish input consumption or camera causal phase.",
            "Repeated FRAME_START observations are not distinct simulation ticks or saved images.",
            "Identical pixel hashes do not independently disambiguate repeated frames.",
            "Additional recorded module identities are preserved, not certified as server/input protocol contracts.",
            "This checks retained local-calibration pixels, not encoded-video fidelity or replay acceptance."]}


@exclusive_output(file_output=True)
def audit_calibration_pixels(run_dir: Path, out: Path):
    """Write a fresh JSON report only after every raw-pixel association passes."""
    staged = staging_paths([out])[0]
    try:
        try:
            report = _audit(Path(run_dir).resolve())
        except (AttributeError, TypeError, KeyError, IndexError) as error:
            raise ValueError("Malformed calibration evidence structure") from error
        staged.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        publish([staged], [out])
        return report
    finally:
        staged.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh pixel report JSON path")
    args = parser.parse_args(argv)
    try:
        report = audit_calibration_pixels(args.run_dir, args.output)
    except (ValueError, OSError, TypeError, KeyError, IndexError) as error:
        parser.exit(2, f"Calibration pixel audit failed: {error}\n")
    print(json.dumps({"status": report["status"], "pixels": report["pixel_comparison"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
