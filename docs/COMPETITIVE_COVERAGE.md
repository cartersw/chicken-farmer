# Competitive action coverage

`cs2_data.competitive_coverage` finds competitive player clips worth rendering and
reports usable recorded-control labels after acceptance. Candidate discovery is a
scheduling step. It cannot approve frames or training samples.

Use [collection preparation](COMPETITIVE_COLLECTION.md) to combine source pools,
extract selected clock windows and produce a protected batch automatically.
The [first collection milestone](progress/ACTION_COVERAGE_AND_THROUGHPUT.md)
records real captures and the distinction between action hints and accepted labels.

## Discover candidates

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_coverage discover `
  --parsed data/parsed/v2-state-fixed/DEMO_SHA256 `
  --demo path/to/match.dem `
  --phase-manifest data/phases/match.json `
  --clip-ticks 640 --max-clips 8 --ordinary-fraction 0.5 `
  --out data/validation/match-action-discovery.json
```

The output must be new. Clips contain 32–1280 even demo ticks, or 0.5–20 seconds
at the supported 64 Hz source rate. The default remains 320 ticks. Longer clips
amortize game startup across more footage but require longer uninterrupted alive
windows. Discovery does not launch CS2 or change settings.

The scanner reads canonical v2 player states and commands in bounded Arrow batches.
It requires retained competitive phase evidence, continuously alive identity,
known unpaused state, and full fixed-length windows. Death, freeze time, setup play,
unknown pause state, identity changes, and missing state end a window. A second
streaming pass measures command coverage and raw button evidence. Intervals whose
command gaps exceed four demo ticks cannot be selected.

It looks for these recorded-control hints:

| Hint | Scheduling evidence |
| --- | --- |
| Reload | Present raw button parent contains a held reload bit or recorded reload activity. |
| Sustained primary attack | At least eight adjacent recorded commands hold primary attack, with consecutive command numbers and execution ticks. This does not establish bullets fired. |
| Movement start/stop | A consistent consecutive command boundary changes from no held WASD buttons to at least one, or back. Changing direction while continuing to move is separate. |
| Crouch/jump | Present raw button parent contains the corresponding held bit or recorded activity. |

The first 20 ticks of a candidate cannot supply its action hints. This avoids
selecting an action that occurs entirely before the image history and future target
window are available. Acceptance still establishes the actual frame/command timing.

The three button planes are compared with their raw protobuf fields. An absent
button parent is unknown. A missing optional scalar can mean zero only inside a
present parent. Unsupported command flags and inconsistent projections stay
unknown. Missing command baselines and gaps cannot manufacture a movement edge or
bridge a sustained hold.

State corroboration is reported separately: same-weapon ammunition decreases,
reload-like magazine/reserve transfers, crouching state, movement transitions and
ground-to-air transitions. These are useful checks and are not physical-key labels.

## Ordinary play and diversity

At least `ceil(selected_count * ordinary_fraction)` clips are selected without
looking at action hints. The default ordinary share is 50%; the supported minimum
is 25%. Ordinary means action-blind sampling, not idle or action-free footage.
The remaining slots favor underrepresented positive hints. Selection rotates
sources, rounds and players, with deterministic hash tie-breaking.

Discovery writes both selected clips and a bounded cross-source candidate pool.
Pool members retain `ordinary_pool_eligible`: only candidates selected by the
action-blind policy can fill ordinary slots when combining pools. Missing or
insufficient ordinary membership fails explicitly. A targeted candidate is never
reclassified as an ordinary sample to satisfy the share.

Python integration:

```python
from cs2_data.competitive_coverage import (
    discover_candidates, choose_candidates, source_selections,
)

report = discover_candidates(parsed, demo, phase, fresh_report_path,
                             clip_ticks=640, max_clips=8)
# Combine candidate_pool from independently identified demo reports, if needed.
selected = choose_candidates(report["candidate_pool"], max_clips=4)
selections = source_selections(selected)
```

`selections` contains only `start_demo_tick`, exclusive `end_demo_tick`, `round_id`
and integer `steam_id`, matching a source entry in
[the protected batch manifest](COMPETITIVE_BATCH.md). When combining sources, group
selected entries by `demo_id` before producing each source's selections. Clock
evidence must cover the selected windows and the batch worker's margins.

Reports bind the original demo, canonical tables, phase sidecar and discovery
implementation to their SHA256 values. They record canonical parser warnings and
set both `training_ready` and `original_source_reconstruction_verified` to false.
The later acceptance stage independently reconstructs the original source and
checks timing, identity, HUD review, pixels and each supported label field.

Resource limits are explicit: 50 million rows per streamed state/command table,
10,000 rounds, 100,000 in-memory fixed windows, 128 exported pool candidates plus
at most 16 source selections. Limits fail rather than silently truncating the
source scan. The pool supports scheduling diversity; it is not a claim that the
corpus has been sampled without bias.

## Count accepted labels

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_coverage accepted `
  --acceptance data/accepted/first-clip `
  --acceptance data/accepted/second-clip `
  --out data/validation/accepted-action-coverage.json
```

Every acceptance publication is independently loaded and revalidated. The report
counts valid positive, valid negative and unknown cells for each button field,
plus valid/nonzero angular targets and per-clip positive/valid sample counts.
Unknown tensor placeholders do not become negative examples. Duplicate sample
identities or repeated observations are rejected to avoid double-counting an
acceptance republished under another proof. The report supports up to 128
publications and 100,000 accepted samples per run and refuses to overwrite output.

These counts describe overlapping samples and recorded command fields. They are
not independent episodes, numbers of physical key presses or successful gameplay
actions. No training runs in this module. Independent match series remain necessary
for validation and test data.
