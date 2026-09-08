"""Compile fully specified 32 Hz actions into bounded synthetic input plans.

This core does no I/O, waiting or input injection. An issued measured profile is
required, but applicability to combined movement/aim and exact consumption time
remain unverified. prepare/commit stages state; callers commit only after a
successful dispatch (or an intentional no-op). An interrupted physical backend
must release its owned controls before stopping; this core cannot undo input.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import re
from typing import Any, Iterable

from .control_contract import BUTTONS, CHANNELS, DECISION_PERIOD_NS, validate_control_action


PROFILE = "cs2-bounded-control-executor-v1"
MAX_EVENTS_PER_DECISION = 8
MAX_MOUSE_COUNTS_PER_DECISION = 200
MAX_EVENTS_PER_SECOND = 128
MAX_CONTINUOUS_HOLD_NS = 2_000_000_000
MAX_SEQUENCE_DECISIONS = 960
MAX_SEQUENCE_EVENTS = 128
MAX_SEQUENCE_MOUSE_COUNTS = 3200
EVENT_RATE_WINDOW_NS = 1_000_000_000
ROUNDING_POLICY = "cumulative_nearest_integer_half_away_from_zero"
CONTROL_MAP = {"forward": ("key", "W"), "back": ("key", "S"),
               "left": ("key", "A"), "right": ("key", "D"),
               "jump": ("key", "SPACE"), "crouch": ("key", "LCTRL"),
               "attack1": ("mouse_button", "left"), "reload": ("key", "R")}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _apply_matrix(matrix, vector):
    try:
        result = tuple(math.fsum(row[i] * vector[i] for i in range(2)) for row in matrix)
    except (OverflowError, ValueError) as error:
        raise ValueError("Requested values overflow the calibrated gain") from error
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Requested values overflow the calibrated gain")
    return result


def _matrix(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(name + " must be a finite 2x2 matrix")
    result = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(name + " must be a finite 2x2 matrix")
        if any(not _finite_number(x) for x in row):
            raise ValueError(name + " must be a finite 2x2 matrix")
        result.append(tuple(float(x) for x in row))
    return tuple(result)


def _nearest_signed(value: Fraction) -> int:
    """Round exact cumulative count arithmetic; ties are away from zero."""
    numerator, denominator = value.numerator, value.denominator
    result = (2 * abs(numerator) + denominator) // (2 * denominator)
    return result if numerator >= 0 else -result


def _readiness_limits():
    return {"training_ready": False, "live_control_ready": False,
            "exact_input_timing_verified": False,
            "combined_aim_movement_calibrated_by_compiler": False,
            "calibration_sources_reverified_by_compiler": False}


@dataclass(frozen=True)
class _State:
    decision_index: int = 0
    held: tuple[bool, ...] = (False,) * len(BUTTONS)
    pressed_since: tuple[tuple[str, int], ...] = ()
    ideal_totals: tuple[Fraction, Fraction] = (Fraction(0), Fraction(0))
    emitted_totals: tuple[int, int] = (0, 0)
    recent_event_times: tuple[int, ...] = ()


@dataclass(frozen=True)
class PreparedDecision:
    """Detached views of an immutable staged decision, never an execution permit."""

    _document_json: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._document_json)

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.to_dict()["events"]

    @property
    def decision_index(self) -> int:
        return self.to_dict()["decision_index"]


class ControlExecutor:
    """Stateful compiler consuming a proof-loader-issued measured profile.

    Declared held state is immediately before offset-zero events, as defined by
    control_contract. It must match committed state; it is not a desired state
    to silently reconcile. All three channels must be known. Mouse output is
    one impulse at offset zero, before equal-offset button events. That is an
    explicit actuator convention, not proof of within-decision consumption.
    """

    def __init__(self, calibration_profile):
        # Deferred import avoids coupling the proof loader's imports to this
        # compiler. A serialized dict or an arbitrary verified flag cannot enter.
        from .control_label_audit import MeasuredControlProfile
        if not isinstance(calibration_profile, MeasuredControlProfile):
            raise TypeError("ControlExecutor requires an issued MeasuredControlProfile")
        calibration_profile.require_checked()
        self._profile = calibration_profile
        self._forward = _matrix(calibration_profile.degrees_per_mouse_count, "degrees_per_mouse_count")
        self._inverse = _matrix(calibration_profile.mouse_counts_per_degree, "mouse_counts_per_degree")
        for first, second in ((self._forward, self._inverse), (self._inverse, self._forward)):
            for row in range(2):
                for column in range(2):
                    value = _apply_matrix(first, (second[0][column], second[1][column]))[row]
                    if not math.isfinite(value) or not math.isclose(value, float(row == column), rel_tol=1e-8, abs_tol=1e-8):
                        raise ValueError("Measured forward/inverse gain matrices do not agree")
        if not _finite_number(calibration_profile.sensitivity) or calibration_profile.sensitivity <= 0:
            raise ValueError("Measured profile needs an explicit positive sensitivity")
        if not calibration_profile.binary_profile or any(not isinstance(key, str) or not key or
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in calibration_profile.binary_profile.items()):
            raise ValueError("Measured profile needs explicit binary hashes")
        if not isinstance(calibration_profile.provenance_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", calibration_profile.provenance_sha256):
            raise ValueError("Measured profile needs a provenance digest")
        self._profile_json = _json(calibration_profile.to_dict())
        self._profile_digest = hashlib.sha256(self._profile_json.encode()).hexdigest()
        self._state = _State()
        self._pending: tuple[PreparedDecision, str, _State] | None = None

    @property
    def state(self):
        state = self._state
        return {"next_decision_index": state.decision_index,
                "next_offset_ns": state.decision_index * DECISION_PERIOD_NS,
                "held": dict(zip(BUTTONS, state.held)), "pressed_since_ns": dict(state.pressed_since),
                "ideal_mouse_count_totals": [float(x) for x in state.ideal_totals],
                "emitted_mouse_count_totals": list(state.emitted_totals),
                "mouse_count_residual": [float(x - y) for x, y in zip(state.ideal_totals, state.emitted_totals)]}

    def prepare(self, action: dict[str, Any]) -> PreparedDecision:
        if self._pending is not None:
            raise RuntimeError("Commit or discard the existing prepared decision first")
        errors = validate_control_action(action)
        if errors:
            raise ValueError("Invalid control action: " + ", ".join(errors))
        if any(action[name] is None or action["channel_status"][name]["available"] is not True for name in CHANNELS):
            raise ValueError("Execution requires available values for every control channel")
        self._profile.require_checked()
        state = self._state
        start = state.decision_index * DECISION_PERIOD_NS
        end = start + DECISION_PERIOD_NS
        held = dict(zip(BUTTONS, state.held))
        if any(action["buttons_held_at_start"][name] for name in BUTTONS if name not in CONTROL_MAP):
            raise ValueError("Active unsupported semantic control")
        if action["buttons_held_at_start"] != held:
            raise ValueError("Declared held-at-start state differs from committed controls")
        if any(event["button"] not in CONTROL_MAP for event in action["button_events"]):
            raise ValueError("Unsupported semantic button event")
        angular = action["angular_delta_deg"]
        desired = (float(angular["yaw"]), float(angular["pitch"]))
        ideal = _apply_matrix(self._inverse, desired)
        totals = tuple(old + Fraction(delta) for old, delta in zip(state.ideal_totals, ideal))
        rounded = tuple(_nearest_signed(total) for total in totals)
        emitted = tuple(new - old for new, old in zip(rounded, state.emitted_totals))
        if sum(abs(x) for x in emitted) > MAX_MOUSE_COUNTS_PER_DECISION:
            raise ValueError("Mouse counts exceed the per-decision bound; clipping is not permitted")
        events = []
        if any(emitted):
            events.append({"offset_ns": 0, "event": {"kind": "mouse_move", "dx": emitted[0], "dy": emitted[1]}})
        pressed_since = dict(state.pressed_since)
        for button_event in action["button_events"]:
            name, pressed, offset = button_event["button"], button_event["pressed"], button_event["offset_ns"]
            absolute = start + offset
            if pressed:
                pressed_since[name] = absolute
            else:
                if absolute - pressed_since[name] > MAX_CONTINUOUS_HOLD_NS:
                    raise ValueError("Continuous control hold exceeds two seconds")
                del pressed_since[name]
            held[name] = pressed
            kind, control = CONTROL_MAP[name]
            payload = {"kind": kind, "key" if kind == "key" else "button": control, "pressed": pressed}
            events.append({"offset_ns": offset, "event": payload})
        if any(end - since > MAX_CONTINUOUS_HOLD_NS for since in pressed_since.values()):
            raise ValueError("Continuous control hold would exceed two seconds before this decision ends")
        if len(events) > MAX_EVENTS_PER_DECISION:
            raise ValueError("Normalized events exceed the per-decision event bound")
        history = deque(state.recent_event_times)
        for event in events:
            absolute = start + event["offset_ns"]
            while history and history[0] <= absolute - EVENT_RATE_WINDOW_NS:
                history.popleft()
            history.append(absolute)
            if len(history) > MAX_EVENTS_PER_SECOND:
                raise ValueError("Normalized event rate exceeds the rolling one-second bound")
        recent = tuple(t for t in history if t > end - EVENT_RATE_WINDOW_NS)
        next_state = _State(state.decision_index + 1, tuple(held[name] for name in BUTTONS),
                            tuple(sorted(pressed_since.items())), totals, rounded, recent)
        predicted = _apply_matrix(self._forward, emitted)
        document = {"schema_version": 1, "profile": PROFILE,
                    "decision_index": state.decision_index, "start_offset_ns": start, "end_offset_ns": end,
                    "decision_period_ns": DECISION_PERIOD_NS, "action": action,
                    "action_sha256": _hash(action), "calibration_profile_sha256": self._profile_digest,
                    "calibration_provenance_sha256": self._profile.provenance_sha256,
                    "mouse_delivery_policy": "single_impulse_at_offset_zero_before_equal_offset_button_events",
                    "rounding_policy": ROUNDING_POLICY,
                    "ideal_mouse_counts": list(ideal), "emitted_mouse_counts": list(emitted),
                    "mouse_count_residual_before": [float(x - y) for x, y in zip(state.ideal_totals, state.emitted_totals)],
                    "mouse_count_residual_after": [float(x - y) for x, y in zip(totals, rounded)],
                    "predicted_emitted_angular_delta_deg": {"yaw": predicted[0], "pitch": predicted[1]},
                    "held_after": held, "events": events, **_readiness_limits()}
        encoded = _json(document)
        prepared = PreparedDecision(encoded)
        self._pending = prepared, encoded, next_state
        return prepared

    def _require_pending(self, prepared):
        if self._pending is None or prepared is not self._pending[0] or prepared._document_json != self._pending[1]:
            raise ValueError("Prepared decision is stale, altered or belongs to another executor")

    def commit(self, prepared: PreparedDecision):
        """Advance semantic/quantization state after success; no input is sent."""
        self._require_pending(prepared)
        self._state = self._pending[2]
        self._pending = None
        return self.state

    def discard(self, prepared: PreparedDecision):
        """Drop an unsent/failed decision without changing committed state.

        Discard does not undo a partially inserted batch. The caller must abort
        and release the physical backend after partial or unknown delivery.
        """
        self._require_pending(prepared)
        self._pending = None


def compile_sequence(actions: Iterable[dict[str, Any]], calibration_profile, *, require_released=True):
    """Pure offline compilation; absolute event offsets remain exact nanoseconds.

    No integer-millisecond projection occurs here. A worker using that schema
    must retain these originals and explicitly validate its projection. The
    offline plan is bounded to 960 decisions and 128 total normalized events.
    """
    if type(require_released) is not bool:
        raise ValueError("require_released must be boolean")
    executor = ControlExecutor(calibration_profile)
    decisions, events = [], []
    mouse_counts = 0
    for action in actions:
        if len(decisions) >= MAX_SEQUENCE_DECISIONS:
            raise ValueError("Control sequence exceeds the bounded decision count")
        prepared = executor.prepare(action)
        decision = prepared.to_dict()
        if len(events) + len(decision["events"]) > MAX_SEQUENCE_EVENTS:
            raise ValueError("Control sequence exceeds the 128-event worker plan bound")
        mouse_counts += sum(abs(event["event"]["dx"]) + abs(event["event"]["dy"])
                            for event in decision["events"] if event["event"]["kind"] == "mouse_move")
        if mouse_counts > MAX_SEQUENCE_MOUSE_COUNTS:
            raise ValueError("Control sequence exceeds the 3200-count worker plan bound")
        for index, event in enumerate(decision["events"]):
            events.append({"offset_ns": decision["start_offset_ns"] + event["offset_ns"],
                           "decision_index": decision["decision_index"], "decision_event_index": index,
                           "decision_offset_ns": event["offset_ns"], "event": event["event"]})
        decisions.append(decision)
        executor.commit(prepared)
    if not decisions:
        raise ValueError("Control sequence needs at least one decision")
    final = executor.state
    if require_released and any(final["held"].values()):
        raise ValueError("Bounded control sequence must end with every control released")
    return {"schema_version": 1, "profile": PROFILE, "decision_period_ns": DECISION_PERIOD_NS,
            "decision_count": len(decisions), "duration_ns": len(decisions) * DECISION_PERIOD_NS,
            "event_count": len(events), "event_time_basis": "offset_ns_from_sequence_start",
            "events": events, "decisions": decisions, "final_state": final,
            "total_absolute_mouse_counts": mouse_counts,
            "rounding_policy": ROUNDING_POLICY, "calibration_profile": json.loads(executor._profile_json),
            "calibration_profile_sha256": executor._profile_digest,
            "limits": {"events_per_decision": MAX_EVENTS_PER_DECISION,
                       "mouse_counts_per_decision": MAX_MOUSE_COUNTS_PER_DECISION,
                       "events_per_rolling_second": MAX_EVENTS_PER_SECOND,
                       "continuous_hold_ns": MAX_CONTINUOUS_HOLD_NS,
                       "sequence_decisions": MAX_SEQUENCE_DECISIONS, "sequence_events": MAX_SEQUENCE_EVENTS,
                       "sequence_mouse_counts": MAX_SEQUENCE_MOUSE_COUNTS},
            **_readiness_limits()}
