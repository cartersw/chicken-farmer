# Frame and input synchronization

The initial training profile predicts a future recorded server command from
eight replay images. It uses a conservative bound on information already
available to the renderer. It does not assume that a replay cursor, a camera
timestamp, and a command's execution tick mean the same thing.

## Why the original join was insufficient

The original diagnostic join assigned complete commands to fractional image
intervals using their integer execution-end tick. Across captures 008–010,
56 valid subtick events occurred before their assigned image. Some additional
fractions fall outside the first profile's supported range. These raw records
are preserved; they are not clamped or silently reassigned.

Trial 015 directly measured the difference between clocks. At the first image,
the replay cursor was 6002, the delivered source packet was demo tick 6000,
and its NET_Tick, snapshot and command-envelope server clocks were 16703.
The render-time value was 16703.65234375 ticks. The cursor is therefore not a
replacement for either the source packet clock or the rendered-image phase.

## Evidence chain

1. `cs2-clocks` retains NET_Tick, PacketEntities and command-envelope protobufs
   with explicit field presence, hashes and bounded source windows.
2. `clock_evidence.py` decodes those retained protobufs independently, verifies
   their advertised fields, and checks the original demo hash.
3. Native message hooks record paired entry/return observations and counters.
   `audit-synchronization` recomputes those counters and joins explicit payload
   clocks. An edited approval field cannot certify an observation.
4. Native `ReadPacket` instrumentation hashes the complete packet bytes returned
   to the game. `packet_evidence.py` reads the original `.dem` framing, Snappy
   blocks and packet bitstreams directly. It preserves original wire ordering
   and distinguishes live packets from seek checkpoints.
5. The packet audit accounts for seeking, filtered packets, null reads,
   process-lifetime maxima and movie/readback boundaries. Only a verified
   per-image bound can feed the new acceptance profile.
6. The server command support audit supplies an enclosing execution interval.
   Player identity, pixel correspondence, competitive phase, pause, life state,
   command continuity and field quality remain separate acceptance requirements.

The native implementation is guarded for the exact inspected Windows binaries.
See [CLOCK_HOOK.md](../tools/renderer/plugin-windows/CLOCK_HOOK.md) and
[SERVER_COMMAND_SUPPORT.md](SERVER_COMMAND_SUPPORT.md) for source evidence,
addresses, hashes, filtering behavior and limits.

## Future targets and mouse-turn labels

Let B be the verified inclusive server-tick ceiling for the image history.
An eligible recorded command with execution stamp E has enclosing support
`[E-1, E]`. Its support must begin strictly after B; equality is rejected.

An aim difference depends on two adjacent commands. Both must be future.
For ordinary consecutive commands and integer B, the first usable pair is:

| Item | Enclosing support |
| --- | --- |
| Normalization predecessor | `[B+1, B+2]` |
| Target command | `[B+2, B+3]` |
| Aim difference using both commands | `[B+1, B+3]` |

The selector uses two observed frame intervals as its deadline and records the
unlabelled gap and target delay. It never extrapolates the final boundary or
silently selects a later command when the first required command is missing.
Previous actions and input-history records are not model inputs in this profile.

Labels describe recorded command axes, raw button bitplanes, mouse fields,
weapon selection and adjacent-command angle differences. Raw subticks and
input history remain available as provenance. Subticks alone do not reconstruct
the complete analog or mouse trajectory. Button planes must not be renamed
generic held/pressed/released masks.

This is a small replay-based behavioral-cloning profile with explicit latency.
It does not establish original-client HUD equivalence, original human mouse
sampling times, or the exact time of every visual weapon/movement effect.
The original strict `accept-samples` profile and its historical diagnostic
reports remain separate and unchanged.

## Reproduce the clock audit

Run from the inner repository, after `scripts/build.ps1`:

```powershell
bin/cs2-clocks.exe --input <source.dem> --windows 5980:6350 --out data/clocks/new-clock-evidence.json

.venv/Scripts/python.exe -m cs2_data audit-synchronization `
  --parsed <parsed-demo-directory> `
  --dataset <completed-process-render-directory> `
  --network-clock data/clocks/new-clock-evidence.json `
  --out data/synchronization/new-audit
```

Use fresh output paths. The clock audit alone always remains
`training_ready=false`; it reports received-message associations, not acceptance.

## Accept and load the first training subset

The completed trial-016 output is `data/accepted/dust2-causal-016-v1/`:
**129 accepted samples and 31 rejected candidates**. Every accepted sample has
eight raw-image references, one future command target and the two canonical
command records used for its aim difference. This is a five-second Dust2 pilot.
The independent loader recomputed the same result from the original sources.

To generate another review of the same capture, use a fresh output directory:

```powershell
$parsed = 'data/parsed/v2-state-fixed/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773'
.venv/Scripts/python.exe -m cs2_data accept-causal-samples `
  --parsed $parsed `
  --dataset data/datasets/dust2-timing-016 `
  --network-clock data/clocks/dust2-three-clips-v1.json `
  --state-context data/context/dust2-context-v2.json `
  --out data/accepted/new-causal-review
```

Defaults are `--history-frames 8 --target-horizon-frames 2`. The output contains
`accepted_samples.jsonl`, `rejected_samples.jsonl`, `packet_source_evidence.json`
and the completion manifest `causal_acceptance.json`. The manifest is published
last; existing output is never overwritten. Images remain at their hashed raw
capture paths instead of being duplicated for overlapping histories.

```python
from pathlib import Path
from cs2_data.causal_acceptance import load_causal_acceptance

report = load_causal_acceptance(Path("data/accepted/dust2-causal-016-v1"))
print(report["accepted_count"])  # 129 on the preserved trial-016 sources
```

Loading rescans the source demo and recomputes packet bounds, identity, pixels,
state and labels. Edited acceptance flags or rehashed sample files cannot
replace that proof. Required source artifacts and the inspected server binary
must remain available; this is an evidence loader, not yet a tensor loader.
Changed or missing sources fail verification.

The acceptance boundary also checks projected command clocks, pawn identity,
actions, optional-field presence, and every projected subtick/history record
against the retained protobuf. Command numbers come from source envelopes;
the usually absent legacy base-command number is not treated as zero. Raw
fractions cannot be hidden by clearing an extracted list. Unsupported command
flags and invalid fractions reject the affected sample without repair.

The existing `process-render` viewer shows diagnostic interval assignments.
Read the accepted JSONL for the actual future targets; that viewer has not yet
been adapted to this profile. Sampled HUD review is retained at
`data/rendered/windows-timing-016/visual-review.json`; the observer name/weapon
strip remains visible, and original-client HUD equivalence is not established.

The [progress tracker](progress/STATUS.md) records real captures, final sample
counts and remaining work. Steam Cloud testing is deferred at the user's request.
