# Remaining work

**Updated: 2026-09-08.** Evidence: [STATUS.md](STATUS.md).

Action-aware selection, longer protected captures, bounded source reuse and
faster HUD review are implemented and verified. The [current milestone](ACTION_COVERAGE_AND_THROUGHPUT.md)
contains **1,692 accepted samples** with positive reload/control fields.
Next priorities are additional independent competitive series for validation/test,
broader weapon/action coverage, then a small CNN/GRU baseline and masked losses.
All current clips belong to one BO3; exact within-tick events remain masked.
No model training has run.

The approved first turning/button investigation is complete. See
[turn and button results](TURN_AND_BUTTON_CALIBRATION.md): continuous turns
support a frozen interpolation candidate, while rapid-input confirmation
falsifies complete event reconstruction from exported records. Startup gaps
occurred before the measured captures. The user clarified that the final agent
must synthesize keyboard/mouse input. The adapter and first protected synthetic
calibration are now complete: [mouse and keyboard results](SYNTHETIC_INPUT_CALIBRATION.md).

The approved executor and scoped label work is now implemented. The combined
recording verifies angular/button/movement response but fails raw mouse-count
sum equality; that stricter result remains incomplete. The independent label
audit passes all twelve mouse and ten keyboard checks. See
[the executor milestone](CONTROL_EXECUTION_AND_LABELS.md).

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
- [x] Investigate the turn discrepancy with an independently tested, frozen interpolation candidate (22 ordinary turns pass; large angle step remains an outlier).
- [x] Repeat hold/tap/overlap and three/four-transition probes; retain the failed exact-event hypothesis and observed held-state/net-change support.
- [x] Add measured startup cadence and continuous settling, independently checked through capture readiness.
- [x] Implement and measure synthetic keyboard presses/releases and relative mouse movement through the protected local workflow; retain sent-event ground truth (770 images, 1,680 full commands, mouse held-out and ten keyboard/button phase checks pass).
- [x] Correct post-insertion scheduling, replace coarse Python polling with a scoped high-resolution wait timer, and preserve both failed scheduling runs and cleanup evidence.
- [x] Connect the 32 Hz angular/held-button/event contract to the adapter, including scoped gain conversion, fractional mouse-count accumulation and explicit unsupported-control handling.
- [x] Define scoped semantic targets using confirmed fields and explicit masks for unrecoverable event count/timing; validate against the synthetic-input experiment.
- [x] Record a fixed combined-aim/WASD/crouch program and independently check compilation, insertion receipts, effective settings, pixels and response; retain the raw-count mismatch as an incomplete overall audit.
- [x] Bound the first combined-input raw mouse-count discrepancy: wider neighborhoods retain the same missing first-impulse counts, while recorded view/history and native angles retain their angular effect. Keep the frozen equality failure and angular label targets.
- [ ] Investigate the underlying raw-count aggregation rule only if pursuing exact mouse-count/event reconstruction; the current scoped angular labels do not assume count conservation.
- [ ] Extend synthetic response coverage to walk/secondary attack and larger turns. The first combined test adds small mixed-axis and repeated mouse input with movement; it does not establish general response or exact consumption timing.
- [ ] Extend actual dispatch phases/cadence beyond the current 32 FPS console loop; off-grid scheduled milliseconds alone are not phase variation.
- [ ] Investigate large angle cuts/recoil separately; do not apply a universal frame shift.
- [ ] Define and measure weapon-selection controls.
- [x] Remove the observer strip through a private resource archive and review all 160 original current-pilot images while preserving the player HUD.
- [x] Re-establish current-build competitive packet/command support and recover the exact original inspected binaries into the workspace.
- [x] Establish original 14178 competitive source button semantics independently, with per-field and per-command masks.
- [x] Extend full source eligibility reconstruction and visual review beyond the pinned Dust2 pilot; support current-profile packet prefixes beyond tick 20,000.
- [x] Add bounded resumable processing across rounds/players/maps while retaining settings and acceptance guards.
- [x] Reduce repeated prefix scanning with verified bounded compression, halve launches for ten-second collections, and retain exact source checks.
- [ ] Profile full batch acceptance before large ingestion; packet-scan and HUD speedups do not establish equal gains for complete verification.
- [x] Review four additional ten-second captures with one-pass sheets and a local index; every original image still needs actual review.
- [ ] Resolve checkpoint duplicates only with complete byte-bound clock/packet evidence; preserve the eight current affected-history rejections.

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

The approved clock-proof and first-subset work is implemented. The historical bounded
future-command profile produces **129 accepted samples** from trial 016. The
older strict fractional-alignment campaign retains its original zero count.
The competitive control profile separately accepts **153 samples** from
the clean 006 capture within the current larger corpus; historical publications remain intact.

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

- [x] Produce accepted/rejected manifests with exact image/command references: current seven-clip control subset 1,692 accepted/68 rejected; historical single-command pilot 129/31.
- [x] Recompute acceptance during loading and retain raw command/subtick provenance.
- [x] Add balanced ordinary/reload/sustained-fire clips across four new rounds/POVs; retain movement/crouch and unresolved jump activity.
- [ ] Expand weapon diversity, rare held/transition targets and ordinary play across independent series; do not equate activity bits with exact presses.
- [x] Regenerate Nuke state with extractor 0.1.2 and independently verify the full source.
- [ ] Regenerate Cache state with extractor 0.1.2 when extending the corpus.
- [ ] Broaden map/build coverage before certifying a reusable rendering profile.
- [x] Keep missing-baseline, absent-message and unsupported-fraction rejection; recover additional data only with proven semantics.

Completion: a small real corpus has accepted windows, complete provenance and
meaningful negative cases. The global validator may still reject incomplete demos.

## 3. Training data interface

- [x] Implement a tensor loader using eight accepted RGB images and future-command targets, with no previous-action input features.
- [x] Define fixed two-command 32 Hz targets and validity masks; preserve variable raw provenance separately and leave exact event channels unavailable.
- [x] Split deterministically by match/series; keep the current three maps together in training.
- [x] Materialize and independently reload a real CPU batch: four samples, `[4,8,3,180,320]` RGB, 8 valid angular fields, 320 valid button fields including 24 positives; 1,692/0/0 series split.
- [ ] Collect additional independent series for validation/test; both are currently empty.

## 4. Train and evaluate

- [ ] Implement the handoff's small CNN/GRU behavioral-cloning baseline.
- [ ] Train aim, movement and button heads.
- [ ] Compare held-out metrics against trivial baselines and inspect failures.

Training and live control remain later milestones.
