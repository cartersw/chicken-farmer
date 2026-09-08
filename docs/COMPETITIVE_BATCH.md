# Bounded competitive batch processing

`cs2_data.competitive_batch` plans several short clips and runs the existing
protected Windows capture and evidence pipeline in sequence. It does not train
a model. Clips use 32 fps, 1280×720 and at most 1280 demo ticks (20 seconds) under
the explicit current competitive profile. The default remains 320 ticks (five
seconds), with three jobs per plan/invocation. For automatic action-aware
selection and clock preparation, use [competitive collection](COMPETITIVE_COLLECTION.md).

Planning selects complete chunks from the competitive, alive player windows
produced by `render_jobs`. It rotates sources, rounds and POVs before selecting
another chunk from the same window. Source files, job specifications and their
hashes are retained in the plan. Each chosen clip also needs network-clock
evidence covering its requested interval with a 16-tick margin on each side.
Selection is scheduling evidence; acceptance independently checks the source,
actual images, POV, clock bounds, live state and available target fields.

## Source manifest and planning

Create a source manifest with profile `cs2-competitive-batch-sources-v1`.
Paths may be absolute or relative to this manifest. `selections` is optional;
each selection can restrict the round, Steam ID and requested interval. Supply
both tick endpoints together. Intervals beyond tick 20,000 are supported by the
planner; their evidence must pass the generalized source verifier.

```json
{
  "schema_version": 1,
  "profile": "cs2-competitive-batch-sources-v1",
  "sources": [
    {
      "source_id": "dust2",
      "parsed": "ABSOLUTE-PATH-TO-PARSED-DEMO",
      "demo": "ABSOLUTE-PATH-TO-SOURCE.dem",
      "phase_manifest": "ABSOLUTE-PATH-TO-PHASE.json",
      "network_clock": "ABSOLUTE-PATH-TO-NETWORK-CLOCK.json",
      "state_context": "ABSOLUTE-PATH-TO-CONTEXT.json",
      "selections": [
        {
          "round_id": 5,
          "steam_id": 76561198254835598,
          "start_demo_tick": 22300,
          "end_demo_tick": 22620
        }
      ]
    }
  ]
}
```

From the project directory:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_batch plan `
  --sources data/jobs/competitive-expansion-sources-001.json `
  --out data/batches/competitive-expansion-NEW --max-jobs 3 --clip-ticks 320
```

The output must be fresh. Planning reads complete source tables and can take
several minutes. It writes candidate intervals, individual job JSON files and
`batch_plan.json`; it does not change game files or launch CS2.

The prepared real plan at `data/batches/competitive-expansion-001` contains:

| Source | Round | POV | Requested demo ticks |
| --- | --- | --- | --- |
| Dust2 | 5 | ay0k (`76561198254835598`) | 22300–22620 |
| Nuke | 3 | Ckanic (`76561198323592528`) | 10300–10620 |

These are exclusive end ticks. A prepared plan does not mean its captures or
training samples have passed. Consult the batch summary for actual results.

## Inspect, execute and resume

The runner defaults to read-only inspection. It checks the source/job hashes
and revalidates any retained stage artifacts without creating a journal or
starting another stage:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_batch run `
  --plan data/batches/competitive-expansion-001
```

To execute, close normal CS2 first and use the existing compatible native plugin:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_batch run `
  --plan data/batches/competitive-expansion-001 --execute --max-jobs 2 `
  --plugin tools/renderer/build/plugin-windows/Release/server.dll `
  --ffmpeg .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffmpeg.exe `
  --ffprobe .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffprobe.exe
```

`--game-dir`, `--steam-dir` and `--steam-user-id` are available when the worker
cannot select the installation/account automatically. The renderer retains its
idle-game requirement, exact current binary profile, `-insecure` replay launch,
private settings/HUD staging and journaled restoration. The batch does not change
Steam mode or Cloud preferences. It does not accept arbitrary launch arguments
or an alternative renderer/proof callback.

Each job advances through these durable stages:

1. Protected Windows render, followed by source, frame and restoration checks.
2. Diagnostic ingestion, timing, alignment and viewer preparation.
3. Current-profile synchronization audit.
4. Original-image HUD contact sheets and source-bound review material.
5. Independent competitive acceptance and accepted/rejected sample partitions.

Stage 4 pauses at `pending_visual_review` until a complete-image review is
registered by the acceptance verifier. Generated sheets, successful mounting
and editable approval flags do not approve a capture. Inspect every original
image, record the reviewed hashes through the existing HUD review workflow,
then repeat the same runner command. Acceptance has not been published while
the run is waiting at this review boundary.

Successful prior stages are checked again and reused. The runner records
attempts in `batch_state.json`, with file hashes and stage outcomes, and writes
`batch_summary.json` with per-job statuses and accepted/rejected sample counts.
Failed attempts remain under `runs/<job-id>/<stage>/attempt-NNN`. A retry uses
another fresh attempt directory; it never overwrites a failed capture.

Use `--retry-failed` to retry a failed stage after addressing its cause. An
interrupted stage with complete artifacts can be adopted only after its real
stage validator succeeds. Missing or incomplete artifacts need an explicit
retry. Corrupted completed artifacts are reported and preserved rather than
silently replaced. A changed acceptance implementation can issue a fresh
acceptance attempt after checking the original source again, without rerendering
valid unchanged frames.

If a capture still needs restoration, the batch stops before launching another
game. Use the existing worker's explicit recovery command against that attempt's
`gameinfo-recovery.json`, as documented in [WINDOWS_RENDERING.md](WINDOWS_RENDERING.md),
then retry. The batch does not automatically kill an unrelated game or infer
permission to overwrite changed normal preferences.

The process-held batch lock releases after termination, so a crashed Python
process does not leave an unrecoverable lock file. Worker settings/GameInfo
journals retain their own recovery requirements.

## Completed two-map run

`data/batches/competitive-expansion-001/` completed both protected captures and
all five stages. Dust2 round 5/ay0k has 150 accepted samples and 10 rejects;
Nuke round 3/Ckanic has 153 accepted samples and seven rejects. The current
publications are each under `acceptance/attempt-002/`.

The initial Nuke attempt is retained with zero accepted samples: an absolute
native-clock cap rejected its timestamps above 1,024 seconds. The corrected
finite/32 Hz cadence check caused fresh acceptance attempts, reusing the
unchanged captures. All 320 new original frames were visually reviewed. Both
sessions preserved the selected personal settings and installed game/HUD
hashes. See [the milestone](progress/COMPETITIVE_EXPANSION.md) for the combined
456-sample result including the reverified earlier pilot and its real tensor
batch.

## Scope

The current runner is deliberately bounded to 1–24 jobs per invocation and
32–1280 even ticks per clip. It supports multiple sources, rounds and POVs, but
does not schedule an entire match automatically, approve HUD images, invent
missing target fields or provide independent validation/test matches. Accepted
partitions can subsequently be opened by the [tensor loader](TRAINING_DATASET.md).

Longer intervals stay within one competitive alive window and one POV. The
worker receives the exact planned length; it cannot silently truncate a ten-
or twenty-second batch clip to the historical default. The sequential lifecycle
still restores settings after every launch. The batch summary now records
diagnostic per-stage execution/verification time and compares planned launches
with the equivalent number of five-second captures.
