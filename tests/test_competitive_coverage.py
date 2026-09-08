from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import competitive_coverage as coverage


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result)+bytes([value])


def message(number, payload):
    return varint(number*8+2)+varint(len(payload))+payload


def command(tick, held=0, changed=0, repeated=0, *, present=True, number=None):
    planes = (held, changed, repeated)
    button = b"".join(varint(n*8)+varint(v) for n, v in enumerate(planes, 1))
    return {"demo_id": "demo", "round_id": 3, "steam_id": 101, "player_slot": 0,
        "demo_tick": tick, "command_number": tick if number is None else number, "server_tick_executed": tick,
        "base_present": True, "buttons_present": present, "command_protobuf": message(1, message(3, button) if present else b""),
        **{"buttonstate"+str(n): v if present else None for n, v in enumerate(planes, 1)}}


def candidate(index=0, *, demo="demo", round_id=3, player="101", hints=None):
    return {"candidate_id": "candidate-"+str(index), "demo_id": demo, "round_id": round_id,
        "steam_id": player, "player_slot": 0, "start_demo_tick": 200+index*32,
        "end_demo_tick": 232+index*32, "command_coverage_within_4_ticks": True,
        "action_hints": {a: (hints or {}).get(a, 0) for a in coverage.ACTIONS}}


def state(tick, **overrides):
    return {"demo_id": "demo", "round_id": 3, "steam_id": 101, "player_slot": 0,
        "spectator_user_id": 1, "demo_tick": tick, "alive": True, "is_warmup": False,
        "is_freeze_time": False, "is_paused": False, "active_weapon": "AK-47", "ammo_clip": 30,
        "ammo_reserve": 90, "velocity_x": 0, "velocity_y": 0, "on_ground": True, "crouching": False, **overrides}


def windows(rows):
    return coverage._state_windows(rows, {3: {"freeze_end_tick": 200, "end_tick": 1000, "map": "de_test"}},
        {3: {"phase_verified": True, "phase": "competitive"}}, "demo", 32)


def test_missing_button_parent_remains_unknown_while_present_empty_parent_is_zero():
    observation, reason = coverage._button_observation(command(200, present=False))
    assert observation is None and reason == "missing_button_parent"
    row = command(200)
    row.update(command_protobuf=message(1, message(3, b"")), buttonstate1=None, buttonstate2=None, buttonstate3=None)
    observation, reason = coverage._button_observation(row)
    assert reason is None and all(o["state_code"] == 0 for o in observation.values())


@pytest.mark.parametrize("mutation", ["plane", "presence", "flags", "malformed"])
def test_raw_protobuf_disagreement_is_unknown(mutation):
    row = command(200, held=8192)
    if mutation == "plane": row["buttonstate1"] = 0
    if mutation == "presence": row["buttons_present"] = False
    if mutation == "flags": row["command_protobuf"] = message(1, message(3, b"")+varint(21*8)+varint(1))
    if mutation == "malformed": row["command_protobuf"] = b"\x0a\xff"
    observation, reason = coverage._button_observation(row)
    assert observation is None and reason


def test_state_windows_require_known_unpaused_competitive_alive_contiguity():
    assert len(windows([state(t) for t in range(200, 232)])) == 1
    for field, value in (("is_paused", None), ("alive", False), ("is_freeze_time", True), ("steam_id", None)):
        assert not windows([state(t, **({field: value} if t == 216 else {})) for t in range(200, 232)])
    assert not windows([state(t) for t in range(200, 233) if t == 200 or t >= 203])
    assert not coverage._state_windows([state(t) for t in range(200, 232)],
        {3: {"freeze_end_tick": 200, "end_tick": 1000}}, {3: {"phase_verified": False}}, "demo", 32)


def test_state_corrobation_never_asserts_action_label():
    rows = [state(t, ammo_clip=10 if t < 216 else 30, ammo_reserve=90 if t < 216 else 70,
                  on_ground=t < 208, crouching=t > 216) for t in range(200, 232)]
    result = windows(rows)[0]
    assert result["state_corroboration"]["reload_like_ammo_transfers"] == 1
    assert result["state_corroboration"]["ground_to_air_transitions"] == 1
    assert result["training_ready"] is False and "action_hints" not in result


def test_button_hints_require_known_rows_and_contiguous_sustained_attack():
    rows = [command(t, held=1 if 205 <= t < 222 else 0) for t in range(200, 232)]
    rows[25] = command(225, held=8192, changed=8192)
    choices = [candidate()]
    coverage._annotate_commands(choices, rows, "demo")
    result = choices[0]
    assert result["action_hints"]["reload"] == 1
    assert result["action_hints"]["sustained_attack1"] == 1
    assert result["maximum_consecutive_attack_hold_commands"] == 17
    assert result["known_button_commands"] == 32
    assert result["command_coverage_within_4_ticks"] is True
    rows = [command(t, held=1, number=t*2) for t in range(200, 232)]
    choices = [candidate()]
    coverage._annotate_commands(choices, rows, "demo")
    assert choices[0]["action_hints"]["sustained_attack1"] == 0
    assert choices[0]["maximum_consecutive_attack_hold_commands"] == 1


def test_unknown_rows_do_not_become_negative_or_bridge_movement_edges():
    rows = [command(t) for t in range(200, 232)]
    rows[21] = command(221, held=8, changed=8)
    rows[22] = command(222, held=0, changed=8)
    rows[24] = command(224, present=False)
    rows[25] = command(225, held=8, changed=8)
    choices = [candidate()]
    coverage._annotate_commands(choices, rows, "demo")
    result = choices[0]
    assert result["action_hints"]["movement_start"] == 1
    assert result["action_hints"]["movement_stop"] == 1
    assert result["known_button_commands"] == 31 and result["unknown_button_commands"] == 1
    assert result["unknown_reason_counts"] == {"missing_button_parent": 1}


def test_command_gaps_exclude_candidate_from_selection():
    choices = [candidate()]
    coverage._annotate_commands(choices, [command(200), command(231)], "demo")
    assert coverage.choose_candidates(choices) == []


def test_ordinary_share_is_action_blind_diverse_and_input_order_independent():
    choices = [candidate(i, demo="demo"+str(i % 2), round_id=3+i % 3, player=str(100+i % 4),
                         hints={"reload": 1} if i >= 4 else {}) for i in range(12)]
    selected = coverage.choose_candidates(choices, max_clips=8)
    assert selected == coverage.choose_candidates(list(reversed(choices)), max_clips=8)
    assert [c["selection_purpose"] for c in selected[:4]] == ["ordinary"]*4
    assert len({c["demo_id"] for c in selected[:2]}) == 2
    changed = deepcopy(choices)
    for c in changed: c["action_hints"] = {a: 1 for a in coverage.ACTIONS}
    assert [c["candidate_id"] for c in selected[:4]] == [c["candidate_id"] for c in coverage.choose_candidates(changed, max_clips=8)[:4]]
    assert choices[0].get("selection_purpose") is None


def test_rounding_preserves_minimum_ordinary_share_and_rare_actions_fill_targets():
    choices = [candidate(i, hints={"reload": 1} if i == 0 else {"sustained_attack1": 1} if i == 1 else {}) for i in range(8)]
    selected = coverage.choose_candidates(choices, max_clips=3)
    assert len(selected) == 3 and sum(c["selection_purpose"] == "ordinary" for c in selected) == 2
    assert any(c["action_hints"]["reload"] or c["action_hints"]["sustained_attack1"] for c in selected)
    assert set(coverage.source_selections(selected)[0]) == {"start_demo_tick", "end_demo_tick", "steam_id", "round_id"}
    assert type(coverage.source_selections(selected)[0]["steam_id"]) is int


def test_combined_pool_cannot_relabel_action_selected_members_as_ordinary():
    choices = [candidate(i, hints={"reload": 1}) for i in range(8)]
    for i, c in enumerate(choices):
        c["ordinary_pool_eligible"] = i < 4
    selected = coverage.choose_candidates(choices, max_clips=4)
    assert all(c["ordinary_pool_eligible"] for c in selected[:2])
    choices[0]["ordinary_pool_eligible"] = False
    with pytest.raises(ValueError, match="enough independently selected ordinary"):
        coverage.choose_candidates(choices, max_clips=8)
    del choices[0]["ordinary_pool_eligible"]
    with pytest.raises(ValueError, match="preserve every ordinary"):
        coverage.choose_candidates(choices, max_clips=4)


def test_initial_history_actions_do_not_supply_target_coverage_hints():
    choices = [candidate()]
    rows = [command(t, held=8192 if t == 201 else 0) for t in range(200, 232)]
    coverage._annotate_commands(choices, rows, "demo")
    assert choices[0]["action_hints"]["reload"] == 0


@pytest.mark.parametrize("options", [{"ordinary_fraction": 0}, {"ordinary_fraction": float("nan")}, {"max_clips": 129}, {"max_clips": True}])
def test_invalid_selection_policy_rejected(options):
    with pytest.raises(ValueError): coverage.choose_candidates([], **options)


def test_source_hash_mismatch_rejected_before_discovery(tmp_path, monkeypatch):
    parsed = tmp_path/"parsed"; parsed.mkdir()
    demo = tmp_path/"source.dem"; demo.write_bytes(b"demo")
    phase = tmp_path/"phase.json"; phase.write_text("{}")
    (parsed/"manifest.json").write_text("{}")
    monkeypatch.setattr(coverage, "parsed_manifest", lambda *a: {"demo_id": "a"*64,
        "tick_rate": 64, "parser_schema_version": 2, "files": {name: "b"*64 for name in
            ("rounds.parquet", "player_state.parquet", "usercmd.parquet")}})
    with pytest.raises(ValueError, match="Source demo SHA256"):
        coverage.discover_candidates(parsed, demo, phase, tmp_path/"out.json")
    assert not (tmp_path/"out.json").exists()


def test_discovery_bound_checked_before_read(tmp_path):
    for ticks in (31, 321, 1282, True):
        with pytest.raises(ValueError, match="even 32..1280"):
            coverage.discover_candidates(tmp_path, tmp_path/"x.dem", tmp_path/"p", tmp_path/"out", clip_ticks=ticks)


@pytest.mark.parametrize("value", ["", "0", "-1", "18446744073709551616", "12.5", " 101", "１０１", 101, True])
def test_invalid_player_filter_is_rejected_before_discovery_reads(tmp_path, value):
    with pytest.raises(ValueError, match="positive uint64"):
        coverage.discover_candidates(tmp_path, tmp_path/"x.dem", tmp_path/"p", tmp_path/"out", steam_id=value)
    assert not (tmp_path/"out").exists()


def test_steam_identity_normalization_preserves_uint64_precision():
    assert coverage.normalize_steam_id(None) is None
    assert coverage.normalize_steam_id("00101") == "101"
    assert coverage.normalize_steam_id("18446744073709551615") == "18446744073709551615"


def test_candidate_memory_bound_is_enforced(monkeypatch):
    monkeypatch.setattr(coverage, "MAX_CANDIDATES", 1)
    with pytest.raises(ValueError, match="memory bound"):
        windows([state(t) for t in range(200, 264)])


@pytest.fixture
def canonical_source(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    parsed = tmp_path/"parsed"; parsed.mkdir()
    demo = tmp_path/"source.dem"; demo.write_bytes(b"fixture original bytes")
    demo_id = coverage.sha256_file(demo)
    rows = {"rounds.parquet": [{"demo_id": demo_id, "round_id": 3, "start_tick": 100,
        "freeze_end_tick": 200, "end_tick": 264, "map": "de_fixture"}],
        "player_state.parquet": [{**state(t), "demo_id": demo_id} for t in range(200, 264)],
        "usercmd.parquet": [{**command(t, held=8192 if t == 223 else 0), "demo_id": demo_id} for t in range(200, 264)]}
    hashes = {}
    for name, values in rows.items():
        pq.write_table(pa.Table.from_pylist(values), parsed/name)
        hashes[name] = coverage.sha256_file(parsed/name)
    (parsed/"manifest.json").write_text(json.dumps({"demo_id": demo_id, "sha256": demo_id,
        "tick_rate": 64, "parse_status": "complete", "partial": False, "parser_schema_version": 2, "files": hashes}))
    events = []
    for kind, tick, score in (("round_start", 100, 0), ("freeze_end", 200, 0),
        ("round_end", 264, 1), ("round_officially_ended", 270, 1), ("demo_end", 300, 1)):
        events.append({"kind": kind, "demo_tick": tick, "round_id": 3, "game_phase": 2,
            "is_match_started": True, "is_warmup": False, "total_rounds_played": score,
            "score_ct": score, "score_t": 0, "detail": {"reason": 9} if kind == "round_end" else {}})
    phase = tmp_path/"phase.json"
    phase.write_text(json.dumps({"schema_version": 1, "parse_status": "complete", "partial": False,
        "timing_clock": "demo_tick", "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0",
        "demo_id": demo_id, "source_demo_sha256": demo_id, "events": events}))
    return parsed, demo, phase


def test_public_discovery_streams_source_and_publishes_only_bound_hints(canonical_source, tmp_path):
    out = tmp_path/"report.json"
    result = coverage.discover_candidates(*canonical_source, out, clip_ticks=32, max_clips=2)
    assert result["candidate_count"] == 2 and result["eligible_candidate_action_counts"]["reload"] == 1
    assert result["training_ready"] is False and result["original_source_reconstruction_verified"] is False
    assert result["configuration"]["steam_id"] is None and result["configuration"]["selection_scope"] == "all_players"
    assert result["selected"][0]["selection_purpose"] == "ordinary"
    assert all(type(c["ordinary_pool_eligible"]) is bool for c in result["candidate_pool"])
    assert all(coverage.sha256_file(Path(p)) == h for p, h in result["source_files"].items())
    assert json.loads(out.read_text())["report_sha256"] == result["report_sha256"]
    with pytest.raises(ValueError, match="fresh output"):
        coverage.discover_candidates(*canonical_source, out, clip_ticks=32)


def test_specific_player_filter_precedes_candidate_bounds_and_ordinary_pool(canonical_source, tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    parsed = canonical_source[0]
    manifest_path = parsed/"manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in ("player_state.parquet", "usercmd.parquet"):
        values = pq.read_table(parsed/name).to_pylist()
        others = [{**row, "steam_id": 102, "player_slot": 1} for row in values]
        pq.write_table(pa.Table.from_pylist([*values, *others]), parsed/name)
        manifest["files"][name] = coverage.sha256_file(parsed/name)
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(coverage, "MAX_CANDIDATES", 2)
    monkeypatch.setattr(coverage, "MAX_POOL", 1)
    choose = coverage.choose_candidates
    pools = []
    def inspect_pool(candidates, **options):
        pools.append(deepcopy(candidates))
        return choose(candidates, **options)
    monkeypatch.setattr(coverage, "choose_candidates", inspect_pool)
    result = coverage.discover_candidates(*canonical_source, tmp_path/"player.json",
        clip_ticks=32, max_clips=1, steam_id="102")
    assert result["candidate_count"] == 2
    assert result["configuration"]["steam_id"] == "102"
    assert result["configuration"]["selection_scope"] == "specific_player"
    assert all(c["steam_id"] == "102" for pool in pools for c in pool)
    assert result["selected"][0]["selection_purpose"] == "ordinary"
    assert result["candidate_pool"][0]["ordinary_pool_eligible"] is True
    assert result["candidate_pool"][0]["steam_id"] == "102"
    with pytest.raises(ValueError, match="candidate memory bound"):
        coverage.discover_candidates(*canonical_source, tmp_path/"all.json", clip_ticks=32, max_clips=1)


def test_absent_player_discovery_records_empty_scope_without_fallback(canonical_source, tmp_path):
    result = coverage.discover_candidates(*canonical_source, tmp_path/"absent.json", clip_ticks=32, steam_id="999")
    assert result["candidate_count"] == result["eligible_command_coverage_count"] == 0
    assert result["candidate_pool"] == result["selected"] == result["selections"] == []
    assert result["configuration"]["steam_id"] == "999" and result["training_ready"] is False


def test_public_discovery_detects_source_mutation_during_scan(canonical_source, tmp_path, monkeypatch):
    annotate = coverage._annotate_commands
    def change(*args):
        annotate(*args)
        canonical_source[2].write_text("modified after phase verification")
    monkeypatch.setattr(coverage, "_annotate_commands", change)
    out = tmp_path/"report.json"
    with pytest.raises(ValueError, match="source changed during scan"):
        coverage.discover_candidates(*canonical_source, out, clip_ticks=32)
    assert not out.exists()


def test_accepted_coverage_revalidates_and_counts_masks_not_placeholder_zeros(tmp_path, monkeypatch):
    from cs2_data import competitive_control, training_dataset
    root = tmp_path/"accepted"; root.mkdir()
    manifest = root/competitive_control.MANIFEST; manifest.write_text("{}")
    sample_path = root/competitive_control.FILES[0]
    sample_path.write_text(json.dumps({"sample_id": "one", "demo_id": "demo", "clip_id": "clip",
        "observation_frame_index": 7, "label": {"fixture": True}})+"\n")
    called = []
    def independent(path):
        called.append(path)
        return {"demo_id": "demo", "clip_id": "clip", "accepted_count": 1,
                "files": {competitive_control.FILES[0]: coverage.sha256_file(sample_path)}}
    monkeypatch.setattr(competitive_control, "load_competitive_acceptance", independent)
    projection = {"aim_mask": [True, False], "aim_target": [2.0, 0.0],
        "button_mask": [[False]*10 for _ in range(8)], "button_target": [[0.0]*10 for _ in range(8)]}
    projection["button_mask"][7][0:2] = [True, True]
    projection["button_target"][7][0] = 1.0
    monkeypatch.setattr(training_dataset, "project_targets", lambda label: projection)
    result = coverage.accepted_action_coverage([root], tmp_path/"coverage.json")
    assert called == [manifest]
    assert result["button_fields"]["reload"]["held_start"] == {"valid": 1, "positive": 1, "negative": 0, "unknown": 0}
    assert result["button_fields"]["reload"]["held_mid"]["negative"] == 1
    assert result["button_fields"]["reload"]["held_end"]["unknown"] == 1
    assert result["button_fields"]["forward"]["held_start"]["negative"] == 0
    assert result["aim_fields"]["pitch"]["unknown"] == 1
    assert result["training_started"] is False


def test_accepted_coverage_refuses_unvalidated_or_changed_partition(tmp_path, monkeypatch):
    from cs2_data import competitive_control
    root = tmp_path/"accepted"; root.mkdir()
    (root/competitive_control.MANIFEST).write_text("{}")
    (root/competitive_control.FILES[0]).write_text("tampered")
    monkeypatch.setattr(competitive_control, "load_competitive_acceptance", lambda path: {"files": {competitive_control.FILES[0]: "a"*64}})
    with pytest.raises(ValueError, match="partition changed"):
        coverage.accepted_action_coverage([root], tmp_path/"coverage.json")
