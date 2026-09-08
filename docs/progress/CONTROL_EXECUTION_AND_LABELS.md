# 32 Hz executor and scoped label milestone

Updated: 2026-09-08. API and reproduction:
[CONTROL_EXECUTION.md](../CONTROL_EXECUTION.md).

## Implemented

- Stateful conversion from the existing 32 Hz action contract to bounded Windows
  input, with measured two-axis gain, cumulative fractional mouse counts, held
  button continuity and explicit unsupported-control rejection.
- Protected compiled-program worker with source-linked compilation artifacts,
  exact decision-boundary scheduling and actual ready-time mouse-setting checks.
- Immutable measured profiles issued only after original source, response and
  pixel evidence are recomputed.
- Separate scoped two-command labels for aim, held states, net changes and
  recorded activity. Every field has an availability mask and reason codes.
- Independent combined-motion analysis with a fixed five-phase program, angular
  windows and tolerances selected before the live test.

## Verified label export

The completed audit is
[`data/control/scoped-labels-001-v2/report.json`](../../data/control/scoped-labels-001-v2/report.json).
It revalidates both original synthetic recordings: 770 images and 1,680 complete
commands. All twelve mouse cases and all ten keyboard response phases agree
with the new labels.

| Source | Two-command candidates | All scoped fields observed | Partially observed | Fully masked |
| --- | ---: | ---: | ---: | ---: |
| Mouse synthetic-001 | 419 | 0 | 417 | 2 |
| Keyboard synthetic-004 | 419 | 63 | 354 | 2 |

Each source has 417 valid aim targets. The mouse-only recording omits button
parents; their absence stays unknown. The keyboard recording has sparse idle
button parents, so many targets have only some usable fields. The two fully
masked candidates per source retain setup/cadence rejection reasons. The export
uses nonoverlapping target pairs and leaves one final source row unused.

"All scoped fields observed" excludes exact event count, order and timing:
those are masked in every label. Matching released endpoints are not treated as
proof that no tap occurred. No labels have competitive image acceptance.

The initial `scoped-labels-001` attempt retained completed source proof but hit
a Python module-class identity bug during label generation. The CLI now calls
the canonical module, a regression covers it, and the successful fresh v2
export is separate from that preserved attempt.

## Configuration evidence

The old recordings' immediate post-command sensitivity readback is 1.096426.
They requested and persisted sensitivity 1; the independently measured response
is about 0.022 degrees/count. Historical capture-ready sensitivity was not
directly observed, and that limitation is explicit in the new profile.

The new native build applies and reads all four scales at capture readiness:
`sensitivity=1`, `m_yaw=0.022`, `m_pitch=0.022`, `sensitivity_y_scale=1`.
Its SHA-256 is
`bf429fb57f176f15da368a9a9061a0c9076293b418fa89ea8cb87b5cb0266fdd`.
The original calibration plugin and old evidence remain retained.

## Preserved preflight attempts

`data/calibration/executor-001` reached verified local readiness with all four
required effective scales. Adapter preflight then rejected an OS/session guard;
**zero events were inserted**. The original error omitted the detailed failed
guard, so it cannot establish which OS condition failed. Constructor errors
now preserve that evidence, with focus, process identity, native scope and
cleanup-failure regression checks.

All 39 selected personal settings files were verified unchanged, with zero
restore operations; gameinfo was restored and the staged plugin was removed.
This attempt is not counted as a completed control test.

`executor-002` repeated the same program after adding the diagnostic. It also
stopped before any insertion: CS2 process identity and native loopback scope
passed, but the foreground PID belonged to VS Code. All four ready-time mouse
scales passed. The same 39-file and plugin/gameinfo cleanup checks passed.
This identifies a focus prerequisite, not an observed game-response failure.

## Completed combined recording

After the user explicitly focused CS2, `executor-003` completed the unchanged
program: **256 decisions, 82 Windows events, 385 original images and 840 complete
UserCmds**. Every image matches its native readback; all command payloads match
the original demo bytes. All four effective mouse scales match the requirements.
The demo SHA-256 is
`1eb9c15579dd00b888425d90c1ff540c9a1c66bb88e1cbf68a79e5aa282daefc`.

The frozen independent analysis is
[`executor-003-analysis/report.json`](../../data/calibration/executor-003-analysis/report.json).
All five phases have matching camera/eye and recorded angular changes, recorded
button press/release responses, and the expected movement or crouch response.
However, raw demo mouse-count totals fail the stricter equality check:

| Phase | Sent X/Y counts | Recorded X/Y sum | Camera yaw/pitch change, degrees |
| --- | --- | --- | --- |
| Forward | 40 / 8 | 37 / 7 | -0.880005 / 0.176000 |
| Left | -40 / -8 | -38 / -8 | 0.880005 / -0.176000 |
| Back | 24 / -24 | 22 / -22 | -0.528076 / -0.528000 |
| Right | -24 / 24 | -23 / 23 | 0.528076 / 0.528000 |
| Crouch | 8 / 8 | 7 / 7 | -0.176025 / 0.176000 |

The overall report therefore remains
`incomplete_or_inconsistent_execution_response`. Its fixed windows and
tolerances have not been changed to make the recording pass. Matching angles
support the measured angular response; they do not establish complete raw
mouse-count preservation or exact event timing during combined input.

An expanded command-neighborhood check retains the same deficits: each missing
vector equals its phase's first emitted mouse impulse. Across the entire demo,
base raw counts sum to X/Y 5/7 versus the emitted net 8/8. In the forward phase,
command 910 changes base yaw by about -0.110001 degrees (five counts) but records
only raw X=2 and no raw Y. Its input history retains the preceding first impulse's
approximately -0.065994 / +0.022 degree change (three X / one Y counts), also
visible in native capture 34. This supports retained angular response with
incomplete base raw-count accounting. It does not prove physical input loss,
an exact event timestamp, or a rule for reconstructing missing raw counts.
The separate [count discrepancy report](../../data/calibration/executor-003-analysis/count-discrepancy.md)
and [source evidence](../../data/calibration/executor-003-analysis/count-discrepancy.json)
retain the expanded-window comparison and original command/history observations.

Observed native dispatch times in this run are 15.625 ms after the requested
decision boundaries. Requested nanoseconds, observed native time and actual
insertion QPC remain separate. The millisecond encoding selects equivalent
thresholds on the 64 Hz grid; it does not promise a callback or insertion at
every exact boundary.

The generic extractor returns failure for the short single-human diagnostic's
competitive quality checks, while independently reporting 840/840 complete
payloads, zero reconstruction rejections and zero unaccounted payloads. The
competitive validator has not been relaxed.

CS2 exited with code zero. Across all three attempts, each run verified all 39
selected personal files with zero restore operations, restored gameinfo and
removed its plugin. The final direct file-hash check also passed, found no
renderer staging, and a process check found no running CS2. Evidence:
[`final-settings-check.json`](../../data/calibration/executor-003/final-settings-check.json).
Steam mode and Cloud preferences were unchanged.

Verification: **1,455 Python tests passed in 45.03 seconds** and the native
Release build and `git diff --check` passed. Tests cover profile provenance, command presence/masks,
clock cadence, executor rounding/state/bounds, scheduling, actual settings
readback, focus failure evidence and independent response rejection cases.

## Remaining limits

The program is scripted. No trained policy, competitive corpus expansion or
tensor loader is delivered by this milestone. Exact input-consumption timing,
subtick event reconstruction, recoil/large-turn coverage and additional controls
remain separate work. The historical 129-sample acceptance partition is unchanged;
its missing original Valve server binary still blocks revalidation after the
game update. Steam online/Cloud testing remains deferred.
