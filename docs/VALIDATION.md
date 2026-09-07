# Reproducible clip validation

`validate-clip` checks one completed `process-render` dataset against its actual
native ledger, retained images, decoded video, canonical commands, states and
events. It writes a new `clip_validation.json`; existing extraction, timing,
calibration and alignment files remain unchanged.

```powershell
cs2-data validate-clip --parsed data/parsed/v2-audited/DEMO_ID --dataset data/datasets/CLIP --state-context data/context/CONTEXT.json --out data/validation/CLIP
```

`--state-context` is optional. It supplies source-bound `cs2-context-v2` game-rule
segments and weapon shot-clock observations. It can resolve the old canonical
pause fields only where all five observed pause flags are available and the
tick is unambiguous. Gaps, unknown properties and round changes remain explicit.
Version 1 context remains readable for diagnostics, but cannot resolve unknown
pause state for sample acceptance. Version 2 must use the pinned parser and
`m_pGameRules.` property prefix, audit event and network parser warnings under
`rule-and-shot-evidence-v1`, and report zero evidence-loss warnings. Only the
documented bombsite/grenade-model warning types are allowed. Pause state is
recomputed from the five flags rather than trusting a summary boolean.

Shot-clock checks require
version 2's explicit unique-event observation policy, an unambiguous observation
and equal event/observation demo ticks. This prevents several events or repeated
frame-completion callbacks from sharing a weapon's later last-shot value.

The Python API is `validate_clip(parsed, dataset, out, state_context=None)`.
Consumers must use `load_validation(path, parsed, dataset, state_context=None)`;
it recomputes the report from its sources and rejects edited statuses. A report
is evidence for its recorded source versions, not a reusable approval token.

## What the results mean

Each check has `passed`, `failed` or `unknown`, its method and tolerances, actual
evidence rows, and exact frame/command coverage. A passed comparison covers only
its `scope.frame_indices` and `scope.command_row_ids`. No observations, an
unexercised action, a missing field or an inconclusive response give `unknown`.
Artifact corruption, missing required files or altered alignment provenance
raise an error instead of publishing a success report.

| Check | Evidence and limits |
| --- | --- |
| `capture_integrity` | Rehashes canonical/derived files and native evidence; verifies actual video dimensions/count/PTS; recomputes calibration and exact future-command membership. |
| `pixel_correspondence` | Decodes retained TGA pixels and compares them to the native readback RGB hashes. This alone does not prove camera state or action phase. |
| `pov_identity` | Requires the actual SteamID, the installed binary's in-eye observer mode, no view-entity override, reciprocal pawn/controller handles, matching camera/matrix inputs, and unchanged POV/camera across submission and before/after pixel readback. Missing evidence cannot inherit `pov_verified`. |
| `execution_clock` | Requires independently measured execution calibration. The packet/execution constant offset remains inferred. |
| `observation_clock` | Remains unknown until the rendered observation phase and execution epoch are independently established. Matching values or an approval flag do not establish this. |
| `canonical_fire/aim/move/jump/crouch` | Compares separate canonical input, state and event streams. These are diagnostics and never substitute for native visual checks. |
| `visual_fire/aim/move/jump/crouch` | Local native/canonical state agreement for exact frames, including inactive states. These checks do not certify an action profile. Unmodeled camera recoil and pawn interpolation residuals remain inconclusive; absolute ammo disagreements remain visible. |
| `native_transition_coverage` | Counts actual native camera, movement, ammo, ground and duck changes. An idle clip has no dynamic evidence. The independent observation/profile gate remains unknown until these changes have measured action/phase correspondence. |
| `native_execution_observations` | Records actual last-executed-command fields, simulation clocks and matching canonical command numbers. Unavailable sentinel values are not measured execution anchors. |
| `weapon_shot_clock` | Checks actual weapon last-shot time against the specific matching attack press's `server_tick_executed - 1 + when`. The maximum residual is 0.03125 ticks; disagreement is preserved. |
| `weapon_shot_future` | Checks whether the measured weapon shot lies strictly after the assigned image's render time and at or before the next measured boundary. It covers only the matched shot, not other inputs. |

Canonical angle comparisons tolerate 0.25 degrees and use wrapped yaw. Native
angle comparisons tolerate 0.5 degrees; native pawn position comparisons
tolerate 2 game units. Adjacent canonical states may provide a fractional
comparison reference only when their ticks are consecutive. The validator
never bridges missing ticks or extrapolates. Camera eye position is not compared
to pawn feet using an invented view-height offset.

Firing checks run from a recorded shot to nearby attack input, not from every
held attack to an expected shot: reloads and weapon cooldowns can prevent shots.
Likewise, movement against a wall and an unobserved jump/crouch transition remain
inconclusive rather than being marked as bad input solely from missing motion.

## Subtick causality requires separate evidence

The integer `server_tick_executed` can describe the end of the tick containing an
action. It is not necessarily the action's exact instant. The real Dust2 frame
50 shot has `server_tick_executed=16805`, attack `when=0.03125`, and observed
weapon last-shot time `262.56298828125` seconds:

```text
262.56298828125 * 64 = 16805 - 1 + 0.03125
```

For capture 006, that actual shot lies inside frame 50's measured render interval
`(262.5577697753906, 262.5890197753906]` seconds. A different capture phase could
place the image after that shot while its integer execution tick still appears
to be in the future. The validator includes a regression for precisely this
failure. It does not apply the shot formula to arbitrary controls, signed
subticks, weapon-cycle behavior or missing attack press records.

An initial broader Dust2 source audit found exact agreement for 497 of 512 matching
nonknife attack-press shots across 10 players and 23 rounds. The other 15 have
nonzero residuals and remain evidence against treating the formula as universal.
The exploratory audit is retained in `data/context/dust2-shot-clock-audit.json`;
acceptance evidence uses the stricter version 2 observation association above.

## Real validation findings

The final three clips use corrected canonical state in
`data/parsed/v2-state-fixed/DEMO_ID` and context version 2. Their datasets are
`data/datasets/dust2-validation-{008,009,010}-state-fixed`; corresponding validation
and acceptance directories end in `-state-fixed-v2`.

| Clip | Raw round | Frames / commands | Canonical jump takeoffs / crouch entries | Native ground / duck changes | Accepted samples |
| --- | --- | --- | --- | --- | --- |
| 008 | 3 | 160 / 320 | 0 / 0 | 0 / 0 | 0 |
| 009 | 4 | 160 / 320 | 5 / 1 | 7 / 2 | 0 |
| 010 | 5 | 160 / 320 | 3 / 1 | 7 / 2 | 0 |

All 480 frames pass native RGB association and strict first-person POV checks.
All corrected 008 ammo states match the native values, and its shot-clock/future
checks pass for the measured firing command. The additional clips exercise
actual jumps and crouches: canonical takeoffs are matched to recent jump inputs,
while native ground changes include both leaving and returning to the ground.
The counts need not match at a 32 FPS observation rate.

Automatic acceptance produced 480 candidate records and accepted none. Both
clock gates remain unknown; local recoil/interpolation uncertainty and specific
invalid input fields also reject affected windows. Clip 009 has one native
crouch-state disagreement at frame 74, which affects nine overlapping history
windows. These are precise rejections, not an implication that every raw command
or every frame is corrupt. Required history and preceding-action context exist
for 152 candidate windows per clip; availability is distinct from eligibility.

A separate source-bound simulation-state audit is retained at
`data/validation/dust2-native-simulation-{008,009,010}/audit.json`. Using the
explicitly assumed `native simulation_time * 64 - 10703` coordinate, all 480
native pawn eye-angle pairs match canonical states exactly, all ground states
match, and 479 duck states match. This is stronger state-clock corroboration
than comparing the rendered camera, which includes view effects. It does not
replace the still-missing pixel observation phase or command execution proof.

Earlier reports below remain useful historical evidence:

`data/validation/dust2-competitive-006-v1/clip_validation.json` retains the first
automatically recomputed validation report. All 160 native pixel associations
match, its firing event is corroborated, and 87 dynamic command/state angle
comparisons pass. There are 255 corroborated moving commands and one stationary
response that remains inconclusive. Jump/crouch and the missing native POV/state
observations are unknown. The shot clock and its future interval pass only for
row 22454/frame 50. The clip remains `training_ready=false`.

The subsequent `data/validation/dust2-validation-008-v2/clip_validation.json`
uses validation version 2 and the stricter context observation policy. All 160
frames pass actual first-person POV and pixel correspondence checks. The native
trace contains one ammo transition, 60 camera changes and 135 movement changes;
it contains no jump/crouch transition. The local camera comparison has 132 matches
and 28 inconclusive differences, beginning around recoil. Pawn position has 90
matches and 70 inconclusive interpolation differences. Neither residual alone
proves incorrect capture timing.

This run also exposed a concrete canonical-state bug: the pinned parser helper
subtracts one from `m_iClip1`, so native ammo is one above the old canonical state
on all 160 frames. The validator keeps that disagreement without adding one or
widening tolerance. These reports against the old canonical extraction remain
historical evidence; the corrected extraction and newly derived datasets require
fresh validation reports with their own hashes. Both execution and observation
clock gates remain unknown.

Automatic sample acceptance must combine this bounded evidence with per-input
quality masks, contiguous alive competitive history, known pause state, exact
identity and the chosen observation/target representation. It must reject
unknown required checks and must not extend one checked shot to a whole clip.
