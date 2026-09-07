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
from .io import (exclusive_output, parsed_manifest, publish, read_json, require_columns,
                 sha256_file, staging_paths, versioned_schema, write_json)

IDENTITY = ("clip_id", "demo_id", "round_id", "player_slot", "steam_id")


def same_identity(left: dict[str, Any], right: dict[str, Any], keys=IDENTITY) -> bool:
    return all(str(left.get(key)) == str(right.get(key)) for key in keys)


def validate_timing(frames: list[dict[str, Any]], clip: dict[str, Any]) -> None:
    if clip.get("schema_version") != 1:
        raise ValueError("Unsupported clip timing schema version (expected 1)")
    if clip.get("timing_status") != "measured" or clip.get("timing_clock") != "demo_tick":
        raise ValueError("Alignment requires measured demo_tick timing; nominal FPS or unverified timing is insufficient")
    if clip.get("pov_verified") is not True:
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
        if frame.get("frame_index") != index:
            raise ValueError("frame_index must be contiguous and start at zero")
        for key in ("pts_seconds", "source_demo_tick_start", "source_demo_tick_end"):
            value = frame.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
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
                    max_gap_ticks: int = 4) -> tuple[list[list[int]], list[int]]:
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
        tick, row_id = command["demo_tick"], command["command_row_id"]
        if row_id in row_ids:
            raise ValueError("Duplicate canonical command_row_id")
        row_ids.add(row_id)
        if last_tick is not None and (tick < last_tick or tick - last_tick > max_gap_ticks):
            raise ValueError("Selected command timeline has a reversal or unexplained gap")
        last_tick = tick
        index = bisect.bisect_right(starts, tick) - 1
        if index < 0 or tick >= frames[index]["source_demo_tick_end"]:
            raise ValueError("Command lies outside measured clip intervals")
        groups[index].append(row_id)
        assigned.append(index)
    if not commands:
        raise ValueError("No commands for this player and measured clip interval")
    if commands[0]["demo_tick"] - starts[0] > max_gap_ticks:
        raise ValueError("Unexplained command gap at clip start")
    if frames[-1]["source_demo_tick_end"] - commands[-1]["demo_tick"] > max_gap_ticks:
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
          normalized: Path | None = None, max_gap_ticks: int = 4) -> dict[str, Any]:
    manifest, clip = parsed_manifest(parsed, ["usercmd.parquet"]), read_json(clip_path)
    if clip.get("demo_id") != manifest["demo_id"]:
        raise ValueError("Clip comes from a different demo than parsed commands")
    frames = load_frames(timing)
    validate_timing(frames, clip)
    video = verify_video(clip, clip_path, frames)
    source = parsed / "usercmd.parquet"
    require_columns(source, ["demo_id", "demo_tick", "command_row_id", "steam_id", "round_id", "player_slot"])
    predicate = ((ds.field("demo_id") == clip["demo_id"]) &
                 (ds.field("steam_id") == pa.scalar(int(clip["steam_id"]), type=pa.uint64())) &
                 (ds.field("round_id") == clip["round_id"]) &
                 (ds.field("player_slot") == clip["player_slot"]) &
                 (ds.field("demo_tick") >= frames[0]["source_demo_tick_start"]) &
                 (ds.field("demo_tick") < frames[-1]["source_demo_tick_end"]))
    table = ds.dataset(source, format="parquet").to_table(filter=predicate)
    commands = table.to_pylist()
    groups, assigned = assign_commands(frames, commands, max_gap_ticks)
    destinations = [out / name for name in ("frame_alignment.parquet", "aligned_commands.parquet", "alignment_manifest.json")]
    outputs = staging_paths(destinations)
    schema = versioned_schema([
        ("demo_id", pa.string()), ("clip_id", pa.string()), ("round_id", pa.int32()),
        ("steam_id", pa.uint64()), ("player_slot", pa.int32()), ("frame_index", pa.int64()),
        ("pts_seconds", pa.float64()), ("source_demo_tick_start", pa.float64()),
        ("source_demo_tick_end", pa.float64()), ("command_row_ids", pa.list_(pa.int64())),
        ("clip_command_start", pa.int64()), ("clip_command_end", pa.int64()),
    ], "alignment", clip["demo_id"])
    schema = schema.with_metadata({**schema.metadata, b"training_ready": b"false", b"command_time_basis": b"demo_packet_arrival"})
    aligned_frames = []
    cursor = 0
    for frame, group in zip(frames, groups):
        aligned_frames.append({**{key: frame[key] for key in IDENTITY},
                               "steam_id": int(frame["steam_id"]),
                               **{key: frame[key] for key in ("frame_index", "pts_seconds", "source_demo_tick_start", "source_demo_tick_end")},
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
        "command_time_basis": "demo_packet_arrival", "training_ready": False,
        "validation_required": "Visually validate command execution timing against the recorded replay; packet arrival can differ from execution.",
        "normalization_included": normalized is not None,
        "parsed_directory": str(parsed.resolve()),
        "source_usercmd_sha256": sha256_file(source),
        "source_timing_sha256": sha256_file(timing),
        "source_clip_manifest_sha256": sha256_file(clip_path),
        "files": {"frame_alignment.parquet": sha256_file(outputs[0]),
                  "aligned_commands.parquet": sha256_file(outputs[1])},
    }
    write_json(outputs[2], report)
    publish(outputs, destinations)
    return report
