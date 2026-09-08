from copy import deepcopy

import pytest

from cs2_data.control_contract import DECISION_PERIOD_NS, SERVER_TICK_NS
from cs2_data.control_program import default_program, project_events, validate_program


def test_default_program_has_five_balanced_combined_phases():
    program = validate_program(default_program())
    assert len(program["actions"]) == 256
    edges = [edge for action in program["actions"] for edge in action["button_events"]]
    assert len(edges) == 10 and sum(edge["pressed"] for edge in edges) == 5
    assert all(not value for value in program["actions"][-1]["buttons_held_at_start"].values())


def test_encoded_threshold_selects_same_native_ticks_as_exact32hz_boundary():
    events = [{"offset_ns": i * DECISION_PERIOD_NS, "event": {"kind": "mouse_move", "dx": 1, "dy": 0}}
              for i in range(16)]
    plan, trace = project_events(default_program(), events)
    for event, proof in zip(plan["events"], trace):
        exact = proof["requested_capture_offset_ns"]
        for tick in range(exact // SERVER_TICK_NS - 1, exact // SERVER_TICK_NS + 2):
            assert (tick * SERVER_TICK_NS >= exact) == (tick * SERVER_TICK_NS / 1e6 >= event["at_ms"])
    assert plan["events"][1]["at_ms"] == 1031
    assert trace[1]["encoded_threshold_delta_ns"] == -250000


@pytest.mark.parametrize("offset", [1, SERVER_TICK_NS, DECISION_PERIOD_NS - 1])
def test_movie_bridge_refuses_finer_offsets(offset):
    with pytest.raises(ValueError, match="decision-boundary"):
        project_events(default_program(), [{"offset_ns": offset, "event": {"kind": "mouse_move", "dx": 1, "dy": 0}}])


@pytest.mark.parametrize("start_delta", [1, SERVER_TICK_NS, DECISION_PERIOD_NS - 1])
def test_program_refuses_start_between_decision_boundaries(start_delta):
    program = default_program()
    program["start_at_ns"] += start_delta
    with pytest.raises(ValueError, match="start.*decision-boundary"):
        validate_program(program)


def test_off_grid_start_and_event_cannot_cancel_into_supported_capture_boundary():
    program = default_program()
    program["start_at_ns"] += 1
    offset = DECISION_PERIOD_NS - 1
    assert (program["start_at_ns"] + offset) % DECISION_PERIOD_NS == 0
    with pytest.raises(ValueError, match="start.*decision-boundary"):
        project_events(program, [{"offset_ns": offset, "event": {"kind": "mouse_move", "dx": 1, "dy": 0}}])


@pytest.mark.parametrize("change", [
    lambda p: p.update(duration_seconds=2), lambda p: p.update(start_at_ns=True),
    lambda p: p.update(actions=[]), lambda p: p["actions"][0].update(decision_period_ns=1),
    lambda p: p.update(extra="ignored"),
])
def test_program_validation_rejects_invalid_time_or_contract(change):
    program = deepcopy(default_program())
    change(program)
    with pytest.raises(ValueError):
        validate_program(program)
