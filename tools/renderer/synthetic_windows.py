"""Protected local CS2 SendInput calibration; dry-run unless --execute is given.

The native recorder supplies verified local state and a simulation clock. This
external worker injects scan-code keys/buttons and relative mouse counts through
Windows. API insertion, game response and exact input timing are distinct facts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import calibration_windows as calibration
from cs2_data.synthetic_input_plan import PROFILE, validate_synthetic_input_plan as validate_plan

CONTROL_SOURCE = "external_windows_SendInput"
LEDGER_PROFILE = "cs2-windows-synthetic-input-ledger-v1"
MARKER = "CHICKEN_SYNTHETIC_INPUT_BRIDGE_V1"
MAX_FRAME_AGE_SECONDS = 0.250
MAX_SCHEDULE_LATENESS_MS = 125
MAX_WALL_HOLD_SECONDS = 2.5


def native_plan(plan, out, run_id):
    plan = validate_plan(plan)
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid owned synthetic run identifier")
    out = out.resolve()
    if any(character in str(out) for character in '\";\r\n'):
        raise ValueError("Synthetic paths cannot contain console delimiters")
    return {**plan, "movie_name": "calibration-" + run_id, "demo_path": (out / "controlled.dem").as_posix()}


def launch_arguments(game, out, plan):
    launch = calibration.launch_arguments(game, out, plan)
    launch.insert(1, "-chicken-synthetic-input")
    return launch


def require_plugin(plugin):
    if MARKER.encode() not in plugin.read_bytes():
        raise ValueError("Rebuild the protected DLL with the synthetic input bridge before this recording")


def payload(event):
    return {key: value for key, value in event.items() if key not in ("id", "at_ms")}


class NativeBridge:
    """Read only complete native records; a stale or lost scope blocks input.

    Scheduling uses observed controller ticks, not wall seconds: host_framerate
    changes simulation time independently of disk/GPU/wall speed. QPC is used
    only to bound observation age and retain the actual Windows API brackets.
    """

    def __init__(self, path, plan, pid, clock, frequency):
        self.path, self.plan, self.pid = path, plan, pid
        self.clock, self.frequency = clock, frequency
        self.rows, self.pending = [], b""
        self.header = self.ready = self.frame = self.bridge_ready = None
        self.stopped = False
        self.handle = None
        self.total = 0

    def read_more(self):
        if self.handle is None:
            if not self.path.exists():
                return
            self.handle = self.path.open("rb")
        data = self.handle.read(4 * 1024**2)
        self.total += len(data)
        if self.total > 256 * 1024**2:
            raise ValueError("Native bridge exceeded its ledger budget")
        self.pending += data
        lines = self.pending.split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > 1024**2:
            raise ValueError("Oversized incomplete native bridge record")
        for line in lines:
            if len(line) > 1024**2 or len(self.rows) >= 100000:
                raise ValueError("Native bridge record budget exceeded")
            row = json.loads(line, object_pairs_hook=calibration._json_object,
                             parse_float=calibration._finite_float,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite native bridge value")))
            if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] != 1:
                raise ValueError("Invalid native bridge record")
            self.accept(row)

    def accept(self, row):
        kind = row.get("event")
        if not isinstance(kind, str):
            raise ValueError("Native bridge record lacks event type")
        if kind in ("calibration_failed", "calibration_error", "error"):
            raise ValueError("Native bridge failed: " + str(row.get("reason", row)))
        if kind == "header":
            if (self.rows or self.header is not None or row.get("plan") != self.plan or
                    row.get("producer") != PROFILE or row.get("control_source") != CONTROL_SOURCE or
                    row.get("synthetic_input_marker") != MARKER or row.get("owned_process_id") != self.pid or
                    type(row.get("qpc_frequency")) is not int or row["qpc_frequency"] != self.frequency or
                    row.get("native_profile") != "cs2-14180-calibration-v1" or
                    row.get("engine_sha256") != calibration.BINARY_PROFILE["bin/win64/engine2.dll"]):
                raise ValueError("Native bridge header does not identify this owned run")
            self.header = row
        elif self.header is None:
            raise ValueError("Native bridge record precedes header")
        if kind == "calibration_ready":
            if (self.ready is not None or row.get("synthetic_input_bridge_verified") is not True or
                    row.get("local_connection_verified") is not True or row.get("local_connection") != "loopback" or
                    row.get("connection_evidence", {}).get("verified") is not True or row.get("map") != "de_dust2" or
                    row.get("clock_basis") != "local_controller_tick_base_64hz" or
                    type(row.get("start_tick_base")) is not int or not self.local_valid(row.get("local_player")) or
                    not calibration.first_person_ready(row.get("local_player"))):
                raise ValueError("Native bridge did not prove local first-person readiness")
            calibration.verify_startup_settle(self.rows + [row], self.header, row, required=True)
            self.ready = row
        if kind == "synthetic_bridge_ready":
            if (self.ready is None or self.bridge_ready is not None or row.get("marker") != MARKER or
                    row.get("owned_process_id") != self.pid or row.get("local_connection_verified") is not True or
                    row.get("start_tick_base") != self.ready["start_tick_base"]):
                raise ValueError("Synthetic bridge readiness identity mismatch")
            self.bridge_ready = row
        if kind == "frame_sample":
            local = row.get("local_player")
            if (self.ready is None or self.bridge_ready is None or self.stopped or
                    row.get("owned_process_id") != self.pid or row.get("local_connection_verified") is not True or
                    not self.local_valid(local) or local.get("pawn_handle") != self.ready["local_player"]["pawn_handle"] or
                    type(row.get("qpc")) is not int or row["qpc"] < self.ready["qpc"] or
                    row.get("elapsed_ms") != (local["controller_tick_base"] - self.ready["start_tick_base"]) * 15.625 or
                    row["elapsed_ms"] < 0 or self.frame is not None and
                    (row["qpc"] < self.frame["qpc"] or row["elapsed_ms"] < self.frame["elapsed_ms"])):
                raise ValueError("Native frame scope, identity or scheduling clock changed")
            self.frame = row
        if kind in ("recording_stop_dispatched", "calibration_complete"):
            self.stopped = True
        self.rows.append(row)

    @staticmethod
    def local_valid(local):
        if not isinstance(local, dict):
            return False
        observer_clear = (local.get("observer_services_present") is False and "observer_mode" in local and
                          local["observer_mode"] is None or local.get("observer_services_present") is True and
                          type(local.get("observer_mode")) is int and local["observer_mode"] == 0)
        # Stationary eye-origin proximity is verified at readiness. During
        # movement the predicted camera can lead the pawn; retain reciprocal
        # local identity and observer/override guards without that static test.
        return (local.get("status") == "observed" and
                type(local.get("life_state")) is int and local["life_state"] == 0 and
                type(local.get("team")) is int and local["team"] in (2, 3) and
                type(local.get("controller_tick_base")) is int and
                type(local.get("pawn_handle")) is int and
                type(local.get("pawn_state", {}).get("health")) is int and local["pawn_state"]["health"] > 0 and
                local.get("observer_services_pointer_observed") is True and observer_clear and
                type(local.get("camera_view_entity_handle")) is int and local["camera_view_entity_handle"] == 2**32 - 1)

    def scope_guard(self):
        self.read_more()
        now = self.clock()
        if self.frame is None or self.stopped:
            raise ValueError("Synthetic input has no active native local frame")
        age = (now - self.frame["qpc"]) / self.frequency
        if not 0 <= age <= MAX_FRAME_AGE_SECONDS:
            raise ValueError("Synthetic input native local-state observation is stale")
        return {"verified": True, "run_id": self.plan["movie_name"].removeprefix("calibration-"),
                "owned_process_id": self.pid, "local_connection": "loopback", "local_connection_verified": True,
                "frame_qpc": self.frame["qpc"], "checked_qpc": now, "frame_age_seconds": age,
                "elapsed_ms": self.frame["elapsed_ms"], "pawn_handle": self.frame["local_player"]["pawn_handle"],
                "controller_tick_base": self.frame["local_player"]["controller_tick_base"]}

    def close(self):
        if self.handle:
            self.handle.close()


def due_batch(events, next_index, frame):
    if next_index >= len(events) or frame["elapsed_ms"] < events[next_index]["at_ms"]:
        return []
    at = events[next_index]["at_ms"]
    if frame["elapsed_ms"] - at > MAX_SCHEDULE_LATENESS_MS:
        raise ValueError("Synthetic event exceeded the scheduling lateness bound")
    end = next_index + 1
    while end < len(events) and events[end]["at_ms"] == at:
        end += 1
    if end < len(events) and events[end]["at_ms"] <= frame["elapsed_ms"]:
        raise ValueError("Delayed synthetic boundaries would collapse into one dispatch; aborting")
    return events[next_index:end]


def budget_check_window(frame, events, next_index, held):
    """Keep synchronous directory/stat work away from short input edges."""
    return (frame is None or next_index >= len(events) or
            not held and events[next_index]["at_ms"] - frame["elapsed_ms"] > 250)


def wait_for_calibration(process, roots, plan, out, timeout, *, bridge_factory=None):
    import win32_input
    import win32_wait
    run_id = plan["movie_name"].removeprefix("calibration-")
    clock, frequency = win32_input.query_performance_counter, win32_input.query_performance_frequency()
    bridge = (bridge_factory or NativeBridge)(out / "calibration_ledger.jsonl", plan, process.pid, clock, frequency)
    adapter, complete, next_index = None, False, 0
    held_since = {}
    pending_dispatch = None
    failure = None
    poll_timer = None
    previous_poll = started_poll = time.monotonic()
    maximum_poll_gap = 0.0
    def guarded_scope():
        nonlocal pending_dispatch
        scope = bridge.scope_guard()
        if pending_dispatch is not None:
            index, expected_batch = pending_dispatch
            # The adapter calls this both before and after SendInput. Enforce
            # schedule eligibility before insertion; natural time advancement
            # during the API call must not misclassify an already sent batch as
            # an unsent batch collapsing with its following release. Both calls
            # still verify fresh local scope and owned OS foreground identity.
            pending_dispatch = None
            if due_batch(plan["events"], index, bridge.frame) != expected_batch:
                raise ValueError("Synthetic dispatch no longer matches the current native scheduling boundary")
        return scope
    started = last_budget = last_notice = time.monotonic()
    expected_executable = Path(process.args[0]).resolve()
    with (out / "input_ledger.jsonl").open("x", encoding="utf-8", newline="\n") as log:
        def write(row):
            log.write(json.dumps({"schema_version": 1, **row}, allow_nan=False) + "\n")
            log.flush()
        source_plan = {key: value for key, value in plan.items() if key not in ("movie_name", "demo_path")}
        write({"event": "header", "profile": LEDGER_PROFILE, "run_id": run_id, "pid": process.pid,
               "plan_sha256": calibration.replay.sha256_file(out / "native-plan.json"), "source_plan": source_plan,
               "qpc_frequency": frequency, "qpc": clock(), "control_source": CONTROL_SOURCE,
               "adapter_source_sha256": calibration.replay.sha256_file(Path(win32_input.__file__)),
               "poll_timer_source_sha256": calibration.replay.sha256_file(Path(win32_wait.__file__)),
               "worker_source_sha256": calibration.replay.sha256_file(Path(__file__)),
               "schedule_basis": "observed_native_local_controller_tick_base_64hz",
               "maximum_frame_age_seconds": MAX_FRAME_AGE_SECONDS,
               "maximum_schedule_lateness_ms": MAX_SCHEDULE_LATENESS_MS,
               "maximum_wall_hold_seconds": MAX_WALL_HOLD_SECONDS,
               "os_insertion_is_game_consumption": False, "training_ready": False})
        try:
            poll_timer = win32_wait.WindowsPollTimer()
            while process.poll() is None:
                now = time.monotonic()
                maximum_poll_gap = max(maximum_poll_gap, now - previous_poll)
                previous_poll = now
                if now - started > timeout:
                    raise TimeoutError("Owned synthetic calibration exceeded its wall timeout")
                bridge.read_more()
                if any((clock() - pressed_at) / frequency > MAX_WALL_HOLD_SECONDS for pressed_at in held_since.values()):
                    raise TimeoutError("Synthetic held control exceeded its wall-clock release bound")
                if not complete and bridge.frame is not None:
                    bridge.scope_guard()
                    if adapter is None:
                        adapter = win32_input.WindowsInputAdapter(process.pid, run_id, guarded_scope,
                                                                 expected_executable=expected_executable)
                        write({"event": "adapter_ready", "qpc": clock(), "pid": process.pid,
                               "receipt": adapter.preflight_receipt})
                    frame = bridge.frame
                    batch = due_batch(plan["events"], next_index, frame)
                    if batch:
                        pending_dispatch = next_index, batch
                        try:
                            receipt = adapter.send([payload(event) for event in batch])
                        finally:
                            pending_dispatch = None
                        dispatch_scope = receipt["guard_before"]["scope"]
                        write({"event": "injection_batch", "events": batch, "scheduled_at_ms": batch[0]["at_ms"],
                               "actual_elapsed_ms": dispatch_scope["elapsed_ms"],
                               "native_frame_qpc": dispatch_scope["frame_qpc"], "receipt": receipt})
                        next_index += len(batch)
                        for event in batch:
                            if event["kind"] != "mouse_move":
                                control = event["kind"], event.get("key", event.get("button"))
                                if event["pressed"]:
                                    held_since[control] = receipt["qpc_before"]
                                else:
                                    held_since.pop(control, None)
                    if next_index == len(plan["events"]):
                        receipt = adapter.release_all("plan_complete")
                        write({"event": "cleanup", "reason": "plan_complete", "receipt": receipt})
                        if receipt.get("success") is not True:
                            raise RuntimeError("Synthetic input cleanup was not confirmed")
                        write({"event": "input_complete", "qpc": clock(), "events_inserted": next_index,
                               "game_consumption_verified": False, "training_ready": False})
                        complete = True
                if now - last_budget >= 0.5 and budget_check_window(bridge.frame, plan["events"], next_index, held_since):
                    budget_started = time.monotonic()
                    count, size = calibration.check_capture_budget(roots, plan, out)
                    last_budget = time.monotonic()
                    write({"event": "worker_poll_sample", "qpc": clock(), "maximum_poll_gap_seconds": maximum_poll_gap,
                           "window_wall_seconds": last_budget - started_poll,
                           "budget_check_wall_seconds": last_budget - budget_started,
                           "completed_events": next_index, "frame_count": count})
                    maximum_poll_gap = 0.0
                    started_poll = last_budget
                    if now - last_notice >= 15:
                        print(f"Synthetic calibration: {now-started:.0f}s, {count} frames, "
                              f"{next_index}/{len(plan['events'])} events inserted, {size/1024**2:.1f} MiB", flush=True)
                        last_notice = now
                poll_timer.wait(milliseconds=2)
            bridge.read_more()
            if not complete:
                raise RuntimeError("Game exited before the synthetic schedule and cleanup completed")
            return process.returncode
        except BaseException as error:
            failure = error
            try:
                write({"event": "input_failed", "qpc": clock(), "error": str(error),
                       "receipt": getattr(error, "receipt", None), "cleanup_receipt": getattr(error, "cleanup_receipt", None)})
            except BaseException as logging_error:
                error.input_logging_error = str(logging_error)
            raise
        finally:
            cleanup_failure = None
            try:
                if adapter is not None:
                    receipt = None
                    try:
                        receipt = adapter.release_all("worker_finally")
                    except BaseException as error:
                        cleanup_failure = error
                    finally:
                        # Closing (including a release retry) must not depend on
                        # the input ledger still being writable.
                        try:
                            closed = adapter.close()
                        except BaseException as error:
                            cleanup_failure = cleanup_failure or error
                            closed = getattr(error, "cleanup_receipt", None)
                    try:
                        write({"event": "cleanup", "reason": "worker_finally", "receipt": receipt,
                               "close_receipt": closed})
                    except BaseException as error:
                        cleanup_failure = cleanup_failure or error
            finally:
                try:
                    if poll_timer is not None:
                        poll_timer.close()
                except BaseException as error:
                    cleanup_failure = cleanup_failure or error
                finally:
                    bridge.close()
            if cleanup_failure is not None:
                if failure is None:
                    raise cleanup_failure
                failure.input_cleanup_error = str(cleanup_failure)


verify_native_completion = calibration.verify_native_completion


def verify_input_completion(out, plan, pid):
    path = out / "input_ledger.jsonl"
    if not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024**2:
        raise ValueError("Missing or oversized synthetic input ledger")
    digest = calibration.replay.sha256_file(path)
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line, object_pairs_hook=calibration._json_object) for line in handle]
    header = rows[0]
    if (header.get("event") != "header" or header.get("profile") != LEDGER_PROFILE or
            header.get("plan_sha256") != calibration.replay.sha256_file(out / "native-plan.json") or
            header.get("pid") != pid or header.get("run_id") != plan["movie_name"].removeprefix("calibration-") or
            any(row.get("event") == "input_failed" for row in rows)):
        raise ValueError("Synthetic input ledger has wrong provenance or a reported failure")
    complete = [row for row in rows if row.get("event") == "input_complete"]
    batches = [row for row in rows if row.get("event") == "injection_batch"]
    if (len(complete) != 1 or complete[0].get("events_inserted") != len(plan["events"]) or
            [event for row in batches for event in row.get("events", [])] != plan["events"]):
        raise ValueError("Synthetic inserted events do not match the immutable plan")
    previous = -1
    for row in batches:
        receipt = row.get("receipt", {})
        if (receipt.get("success") is not True or receipt.get("status") != "inserted" or
                receipt.get("requested_count") != len(row["events"]) or receipt.get("inserted_count") != len(row["events"]) or
                receipt.get("events") != [payload(event) for event in row["events"]] or
                receipt.get("qpc_frequency") != header["qpc_frequency"] or
                not previous <= row["native_frame_qpc"] <= receipt["qpc_before"] <= receipt["qpc_after"] <= complete[0]["qpc"]):
            raise ValueError("Synthetic batch insertion/clock receipt is incomplete")
        for guard in ("guard_before", "guard_after"):
            evidence = receipt.get(guard, {})
            if (evidence.get("scope", {}).get("verified") is not True or
                    evidence.get("os", {}).get("foreground_pid") != pid or
                    evidence.get("os", {}).get("process_identity_verified") is not True or
                    evidence.get("os", {}).get("foreground_matches") is not True):
                raise ValueError("Synthetic batch lacks owned foreground/local scope evidence")
        previous = receipt["qpc_after"]
    cleanups = [row for row in rows if row.get("event") == "cleanup"]
    if not cleanups or any(row.get("receipt", {}).get("success") is not True for row in cleanups):
        raise ValueError("Synthetic cleanup is incomplete")
    if calibration.replay.sha256_file(path) != digest:
        raise ValueError("Synthetic ledger changed during verification")
    return {"input_ledger": {"path": path.name, "sha256": digest},
            "synthetic_input_completion": {"status": "planned_events_inserted_and_released", "event_count": len(plan["events"]),
                "batch_count": len(batches), "game_consumption_verified": False, "exact_input_timing_verified": False}}


def main(argv=None):
    parser = calibration.argument_parser()
    parser.description = __doc__
    args = parser.parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 1800:
            raise ValueError("Timeout must be between 10 and 1800 seconds")
        plan = validate_plan(calibration.replay.read_json(args.plan or ROOT / "plans/synthetic-mouse-probe-012-v1.json"))
        out, game = args.output.resolve(), args.game_dir.resolve()
        if out.exists() or out.is_relative_to(game) or game.is_relative_to(out):
            raise ValueError("Synthetic calibration needs a fresh output directory separate from the game")
        if not args.execute:
            effective = native_plan(plan, out, "0" * 32)
            print(json.dumps({"status": "planned", "native_plan": effective, "control_source": CONTROL_SOURCE,
                              "launch_arguments": launch_arguments(game, out, effective),
                              "training_ready": False, "game_consumption_verified": False}, indent=2))
        else:
            report = calibration.run_calibration(args, plan, backend=sys.modules[__name__])
            print(json.dumps({"manifest": str(out / "calibration.json"),
                              **{key: report[key] for key in ("status", "num_frames", "demo", "synthetic_input_completion",
                                  "settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")}}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Synthetic calibration error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
