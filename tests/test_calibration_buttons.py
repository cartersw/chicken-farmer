from copy import deepcopy
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import calibration_buttons as buttons
from cs2_data.causal_acceptance import protobuf_fields
from test_causal_acceptance import command, embedded, floating, integer
from test_calibration_commands import artifacts
from test_packet_evidence import packet, command as demo_command


def row(number, planes=None, steps=()):
    result = command(number)
    base = protobuf_fields(result["command_protobuf"])[1][0][1]
    old = embedded(3, integer(1, 1) + integer(2, 0) + integer(3, 2))
    raw_planes = b"" if planes is None else embedded(3, b"".join(integer(n, value)
        for n, value in enumerate(planes, 1) if value is not None))
    base = base.replace(old, raw_planes)
    result["buttons_present"] = planes is not None
    for n, value in enumerate(planes or (None, None, None), 1):
        result["buttonstate" + str(n)] = value
    result["subtick_moves"] = []
    for bit, pressed, when in steps:
        base += embedded(18, integer(1, bit) + integer(2, int(pressed)) + floating(3, when))
        result["subtick_moves"].append({"button": bit, "pressed": pressed, "when": when,
            "analog_forward_delta": None, "analog_left_delta": None, "pitch_delta": None, "yaw_delta": None})
    result["command_protobuf"] = embedded(1, base)
    return result


def manifest(commands):
    return {"demo_id": "a" * 64, "command_count": len(commands), "eligible_payload_count": len(commands),
            "full_payload_count": len(commands), "delta_payload_count": 0, "warnings": {},
            "parse_status": "complete", "partial": False}


def dispatch(number, control="forward", pressed=True, name="press"):
    player = {"status": "observed", "steam_id": str(command(number)["steam_id"]), "life_state": 0,
              "controller_tick_base": number, "pawn_handle": 999, "controller_handle": 123,
              "pawn_state": {"movement_last_command_number_processed": number}}
    return {"event": "action_dispatch", "id": name, "command": ("+" if pressed else "-") + control,
            "at_ms": 1000, "local_player_before": player, "local_player_after": deepcopy(player)}


def simple():
    commands = [row(100), row(101, (8, 8, None), [(8, True, 0.0)]), row(102, (8, None, None)),
                row(103, (None, 8, None), [(8, False, 0.0)]), row(104)]
    return commands, [dispatch(100), dispatch(102, pressed=False, name="release")], manifest(commands)


def test_raw_absence_and_numeric_getter_default_are_distinct_and_never_labels():
    report = buttons.summarize_button_hypotheses(*simple())
    idle = report["raw_commands"][0]
    assert not idle["buttons_parent_present"]
    assert idle["raw_planes_hex"] == [None, None, None]
    assert idle["protobuf_getter_default_view_hex"] == ["0x0000000000000000"] * 3
    assert report["control_mask_hypotheses"]["forward"]["candidate_masks_hex"] == ["0x0000000000000008"]
    assert all(not report[key] for key in ("training_ready", "live_control_ready", "button_semantics_verified",
        "exact_input_timing_verified", "dispatch_consumption_mapping_verified"))
    assert all(not r["semantic_held_label_available"] and not r["semantic_event_label_available"] for r in report["raw_commands"])


def test_press_release_supports_any_transition_and_disproves_plane3_press_or_release():
    report = buttons.summarize_button_hypotheses(*simple())
    matrix = report["hypothesis_matrix"]
    assert matrix["plane1_end_state_after_raw_subticks"]["mismatches"] == 0
    assert matrix["plane2_any_raw_transition"]["mismatches"] == 0
    assert matrix["plane2_boundary_xor"]["mismatches"] == 0
    assert matrix["plane3_any_press"]["mismatches"] == 1
    assert matrix["plane3_any_release"]["mismatches"] == 1
    assert matrix["plane3_both_press_and_release"]["expected_nonzero"] == 0


def test_same_command_tap_discriminates_any_transition_from_boundary_xor():
    commands, ledger, _ = simple()
    commands += [row(105, (None, 8, 8), [(8, True, 0.0), (8, False, 0.0)]), row(106)]
    ledger += [dispatch(104, name="tap_press"), dispatch(104, pressed=False, name="tap_release")]
    report = buttons.summarize_button_hypotheses(commands, ledger, manifest(commands))
    assert report["hypothesis_matrix"]["plane2_any_raw_transition"]["mismatches"] == 0
    assert report["hypothesis_matrix"]["plane2_boundary_xor"]["mismatches"] == 1
    assert report["hypothesis_matrix"]["plane3_both_press_and_release"]["expected_nonzero"] == 1
    assert report["dispatch_batches"][-1]["raw_subtick_sequence_matches_hypothesis"] is True
    assert len(report["dispatch_batches"][-1]["commands"]) == 2


@pytest.mark.parametrize("change", ["projection", "invalid_fraction", "missing_step_field", "missing_base"])
def test_bad_raw_or_sparse_subticks_mask_numeric_comparison(change):
    commands, ledger, info = simple()
    if change == "projection": commands[1]["buttonstate1"] = 16
    elif change == "invalid_fraction": commands[1] = row(101, (8, 8, None), [(8, True, 1.0)])
    elif change == "missing_step_field": commands[1]["subtick_moves"][0]["pressed"] = None
    else:
        commands[1].update(command_protobuf=b"", base_present=False)
    report = buttons.summarize_button_hypotheses(commands, ledger, info)
    assert report["raw_commands"][1]["protobuf_getter_default_view_hex"] is None
    assert report["raw_commands"][1]["reason_codes"]
    assert report["dispatch_batches"][0]["status"] != "numeric_pair_observed"


def test_delta_or_missing_baseline_never_gets_standalone_default_view():
    commands, ledger, info = simple()
    info.update(full_payload_count=0, delta_payload_count=len(commands))
    report = buttons.summarize_button_hypotheses(commands, ledger, info)
    assert not report["standalone_full_payload_coverage"]
    assert all(r["protobuf_getter_default_view_hex"] is None for r in report["raw_commands"])
    assert not report["control_mask_hypotheses"]


@pytest.mark.parametrize("change", ["duplicate", "clock", "pawn", "boundary"])
def test_ambiguous_or_changed_associations_do_not_infer_control_masks(change):
    commands, ledger, info = simple()
    ledger = ledger[:1]
    if change == "duplicate": commands.append(deepcopy(commands[1])); info = manifest(commands)
    elif change == "clock": commands[1]["server_tick_executed"] += 1
    elif change == "pawn": commands[1]["pawn_entity_handle"] += 1
    else: ledger[0]["local_player_after"]["pawn_handle"] += 1
    report = buttons.summarize_button_hypotheses(commands, ledger, info)
    assert not report["control_mask_hypotheses"]
    assert report["dispatch_batches"][0]["status"] != "numeric_pair_observed"


def test_raw_server_and_native_pawn_handles_are_not_assumed_same_namespace():
    commands, ledger, info = simple()
    assert commands[0]["pawn_entity_handle"] != ledger[0]["local_player_before"]["pawn_handle"]
    report = buttons.summarize_button_hypotheses(commands, ledger, info)
    assert report["dispatch_batches"][0]["status"] == "numeric_pair_observed"
    assert not report["dispatch_batches"][0]["input_consumption_mapping_verified"]


def test_disagreeing_single_control_bit_hypotheses_are_not_resolved_arbitrarily():
    commands, ledger, info = simple()
    commands[3] = row(103, (None, 16, None), [(16, False, 0.0)])
    report = buttons.summarize_button_hypotheses(commands, ledger, info)
    assert report["control_mask_hypotheses"]["forward"]["status"] == "conflicting_bit_hypotheses"
    assert all(b["raw_subtick_sequence_matches_hypothesis"] is None for b in report["dispatch_batches"])


def test_probe_plan_validates_and_leaves_reload_and_final_idle_time():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "renderer"))
    from calibration_windows import validate_plan
    path = Path(__file__).resolve().parents[1] / "tools/renderer/plans/button-probe-020-v1.json"
    plan = validate_plan(json.loads(path.read_text()))
    assert plan["duration_seconds"] == 20 and len(plan["actions"]) == 40
    assert plan["actions"][-1]["at_ms"] <= 18500
    release = next(i for i, a in enumerate(plan["actions"]) if a["id"] == "reload_release")
    assert plan["actions"][release + 1]["at_ms"] - plan["actions"][release]["at_ms"] >= 3000


def make_demo(commands, checkpoint=False):
    def envelope(row):
        return embedded(1, row["command_protobuf"]) + b"".join(integer(field, row[key]) for field, key in
            ((2, "command_number"), (3, "player_slot"), (4, "server_tick_executed"), (5, "client_tick")))
    data = b"PBDEMS2\0" + bytes(8)
    if checkpoint:
        body = embedded(2, embedded(3, packet([(76, embedded(1, envelope(commands[1])))])))
        data += demo_command(13, commands[0]["demo_tick"], body)
    for r in commands:
        data += demo_command(7, r["demo_tick"], embedded(3, packet([(76, embedded(1, envelope(r)))])))
    return data + demo_command(0, commands[-1]["demo_tick"])


def bind_plan(run, parsed):
    canonical = pq.read_table(parsed / "usercmd.parquet").to_pylist()
    (run / "controlled.dem").write_bytes(make_demo(canonical))
    digest = buttons._sha(run / "controlled.dem")
    info = json.loads((parsed / "manifest.json").read_text())
    info.update(demo_id=digest, sha256=digest)
    for name in ("usercmd", "player_state", "events"):
        path = parsed / (name + ".parquet")
        values = pq.read_table(path).to_pylist()
        for value in values:
            value["demo_id"] = digest
        pq.write_table(pa.Table.from_pylist(values), path)
        info["files"][path.name] = buttons._sha(path)
    (parsed / "manifest.json").write_text(json.dumps(info))
    action = dispatch(117)
    plan = {"producer": buttons.PLAN_PROFILE, "actions": [{k: action[k] for k in ("id", "at_ms", "command")}]}
    (run / "native-plan.json").write_text(json.dumps(plan))
    rows = [{"event": "header", "plan": plan}, {"event": "calibration_ready"}, action, {"event": "calibration_complete"}]
    (run / "calibration_ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    worker = json.loads((run / "calibration.json").read_text())
    worker["demo"]["sha256"] = digest
    worker["calibration_ledger"]["sha256"] = buttons._sha(run / "calibration_ledger.jsonl")
    worker["native_plan_sha256"] = buttons._sha(run / "native-plan.json")
    (run / "calibration.json").write_text(json.dumps(worker))


def test_reader_plan_binding_fresh_output_and_hashes(artifacts, tmp_path):
    run, parsed = artifacts
    bind_plan(run, parsed)
    out = tmp_path / "buttons.json"
    report = buttons.analyze_calibration_buttons(run, parsed, out)
    assert out.is_file() and report["command_count"] == 3
    with pytest.raises(ValueError, match="overwrite"):
        buttons.analyze_calibration_buttons(run, parsed, out)
    (run / "native-plan.json").write_text('{}')
    with pytest.raises(ValueError, match="plan hash"):
        buttons.analyze_calibration_buttons(run, parsed, tmp_path / "bad.json")


def test_plan_dispatch_order_mismatch_is_rejected_even_with_rehashed_ledger(artifacts, tmp_path):
    run, parsed = artifacts
    bind_plan(run, parsed)
    path = run / "calibration_ledger.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[2]["command"] = "+back"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    worker = json.loads((run / "calibration.json").read_text()); worker["calibration_ledger"]["sha256"] = buttons._sha(path)
    (run / "calibration.json").write_text(json.dumps(worker))
    with pytest.raises(ValueError, match="plan/order/completion"):
        buttons.analyze_calibration_buttons(run, parsed, tmp_path / "bad.json")


def test_full_wire_protobuf_bytes_and_nonlive_checkpoint_are_reverified(tmp_path):
    commands, _, _ = simple()
    path = tmp_path / "source.dem"
    path.write_bytes(make_demo(commands, checkpoint=True))
    proof = buttons._full_payload_evidence(path, commands)
    assert proof["standalone_full_payload_bytes_match"]
    assert proof["live_full_payload_count"] == len(commands)
    assert proof["all_packet_full_payload_count"] == len(commands) + 1
    assert proof["unmatched_source_envelopes"][0]["demo_command_kind"] == 13
    commands[1]["command_protobuf"] += integer(100, 1)
    with pytest.raises(ValueError, match="differs from full wire payload"):
        buttons._full_payload_evidence(path, commands)


def test_manifest_cannot_fabricate_full_payload_count(artifacts, tmp_path):
    run, parsed = artifacts
    bind_plan(run, parsed)
    path = parsed / "manifest.json"
    info = json.loads(path.read_text()); info["full_payload_count"] += 1
    path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="coverage disagrees with live wire"):
        buttons.analyze_calibration_buttons(run, parsed, tmp_path / "bad.json")


def test_duplicate_canonical_row_cannot_replace_missing_wire_command(tmp_path):
    commands, _, _ = simple()
    path = tmp_path / "source.dem"
    path.write_bytes(make_demo(commands))
    commands[-1] = deepcopy(commands[-2])
    with pytest.raises(ValueError, match="ambiguous wire envelope"):
        buttons._full_payload_evidence(path, commands)


def frozen_example():
    return {"rules": buttons.FROZEN_RULES, "confirmation_controls": {"forward": "0x0000000000000008"}}


@pytest.mark.parametrize("count", [3, 4])
def test_frozen_rule_confirmation_preserves_ordered_three_and_four_transitions(count):
    steps = [(8, n % 2 == 0, 0.0) for n in range(count)]
    commands = [row(100), row(101, (8 if count % 2 else None, 8 if count % 2 else None, 8), steps)]
    ledger = [dispatch(100, pressed=step[1], name=str(n)) for n, step in enumerate(steps)]
    report = buttons.summarize_button_hypotheses(commands, ledger, manifest(commands), comparison_mask=8)
    # No single-dispatch example occurs in this recording. The prior mask is required.
    assert not report["control_mask_hypotheses"]
    result = buttons._confirmation(report, frozen_example())
    assert result["confirmation_passed"] and not result["semantic_training_labels_enabled"]
    assert len(result["dispatch_sequence_checks"][0]["observed_ordered_subticks"]) == count


@pytest.mark.parametrize("failure", ["collapsed", "reordered", "wrong_plane", "unavailable"])
def test_frozen_confirmation_rejects_unrecorded_reordered_or_contradictory_evidence(failure):
    steps = [(8, True, 0.0), (8, False, 0.0), (8, True, 0.0)]
    observed = steps if failure != "collapsed" else steps[-1:]
    if failure == "reordered": observed = [steps[0], steps[2], steps[1]]
    commands = [row(100), row(101, (8, 8, None if failure == "wrong_plane" else 8), observed)]
    ledger = [dispatch(100, pressed=step[1], name=str(n)) for n, step in enumerate(steps)]
    if failure == "unavailable": ledger[0]["local_player_after"]["pawn_handle"] += 1
    report = buttons.summarize_button_hypotheses(commands, ledger, manifest(commands), comparison_mask=8)
    result = buttons._confirmation(report, frozen_example())
    assert not result["confirmation_passed"]
    assert result["status"] == "contradicted_or_incomplete"


def test_frozen_comparison_mask_does_not_shrink_when_control_subticks_are_missing():
    commands = [row(100), row(101, (8, 8, None))]
    report = buttons.summarize_button_hypotheses(commands, [], manifest(commands), comparison_mask=8)
    assert report["tested_subtick_mask_hex"] == "0x0000000000000008"
    assert report["hypothesis_matrix"]["plane1_end_state_after_raw_subticks"]["mismatches"] == 1
    assert not buttons._confirmation(report, frozen_example())["confirmation_passed"]


def frozen_files(tmp_path):
    commands, ledger, _ = simple()
    commands += [row(105, (None, None, 8), [(8, True, 0.0), (8, False, 0.0)])]
    report = buttons.summarize_button_hypotheses(commands, ledger, manifest(commands))
    source = tmp_path / "source.json"; source.write_text(json.dumps(report))
    plan = tmp_path / "plan.json"; plan.write_text(json.dumps({"actions": []}))
    value = {"schema_version": 1, "profile": "frozen_button_hypotheses_v1", **frozen_example(),
        "required_rule_mismatches": 0, "require_exact_ordered_subtick_sequence_for_each_dispatch_batch": True,
        "no_retuning_from_confirmation_data": True, "source_analysis_path": str(source),
        "source_analysis_sha256": buttons._sha(source), "confirmation_plan_path": str(plan),
        "confirmation_plan_sha256": buttons._sha(plan), "source_demo_sha256": "a" * 64}
    path = tmp_path / "frozen.json"; path.write_text(json.dumps(value))
    return path, value, source


@pytest.mark.parametrize("failure", [None, "source_hash", "same_demo", "plan", "mask", "rule", "vacuous"])
def test_frozen_artifact_binds_prior_evidence_control_masks_rules_and_new_recording(tmp_path, failure):
    path, value, source = frozen_files(tmp_path)
    plan, demo = {"actions": [], "movie_name": "runtime", "demo_path": "runtime.dem"}, "b" * 64
    if failure == "source_hash": source.write_text("{}")
    elif failure == "same_demo": demo = "a" * 64
    elif failure == "plan": plan["actions"] = [{"command": "+back"}]
    elif failure == "mask": value["confirmation_controls"]["forward"] = "0x0000000000000010"
    elif failure == "rule": value["rules"] = {**value["rules"], "plane2": "any transition"}
    elif failure == "vacuous":
        report = json.loads(source.read_text())
        report["hypothesis_matrix"]["plane3_both_press_and_release"]["expected_nonzero"] = 0
        source.write_text(json.dumps(report)); value["source_analysis_sha256"] = buttons._sha(source)
    path.write_text(json.dumps(value))
    if failure is None:
        _, mask, sources = buttons._frozen_hypotheses(path, plan, demo)
        assert mask == 8 and len(sources) == 3
    else:
        with pytest.raises(ValueError): buttons._frozen_hypotheses(path, plan, demo)


def test_source_metadata_changes_during_analysis_block_publication(artifacts, tmp_path, monkeypatch):
    run, parsed = artifacts
    bind_plan(run, parsed)
    original = buttons.summarize_button_hypotheses
    def mutate(*args, **kwargs):
        report = original(*args, **kwargs)
        path = run / "calibration.json"
        path.write_text(path.read_text() + "\n")
        return report
    monkeypatch.setattr(buttons, "summarize_button_hypotheses", mutate)
    out = tmp_path / "changed.json"
    with pytest.raises(ValueError, match="changed during analysis"):
        buttons.analyze_calibration_buttons(run, parsed, out)
    assert not out.exists()


def test_confirmation_plan_validates_offgrid_triple_and_quad_batches():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "renderer"))
    from calibration_windows import validate_plan
    path = Path(__file__).resolve().parents[1] / "tools/renderer/plans/button-confirmation-008-v1.json"
    plan = validate_plan(json.loads(path.read_text()))
    assert plan["duration_seconds"] == 8 and len(plan["actions"]) == 12
    assert [a["command"] for a in plan["actions"] if a["at_ms"] == 1001] == ["+forward", "-forward", "+forward"]
    assert [a["command"] for a in plan["actions"] if a["at_ms"] == 2407] == ["-back", "+back", "-back"]
    assert [a["command"] for a in plan["actions"] if a["at_ms"] == 4009] == ["+attack", "-attack", "+attack", "-attack"]
    assert all(a["at_ms"] % 31.25 != 0 for a in plan["actions"])
