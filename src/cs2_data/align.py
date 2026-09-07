"""Join actual captured frame intervals to the immutable command stream."""

from __future__ import annotations

import bisect
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from . import __version__
from .calibration import identity_filter, load_calibration
from .timing import CAPTURE_STATUS, verify_capture_evidence
from .io import (exclusive_output, parsed_manifest, publish, read_json, require_columns,
                 sha256_file, staging_paths, versioned_schema, write_json)

IDENTITY = ("clip_id", "demo_id", "round_id", "player_slot", "steam_id")


def same_identity(left: dict[str, Any], right: dict[str, Any], keys=IDENTITY) -> bool:
    return all(str(left.get(key)) == str(right.get(key)) for key in keys)


def validate_timing(frames: list[dict[str, Any]], clip: dict[str, Any], *, diagnostic: bool = False) -> None:
    if clip.get("schema_version") != 1:
        raise ValueError("Unsupported clip timing schema version (expected 1)")
    if clip.get("capture_evidence") is not None and clip.get("timing_status") != CAPTURE_STATUS:
        raise ValueError("Native submission evidence cannot be promoted to measured timing by changing its status")
    accepted_status = clip.get("timing_status") == "measured" or (diagnostic and clip.get("timing_status") == CAPTURE_STATUS)
    if not accepted_status or clip.get("timing_clock") != "demo_tick":
        raise ValueError("Alignment requires measured demo_tick timing; nominal FPS or unverified timing is insufficient")
    if clip.get("pov_verified") is not True and not diagnostic:
        raise ValueError("Clip must record pov_verified=true after verifying the rendered player's identity")
    for key in IDENTITY:
        if clip.get(key) is None:
            raise ValueError(f"Clip manifest is missing {key}")
    if not int(clip["steam_id"]):
        raise ValueError("Clip Steam identity must be resolved")
    if not frames or len(frames) != clip.get("num_frames"):
        raise ValueError("Frame timing count must equal clip num_frames and be nonzero")
    last_end = None
    last_pts = None
    for index, frame in enumerate(frames):
        if not same_identity(frame, clip):
            raise ValueError(f"Frame {index} identity disagrees with clip manifest")
        if isinstance(frame.get("frame_index"), bool) or frame.get("frame_index") != index:
            raise ValueError("frame_index must be contiguous and start at zero")
        for key in ("pts_seconds", "source_demo_tick_start", "source_demo_tick_end"):
            value = frame.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"Frame {index} needs finite measured {key}")
        start, end = frame["source_demo_tick_start"], frame["source_demo_tick_end"]
        pts = frame["pts_seconds"]
        if start < 0 or end <= start or pts < 0:
            raise ValueError(f"Frame {index} has an invalid interval or presentation timestamp")
        if last_end is not None and start != last_end:
            raise ValueError("Frame source intervals must be contiguous: no overlap, gaps, seeks, or interpolated replacements")
        if last_pts is not None and pts <= last_pts:
            raise ValueError("Frame presentation timestamps must strictly increase")
        last_end, last_pts = end, pts


def assign_commands(frames: list[dict[str, Any]], commands: list[dict[str, Any]],
                    max_gap_ticks: int = 4, *, time_field: str = "demo_tick",
                    future_actions: bool = False) -> tuple[list[list[int]], list[int]]:
    """Return lists of exact RAW row IDs and frame index per selected command.

    Tick on a right boundary belongs to the next frame. Empty frames remain empty;
    multiple commands are retained in capture order, regardless of nominal FPS.
    """
    if max_gap_ticks < 1:
        raise ValueError("max_gap_ticks must be positive")
    starts = [frame["source_demo_tick_start"] for frame in frames]
    groups: list[list[int]] = [[] for _ in frames]
    assigned: list[int] = []
    last_tick = None
    row_ids: set[int] = set()
    for command in commands:
        tick, row_id = command[time_field], command["command_row_id"]
        if not isinstance(tick, (int, float)) or isinstance(tick, bool) or not math.isfinite(tick):
            raise ValueError("Command alignment clock needs a finite timestamp")
        if row_id in row_ids:
            raise ValueError("Duplicate canonical command_row_id")
        row_ids.add(row_id)
        if last_tick is not None and (tick < last_tick or tick - last_tick > max_gap_ticks):
            raise ValueError("Selected command timeline has a reversal or unexplained gap")
        last_tick = tick
        # A rendered observation after tick t must not predict the command that
        # produced state at t. Calibrated targets use (observation, next] instead.
        index = (bisect.bisect_left(starts, tick) if future_actions else bisect.bisect_right(starts, tick)) - 1
        outside_end = index >= 0 and (tick > frames[index]["source_demo_tick_end"] if future_actions
                                     else tick >= frames[index]["source_demo_tick_end"])
        if index < 0 or outside_end:
            raise ValueError("Command lies outside measured clip intervals")
        groups[index].append(row_id)
        assigned.append(index)
    if not commands:
        raise ValueError("No commands for this player and measured clip interval")
    if commands[0][time_field] - starts[0] > max_gap_ticks:
        raise ValueError("Unexplained command gap at clip start")
    if frames[-1]["source_demo_tick_end"] - commands[-1][time_field] > max_gap_ticks:
        raise ValueError("Unexplained command gap at clip end")
    return groups, assigned


def load_frames(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        return pq.read_table(path).to_pylist()
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify_video(clip: dict[str, Any], clip_path: Path, frames: list[dict[str, Any]]) -> Path:
    uri = clip.get("video_uri")
    if not isinstance(uri, str) or not uri:
        raise ValueError("Clip needs a local video_uri")
    video = Path(uri)
    if not video.is_absolute():
        video = clip_path.parent / video
    if not video.is_file():
        raise ValueError(f"Rendered video is missing: {video}")
    expected_hash = clip.get("video_sha256")
    if not expected_hash or sha256_file(video) != expected_hash:
        raise ValueError("Rendered video SHA256 does not match clip manifest")
    executable = shutil.which("ffprobe")
    if not executable:
        raise ValueError("ffprobe must be installed and on PATH to validate decoded video timing")
    result = subprocess.run([
        executable, "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_streams",
        "-show_entries", "frame=best_effort_timestamp_time:stream=width,height", "-of", "json", str(video),
    ], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f"Video decode/probe failed: {result.stderr.strip()}")
    probe = json.loads(result.stdout)
    decoded = probe.get("frames", [])
    if len(decoded) != len(frames):
        raise ValueError("Decoded video frame count disagrees with measured timing")
    streams = probe.get("streams", [])
    if len(streams) != 1 or any(streams[0].get(key) != clip.get(key) for key in ("width", "height")):
        raise ValueError("Decoded video dimensions disagree with clip manifest")
    for expected, actual in zip(frames, decoded):
        if not math.isclose(float(actual["best_effort_timestamp_time"]), expected["pts_seconds"], abs_tol=1e-5):
            raise ValueError("Measured frame PTS disagrees with decoded video PTS")
    return video.resolve()


@exclusive_output()
def align(parsed: Path, timing: Path, clip_path: Path, out: Path,
          normalized: Path | None = None, max_gap_ticks: int = 4,
          calibration: Path | None = None, diagnostic: bool = False) -> dict[str, Any]:
    manifest, clip = parsed_manifest(parsed, ["usercmd.parquet"]), read_json(clip_path)
    if clip.get("demo_id") != manifest["demo_id"]:
        raise ValueError("Clip comes from a different demo than parsed commands")
    frames = load_frames(timing)
    validate_timing(frames, clip, diagnostic=diagnostic)
    capture_evidence = None
    if diagnostic:
        # Explicit diagnostic mode relaxes only certification, never provenance.
        capture_evidence = verify_capture_evidence(clip, frames)
    video = verify_video(clip, clip_path, frames)
    source = parsed / "usercmd.parquet"
    require_columns(source, ["demo_id", "demo_tick", "command_row_id", "steam_id", "round_id", "player_slot"])
    player_filter = identity_filter(clip)
    first_tick, last_tick = frames[0]["source_demo_tick_start"], frames[-1]["source_demo_tick_end"]
    calibration_report = None
    action_frames = frames
    observation_basis = "demo_packet_cursor"
    render_epoch_assumption = None
    time_basis = "demo_packet_arrival"
    if calibration is None:
        predicate = player_filter & (ds.field("demo_tick") >= first_tick) & (ds.field("demo_tick") < last_tick)
        table = ds.dataset(source, format="parquet").to_table(filter=predicate)
        commands = table.to_pylist()
        groups, assigned = assign_commands(frames, commands, max_gap_ticks)
    else:
        # Read by identity, not packet time: delayed/batched packets must not
        # silently remove commands that executed inside the capture interval.
        table = ds.dataset(source, format="parquet").to_table(filter=player_filter)
        all_commands = table.to_pylist()
        calibration_report = load_calibration(calibration, manifest, clip, all_commands)
        offset = calibration_report["offset_demo_ticks"]
        if any(frame.get("render_time_seconds_start") is not None for frame in frames):
            if capture_evidence is None or clip.get("render_time_clock_status") != "observed_event_client_output":
                raise ValueError("Fractional render-clock alignment requires verified native capture evidence")
            tick_rate = manifest.get("tick_rate")
            if not isinstance(tick_rate, (int, float)) or isinstance(tick_rate, bool) or not math.isfinite(tick_rate) or tick_rate <= 0:
                raise ValueError("Fractional render time requires the canonical demo's positive tick_rate")
            action_frames = []
            for frame in frames:
                start, end = frame.get("render_time_seconds_start"), frame.get("render_time_seconds_end")
                if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                        or not math.isfinite(start) or not math.isfinite(end) or end <= start):
                    raise ValueError("Incomplete/nonincreasing fractional render-time interval")
                action_frames.append({**frame, "source_demo_tick_start": start*tick_rate+offset,
                                      "source_demo_tick_end": end*tick_rate+offset})
            first_tick, last_tick = action_frames[0]["source_demo_tick_start"], action_frames[-1]["source_demo_tick_end"]
            observation_basis = "EventClientOutput_t.m_flRenderTime"
            render_epoch_assumption = "render_time_seconds * canonical tick_rate shares server_tick_executed epoch"
        domain_start, domain_end = calibration_report["mapped_interval"]
        if first_tick < domain_start or last_tick > domain_end:
            raise ValueError("Frame action windows extend outside the calibrated execution domain; extrapolation is forbidden")
        indexes, mapped_ticks = [], []
        for index, row in enumerate(all_commands):
            if not calibration_report["first_command_row_id"] <= row["command_row_id"] <= calibration_report["last_command_row_id"]:
                continue
            tick = row["server_tick_executed"] + offset
            if first_tick < tick <= last_tick:
                indexes.append(index)
                mapped_ticks.append(tick)
        table = table.take(pa.array(indexes, type=pa.int64()))
        table = table.append_column("execution_demo_tick", pa.array(mapped_ticks, type=pa.float64()))
        commands = table.to_pylist()
        groups, assigned = assign_commands(action_frames, commands, max_gap_ticks, time_field="execution_demo_tick", future_actions=True)
        time_basis = "server_tick_executed_with_render_clock" if render_epoch_assumption else "server_tick_executed_with_calibration"
    # Normalization is keyed to exact raw IDs, including execution-selected
    # commands whose packet times may lie outside the capture interval.
    predicate = player_filter & ds.field("command_row_id").isin([row["command_row_id"] for row in commands])
    destinations = [out / name for name in ("frame_alignment.parquet", "aligned_commands.parquet", "alignment_manifest.json")]
    outputs = staging_paths(destinations)
    schema = versioned_schema([
        ("demo_id", pa.string()), ("clip_id", pa.string()), ("round_id", pa.int32()),
        ("steam_id", pa.uint64()), ("player_slot", pa.int32()), ("frame_index", pa.int64()),
        ("pts_seconds", pa.float64()), ("source_demo_tick_start", pa.float64()),
        ("source_demo_tick_end", pa.float64()), ("command_row_ids", pa.list_(pa.int64())),
        ("submission_event_0x28_float_raw", pa.float64()),
        ("native_demo_start_tick", pa.int64()),
        ("render_time_seconds_start", pa.float64()), ("render_time_seconds_end", pa.float64()),
        ("action_window_demo_tick_start", pa.float64()), ("action_window_demo_tick_end", pa.float64()),
        ("clip_command_start", pa.int64()), ("clip_command_end", pa.int64()),
    ], "alignment", clip["demo_id"])
    schema = schema.with_metadata({**schema.metadata, b"training_ready": b"false",
                                   b"command_time_basis": time_basis.encode(),
                                   b"action_interval_convention": b"(start,end]" if calibration else b"[start,end)"})
    aligned_frames = []
    cursor = 0
    for frame, action_frame, group in zip(frames, action_frames, groups):
        aligned_frames.append({**{key: frame[key] for key in IDENTITY},
                               "steam_id": int(frame["steam_id"]),
                               **{key: frame[key] for key in ("frame_index", "pts_seconds", "source_demo_tick_start", "source_demo_tick_end")},
                               "submission_event_0x28_float_raw": frame.get("submission_event_0x28_float_raw"),
                               "native_demo_start_tick": frame.get("native_demo_start_tick"),
                               "render_time_seconds_start": frame.get("render_time_seconds_start"),
                               "render_time_seconds_end": frame.get("render_time_seconds_end"),
                               "action_window_demo_tick_start": action_frame["source_demo_tick_start"],
                               "action_window_demo_tick_end": action_frame["source_demo_tick_end"],
                               "command_row_ids": group, "clip_command_start": cursor, "clip_command_end": cursor + len(group)})
        cursor += len(group)
    if normalized is not None:
        norm_path = normalized / "normalized_actions.parquet" if normalized.is_dir() else normalized
        norm_report = read_json(norm_path.parent / "normalization_report.json")
        if norm_report.get("status") != "complete" or norm_report.get("normalizer_version") not in (1, 2):
            raise ValueError("Normalization stage is incomplete or has an unsupported version")
        if norm_report.get("demo_id") != clip["demo_id"] or norm_report.get("source_usercmd_sha256") != sha256_file(source):
            raise ValueError("Normalization provenance does not match the canonical command file")
        if norm_report.get("files", {}).get("normalized_actions.parquet") != sha256_file(norm_path):
            raise ValueError("Normalized file hash disagrees with its completion report")
        norm = ds.dataset(norm_path, format="parquet").to_table(filter=predicate).to_pylist()
        by_id = {row["command_row_id"]: row for row in norm}
        if len(by_id) != len(norm) or set(by_id) != {row["command_row_id"] for row in commands}:
            raise ValueError("Normalized commands do not exactly cover the selected canonical command rows")
        for key, dtype in (("delta_yaw_deg", pa.float32()), ("delta_pitch_deg", pa.float32()),
                           ("aim_valid", pa.bool_()), ("reset_reason", pa.string()),
                           ("mousedx_effective", pa.int32()), ("mousedy_effective", pa.int32())):
            if key not in norm[0]:
                continue
            table = table.append_column(key, pa.array([by_id[row["command_row_id"]][key] for row in commands], type=dtype))
    table = table.append_column("clip_id", pa.array([clip["clip_id"]] * len(commands)))
    table = table.append_column("frame_index", pa.array(assigned, type=pa.int64()))
    table = table.append_column("clip_command_index", pa.array(range(len(commands)), type=pa.int64()))
    table = table.replace_schema_metadata(schema.metadata)
    pq.write_table(pa.Table.from_pylist(aligned_frames, schema=schema), outputs[0], compression="zstd")
    pq.write_table(table, outputs[1], compression="zstd")
    report = {
        "status": "complete", "schema_version": 1, "alignment_version": 1, "processing_version": __version__,
        **{key: clip[key] for key in IDENTITY}, "video_uri": str(video), "video_sha256": clip["video_sha256"],
        "num_frames": len(frames), "command_count": len(commands),
        "empty_frames": sum(not group for group in groups),
        "max_commands_per_frame": max(map(len, groups)), "timing_clock": "demo_tick",
        "command_time_basis": time_basis, "training_ready": False,
        "diagnostic_alignment": diagnostic, "capture_timing_status": clip.get("timing_status"),
        "pov_verified": clip.get("pov_verified") is True, "capture_evidence_verification": capture_evidence,
        "action_interval_convention": "(start,end]" if calibration else "[start,end)",
        "observation_phase_assumption": ("state_at_observed_render_time" if render_epoch_assumption else "after_tick_state") if calibration else None,
        "observation_time_basis": observation_basis, "render_time_epoch_assumption": render_epoch_assumption,
        "canonical_tick_rate": manifest.get("tick_rate"),
        "native_playback_epoch_evidence": {
            "observed_demo_start_ticks": sorted({frame["native_demo_start_tick"] for frame in frames if frame.get("native_demo_start_tick") is not None}),
            "execution_epoch_equivalence_verified": False,
            "note": "The native playback loop epoch can change after seeks; it is not substituted for the recorded command execution epoch."},
        "execution_mapping_version": 1 if calibration else None,
        "execution_calibration": calibration_report,
        "validation_required": "Validate movie-frame correspondence, observation phase, POV and visible actions. An inferred clock offset is not measured execution timing.",
        "normalization_included": normalized is not None,
        "parsed_directory": str(parsed.resolve()),
        "source_usercmd_sha256": sha256_file(source),
        "source_parsed_manifest_sha256": sha256_file(parsed / "manifest.json"),
        "source_timing_sha256": sha256_file(timing),
        "source_clip_manifest_sha256": sha256_file(clip_path),
        "files": {"frame_alignment.parquet": sha256_file(outputs[0]),
                  "aligned_commands.parquet": sha256_file(outputs[1])},
    }
    write_json(outputs[2], report)
    publish(outputs, destinations)
    return report
