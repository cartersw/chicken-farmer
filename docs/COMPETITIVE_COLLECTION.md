# Action-aware competitive collection

`python -m cs2_data.competitive_collection` prepares a complete bounded batch:
discover competitive alive windows, select ordinary and action examples,
extract clock evidence for their exact intervals, and write protected batch
jobs. Preparation launches no game and grants no sample acceptance.

The [action-coverage milestone](progress/ACTION_COVERAGE_AND_THROUGHPUT.md)
records the first real ten-second collection, measured throughput, retained
checkpoint rejection, visual review and settings checks.

The default is four ten-second clips, with half reserved for ordinary play.
Ordinary clips are selected without inspecting their actions, with diversity
across sources, rounds and players. The remaining clips cover recorded reload,
sustained primary attack, movement starts/stops, crouch and jump hints. Ordinary
means action-blind selection, not an idle-player filter. Source pools retain
which candidates are eligible for that ordinary share when combined across maps.

## Prepare sources and a plan

Supply one entry per original demo. Paths are absolute or relative to the
source manifest. Clock files and tick selections are generated automatically.

```json
{
  "schema_version": 1,
  "profile": "cs2-competitive-collection-sources-v1",
  "sources": [
    {
      "source_id": "dust2",
      "parsed": "ABSOLUTE-PATH-TO-CANONICAL-PARSED-DEMO",
      "demo": "ABSOLUTE-PATH-TO-ORIGINAL.dem",
      "phase_manifest": "ABSOLUTE-PATH-TO-PHASE.json",
      "state_context": "ABSOLUTE-PATH-TO-CONTEXT.json"
    }
  ]
}
```

From the repository directory:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_collection `
  --sources data/jobs/action-coverage-sources-001.json `
  --out data/collections/action-coverage-NEW `
  --clip-ticks 640 --max-jobs 4 --ordinary-fraction 0.5
```

The output must be fresh. It contains per-source `discovery/` reports, fresh
`clocks/`, `batch_sources.json`, the normal `batch/` plan, and a final
`collection_plan.json` with selected actions and preparation time. A failed
preparation retains diagnostics without publishing a completed collection plan.
The selected source/player/round/interval set must exactly match the produced
batch, so filtering cannot silently change the ordinary/action mixture.

`--reuse-discovery DIRECTORY` optionally reuses source-bound discovery JSONs
named `<source_id>.json`. It checks every required canonical/source/tool hash,
report checksum, source identity, clip length and ordinary-pool marker first.
Discovery is scheduling evidence only; a stored report cannot approve labels.
Changes to discovery code or its dependencies require fresh discovery.

The collection supports 1-8 sources and 1-16 selected clips. Clip lengths must
be even 32-1280 ticks (0.5-20 seconds at 64 Hz). The ordinary share is 0.25-1;
the default is 0.5. Each source's selected clock windows are merged with a
32-tick margin and must fit the clock producer's 32-window/20,000-tick budget.
The default four ten-second clips fit comfortably within those bounds.

## Capture, review and accept

Close normal CS2, then run the existing protected worker through the batch:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_batch run `
  --plan data/collections/action-coverage-NEW/batch --execute --max-jobs 4 `
  --plugin tools/renderer/build/plugin-windows/Release/server.dll `
  --ffmpeg .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffmpeg.exe `
  --ffprobe .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffprobe.exe
```

This uses one game launch per contiguous clip. Four ten-second clips need four
launches; capturing the same durations as five-second chunks would need eight.
It does not keep a game running across different players or rounds. The current
profile supports the longer bounded originals and their disk budget; historical
profiles retain their five-second/1 GiB limits. Settings restoration, exact
native profile checks, frame guards and complete visual review still apply.

Open each generated HUD `index.html`, inspect every sheet, and consult original
images where needed. Generated material remains unapproved. Register the exact
completed visual review through the existing acceptance workflow, then repeat
the same batch command. The runner resumes without rerendering valid captures.
See [HUD review](HUD_REVIEW.md) and [batch lifecycle/recovery](COMPETITIVE_BATCH.md).

`batch_summary.json` includes diagnostic stage timings and call counts for the
current invocation, alongside accepted/rejected counts. Timing is not part of
acceptance evidence. Repeated source packet scans use bounded process-local
reuse with full source/code rehashing; independently verified labels still
control the [accepted action coverage report](COMPETITIVE_COVERAGE.md).

Positive reload input does not necessarily mean a completed reload: a player
can press reload with a full magazine. Ammo and movement-state observations are
reported separately from recorded button hints. Unknown parents, missing
baselines and unverified fields remain masked. Whole-series separation and
additional independent matches are still required for validation/test.
