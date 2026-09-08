# Action coverage and collection throughput

The collection pipeline can now find competitive clips containing underrepresented actions, retain an ordinary-play share, prepare their clock evidence, and run longer protected captures. The first collection produced **four ten-second captures: 1,280 original frames at 32 fps**. Independent acceptance adds **1,236 samples**, with **44 rejected candidates**. Refreshing the earlier three clips yields **1,692 accepted / 68 rejected** across seven clips. No model was trained.

## Collection implementation and actual captures

`competitive_coverage` scans competitive, alive player windows and records scheduling hints for reload, sustained primary attack, movement starts/stops, crouch and jump. It checks button projections against their raw protobuf representation, preserves missing or unsupported fields as unknown, and retains state corroboration separately. Hints identify promising capture locations; accepted-label coverage is computed separately from independently revalidated acceptance artifacts.

Selection balances sources, rounds and POVs. The default ordinary share is 50%, selected without action scores within the bounded source pool. An ordinary clip can contain shooting or reloading; the name describes how it was selected. This is not population-uniform sampling or a guarantee that all actions are represented.

`competitive_collection` saves the selected mixture, source/tool hashes and discovery reports, merges required clock windows per demo, and prepares the existing resumable batch. It checks that batch eligibility did not silently drop or change the selected clips. Retained discovery can be reused only after its provenance checks pass. See the [collection plan](../../data/collections/action-coverage-001/collection_plan.json), [Dust2 discovery](../../data/collections/action-coverage-001/discovery/dust2-ay0k.json), and [Nuke discovery](../../data/collections/action-coverage-001/discovery/nuke-ckanic.json).

The first collection selected and captured these exclusive-end intervals:

| Map | Round | POV slot | Demo ticks | Selection purpose | Original frames |
| --- | ---: | ---: | --- | --- | ---: |
| Nuke | 7 | 9 | 38234–38874 | Ordinary | 320 |
| Dust2 | 8 | 8 | 51700–52340 | Ordinary | 320 |
| Nuke | 21 | 2 | 131350–131990 | Reload hint | 320 |
| Dust2 | 24 | 13 | 186135–186775 | Sustained primary-attack hint | 320 |

These four launches captured 40 seconds of source playback, compared with eight launches at the historical five-second clip length. This reduces launch count; it does not establish a twofold improvement in total pipeline time. The [original capture-stage report](../../data/validation/action-coverage-001/capture-stage-performance.json) preserves timings before acceptance/resume replaced the current [batch summary](../../data/collections/action-coverage-001/batch/batch_summary.json). Rendering itself took 266.319 seconds across four launches; processing, synchronization and HUD preparation took another 98.303 seconds, excluding their verification passes. Concurrent diagnostics affected these local measurements.

The current competitive planner/worker supports clips up to 1,280 demo ticks, or 20 seconds, with the exact planned duration passed to the renderer. **The longer limit has automated test coverage; this collection exercised ten seconds, not a live twenty-second capture.** Each clip still stays within one competitive alive window and POV. See [batch operation and resume behavior](../COMPETITIVE_BATCH.md).

The capture budget accounts for BGRA originals and permits at most the planned frame count plus eight, bounded by 5 GiB for the current competitive profile. Review also closed an exit-between-polls gap: the same budget is checked once more after the owned process exits and before archiving. Historical profiles retain their existing limits. No native plugin or game binary changed in this milestone.

## Measured review and packet-cache improvements

HUD contact-sheet generation now uses one bounded FFmpeg sequence pass and publishes a static local `index.html` linking every sheet, frame range and original full-resolution TGA. It preserves source/sheet validation, immutable publication and historical bundle compatibility. The page does not approve images; every frame still needs actual visual review. See [the review workflow](../HUD_REVIEW.md).

On the same 160-frame capture, generation changed from **5.508 to 2.852 seconds**, with **20 FFmpeg processes reduced to one**. All 20 resulting PNG sheets were byte-identical to the previous implementation. This single local measurement was 1.93 times faster; new-bundle validation took 1.596 seconds. The [comparison artifact](../../data/validation/hud-review-efficiency-v1/comparison.json) and [generated index](../../data/validation/hud-review-efficiency-v1/sequence-final/index.html) retain the evidence.

Packet inspection now retains compressed, process-local verified prefixes. Repeated requests can restore the same prefix, trim it at the exact first later header, or extend it from its recorded source position. Every scan still hashes the original demo and reader implementation before and after inspection. Cache metadata is diagnostic and cannot grant acceptance.

The real late Dust2 benchmark inspected through demo tick 186810:

| Measurement | Cold scan | Identical warm scan |
| --- | ---: | ---: |
| Scan wall time | 63.351 seconds | 5.302 seconds |
| Additional decoded demo commands | 186,891 | 0 |
| Retained compressed bytes | 175,372,390 | 175,372,390 |
| Canonical output bytes | 663,699,250 | 663,699,250 |

The warm scan was approximately 11.95 times faster, recorded exactly one cache hit, and reproduced the expected canonical output SHA256 `b626a218136df37ec8dbe5f0db580a4693b84e92b083e353e7b84eca6f7c7dc0`. Both results contain 186,891 demo commands and 186,870 packets. See the [cold/warm benchmark](../../data/validation/late-packet-cache-verified-001.json) and [earlier sizing measurement](../../data/validation/late-packet-compression-001.json). These timings cover packet scans, not complete render/acceptance throughput.

The cache keeps at most **four entries and 384 MiB of compressed data in total**, with a **1 GiB uncompressed serialization ceiling per entry**. Oversized results remain verifiable but are not retained. Restoration checks the declared size before bounded decompression, then verifies exact length, SHA256, end-of-stream and absence of trailing data. Corrupt or incomplete entries reject. These are serialization limits, not a cap on total Python memory: the benchmark reached approximately **3.48 GB peak working set** while holding decoded objects and serializing results. Its retained compressed cache alone was approximately 175.4 MB.

## Acceptance and final verification

The four new clips contributed **1,236 accepted / 44 rejected** candidates. Existing captures were independently reissued under the final verifier; no additional game launches occurred during acceptance. The current corpus totals **1,692 accepted / 68 rejected** from 1,760 original images over 55 seconds of source footage. These are overlapping eight-image samples, not independent episodes.

| Clip | Accepted | Rejected | Current publication |
| --- | ---: | ---: | --- |
| Dust2 round 8, ordinary | 310 | 10 | [Acceptance](../../data/collections/action-coverage-001/batch/runs/7d85cdc5424882107fa8e918/acceptance/attempt-001/competitive_acceptance.json) |
| Nuke round 7, ordinary | 302 | 18 | [Acceptance](../../data/collections/action-coverage-001/batch/runs/de80011735c3c92204241e4d/acceptance/attempt-001/competitive_acceptance.json) |
| Dust2 round 24, attack hint | 313 | 7 | [Acceptance](../../data/collections/action-coverage-001/batch/runs/a5b61fe50beca1ffd70a15ef/acceptance/attempt-001/competitive_acceptance.json) |
| Nuke round 21, reload hint | 311 | 9 | [Acceptance](../../data/collections/action-coverage-001/batch/runs/a88c3aa49c7b41fffe78d927/acceptance/attempt-001/competitive_acceptance.json) |
| Dust2 round 5, refreshed | 150 | 10 | [Acceptance](../../data/batches/competitive-expansion-001/runs/df971dd315eeae8b6d3f6211/acceptance/attempt-003/competitive_acceptance.json) |
| Nuke round 3, refreshed | 153 | 7 | [Acceptance](../../data/batches/competitive-expansion-001/runs/5ebb0db256cd61709ee245af/acceptance/attempt-003/competitive_acceptance.json) |
| Dust2 round 3 pilot, refreshed | 153 | 7 | [Acceptance](../../data/accepted/dust2-competitive-controls-006-v3/competitive_acceptance.json) |

The [accepted-control coverage report](../../data/validation/action-coverage-001/accepted-control-coverage.json) independently revalidates all seven publications, rejects duplicate observations and counts masks. It contains **3,384 valid angular fields and 125,240 valid button fields**. Unknown values remain separate from valid negatives.

| Button | Positive held-end fields | Positive recorded-activity fields |
| --- | ---: | ---: |
| forward | 649 | 42 |
| back | 208 | 32 |
| left | 689 | 131 |
| right | 383 | 93 |
| crouch | 23 | 4 |
| jump | 11 | 16 |
| attack1 | 63 | 9 |
| reload | 40 | 5 |

These are label-field counts, not physical presses, bullets, completed reloads or independent actions. In the new clips, jump evidence can be recorded activity with unresolved rapid transitions despite no held jump at sampled boundaries. Exact timing/count/order remains unavailable. The ordinary Dust2 clip visibly reloads its rifle from 4 to 20 rounds. The targeted Nuke clip has positive reload input with a full AWP magazine; its input does not prove a completed reload. See the [separate state corroboration](../../data/validation/action-coverage-selected-state-v1.json).

The [real tensor batch](../../data/training/action-coverage-001/batch_report.json) includes one illustrative positive-control sample from each new clip: reload, forward, primary attack and reload. RGB shape is **`[4,8,3,180,320]`**; aim and button targets/masks have shapes `[4,2]` and `[4,8,10]`. It contains **8 valid angular fields, 320 valid button fields and 24 positive valid button fields**. All tensors are finite, pixels stay within `[0,1]`, and saving/reloading with `weights_only=True` reproduces every tensor exactly. Only images are model observations.

Whole-series splits are **1,692 training / 0 validation / 0 test**. All seven clips belong to the same supplied BO3. More independent competitive series, broader weapon/action coverage and a trained/evaluated baseline remain future work. Weapon-selection controls and exact event reconstruction are unfinished. Earlier publications reuse frames and must not be added to these totals.


The final Python suite passed **2,030 tests in 90.37 seconds**, including cache corruption/decompression bounds, longer capture budgets, immutable HUD review, selection provenance, source revalidation and existing control/tensor checks. Relevant source/test hashes were unchanged during execution. See the [complete test log](../../data/validation/action-coverage-001/tests.txt) and [hashed test summary](../../data/validation/action-coverage-001/tests.json).

All 1,280 frames were actually visually reviewed in 160 contact sheets. Reviews are scoped to 640x360 original-derived thumbnails; Dust2 round 24 also includes an original-resolution check of frame 93. Each published review binds its precise frame, ledger, HUD and plugin hashes. Flash-like veils/afterimages in Dust2 round 8 and round 24 are documented rather than removed or silently described as clear scenes. The spectator identity strip is absent and the ordinary player HUD remains present.

Nuke round 7 has one ambiguous native clock association at movie frame 83. An ordinary packet and a full-snapshot checkpoint repeat the same source clock, 95413, at demo tick 38400. The current matcher requires one source row per message type/tick; histories containing that observation cannot use it. The native hook is healthy and its packet bytes identify the ordinary packet, but this stronger association has not been integrated into acceptance. The other 319 movie frames and the end observation match. The [source-bound diagnostic](../../data/validation/action-coverage-001/nuke-round7-checkpoint-clock-diagnostic.json) preserves exact source and native packet/message hashes. Eight histories retain clock, checkpoint-boundary and packet-information rejection reasons. Any future improvement must establish all required checks, not just choose one duplicate clock row.

The [direct cleanup audit](../../data/validation/action-coverage-001/final_cleanup.json) passed all 156 comparisons of the 39 selected settings files against the four backups, including recorded size, modification time and read-only state. GameInfo matches each backup; the eight installed native DLLs, official VPK directory and original HUD resource match their pinned bytes. CS2 is closed, and no renderer staging directories or settings/GameInfo locks remain. Steam mode and Cloud preferences were not changed.
