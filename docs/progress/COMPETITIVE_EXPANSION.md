# Competitive buttons and repeatable batch processing

Updated: 2026-09-08. This milestone implements the two approved next steps:
verify recorded competitive button semantics and process more than one pilot.
No model training or cloud work is included.

**Completed result: 456 accepted samples from three five-second clips across
Dust2 and Nuke, with verified angular and available button targets.** A real
three-sample CPU tensor batch was saved and reloaded exactly. All **1,889 Python
tests passed** in 66.77 seconds.

## Implemented

- Original 14178 button-name and eight-state enums, transition table and
  protobuf/native conversions are checked against the recovered server binary.
  Every training field needs its own exact command-row proof and allowlist.
- Full original-demo reconstruction replaces fixed Dust2 eligibility hashes.
  Commands, state, rounds, events, phase and pause context are checked again;
  missing baselines and unsupported fractions remain explicit.
- Current-profile packet scans can reach later rounds beyond tick 20,000.
  Historical replay profiles retain their original scope.
- A bounded batch planner rotates sources, rounds and players. The sequential
  runner keeps stage journals, checks artifacts on resume, retains failed
  attempts and uses fresh directories when a proof must be issued again.
- Hash-bound contact sheets cover every original image. The runner pauses
  before acceptance until the complete visual review is registered.
- The current acceptance/label profiles are v2. Historical publications are
  retained; opening them under changed proof code requires a fresh publication.

Guides: [buttons](../COMPETITIVE_BUTTONS.md),
[batch commands](../COMPETITIVE_BATCH.md),
[acceptance](../COMPETITIVE_ACCEPTANCE.md),
[tensor interface](../TRAINING_DATASET.md).

## Independently reconstructed sources

All four Parquet tables reproduced **byte-for-byte** from each original demo.
Full typed phase/context comparisons also passed.

| Source | Commands | Player states | Rounds | Events | Missing-baseline warnings retained |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dust2 | 1,416,312 | 2,490,975 | 26 | 3,653 | 1,074,675 |
| Nuke | 1,870,193 | 2,179,831 | 26 | 3,587 | 309,650 |

Reports are under `data/validation/dust2-full-source-reverification-v1/` and
`data/validation/nuke-full-source-reverification-v1/`. Nuke's corrected
extractor 0.1.2 tables are now in `data/parsed/v2-state-fixed/`; Cache still needs
that state re-extraction before joining this acceptance workflow.

The standalone original-button audit selected 5,600 Dust2 command rows in
[5000,6400): 3,923 have supported fields, 1,677 remain unknown. It retained 1,676
missing button parents and 31,216 known boundary comparisons with no mismatch.
Its observed state codes include repeated jump activity. Reload's bit meaning
is verified statically, but this interval contains no positive reload example.
See `data/validation/dust2-buttons-14178-v1/report.md`.

## Expanded captures and a retained validation failure

`data/batches/competitive-expansion-001/` contains two protected captures:
Dust2 round 5/ay0k at 22300–22620 and Nuke round 3/Ckanic at 10300–10620.
Each produced 160 original frames at 1280×720/32 fps. Both passed native pixel
and message-clock checks. All 320 images were reviewed at 640×360 in ordered
contact sheets: spectator identity/weapon strips are absent and normal player
HUD is preserved. Review hashes are registered against each exact capture.

The first Nuke acceptance attempt retained zero accepted samples because an
old pilot condition required absolute native render times below 1,024 seconds.
This capture starts around 1,051.775 seconds. Its actual adjacent-frame interval
is exactly 0.03125 seconds. The corrected check requires finite, increasing
timestamps and the same measured 32 Hz interval, independent of absolute epoch.
Tests cover later clocks, NaN/infinity, reversed and incorrect intervals.
The original rejected publication remains under `acceptance/attempt-001/`.
The runner reuses the verified captures when publishing the corrected proof.

## Accepted data and tensor verification

Fresh v2 publications include the existing clean Dust2 pilot and both new
captures. The pilot was reverified from its original source without rerendering.

| Capture | Original frames | Accepted samples | Rejected candidates | Available angular fields | Available button fields |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dust2 round 3 / Ckanic, existing 006 | 160 | 153 | 7 | 306 | 10,480 |
| Dust2 round 5 / ay0k, new batch | 160 | 150 | 10 | 300 | 11,032 |
| Nuke round 3 / Ckanic, new batch | 160 | 153 | 7 | 306 | 10,104 |
| **Total** | **480** | **456** | **24** | **912** | **31,616** |

Each accepted sample contains eight overlapping images and a future 32 Hz
control target. These are 15 seconds of source footage, not 456 independent
episodes. Seven candidates per clip lack the initial image history. The new
Dust2 clip also rejects two unsupported command-flag cases and one unsupported
raw fraction. Rejection reasons can overlap; the old pilot's early rejected
history also contains an unsupported raw fraction.

Publications:

- [Existing pilot, v2](../../data/accepted/dust2-competitive-controls-006-v2/competitive_acceptance.json)
- [New Dust2, attempt 002](../../data/batches/competitive-expansion-001/runs/df971dd315eeae8b6d3f6211/acceptance/attempt-002/competitive_acceptance.json)
- [New Nuke, attempt 002](../../data/batches/competitive-expansion-001/runs/5ebb0db256cd61709ee245af/acceptance/attempt-002/competitive_acceptance.json)

Positive available fields occur for all four movement directions, crouch, jump
and primary attack. Reload has verified negative labels but no positive example.
Unavailable fields retain false masks; acceptance of angular targets does not
make every button field available. Counts above are field cells, not distinct
physical events.

The [expanded tensor report](../../data/training/competitive-expansion-001/batch_report.json)
records one illustrative sample per clip: RGB shape `[3,8,3,180,320]`, aim shape
`[3,2]`, and button shape `[3,8,10]`. All six angular fields and 184 of 240 button
fields are available; 19 available button fields are positive. Image values are
finite and within `[0.0078814346,1.0]`. The saved `batch.pt` passes exact tensor
comparison after a `weights_only=True` reload.

Whole-series grouping keeps all 456 samples in training, with zero validation
and test samples. Both maps belong to the same supplied BO3. This verifies the
data interface; it does not measure a trained model or establish corpus size.

## Settings and limits

The direct final check confirms both sessions preserved all 39 selected personal
files, restored GameInfo and removed every staged renderer folder. All eight
installed game binaries and the installed HUD directory/resource hashes remain
unchanged. CS2 was closed. Evidence:
`data/validation/competitive-expansion-001/final_cleanup.json`.
Steam mode and Cloud preferences were unchanged.

Recorded held/net/activity labels are useful mouse/keyboard control targets;
exact physical event counts, order and within-tick times remain unavailable.
Weapon-selection controls are still undefined. The batch remains bounded to
five-second clips and requires manual visual review. More independent series
are needed for validation/test; these Dust2/Nuke recordings belong to one BO3.
Positive reload and broader firing/weapon examples still need corpus coverage.
