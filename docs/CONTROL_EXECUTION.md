# Scoped control execution and labels

The executor translates the existing 32 Hz action format into Windows keyboard
and relative mouse events. The label builder separately derives partially masked
targets from verified recorded commands. These are local calibration tools;
neither component accepts competitive images for training or runs a trained policy.

The [completed milestone](progress/CONTROL_EXECUTION_AND_LABELS.md) includes real
combined aim/movement evidence, 838 diagnostic label candidates and the remaining
raw mouse-count discrepancy. Angular response matches the sent input in all five
combined phases; raw base count fields do not preserve all emitted counts.

## Execution

`src/cs2_data/control_executor.py` consumes a measured profile issued by
`load_measured_control_profile`. Loading the profile recomputes the original
mouse, keyboard, protobuf and pixel evidence. A serialized profile or a
`verified` flag cannot replace that check.

Each decision spans 31,250,000 ns. The executor applies the inverse measured
two-axis gain matrix to the requested angular delta. It rounds cumulative ideal
mouse counts to the nearest integer, with half-count ties away from zero, and
emits the difference from the previous rounded total. This retains small aim
requests across decisions without introducing movement during zero-action steps.
The declared held state must match the previous decision's resulting state.

`prepare(action)` stages a decision; `commit(prepared)` advances state after a
successful dispatch or intentional no-op. `discard(prepared)` drops an unsent
decision. A partial physical dispatch requires releasing owned controls and
aborting; discarding compiler state cannot undo OS input. `compile_sequence`
uses this core to produce a bounded, inspectable offline program.

Supported mappings are W/A/S/D, Space, left Ctrl, left mouse and R. Active walk,
secondary attack, use, drop and scoreboard controls are rejected. The bounded
probe permits at most eight events and 200 mouse counts per decision, 128 events
per second, two seconds per continuous hold, and 960 decisions / 128 events /
3,200 mouse counts per program. Requests exceeding limits are rejected.

`tools/renderer/control_windows.py` runs the compiled program through the existing
protected local capture and SendInput adapter. It verifies actual ready-time
`sensitivity=1`, `m_yaw=0.022`, `m_pitch=0.022` and `sensitivity_y_scale=1` before
input. The native setup applies these settings to the private profile.

The wrapper currently accepts events only at exact 32 Hz decision boundaries.
Its integer-millisecond schedule stores the floor of each exact boundary: on
the verified native 64 Hz clock this selects the same tick as the original
nanosecond threshold. Within-decision event offsets remain representable in the
core but are rejected by this wrapper. Scheduled boundaries and OS receipts do
not establish exact engine input-consumption time.

Dry-run from the repository root:

```powershell
.venv/Scripts/python.exe tools/renderer/control_windows.py --output data/calibration/executor-NEW --evidence-output data/control/executor-NEW
```

To execute, close normal CS2 and add `--allow-version-mismatch --execute` for the
currently inspected build. All eight native binary hash checks remain required.
Use separate fresh output paths. The default 12-second recording includes 256
decisions: five half-second mixed-aim phases paired with forward, left, back,
right and crouch. Compilation retains the original program, all decision states,
fractional carry, projected events and hashes linking them to the capture.

The protected worker retains settings snapshots, native isolation, owned-process
and focus checks, input-release cleanup, gameinfo restoration and plugin removal.
Steam mode and Cloud preferences are not changed. See
[the protection workflow](WINDOWS_RENDERING.md#keeping-normal-play-separate).

## Label semantics

`src/cs2_data/control_labels.py` provides
`build_control_label([predecessor, first_target, second_target], profile=profile)`
and `control_label_schema()`. Profile `cs2-scoped-control-label-32hz-v1` binds
every complete source row to the independently checked original local recordings.
It does not replace the older competitive candidate or acceptance profiles.

Each derived field contains `value`, `valid` and `reason_codes`:

| Field | Meaning |
| --- | --- |
| Aim | Adjacent command-angle differences and their sum across two target commands |
| Held states | Recorded start, intermediate and end states for the eight measured controls |
| Net changes | Per-command change bits; aggregate net change, net press and net release |
| Recorded activity | Evidence of activity in retained planes/subtick records |
| Unresolved rapid activity | Positive retained plane-3 bit; exact transitions remain unresolved |
| Exact count/order/offsets | Unavailable; always masked |

Missing parent messages remain unavailable. Scalar defaults are used only inside
verified present protobuf parents. Matching released endpoints do not establish
that no tap occurred. Raw button planes, presence, subticks and protobuf bytes
remain attached for inspection.

Command number, execution tick and demo tick must advance consecutively. The
local client-generation clock can repeat and then advance by two under the
measured 32 FPS loop; it remains a separate field and cannot define the decision
interval. Resets, larger jumps, identity changes and unsupported records mask
the affected label. This rule is specific to the new local profile.

`label_valid` means some scoped fields are usable. It does not establish image
alignment or competitive-source acceptance. `training_ready`, `live_control_ready`,
`image_alignment_verified` and `competitive_source_verified` remain false.

Rebuild the source-checked labels into a fresh directory:

```powershell
.venv/Scripts/python.exe -m cs2_data.control_label_audit `
  --mouse-run data/calibration/synthetic-001 `
  --mouse-parsed data/calibration/synthetic-001-parsed/fcab7de82ed3cf8a5a38754d122379f54f4ad0bb4cf72a633c393b5c808aaef1 `
  --keyboard-run data/calibration/synthetic-004 `
  --keyboard-parsed data/calibration/synthetic-004-parsed/1e0e8d1127fa0172732bfbf5fff8817a005b78d3c407a1c12a223e2de9a1324e `
  --keyboard-protocol tools/renderer/plans/synthetic-keyboard-protocol-v1.json `
  --output data/control/scoped-labels-NEW
```

The output retains the recomputed profile evidence, separate mouse/keyboard
label JSONL, field validity counts, rejection reasons and cross-checks against
the twelve fixed mouse cases and ten keyboard response phases. It does not pair
these diagnostic labels with images under the competitive acceptance rules.

Independently analyze a completed default program using fresh report paths:

```powershell
.venv/Scripts/python.exe -m cs2_data.control_execution_analysis `
  --run data/calibration/executor-003 `
  --parsed data/calibration/executor-003-parsed/1eb9c15579dd00b888425d90c1ff540c9a1c66bb88e1cbf68a79e5aa282daefc `
  --compilation data/control/executor-003 `
  --out data/calibration/executor-003-analysis/report-NEW.json
```

This rechecks the fixed program, compilation arithmetic, source profile, actual
OS receipts, original demo bytes, pixels and five response windows. It retains
the stricter raw-count mismatch even when recorded and rendered angles agree.

## Calibration setting evidence

The historical synthetic runs recorded sensitivity immediately after queuing
the setup command, before that command had taken effect. That early readback is
1.096426. The requested value and saved private configuration are 1, and the
measured gain is approximately 0.022 degrees per count. The source evidence keeps
all of these facts and marks historical ready-time sensitivity as unobserved.
New execution requires a fresh readback of all four effective scales at readiness.
The old recording evidence has not been rewritten.
