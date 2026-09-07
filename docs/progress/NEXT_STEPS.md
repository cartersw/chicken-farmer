# Remaining work

**Updated: 2026-09-07.** Evidence: [STATUS.md](STATUS.md).

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

The two approved features are implemented. Their real result is **zero accepted
samples**, with the remaining timing uncertainty recorded explicitly.

## Settings protection: offline trial passed; online check remains

- [x] Snapshot selected personal settings and create a private render profile.
- [x] Implement recovery for handled failures and a separate interrupted-run command.
- [x] Require native path/Cloud guards and move each new run's plugin out of CS2.
- [x] Build the new native DLL and test restoration with temporary fixtures.
- [x] User restores normal audio/video/HUD preferences; worker confirms CS2 is closed.
- [x] Run a short capture and verify native isolation plus exact personal-file restoration (012: 64 frames, clean exit, 39 files unchanged).
- [x] Fix the shutdown crash caused by an optional plugin command destructor (011 diagnosis, 012 verification).
- [x] Archive all 17 verified historical plugin folders out of CS2; retain their evidence.
- [x] User confirms audio, video and HUD look correct after launching normally through Steam.
- [ ] Separately verify a protected capture with Steam online and check personal settings after synchronization/reconnection; the completed trial used the user's reported Offline Mode.

If the startup guard finds the Cloud interface already used, stop and implement
an earlier loader; do not disable the check. The exact installed binaries passed
the local live test. The native interface guard's observed behavior does not
establish online Steam client synchronization behavior. Future game updates
require compatibility verification. See
[the rendering guide](../WINDOWS_RENDERING.md#keeping-normal-play-separate).

## 1. Establish the remaining timing contract

- [ ] Establish the relation between execution ticks, fractional subactions, native simulation time and rendered observations.
- [ ] Resolve commands whose subactions straddle an observation. Define strictly future continuous and discrete targets.
- [ ] Distinguish camera recoil/interpolation from base aim and discrete pawn transitions.
- [ ] Add independent evidence that the validator can recompute; preserve unknown native fields.

Completion: healthy real windows pass both clock gates with evidence, while
deliberately shifted labels fail. One matching shot or edited flags cannot prove this.

## 2. Produce the first accepted subset

- [ ] Re-run validation/acceptance after timing is proven and inspect accepted windows.
- [ ] Extend firing to more players/weapons; include movement starts/stops and rapid transitions.
- [ ] Regenerate Nuke/Cache state with extractor 0.1.2 when extending the corpus.
- [ ] Broaden map/build coverage before certifying a reusable rendering profile.
- [ ] Keep missing-baseline, absent-message and invalid-fraction rejection; recover data only with proven semantics.

Completion: a small real corpus has accepted windows, complete provenance and
meaningful negative cases. The global validator may still reject incomplete demos.

## 3. Build the training data interface

- [ ] Implement an image/previous-action loader using accepted manifests.
- [ ] Define variable command/subtick targets, tensor padding and label masks.
- [ ] Split deterministically by match/series; keep the current three maps together.
- [ ] Verify a complete training batch and sampling statistics.
- [ ] Collect additional matches for independent evaluation.

## 4. Train and evaluate

- [ ] Implement the handoff's small CNN/GRU behavioral-cloning baseline.
- [ ] Train aim, movement and button heads.
- [ ] Compare held-out metrics against trivial baselines and inspect failures.

Training and live control remain later milestones.
