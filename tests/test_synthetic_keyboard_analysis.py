from copy import deepcopy
import json
from pathlib import Path

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from cs2_data import synthetic_keyboard_analysis as audit
from cs2_data.synthetic_input_plan import PROFILE as PLAN_PROFILE
from cs2_data.causal_acceptance import protobuf_fields
from test_calibration_buttons import row, manifest
from test_calibration_buttons import make_demo
from test_causal_acceptance import embedded, floating
from test_synthetic_input_analysis import injection_evidence


def fixture(control="W", *, observed=True, release=108):
    mask = audit.KEY_MASKS.get(control, 1)
    first, last = 98, release + audit.TAIL.get(control, 16) + 2
    commands, native, events = [], [], []
    for number in range(first, last + 1):
        active = observed and 101 <= number <= release
        edge = observed and number in (101, release + 1)
        steps = [(mask, number == 101, .25)] if edge and control != "R" else []
        command = row(number, (mask if active else None, mask if edge else None, None) if active or edge else None, steps)
        base = protobuf_fields(command["command_protobuf"])[1][0][1]
        f = (1.0 if control == "W" else -1.0) if active and control in ("W", "S") else 0.0
        l = (1.0 if control == "A" else -1.0) if active and control in ("A", "D") else 0.0
        command.update(command_protobuf=embedded(1, base.replace(floating(5, 1.0), floating(5, f)).replace(floating(6, 0.0), floating(6, l))),
                       forwardmove=f, leftmove=l)
        commands.append(command)
        state = {"health": 100, "movement_last_command_number_processed": number, "origin": [0.0, 0.0, 0.0],
                 "velocity": [0.0, 0.0, 0.0], "duck_amount": 0.0, "ducked": False, "on_ground": True,
                 "ammo_clip": 19 if control == "R" else 20, "active_weapon_handle": 123, "last_shot_time": 0.0}
        if observed and number >= 101:
            if control in ("W", "S", "A", "D"):
                state["origin"] = [float(min(number - 100, release - 100)), 0.0, 0.0]
                if active: state["velocity"] = [10.0, 0.0, 0.0]
            elif control == "LCTRL" and active: state.update(duck_amount=1.0, ducked=True)
            elif control == "SPACE" and number < 145: state.update(on_ground=False, velocity=[0.0, 0.0, 10.0])
            elif control == "mouse_left": state.update(ammo_clip=19, last_shot_time=10.0)
            elif control == "R" and number >= 200: state["ammo_clip"] = 20
        player = {"status": "observed", "steam_id": str(command["steam_id"]), "pawn_handle": 999,
                  "controller_handle": 1234, "life_state": 0, "controller_tick_base": number, "pawn_state": state}
        native.append({"event": "frame_sample", "qpc": 100000 + number * 100,
                       "elapsed_ms": (number - 36) * 15.625, "local_player": player})
    press = {"id": "press", "at_ms": 1000, "kind": "key", "key": control, "pressed": True}
    if control == "mouse_left": press = {"id": "press", "at_ms": 1000, "kind": "mouse_button", "button": "left", "pressed": True}
    up = {**press, "id": "release", "at_ms": int((release - 36) * 15.625), "pressed": False}
    plan = {"schema_version": 1, "producer": PLAN_PROFILE, "map": "de_dust2", "fps": 32,
            "width": 1280, "height": 720, "duration_seconds": 12, "events": [press, up]}
    batches = [{"events": [event], "native_frame_qpc": 100000 + number * 100,
                "actual_elapsed_ms": (number - 36) * 15.625,
                "receipt": {"qpc_before": 100001 + number * 100, "qpc_after": 100002 + number * 100}}
               for number, event in ((100, press), (release, up))]
    if observed and control == "mouse_left":
        events.append({"kind": "weapon_fire", "steam_id": commands[0]["steam_id"], "round_id": 3, "demo_tick": 51, "weapon": "Glock-18"})
    return plan, batches, native, commands, [], events, manifest(commands)


@pytest.mark.parametrize("control", ["W", "S", "A", "D", "LCTRL", "SPACE", "mouse_left", "R"])
def test_supported_response_families_need_raw_and_native_response_evidence(control):
    report = audit.summarize_synthetic_keyboard(*fixture(control))
    assert report["status"] == "response_evidence_observed_for_all_phases"
    assert report["phase_status_counts"] == {"observed_response_evidence": 1}
    assert all(not report[key] for key in ("training_ready", "live_control_ready", "button_semantics_verified",
        "exact_input_timing_verified", "input_consumption_mapping_verified", "source_provenance_verified_by_core"))
    assert all(not e["one_to_one_edge_assignment_verified"] and e["exact_input_time_ns"] is None for e in report["edges"])
    if control != "R": assert report["edges"][0]["raw_subtick_records"][0]["raw"]["when"] == .25
    else:
        assert report["edges"][0]["raw_subtick_records"] == []
        assert report["edges"][0]["plane_change_consistency_command_numbers"] == [101]


@pytest.mark.parametrize("control", ["W", "S", "A", "D", "LCTRL", "SPACE", "mouse_left", "R"])
def test_accepted_os_insertion_with_no_game_response_never_passes(control):
    report = audit.summarize_synthetic_keyboard(*fixture(control, observed=False))
    assert report["status"] == "incomplete_or_missing_response"
    assert report["phases"][0]["status"] == "missing_observed_response"
    assert report["edges"][0]["status"] == "missing_response_record"


@pytest.mark.parametrize("failure", ["missing_command", "duplicate_command", "raw_projection", "dead_command", "native_pawn",
                                     "native_dead", "native_clock_gap", "native_clock", "native_qpc_duplicate", "bad_anchor"])
def test_missing_changed_or_ambiguous_clock_and_identity_evidence_stays_unknown(failure):
    values = list(fixture())
    plan, batches, native, commands, states, events, info = values
    if failure == "missing_command": commands.pop(5); info.update(manifest(commands))
    elif failure == "duplicate_command": commands.insert(5, deepcopy(commands[5])); info.update(manifest(commands))
    elif failure == "raw_projection": commands[5]["buttonstate1"] = 16
    elif failure == "dead_command": commands[5]["alive"] = False
    elif failure == "native_pawn": native[5]["local_player"]["pawn_handle"] += 1
    elif failure == "native_dead": native[5]["local_player"]["life_state"] = 1
    elif failure == "native_clock_gap": del native[5:10]
    elif failure == "native_clock": native[5]["local_player"]["controller_tick_base"] += 1
    elif failure == "native_qpc_duplicate": native[5]["qpc"] = native[4]["qpc"]
    else: batches[0]["native_frame_qpc"] += 1
    report = audit.summarize_synthetic_keyboard(*values)
    assert report["phases"][0]["status"] == "unknown"
    assert report["status"] == "incomplete_or_missing_response"


def test_queued_edge_does_not_count_without_native_movement():
    values = list(fixture())
    for frame in values[2]:
        frame["local_player"]["pawn_state"].update(origin=[0.0, 0.0, 0.0], velocity=[0.0, 0.0, 0.0])
    report = audit.summarize_synthetic_keyboard(*values)
    assert report["edges"][0]["status"] == "response_record_observed"
    assert report["phases"][0]["status"] == "missing_observed_response"


@pytest.mark.parametrize("failure", ["missing_event", "other_player", "other_round", "outside_window", "missing_ammo_drop", "weapon_change"])
def test_shot_requires_matching_event_and_same_weapon_native_ammo_response(failure):
    values = list(fixture("mouse_left"))
    if failure == "missing_event": values[5].clear()
    elif failure == "other_player": values[5][0]["steam_id"] += 1
    elif failure == "other_round": values[5][0]["round_id"] += 1
    elif failure == "outside_window": values[5][0]["demo_tick"] += 1000
    elif failure == "missing_ammo_drop":
        for frame in values[2]: frame["local_player"]["pawn_state"]["ammo_clip"] = 20
    else: values[2][5]["local_player"]["pawn_state"]["active_weapon_handle"] += 1
    report = audit.summarize_synthetic_keyboard(*values)
    assert report["phases"][0]["status"] != "observed_response_evidence"


def test_incomplete_long_reload_window_is_not_accepted_after_early_ammo_increase():
    values = list(fixture("R"))
    values[2] = values[2][:-20]
    report = audit.summarize_synthetic_keyboard(*values)
    assert report["phases"][0]["effect"]["status"] == "observed_response_evidence"
    assert report["phases"][0]["status"] == "unknown"


def test_overlapping_other_input_response_window_is_explicitly_ambiguous():
    values = list(fixture())
    other = {"id": "other_press", "at_ms": 1200, "kind": "key", "key": "A", "pressed": True}
    other_up = {**other, "id": "other_release", "at_ms": 1220, "pressed": False}
    values[0]["events"] += [other, other_up]
    for number, event in ((114, other), (115, other_up)):
        values[1].append({"events": [event], "native_frame_qpc": 100000 + number * 100, "actual_elapsed_ms": (number - 36) * 15.625,
                         "receipt": {"qpc_before": 100001 + number * 100, "qpc_after": 100002 + number * 100}})
    report = audit.summarize_synthetic_keyboard(*values)
    assert "other_planned_input_overlaps_response_window" in report["phases"][0]["reason_codes"]
    assert report["phases"][0]["status"] == "unknown"


def test_full_window_defaults_preserve_absence_without_fabricating_edges():
    report = audit.summarize_synthetic_keyboard(*fixture())
    raw = report["raw_commands"][0]
    assert not raw["buttons_parent_present"] and raw["raw_planes_hex"] == [None, None, None]
    assert raw["protobuf_getter_default_view_hex"] == ["0x0000000000000000"] * 3
    assert not raw["semantic_event_label_available"]


def test_sparse_delta_input_cannot_use_standalone_defaults():
    values = list(fixture())
    values[-1].update(full_payload_count=0, delta_payload_count=len(values[3]))
    report = audit.summarize_synthetic_keyboard(*values)
    assert not report["standalone_full_payload_coverage"]
    assert report["phases"][0]["status"] == "unknown"


def test_receipt_event_list_cannot_silently_drop_a_planned_release():
    values = list(fixture()); values[1].pop()
    with pytest.raises(ValueError, match="batches differ"):
        audit.summarize_synthetic_keyboard(*values)


def test_frozen_protocol_is_bound_to_exact_checked_in_plan_and_windows():
    path = Path(__file__).resolve().parents[1] / "tools/renderer/plans/synthetic-keyboard-protocol-v1.json"
    policy = json.loads(path.read_text())
    assert audit._sha(path) == audit.PROTOCOL_SHA256
    assert audit._sha(path.with_name("synthetic-keyboard-probe-012-v1.json")) == policy["plan_sha256"]
    assert policy["command_edge_window_relative_to_observed_last_processed"] == list(audit.EDGE_WINDOW)
    assert policy["jump_phase_tail_ticks"] == audit.TAIL["SPACE"]
    assert policy["reload_phase_tail_ticks"] == audit.TAIL["R"]


def test_edge_already_processed_before_delayed_insertion_is_not_a_response():
    values = list(fixture())
    values[1][0]["receipt"].update(qpc_before=110201, qpc_after=110202)
    report = audit.summarize_synthetic_keyboard(*values)
    edge = report["edges"][0]
    assert edge["observed_already_processed_before_insertion"] == 102
    assert edge["raw_subtick_records"]  # Preserve the earlier edge as raw evidence.
    assert edge["status"] == "missing_response_record"
    assert report["phases"][0]["status"] == "missing_observed_response"


def test_insertion_after_fixed_window_stays_unknown_without_searching_later():
    values = list(fixture())
    values[1][1]["receipt"].update(qpc_before=130000, qpc_after=130001)
    report = audit.summarize_synthetic_keyboard(*values)
    assert report["phases"][0]["status"] == "unknown"
    assert "insertion_not_before_end_of_fixed_response_window" in report["phases"][0]["reason_codes"]


def test_shot_record_already_processed_before_insertion_is_retained_but_cannot_corroborate_new_response():
    values = list(fixture("mouse_left"))
    values[1][0]["receipt"].update(qpc_before=110201, qpc_after=110202)
    for frame in values[2]:
        if frame["local_player"]["pawn_state"]["movement_last_command_number_processed"] < 105:
            frame["local_player"]["pawn_state"].update(ammo_clip=20, last_shot_time=0.0)
    phase = audit.summarize_synthetic_keyboard(*values)["phases"][0]
    assert phase["canonical_weapon_fire_events_in_full_window"]
    assert phase["effect"]["canonical_weapon_fire_events"] == []
    assert phase["effect"]["status"] == "missing_observed_response"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Synthetic provenance fixture; replaces the frozen profile only in tests."""
    run, parsed, policies = (tmp_path / name for name in ("run", "parsed", "policies"))
    for path in (run, parsed, policies): path.mkdir()
    plan, batches, native, commands, states, game_events, info = fixture()
    template_rows, template_native, _, worker = injection_evidence()
    native_plan = {**plan, "movie_name": "calibration-" + worker["run_id"], "demo_path": str(run / "controlled.dem")}
    (run / "native-plan.json").write_text(json.dumps(native_plan))
    worker["native_plan_sha256"] = audit._sha(run / "native-plan.json")
    header = deepcopy(template_native[0]); header["plan"] = native_plan
    ready = deepcopy(template_native[1]); ready["start_tick_base"] = 36
    for frame in native: frame.update(owned_process_id=worker["owned_cs2_pid"], local_connection_verified=True)
    native = [header, ready, *native, {"event": "recording_stop_dispatched", "qpc": 200000}]
    input_header = deepcopy(template_rows[0]); input_header.update(source_plan=plan, plan_sha256=worker["native_plan_sha256"])
    input_rows = [input_header]
    for batch in batches:
        receipt = deepcopy(template_rows[1]["receipt"])
        receipt.update(batch["receipt"])
        receipt["events"] = [{k: v for k, v in e.items() if k not in ("id", "at_ms")} for e in batch["events"]]
        input_rows.append({**batch, "event": "injection_batch", "schema_version": 1,
                           "scheduled_at_ms": batch["events"][0]["at_ms"], "receipt": receipt})
    complete = deepcopy(template_rows[-2]); complete.update(qpc=130000, events_inserted=2)
    input_rows += [deepcopy(template_rows[-3]), complete, deepcopy(template_rows[-1])]
    for name, values in (("calibration_ledger.jsonl", native), ("input_ledger.jsonl", input_rows)):
        path = run / name; path.write_text("".join(json.dumps(r) + "\n" for r in values))
        worker[name.removesuffix(".jsonl")] = {"path": name, "sha256": audit._sha(path)}
    (run / "controlled.dem").write_bytes(make_demo(commands))
    digest = audit._sha(run / "controlled.dem")
    worker["demo"] = {"sha256": digest}
    (run / "calibration.json").write_text(json.dumps(worker))
    info.update(demo_id=digest, sha256=digest, files={}, tick_rate=64)
    for name, values in (("usercmd", commands), ("player_state", states), ("events", game_events)):
        for value in values: value["demo_id"] = digest
        path = parsed / (name + ".parquet")
        pq.write_table(pa.Table.from_pylist(values), path)
        info["files"][path.name] = audit._sha(path)
    (parsed / "manifest.json").write_text(json.dumps(info))
    plan_path = policies / "synthetic-keyboard-probe-012-v1.json"; plan_path.write_text(json.dumps(plan))
    protocol = policies / "protocol.json"; protocol.write_text(json.dumps({"plan_sha256": audit._sha(plan_path)}))
    monkeypatch.setattr(audit, "PROTOCOL_SHA256", audit._sha(protocol))
    return run, parsed, protocol


def test_reader_verifies_receipts_full_wire_payloads_and_never_overwrites(files, tmp_path):
    out = tmp_path / "report.json"
    report = audit.analyze_synthetic_keyboard(*files, out)
    assert report["status"] == "response_evidence_observed_for_all_phases"
    assert report["input_receipt_provenance_verified"]
    assert report["wire_payload_evidence"]["standalone_full_payload_bytes_match"]
    assert not report["training_ready"]
    with pytest.raises(ValueError, match="overwrite"):
        audit.analyze_synthetic_keyboard(*files, out)


@pytest.mark.parametrize("target", ["input_ledger", "native_plan", "source_plan", "protocol", "raw_command", "tick_rate"])
def test_reader_rejects_provenance_mutations_before_publishing(files, tmp_path, target):
    run, parsed, protocol = files
    if target == "input_ledger":
        path = run / "input_ledger.jsonl"; path.write_text(path.read_text() + "\n")
    elif target == "native_plan":
        path = run / "native-plan.json"; path.write_text(path.read_text() + "\n")
    elif target == "source_plan":
        path = protocol.with_name("synthetic-keyboard-probe-012-v1.json"); path.write_text(path.read_text() + "\n")
    elif target == "protocol": protocol.write_text("{}")
    elif target == "tick_rate":
        path = parsed / "manifest.json"; info = json.loads(path.read_text()); info["tick_rate"] = 128
        path.write_text(json.dumps(info))
    else:
        path = parsed / "usercmd.parquet"
        values = pq.read_table(path).to_pylist(); values[0]["command_protobuf"] += b"\xa0\x06\x01"
        pq.write_table(pa.Table.from_pylist(values), path)
        info = json.loads((parsed / "manifest.json").read_text()); info["files"][path.name] = audit._sha(path)
        (parsed / "manifest.json").write_text(json.dumps(info))
    out = tmp_path / "bad.json"
    with pytest.raises(ValueError): audit.analyze_synthetic_keyboard(*files, out)
    assert not out.exists()


def test_input_source_change_during_core_analysis_blocks_publication(files, tmp_path, monkeypatch):
    original = audit.summarize_synthetic_keyboard
    def changing(*args, **kwargs):
        report = original(*args, **kwargs)
        path = files[0] / "input_ledger.jsonl"
        path.write_text(path.read_text() + "\n")
        return report
    monkeypatch.setattr(audit, "summarize_synthetic_keyboard", changing)
    out = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="changed during analysis"):
        audit.analyze_synthetic_keyboard(*files, out)
    assert not out.exists()
