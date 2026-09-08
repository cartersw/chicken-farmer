from copy import deepcopy
import json
from pathlib import Path

import pytest

from cs2_data import synthetic_input_plan as plans


def move(name="move", at=500, dx=100, dy=0):
    return {"id": name, "at_ms": at, "kind": "mouse_move", "dx": dx, "dy": dy}


def key(name="press", at=500, control="W", pressed=True):
    return {"id": name, "at_ms": at, "kind": "key", "key": control, "pressed": pressed}


def button(name="press", at=500, control="left", pressed=True):
    return {"id": name, "at_ms": at, "kind": "mouse_button", "button": control, "pressed": pressed}


def plan(events=None, duration=12):
    return {"schema_version": 1, "producer": plans.PROFILE, "map": "de_dust2",
        "fps": 32, "width": 1280, "height": 720, "duration_seconds": duration,
        "events": events if events is not None else [move()]}


def test_valid_plan_copy_cannot_mutate_callers_original():
    value = plan()
    validated = plans.validate_synthetic_input_plan(value)
    assert validated == value and validated is not value
    validated["events"][0]["dx"] = 20
    assert value["events"][0]["dx"] == 100


@pytest.mark.parametrize("change", [
    {"producer": "cs2-controlled-calibration-plan-v1"}, {"schema_version": True}, {"schema_version": 2},
    {"map": "de_mirage"}, {"fps": True}, {"fps": 64}, {"width": 1920}, {"height": 1080},
    {"duration_seconds": 1}, {"duration_seconds": 31}, {"duration_seconds": 12.0},
    {"duration_seconds": True}, {"command": "+forward"}, {"events": []}, {"events": None},
])
def test_rejects_wrong_profile_scope_shape_or_boundaries(change):
    value = plan(); value.update(change)
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(value)


@pytest.mark.parametrize("change", [
    {"kind": "console", "command": "+forward"}, {"kind": None}, {"kind": []},
    {"dx": True}, {"dx": 0.5}, {"dy": float("nan")}, {"dy": float("inf")},
    {"dx": 0, "dy": 0}, {"dx": 201}, {"dx": -201}, {"dx": 100, "dy": 101},
    {"absolute": True}, {"command": "setang 0 0 0"}, {"key": "W"},
    {"id": "UPPER"}, {"id": "a" * 65}, {"id": "a\nb"}, {"id": 1},
    {"at_ms": True}, {"at_ms": 500.0}, {"at_ms": 499}, {"at_ms": 11501},
])
def test_rejects_unknown_actuators_extra_fields_unbounded_moves_and_bad_times(change):
    event = move(); event.update(change)
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(plan([event]))


@pytest.mark.parametrize("control", ["ESC", "LWIN", "F1", "ENTER", "TAB", "OEM_3", "w", "CTRL", None, []])
def test_disallowed_or_ambiguous_keys_are_rejected(control):
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(plan([key(control=control)]))


@pytest.mark.parametrize("control", ["middle", "x1", "LEFT", None, []])
def test_disallowed_mouse_buttons_are_rejected(control):
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(plan([button(control=control)]))


@pytest.mark.parametrize("make", [key, button])
@pytest.mark.parametrize("failure", ["unreleased", "orphan_release", "double_press", "double_release", "too_long", "nonbool"])
def test_all_press_channels_require_explicit_bounded_pairing(make, failure):
    a, b = make(), make("release", 700, pressed=False)
    events = [a, b]
    if failure == "unreleased": events.pop()
    elif failure == "orphan_release": events.pop(0)
    elif failure == "double_press": events.insert(1, make("again", 600))
    elif failure == "double_release": events.append(make("again", 800, pressed=False))
    elif failure == "too_long": b["at_ms"] = 2501
    else: a["pressed"] = 1
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(plan(events))


def test_overlaps_have_independent_key_and_button_state_and_equal_time_order_is_preserved():
    events = [key(), key("duck_press", control="LCTRL"), button("mouse_press"),
              key("forward_release", 700, pressed=False), button("mouse_release", 700, pressed=False),
              key("duck_release", 700, control="LCTRL", pressed=False)]
    assert plans.validate_synthetic_input_plan(plan(events))["events"] == events
    # Equal-time taps are supported as requests, without promising the game will
    # retain each edge or assign unique physical input timestamps.
    events = [key("p1"), key("r1", pressed=False), key("p2"), key("r2", pressed=False)]
    assert plans.validate_synthetic_input_plan(plan(events))["events"] == events


def test_identifiers_order_boundary_burst_total_count_and_absolute_mouse_budget():
    cases = [
        [move(), move(at=600)],  # Duplicate ID.
        [move(at=600), move("earlier", at=500)],
        [move(str(n), at=500, dx=1) for n in range(9)],  # IDs fixed below.
        [move("m" + str(n), at=500 + n * 10, dx=1) for n in range(129)],
        [move("a", dx=100), move("b", dx=-101)],  # Absolute, not signed-net bound.
        [move("m" + str(n), at=500 + n * 100, dx=200 * (-1 if n % 2 else 1)) for n in range(17)],
    ]
    for i, event in enumerate(cases[2]): event["id"] = "m" + str(i)
    for events in cases:
        with pytest.raises(ValueError): plans.validate_synthetic_input_plan(plan(events))


def test_exact_limits_are_allowed():
    events = [key(), key("release", 2500, pressed=False)]
    assert plans.validate_synthetic_input_plan(plan(events))
    events = [move("m" + str(n), at=500 + n * 50, dx=25) for n in range(128)]
    assert plans.validate_synthetic_input_plan(plan(events))
    events = [move("m" + str(n), at=500, dx=25) for n in range(8)]
    assert plans.validate_synthetic_input_plan(plan(events))
    assert plans.validate_synthetic_input_plan(plan([move(at=1500)], duration=2))
    assert plans.validate_synthetic_input_plan(plan([move(at=29500)], duration=30))


def test_console_actions_cannot_be_passed_as_synthetic_events():
    value = plan()
    value["actions"] = [{"id": "forward", "at_ms": 1000, "command": "+forward"}]
    del value["events"]
    with pytest.raises(ValueError): plans.validate_synthetic_input_plan(value)


def test_checked_in_mouse_probe_has_isolated_axes_and_balanced_bounded_requested_counts():
    path = Path(__file__).resolve().parents[1] / "tools/renderer/plans/synthetic-mouse-probe-012-v1.json"
    value = plans.validate_synthetic_input_plan(json.loads(path.read_text()))
    assert len(value["events"]) == 12 and value["duration_seconds"] == 12
    offsets = {"dx": 0, "dy": 0}
    for event in value["events"]:
        assert event["kind"] == "mouse_move" and (event["dx"] == 0) != (event["dy"] == 0)
        for axis in offsets:
            offsets[axis] += event[axis]
            assert abs(offsets[axis]) <= 80
    assert offsets == {"dx": 0, "dy": 0}
    for axis in offsets:
        assert {event[axis] for event in value["events"]} == {-80, -40, -20, 0, 20, 40, 80}
    assert min(b["at_ms"] - a["at_ms"] for a, b in zip(value["events"], value["events"][1:])) >= 750
    fitting = [event for event in value["events"] if event["id"].startswith("fit-")]
    held_out = [event for event in value["events"] if event["id"].startswith("validation-")]
    assert len(fitting) == 4 and len(held_out) == 8
    assert all(abs(event["dx"]) + abs(event["dy"]) == 40 for event in fitting)
    assert all(abs(event["dx"]) + abs(event["dy"]) in (20, 80) for event in held_out)
    for axis, field in (("x", "dx"), ("y", "dy")):
        for direction, sign in (("positive", 1), ("negative", -1)):
            assert next(event for event in fitting if event["id"] == f"fit-{axis}-{direction}")[field] == sign * 40
            for scale, count in (("small", 20), ("large", 80)):
                name = f"validation-{axis}-{scale}-{direction}"
                assert next(event for event in held_out if event["id"] == name)[field] == sign * count


def test_checked_in_key_probe_has_release_pairs_jump_settle_and_reload_idle():
    path = Path(__file__).resolve().parents[1] / "tools/renderer/plans/synthetic-keyboard-probe-012-v1.json"
    value = plans.validate_synthetic_input_plan(json.loads(path.read_text()))
    assert len(value["events"]) == 20 and all(e["kind"] != "mouse_move" for e in value["events"])
    jump = next(i for i, event in enumerate(value["events"]) if event["id"] == "jump_release")
    assert value["events"][jump + 1]["at_ms"] - value["events"][jump]["at_ms"] >= 1900
    assert value["duration_seconds"] * 1000 - value["events"][-1]["at_ms"] >= 2000


def test_cli_only_validates_and_never_reports_delivery_or_training_ready(tmp_path, capsys):
    path = tmp_path / "plan.json"; path.write_text(json.dumps(plan()))
    assert plans.main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["static_plan_valid"] and not result["input_delivery_verified"] and not result["training_ready"]
    path.write_text(json.dumps(plan([move(dx=float("nan"))])))
    with pytest.raises(ValueError, match="Non-finite JSON"): plans.main([str(path)])
