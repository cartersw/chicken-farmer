# Competitive control acceptance

The current replay path uses `cs2-14180-competitive-replay-v1`, separate from
both the historical 14178 replay profile and local 14180 input calibration.
`accept-competitive-controls` prepares eight-image histories with two future
recorded command boundaries per 32 Hz target. It does not train a model.

The current source-proof profile is `cs2-competitive-replay-source-proof-v3`;
the outer acceptance schema remains v2. Earlier accepted publications bind old
proof and source-code hashes, so the current loader requires fresh numerical
acceptance publications. The approved HUD setup does not silently upgrade old
partitions. No historical reports have been rewritten.

The [last published seven-clip collection](progress/ACTION_COVERAGE_AND_THROUGHPUT.md)
had 1,692 accepted samples. That count was verified under the earlier proof,
not reverified by this HUD policy change. [Publication history](ACCEPTANCE.md)
retains those results; regenerate acceptance before current training use.

## Evidence required

The acceptance loader rechecks the original demo, canonical Parquet hashes,
native message clocks, packet information bounds, first-person identity,
original image pixels, competitive phase, live state and pause evidence.
It also reconstructs the complete original demo with the pinned extractor and
compares commands, player state, rounds and events, including raw protobuf
bytes, nullable presence and source order. Separate pinned phase/context tools
regenerate competitive eligibility and pause evidence. Commands must independently match a
unique live packet envelope; seek checkpoints do not establish their support.

The current renderer's eight binary hashes are separate from the older demo's
command processing profile. The exact original inspected 14178 server binary
was recovered into `data/native-profiles/recovered-14178-v1/`, together with
the original inspected client and engine. All three match their previously
recorded SHA-256 hashes. They were downloaded into the workspace using the
existing local Steam depot manifest; no old binary was installed into CS2.
See `recovery_manifest.json` in that directory and
[the original command audit](SERVER_COMMAND_SUPPORT.md).

The maintained current packet reader/filter comparison is retained at
`data/native-profiles/current-competitive-replay-v1/binary_comparison.json`.
Fixed profile selection alone never establishes that a capture passed: actual
packet bytes and native observations must still agree with the source demo.

## Targets and limits

Let **B** be the largest verified information ceiling across the eight images.
The selected normalization predecessor executes at B+2, and the two target
commands execute at B+3 and B+4. Their enclosing supports are respectively
[B+1,B+2], [B+2,B+3] and [B+3,B+4]. Every contributing command therefore begins
strictly after the information already consumed for the images.

The angular target sums two adjacent yaw differences with wrap handling and
ordinary pitch differences, in degrees. It is independent of raw mouse-count
conservation. This is a
deliberately delayed prediction target: it does not establish the player's
physical input time or the controller latency needed at deployment.

The v2 profile checks the recovered 14178 server's button names, native state
enum, transition table and protobuf conversion separately from local keyboard
calibration. Each control field also needs an exact reconstructed command row,
unique live envelope, present button parent and consistent recorded boundaries.
Only supported fields receive true masks. These are recorded control states
that the adapter can express using mouse/keyboard controls; they do not identify
the player's physical keypress times. Exact event count, order and subtick times
remain unavailable. A sample can have usable angular fields while every button
field is masked; its masks define what a training loss may use.

The v2 source verifier replaces the fixed Dust2 eligibility hashes with fresh
full-source reconstruction. The current packet scanner supports later rounds
beyond tick 20,000, with an explicit one-million-command resource bound; the
historical replay profile retains its original scope. Missing delta baselines
remain missing, and rejected samples retain reason codes. A process-local
source cache is reused only after rehashing every source, table, sidecar and
tool dependency. A saved report or rehashed edited manifest cannot authorize
another source. See [batch processing](COMPETITIVE_BATCH.md).

Historical v1 acceptance publications remain archived. Because opening an
acceptance recomputes the current implementation, use a fresh v2 publication
after this change rather than editing the old report or its hashes.

The user approved the current capture HUD setup after viewing the nine-clip
round trial. Supported captures now use `user_approved_capture_setup` as their
HUD acceptance basis, bound to the fixed renderer/plugin/resource metadata.
There is no recurring manual HUD gate, automated overlay detector or required
spot check. This is an explicit setup assumption, not a claim that every frame
was inspected. Incompatible setup metadata fails normal compatibility checks;
it does not automatically trigger a new visual-review process.

Numerical timing, source reconstruction, first-person identity, original-image
pixel correspondence and integrity checks remain required. Existing manual
review records are historical evidence; the [HUD review tool](HUD_REVIEW.md)
remains an optional diagnostic. Routine batches do not create contact sheets.

## Commands

The protected current-build job must explicitly set:

```json
{"competitive_replay_profile": "cs2-14180-competitive-replay-v1"}
```

Use a complete normal job with that additional field, then render and run
`process-render` into fresh directories using the
[Windows workflow](WINDOWS_RENDERING.md). Put the bundled FFmpeg directory
on the process PATH before preparing timing or revalidating video evidence.

The explicit current synchronization check is:

```powershell
.venv/Scripts/python.exe -m cs2_data audit-synchronization `
  --parsed data/parsed/PARSED-DEMO --dataset data/datasets/NEW-CLIP `
  --network-clock data/clocks/SOURCE-CLOCK.json `
  --native-profile cs2-14180-competitive-replay-v1 `
  --out data/validation/NEW-SYNCHRONIZATION
```

For the separate competitive acceptance publication:

```powershell
.venv/Scripts/python.exe -m cs2_data accept-competitive-controls `
  --parsed data/parsed/PARSED-DEMO --dataset data/datasets/NEW-CLIP `
  --network-clock data/clocks/SOURCE-CLOCK.json `
  --state-context data/context/SOURCE-CONTEXT.json `
  --out data/acceptance/NEW-COMPETITIVE-PARTITION
```

The publication contains `competitive_acceptance.json` and exact accepted and
rejected JSONL records. Opening it with `load_competitive_acceptance` recomputes
the evidence and partitions. Editing a readiness flag does not authorize data.
The [tensor loader](TRAINING_DATASET.md) uses that boundary and keeps the whole
BO3 series in one split. Additional independent series are needed for validation
and test data.
