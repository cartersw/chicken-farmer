# Project progress

This folder tracks completed work, verification, blockers, and the remaining CS2 vision imitation-learning milestones.

**Recording sessions:** [one session per demo/player](../RECORDING_SESSION_DESIGN.md)
is now the default queue workflow. CS2 stays open through forward playback;
training-file splits do not restart recording. Native endpoints, shared timing
evidence, whole-session disk estimates and capture-first recovery are implemented.
Live verification is recorded under `data/validation/recording-session-001` and
`data/validation/recording-session-full-001`; see the
[implementation and test record](RECORDING_SESSION_2026_09_08.md).

**Parallel processing:** After all recording closes, the Demo queue uses 1-4
validation processes (default 2) and ordered lossless compression. Backlog limits,
graceful draining and capture reuse on resume remain implemented. See
[full-demo processing](../FULL_DEMO_PROCESSING.md#queue-and-recovery).

**New full-demo queue:** [Demo Processor](../DESKTOP_APP.md) now queues a complete
demo for one named player, preprocesses all eligible rounds, captures them
automatically and publishes lossless 640x360 RGB8 training/evidence archives.
Eight-frame histories at 32 FPS remain the baseline. Coverage reports, bounded
rolling RGB decompression, persistent stop/resume and verified raw-work cleanup
are implemented. See [full-demo processing](../FULL_DEMO_PROCESSING.md).
Legacy sample durations remain 5, 10 or 20 seconds; those controls do not limit
full-demo processing. The complete trainer remains pending.

**Current policy:** the user approved the current capture HUD setup after
viewing the nine clips. Routine processing uses that fixed setup without
recurring manual review, automated overlay detectors or spot checks. It records
the setup assumption without claiming complete-image inspection, and retains
automatic timing, source, POV and original-pixel integrity checks. See
[competitive acceptance](../COMPETITIVE_ACCEPTANCE.md).
The [policy verification](../../data/validation/trusted-hud-001/nine-capture-policy.json)
passes for all nine captures without image reads or sheet generation;
[243 core tests pass](../../data/validation/trusted-hud-001/core-tests.xml).

**Current recording result:** the [round progression trial](ROUND_PROGRESSION_TRIAL.md)
recorded ordinary Ckanic/Dust2 progression in order with a twenty-second maximum,
explicit interval exclusions, resource budgets, sequential protected resume and
an ordered video/report page. The user stopped after **nine captures: 166.75
seconds and 5,336 images**, approximately 28.09 GB retained and 34 minutes 41.8
seconds of active processing. Cleanup passes for all nine captures. The final
22 planned seconds are intentionally unrecorded, so two complete rounds are
not claimed. Their acceptance evaluation remains pending; one source-clock exception
affects eight prospective histories. **123 focused tests pass.** See the
[round collection workflow](../ROUND_COLLECTION.md).

**Previous milestone:** [action coverage and throughput](ACTION_COVERAGE_AND_THROUGHPUT.md)
adds four ten-second protected captures, balanced action/ordinary selection,
faster source scanning and HUD review. Its last publication had **1,692 accepted samples**
across seven refreshed clips, including positive reload/control fields. Those
partitions need fresh numerical publications under source-proof v3 before
current loader use; the HUD policy change has not reverified them. A real
four-sample tensor batch reloads exactly; **2,030 tests pass**. Settings and
installed game/HUD bytes are unchanged. All footage belongs to one BO3;
validation/test remain empty and no model was trained.

**Larger collection under consideration:** [Ckanic's full Dust2 POV](FULL_PLAYER_DEMO_PLAN.md).
The source contains about 31.5 minutes of eligible alive play. The feasibility
plan records measured storage costs and the remaining complete-match queue and
history work. The first bounded trial stopped at the user's requested nine
clips; a full-demo run has not been implemented or captured.

**Previous milestone:** [competitive buttons and batch processing](COMPETITIVE_EXPANSION.md)
established the original-source button proof, full reconstruction and first
three-clip corpus. Its published 456-sample result remains historical.

**Previous work:** [current competitive rendering and training data](COMPETITIVE_TRAINING_PILOT.md)
provided 160 reviewed original frames and 153 accepted eight-image samples.
The spectator strip is removed while the player HUD remains. Angular targets
are verified within the bounded future-command contract; competitive button
semantics and exact event timing remained masked in that publication. See the
[acceptance guide](../COMPETITIVE_ACCEPTANCE.md) and [tensor interface](../TRAINING_DATASET.md).
The real CPU tensor batch passes independent reload, with two eight-frame RGB
samples, four valid angular fields and all button fields masked. That milestone's
suite passed **1,698 tests**. All six protected attempts preserved
39 selected personal files and removed staging; final installed game/HUD hashes
match and CS2 is closed. No model has been trained.

**Previous work:** the [32 Hz executor and scoped labels](CONTROL_EXECUTION_AND_LABELS.md)
are implemented. The combined local recording shows the expected angular and
movement/crouch responses, with a raw mouse-count discrepancy still unresolved.
The independent label audit passes all twelve mouse and ten keyboard checks.
See [the implementation guide](../CONTROL_EXECUTION.md).

The Windows synthetic keyboard/mouse adapter and first protected
response calibration are complete. Mouse fit/held-out checks and all ten
keyboard/button response phases pass. See [synthetic input results](SYNTHETIC_INPUT_CALIBRATION.md),
[turn and button results](TURN_AND_BUTTON_CALIBRATION.md) and
[earlier calibration run evidence](CONTROL_CALIBRATION_RUNS.md).
The earlier
[pause checkpoint](CONTROL_CALIBRATION_CHECKPOINT.md) is historical.

**Last updated: 2026-09-08. Current policy: trust the user-approved capture HUD setup.**

The earlier execution/label milestone passed **1,455 Python tests**. Its combined run has 385 verified
images, 840 complete commands and 82 Windows input events. Its two preflight
failures inserted no input. All three attempts preserved the 39 selected
personal files and removed staging. The older two completed synthetic recordings
contain 770 verified frames and 1,680 full commands. Two aborted scheduling
attempts are retained. All four attempts preserved the 39 selected personal
files and removed their renderer staging.

The historical 129-sample partition remains intact. Its exact original inspected
binaries have now been recovered into the workspace for the new source proof.
The older loader still expects its historical installed-binary path; the new
competitive path explicitly uses the recovered archive. Neither historical
results nor local calibration diagnostics are promoted to the new profile.

Trial 016 verifies complete packet information bounds, exact pixel matching and
first-person identity. Its accepted samples contain eight images and one future
command, with both commands contributing to aim normalization kept strictly
after the observation bound. The 31 rejected candidates retain explicit reasons.
See [the synchronization guide](../SYNCHRONIZATION.md) and [current status](STATUS.md).

The protected renderer produced a two-second clip, exited cleanly, and left all
39 selected personal settings files unchanged. An initial shutdown bug was fixed,
and all 17 historical renderer staging folders were archived out of CS2. See
[the settings workflow and evidence](../WINDOWS_RENDERING.md#keeping-normal-play-separate).
Steam Offline Mode was subsequently reported by the user; online Cloud and
reconnection behavior are not yet verified and are deferred at the user's request.

The project retains **4,933,316 reconstructed commands**. Its historical strict
three-player campaign has 480 frames and 960 aligned commands; those reports
remain diagnostic. The historical single-command accepted subset is scoped to
trial 016; current competitive v2 results are linked above. Pause/ammo extraction is corrected and Dust2 was
re-extracted without altering raw commands. No model has been trained.

| Document | Purpose |
| --- | --- |
| [Current status](STATUS.md) | Everything built, what works, what is only partly verified, and where the artifacts live |
| [Next steps](NEXT_STEPS.md) | Ordered work items with dependencies and completion criteria |
| [Desktop app](../DESKTOP_APP.md) | Double-click launcher, demo folder selection, preparation and protected capture controls |
| [Round progression trial](ROUND_PROGRESSION_TRIAL.md) | Action-blind nine-clip recording, measured costs, cleanup and retained timing/review limits |
| [Round collection workflow](../ROUND_COLLECTION.md) | Bounded chronological planning, sequential recording/resume, budgets and ordered report |
| [Full player POV plan](FULL_PLAYER_DEMO_PLAN.md) | Ckanic/Dust2 eligible duration, storage estimates and proposed resumable full-match collection |
| [Completion history](CHANGELOG.md) | Dated record of delivered work, checks, and decisions |
| [Action coverage and throughput](ACTION_COVERAGE_AND_THROUGHPUT.md) | Last published 1,692-sample corpus, real controls, capture/cache/review benchmarks and retained rejections |
| [Collection preparation](../COMPETITIVE_COLLECTION.md) | Balanced selection, clock extraction and protected batch commands |
| [Competitive expansion](COMPETITIVE_EXPANSION.md) | Earlier button proof, two-map batch and 456-sample milestone |
| [Batch processing](../COMPETITIVE_BATCH.md) | Bounded plans, protected execution, resume, visual review and retained attempts |
| [Competitive buttons](../COMPETITIVE_BUTTONS.md) | Original-source button semantics and per-field evidence |
| [Competitive pilot](COMPETITIVE_TRAINING_PILOT.md) | First current-build HUD/replay proof, accepted samples, tensor artifact and retained attempts |
| [Competitive acceptance](../COMPETITIVE_ACCEPTANCE.md) | Source-specific proof, future angular targets, masks and current CLI |
| [Training tensors](../TRAINING_DATASET.md) | RGB tensor interface, masks, whole-series splitting and batch writer |
| [Controller contract](../CONTROL_CONTRACT.md) | 32 Hz action format, masks, diagnostic candidate CLI and timing bounds |
| [Control execution and labels](../CONTROL_EXECUTION.md) | Measured-profile loader, fractional mouse carry, protected program worker and scoped label API |
| [Executor/label milestone](CONTROL_EXECUTION_AND_LABELS.md) | Real combined response, remaining raw-count discrepancy, label counts and verification |
| [Synthetic input guide](../SYNTHETIC_INPUT.md) | Windows keyboard/mouse API, protected worker, plans, measured response and reproduction |
| [Synthetic calibration results](SYNTHETIC_INPUT_CALIBRATION.md) | Mouse fit/held-out result, keyboard effects, polling fixes, failed attempts and settings checks |
| [Controlled calibration](../CALIBRATION.md) | Protected Windows recording, demo extraction and replay comparison commands |
| [Calibration run evidence](CONTROL_CALIBRATION_RUNS.md) | Successful recording/replay, measured differences, failures and final verification |
| [Turn/button results](TURN_AND_BUTTON_CALIBRATION.md) | Startup cadence, independently tested turning model, rapid-button counterexamples and the intended keyboard/mouse controller |
| [Validation guide](../VALIDATION.md) | Native POV/action checks and the remaining clock evidence |
| [Acceptance guide](../ACCEPTANCE.md) | Temporal sample rules, manifests and campaign summaries |
| [Synchronization guide](../SYNCHRONIZATION.md) | Packet proof, future-command support and the first accepted profile |

The [project handoff](../../CS2_Vision_Imitation_Learning_Project_Handoff.md) remains the specification. Use the [Windows workflow](../WINDOWS_RENDERING.md), [phase filtering](../PHASES.md), [HUD profile](../HUD_PROFILE.md), and [alignment guide](../ALIGNMENT.md) for current commands and evidence. [The repository README](../../README.md) has setup examples; the [initial run report](../INITIAL_RUN.md) preserves earlier parser results.

## Keeping this up to date

After a meaningful implementation or verification milestone:

1. Update `STATUS.md` to describe the current behavior and its evidence.
2. Check off completed items in `NEXT_STEPS.md`; record what proves they are complete.
3. Add a dated entry to `CHANGELOG.md` with changed components, checks, output locations, and unresolved issues.
4. Record new parser/schema/normalizer/renderer versions when they change. Keep earlier history and raw artifacts intact.

Use **implemented**, **verified on real demos**, **verified with synthetic fixtures**, **blocked**, and **not implemented** precisely. A passing unit test or renderer dry run does not prove a complete replay-to-training-data run.
