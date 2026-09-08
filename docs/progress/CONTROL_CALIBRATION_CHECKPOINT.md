# Controller and calibration checkpoint

Historical pause record. Work subsequently resumed; see
[current calibration run evidence](CONTROL_CALIBRATION_RUNS.md) and
[current status](STATUS.md) for the implemented recorder and measured results.

Paused at the user's request on 2026-09-07, during the two approved next steps:
define the controller action format and build controlled Windows calibration.
This is an implementation checkpoint, not a completed calibration milestone.

## Implemented so far

- `src/cs2_data/control_contract.py`: a proposed 32 Hz action format (31.25 ms
  per decision), angular deltas in degrees, held buttons, ordered button events,
  channel availability masks, and strict format validation.
- A diagnostic candidate builder combines two consecutive future commands,
  retaining their raw command provenance and checking continuity and quality.
  It leaves uncalibrated button semantics masked and never grants training or
  live-control readiness. The existing accepted single-command profile remains
  separate.
- `tools/renderer/calibration_windows.py`: a dry-run-first Windows worker and
  bounded ten-second probe covering movement, counterstrafing, a shot, crouching,
  turning and strafing. The worker reuses the renderer's settings snapshot,
  cloned profile, native isolation guard, restoration and owned-plugin cleanup.
- Focused synthetic tests accompany both components. Native calibration support
  is deliberately required by a DLL marker; the existing DLL lacks that support.

Verification at pause: **94 focused tests passed** (60 controller tests and 34
Windows calibration-worker tests). These include malformed control plans and
simulated launch failure, crash, timeout and interruption recovery. The full
project suite was not rerun at this pause. Component details are in
[the controller guide](../CONTROL_CONTRACT.md) and
[the calibration guide](../CALIBRATION.md).

## Not completed or measured

- No native calibration implementation, DLL rebuild, or controlled game session
  occurred in this work. No calibration frames or new demos were recorded.
- The action module has not been wired into the main CLI or audited against the
  real accepted pilot. Its unit tests do not establish calibrated labels.
- The calibration analyzer was not started. Input consumption time, button-plane
  meanings, dispatch-to-effect timing and original/replay agreement remain
  unmeasured.
- Initial probes will dispatch engine console controls. That can measure a
  useful part of the pipeline, but does not establish physical keyboard/mouse
  sampling or device latency.

## Resume here

1. Integrate the action schema/candidate audit into the CLI and inspect candidates
   from the existing Dust2 data without changing earlier acceptance artifacts.
2. Implement the opt-in native calibration mode in the Windows plugin. Validate
   a bounded plan, require an alive local player in a private local game, record
   command dispatch and local state, and capture original frames plus a demo.
   Add the worker's `CHICKEN_CONTROLLED_CALIBRATION_V1` marker only when this mode
   actually exists.
3. Finalize the worker/native interface: `-chicken-calibration-plan` and
   `-chicken-calibration-ledger`, with readiness, dispatch and completion events.
   Keep simulation-clock scheduling separate from QPC wall timestamps; movie
   capture can advance simulated time differently from wall time.
4. Build and verify the plugin, then run the protected ten-second probe. Confirm
   command availability, local-demo recording and presence of usable recorded
   commands before treating the recording as useful calibration evidence.
5. Analyze dispatch/state/frame evidence, replay the controlled demo and compare
   it with the original capture. Record measured errors and unknowns per action;
   calibrate button/angle labels only where the evidence supports them.

## Game and data state at pause

CS2 was not launched, and no game-installation files, normal game settings,
Steam mode or Steam Cloud preferences were changed during this work. CS2 was
closed at the checkpoint. The existing renderer DLL was not rebuilt or staged.

The previous pilot remains 129 accepted and 31 rejected future-command samples
from trial 016. No new samples were accepted and no model was trained. Online
Steam Cloud testing remains deferred at the user's request.

Changes are saved in the working tree; this checkpoint is not a Git commit.
