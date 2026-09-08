# Competitive training tensors

`cs2_data.training_dataset.CompetitiveTrainingDataset` loads the new competitive
acceptance profile. Opening it recomputes the acceptance evidence and compares
the stored partition with that result. An edited `training_ready` flag or a
rehashed label file cannot substitute for that verification.

This stage prepares data. It does not run model training.

## Tensor interface

Each sample returns only these tensors:

| Tensor | Sample shape | Meaning |
| --- | --- | --- |
| `images` | `[8, 3, H, W]` | Eight ordered RGB images, oldest first, float32 in `[0,1]` |
| `aim_target` | `[2]` | Recorded yaw and pitch changes, in degrees |
| `aim_mask` | `[2]` | Boolean availability of those angular targets |
| `button_target` | `[8, 10]` | Scoped button fields, represented as float32 zero/one |
| `button_mask` | `[8, 10]` | Boolean availability for each button field |

Button order is `forward, back, left, right, crouch, jump, attack1, reload`.
Field order is `held_start, held_mid, held_end, net_changed, net_pressed,
net_released, recorded_activity_present, unresolved_rapid_activity,
net_changed_tick0, net_changed_tick1`.

Only `images` are model observations. Raw commands, positions, player identity,
future images, previous actions and private game state are absent from the
tensor inputs. `sample_provenance(index)` provides a separate diagnostic record.

An unavailable target is represented by a zero placeholder and a false mask.
A known released button also has value zero, with a true mask. Loss computation
must apply these masks; treating every zero as a measured negative would corrupt
the labels. Exact event counts, ordering and subtick times are omitted entirely.
Competitive button masks are enabled per field and exact command row by the
separate original-14178 source audit. Missing parents, unsupported fields or
inconsistent recorded boundaries stay masked even when angular targets pass.
The local synthetic keyboard calibration alone cannot authorize these fields.

## Image and temporal checks

The loader requires exactly eight consecutive history indices ending at the
declared observation. Their conservative information bounds must be ordered.
All three command supports—normalization predecessor and two targets—must begin
strictly after every input image's bound. The target pair must be consecutive
and match its attached raw command provenance.

Original TGA bytes are hashed and decoded from the same in-memory snapshot.
RGB order and image orientation are preserved. Native resolution is the default.
An explicit `image_size=(height, width)` uses bilinear resizing with antialiasing;
the result is clamped to `[0,1]` to remove float32 filter roundoff at saturated
pixels. Resizing does not change targets or temporal selection.

Source evidence is fully checked when the dataset opens. Later accesses rehash
the acceptance partition and grouping metadata, check source size/mtime, and
hash the eight actual image files. This avoids repeatedly parsing an entire
match for each training sample while still detecting ordinary source changes.

## Whole-series splits

The required grouping sidecar uses profile `cs2-series-grouping-v1`:

```json
{
  "schema_version": 1,
  "profile": "cs2-series-grouping-v1",
  "series": [
    {
      "series_id": "stable-series-identity",
      "match_id": "canonical-match-identity",
      "demo_ids": ["complete-demo-sha256-here"]
    }
  ]
}
```

Each demo appears once, and one match cannot be divided between series groups.
The match identity must agree with the independently checked canonical manifest.
Series identity is curated source metadata; it is not inferred from screenshots.

The split uses the full SHA-256 integer of
`cs2-series-split-v1 + NUL + default-v1 + NUL + series_id`, modulo 10,000.
Buckets below 8,000 are training, below 9,000 are validation, and the rest are
test. Adding clips, players or maps does not move an existing series. Sample
shuffling happens only after this grouping decision.

`data/training/esl-misa-mouz-series-v1.json` groups all three supplied Dust2,
Nuke and Cache demos into their one BO3. This series maps to training. Validation
and test are therefore empty until additional independent series are supplied;
the loader never fills them with another player or round from this BO3.

## Current action-coverage batch

The [current report](../data/training/action-coverage-001/batch_report.json)
combines seven freshly verified publications with **1,692 accepted samples**,
3,384 available angular fields and 125,240 available button fields.
The [coverage report](../data/validation/action-coverage-001/accepted-control-coverage.json)
counts known positive/negative fields separately from unknowns.

The four illustrative samples respectively include valid positive reload, forward,
primary attack and reload targets. PyTorch 2.8.0+cpu saves RGB `[4,8,3,180,320]`, aim `[4,2]` and
buttons `[4,8,10]`. There are 8 valid angular fields and 320
valid button fields, including 24 positives. All values are finite;
RGB remains in `[0,1]` and a `weights_only=True` reload matches exactly.

Whole-series splits are **1,692 training / 0 validation / 0 test**. These
samples exercise loading and masks, not evaluation. The current corpus needs
independent competitive series and wider weapon/action coverage. Exact event
channels remain absent; jump activity can occur between sampled held-state boundaries.
See [the milestone](progress/ACTION_COVERAGE_AND_THROUGHPUT.md) for every
publication and retained rejection. The earlier [three-sample report](../data/training/competitive-expansion-001/batch_report.json)
is historical and reuses frames; do not add it to the current totals.

## Creating a batch

PyTorch is optional for metadata verification and required for tensor loading.
The `training` extra pins `torch==2.8.0`; this workspace uses its CPU build.
The existing standard-library TGA decoder requires neither Pillow nor NumPy.

After a fresh competitive acceptance directory exists:

```powershell
$env:PATH = (Resolve-Path '.tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin').Path + [IO.Path]::PathSeparator + $env:PATH
.venv/Scripts/python.exe -m cs2_data.training_dataset `
  --acceptance data/acceptance/NEW-COMPETITIVE-PARTITION `
  --series-groups data/training/esl-misa-mouz-series-v1.json `
  --output data/training/first-competitive-batch-NEW `
  --batch-size 2 --height 180 --width 320
```

The output directory must be fresh. It receives `batch.pt`, containing only the
tensor dictionary, and `batch_report.json`, recording shapes, masks, source
identities, split counts and hashes. The class also works with PyTorch's regular
`DataLoader`; use `num_workers=0` for the first verification batch.

## Historical first batch

The previously verified real pilot is
[`first-competitive-batch-002`](../data/training/first-competitive-batch-002/batch_report.json),
created from `data/accepted/dust2-competitive-controls-006-v1` with PyTorch
`2.8.0+cpu`. Its acceptance contains **153 samples**, with seven initial frames
rejected because they lack a complete eight-image history. All 153 are in the
training split; validation and test contain zero samples from this one BO3.
Across the accepted partition, all 306 angular fields are available and all
competitive button fields remain masked.

The saved two-sample batch has image shape `[2,8,3,180,320]`, angular target and
mask shapes `[2,2]`, and button target and mask shapes `[2,8,10]`. It uses
observation frames 7 and 8 with histories 0–7 and 1–8. The four angular targets
are valid zeros for these two particular samples; the 160 button fields are
unavailable zero placeholders. An independent
[`weights_only` reload check](../data/training/first-competitive-batch-002/batch_reload_verification.json)
confirmed tensor keys, shapes, masks, source identities, finite values and the
exact image range `[0.0576855838,1.0]`.

`first-competitive-batch-001` is preserved as the earlier diagnostic. It exposed
806 resized values one float32 step above one (`1.0000001192`), prompting the
explicit clamp and saturated-pixel regression. Use batch002 for the completed
historical verification result. The action-coverage batch above is the current result.
Re-running the command requires another fresh output name.

Passing this step establishes that the accepted fields can be loaded into a
training batch. It does not establish exact physical input timing, validation
performance, a trained controller, or a sufficiently large training corpus.
