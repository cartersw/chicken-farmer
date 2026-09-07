"""Internal temporal selection from a caller-verified clock/support contract.

This module performs no evidence decoding, publication, or training acceptance.
Its caller must independently verify the raw capture/NET clock evidence before
supplying bounds. A dictionary's ``verified`` status is only an interface
precondition here, never sufficient evidence to approve a training artifact.
Every result retains ``training_ready=False``. Action normalization, observation
quality, and model feature/target projection belong to the acceptance layer.
"""
from __future__ import annotations

import math
import re
from typing import Any, Sequence


IDENTITY = ("demo_id", "round_id", "steam_id", "player_slot")
POLICY = "first_complete_future_command_v1"
FUTURE_PREDECESSOR_POLICY = "first_complete_future_command_and_predecessor_v1"


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _identity(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(row.get(key) is not None and str(row[key]) == str(expected[key]) for key in IDENTITY)


def _fraction_reasons(command: dict[str, Any]) -> set[str]:
    """Invalid nested time values matter only when this command is selected.

    Omitted scalar fields inside an existing protobuf submessage have their
    defined zero default; a missing repeated-message collection is unknown.
    Raw values are inspected without clamping, rescaling, or splitting them.
    """
    reasons: set[str] = set()
    for field, fractions in (("subtick_moves", ("when",)),
                             ("input_history", ("render_tick_fraction", "player_tick_fraction"))):
        entries = command.get(field)
        if not isinstance(entries, list):
            reasons.add("missing_" + field)
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                reasons.add("invalid_" + field)
                continue
            for name in fractions:
                value = entry.get(name)
                if value is not None and (not _finite(value) or not 0 <= value <= 1):
                    reasons.add("invalid_subtick_fraction" if field == "subtick_moves" else "invalid_history_fraction")
    return reasons


def _validate_inputs(commands, frames, identity, contract):
    if any(identity.get(key) is None for key in IDENTITY):
        raise ValueError("Causal selection needs complete demo/player/round identity")
    if (type(contract.get("schema_version")) is not int or contract["schema_version"] != 1 or contract.get("status") != "verified"
            or contract.get("support_interval") != "[N-1,N]"
            or contract.get("execution_clock") != "server_tick_executed"
            or not _identity(contract, identity)
            or not re.fullmatch(r"[0-9a-f]{64}", str(contract.get("contract_sha256", "")))):
        raise ValueError("Causal selection requires the caller-verified whole-command support contract")
    previous = None
    issues: dict[int, set[str]] = {}
    for row in commands:
        if not isinstance(row, dict) or not _identity(row, identity):
            raise ValueError("Canonical command identity disagrees with the causal contract")
        for key in ("command_row_id", "command_number", "client_tick", "server_tick_executed", "pawn_entity_handle"):
            if type(row.get(key)) is not int or row[key] < 0:
                raise ValueError("Missing or invalid canonical " + key)
        if row["server_tick_executed"] == 0:
            raise ValueError("Unknown zero command execution tick")
        if previous is not None and row["command_row_id"] <= previous["command_row_id"]:
            raise ValueError("Canonical command IDs must increase without duplication")
        # A reversal cannot be ordered within this clock segment. A forward gap
        # remains local so an unrelated missing/invalid earlier row cannot erase
        # later usable commands. Targets depending on that gap are rejected.
        if previous is not None and row["server_tick_executed"] < previous["server_tick_executed"]:
            raise ValueError("Execution clock reversal requires a separate command segment")
        local = _fraction_reasons(row)
        if previous is None:
            local.add("missing_command_predecessor")
        else:
            if row["command_number"] != previous["command_number"] + 1:
                local.add("command_number_discontinuity")
            if row["server_tick_executed"] - previous["server_tick_executed"] > 1:
                local.add("server_tick_executed_discontinuity")
            if row["client_tick"] < previous["client_tick"]:
                local.add("client_tick_reset")
            if row["pawn_entity_handle"] != previous["pawn_entity_handle"]:
                local.add("pawn_identity_changed")
        issues[row["command_row_id"]] = local
        previous = row
    previous = None
    last_verified_by_segment: dict[str, dict[str, Any]] = {}
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict) or not _identity(frame, identity):
            raise ValueError("Frame identity disagrees with the causal contract")
        if type(frame.get("frame_index")) is not int or frame["frame_index"] != index:
            raise ValueError("Frame indices must be contiguous and begin at zero")
        if frame.get("contract_sha256") != contract["contract_sha256"]:
            raise ValueError("Frame bound belongs to a different evidence contract")
        segment = frame.get("clock_segment_id")
        if not isinstance(segment, str) or not segment:
            raise ValueError("Frame bound requires an explicit clock segment")
        start, end = frame.get("observation_upper_execution_tick"), frame.get("next_observation_upper_execution_tick")
        if frame.get("bound_status") == "verified":
            if not _finite(start) or not _finite(end) or not 0 <= start < end:
                raise ValueError("Verified frame bounds must be finite, nonnegative and increasing")
            last_verified = last_verified_by_segment.get(segment)
            if last_verified is not None and start < last_verified["next_observation_upper_execution_tick"]:
                raise ValueError("Frame clock reversal requires a new verified clock segment")
            if (previous is not None and previous.get("bound_status") == "verified"
                    and previous["clock_segment_id"] == segment
                    and previous["next_observation_upper_execution_tick"] != start):
                raise ValueError("Same-segment frame bounds have a gap, overlap or reversal")
            last_verified_by_segment[segment] = frame
        previous = frame
    return issues


def select_causal_targets(commands: Sequence[dict[str, Any]], frame_bounds: Sequence[dict[str, Any]],
                          *, identity: dict[str, Any], support_contract: dict[str, Any],
                          history_frames: int = 8, target_horizon_frames: int = 2,
                          require_future_predecessor: bool = False) -> list[dict[str, Any]]:
    """Choose the first complete command after each verified observation bound.

    Inputs are canonical commands in source order and caller-verified bounds in
    capture order. ``next_observation_upper_execution_tick`` must come from an
    actual next boundary or endpoint; this helper never extrapolates a clock.

    A command's complete support is [N-1,N]. Its beginning must be STRICTLY
    greater than the image's conservative upper bound. At equality, the command
    is excluded. The horizon deadline is the recorded bound after the requested
    number of frame intervals, rather than a claim of exact image exposure time.

    Only selected commands' fractions and predecessor continuity affect their
    candidate. Invalid unselected overlap commands are retained diagnostically.
    No preceding-action rows are features in policy v1. Raw input_history is
    provenance, not a declared feature or target tensor.

    With ``require_future_predecessor=True``, the preceding command used to
    normalize aim must also have its entire support strictly after the bound.
    Its own fractions are checked, and both supports are recorded. It remains
    normalization provenance, never an observation feature. The unlabelled gap
    then ends at the beginning of the combined support. For one command per
    consecutive server tick and integer bound B, this selects target B+3 and
    predecessor B+2. No missing third command is needed to normalize that pair.
    """
    for name, value in (("history_frames", history_frames), ("target_horizon_frames", target_horizon_frames)):
        if type(value) is not int or not 1 <= value <= 1024:
            raise ValueError(name + " must be an integer between 1 and 1024")
    if type(require_future_predecessor) is not bool:
        raise ValueError("require_future_predecessor must be a boolean")
    issues = _validate_inputs(commands, frame_bounds, identity, support_contract)
    by_id = {row["command_row_id"]: row for row in commands}
    predecessors = {row["command_row_id"]: commands[index-1]["command_row_id"] if index else None
                    for index, row in enumerate(commands)}
    results = []
    for index, frame in enumerate(frame_bounds):
        first, horizon_end = index-history_frames+1, index+target_horizon_frames
        history = list(range(max(0, first), index+1))
        future = list(range(index, min(len(frame_bounds), horizon_end)))
        needed = sorted(set(history+future))
        reasons: set[str] = set()
        if first < 0:
            reasons.add("insufficient_history_frames")
        if horizon_end > len(frame_bounds):
            reasons.add("insufficient_future_intervals")
        if any(frame_bounds[i].get("bound_status") != "verified" for i in needed):
            reasons.add("observation_bound_unknown")
        if len({frame_bounds[i]["clock_segment_id"] for i in needed}) != 1:
            reasons.add("clock_segment_boundary")
        result = {**{key: identity[key] for key in IDENTITY}, "schema_version": 1,
                  "policy": FUTURE_PREDECESSOR_POLICY if require_future_predecessor else POLICY,
                  "contract_sha256": support_contract["contract_sha256"],
                  "clock_segment_id": frame["clock_segment_id"], "observation_frame_index": index,
                  "history_frame_indices": history, "target_horizon_frame_indices": future,
                  "history_frames": history_frames, "target_horizon_frames": target_horizon_frames,
                  "previous_action_features_included": False, "previous_action_command_row_ids": [],
                  "target_command_row_ids": [], "target_support": [],
                  "remaining_future_command_row_ids": [], "excluded_overlap_command_row_ids": [],
                  "normalization_predecessor_command_row_ids": [],
                  "observation_upper_execution_tick": None, "target_cutoff_execution_tick": None,
                  "unlabelled_gap_ticks": None, "target_end_delay_from_upper_bound_ticks": None,
                  "temporal_eligible": False, "training_ready": False}
        if require_future_predecessor:
            result.update(require_future_predecessor=True, normalization_predecessor_support=[],
                          excluded_predecessor_overlap_command_row_ids=[],
                          excluded_missing_predecessor_command_row_ids=[],
                          normalization_support_start_execution_tick=None,
                          target_command_start_delay_ticks=None)
        if not reasons.intersection({"insufficient_future_intervals", "observation_bound_unknown", "clock_segment_boundary"}):
            upper = frame["observation_upper_execution_tick"]
            cutoff = frame_bounds[horizon_end-1]["next_observation_upper_execution_tick"]
            result.update(observation_upper_execution_tick=upper, target_cutoff_execution_tick=cutoff)
            overlaps = [row for row in commands if row["server_tick_executed"]-1 <= upper <= row["server_tick_executed"]]
            candidates = [row for row in commands if upper < row["server_tick_executed"]-1 and row["server_tick_executed"] <= cutoff]
            result["excluded_overlap_command_row_ids"] = [row["command_row_id"] for row in overlaps]
            if require_future_predecessor:
                eligible = []
                for candidate in candidates:
                    rid = candidate["command_row_id"]
                    predecessor = by_id.get(predecessors[rid])
                    if predecessor is None:
                        result["excluded_missing_predecessor_command_row_ids"].append(rid)
                    elif predecessor["server_tick_executed"]-1 <= upper:
                        result["excluded_predecessor_overlap_command_row_ids"].append(rid)
                    else:
                        eligible.append(candidate)
                candidates = eligible
            if not candidates:
                reasons.add("no_complete_future_command_with_future_predecessor" if require_future_predecessor
                            else "no_complete_future_command")
            else:
                selected = candidates[0]
                rid, end = selected["command_row_id"], selected["server_tick_executed"]
                start = end-1
                # Otherwise a missing first eligible command could silently turn
                # this policy into a later-target selector with a different delay.
                if not require_future_predecessor and end != math.floor(upper)+2:
                    reasons.add("first_future_command_coverage_gap")
                reasons |= issues[rid]
                result.update(target_command_row_ids=[rid],
                    target_support=[{"command_row_id": rid, "execution_clock": "server_tick_executed",
                                     "support_start_execution_tick": start, "support_end_execution_tick": end,
                                     "support_interval": "[N-1,N]"}],
                    remaining_future_command_row_ids=[row["command_row_id"] for row in candidates[1:]],
                    normalization_predecessor_command_row_ids=([predecessors[rid]] if predecessors[rid] is not None else []),
                    unlabelled_gap_ticks=start-upper, target_end_delay_from_upper_bound_ticks=end-upper)
                if require_future_predecessor:
                    predecessor = by_id[predecessors[rid]]
                    pred_id, pred_end = predecessor["command_row_id"], predecessor["server_tick_executed"]
                    pred_start = pred_end-1
                    # Test the earliest complete pair's coverage; never skip a
                    # missing first predecessor or a bad selected pair silently.
                    if pred_end != math.floor(upper)+2:
                        reasons.add("first_future_predecessor_coverage_gap")
                    reasons |= {"normalization_predecessor_"+reason for reason in _fraction_reasons(predecessor)}
                    result.update(normalization_predecessor_support=[{
                        "command_row_id": pred_id, "execution_clock": "server_tick_executed",
                        "support_start_execution_tick": pred_start, "support_end_execution_tick": pred_end,
                        "support_interval": "[N-1,N]"}],
                        normalization_support_start_execution_tick=pred_start,
                        unlabelled_gap_ticks=pred_start-upper,
                        target_command_start_delay_ticks=start-upper)
        result["reason_codes"] = sorted(reasons)
        result["temporal_eligible"] = not reasons
        results.append(result)
    return results


def verify_causal_targets(records: Sequence[dict[str, Any]], commands: Sequence[dict[str, Any]],
                          frame_bounds: Sequence[dict[str, Any]], **kwargs) -> None:
    """Reject altered/shifted derived selection by recomputing from supplied inputs.

    Like selection, this does not verify raw evidence files or grant acceptance.
    """
    def equal(left, right):
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(equal(left[key], right[key]) for key in left)
        if isinstance(left, list):
            return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
        return left == right

    if not equal(list(records), select_causal_targets(commands, frame_bounds, **kwargs)):
        raise ValueError("Causal target records disagree with recomputed canonical supports and observed bounds")
