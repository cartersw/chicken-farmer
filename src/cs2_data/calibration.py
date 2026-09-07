"""Versioned execution-clock candidates and independently measured clock anchors.

A constant packet/execution offset is useful evidence of clock domains, not proof
of visual timing. No calibration produced here certifies a training sample.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import statistics
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds

from .io import (exclusive_output, parsed_manifest, publish, read_json, sha256_file,
                 staging_paths, write_json)

IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot")


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def identity_filter(identity: dict[str, Any]):
    return ((ds.field("demo_id") == identity["demo_id"]) &
            (ds.field("round_id") == identity["round_id"]) &
            (ds.field("steam_id") == pa.scalar(int(identity["steam_id"]), type=pa.uint64())) &
            (ds.field("player_slot") == identity["player_slot"]))


def validate_command_clock(commands: list[dict[str, Any]], max_gap_ticks: int = 4) -> None:
    if len(commands) < 3:
        raise ValueError("Execution calibration requires at least three canonical commands")
    if max_gap_ticks < 1:
        raise ValueError("max_gap_ticks must be positive")
    previous = None
    for row in commands:
        if any(str(row.get(key)) != str(commands[0].get(key)) for key in IDENTITY):
            raise ValueError("Execution calibration crosses player/demo/round identity")
        for key in ("command_row_id", "command_number", "demo_tick", "client_tick", "server_tick_executed"):
            value = row.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Missing/invalid canonical {key}")
        if row["server_tick_executed"] == 0:
            raise ValueError("Zero server_tick_executed has unavailable upstream semantics")
        if previous is not None:
            if row["command_row_id"] <= previous["command_row_id"]:
                raise ValueError("Canonical command row IDs must increase without duplicates")
            if row["command_number"] != previous["command_number"] + 1:
                raise ValueError("Command number gap/reset requires a separate calibration segment")
            if row["client_tick"] < previous["client_tick"]:
                raise ValueError("Client clock reset requires a separate calibration segment")
            if row.get("pawn_entity_handle") != previous.get("pawn_entity_handle"):
                raise ValueError("Pawn identity change requires a separate calibration segment")
            for clock in ("demo_tick", "server_tick_executed"):
                gap = row[clock] - previous[clock]
                if gap < 0 or gap > max_gap_ticks:
                    raise ValueError(f"{clock} reversal/gap requires a separate calibration segment")
        previous = row


def clock_diagnostics(commands: list[dict[str, Any]]) -> dict[str, Any]:
    def counts(values):
        return {str(key): count for key, count in sorted(Counter(values).items())}
    return {
        "server_minus_packet_ticks": counts(row["server_tick_executed"] - row["demo_tick"] for row in commands),
        "client_minus_server_ticks": counts(row["client_tick"] - row["server_tick_executed"] for row in commands),
        "history_render_minus_server_ticks": counts(
            entry["render_tick_count"] - row["server_tick_executed"]
            for row in commands for entry in row.get("input_history", [])
            if entry.get("render_tick_count") is not None),
        "history_player_minus_server_ticks": counts(
            entry["player_tick_count"] - row["server_tick_executed"]
            for row in commands for entry in row.get("input_history", [])
            if entry.get("player_tick_count") is not None),
        "negative_subtick_count": sum(1 for row in commands for event in row.get("subtick_moves", [])
                                      if finite_number(event.get("when")) and event["when"] < 0),
    }


def fit_offset(commands: list[dict[str, Any]], anchors: dict[str, Any] | None = None,
               max_gap_ticks: int = 4) -> dict[str, Any]:
    validate_command_clock(commands, max_gap_ticks)
    if anchors is None:
        offsets = {row["demo_tick"] - row["server_tick_executed"] for row in commands}
        if len(offsets) != 1:
            raise ValueError("Packet/execution offset changes; split the segment or supply independent measured anchors")
        return {"method": "paired_packet_execution_clocks", "mapping_status": "inferred_from_packet_arrival",
                "offset_demo_ticks": offsets.pop(), "max_anchor_residual_ticks": None,
                "anchor_count": 0, "execution_timing_verified": False}
    if anchors.get("schema_version") != 1 or anchors.get("measurement_method") != "engine_command_execution_hook":
        raise ValueError("Measured anchors require schema 1 engine_command_execution_hook evidence")
    if any(str(anchors.get(key)) != str(commands[0].get(key)) for key in IDENTITY):
        raise ValueError("Anchor identity disagrees with canonical commands")
    records = anchors.get("anchors", [])
    if len(records) < 3:
        raise ValueError("At least three independent execution anchors are required")
    by_id = {row["command_row_id"]: row for row in commands}
    seen = set()
    offsets = []
    previous_tick = None
    for anchor in records:
        row_id = anchor.get("command_row_id")
        row = by_id.get(row_id)
        if row is None or row_id in seen or anchor.get("server_tick_executed") != row["server_tick_executed"]:
            raise ValueError("Anchor must reference a unique, matching canonical command")
        seen.add(row_id)
        tick, uncertainty = anchor.get("source_demo_tick"), anchor.get("uncertainty_ticks")
        if not finite_number(tick) or tick < 0 or not finite_number(uncertainty) or not 0 <= uncertainty <= 0.5:
            raise ValueError("Execution anchor needs a finite replay tick and uncertainty <= 0.5 tick")
        if previous_tick is not None and tick <= previous_tick:
            raise ValueError("Execution anchors must increase without clock resets")
        previous_tick = tick
        offsets.append(tick - row["server_tick_executed"])
    if records[0]["command_row_id"] != commands[0]["command_row_id"] or records[-1]["command_row_id"] != commands[-1]["command_row_id"]:
        raise ValueError("Measured anchors must bracket the complete canonical calibration segment")
    offset = statistics.median(offsets)
    residuals = [abs(value-offset) for value in offsets]
    if any(residual > anchor["uncertainty_ticks"] + 1e-9 for residual, anchor in zip(residuals, records)):
        raise ValueError("Measured execution anchors disagree beyond their stated uncertainty")
    return {"method": anchors["measurement_method"], "mapping_status": "measured_execution_clock",
            "offset_demo_ticks": offset, "anchor_count": len(records),
            "max_anchor_residual_ticks": max(residuals), "execution_timing_verified": True}


@exclusive_output()
def calibrate(parsed: Path, out: Path, round_id: int, steam_id: int, player_slot: int,
              start_demo_tick: int, end_demo_tick: int, anchors: Path | None = None,
              max_gap_ticks: int = 4) -> dict[str, Any]:
    if not 0 <= start_demo_tick < end_demo_tick or round_id < 1 or not 0 < int(steam_id) < 2**64:
        raise ValueError("Invalid calibration identity or packet-tick interval")
    manifest = parsed_manifest(parsed, ["usercmd.parquet"])
    identity = {"demo_id": manifest["demo_id"], "round_id": round_id,
                "steam_id": str(steam_id), "player_slot": player_slot}
    predicate = identity_filter(identity) & (ds.field("demo_tick") >= start_demo_tick) & (ds.field("demo_tick") < end_demo_tick)
    commands = ds.dataset(parsed / "usercmd.parquet", format="parquet").to_table(filter=predicate).to_pylist()
    anchor_data = read_json(anchors) if anchors else None
    evidence = []
    if anchor_data is not None:
        for item in anchor_data.get("evidence_files", []):
            path = Path(item["path"])
            path = path if path.is_absolute() else anchors.parent / path
            if not path.is_file() or sha256_file(path) != item.get("sha256"):
                raise ValueError("Execution anchor evidence file/hash mismatch")
            evidence.append({"path": str(path.resolve()), "sha256": item["sha256"]})
        if not evidence or not anchor_data.get("capture_method") or not anchor_data.get("plugin_sha256"):
            raise ValueError("Measured execution anchors need hashed raw evidence, capture method and plugin provenance")
    fit = fit_offset(commands, anchor_data, max_gap_ticks)
    offset = fit["offset_demo_ticks"]
    report = {
        "status": "complete", "schema_version": 1, "calibration_version": 1, **identity, **fit,
        "source_usercmd_sha256": manifest["files"]["usercmd.parquet"],
        "command_count": len(commands), "first_command_row_id": commands[0]["command_row_id"],
        "last_command_row_id": commands[-1]["command_row_id"],
        "packet_interval": [start_demo_tick, end_demo_tick],
        "execution_interval": [commands[0]["server_tick_executed"], commands[-1]["server_tick_executed"]],
        "mapped_interval": [commands[0]["server_tick_executed"] + offset, commands[-1]["server_tick_executed"] + offset],
        "max_gap_ticks": max_gap_ticks, "diagnostics": clock_diagnostics(commands),
        "anchors_path": str(anchors.resolve()) if anchors else None,
        "anchors_sha256": sha256_file(anchors) if anchors else None, "evidence_files": evidence,
        "training_ready": False,
        "remaining_validation": ["Measured movie-frame correspondence and observation phase",
                                 "Visual action/POV checks and per-label data quality",
                                 "Input-history rendering clock describes original client view, not replay captures"],
    }
    destinations = [out / "execution_calibration.json"]
    staged = staging_paths(destinations)
    write_json(staged[0], report)
    publish(staged, destinations)
    return report


def load_calibration(path: Path, parsed_manifest_data: dict[str, Any], clip: dict[str, Any],
                     commands: list[dict[str, Any]]) -> dict[str, Any]:
    path = path / "execution_calibration.json" if path.is_dir() else path
    report = read_json(path)
    if report.get("status") != "complete" or report.get("calibration_version") != 1 or report.get("schema_version") != 1:
        raise ValueError("Incomplete or unsupported execution calibration")
    if any(str(report.get(key)) != str(clip.get(key)) for key in IDENTITY):
        raise ValueError("Execution calibration identity disagrees with clip")
    if report.get("source_usercmd_sha256") != parsed_manifest_data["files"]["usercmd.parquet"]:
        raise ValueError("Execution calibration source hash disagrees with canonical commands")
    selected = [row for row in commands if report["first_command_row_id"] <= row["command_row_id"] <= report["last_command_row_id"]]
    if len(selected) != report.get("command_count"):
        raise ValueError("Execution calibration canonical segment is incomplete")
    anchor_data = None
    if report.get("anchors_path"):
        anchor_path = Path(report["anchors_path"])
        if sha256_file(anchor_path) != report.get("anchors_sha256"):
            raise ValueError("Execution calibration anchor hash mismatch")
        anchor_data = read_json(anchor_path)
        for evidence in report.get("evidence_files", []):
            if sha256_file(Path(evidence["path"])) != evidence["sha256"]:
                raise ValueError("Execution calibration evidence hash mismatch")
        if not report.get("evidence_files"):
            raise ValueError("Measured calibration has no execution evidence")
    expected = fit_offset(selected, anchor_data, report["max_gap_ticks"])
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Execution calibration fit/status was altered; recomputation disagrees")
    for name, actual in (("execution_interval", [selected[0]["server_tick_executed"], selected[-1]["server_tick_executed"]]),
                         ("mapped_interval", [selected[0]["server_tick_executed"] + expected["offset_demo_ticks"],
                                              selected[-1]["server_tick_executed"] + expected["offset_demo_ticks"]])):
        if report.get(name) != actual:
            raise ValueError("Execution calibration domain disagrees with canonical segment")
    return {**report, "calibration_path": str(path.resolve()), "calibration_sha256": sha256_file(path)}
