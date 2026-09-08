# Current-build competitive training pilot

Updated 2026-09-08. This milestone connects current Windows replay rendering to
eight-image histories and masked 32 Hz targets from the supplied competitive
Dust2 demo. Local calibration remains separate, and no model training is run.

## Implemented

- An explicit current competitive native profile, checked against all eight
  archived game binaries, with independent packet/filter and clock audits.
- Recovery of the three exact originally inspected 14178 DLLs into the
  workspace. Their existing SHA-256 values all match. The current CS2
  installation and historical acceptance outputs were not replaced.
- Fresh original-demo command reconstruction and exact typed full-row
  comparison, plus unique live-packet envelope matching.
- Separate competitive acceptance with eight RGB images, two consecutive
  future command boundaries, competitive/live state checks and missing masks.
- A CPU PyTorch loader and batch writer, including original image verification
  and whole-series train/validation/test separation.
- Temporary spectator HUD resources owned by each protected replay run, with
  archive verification and a native resource-path diagnostic.

## Retained attempts

| Run | Result | Settings and cleanup |
| --- | --- | --- |
| `windows-competitive-current-001` | 160 frames; native clocks, pixels, POV and packet bounds verify. The first loose CSS candidate leaves the spectator strip visible, so visual acceptance fails. | 39 selected files preserved, gameinfo restored, staging archived out of CS2. |
| `windows-competitive-current-002` | Startup exits before the plugin loads. The error dump identifies the private HUD VPK load; no frames were captured. | 39 selected files preserved, gameinfo restored, staging archived out of CS2. |
| `windows-competitive-current-003` | Startup also exits before plugin load. Independent inspection confirms a valid VPK; native code identifies a reserved `pak01` name/directory mismatch. | 39 selected files preserved, gameinfo restored, staging archived out of CS2. |
| `windows-competitive-current-004` | Renaming the private archive permits a complete 160-frame capture. Native path diagnostics still select the stock HUD, and the strip remains visible. | 39 selected files preserved, gameinfo restored, staging archived out of CS2. |
| `windows-competitive-current-005` | Explicit mounting reaches the private archive, but `_dir.vpk` as the logical name makes CS2 look for `_dir_000.vpk`. No frames captured. | 39 selected files preserved, gameinfo restored, staging archived out of CS2. |
| **`windows-competitive-current-006`** | **160 clean original frames; all visual, pixel, POV, clock and packet-bound checks pass. 153 accepted samples, 7 rejected.** | **39 selected files preserved, gameinfo restored, staging archived out of CS2.** |

Run 001 has 2,688 exact returned packets, including 189 seek-filtered returns,
and 336 matched command envelopes. Source proof is retained at
`data/validation/dust2-competitive-source-001/report.json`. Its 153 complete
histories pass the non-HUD gates. An earlier estimate of 152 double-counted an
overlapping rejection: candidate frame 2 contains an unsupported raw fraction
and already lacks complete history. Run 001 remains visually rejected; its
diagnostic source evidence is not accepted training data.

The first bounded reconstruction through source tick 6,400 exactly matches all
23,643 canonical commands strictly before that boundary. Final-tick rows are
excluded because parser cancellation can occur between packets sharing a tick.
Missing delta baselines elsewhere in the prefix remain unresolved. Evidence:
`data/validation/dust2-command-prefix-6400-v1/report.json`.

## Verified current result

Run 006 uses `spectator-strip-private-css-v4` plus
`competitive-hud-archive-search-v2`. Its real owned mod directory remains the
first Game entry, preserving the default write/plugin root. Game and Mod then
explicitly mount logical `pakchicken_hud.vpk`; CS2 derives the physical
`pakchicken_hud_dir.vpk` and `pakchicken_hud_000.vpk` names. Both runtime search
paths resolve the private stylesheet. The original game assets remain intact.

All 160 original frames were visually reviewed in ordered 640x360 contact
sheets, with original frame 0 additionally inspected at native resolution.
The spectator strip is absent, while health/armor, ammo, money, radar, crosshair,
round clock, own-weapon panel and viewmodel remain. Shot effects and a bright
flash/decaying afterimage are preserved. The latter is visually consistent
with a flashbang; this review does not independently establish its cause.
The exact capture and all image hashes are bound by
`data/validation/dust2-hud-006-v1/hud_review.json`.

Fresh acceptance verifies 2,690 exact returned packets (189 seek-filtered), all
160 image bounds, and 23,619 reconstructed canonical command rows strictly
before the observed prefix stop at source tick 6,394. The original 53,117
missing-baseline payload warnings remain visible; no baseline was invented.
Its 153 complete image histories all pass. Seven initial candidates are
rejected for insufficient history; frame 2 also rejects command row 22,074's
raw subtick `when=-0.0078125`. Native observation bounds are identical to run
001, and the command/fraction gates were not weakened.

| Artifact | Location |
| --- | --- |
| Original render | `data/rendered/windows-competitive-current-006/` |
| Timing and diagnostic viewer | `data/datasets/dust2-competitive-current-006/` |
| Accepted/rejected samples and recomputed proof | `data/accepted/dust2-competitive-controls-006-v1/competitive_acceptance.json` |
| Verified tensor batch and shape/mask/split report | `data/training/first-competitive-batch-002/` |
| Full visual review and contact sheets | `data/validation/dust2-hud-006-v1/` |
| Final direct settings/game/HUD checks | `data/validation/dust2-hud-006-v1/final_cleanup.json` |

All six attempts preserved the 39 selected personal files and restored gameinfo.
Final direct hashes verify the current eight game binaries and original HUD
resource/directory are unchanged, CS2 is closed, and renderer staging/locks are
absent from the game. Steam mode and Cloud preferences were not changed.

## Training scope

The [competitive acceptance contract](../COMPETITIVE_ACCEPTANCE.md) and
[tensor guide](../TRAINING_DATASET.md) describe the APIs and proof boundaries.
Angular labels are degree changes across future recorded command boundaries.
Competitive button semantics are not yet independently established, so button
targets stay masked. Exact event count, order, subtick timing and physical
input latency remain unverified.

All three supplied maps belong to one BO3 series. They stay together in the
training split; validation and test require additional independent series.
The current bounded source proof supports early prefixes through tick 20,000.
Full-match automation and a scalable visual review workflow remain future work.
Eligibility metadata is pinned to the already audited Dust2 source for this
pilot, so editing and rehashing a state/round/phase/context manifest cannot
authorize other samples. General fresh extraction of those proofs is also a
prerequisite for extending acceptance beyond this source.

## Verified tensor batch

`data/training/first-competitive-batch-002/batch.pt` was materialized with CPU
PyTorch 2.8.0 and independently reloaded using `weights_only=True`.

| Tensor | Shape | Verification |
| --- | --- | --- |
| RGB images | `[2,8,3,180,320]` | Finite float32, exact range `[0.0576855838,1.0]` |
| Aim targets/masks | `[2,2]` each | Four valid angular fields; these first two targets happen to be zero |
| Button targets/masks | `[2,8,10]` each | All 160 fields masked, with zero placeholders |

The selected observations are frames 7 and 8, with histories 0–7 and 1–8.
The complete partition has 306 valid angular fields and no valid button fields.
Whole-series split counts are train 153, validation 0, test 0. The first batch
validates loading and masks; its zero aim targets do not demonstrate learning.

Batch SHA-256:
`913b8b673a845a537c45ad6cfb3ea5e1cd46188dd6ddc599a2e2db8dd3562f8b`.
`batch_report.json` retains sample/source identities, and
`batch_reload_verification.json` records the independent reload.

The first actual batch exposed float32 resize roundoff up to `1.0000001192`;
that artifact remains at `data/training/first-competitive-batch-001/`. A
saturated-image regression now reproduces the issue, and the loader clamps
normalized resized pixels to its documented range before writing batch 002.

Final verification: **1,698 Python tests passed in 55.53 seconds**, including
33 tensor-loader tests; native Release build passed. No model was trained.
