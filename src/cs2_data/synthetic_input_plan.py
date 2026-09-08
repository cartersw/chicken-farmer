"""Bounded plans for local Windows synthetic keyboard/relative-mouse calibration.

Validation is static: it cannot establish window focus, input delivery, bindings,
mouse sensitivity or game-state effects. The adapter must check those at runtime.
These events contain no console commands and do not authorize a fallback actuator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

PROFILE = "cs2-synthetic-input-calibration-plan-v1"
KEYS = frozenset(("W", "A", "S", "D", "SPACE", "LCTRL", "LSHIFT", "R"))
MOUSE_BUTTONS = frozenset(("left", "right"))
MAX_EVENTS = 128
MAX_EVENTS_PER_BOUNDARY = 8
MAX_MOUSE_COUNTS_PER_EVENT = 200
MAX_MOUSE_COUNTS_PER_BOUNDARY = 200
MAX_MOUSE_COUNTS_PER_PLAN = 3200
MAX_HOLD_MS = 2000
INITIAL_IDLE_MS = 500
FINAL_IDLE_MS = 500
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def validate_synthetic_input_plan(value: Any) -> dict[str, Any]:
    """Return a detached plan or reject any out-of-scope or unbalanced input.

    Equal scheduled times preserve list order, including press/release batches.
    Scheduling precision does not imply equivalent OS or game processing times.
    Mouse bounds use sum(abs(dx), abs(dy)); they are counts, never degrees.
    """
    required = {"schema_version", "producer", "map", "fps", "width", "height", "duration_seconds", "events"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Synthetic input plan must contain exactly the documented schema fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["producer"] != PROFILE:
        raise ValueError("Unsupported synthetic input schema or producer")
    if value["map"] != "de_dust2" or any(type(value[key]) is not int or value[key] != expected
        for key, expected in (("fps", 32), ("width", 1280), ("height", 720))):
        raise ValueError("Synthetic calibration supports only Dust2 at 1280x720 and 32 FPS")
    duration = value["duration_seconds"]
    if type(duration) is not int or not 2 <= duration <= 30:
        raise ValueError("Synthetic calibration duration must be an integer from 2 to 30 seconds")
    events = value["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError("Synthetic calibration requires between 1 and 128 events")
    seen, held = set(), {}
    boundary_counts, boundary_mouse = Counter(), Counter()
    previous, mouse_total = -1, 0
    common = {"id", "at_ms", "kind"}
    payloads = {"key": {"key", "pressed"}, "mouse_button": {"button", "pressed"}, "mouse_move": {"dx", "dy"}}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str) or event["kind"] not in payloads:
            raise ValueError("Synthetic event kind must be key, mouse_button or mouse_move")
        kind = event["kind"]
        if set(event) != common | payloads[kind]:
            raise ValueError("Synthetic event fields must match its exact kind-specific schema")
        name, at = event["id"], event["at_ms"]
        if not isinstance(name, str) or not ID.fullmatch(name) or name in seen:
            raise ValueError("Synthetic event identifiers must be unique bounded lowercase names")
        seen.add(name)
        if type(at) is not int or not INITIAL_IDLE_MS <= at <= duration * 1000 - FINAL_IDLE_MS or at < previous:
            raise ValueError("Synthetic events need ordered integer milliseconds and 500 ms initial/final idle")
        previous = at
        boundary_counts[at] += 1
        if boundary_counts[at] > MAX_EVENTS_PER_BOUNDARY:
            raise ValueError("Synthetic scheduled boundary exceeds eight events")
        if kind == "mouse_move":
            if type(event["dx"]) is not int or type(event["dy"]) is not int:
                raise ValueError("Relative mouse dx and dy must be integer counts")
            counts = abs(event["dx"]) + abs(event["dy"])
            if not 1 <= counts <= MAX_MOUSE_COUNTS_PER_EVENT:
                raise ValueError("Relative mouse event must contain between 1 and 200 absolute counts")
            mouse_total += counts
            boundary_mouse[at] += counts
            if boundary_mouse[at] > MAX_MOUSE_COUNTS_PER_BOUNDARY or mouse_total > MAX_MOUSE_COUNTS_PER_PLAN:
                raise ValueError("Relative mouse counts exceed the boundary or whole-plan limit")
            continue
        field, allowed = ("key", KEYS) if kind == "key" else ("button", MOUSE_BUTTONS)
        control, pressed = event[field], event["pressed"]
        if not isinstance(control, str) or control not in allowed or type(pressed) is not bool:
            raise ValueError("Synthetic button event requires a whitelisted control and boolean pressed")
        identity = kind, control
        if pressed:
            if identity in held:
                raise ValueError("A held synthetic control cannot be pressed again before release")
            held[identity] = at
        else:
            if identity not in held:
                raise ValueError("A synthetic release requires a preceding press")
            if at - held.pop(identity) > MAX_HOLD_MS:
                raise ValueError("A synthetic control cannot be held longer than 2000 ms")
    if held:
        raise ValueError("Every synthetic key and mouse-button press must be released before the plan ends")
    return deepcopy(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args(argv)
    # JSON's optional NaN extension is not accepted, including in fields rejected
    # later by the exact schema. Keep malformed numeric values out at the edge.
    def invalid_number(value):
        raise ValueError("Non-finite JSON number is not allowed: " + value)
    value = json.loads(args.plan.read_text(encoding="utf-8-sig"), parse_constant=invalid_number)
    plan = validate_synthetic_input_plan(value)
    print(json.dumps({"profile": PROFILE, "plan": str(args.plan), "event_count": len(plan["events"]),
        "duration_seconds": plan["duration_seconds"], "static_plan_valid": True,
        "input_delivery_verified": False, "training_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
