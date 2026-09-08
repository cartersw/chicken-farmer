# Synthetic Windows calibration plan contract

These plans request Windows keyboard and relative-mouse input. They use a
separate schema from the earlier engine-console probes. They contain no console
commands, absolute cursor positions, virtual-key overrides, arbitrary scan
codes, window destinations or actuator fallback settings.

The validator is
`cs2_data.synthetic_input_plan.validate_synthetic_input_plan(value)`.
It returns a detached copy of a valid plan and raises `ValueError` for invalid
input. It does not execute events or verify input delivery.

## Exact format

The top-level JSON object contains exactly these fields:

| Field | Required value |
| --- | --- |
| `schema_version` | Integer `1` |
| `producer` | `cs2-synthetic-input-calibration-plan-v1` |
| `map` | `de_dust2` |
| `fps` | Integer `32` |
| `width`, `height` | Integers `1280`, `720` |
| `duration_seconds` | Integer from `2` through `30` |
| `events` | Ordered list of `1` through `128` events |

Every event contains `id`, `at_ms` and `kind`. The remaining fields depend on its
kind; extra or missing fields are rejected:

| Kind | Additional fields | Meaning |
| --- | --- | --- |
| `key` | `key`, `pressed` | A whitelisted keyboard control and a Boolean press/release |
| `mouse_button` | `button`, `pressed` | `left` or `right`, and a Boolean press/release |
| `mouse_move` | `dx`, `dy` | Signed integer relative-mouse count requests |

Keyboard names are `W`, `A`, `S`, `D`, `SPACE`, `LCTRL`, `LSHIFT` and `R`. They are
the adapter's fixed key identifiers. Their gameplay meanings depend on the
measured game bindings; a plan does not establish those bindings. Escape, console,
Windows, menu and arbitrary keys are outside this calibration contract.

For example, an event can be
`{"id":"forward_press","at_ms":1000,"kind":"key","key":"W","pressed":true}`
or
`{"id":"horizontal_probe","at_ms":1000,"kind":"mouse_move","dx":100,"dy":0}`.

Identifiers are unique lowercase names matching `[a-z][a-z0-9_-]{0,63}`.
Scheduled times are integer milliseconds in nondecreasing order, with at least
500 ms before the first event and after the last event. Equal-time events retain
list order. Up to eight events may share a scheduled time. Each press requires
one following release, and no control may be pressed again while held. A paired
hold may span at most 2,000 scheduled milliseconds. Key and mouse-button state
are tracked separately, and every control must end released.

A mouse request must have `1 <= abs(dx) + abs(dy) <= 200`. The sum of absolute
counts across events at one scheduled time is at most 200; across the whole plan,
it is at most 3,200. Opposite directions do not cancel these limits. Boolean
values are not accepted as integer counts or times.

## Initial probes

`synthetic-mouse-probe-012-v1.json` requests twelve isolated horizontal or vertical
movements over twelve seconds, with 750 ms or more between events. Its split is
fixed before observing any results:

| Role | Event IDs | Counts |
| --- | --- | --- |
| Fit | `fit-x-positive`, `fit-x-negative`, `fit-y-positive`, `fit-y-negative` | +40 and -40 on each isolated axis |
| Held-out validation | `validation-x-small-positive`, `validation-x-small-negative`, `validation-y-small-positive`, `validation-y-small-negative` | +20 and -20 on each isolated axis |
| Held-out validation | `validation-x-large-positive`, `validation-x-large-negative`, `validation-y-large-positive`, `validation-y-large-negative` | +80 and -80 on each isolated axis |

Only the four fitting events may determine the conversion. The eight validation
events test that conversion without changing its fitted parameters or tolerances
after observing their results. This within-recording split does not replace a
future independent confirmation recording.

The plan ends at net zero requested counts per axis; intermediate net offsets are
at most 80 counts. Its total absolute count budget is 560. These are request-space properties:
unknown sensitivity, acceleration, clipping or missed input can prevent the view
from returning to its original angle. The adapter must observe the actual view
and exclude pitch-clamped measurements.

`synthetic-keyboard-probe-012-v1.json` contains twenty events over twelve seconds:
separate WASD holds, crouch, jump, short W/A taps, one left mouse click and reload.
It leaves more than 1.9 seconds after jump release and more than 2 seconds after
reload release. LSHIFT and right-click are allowed by the schema but untested by
this first probe. Same-boundary repeated edges are also allowed by the schema;
the first probe deliberately leaves their confirmation for later.

A future independent confirmation must use a distinct plan and recording, with
any measured conversion rules fixed before that recording. These initial probes
do not supply frozen mouse sensitivity or button-semantics constants.

## Runtime responsibilities

Static validation cannot verify the correct process/window is focused, that the
player is in the intended local calibration session, or that Windows and CS2
consumed an event. The adapter must perform its runtime checks and record the
actual injection results, observed times and game effects. Scheduled times and
scheduled hold limits are not guarantees about actual OS delivery or game
consumption. Delayed callbacks can also combine separate scheduled times; the
plan's per-time limits do not bound such a delayed runtime batch.

The adapter must release its owned held inputs during normal completion and
failure cleanup. A rejected or undelivered synthetic event cannot be replaced
with console dispatch or memory manipulation while retaining a synthetic-input
success claim. Relative counts are not angles, and successful API insertion is
not by itself proof of input consumption. No plan enables training labels or
claims exact physical-device timestamps.

To validate the checked-in files without launching the game:

```powershell
.venv/Scripts/python.exe -m cs2_data.synthetic_input_plan tools/renderer/plans/synthetic-mouse-probe-012-v1.json
.venv/Scripts/python.exe -m cs2_data.synthetic_input_plan tools/renderer/plans/synthetic-keyboard-probe-012-v1.json
.venv/Scripts/python.exe -m pytest tests/test_synthetic_input_plan.py -q
```

## Keyboard response analysis

`synthetic-keyboard-protocol-v1.json` fixes the first keyboard experiment's
observation windows and binds the twenty-event plan bytes before capture. Its
SHA-256 is `aa063c2a8f6d80eb9fdc4555b42b251602770fc35b3640a13e3fbbc5fcb2025d`.
It is diagnostic and does not enable semantic training labels.

The analyzer verifies the source-plan and input-ledger hashes, exact ordered
insertion receipts, foreground/local-session observations, completed releases,
and native scheduling snapshots. It independently re-reads full UserCmd payloads
from the original demo and compares every canonical protobuf byte. Delta-only or
incomplete command recovery is outside this first experiment.

For each press or release, it reports commands N-2 through N+16, where N is the
native snapshot's observed last-processed command number. That N must have a
unique, raw-checked canonical match whose execution tick equals the observed
controller tick. Raw edges already observed processed before the actual Windows
insertion call cannot count as responses. The report retains these earlier raw
records rather than hiding them. This is a bounded numeric association, not a
claim that N+1 consumed the input.

Each paired press/release phase then checks native state through sixteen ticks
after the release observation. Jump uses a ninety-six-tick tail, and reload uses
a 144-tick tail. These windows are fixed; the analyzer does not search later for
a desired result. A changed player, an incomplete command range, a native clock
gap or another planned input inside the response window leaves the phase
unknown. Successful insertion alone cannot pass a phase.

| Phase | Required observed response evidence |
| --- | --- |
| W/S/A/D | Matching signed raw movement value, horizontal displacement over 0.25 units and speed over 1 unit/second |
| Crouch | Duck amount rises by over 0.05 and the native ducked flag is observed |
| Jump | Initially grounded, then airborne with upward velocity over 1 unit/second |
| Left click | Same weapon loses ammunition, native last-shot time increases and a matching-player/round `weapon_fire` event is present in the bounded demo window |
| Reload | Same weapon's clip ammunition increases; this is an observed reload effect, not its exact start time |

Ordinary controls also require matching raw press/release records. Reload uses
separately named plane-change consistency observations because its prior console
probe did not contain corresponding subtick edges. No missing raw reload edge is
manufactured. Button masks and movement signs remain scoped hypotheses that must
be checked against the synthetic-input observations.

State-response comparisons use the latest observed native state before the
Windows insertion call as a baseline, and state observations after that call
returns. API insertion time remains distinct from game consumption time. Short
press/release windows may overlap and do not provide one-to-one edge assignments.
The analyzer retains raw parent/scalar presence, all relevant subticks, the
native snapshots and the extracted state rows for review.

Use a fresh output filename after a protected keyboard recording and extraction:

```powershell
.venv/Scripts/python.exe -m cs2_data.synthetic_keyboard_analysis --run RUN_DIRECTORY --parsed PARSED_DEMO_DIRECTORY --protocol tools/renderer/plans/synthetic-keyboard-protocol-v1.json --out FRESH_REPORT.json
.venv/Scripts/python.exe -m pytest tests/test_synthetic_keyboard_analysis.py -q
```

`response_evidence_observed_for_all_phases` means these bounded observations
agreed. `incomplete_or_missing_response` preserves missing responses and unknown
cases. Both statuses leave training readiness, general button semantics and
exact input timing unverified. This module analyzes state and command evidence;
it does not certify replay pixels.
