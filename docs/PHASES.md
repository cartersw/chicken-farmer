# Competitive phase evidence

`render-jobs` now requires `--phase-manifest` by default. A complete command stream and `is_warmup=false` do not prove that a round belongs to competitive play. The supplied ESL Dust2, Nuke, and Cache demos all begin with a knife/setup round whose `IsMatchStarted` is true and whose warmup flag is false. The game-rule phase is `Pregame` (1); its temporary score is reset before the real match. Each demo then contains a warmup round and 24 scored competitive rounds.

The separate `cs2-phases` Go command records game-rule phase, match-started/warmup flags, round boundaries, score totals, and relevant game events. It disables user-command parsing, so extracting phase evidence takes about ten seconds per supplied demo and leaves existing canonical Parquet files unchanged. It hashes the demo before and after parsing, publishes only after a complete parse, and refuses to replace an existing output. Its JSON schema is version 1, independent of canonical parser schema 2.

From `tools/usercmd-extractor`, with the repository Go cache configured:

```powershell
& ../../.tools/go/bin/go.exe run ./cmd/cs2-phases `
  --input ../../esl-challenger-league-season-52-europe-cup-6-misa-vs-mouz-nxt-bo3-FrnpfKbdBS_lppg7U-SOO5/mouz-nxt-vs-misa-m1-dust2.dem `
  --out ../../data/phases/dust2-phase-audit.json
```

Existing audited sidecars are `data/phases/{dust2,nuke,cache}-phase-audit.json`. Choose a new output filename when rerunning the producer.

From the repository root:

```powershell
& .venv/Scripts/python.exe -m cs2_data render-jobs `
  --parsed data/parsed/v2-audited/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773 `
  --phase-manifest data/phases/dust2-phase-audit.json `
  --round-id 3 --steam-id 76561198323592528 `
  --start-demo-tick 6000 --end-demo-tick 6320 --min-ticks 320 --limit 1 `
  --out data/jobs/dust2-competitive-shot-next.json
```

Both optional tick bounds must be provided together; the end is exclusive. The planner intersects them with alive intervals and recomputes command coverage on the resulting window.

The classifier requires matching canonical round boundaries, game phase 2 or 3 throughout the recorded active interval, match-started and nonwarmup flags, a competitive round-end reason, and a consistent scored result. A later rollback of the round total invalidates the lost scored rounds. Unknown evidence is excluded. The raw `cs_pre_restart` event is retained for inspection but is not itself a rejection condition: it appears before ordinary rounds in all three supplied demos. Round IDs are never used as phase evidence.

Each job includes its phase classification, reason codes, raw event references, source sidecar path, and sidecar SHA256. The loader checks the sidecar's demo SHA, parser version, complete status, timeline, and canonical round boundaries. `--allow-unverified-phase` explicitly enables diagnostic jobs for unknown or setup phases; it does not remove the recorded classification. `--allow-incomplete-commands` is a separate diagnostic override.

The prepared Dust2 job `data/jobs/dust2-competitive-shot-001.json` follows Ckanic (slot 9) for ticks `[6000, 6320)` in canonical round 3, competitive round 1. It contains one Glock-18 fire event at tick 6102, 320 commands for 320 ticks, and substantial aim and movement changes. Its `.selection.json` companion records observed events, ranges, and source file hashes.

Phase verification establishes the replay's competitive context. It does not establish video timing, POV fidelity, execution-clock alignment, or training readiness. Jobs remain `training_ready=false` until those independent requirements are satisfied. The classifier deliberately rejects ambiguous game formats rather than infer a competitive phase from round numbering or chat text.

Implementation references: pinned demoinfocs `common/gamerules.go` defines Pregame=1, StartGamePhase=2 and TeamSideSwitch=3; `events/events.go` defines the match/warmup/round/score events. The exact initial Dust2 observations are retained in `tests/fixtures/dust2-phase-v1.json`. Tests cover this captured sequence, nonordinal round IDs, score rollback, missing evidence, source mismatch, immutable outputs, and interval-specific command coverage.
