# Observed state and weapon-clock context

`cs2-context` parses game rules and weapon-fire observations independently of
UserCmd reconstruction. It writes a new source-hashed JSON sidecar without
changing canonical outputs. Build it alongside extraction and phase auditing:

```powershell
# From tools/usercmd-extractor, using the configured Go environment:
../../.tools/go/bin/go.exe build -trimpath -o ../../bin/cs2-context.exe ./cmd/cs2-context
```

The repository's `scripts/build.ps1` also builds all three Go tools. From the
inner repository root, audit a supplied recording into a fresh path:

```powershell
bin/cs2-context.exe --input esl-challenger-league-season-52-europe-cup-6-misa-vs-mouz-nxt-bo3-FrnpfKbdBS_lppg7U-SOO5/mouz-nxt-vs-misa-m1-dust2.dem --out data/context/dust2-new.json
```

Schema 2 / producer `cs2-context-v2` records half-open tick segments, round
identity, freeze/warmup/match state, and five observed pause flags under
`m_pGameRules.`. Unknown flags remain null; gaps are preserved. Repeated ticks
with conflicting rule state are marked ambiguous. Consumers derive pause from
all five flags and require complete coverage of the requested interval.

Weapon observations retain both the fire-event tick and the FrameDone tick,
SteamID/slot, weapon and its network `m_fLastShotTime`. Repeated frame callbacks,
multiple same-weapon events in one tick, or mismatched event/observation ticks
cannot establish unique shot timing. Such rows are explicitly ambiguous.

Both parser warning channels are audited. Only unrelated bombsite-geometry
and unknown-grenade-model warnings are allowed by the versioned policy.
Potential state/event loss or unsupported schema evidence prevents publication.
Parsing must complete, pending events must have observations, and the demo's
hash must remain unchanged. Publication refuses existing files, including races.

The supplied Dust2 v2 audit has 75 segments and 3,486 weapon observations;
two are marked ambiguous. No parser warnings occurred. See
`data/context/dust2-context-v2.json`. Version 1 remains historical diagnostic
evidence and cannot establish trusted pause or shot-clock acceptance.

## Canonical state corrections

Extractor 0.1.2 fixes the old game-rule prefix and records `m_bGamePaused` in
addition to timeout/resume flags. It also reads the CS2 firearm magazine count
directly from `m_iClip1`; the pinned library helper subtracted one incorrectly
for the observed demo. Zero is preserved, while absent/unsupported values and
equipment without magazines stay null. Canonical schema remains 2.

Dust2 was re-extracted fully into `data/parsed/v2-state-fixed/`. Its commands,
rounds and events are byte-identical to the old extraction. The corrected
state matches the observed Glock 20-to-19 shot transition. Old outputs remain
available for historical comparison; use a fresh output root for further
re-extraction. Full-demo baseline/subtick quality failures are still reported.
