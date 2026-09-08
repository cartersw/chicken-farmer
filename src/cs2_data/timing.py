"""Reconcile native movie-submission records to real frames and decoded video.

Movie submission is an observed association. Until readback correspondence and
render phase are verified, these artifacts are explicit diagnostics.
"""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
import re
import struct
from typing import Any

from .io import exclusive_output, publish, read_json, sha256_file, staging_paths, write_json
from .session_evidence import SessionLedger, events, endpoints

CAPTURE_STATUS = "observed_movie_submission"
IDENTITY = ("demo_id", "clip_id", "round_id", "steam_id", "player_slot")


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if path.name.endswith(".view.json"):
        return SessionLedger(path)
    with path.open(encoding="utf-8-sig") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("Every capture ledger record must be a JSON object")
    return records


def archive_provenance(render: dict[str, Any], render_path: Path) -> dict[str, Any]:
    path = Path(render.get("capture_frame_files", ""))
    path = path if path.is_absolute() else render_path.parent / path
    if not path.is_file() or sha256_file(path) != render.get("capture_frame_files_sha256"):
        raise ValueError("Native frame archive provenance is missing or its hash changed")
    archive = read_json(path)
    if (archive.get("schema_version") != 1 or archive.get("capture_prefix") != render.get("capture_prefix")
            or archive.get("archived_prefix") != render.get("clip_id")
            or len(archive.get("frames", [])) != render.get("num_frames")):
        raise ValueError("Native frame archive identity/count disagrees with renderer")
    return archive


def capture_boundaries(records: list[dict[str, Any]], render: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    if not records or records[0].get("event") != "header" or records[0].get("schema_version") != 1:
        raise ValueError("Native capture ledger requires a versioned header")
    header = records[0]
    if header.get("hook") not in ("engine2.CMovieRecorder.movie_frame_submit", "engine2.CMovieRecorder.movie_frame_submit+ReadTexturePixels") or header.get("clock") != "IDemoFile.GetDemoTick":
        raise ValueError("Unsupported capture hook/clock; frame-stage timestamps are not movie records")
    if not re.fullmatch(r"[0-9a-f]{64}", str(header.get("engine_sha256", ""))):
        raise ValueError("Capture ledger must identify the engine binary SHA256")
    prefix = render.get("capture_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9-]+", prefix):
        raise ValueError("Render manifest requires the native capture prefix")
    frames = list(events(records, ("movie_frame",)))
    ends = endpoints(records)
    if len(frames) != render.get("num_frames") or len(ends) != 1 or len(frames) < 2:
        raise ValueError("Movie ledger must contain every encoded frame and exactly one explicit endpoint")
    if any(row.get("movie_name") != prefix + "_" for row in frames + ends):
        raise ValueError("Movie ledger contains another capture identity or repeated clip")
    end = ends[0]
    if records.index(end) <= records.index(frames[-1]) or end.get("next_capture_index") != len(frames):
        raise ValueError("Movie endpoint must follow the final submitted frame and its counter")
    ticks = []
    for index, row in enumerate(frames):
        if row.get("capture_index") != index or row.get("counter_after") != index + 1:
            raise ValueError("Native movie counters must be contiguous and start at zero")
        name = row.get("tga_filename", "")
        match = re.fullmatch(re.escape(prefix) + r"_(\d+)\.tga", name, re.IGNORECASE)
        native_index = row.get("native_capture_index") if isinstance(records, SessionLedger) else index
        if not match or int(match[1]) != native_index:
            raise ValueError("Native TGA filename disagrees with movie counter")
    for row in frames + ends:
        tick = row.get("replay_demo_tick")
        if not isinstance(tick, (int, float)) or isinstance(tick, bool) or not math.isfinite(tick) or tick < 0:
            raise ValueError("Movie records require finite observed replay ticks")
        if ticks and tick <= ticks[-1]:
            raise ValueError("Observed movie ticks repeat/reverse; split or investigate capture, never invent boundaries")
        ticks.append(tick)
    return ticks, header


def render_boundaries(records: list[dict[str, Any]]) -> list[float] | None:
    """Use the native semantic field only, never reinterpret a guessed raw float."""
    selected = list(events(records, ("movie_frame",)))
    selected += endpoints(records)
    if not any("render_time_seconds" in row for row in selected):
        return None
    values = []
    for row in selected:
        value = row.get("render_time_seconds")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("Every frame and endpoint need observed EventClientOutput render_time_seconds")
        if values and value <= values[-1]:
            raise ValueError("Observed render time repeats/reverses; no fractional boundary may be invented")
        values.append(value)
    return values


def tga_rgb24(path: Path) -> bytes:
    """Decode uncolormapped 24/32-bit raw/RLE TGA to tightly packed, top-first RGB."""
    data = path.read_bytes()
    if len(data) < 18:
        raise ValueError("Incomplete TGA header")
    identifier_size, color_map, image_type = data[:3]
    width, height, depth, descriptor = struct.unpack_from("<HHBB", data, 12)
    if color_map or image_type not in (2, 10) or depth not in (24, 32) or not width or not height:
        raise ValueError("Unsupported TGA pixel layout")
    channels, count = depth//8, width*height
    if count > 4096*4096:
        raise ValueError("TGA exceeds bounded replay dimensions")
    cursor = 18 + identifier_size
    size = count*channels
    if image_type == 2:
        pixels = data[cursor:cursor+size]
        if len(pixels) != size:
            raise ValueError("Incomplete TGA pixels")
    else:
        pixels = bytearray()
        while len(pixels) < size:
            if cursor >= len(data):
                raise ValueError("Incomplete RLE TGA packet")
            packet = data[cursor]
            cursor += 1
            length = (packet & 127) + 1
            encoded_size = channels if packet & 128 else length*channels
            chunk = data[cursor:cursor+encoded_size]
            if len(chunk) != encoded_size or len(pixels)+length*channels > size:
                raise ValueError("Invalid RLE TGA run")
            pixels.extend(chunk*length if packet & 128 else chunk)
            cursor += encoded_size
    rows = []
    for output_y in range(height):
        input_y = output_y if descriptor & 32 else height-1-output_y
        row = pixels[input_y*width*channels:(input_y+1)*width*channels]
        rgb = bytearray(width*3)
        for out_channel, in_channel in ((0, 2), (1, 1), (2, 0)):
            values = row[in_channel::channels]
            rgb[out_channel::3] = values[::-1] if descriptor & 16 else values
        rows.append(rgb)
    return b"".join(rows)


def verify_readback_pixels(records: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    readbacks = list(events(records, ("pixel_readback",)))
    if not readbacks:
        return {"status": "unavailable", "matched_frames": 0, "verified": False}
    by_index = {}
    for row in readbacks:
        candidate = row.get("submission_candidate") or {}
        index = candidate.get("capture_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(inventory) or index in by_index:
            raise ValueError("Pixel readback candidate is missing, duplicated or outside native frame range")
        by_index[index] = row
    if len(by_index) != len(inventory):
        raise ValueError("Pixel readbacks do not cover every submitted/archived frame")
    frame_records = list(events(records, ("movie_frame",)))
    matched = []
    for index, item in enumerate(inventory):
        row = by_index[index]
        if row.get("success") is not True or row["submission_candidate"].get("tga_filename") != frame_records[index]["tga_filename"]:
            raise ValueError("Pixel readback failed or refers to a different native frame filename")
        digest = hashlib.sha256(tga_rgb24(Path(item["path"]))).hexdigest()
        if digest not in (row.get("rgb_sha256"), row.get("rgb_sha256_flipped")):
            raise ValueError(f"Native pixel readback SHA256 disagrees with archived TGA frame {index}")
        matched.append(digest)
    return {"status": "all_archived_pixels_match_readback", "matched_frames": len(matched),
            "verified": True, "distinct_pixel_hashes": len(set(matched)),
            "repeated_pixel_frames": len(matched)-len(set(matched)),
            "limits": "Identical pixel frames cannot independently disambiguate repeated images; replay interpolation and observation phase remain unverified"}


def verify_capture_evidence(clip: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = clip.get("capture_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("Diagnostic timing requires versioned capture evidence")
    for name in ("ledger", "render_manifest", "frame_inventory"):
        path = Path(evidence.get(name + "_path", ""))
        if not path.is_file() or sha256_file(path) != evidence.get(name + "_sha256"):
            raise ValueError(f"Diagnostic capture {name} hash mismatch")
    render = read_json(Path(evidence["render_manifest_path"]))
    if render.get("capture_ledger_sha256") != evidence["ledger_sha256"]:
        raise ValueError("Native render manifest does not bind this capture ledger hash")
    if any(str(render.get(key)) != str(clip.get(key)) for key in IDENTITY + ("num_frames", "video_sha256", "width", "height", "fps")):
        raise ValueError("Diagnostic clip identity/video disagrees with its native render manifest")
    records = read_ledger(Path(evidence["ledger_path"]))
    ticks, header = capture_boundaries(records, render)
    render_times = render_boundaries(records)
    native_frames = list(events(records, ("movie_frame",)))
    archive = archive_provenance(render, Path(evidence["render_manifest_path"]))
    if len(frames) != len(ticks)-1:
        raise ValueError("Frame timing count disagrees with native capture ledger")
    for index, frame in enumerate(frames):
        if frame["source_demo_tick_start"] != ticks[index] or frame["source_demo_tick_end"] != ticks[index+1]:
            raise ValueError("Frame timing was altered; native capture evidence disagrees")
        if frame.get("submission_event_0x28_float_raw") != native_frames[index].get("submission_event_0x28_float_raw"):
            raise ValueError("Raw movie event clock disagrees with native capture evidence")
        expected_start = render_times[index] if render_times is not None else None
        expected_end = render_times[index+1] if render_times is not None else None
        if frame.get("render_time_seconds_start") != expected_start or frame.get("render_time_seconds_end") != expected_end:
            raise ValueError("Observed fractional render time disagrees with native capture evidence")
        if frame.get("native_demo_start_tick") != native_frames[index].get("demo_start_tick"):
            raise ValueError("Native playback epoch disagrees with capture evidence")
    inventory = read_json(Path(evidence["frame_inventory_path"]))
    if len(inventory.get("frames", [])) != len(frames):
        raise ValueError("Capture frame inventory count disagrees with ledger")
    for index, item in enumerate(inventory["frames"]):
        path = Path(item["path"])
        original = archive["frames"][index]
        if (item.get("capture_index") != index or original.get("capture_index") != index
                or original.get("source_name") != native_frames[index]["tga_filename"]
                or original.get("archived_name") != path.name
                or original.get("sha256") != item.get("sha256") or sha256_file(path) != item.get("sha256")):
            raise ValueError("Archived capture frame index/hash mismatch")
    pixels = verify_readback_pixels(records, inventory["frames"])
    return {"status": CAPTURE_STATUS, "header": header, "evidence": evidence,
            "pixel_correspondence": pixels}


@exclusive_output()
def prepare_timing(clip_path: Path, ledger: Path, pts_path: Path, frames_dir: Path,
                   out: Path) -> dict[str, Any]:
    from .align import verify_video  # local import: align also verifies this evidence

    render = read_json(clip_path)
    if render.get("capture_ledger_sha256") != sha256_file(ledger):
        raise ValueError("Native render manifest capture ledger hash mismatch")
    records = read_ledger(ledger)
    ticks, header = capture_boundaries(records, render)
    render_times = render_boundaries(records)
    native_frames = list(events(records, ("movie_frame",)))
    archive = archive_provenance(render, clip_path)
    pts = read_json(pts_path)
    if pts.get("clip_id") != render["clip_id"] or pts.get("clock") != "video_presentation":
        raise ValueError("Decoded PTS belongs to another clip/clock")
    if len(pts.get("frames", [])) != len(ticks)-1:
        raise ValueError("Decoded PTS and native movie counter counts disagree")
    frames, inventory = [], []
    for index, stamp in enumerate(pts["frames"]):
        if stamp.get("frame_index") != index:
            raise ValueError("Decoded PTS indexes must be contiguous and zero based")
        frame_path = frames_dir / f"{render['clip_id']}_{index:08d}.tga"
        if not frame_path.is_file():
            raise ValueError(f"Actual archived TGA frame missing: {frame_path}")
        digest = sha256_file(frame_path)
        original = archive["frames"][index]
        if (original.get("capture_index") != index or original.get("source_name") != native_frames[index]["tga_filename"]
                or original.get("archived_name") != frame_path.name or original.get("sha256") != digest):
            raise ValueError("Native movie filename/counter disagrees with archived frame provenance")
        inventory.append({"capture_index": index, "path": str(frame_path.resolve()), "sha256": digest})
        frames.append({"schema_version": 1, **{key: render[key] for key in IDENTITY},
                       "frame_index": index, "pts_seconds": stamp["pts_seconds"],
                       "source_demo_tick_start": ticks[index], "source_demo_tick_end": ticks[index+1],
                       "submission_event_0x28_float_raw": native_frames[index].get("submission_event_0x28_float_raw"),
                       "render_time_seconds_start": render_times[index] if render_times is not None else None,
                       "render_time_seconds_end": render_times[index+1] if render_times is not None else None,
                       "native_demo_start_tick": native_frames[index].get("demo_start_tick")})
    video = verify_video(render, clip_path, frames)
    pixels = verify_readback_pixels(records, inventory)
    destinations = [out / "frames.jsonl", out / "frame_inventory.json", out / "clip.json"]
    staged = staging_paths(destinations)
    with staged[0].open("x", encoding="utf-8", newline="\n") as handle:
        for frame in frames:
            handle.write(json.dumps(frame, allow_nan=False) + "\n")
    write_json(staged[1], {"schema_version": 1, "frames": inventory})
    clip = {**render, "schema_version": 1, "video_uri": str(video),
            "timing_status": CAPTURE_STATUS, "timing_clock": "demo_tick", "training_ready": False,
            "pov_verified": render.get("pov_verified") is True,
            "pixel_correspondence_validated": pixels["verified"], "capture_ledger_header": header,
            "pixel_correspondence": pixels,
            "render_time_clock_status": "observed_event_client_output" if render_times is not None else "unavailable",
            "capture_evidence": {"schema_version": 1, "ledger_path": str(ledger.resolve()),
                                 "ledger_sha256": sha256_file(ledger),
                                 "render_manifest_path": str(clip_path.resolve()),
                                 "render_manifest_sha256": sha256_file(clip_path),
                                 "frame_inventory_path": str(destinations[1].resolve()),
                                 "frame_inventory_sha256": sha256_file(staged[1])},
            "timing_limitations": ["Replay cursor is not the fractional rendered-state observation time",
                                   "Render-time epoch versus UserCmd execution clock requires scoped calibration",
                                   "POV, future-action causality and label eligibility require independent checks"]}
    write_json(staged[2], clip)
    publish(staged, destinations)
    return {"status": "complete", "timing_status": CAPTURE_STATUS, "training_ready": False,
            "num_frames": len(frames), "clip_path": str(destinations[2]), "timing_path": str(destinations[0])}
