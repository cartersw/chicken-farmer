"""Bounded action-aware scheduling hints and independently checked label coverage.

Discovery never grants acceptance. A present raw protobuf button parent supplies
recorded state hints; absent parents, command gaps and unknown state stay unknown.
Ordinary clips are selected without looking at their actions.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import pyarrow.parquet as pq

from .causal_acceptance import _one
from .clock_evidence import protobuf_fields, scalar
from .competitive_buttons import BUTTON_MASKS, decode_native_button_state
from .io import parsed_manifest, sha256_file, write_json
from .jobs import phase_evidence

PROFILE = "cs2-competitive-action-discovery-v1"
COVERAGE_PROFILE = "cs2-competitive-accepted-action-coverage-v1"
ACTIONS = ("reload", "sustained_attack1", "movement_start", "movement_stop", "crouch", "jump")
MAX_CANDIDATES = 100_000
MAX_TABLE_ROWS = 50_000_000
MAX_POOL = 512
STATE_FIELDS = ("demo_id", "demo_tick", "round_id", "steam_id", "player_slot", "spectator_user_id",
    "alive", "is_warmup", "is_freeze_time", "is_paused", "active_weapon", "ammo_clip", "ammo_reserve",
    "velocity_x", "velocity_y", "on_ground", "crouching")
COMMAND_FIELDS = ("demo_id", "demo_tick", "round_id", "steam_id", "player_slot", "command_number",
    "server_tick_executed", "base_present", "buttons_present", "buttonstate1", "buttonstate2", "buttonstate3",
    "command_protobuf")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize_steam_id(steam_id: str | None) -> str | None:
    """Validate an optional decimal player identity before reading any source."""
    if steam_id is None:
        return None
    _require(isinstance(steam_id, str) and 1 <= len(steam_id) <= 20 and
             steam_id.isascii() and steam_id.isdecimal() and 0 < int(steam_id) < 2**64,
             "Steam ID must be a positive uint64 decimal string")
    return str(int(steam_id))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _rows(path, columns, *, row_limit=MAX_TABLE_ROWS):
    with pq.ParquetFile(path) as reader:
        _require(reader.metadata.num_rows <= row_limit, "Discovery table exceeds row bound")
        _require(set(columns) <= set(reader.schema_arrow.names), "Discovery source is missing required columns")
        for batch in reader.iter_batches(batch_size=8192, columns=list(columns)):
            yield from batch.to_pylist()


def _identity(row):
    return row["round_id"], str(row["steam_id"]), row["player_slot"]


def _speed(row):
    values = [row.get("velocity_x"), row.get("velocity_y")]
    return math.hypot(*values) if all(type(v) in (int, float) and math.isfinite(v) for v in values) else None


def _state_windows(states, rounds, phases, demo_id, clip_ticks, *, steam_id=None):
    """One active chunk per slot, plus a strictly bounded candidate index."""
    active, previous_ticks, result = {}, {}, []
    for row in states:
        _require(row.get("demo_id") == demo_id, "State source identity disagrees with manifest")
        slot, tick = row.get("player_slot"), row.get("demo_tick")
        _require(type(slot) is int and 0 <= slot < 128 and type(tick) is int and tick >= 0, "Invalid state slot/tick")
        _require(tick > previous_ticks.get(slot, -1), "State timeline reverses or repeats")
        previous_ticks[slot] = tick
        info = rounds.get(row.get("round_id"), {})
        phase = phases.get(row.get("round_id"), {})
        freeze, end = info.get("freeze_end_tick"), info.get("end_tick")
        valid = (phase.get("phase_verified") is True and phase.get("phase") == "competitive" and
            type(freeze) is int and type(end) is int and max(199, freeze) <= tick < end and
            row.get("alive") is True and row.get("is_warmup") is False and row.get("is_freeze_time") is False and
            row.get("is_paused") is False and type(row.get("steam_id")) is int and 0 < row["steam_id"] < 2**64 and
            (steam_id is None or str(row["steam_id"]) == steam_id) and
            type(row.get("spectator_user_id")) is int and 0 <= row["spectator_user_id"] <= 255)
        current = active.get(slot)
        if current is not None and (not valid or tick != current["last_tick"]+1 or _identity(row) != current["identity"]):
            active.pop(slot)
            current = None
        if not valid:
            continue
        if current is None:
            current = {"identity": _identity(row), "start": tick, "last_tick": tick-1,
                       "previous": None, "corroboration": Counter(), "state_known": Counter()}
            active[slot] = current
        evidence, known, previous = current["corroboration"], current["state_known"], current["previous"]
        for key in ("on_ground", "crouching"):
            known[key] += type(row.get(key)) is bool
        evidence["crouching_state_ticks"] += row.get("crouching") is True
        speed = _speed(row)
        known["horizontal_speed"] += speed is not None
        evidence["moving_state_ticks"] += speed is not None and speed > 5
        if previous is not None:
            prior_speed = _speed(previous)
            if speed is not None and prior_speed is not None:
                evidence["state_movement_starts"] += prior_speed <= 5 < speed
                evidence["state_movement_stops"] += speed <= 5 < prior_speed
            evidence["ground_to_air_transitions"] += previous.get("on_ground") is True and row.get("on_ground") is False
            if row.get("active_weapon") and row.get("active_weapon") == previous.get("active_weapon"):
                before, after = previous.get("ammo_clip"), row.get("ammo_clip")
                if type(before) is int and type(after) is int and min(before, after) >= 0:
                    known["same_weapon_ammo_pairs"] += 1
                    evidence["ammo_clip_decreases"] += after < before
                    reserve_before, reserve_after = previous.get("ammo_reserve"), row.get("ammo_reserve")
                    evidence["reload_like_ammo_transfers"] += (after > before and type(reserve_before) is int and
                        type(reserve_after) is int and 0 <= reserve_after < reserve_before)
        current.update(last_tick=tick, previous=row)
        if tick-current["start"]+1 == clip_ticks:
            begin = current["start"]
            result.append({"candidate_id": _digest([PROFILE, demo_id, *_identity(row), begin, tick+1])[:24],
                "demo_id": demo_id, "map": info.get("map"), "round_id": row["round_id"],
                "steam_id": str(row["steam_id"]), "player_slot": slot, "start_demo_tick": begin,
                "end_demo_tick": tick+1, "state_corroboration": dict(evidence), "known_state_counts": dict(known),
                "state_ticks": clip_ticks, "phase_verified_for_scheduling": True, "training_ready": False})
            _require(len(result) <= MAX_CANDIDATES, "Discovery exceeds candidate memory bound")
            active.pop(slot)
    return sorted(result, key=lambda c: (c["player_slot"], c["start_demo_tick"]))


def _button_observation(row):
    """Check the button projection against raw PB; no missing-parent defaults."""
    try:
        raw = row.get("command_protobuf")
        _require(isinstance(raw, bytes) and len(raw) <= 4*1024*1024, "invalid_protobuf")
        base_raw = _one(protobuf_fields(raw), 1, 2)
        _require(row.get("base_present") is (base_raw is not None), "base_presence_mismatch")
        _require(base_raw is not None, "missing_base_parent")
        base = protobuf_fields(base_raw)
        button_raw = _one(base, 3, 2)
        _require(row.get("buttons_present") is (button_raw is not None), "button_presence_mismatch")
        _require(button_raw is not None, "missing_button_parent")
        flags = scalar(base, 21, signed=True)
        _require(flags in (None, 0), "unsupported_command_flags")
        buttons = protobuf_fields(button_raw)
        raw_planes = [_one(buttons, n, 0) for n in (1, 2, 3)]
        for number, value in enumerate(raw_planes, 1):
            actual = row.get("buttonstate"+str(number))
            _require(actual is None if value is None else type(actual) is int and actual == value,
                     "button_projection_mismatch")
            _require(value is None or type(value) is int and 0 <= value < 2**64, "invalid_button_plane")
        return {name: decode_native_button_state([v or 0 for v in raw_planes], mask)
                for name, mask in BUTTON_MASKS.items()}, None
    except (ValueError, TypeError, OverflowError) as error:
        return None, str(error)[:120] or "invalid_button_protobuf"


def _annotate_commands(candidates, commands, demo_id):
    by_slot = defaultdict(list)
    for candidate in candidates:
        by_slot[candidate["player_slot"]].append(candidate)
        candidate["_scan"] = {"commands": 0, "known": 0, "unknown": Counter(), "hints": Counter(),
            "last_tick": None, "first_tick": None, "max_gap": 0, "previous": None, "attack_run": 0,
            "maximum_attack_run": 0, "boundary_gaps": 0}
    starts = {slot: [c["start_demo_tick"] for c in group] for slot, group in by_slot.items()}
    previous_ticks = {}
    for row in commands:
        _require(row.get("demo_id") == demo_id, "Command source identity disagrees with manifest")
        slot, tick = row.get("player_slot"), row.get("demo_tick")
        _require(type(slot) is int and 0 <= slot < 128 and type(tick) is int and tick >= 0, "Invalid command slot/tick")
        _require(tick >= previous_ticks.get(slot, -1), "Command timeline reverses")
        previous_ticks[slot] = tick
        index = bisect.bisect_right(starts.get(slot, []), tick)-1
        if index < 0:
            continue
        candidate = by_slot[slot][index]
        if tick >= candidate["end_demo_tick"] or _identity(row) != _identity(candidate):
            continue
        state = candidate["_scan"]
        state["commands"] += 1
        if state["first_tick"] is None:
            state["first_tick"] = tick
        if state["last_tick"] is not None:
            state["max_gap"] = max(state["max_gap"], tick-state["last_tick"])
        state["last_tick"] = tick
        observation, reason = _button_observation(row)
        previous = state["previous"]
        if observation is None:
            state["unknown"][reason] += 1
            state.update(previous=None, attack_run=0)
            continue
        state["known"] += 1
        contiguous = (previous is not None and type(row.get("command_number")) is int and
            type(row.get("server_tick_executed")) is int and row["command_number"] == previous[0]+1 and
            row["server_tick_executed"] == previous[1]+1 and 0 <= tick-previous[2] <= 1)
        if not contiguous:
            state["boundary_gaps"] += 1
        hints = state["hints"]
        # Seven history frames consume fourteen demo ticks. Keep a conservative
        # extra margin for future-command targets; this remains a scheduling hint.
        target_interior = tick >= candidate["start_demo_tick"]+20
        for name in ("reload", "crouch", "jump"):
            value = observation[name]
            hints[name] += target_interior and (value["held_at_command_end"] or value["recorded_activity_present"])
        movement = ("forward", "back", "left", "right")
        consistent = contiguous and all(previous[3][name]["held_at_command_end"] ==
            observation[name]["held_at_command_start"] for name in movement)
        if consistent and target_interior:
            before = any(previous[3][name]["held_at_command_end"] for name in movement)
            after = any(observation[name]["held_at_command_end"] for name in movement)
            hints["movement_start"] += not before and after
            hints["movement_stop"] += before and not after
        attack = observation["attack1"]
        held = attack["state_code"] == 1
        state["attack_run"] = state["attack_run"]+1 if held and contiguous and previous[3]["attack1"]["held_at_command_end"] else int(held)
        if target_interior:
            state["maximum_attack_run"] = max(state["maximum_attack_run"], state["attack_run"])
        number, execution = row.get("command_number"), row.get("server_tick_executed")
        state["previous"] = (number, execution, tick, observation) if type(number) is int and type(execution) is int else None
    for candidate in candidates:
        state = candidate.pop("_scan")
        first, last = state["first_tick"], state["last_tick"]
        max_gap = max(state["max_gap"], first-candidate["start_demo_tick"], candidate["end_demo_tick"]-last) if first is not None else None
        hints = {name: int(state["hints"].get(name, 0)) for name in ACTIONS}
        hints["sustained_attack1"] = int(state["maximum_attack_run"] >= 8)
        candidate.update(action_hints=hints, known_button_commands=state["known"], command_count=state["commands"],
            unknown_button_commands=state["commands"]-state["known"], unknown_reason_counts=dict(state["unknown"]),
            command_boundary_gaps=state["boundary_gaps"], maximum_consecutive_attack_hold_commands=state["maximum_attack_run"],
            maximum_command_gap_demo_ticks=max_gap, command_coverage_within_4_ticks=max_gap is not None and max_gap <= 4)


def choose_candidates(candidates, *, max_clips=8, ordinary_fraction=0.5):
    """Reserve ceil(N*share) action-blind clips, then balance positive hints.

    Selection order is deterministic and source/round/player diversity precedes
    hash tie-breaking. Ordinary means sampled without action filtering, not idle.
    """
    _require(type(max_clips) is int and 1 <= max_clips <= 128, "Selection requires 1..128 clips")
    _require(type(ordinary_fraction) in (int, float) and math.isfinite(ordinary_fraction) and
             0.25 <= ordinary_fraction <= 1, "Ordinary share must be within 0.25..1")
    _require(isinstance(candidates, list) and len(candidates) <= MAX_CANDIDATES, "Invalid bounded candidate pool")
    available = {c["candidate_id"]: deepcopy(c) for c in candidates if c.get("command_coverage_within_4_ticks") is True}
    _require(len({c["candidate_id"] for c in candidates}) == len(candidates), "Duplicate candidate identity")
    target_count = min(max_clips, len(available))
    ordinary_count = math.ceil(target_count*ordinary_fraction)
    marked = any("ordinary_pool_eligible" in c for c in available.values())
    if marked:
        _require(all(type(c.get("ordinary_pool_eligible")) is bool for c in available.values()),
                 "A bounded source pool must preserve every ordinary eligibility marker")
        _require(sum(c["ordinary_pool_eligible"] for c in available.values()) >= ordinary_count,
                 "Bounded pool lacks enough independently selected ordinary candidates")
    selected, sources, rounds, players, covered = [], Counter(), Counter(), Counter(), Counter()

    def diversity(candidate):
        demo = candidate["demo_id"]
        return (sources[demo], rounds[demo, candidate["round_id"]], players[demo, candidate["steam_id"]],
                _digest([PROFILE, "ordinary-independent-of-actions", candidate["candidate_id"]]))

    def take(candidate, purpose):
        available.pop(candidate["candidate_id"])
        candidate["selection_purpose"] = purpose
        selected.append(candidate)
        demo = candidate["demo_id"]
        sources[demo] += 1; rounds[demo, candidate["round_id"]] += 1; players[demo, candidate["steam_id"]] += 1
        for action in ACTIONS:
            covered[action] += candidate["action_hints"].get(action, 0) > 0

    for _ in range(ordinary_count):
        ordinary = [c for c in available.values() if not marked or c["ordinary_pool_eligible"]]
        take(min(ordinary, key=diversity), "ordinary")
    while len(selected) < target_count:
        remaining_actions = [a for a in ACTIONS if any(c["action_hints"].get(a, 0) for c in available.values())]
        if not remaining_actions:
            take(min(available.values(), key=diversity), "unstratified_fallback")
            continue
        action = min(remaining_actions, key=lambda a: (covered[a], ACTIONS.index(a)))
        choices = [c for c in available.values() if c["action_hints"].get(action, 0)]
        take(min(choices, key=diversity), action)
    return selected


def source_selections(candidates):
    """The only fields carried into a competitive_batch source selection."""
    return [{"start_demo_tick": c["start_demo_tick"], "end_demo_tick": c["end_demo_tick"],
             "round_id": c["round_id"], "steam_id": int(c["steam_id"])} for c in candidates]


def discover_candidates(parsed, source_demo, phase_manifest, out, *, clip_ticks=320, max_clips=8,
                        ordinary_fraction=0.5, steam_id: str | None = None):
    steam_id = normalize_steam_id(steam_id)
    _require(type(clip_ticks) is int and 32 <= clip_ticks <= 1280 and clip_ticks % 2 == 0, "Clips require an even 32..1280 ticks")
    _require(type(max_clips) is int and 1 <= max_clips <= 16, "One source permits 1..16 selections")
    # Validate policy before the expensive source reads.
    choose_candidates([], max_clips=max_clips, ordinary_fraction=ordinary_fraction)
    parsed, source_demo, phase_manifest, out = [Path(p).resolve() for p in (parsed, source_demo, phase_manifest, out)]
    _require(not out.exists(), "Discovery requires a fresh output JSON")
    _require(source_demo.suffix.lower() == ".dem", "Discovery requires an original .dem")
    tables = ("rounds.parquet", "player_state.parquet", "usercmd.parquet")
    manifest = parsed_manifest(parsed, tables)
    _require(manifest.get("tick_rate") == 64 and str(manifest.get("parser_schema_version")) == "2", "Discovery requires canonical v2 at 64 Hz")
    files = {str(parsed/name): manifest["files"][name] for name in tables}
    implementation = Path(__file__).resolve()
    dependencies = [implementation, *(implementation.with_name(name+".py") for name in
        ("competitive_buttons", "clock_evidence", "causal_acceptance", "jobs", "io"))]
    for path in (source_demo, phase_manifest, parsed/"manifest.json", *dependencies):
        files[str(path)] = sha256_file(path)
    _require(files[str(source_demo)] == manifest["demo_id"] == manifest.get("sha256", manifest["demo_id"]), "Source demo SHA256 disagrees with manifest")
    round_rows = list(_rows(parsed/"rounds.parquet", ("demo_id", "round_id", "start_tick", "freeze_end_tick", "end_tick", "map"), row_limit=10000))
    _require(len(round_rows) <= 10000 and all(r["demo_id"] == manifest["demo_id"] for r in round_rows), "Invalid bounded rounds table")
    rounds = {r["round_id"]: r for r in round_rows}
    _require(len(rounds) == len(round_rows), "Duplicate canonical rounds")
    phases = phase_evidence(phase_manifest, manifest, rounds)
    candidates = _state_windows(_rows(parsed/"player_state.parquet", STATE_FIELDS), rounds, phases,
                                manifest["demo_id"], clip_ticks, steam_id=steam_id)
    _annotate_commands(candidates, _rows(parsed/"usercmd.parquet", COMMAND_FIELDS), manifest["demo_id"])
    selected = choose_candidates(candidates, max_clips=max_clips, ordinary_fraction=ordinary_fraction)
    # A bounded pool permits cross-map selection without writing every full-match window.
    pool = choose_candidates(candidates, max_clips=min(128, MAX_POOL), ordinary_fraction=ordinary_fraction)
    pool_by_id = {c["candidate_id"]: c for c in [*pool, *selected]}
    ordinary_ids = {c["candidate_id"] for c in [*pool, *selected] if c["selection_purpose"] == "ordinary"}
    for candidate in pool_by_id.values():
        candidate["ordinary_pool_eligible"] = candidate["candidate_id"] in ordinary_ids
    report = {"schema_version": 1, "profile": PROFILE, "status": "complete", "demo_id": manifest["demo_id"],
        "inputs": {"parsed": str(parsed), "demo": str(source_demo), "phase_manifest": str(phase_manifest)},
        "source_files": files, "configuration": {"clip_ticks": clip_ticks, "max_clips": max_clips,
            "steam_id": steam_id, "selection_scope": "specific_player" if steam_id is not None else "all_players",
            "ordinary_fraction": ordinary_fraction, "sustained_attack_minimum_commands": 8, "maximum_command_gap_ticks": 4,
            "action_hint_excluded_initial_demo_ticks": 20},
        "candidate_count": len(candidates), "eligible_command_coverage_count": sum(c["command_coverage_within_4_ticks"] for c in candidates),
        "candidate_action_counts": {a: sum(c["action_hints"][a] > 0 for c in candidates) for a in ACTIONS},
        "eligible_candidate_action_counts": {a: sum(c["action_hints"][a] > 0 and c["command_coverage_within_4_ticks"] for c in candidates) for a in ACTIONS},
        "known_button_commands": sum(c["known_button_commands"] for c in candidates),
        "unknown_button_commands": sum(c["unknown_button_commands"] for c in candidates),
        "source_parser_warnings": manifest.get("warnings", {}), "candidate_pool": list(pool_by_id.values()),
        "selected": selected, "selections": source_selections(selected), "training_ready": False,
        "original_source_reconstruction_verified": False,
        "limits": ["Scheduling hints only: canonical tables are hash-bound, not independently reconstructed here.",
            "Ordinary selection ignores action labels; ordinary is not a claim that a clip contains no actions.",
            "Button parent absence and malformed projections remain unknown, never negative examples.",
            "Sustained attack means at least eight adjacent recorded held commands, not confirmed bullets fired.",
            "State changes are separate corroboration; ammo transfers and airborne transitions do not prove a physical key.",
            "Independent source, HUD, frame timing and per-field acceptance checks still decide usability.",
            "A bounded cross-source pool is a scheduling convenience, not an unbiased corpus sample."]}
    _require(all(sha256_file(Path(path)) == digest for path, digest in files.items()), "Discovery source changed during scan")
    report["report_sha256"] = _digest(report)
    write_json(out, report)
    return report


def accepted_action_coverage(acceptance_paths, out):
    """Revalidate every publication, then count masks; never trust saved counts."""
    from .competitive_control import FILES, MANIFEST, load_competitive_acceptance
    from .training_dataset import AXES, BUTTONS, BUTTON_FIELDS, project_targets
    paths = [Path(p).resolve() for p in acceptance_paths]
    _require(1 <= len(paths) <= 128 and len(set(paths)) == len(paths), "Coverage needs 1..128 distinct acceptances")
    out = Path(out).resolve()
    _require(not out.exists(), "Coverage requires a fresh output JSON")
    fields = {button: {field: Counter(valid=0, positive=0, negative=0, unknown=0) for field in BUTTON_FIELDS} for button in BUTTONS}
    aim = {axis: Counter(valid=0, unknown=0, nonzero=0) for axis in AXES}
    seen, observations, clips, files, total = set(), set(), [], {}, 0
    for path in paths:
        manifest_path = path/MANIFEST if path.is_dir() else path
        before = sha256_file(manifest_path)
        report = load_competitive_acceptance(manifest_path)
        _require(sha256_file(manifest_path) == before, "Acceptance changed during independent validation")
        samples_path = manifest_path.parent/FILES[0]
        _require(sha256_file(samples_path) == report["files"][FILES[0]], "Accepted partition changed")
        files[str(manifest_path)] = before; files[str(samples_path)] = report["files"][FILES[0]]
        local = {"acceptance": str(manifest_path), "demo_id": report["demo_id"], "clip_id": report["clip_id"],
            "accepted_samples": 0, "positive_samples_by_button": Counter(), "valid_samples_by_button": Counter()}
        with samples_path.open(encoding="utf-8") as stream:
            while line := stream.readline(2*1024*1024+1):
                _require(len(line) <= 2*1024*1024, "Accepted sample exceeds coverage record bound")
                sample = json.loads(line)
                _require(sample["sample_id"] not in seen, "Duplicate accepted sample would double-count coverage")
                seen.add(sample["sample_id"])
                observation = (sample["demo_id"], sample["clip_id"], sample["observation_frame_index"])
                _require(observation not in observations, "Duplicate accepted observation would double-count coverage")
                observations.add(observation)
                total += 1
                _require(total <= 100000, "Coverage exceeds accepted sample bound")
                targets = project_targets(sample["label"])
                local["accepted_samples"] += 1
                for i, axis in enumerate(AXES):
                    valid, value = targets["aim_mask"][i], targets["aim_target"][i]
                    aim[axis]["valid" if valid else "unknown"] += 1
                    aim[axis]["nonzero"] += valid and value != 0
                for i, button in enumerate(BUTTONS):
                    local["valid_samples_by_button"][button] += any(targets["button_mask"][i])
                    local["positive_samples_by_button"][button] += any(v and bool(x) for x, v in zip(targets["button_target"][i], targets["button_mask"][i]))
                    for j, field in enumerate(BUTTON_FIELDS):
                        valid, value = targets["button_mask"][i][j], targets["button_target"][i][j]
                        count = fields[button][field]
                        count["valid" if valid else "unknown"] += 1
                        if valid:
                            count["positive" if value else "negative"] += 1
        _require(local["accepted_samples"] == report["accepted_count"], "Accepted sample count disagrees with verified publication")
        clips.append(local)
    _require(all(sha256_file(Path(path)) == digest for path, digest in files.items()), "Accepted coverage source changed")
    result = {"schema_version": 1, "profile": COVERAGE_PROFILE, "status": "complete", "accepted_sample_count": total,
        "clips": clips, "button_fields": fields, "aim_fields": aim, "source_files": files,
        "exact_input_timing_verified": False, "training_started": False,
        "limits": ["Samples overlap in image history and may share commands; counts are not independent observations.",
            "Positive cells describe recorded controls, not physical presses, bullet counts or successful reloads.",
            "Unknown masks are counted separately from valid negative labels."]}
    result["report_sha256"] = _digest(result)
    write_json(out, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover")
    for name in ("parsed", "demo", "phase-manifest", "out"):
        discover.add_argument("--"+name, type=Path, required=True)
    discover.add_argument("--clip-ticks", type=int, default=320)
    discover.add_argument("--max-clips", type=int, default=8)
    discover.add_argument("--ordinary-fraction", type=float, default=0.5)
    discover.add_argument("--steam-id", type=normalize_steam_id, help="Select only this player's positive uint64 Steam ID")
    coverage = commands.add_parser("accepted")
    coverage.add_argument("--acceptance", action="append", type=Path, required=True)
    coverage.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "discover":
        report = discover_candidates(args.parsed, args.demo, args.phase_manifest, args.out,
            clip_ticks=args.clip_ticks, max_clips=args.max_clips, ordinary_fraction=args.ordinary_fraction,
            steam_id=args.steam_id)
        print(json.dumps({"out": str(args.out), "candidates": report["candidate_count"], "actions": report["eligible_candidate_action_counts"], "selections": report["selections"]}))
    else:
        report = accepted_action_coverage(args.acceptance, args.out)
        print(json.dumps({"out": str(args.out), "accepted_samples": report["accepted_sample_count"]}))


if __name__ == "__main__":
    main()
