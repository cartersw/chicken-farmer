"""Bounded scripted 32 Hz action programs and explicit native schedule projection."""
from __future__ import annotations

from copy import deepcopy

from .control_contract import BUTTONS, CHANNELS, CONTRACT_ID, DECISION_PERIOD_NS, validate_control_action
from .synthetic_input_plan import PROFILE as SYNTHETIC_PROFILE, validate_synthetic_input_plan

PROFILE = "cs2-control-execution-program-v1"


def default_program():
    """Five separated combined-motion phases; this is a scripted probe, not a policy."""
    phases = [(0, "forward", -.055, .011), (48, "left", .055, -.011),
              (96, "back", -.033, -.033), (144, "right", .033, .033),
              (192, "crouch", -.011, .011)]
    actions, held = [], dict.fromkeys(BUTTONS, False)
    for index in range(256):
        desired = dict.fromkeys(BUTTONS, False)
        yaw = pitch = 0.0
        for start, button, dyaw, dpitch in phases:
            if start <= index < start + 16:
                desired[button] = True
                yaw, pitch = dyaw, dpitch
        edges = [{"offset_ns": 0, "button": button, "pressed": desired[button]}
                 for button in BUTTONS if held[button] != desired[button]]
        actions.append({"schema_version": 1, "contract_id": CONTRACT_ID,
                        "decision_period_ns": DECISION_PERIOD_NS,
                        "angular_delta_deg": {"yaw": yaw, "pitch": pitch},
                        "buttons_held_at_start": dict(held), "button_events": edges,
                        "channel_status": {name: {"available": True, "calibration_status": "unmeasured"}
                                           for name in CHANNELS}})
        held = desired
    return {"schema_version": 1, "producer": PROFILE, "duration_seconds": 12,
            "start_at_ns": 1_000_000_000, "actions": actions}


def validate_program(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "producer", "duration_seconds", "start_at_ns", "actions"}:
        raise ValueError("Control program requires its exact documented fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["producer"] != PROFILE:
        raise ValueError("Unsupported control program schema")
    duration, start, actions = value["duration_seconds"], value["start_at_ns"], value["actions"]
    if (type(duration) is not int or not 2 <= duration <= 30 or type(start) is not int or start < 500_000_000 or
            not isinstance(actions, list) or not 1 <= len(actions) <= 960 or
            start + len(actions) * DECISION_PERIOD_NS > duration * 1_000_000_000 - 500_000_000):
        raise ValueError("Control program needs bounded duration/decisions and initial/final idle")
    if start % DECISION_PERIOD_NS:
        raise ValueError("Control program start must lie on the exact 32 Hz decision-boundary grid")
    for action in actions:
        errors = validate_control_action(action)
        if errors:
            raise ValueError("Invalid proposed control action: " + ", ".join(errors))
    return deepcopy(value)


def project_events(program, events):
    """Encode decision boundaries for the native 64 Hz observed clock.

    A floor-ms threshold selects exactly the same native ticks as its original
    31.25ms-grid boundary. Ceil would incorrectly defer fractional boundaries.
    Finer offsets remain representable by the executor but are unsupported by
    this bounded movie bridge; no finer timing is silently rounded away.
    """
    program = validate_program(program)
    projected, provenance, boundaries = [], [], {}
    for index, item in enumerate(events):
        offset, event = item["offset_ns"], item["event"]
        if type(offset) is not int or not 0 <= offset < len(program["actions"]) * DECISION_PERIOD_NS:
            raise ValueError("Compiled event is outside its decision sequence")
        exact_ns = program["start_at_ns"] + offset
        if offset % DECISION_PERIOD_NS or exact_ns % DECISION_PERIOD_NS:
            raise ValueError("The native movie bridge supports only exact 32 Hz decision-boundary events")
        at_ms = exact_ns // 1_000_000
        if at_ms in boundaries and boundaries[at_ms] != exact_ns:
            raise ValueError("Distinct control boundaries collide in the native millisecond projection")
        boundaries[at_ms] = exact_ns
        name = f"decision-{offset // DECISION_PERIOD_NS:04d}-event-{index:04d}"
        projected.append({"id": name, "at_ms": at_ms, **event})
        provenance.append({"id": name, "decision_index": offset // DECISION_PERIOD_NS,
                           "decision_start_ns": (offset // DECISION_PERIOD_NS) * DECISION_PERIOD_NS,
                           "sequence_offset_ns": offset, "requested_capture_offset_ns": exact_ns,
                           "projected_at_ms": at_ms, "encoded_threshold_delta_ns": at_ms * 1_000_000 - exact_ns,
                           "threshold_equivalent_on_native_64hz_grid": True})
    plan = {"schema_version": 1, "producer": SYNTHETIC_PROFILE, "map": "de_dust2", "fps": 32,
            "width": 1280, "height": 720, "duration_seconds": program["duration_seconds"], "events": projected}
    return validate_synthetic_input_plan(plan), provenance
