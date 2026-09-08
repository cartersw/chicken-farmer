# Turning, button fields and startup cadence

The user approved investigating the original/replay turn difference and testing
button meanings, and reported intermittent startup freezes. This work adds
measured startup settling, repeatable probe plans and independent diagnostic
analyzers. It remains separate from competitive training acceptance.

## Intended final controller

The user clarified that the finished agent must synthesize keyboard and mouse
input: held keys, press/release events and relative mouse motion. No person
needs to operate the devices for the agent.

The current calibration worker dispatches engine console commands and inspects
the resulting underlying UserCmds, state and images. These are controlled
experiments for understanding labels and replay presentation. `+turnleft` and
`setang` do not measure a mouse actuator. The 32 Hz angular/held-button/event
contract will need an adapter to synthetic keyboard events and calibrated
relative mouse counts. That adapter is not implemented, and this milestone
does not claim physical-device or synthetic-input delivery latency.

## Startup gaps and settling

New native evidence records observed callback gaps before capture, alongside
simulation ticks and QPC wall time. Sparse milestones are kept separate from
cadence observations; a gap between two milestone records alone is not called
a frame stall.

After the existing six-second setup wait, new recordings require a continuous
two-second observed settling window, at least 32 callbacks and at least 64
advancing controller ticks. A callback gap over 250 ms, an unavailable player,
a changed pawn or a backward tick restarts that window. The settled pawn and
nondecreasing clock must remain valid through capture readiness. The Python
worker independently recomputes the window from its raw records. Historical
recordings remain readable without claiming that they had this new check.

| Measurement | Turn recording 009 | Button recording 010 |
| --- | ---: | ---: |
| Maximum callback gap during map loading | 4,976.0313 ms | 5,006.5016 ms |
| Callback gap spanning recorder configuration | 971.9959 ms | 1,036.6440 ms |
| Accepted settling callbacks | 773 | 759 |
| Maximum gap inside accepted settling window | 4.5938 ms | 4.7509 ms |
| Maximum captured movie interval | 35.0679 ms | 35.7025 ms |

Both larger gaps occur before capture. The roughly one-second configuration
interval includes guarded setup and binary/ConVar checks; it is not attributed
to a single internal engine operation. Captured movie/readback/FRAME_START gaps
remain below 36 ms in both runs, including control windows. Replay009's maximum
movie interval is 32.6687 ms. These observations support separating the startup
delays from the measured turn-presentation difference. They do not measure all
GPU/display/input latency or establish a cause for every visible freeze.

Reports: `data/calibration/control-009-stalls-v2/stalls.md`,
`control-010-stalls/stalls.md`, and `control-009-replay-stalls/stalls.md`.
Each directory also contains `stalls.json` with individual observations, source
hashes, explicit thresholds and limitations.

## Turning experiment

Recording009 contains left and right turns of different durations, a yaw wrap
and an explicit large angle step. Its 257 original images all match their own
native pixel readbacks; all 584 full commands reconstruct. All 257 image render
clocks and camera angles also match uniquely associated recorded input-history
samples within the checked clock rounding interval and 0.001-degree angle
tolerance. This distinguishes input-history samples from a command's later base
view-angle value.

Before recording009, a candidate model was frozen from recording008:
circularly interpolate adjacent observed pawn-eye yaw snapshots using their
simulation timestamps plus one tick, evaluated at the image's recorded render
time. This is a presentation model; it does not shift source command timestamps.
The hypothesis, tolerance and zero-offset control are retained in
`tools/renderer/plans/turn-hypothesis-008-v1.json`.

On the independent recording009 replay, all 22 ordinary changing-yaw pairs
pass the original 0.001-degree tolerance, including left/right onset and stop;
the maximum ordinary-turn residual is about 0.000412 degrees. The deliberate
175-degree angle step fails that tolerance with a 0.036163-degree residual.
The failure remains recorded; no parameter or tolerance was adjusted after
seeing the new recording. The zero-offset control fails all 23 changing pairs,
with a maximum residual of 35.123718 degrees.

All 160 replay images pass native readback and stable in-eye identity checks.
Evidence supports the candidate for these continuous turns, while large cuts,
recoil and other presentation effects need separate handling. It does not
certify the entire interpolation buffer, a universal time shift, mouse counts
or exact input-consumption instants.

Reports: `data/calibration/control-009-turn-analysis/report.md` and
`control-009-replay-analysis/report.md`, with the complete measurements in their
adjacent JSON files. Recording008 remains the derivation case in
`control-008-turn-analysis/report-v2.md`; its earlier report is preserved.

## Button experiment

Recording010 contains 40 dispatches covering holds, brief taps, opposite-control
overlaps, crouch/jump, firing, reload, walking and press/release sequences within
one observed command boundary. All 641 original images match native readbacks;
all 1,351 full commands reconstruct without parser or projection errors.
The button analyzer independently reads the original demo envelopes and matches
every full payload to its retained canonical protobuf before using a separate
protobuf getter-default numeric view. Raw absence remains visible.

For the tested attack/jump/crouch/forward/back/left/right mask `0x61f`, the first
experiment supplies these candidate rules over 1,349 contiguous identified
command pairs:

| Raw field | Candidate interpretation | 010 result |
| --- | --- | --- |
| Plane 1 | Held state after the retained button transitions | 1,349 matches |
| Plane 2 | Previous held state XOR current held state | 1,349 matches |
| Plane 3 | Both press and release occur for that button within the command | 1,349 matches, including three nonzero cases |

Plane 2 as "any transition occurred" fails three cases. A forward press then
release has planes `[0,0,8]`; a held-back release then re-press has `[16,0,16]`;
an attack tap has `[0,0,1]` in the numeric view. Ordered raw subtick records retain
the individual transitions. Plane 3 therefore cannot be treated as a simple
release-only field.

Reload (`0x2000`) and sprint/walk (`0x10000`) change button planes without
retained subtick records in this experiment and remain outside that tested
transition mask. Scheduled millisecond offsets are quantized by the existing
32 FPS engine dispatch loop; varying a planned offset does not establish
arbitrary within-tick dispatch. Console-generated subtick timestamps do not
calibrate synthetic mouse/keyboard delivery.

The 010 rules and an independent three/four-transition confirmation plan were
frozen in `tools/renderer/plans/button-hypotheses-010-v1.json` before recording011.
The confirmation was evaluated without retuning and **failed the exact-event
hypothesis**. Recording011 produced 257 verified images, 12 dispatches and 584
independently byte-matched full commands. Over 582 contiguous identified pairs
within the frozen confirmation mask `0x19` (attack/forward/back), plane 1's
held-end and plane 2's boundary-XOR rules pass every pair. Plane 3's
"both exported raw directions" rule fails two pairs, and exact dispatch-to-raw
sequence checks fail three of five dispatch batches:

| Known console dispatch batch | Exported subtick sequence | Numeric planes |
| --- | --- | --- |
| Forward press, release, press | One press | `[8,8,8]` |
| Held-back release, press, release | One release | `[0,16,16]` |
| Attack press, release, press, release | One press and one release | `[0,0,1]` |

Plane 3 retains information about multiple changes that is not fully described
by the exported subtick sequence. The confirmed held-state/net-change behavior
does not allow every original transition or its count to be reconstructed.
This experiment does not locate whether the reduction occurs during input
consumption, command construction or export, and does not establish lost
physical or synthetic OS input. The initial plane-3 hypothesis remains falsified
for these cases; it was not rewritten after seeing the result.

The attack batch also corresponds to a recorded weapon-fire event at demo tick
328 even though plane 1 ends released and plane 2 has no net change. The generic
validator's "weapon-fire without attack bits" warning remains intact. Training
only on held state would omit this tap information.

Reports: `data/calibration/control-010-button-analysis/report-v1.json` and
`control-011-button-analysis/report-v1.json`; the latter contains the frozen
confirmation results. Semantic training channels remain masked pending an
explicitly scoped integration and the remaining actuator/competitive-data
checks. The ordered raw records remain available as evidence without claiming
that they reproduce the entire dispatched sequence.

## Completed work and next boundary

The approved first turn investigation and independent button experiment are
complete. Startup gaps are now measured and excluded from the settling window;
continuous replay turning has a tested presentation model, and rapid-button
counterexamples establish a concrete limit on event reconstruction.

Next, implement the intended synthetic keyboard/mouse adapter inside the
protected local calibration workflow, recording its own sent events. Measure
relative mouse counts against angular changes and compare key/tap behavior with
recorded UserCmds. Then define scoped training targets that retain observable
tap/multiple-change information while masking unrecoverable timing/counts.
Do not silently expand the current console results into actuator calibration.

Current-build competitive packet/command verification, the observer HUD strip,
the training loader and match/series evaluation splits remain later work.
Steam Cloud testing remains deferred at the user's request.

## Verification and retained artifacts

- Full Python suite: **987 passed in 42.01 seconds**.
- Native Release build succeeded. Recordings009, 010, 011 and replay009 all
  exited with code zero. Every run verified all 39 original/post settings files
  with zero restore operations, restored gameinfo and removed its staged plugin.
  The final check found no CS2 process and no remaining owned staging paths.
- 009 and 010, plus replay009, used plugin SHA256
  `475bac9ec7b8da4f58a023f41a6ed5076a6e0f66b71f8ded3176d16cdbebb13b`.
  011 used the additional readiness-continuity check in plugin SHA256
  `ff18f05a73d3ad20002aafafd34f63f8fc05233470b87a4f96ea17e075fe8177`.
  Earlier009/010 observations also satisfy the stronger continuity check.
- Source demo SHA256 values: 009
  `a87cb6da2580039f4373d3e82761e2b56ecf3a26f09e5ea1f2ad460270632428`,
  010 `020db6f4e1e6f533594801e8573330daeffc5b52b3445539c12ee5cde3bd380c`,
  011 `7b7713d8d9083ebf9a811e18daed6d30ae2850600854b2900ff4ff13819cc10c`.
- `git diff --check` passed. Earlier experiments, hypotheses and reports remain
  intact. No model was trained and no new competitive samples were accepted.

## Reproduction

Probe plans are in `tools/renderer/plans/`. Use a fresh output directory for
each execution; the Windows workers remain dry-run by default. See
[the calibration guide](../CALIBRATION.md) for protected recording and replay.
The new analyzers are `python -m cs2_data.calibration_stalls`,
`python -m cs2_data.calibration_turns`, and
`python -m cs2_data.calibration_buttons`; each provides `--help`.

The generic competitive-match validator still rejects these controlled local
recordings for their single player and intentionally restricted activity.
Those failures remain distinct from complete command reconstruction and do
not promote the probes into the competitive training corpus.
