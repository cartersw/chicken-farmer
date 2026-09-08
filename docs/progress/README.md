# Project progress

This folder tracks completed work, verification, blockers, and the remaining CS2 vision imitation-learning milestones.

**Current work:** the controller CLI and native Windows calibration are
implemented; a protected probe has 321 verified images, 14 actions and 711 fully
reconstructed commands. Its protected five-second replay produced 160 images.
See [current calibration run evidence](CONTROL_CALIBRATION_RUNS.md) for measured
results and remaining checks. The earlier
[pause checkpoint](CONTROL_CALIBRATION_CHECKPOINT.md) is historical.

**Last updated: 2026-09-07. Current milestone: controlled recording and replay.**

The historical 129-sample partition remains intact. Revalidating its original
binary proof is currently blocked by a game update; new calibration diagnostics
do not add accepted training samples.

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
remain diagnostic. The new accepted subset is scoped to trial 016 and its
future-command profile. Pause/ammo extraction is corrected and Dust2 was
re-extracted without altering raw commands. No model has been trained.

| Document | Purpose |
| --- | --- |
| [Current status](STATUS.md) | Everything built, what works, what is only partly verified, and where the artifacts live |
| [Next steps](NEXT_STEPS.md) | Ordered work items with dependencies and completion criteria |
| [Completion history](CHANGELOG.md) | Dated record of delivered work, checks, and decisions |
| [Controller contract](../CONTROL_CONTRACT.md) | 32 Hz action format, masks, diagnostic candidate CLI and timing bounds |
| [Controlled calibration](../CALIBRATION.md) | Protected Windows recording, demo extraction and replay comparison commands |
| [Calibration run evidence](CONTROL_CALIBRATION_RUNS.md) | Successful recording/replay, measured differences, failures and final verification |
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
