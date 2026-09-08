# Evidence-bound sample acceptance

## Publication history and current workflow

**The last published competitive corpus had 1,692 accepted samples and 68
rejected candidates across seven clips on Dust2 and Nuke.** Each sample contains
eight images and masked 32 Hz angular/control targets. Use the
[competitive workflow](COMPETITIVE_ACCEPTANCE.md), [tensor loader](TRAINING_DATASET.md)
and [the recorded milestone](progress/ACTION_COVERAGE_AND_THROUGHPUT.md).

The source-proof profile is now `cs2-competitive-replay-source-proof-v3`, while
the outer acceptance schema remains v2. The publications below bind earlier
proof and source-code hashes and have not been reverified under this change.
The current loader requires fresh numerical acceptance publications; trusting
the approved HUD setup does not upgrade historical partitions. Existing reports
remain unchanged, and the earlier 1,692 count is not a new acceptance result.

| Clip | Accepted | Rejected | Last published result |
| --- | ---: | ---: | --- |
| Dust2 round 8, ordinary | 310 | 10 | [Acceptance](../data/collections/action-coverage-001/batch/runs/7d85cdc5424882107fa8e918/acceptance/attempt-001/competitive_acceptance.json) |
| Nuke round 7, ordinary | 302 | 18 | [Acceptance](../data/collections/action-coverage-001/batch/runs/de80011735c3c92204241e4d/acceptance/attempt-001/competitive_acceptance.json) |
| Dust2 round 24, attack hint | 313 | 7 | [Acceptance](../data/collections/action-coverage-001/batch/runs/a5b61fe50beca1ffd70a15ef/acceptance/attempt-001/competitive_acceptance.json) |
| Nuke round 21, reload hint | 311 | 9 | [Acceptance](../data/collections/action-coverage-001/batch/runs/a88c3aa49c7b41fffe78d927/acceptance/attempt-001/competitive_acceptance.json) |
| Dust2 round 5, refreshed | 150 | 10 | [Acceptance](../data/batches/competitive-expansion-001/runs/df971dd315eeae8b6d3f6211/acceptance/attempt-003/competitive_acceptance.json) |
| Nuke round 3, refreshed | 153 | 7 | [Acceptance](../data/batches/competitive-expansion-001/runs/5ebb0db256cd61709ee245af/acceptance/attempt-003/competitive_acceptance.json) |
| Dust2 round 3 pilot, refreshed | 153 | 7 | [Acceptance](../data/accepted/dust2-competitive-controls-006-v3/competitive_acceptance.json) |

Those publications recorded **3,384 valid angular fields and 125,240 valid button fields**,
including positive reload labels. Unknowns remain masked. All 55 seconds of
source footage belong to one BO3 training group; validation/test remain empty.
The new Nuke round 7 capture retains eight histories rejected at an ambiguous
checkpoint frame, alongside incomplete histories and unsupported commands.
Rejection reasons can overlap and must not be summed as independent samples.

Earlier publications are preserved historical evidence and often reuse the same
images. The [three-clip expansion](progress/COMPETITIVE_EXPANSION.md) reported 456
accepted / 24 rejected; its first Nuke zero result was later reissued after fixing
an absolute-clock cap. The [single-command 016 pilot](../data/accepted/dust2-causal-016-v1/causal_acceptance.json)
reported 129 / 31. The [initial strict campaign](../data/validation-campaigns/dust2-three-player-v1/campaign_manifest.json)
reported 0 / 480. Zero counts below refer to these older contracts/captures, not the
last published corpus. Do not add historical republications to those counts.

These workflows have different target definitions. The historical strict workflow
requires proof of action timing inside its assigned frame intervals, including
the relevant fractional timing and visual transitions. Those requirements
remain unresolved for its recorded captures.

The historical `recorded_future_server_command_v1` profile predicts one
recorded future command from eight images. Trial 016 supplies complete packet
information bounds, pixel matching and first-person identity evidence. Both
commands contributing to the aim difference must begin strictly after the
image history's information bound. This establishes the narrower future target;
it does not establish every subtick's original timing or promote the old runs.

The historical command below also requires its original installed-binary path.
The new competitive workflow explicitly uses the recovered archive and should
be used with the current installation. Historical command reference:

```powershell
$parsed = 'data/parsed/v2-state-fixed/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773'
.venv/Scripts/python.exe -m cs2_data accept-causal-samples `
  --parsed $parsed `
  --dataset data/datasets/dust2-timing-016 `
  --network-clock data/clocks/dust2-three-clips-v1.json `
  --state-context data/context/dust2-context-v2.json `
  --out data/accepted/new-causal-review
```

Defaults are eight history frames and a two-frame target horizon, with no
previous-action input features. That historical manifest is `causal_acceptance.json`.
See [the synchronization guide](SYNCHRONIZATION.md#accept-and-load-the-first-training-subset)
for exact target semantics, outputs, retained source requirements and the loader
that independently recomputes acceptance. The 129 samples are a five-second
pilot; broader data collection remains unfinished. The new tensor loader uses
the separate competitive acceptance profile linked above.

## Historical `accept-samples` reference

Everything below documents the historical workflow, its requirements and its
original results. Its command, defaults and manifest differ from the competitive and historical future-command workflows above.

`accept-samples` writes a new partition of accepted and rejected temporal samples. It does not train a model or change the video, canonical commands, alignment, or their original readiness flags. A completed pipeline can legitimately produce zero accepted samples.

Run from the repository root, with the bundled FFmpeg directory on `PATH` so validation can decode and check the video:

```powershell
$env:PATH = (Resolve-Path '.tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin').Path + ';' + $env:PATH
$parsed = 'data/parsed/v2-audited/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773'
.venv/Scripts/python.exe -m cs2_data validate-clip --parsed $parsed --dataset data/datasets/dust2-competitive-006 --state-context data/context/dust2-context-v2.json --out data/validation/my-review
.venv/Scripts/python.exe -m cs2_data accept-samples --parsed $parsed --dataset data/datasets/dust2-competitive-006 --validation data/validation/my-review --state-context data/context/dust2-context-v2.json --out data/accepted/my-review
```

Both output directories must be new or empty. Acceptance obtains an exclusive output lock and publishes its completion manifest last. Existing outputs are never overwritten. If evidence or the validator implementation changes, generate a new validation report in another directory; acceptance recomputes validation and rejects stale or edited reports.

### Temporal sample contract

The defaults are `--history-frames 8 --target-horizon-frames 1`, with previous action features included. An aligned interval belonging to image `i` contains the proposed future commands between observation `i` and observation `i+1`, using `(start,end]`. These diagnostic assignments still require separate proof of execution and observation phase before acceptance.

For the first complete default window, observation image 8:

| Component | Exact indices |
| --- | --- |
| Image history | 1 through 8 |
| Previous action intervals corresponding to those images | 0 through 7 |
| Target action interval | 8 |
| Images excluded from features | 9 and later |

The preceding interval supplies the previous action feature for each image; its following target does not leak into that image's features. `--no-previous-actions` removes those features and their input quality requirements. Historical images still need valid identity, state, and capture evidence. `--target-horizon-frames N` uses future intervals `i` through `i+N-1` without adding their later images to the input history.

Every image position receives a candidate record, including early or late positions without enough history or future intervals. `eligible_window_count` counts positions with all configured windows available. It is independent of acceptance: complete windows can still fail quality or evidence checks. With 160 frames and the default configuration, 152 windows are structurally complete.

### Acceptance requirements

All requirements apply to the particular window:

- The completed dataset and validation report identify the same demo, round, Steam ID, slot, and clip. The parsed manifest, aligned files, timing sources, source phase audit, optional state context, and validation evidence must match their recorded hashes. Every aligned raw command must equal its original canonical row.
- The round must be reclassified as verified competitive play from the source phase audit. Embedded render-job flags alone cannot approve setup, knife, warmup, or discarded rounds. See [PHASES.md](PHASES.md).
- An observed canonical player-state row must exist for every tick spanning the image and mapped action domains, including conservative interval endpoints. Identity and alive state must be known; warmup, freeze, and pause must be known false. The entire interval must lie inside the live round.
- Inputs require present base, buttons, and view-angle messages, the original protobuf bytes, finite values, valid button masks, contiguous command numbers, and non-resetting client/server/demo clocks. Missing nested messages remain unknown. Omitted scalar values use protobuf defaults only inside a known-present parent message.
- Every used command needs a valid predecessor and valid normalized aim. Yaw/pitch deltas and effective mouse values are recomputed from canonical inputs and compared with the derived values. Negative, nonfinite, or greater-than-one subtick/history fractions reject affected windows; canonical values are never clamped or repaired.
- Capture integrity, pixel correspondence, POV identity, observation clock, execution clock, and visual fire/aim/movement/jump/crouch checks need positive evidence covering the exact sample scope. Execution proof also covers the exact used command-row IDs. Unknown checks and missing scopes never pass.

POV/action failures are local when the report contains independent passed evidence for every frame in another window. A wrong frame rejects intersecting windows; an unrelated frame's aggregate failure does not discard proven observations elsewhere. Capture/pixel integrity or timing failures cannot be waived this way. Measured negative shot evidence also rejects any window using that command even if a broader check passes.

The optional `cs2-context-v2` sidecar can fill a canonical null pause state only when it identifies the same source demo, is bound to the validation report, and explicitly covers the whole requested interval. The verifier requires the pinned parser/property prefix, the supported warning policy, and zero evidence-loss warnings. Pause is recomputed from all five observed game-rule flags. Missing flags, ambiguous ticks, gaps, or disagreement with known canonical state reject the sample. Legacy v1 context remains diagnostic-readable but cannot fill unknown canonical pause. This handles the supplied parser's historical wrong-prefix pause lookup without changing existing canonical Parquet files.

### Outputs and reason codes

`accepted_samples.jsonl` and `rejected_samples.jsonl` are disjoint and contain every candidate exactly once. Records include:

- Stable sample ID, source identity, clip ID, configuration, and observation frame index.
- Exact history image indices, previous-action interval indices and command IDs, target interval indices and command IDs, and all checked command IDs.
- Exact normalization predecessor command IDs and checked state tick bounds.
- `window_complete`, explicit `reason_codes`, and `training_ready` for that record only.

`acceptance_manifest.json` binds source hashes and output file hashes and reports candidate, complete-window, accepted, rejected, and training-ready-sample counts. It has no global `training_ready=true` flag. Only records in the accepted file are approved for the declared feature/target contract.

Representative reasons include `missing_previous_action_context`, `missing_buttons_present`, `invalid_subtick_fraction`, `normalized_aim_invalid`, `is_paused_unknown`, `competitive_phase_unverified`, and `validation_observation_clock_unknown`. Multiple reasons can apply to one window, so reason counts do not sum to the candidate count. Artifact corruption or a stale validation report aborts publication instead of producing misleading accepted/rejected records.

### Reproducible campaigns

`cs2_data.campaign.summarize_campaign(acceptance: list[Path], out: Path)` summarizes a list of existing acceptance directories into a fresh `campaign_manifest.json`. It rehashes all partitions and source manifests, calls the current validation verifier, and reruns the acceptance policy in a private temporary directory. The regenerated acceptance manifest must equal the original, including partition hashes. This catches edited counts, self-consistent forged sample promotions, and reports made stale by new evidence or policy.

The campaign rejects repeated paths, copied identical artifacts, overlapping sample IDs, and repeated source pipeline hashes, including the same dataset accepted with different history configurations. Separate replay captures can be compared as separate observations; unique demo, round, and player counts describe their actual diversity.

The summary reports actual candidate, complete-window, accepted, and rejected counts, source hashes, per-clip required-check statuses and scopes, and native transition observations. Aggregate check status counts count clips. A passed aggregate can still cover only some frames: exact scoped frame counts and local evidence remain visible. Transition observations are recorded separately from proof of action/observation phase. The campaign itself always has `training_ready=false` and cannot approve additional samples.

The historical [three-player Dust2 campaign](../data/validation-campaigns/dust2-three-player-v1/campaign_manifest.json) revalidated these artifacts from captures 008-010:

| Acceptance | Round | Steam ID | Candidates | Complete windows | Accepted |
| --- | --- | --- | ---: | ---: | ---: |
| [008](../data/accepted/dust2-validation-008-state-fixed-v2/acceptance_manifest.json) | 3 | 76561198323592528 | 160 | 152 | 0 |
| [009](../data/accepted/dust2-validation-009-state-fixed-v2/acceptance_manifest.json) | 4 | 76561198407480534 | 160 | 152 | 0 |
| [010](../data/accepted/dust2-validation-010-state-fixed-v2/acceptance_manifest.json) | 5 | 76561198254835598 | 160 | 152 | 0 |

All three historical captures pass integrity, native pixel correspondence, and strict first-person POV checks. Both clock checks and independent transition calibration remain unknown in these reports. Across their 480 candidates, 456 windows are complete and all 480 are rejected. Local input quality also rejects 108 windows with missing button messages and 45 with invalid subticks; a crouch mismatch affects 9 windows. These counts overlap with the timing rejections. The historical 016 pilot is a separate capture and is not included in this table or campaign.

To repeat the campaign with the same inputs:

```powershell
cs2-data summarize-campaign --acceptance data/accepted/dust2-validation-008-state-fixed-v2 data/accepted/dust2-validation-009-state-fixed-v2 data/accepted/dust2-validation-010-state-fixed-v2 --out data/validation-campaigns/my-repeat
```

Or use the Python API:

```python
from pathlib import Path
from cs2_data.campaign import summarize_campaign

summarize_campaign(
    [Path(f"data/accepted/dust2-validation-{index:03d}-state-fixed-v2")
     for index in (8, 9, 10)],
    Path("data/validation-campaigns/my-repeat"),
)
```

FFmpeg must remain on `PATH` because current validation decodes the bound videos again. Use a new output directory; existing campaign reports remain immutable.

### Historical supplied-demo baseline

The first actual acceptance run used [Dust2 capture 006's dataset](../data/datasets/dust2-competitive-006/pipeline_manifest.json), [validation 006-v2](../data/validation/dust2-competitive-006-v2/clip_validation.json), and [the observed game-rule context](../data/context/dust2-context-v1.json). Its immutable result is [acceptance 006-v1](../data/accepted/dust2-competitive-006-v1/acceptance_manifest.json): **160 candidates, 0 accepted, 160 rejected**.

All windows lacked independently proven observation/execution timing, POV, and visual action evidence in that older capture. Additionally, 67 windows touch commands without a present buttons message, and 9 touch the recorded negative subtick. The first 7 lack a full image history, and the first 8 lack the complete previous-action context. Competitive phase and pause context passed the then-current checks. This historical report predates validator v2 and its context warning audit; regenerate validation and acceptance under current code before including an older capture in a new campaign.

The known shot supports one scoped timing observation: weapon last-shot time equals `server_tick_executed - 1 + attack_subtick_when` for that press. An integer execution tick is a command boundary, so its placement alone does not prove every action inside that command occurs after the image. The current pilot adds independently audited packet bounds and a future-command target; it retains this limitation on exact fractional timing and leaves the older results unchanged.

Tests cover valid synthetic acceptance, temporal feature/target separation, local bad-frame contamination, unknown and incorrectly scoped evidence, inactive/unknown state, missing protobuf parents, command continuity, aim mismatches, invalid subticks, context gaps and unknown flags, tampered artifacts, immutable outputs, and failure before publication. The synthetic passed evidence exists only in test fixtures; production has no override to bypass validation.
