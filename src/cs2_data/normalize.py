"""Stream derived aim labels without changing canonical command records."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from . import __version__
from .io import (batches, exclusive_output, parsed_manifest, publish, read_json, require_columns,
                 sha256_file, staging_paths, versioned_schema, write_json)


def wrap_angle(degrees: float) -> float:
    """Return the shortest signed rotation in [-180, 180)."""
    return (degrees + 180.0) % 360.0 - 180.0


def finite(value: Any) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def effective_scalar(row: dict[str, Any], field: str, presence_field: str) -> Any:
    """Apply a protobuf scalar default only inside a known-present parent message."""
    value = row.get(field)
    return 0 if value is None and row.get(presence_field) is True else value


def transition(previous: dict[str, Any] | None, current: dict[str, Any],
               max_gap_ticks: int = 4) -> tuple[float | None, float | None, str | None]:
    """Attach previous->current rotation to the CURRENT command, with explicit reset masks."""
    if previous is None:
        return None, None, "first_command"
    for field in ("demo_id", "round_id", "steam_id", "pawn_entity_handle"):
        if previous.get(field) != current.get(field):
            return None, None, f"changed_{field}"
    if not current.get("steam_id"):
        return None, None, "unresolved_player"
    if previous.get("alive") is False or current.get("alive") is False:
        return None, None, "not_alive"
    for row in (previous, current):
        if not all(finite(effective_scalar(row, field, "viewangles_present")) for field in ("view_yaw", "view_pitch")):
            return None, None, "missing_viewangles"
    gap = current["demo_tick"] - previous["demo_tick"]
    if gap < 0 or gap > max_gap_ticks:
        return None, None, "demo_tick_discontinuity"
    if current["command_number"] != previous["command_number"] + 1:
        return None, None, "command_number_discontinuity"
    if current["client_tick"] < previous["client_tick"]:
        return None, None, "client_tick_reset"
    return (wrap_angle(effective_scalar(current, "view_yaw", "viewangles_present") -
                       effective_scalar(previous, "view_yaw", "viewangles_present")),
            effective_scalar(current, "view_pitch", "viewangles_present") -
            effective_scalar(previous, "view_pitch", "viewangles_present"), None)


class RobustSamples:
    """Bounded deterministic reservoir; estimates are diagnostics, never player DPI."""

    def __init__(self, capacity: int = 4096) -> None:
        self.values: list[float] = []
        self.count = 0
        self.capacity = capacity
        self.rng = random.Random(0)

    def add(self, value: float) -> None:
        self.count += 1
        if len(self.values) < self.capacity:
            self.values.append(value)
        else:
            index = self.rng.randrange(self.count)
            if index < self.capacity:
                self.values[index] = value

    def report(self) -> dict[str, Any]:
        median = statistics.median(self.values) if self.values else None
        return {"num_samples": self.count, "reservoir_samples": len(self.values),
                "median_degrees_per_mouse_count": median,
                "median_absolute_deviation": statistics.median(abs(x - median) for x in self.values)
                if self.values else None}


NORMALIZE_COLUMNS = ["demo_id", "command_row_id", "round_id", "steam_id", "player_slot", "demo_tick",
                     "command_number", "client_tick", "pawn_entity_handle", "view_yaw", "view_pitch",
                     "mousedx_raw", "mousedy_raw"]


@exclusive_output()
def normalize(parsed: Path, out: Path, max_gap_ticks: int = 4) -> dict[str, Any]:
    if max_gap_ticks < 1:
        raise ValueError("max_gap_ticks must be positive")
    manifest = parsed_manifest(parsed, ["usercmd.parquet"])
    demo_id = manifest["demo_id"]
    raw = parsed / "usercmd.parquet"
    require_columns(raw, NORMALIZE_COLUMNS)
    optional = [key for key in ("base_present", "viewangles_present", "alive") if key in pq.read_schema(raw).names]
    destinations = [out / "normalized_actions.parquet", out / "normalization_report.json"]
    staged = staging_paths(destinations)
    destination, report_path = staged
    schema = versioned_schema([
        ("demo_id", pa.string()), ("command_row_id", pa.int64()), ("round_id", pa.int32()),
        ("steam_id", pa.uint64()), ("player_slot", pa.int32()), ("demo_tick", pa.int64()),
        ("previous_command_row_id", pa.int64()), ("delta_yaw_deg", pa.float32()),
        ("delta_pitch_deg", pa.float32()), ("aim_valid", pa.bool_()), ("reset_reason", pa.string()),
        ("mousedx_raw", pa.int32()), ("mousedy_raw", pa.int32()),
        ("mousedx_effective", pa.int32()), ("mousedy_effective", pa.int32()),
    ], "normalization", demo_id, schema_version=2)
    schema = schema.with_metadata({**schema.metadata, b"training_ready": b"false", b"normalizer_version": b"2"})
    previous_by_slot: dict[int, dict[str, Any]] = {}
    reasons: Counter[str] = Counter()
    estimates = defaultdict(lambda: (RobustSamples(), RobustSamples()))
    count = 0
    last_row_id = -1
    with destination.open("xb") as handle, pq.ParquetWriter(handle, schema, compression="zstd") as writer:
        for rows in batches(raw, NORMALIZE_COLUMNS + optional):
            normalized = []
            for row in rows:
                if row["demo_id"] != demo_id:
                    raise ValueError("Command demo_id disagrees with parsed manifest")
                if row["command_row_id"] <= last_row_id:
                    raise ValueError("Canonical command_row_id must be unique and increase in file order")
                last_row_id = row["command_row_id"]
                previous = previous_by_slot.get(row["player_slot"])
                yaw, pitch, reason = transition(previous, row, max_gap_ticks)
                reasons[reason or "valid"] += 1
                normalized.append({
                    **{key: row[key] for key in ("demo_id", "command_row_id", "round_id", "steam_id",
                                                "player_slot", "demo_tick", "mousedx_raw", "mousedy_raw")},
                    "previous_command_row_id": previous["command_row_id"] if previous and not reason else None,
                    "delta_yaw_deg": yaw, "delta_pitch_deg": pitch, "aim_valid": reason is None,
                    "reset_reason": reason,
                    "mousedx_effective": effective_scalar(row, "mousedx_raw", "base_present"),
                    "mousedy_effective": effective_scalar(row, "mousedy_raw", "base_present"),
                })
                if reason is None:
                    xs, ys = estimates[str(row["steam_id"])]
                    if row["mousedx_raw"] and 0 < abs(yaw) <= 45:
                        xs.add(yaw / row["mousedx_raw"])
                    if row["mousedy_raw"] and 0 < abs(pitch) <= 30:
                        ys.add(pitch / row["mousedy_raw"])
                previous_by_slot[row["player_slot"]] = row
                count += 1
            writer.write_table(pa.Table.from_pylist(normalized, schema=schema))
    report = {
        "status": "complete", "demo_id": demo_id, "normalizer_version": 2, "processing_version": __version__,
        "command_count": count, "transitions": dict(reasons), "max_gap_demo_ticks": max_gap_ticks,
        "delta_attachment": "current_command_minus_previous_command",
        "protobuf_parent_presence_available": "base_present" in optional,
        "source_usercmd_sha256": sha256_file(raw),
        "files": {"normalized_actions.parquet": sha256_file(destination)},
        "source_parser_schema_version": manifest.get("parser_schema_version"),
        "training_ready": False,
        "source_parser_warnings": manifest.get("warnings", {}),
        "source_validation": read_json(parsed / "validation.json") if (parsed / "validation.json").exists() else {"status": "unavailable"},
        "effective_mouse_scale": {key: {"x": pair[0].report(), "y": pair[1].report()}
                                  for key, pair in estimates.items()},
        "notes": ["Effective mouse scale is a diagnostic; it does not identify physical DPI or exact sensitivity.",
                  "Raw null scalar means absent protobuf field; effective zero is derived only with known parent-message presence.",
                  "Null aim targets are masked reset boundaries, not zero rotation."],
    }
    write_json(report_path, report)
    publish(staged, destinations)
    return report
