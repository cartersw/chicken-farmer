# Remaining work

**Updated: 2026-09-07.** Evidence: [STATUS.md](STATUS.md).

The user approved defining the controller format and building controlled Windows
calibration before expanding training work. Work has resumed; see the
[current calibration run evidence](CONTROL_CALIBRATION_RUNS.md). Remaining work
includes resolving measured original/replay phase differences and button
semantics before training labels can be promoted. Complete-command extraction
and the first original/replay diagnostic comparison now pass.

## Current controller/calibration work

- [x] Define the 32 Hz angular/held-button/event representation and missing-data masks.
- [x] Add CLI export and a candidate audit that revalidates source acceptance.
- [x] Implement protected local Windows recording and a bounded native action plan.
- [x] Inspect the installed 1.41.8.0 binaries under a separate calibration profile.
- [x] Record original images and recover all commands (008: 321 images, 711 commands).
- [x] Independently compare all original TGA pixels to native readbacks.
- [x] Measure dispatch/command associations while preserving separate clock fields.
- [x] Replay a state-derived window covering shot, crouch and turn (160 frames).
- [x] Finish original/replay state and pixel diagnostics (160 native pixel matches; sampled shot/crouch state agrees).
- [ ] Investigate the measured turn discrepancy (up to 9.375 degrees at equal numeric controller ticks) and distinguish input, state and render phases.
- [ ] Repeat phase-varied press/release/tap trials before assigning general semantic button labels.
- [ ] Add a separately measured mouse/angle actuator and weapon-selection representation.
- [ ] Resolve the remaining replay observer HUD strip before full visual training acceptance.
- [ ] Re-establish current-build competitive packet/command support, or recover the exact archived binaries needed to revalidate the historical pilot.

These calibration probes are controlled local tests. Training/evaluation corpus
work remains competitive round footage, with match/series separation. A single
probe cannot certify general subtick timing or physical input latency.

## Completed foundation

- [x] Extract/audit all three demos with preserved protobufs and immutable Parquet.
- [x] Normalize aim with explicit unavailable/reset masks.
- [x] Plan competitive renders with phase and command-coverage evidence.
- [x] Render real Windows clips with native HUD cleanup, PTS and pixel matching.
- [x] Build fractional observation intervals, diagnostic alignment and an inspector.
- [x] Verify exact native first-person identity across capture/readback boundaries.
- [x] Exercise firing, aim, movement, jumps and crouches across three players and rounds.
- [x] Implement repeatable clip validation with passed/failed/unknown evidence.
- [x] Implement temporal sample acceptance with exact references and rejection reasons.
- [x] Fix pause/ammo extraction and re-extract Dust2 without changing raw commands.

The approved clock-proof and first-subset work is implemented. The new bounded
future-command profile produces **129 accepted samples** from trial 016. The
older strict fractional-alignment campaign retains its original zero count.

## Settings protection: online check deferred by the user

- [x] Snapshot selected personal settings and create a private render profile.
- [x] Implement recovery for handled failures and a separate interrupted-run command.
- [x] Require native path/Cloud guards and move each new run's plugin out of CS2.
- [x] Build the new native DLL and test restoration with temporary fixtures.
- [x] User restores normal audio/video/HUD preferences; worker confirms CS2 is closed.
- [x] Run a short capture and verify native isolation plus exact personal-file restoration (012: 64 frames, clean exit, 39 files unchanged).
- [x] Fix the shutdown crash caused by an optional plugin command destructor (011 diagnosis, 012 verification).
- [x] Archive all 17 verified historical plugin folders out of CS2; retain their evidence.
- [x] User confirms audio, video and HUD look correct after launching normally through Steam.
- [ ] Deferred: verify Steam online/reconnection behavior when requested. This is not a prerequisite for the current offline data work.

If the startup guard finds the Cloud interface already used, stop and implement
an earlier loader; do not disable the check. The exact installed binaries passed
the local live test. The native interface guard's observed behavior does not
establish online Steam client synchronization behavior. Future game updates
require compatibility verification. See
[the rendering guide](../WINDOWS_RENDERING.md#keeping-normal-play-separate).

## 1. Completed: bounded future-command timing

- [x] Match complete native packet bytes to the original demo, including seeking and filtering.
- [x] Establish scoped enclosing server-command support and strictly future target selection.
- [x] Include both adjacent command supports when deriving normalized aim differences.
- [x] Recompute independent source evidence, paired handlers, image bounds, pixels and POV; reject edited or shifted derived labels.
- [ ] Later extension: reconstruct field-specific subtick trajectories and exact visual effect timing. These are not claimed by the first profile.

Evidence: all 160 trial-016 image bounds and the endpoint verify. See
[SYNCHRONIZATION.md](../SYNCHRONIZATION.md). Earlier strict reports remain unchanged.

## 2. First accepted subset delivered; expand coverage next

- [x] Produce accepted/rejected manifests with exact image/command references: 129 accepted, 31 rejected.
- [x] Recompute acceptance during loading and retain raw command/subtick provenance.
- [ ] Extend firing to more players/weapons; include movement starts/stops and rapid transitions.
- [ ] Regenerate Nuke/Cache state with extractor 0.1.2 when extending the corpus.
- [ ] Broaden map/build coverage before certifying a reusable rendering profile.
- [x] Keep missing-baseline, absent-message and unsupported-fraction rejection; recover additional data only with proven semantics.

Completion: a small real corpus has accepted windows, complete provenance and
meaningful negative cases. The global validator may still reject incomplete demos.

## 3. Next: build the training data interface

- [ ] Implement a tensor loader using accepted image histories and future-command targets. The first profile has no previous-action input features.
- [ ] Define variable command/subtick targets, tensor padding and label masks.
- [ ] Split deterministically by match/series; keep the current three maps together.
- [ ] Verify a complete training batch and sampling statistics.
- [ ] Collect additional matches for independent evaluation.

## 4. Train and evaluate

- [ ] Implement the handoff's small CNN/GRU behavioral-cloning baseline.
- [ ] Train aim, movement and button heads.
- [ ] Compare held-out metrics against trivial baselines and inspect failures.

Training and live control remain later milestones.
