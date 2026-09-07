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
The [progress tracker](progress/STATUS.md) records real captures, final sample
counts and remaining work. Steam Cloud testing is deferred at the user's request.
