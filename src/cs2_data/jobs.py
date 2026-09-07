"""Derive conservative, alive player-round intervals for an external CS2 renderer."""

from __future__ import annotations

import hashlib
import bisect
import json
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

from .io import batches, exclusive_output, parsed_manifest, publish, read_json, require_columns, sha256_file, staging_paths


def classify_round_phases(events: list[dict[str, Any]],
                          rounds: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Require live game rules and a retained scored result, never a round ordinal.

    In the supplied ESL demos knife play has IsMatchStarted=True and IsWarmup=False,
    but GamePhase=Pregame (1). cs_pre_restart also occurs before ordinary rounds;
    it is retained as evidence but deliberately does not imply an abandoned round.
    """
    groups: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    previous_tick = -1
    rollbacks = []
    previous_total = 0
    for index, event in enumerate(events):
        for field in ("demo_tick", "round_id", "total_rounds_played", "game_phase", "score_ct", "score_t"):
            if type(event.get(field)) is not int or event[field] < 0:
                raise ValueError(f"Phase event has invalid {field}")
        if not isinstance(event.get("kind"), str) or any(type(event.get(field)) is not bool for field in ("is_match_started", "is_warmup")):
            raise ValueError("Phase event has missing rule state or kind")
        if event["demo_tick"] < previous_tick:
            raise ValueError("Phase event timeline reverses")
        previous_tick = event["demo_tick"]
        if event["total_rounds_played"] < previous_total:
            rollbacks.append((index, event))
        previous_total = event["total_rounds_played"]
        groups.setdefault(event["round_id"], []).append((index, event))

    def reference(pair: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, event = pair
        return {"event_index": index, **{key: event[key] for key in
                ("kind", "demo_tick", "game_phase", "is_match_started", "is_warmup", "total_rounds_played", "score_ct", "score_t")}}

    result = {}
    for round_id, info in rounds.items():
        group = groups.get(round_id, [])
        phase = {"phase": "unknown", "phase_verified": False, "classifier": "retained-live-round-v1",
                 "reason_codes": [], "evidence": []}
        result[round_id] = phase
        starts = [pair for pair in group if pair[1]["kind"] == "round_start"]
        if len(starts) != 1:
            phase["reason_codes"].append("missing_or_ambiguous_round_start")
            continue
        start_pair, start = starts[0], starts[0][1]
        if start["demo_tick"] != info.get("start_tick"):
            raise ValueError("Phase round start disagrees with canonical rounds")
        phase["evidence"].append(reference(start_pair))
        if start["is_warmup"] or start["game_phase"] in (0, 1):
            phase.update(phase="setup", reason_codes=["warmup_or_pregame_rules"])
            continue
        freeze_tick, end_tick = info.get("freeze_end_tick"), info.get("end_tick")
        freezes = [pair for pair in group if pair[1]["kind"] == "freeze_end" and pair[1]["demo_tick"] == freeze_tick]
        ends = [pair for pair in group if pair[1]["kind"] == "round_end" and pair[1]["demo_tick"] == end_tick]
        if (freeze_tick is None or end_tick is None or not start["demo_tick"] <= freeze_tick < end_tick or
                len(freezes) != 1 or len(ends) != 1):
            phase["reason_codes"].append("missing_or_mismatched_round_boundaries")
            continue
        phase["evidence"].extend((reference(freezes[0]), reference(ends[0])))
        reason = ends[0][1].get("detail", {}).get("reason")
        if reason == 16:
            phase.update(phase="restarted", reason_codes=["round_end_reason_game_start"])
            continue
        active = [event for _, event in group if freeze_tick <= event["demo_tick"] < end_tick]
        if not active or any(event["game_phase"] not in (2, 3) or not event["is_match_started"] or event["is_warmup"] for event in active):
            phase["reason_codes"].append("live_rules_not_stable_during_play")
            continue
        if reason not in (1, 7, 8, 9, 11, 12, 13, 17, 18):
            phase["reason_codes"].append("unverified_competitive_round_end_reason")
            continue
        start_total = start["total_rounds_played"]
        phase["competitive_round_number"] = start_total + 1
        scored = [pair for pair in group if pair[1]["demo_tick"] >= end_tick and
                  pair[1]["total_rounds_played"] == start_total + 1 and
                  pair[1]["score_ct"] + pair[1]["score_t"] == start_total + 1]
        if start["score_ct"] + start["score_t"] != start_total or not scored:
            phase["reason_codes"].append("missing_consistent_scored_result")
            continue
        phase["evidence"].append(reference(scored[0]))
        # A backup restoration or match reset discards the scores above its restored
        # total, including rounds that looked live before the later rollback arrived.
        rollback = next((pair for pair in rollbacks if pair[1]["demo_tick"] >= end_tick and
                         pair[1]["total_rounds_played"] <= start_total), None)
        if rollback is not None:
            phase.update(phase="restarted", reason_codes=["scored_result_later_rolled_back"])
            phase["evidence"].append(reference(rollback))
            continue
        phase.update(phase="competitive", phase_verified=True,
                     reason_codes=["live_game_phase_2_or_3", "match_started_without_warmup", "retained_scored_result"])
    return result


def phase_evidence(path: Path | None, manifest: dict[str, Any],
                   rounds: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if path is None:
        return {round_id: {"phase": "unknown", "phase_verified": False,
                           "reason_codes": ["phase_sidecar_not_supplied"], "evidence": []}
                for round_id in rounds}
    audit = read_json(path)
    if (audit.get("schema_version") != 1 or audit.get("parse_status") != "complete" or
            audit.get("partial") is not False or audit.get("timing_clock") != "demo_tick" or
            audit.get("parser") != "demoinfocs-golang/v6" or audit.get("parser_version") != "v6.0.0-alpha.0"):
        raise ValueError("Phase sidecar must be a complete supported cs2-phases artifact")
    if (audit.get("demo_id") != manifest["demo_id"] or
            audit.get("source_demo_sha256") != manifest.get("sha256", manifest["demo_id"])):
        raise ValueError("Phase sidecar source SHA256 disagrees with parsed manifest")
    events = audit.get("events")
    if not isinstance(events, list) or not events or events[-1].get("kind") != "demo_end":
        raise ValueError("Phase sidecar has no complete event timeline")
    result = classify_round_phases(events, rounds)
    digest = sha256_file(path)
    for phase in result.values():
        phase.update(source_phase_path=str(path.resolve()), source_phase_sha256=digest)
    return result


def alive_windows(states: Iterable[dict[str, Any]], rounds: dict[int, dict[str, Any]],
                  demo_id: str, max_state_gap_ticks: int = 1) -> list[dict[str, Any]]:
    """End windows on death, identity changes, pauses, round changes, or missing state."""
    if max_state_gap_ticks < 1:
        raise ValueError("max_state_gap_ticks must be positive")
    active: dict[int, dict[str, Any]] = {}
    previous_tick: dict[int, int] = {}
    result: list[dict[str, Any]] = []

    def close(slot: int, boundary: int | None = None) -> None:
        window = active.pop(slot, None)
        if window is not None:
            end = min(window.pop("last_tick") + 1, window.pop("round_end_tick"))
            if boundary is not None:
                end = min(end, boundary)
            window["end_demo_tick"] = end
            if end > window["start_demo_tick"]:
                result.append(window)

    for state in states:
        if state["demo_id"] != demo_id:
            raise ValueError("Player state demo_id disagrees with parsed manifest")
        slot, tick = state["player_slot"], state["demo_tick"]
        if slot in previous_tick and tick <= previous_tick[slot]:
            raise ValueError("Player-state demo ticks must strictly increase for each slot")
        previous_tick[slot] = tick
        round_info = rounds.get(state["round_id"])
        freeze_end = round_info.get("freeze_end_tick") if round_info else None
        round_end = round_info.get("end_tick") if round_info else None
        valid = (state.get("alive") is True and bool(state.get("steam_id")) and
                 state.get("spectator_user_id") is not None and round_info is not None and
                 freeze_end is not None and round_end is not None and freeze_end <= tick < round_end and
                 not any(state.get(flag) is True for flag in ("is_warmup", "is_freeze_time", "is_paused")))
        window = active.get(slot)
        if window and (not valid or any(str(window[key]) != str(state.get(key)) for key in
                                         ("steam_id", "round_id", "spectator_user_id")) or
                       tick - window["last_tick"] > max_state_gap_ticks):
            close(slot, tick)
        if not valid:
            continue
        if slot not in active:
            active[slot] = {
                "demo_id": demo_id, "round_id": state["round_id"], "steam_id": str(state["steam_id"]),
                "player_slot": slot, "spectator_user_id": state["spectator_user_id"],
                "start_demo_tick": tick, "round_end_tick": round_end, "last_tick": tick,
                "map": round_info.get("map"), "team": state.get("team"),
                "pause_state_verified": state.get("is_paused") is not None,
            }
        active[slot]["last_tick"] = tick
        active[slot]["pause_state_verified"] &= state.get("is_paused") is not None
    for slot in list(active):
        close(slot)
    return sorted(result, key=lambda row: (row["round_id"], row["start_demo_tick"], row["player_slot"]))


def command_coverage(windows: list[dict[str, Any]], commands: Iterable[dict[str, Any]],
                     demo_id: str, max_gap_ticks: int = 4) -> None:
    """Audit observed command ticks for each proposed interval; never manufacture missing commands."""
    if max_gap_ticks < 1:
        raise ValueError("max_command_gap_ticks must be positive")
    by_slot: dict[int, list[dict[str, Any]]] = {}
    for window in windows:
        by_slot.setdefault(window["player_slot"], []).append(window)
        window["command_coverage"] = {"command_count": 0, "observed_demo_ticks": 0,
                                      "first_demo_tick": None, "last_demo_tick": None, "max_gap_demo_ticks": 0}
    for group in by_slot.values():
        group.sort(key=lambda row: row["start_demo_tick"])
    starts = {slot: [window["start_demo_tick"] for window in group] for slot, group in by_slot.items()}
    for command in commands:
        if command["demo_id"] != demo_id:
            raise ValueError("Command coverage source belongs to another demo")
        slot, tick = command["player_slot"], command["demo_tick"]
        index = bisect.bisect_right(starts.get(slot, []), tick) - 1
        if index < 0:
            continue
        window = by_slot[slot][index]
        if tick >= window["end_demo_tick"] or command["round_id"] != window["round_id"] or str(command["steam_id"]) != window["steam_id"]:
            continue
        coverage = window["command_coverage"]
        previous = coverage["last_demo_tick"]
        if previous is not None and tick < previous:
            raise ValueError("Command coverage timeline reverses")
        if previous is None:
            coverage["first_demo_tick"] = tick
        if tick != previous:
            coverage["observed_demo_ticks"] += 1
        if previous is not None:
            coverage["max_gap_demo_ticks"] = max(coverage["max_gap_demo_ticks"], tick - previous)
        coverage["last_demo_tick"] = tick
        coverage["command_count"] += 1
    for window in windows:
        coverage = window["command_coverage"]
        first, last = coverage["first_demo_tick"], coverage["last_demo_tick"]
        if first is not None:
            coverage["max_gap_demo_ticks"] = max(coverage["max_gap_demo_ticks"],
                                                   first - window["start_demo_tick"], window["end_demo_tick"] - last)
        coverage["complete_within_gap_limit"] = first is not None and coverage["max_gap_demo_ticks"] <= max_gap_ticks
        coverage["gap_limit_demo_ticks"] = max_gap_ticks
        coverage["observed_tick_fraction"] = coverage["observed_demo_ticks"] / (window["end_demo_tick"] - window["start_demo_tick"])


@exclusive_output(file_output=True)
def render_jobs(parsed: Path, out: Path, demo: Path | None = None, fps: int = 32,
                width: int = 1280, height: int = 720, min_ticks: int = 32,
                limit: int | None = None, round_id: int | None = None,
                steam_id: int | None = None, max_state_gap_ticks: int = 1,
                max_command_gap_ticks: int = 4, allow_incomplete_commands: bool = False,
                phase_manifest: Path | None = None, allow_unverified_phase: bool = False,
                start_demo_tick: int | None = None, end_demo_tick: int | None = None) -> dict[str, Any]:
    if fps <= 0 or width <= 0 or height <= 0 or min_ticks < 1 or (limit is not None and limit < 1):
        raise ValueError("FPS, resolution, min_ticks, and limit must be positive")
    if (start_demo_tick is None) != (end_demo_tick is None) or (start_demo_tick is not None and
            (start_demo_tick < 0 or end_demo_tick <= start_demo_tick)):
        raise ValueError("Supply both start_demo_tick and exclusive end_demo_tick as an increasing interval")
    if phase_manifest is None and not allow_unverified_phase:
        raise ValueError("Competitive jobs require --phase-manifest from cs2-phases; --allow-unverified-phase permits explicit diagnostic setup/unknown jobs")
    manifest = parsed_manifest(parsed, ["rounds.parquet", "player_state.parquet", "usercmd.parquet"])
    if demo is None:
        value = next((manifest[key] for key in ("demo_path", "source_path", "input_path") if manifest.get(key)), None)
        if not value:
            raise ValueError("Parsed manifest has no demo path; supply --demo PATH")
        demo = Path(value)
    if not demo.is_file() or demo.suffix.lower() != ".dem":
        raise ValueError(f"Source .dem file is missing: {demo}")
    if sha256_file(demo) != manifest.get("sha256", manifest["demo_id"]):
        raise ValueError("Source .dem SHA256 disagrees with parsed manifest")
    rounds_path, states_path = parsed / "rounds.parquet", parsed / "player_state.parquet"
    require_columns(rounds_path, ["demo_id", "round_id", "freeze_end_tick", "end_tick"])
    require_columns(states_path, ["demo_id", "demo_tick", "round_id", "steam_id", "player_slot", "alive", "spectator_user_id"])
    round_rows = pq.read_table(rounds_path).to_pylist()
    if any(row["demo_id"] != manifest["demo_id"] for row in round_rows):
        raise ValueError("Round table demo_id disagrees with manifest")
    rounds = {row["round_id"]: row for row in round_rows}
    if len(rounds) != len(round_rows):
        raise ValueError("Duplicate round_id in rounds table")
    phases = phase_evidence(phase_manifest, manifest, rounds)
    fields = [name for name in ("demo_id", "demo_tick", "round_id", "steam_id", "player_slot", "alive",
                                "spectator_user_id", "is_warmup", "is_freeze_time", "is_paused", "team")
              if name in pq.read_schema(states_path).names]
    windows = alive_windows((row for batch in batches(states_path, fields) for row in batch), rounds,
                            manifest["demo_id"], max_state_gap_ticks)
    rejected_phase_windows = sum(not phases[window["round_id"]]["phase_verified"] for window in windows)
    windows = [window for window in windows if allow_unverified_phase or phases[window["round_id"]]["phase_verified"]]
    for window in windows:
        window["phase_evidence"] = phases[window["round_id"]]
        if start_demo_tick is not None:
            window["start_demo_tick"] = max(window["start_demo_tick"], start_demo_tick)
            window["end_demo_tick"] = min(window["end_demo_tick"], end_demo_tick)
    windows = [window for window in windows if window["end_demo_tick"] > window["start_demo_tick"]]
    cmd_fields = ["demo_id", "demo_tick", "round_id", "steam_id", "player_slot"]
    require_columns(parsed / "usercmd.parquet", cmd_fields)
    command_coverage(windows, (row for batch in batches(parsed / "usercmd.parquet", cmd_fields) for row in batch),
                     manifest["demo_id"], max_command_gap_ticks)
    jobs = []
    for window in windows:
        if window["end_demo_tick"] - window["start_demo_tick"] < min_ticks:
            continue
        if round_id is not None and window["round_id"] != round_id:
            continue
        if steam_id is not None and int(window["steam_id"]) != steam_id:
            continue
        if not allow_incomplete_commands and not window["command_coverage"]["complete_within_gap_limit"]:
            continue
        key = ":".join(str(window[field]) for field in
                       ("demo_id", "round_id", "steam_id", "player_slot", "start_demo_tick", "end_demo_tick"))
        key += f":{fps}:{width}:{height}:render-v1"
        jobs.append({"schema_version": 1, **window, "demo_path": str(demo.resolve()),
                     "clip_id": hashlib.sha256(key.encode()).hexdigest()[:24],
                     "fps": fps, "width": width, "height": height, "timing_clock": "demo_tick",
                     "renderer_profile": "render-v1", "training_ready": False,
                     "source_parser_warnings": manifest.get("warnings", {}),
                     "source_validation_status": manifest.get("validation_status", "unavailable")})
        if limit is not None and len(jobs) >= limit:
            break
    if not jobs:
        raise ValueError("No eligible competitive alive intervals with resolved identity, complete rounds, known freeze-end ticks, and command coverage; --allow-unverified-phase and --allow-incomplete-commands permit explicit diagnostic jobs")
    staged = staging_paths([out])
    with staged[0].open("x", encoding="utf-8", newline="\n") as handle:
        for job in jobs:
            handle.write(json.dumps(job, allow_nan=False) + "\n")
    publish(staged, [out])
    return {"demo_id": manifest["demo_id"], "job_count": len(jobs), "output": str(out),
            "unverified_phase_windows": rejected_phase_windows,
            "unverified_phase_jobs": sum(not job["phase_evidence"]["phase_verified"] for job in jobs),
            "incomplete_command_windows": sum(not window["command_coverage"]["complete_within_gap_limit"] for window in windows),
            "pause_state_unverified_jobs": sum(not job["pause_state_verified"] for job in jobs)}
