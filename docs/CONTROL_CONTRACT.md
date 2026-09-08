# Proposed 32 Hz control contract

The Windows adapter now sends synthetic keyboard presses/releases and relative
mouse movement. Its protected mouse probe measures approximately −0.022 degrees
yaw per X count and +0.022 degrees pitch per Y count at the isolated sensitivity
of 1, with separate held-out checks. See [synthetic input calibration](SYNTHETIC_INPUT.md).
The [scoped executor and label builder](CONTROL_EXECUTION.md) now connect this
representation to the protected adapter and derive partially masked local
targets. Their measured source proof remains separate from this v1 representation.
The [independent turn/button experiments](progress/TURN_AND_BUTTON_CALIBRATION.md)
support scoped held-state/net-change behavior, but show that exported subtick
records do not reproduce every known rapid console transition. Exact ordered
event targets remain masked; the raw three planes and records stay available.

The original contract defines the representation of a proposed action and
builds diagnostic candidates from recorded commands. It does not execute game
controls or produce new accepted training samples. Existing acceptance artifacts
retain their original single-command target definition.

One decision covers **31,250,000 nanoseconds (32 Hz)**. The Python API is in
`src/cs2_data/control_contract.py`:

- `control_contract_schema()` returns a fresh JSON Schema.
- `validate_control_action(payload)` returns representation errors. An empty
  list does not establish timing calibration or authorize execution.
- `build_control_candidate(commands, observation_upper_execution_tick=B,
  identity=identity, observation_frame_index=index)` produces a diagnostic
  two-command candidate. The caller must independently establish the frame
  information bound, original source envelopes and competitive state evidence.

## What an action means

`angular_delta_deg` contains yaw and pitch changes in game-angle degrees over
the decision period. It does not contain mouse counts, DPI, sensitivity or a
physical input trajectory. Game angle conventions remain unchanged. The measured
local mouse mapping must retain its build/settings scope when used by an executor.

`buttons_held_at_start` specifies the state of every supported semantic button
immediately before any offset-zero events. `button_events` is an ordered array
of `{offset_ns, button, pressed}` transitions within the period. Offsets must be
integers in `[0, 31250000)`. Equal offsets retain array order. A press must follow
a released state, and a release must follow a held state. This can represent a
short tap whose initial and final held states are both false.

The initial button vocabulary is `forward`, `back`, `left`, `right`, `jump`,
`crouch`, `walk`, `attack1`, `attack2`, `reload`, `use`, `drop`, and `scoreboard`.
It is a proposed semantic vocabulary, not a claim about engine bit numbers or
keyboard bindings. Weapon selection remains raw diagnostic command metadata;
the current contract has not yet defined a calibrated selection action.

Each channel has an explicit `available` flag. An unavailable value is `null`,
not a zero angle, an all-released button state or an empty event list. All channel
`calibration_status` values remain `unmeasured`. No API accepts a `verified=true`
override to turn these candidates into control or training approvals.

The JSON Schema describes shape and constants. The Python validator also checks
finite values, strict scalar types, channel availability, event ordering and
transition consistency. Consumers should use the validator as well as a schema
validator when checking Python values.

## Recorded-command candidate timing

For an integer image information bound `B`, the initial profile requires one
command at each of three consecutive server execution ticks:

| Role | Execution tick | Enclosing command support |
|---|---:|---|
| Angle normalization predecessor | B + 2 | [B + 1, B + 2] |
| First target command | B + 3 | [B + 2, B + 3] |
| Second target command | B + 4 | [B + 3, B + 4] |

All three contributing supports begin strictly after `B`. Their combined
support is `[B+1, B+4]`. The two targets' processing window is `[B+2, B+4]`,
which spans 31.25 ms at 64 Hz. Its beginning is 31.25 ms after the conservative
image information bound, and its end is 62.5 ms after that bound. These are
clock-domain bounds, not a measured physical input delay or exact event times.

Yaw changes are computed between adjacent commands using wrapped differences,
then summed. The sum is not wrapped again: two 150-degree increments remain a
300-degree change. This retains the existing shortest-angle interpretation for
each adjacent pair; it does not reconstruct hidden rotations between commands.
Pitch changes are summed without yaw wrapping.

The candidate requires the same demo, round, player and pawn; known alive,
non-warmup, non-freeze commands; increasing source row IDs; and consecutive
command numbers and server, demo and client ticks. This deliberately narrow
profile rejects repeated or skipped clocks. Missing commands are never replaced
by later commands, and duplicate execution ticks are not merged.

Raw protobuf bytes are rechecked against their projected columns using the
existing acceptance helpers. Missing button/angle/base parents, unsupported
command flags, invalid nested fractions and altered projections reject the
candidate. The normalization predecessor receives the same intrinsic checks.
The helper also rejects explicitly paused commands, but complete pause evidence
and source/observation verification remain the integration layer's job.

## What stays masked

The three raw button planes are retained separately as lossless hexadecimal
values with parent presence. They are **not** assumed to mean held, pressed and
released. Raw subtick and input-history records remain provenance. Fractions
are neither rescaled into event offsets nor promoted to controller events.

Consequently, generated candidates expose a locally valid angular aggregate
while both semantic button channels remain unavailable. Raw analog movement,
weapon selection, impulse and mouse-count values are diagnostic fields; they
are not declared calibrated control labels.

Every candidate has `training_ready=false` and `live_control_ready=false`, even
when `candidate_valid=true`. Candidate validity describes local command quality.
It does not replace the acceptance pipeline or verify a caller-supplied image
bound.

## CLI and reverified candidate audits

Write a standalone proposed-action JSON Schema to a new file:

```powershell
.venv/Scripts/python.exe -m cs2_data control-contract --out data/control/control-contract-v1.schema.json
```

Audit the accepted observation histories using fresh verification of their
original demo, native packet bounds, images, POV and command-support profile:

```powershell
.venv/Scripts/python.exe -m cs2_data audit-control-candidates `
  --acceptance data/accepted/dust2-causal-016-v1 `
  --out data/control/dust2-control-016-v1
```

The audit creates `control_contract.schema.json`, `control_candidates.jsonl`
and a completion manifest `control_audit.json`. Every contributing command,
including the additional second target, must match a unique live source packet
envelope. Complete competitive state/context coverage is checked through the
new window. It preserves the original accepted histories and their image hashes;
it does not overwrite or change the old single-command samples.

`eligible_candidate_count` means the two-command diagnostic passed those checks.
It does not mean calibrated button labels or a new training acceptance. Both
semantic button channels stay masked and training/live-control readiness stays
false. The `load_control_audit(path)` API redoes original acceptance and candidate
verification and compares exact report and sample contents. Editing flags and
rehashing the files cannot approve a changed candidate.

The current real-data attempt stopped at original acceptance revalidation:
the installed Valve `server.dll` no longer matches the inspected command-support
binary. The required SHA256 is
`9e5749d77dcb68883477feae751a3f28068d119ec145edcb0e4d48d15b538d36`;
the observed installed SHA256 is
`cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c`.
The old report still records 129 historical accepted samples, but its loader
cannot currently reproduce the support proof. No new completed control audit or
verified real-data candidate count was published. An archived inspected binary
or a reviewed compatible support profile is needed; changing a hash constant
alone is not a valid fix. The audit does not modify the installed game.

## Verification and remaining work

The modules and their 76 focused tests are implemented. Tests cover future support,
missing/duplicate commands, identity/state/clock boundaries, raw projection
tampering, invalid flags and fractions, wrapped-angle sums, button masking, and
proposed event ordering/transition rules, independently checked second-target
envelopes, original acceptance and new audit tampering, changed source files,
and immutable publication. No game session was launched for this work.

Remaining work is to resolve the changed inspected-binary dependency, complete
the real candidate audit, add an inspector, and compare controlled original/replay
recordings. Measured per-action evidence is needed
before defining button-plane semantics or converting subtick records into timed
events. Competitive weapon selection also needs a defined semantic channel and
calibration. Those tasks must preserve the existing protected Windows session
workflow and leave Steam Cloud testing deferred.
