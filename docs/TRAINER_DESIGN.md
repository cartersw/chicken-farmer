# Imitation-learning trainer design

Updated 2026-09-08. The user decided on **640x360, full-color RGB, 8 bits per
channel, eight consecutive frames at 32 FPS, with rolling decompression,
frame reuse and background prefetching**. This is the agreed trainer baseline.
This document records the trainer plan and does not claim model results.
The [full-demo queue](FULL_DEMO_PROCESSING.md) now captures this baseline directly
and implements compressed RGB shards plus a bounded lazy frame cache. The trainer
and background prefetch scheduler remain future work.
The implemented interface remains [TRAINING_DATASET.md](TRAINING_DATASET.md).
For this baseline, this design supersedes the original handoff's example input
sizes, strided histories, previous-action inputs and MP4-first storage suggestion.

## Decided trainer baseline

| Setting | Baseline decision |
| --- | --- |
| Model image | 640x360, full 16:9 image |
| Color | RGB, 8 bits per channel; 24-bit color, no alpha |
| Prepared storage/cache | uint8 pixels; independently decodable lossless frames or indexed chunks |
| History | Eight consecutive accepted images at 32 Hz, oldest first |
| Loading | Rolling decompression into a bounded uint8 cache, shared-frame reuse and background prefetching |
| Model batch | `[B,8,3,360,640]`, converted to floating point and normalized at batch execution |
| Temporal model | Small CNN plus GRU; reset the GRU for each independent eight-frame example |
| Targets | Existing accepted angular/button tensors and validity masks |
| Extra observations | None: no previous actions, parsed state, identity or future images |

Implement and train this baseline first. Grayscale and alternative resolution
experiments are outside the active plan. Revisit the format only if learning is
not making progress and the observed failures give a reason to adjust it.
The decision is a baseline, not a claim of measured optimal accuracy. Preserve
the full 16:9 view and its color; no crop, stretch, palette reduction or
frame-rate change is part of this baseline.

## Deterministic image preparation

Version and hash the complete transform with every prepared dataset and model
checkpoint: source RGB decoder/orientation, source and destination dimensions,
color conversion, interpolation, antialiasing, rounding, normalization, and
implementation versions. The same transform must run at inference.

New full-demo captures already have the decided 640x360 dimensions: decode
top-first RGB, discard alpha, and store the exact RGB8 bytes without resizing.
Normalize by 255 when executing a batch. For a future migration of larger
legacy captures, decode top-first RGB, convert a frame to float32 in [0,1],
resize with bilinear interpolation (`align_corners=false`, `antialias=true`),
clamp to [0,1], multiply by 255, round to nearest with ties to even, and store
uint8. Normalize the prepared batch by 255 at execution. This intentionally
introduces a versioned 8-bit rounding step after resizing; it is not byte-for-byte
equivalent to the existing loader's unquantized float32 resize. Any additional
backbone normalization must also be recorded and identical at inference.

Use the RGB transform above without automatic per-frame histogram equalization
or adaptive color reduction. Random augmentation, if later enabled, happens
after the deterministic cache and uses consistent parameters within a history.
Geometry-changing augmentation needs corresponding label treatment and is
outside this baseline.

## Rolling preparation and prefetch

```text
Immutable compressed frame archive + accepted sample index
                 |
         shuffled chunk schedule
                 |
      background decode/resize workers
                 |
        bounded uint8 frame cache
                 |
   mixed batches of ordered eight-frame histories
                 |
        transfer / normalize / CNN / GRU / masked losses
```

1. Split by whole series and deduplicate canonical examples before scheduling.
   Build an index of accepted sample IDs, exact frame IDs, source/clock segments,
   archive offsets/lengths, transform identity, target command IDs and masks.
2. Schedule chunks from the training split in a seeded, shuffled order. Each chunk
   owns a non-overlapping span of observation endpoints. Workers prepare up to
   128 consecutive frame positions (four seconds), with up to seven preceding
   frames as history context. Short tails are allowed. History context creates
   no extra training examples; rejected candidates remain rejected.
3. Decode each needed frame on a cache miss, verify its binding, apply the frozen
   transform and cache uint8 pixels. Key entries by immutable dataset identity,
   source frame identity and transform hash. Reuse pixels across overlapping
   examples; do not cache learned CNN embeddings across optimizer updates.
4. Mix ready examples across several shuffled chunks, using different rounds or
   series when available. Shuffle examples within each chunk and maintain exact
   sample ownership so parallel workers neither duplicate nor omit examples.
   Batch membership is determined by the seeded schedule, not which worker
   happens to finish first. Preserve order within each history.
5. While the GPU consumes one batch, workers prepare upcoming chunks/batches.
   Convert to model floating precision near execution, rather than retaining
   full-resolution float32 histories in the loader. Pin staging memory and use
   asynchronous transfers where the measured GPU path benefits.
6. Release unused decoded entries under the byte budget. Compressed originals
   remain on disk; eviction does not recompress pixels. A later visit after
   eviction incurs another decode, including on later epochs.

Storage chunks are an I/O unit, not the model's history length. A four-second
cache chunk does not silently grant the model four seconds of context. Future
frames may be prefetched into memory, but only the eight accepted causal frames
are supplied to an example. Keep current clip/round/player/clock boundaries;
do not stitch clips, carry hidden state between shuffled histories, or feed an
overlapping history twice into a persistent GRU state. Longer recurrent training
needs a separate sequence/reset design.

For B consecutive valid examples in one uninterrupted clip, only B+7 unique
images are needed, versus 8B loads in the current uncached loader. Cache hits
avoid repeated decoding; prefetch hides remaining preparation only when worker
throughput keeps up with model consumption. Startup and queue starvation can
still cause waits. PyTorch provides loader workers, persistent workers and batch
prefetching; chunk scheduling and background integration remain implementation work
([PyTorch 2.8 DataLoader](https://docs.pytorch.org/docs/2.8/data.html)).

## Initial resource controls

These are adjustable design defaults, not measured optimal settings:

| Control | Starting value |
| --- | --- |
| Decode workers | 2, with a synchronous debug option |
| Chunk core | At most 128 frame positions, plus up to 7 history frames |
| Active chunk pool | Up to 4 total across workers |
| Ready batch queue | Up to 2 upcoming batches total |
| Prepared CPU pixel budget | 1 GiB total across workers, caches and staging queues |

Reserve bytes before decoding or staging; queue lengths alone are not a memory
bound. Account for uint8 batch copies and pinned buffers in the shared budget.
Track decoder/resize scratch space, metadata, process overhead and GPU tensors
separately; 1 GiB is not a total-process-RAM promise. Apply backpressure rather
than allocating beyond the budget. An impossible batch size fails clearly or is
reduced through an explicit run setting. Worker thread counts are bounded to
avoid each worker starting a full CPU thread pool. Stop/error handling cancels
workers, drains queues and releases buffers.

At 640x360 RGB, one uint8 frame is 675 KiB; 135 frames use about 89 MiB, excluding
indexes and temporary buffers.
If using PyTorch's `prefetch_factor`, remember it counts batches per worker;
configure the total queue budget accordingly instead of adding another unbounded
prefetch queue.

## Archive and acceptance compatibility

Share immutable source demos, store frames once, and keep labels/indexes separate
from pixels. Pack frames into indexed shards for scale rather than creating
millions of independent filesystem entries. Select the codec from read/decode
throughput as well as compression ratio. The smallest benchmark output is not
automatically the fastest training input. Long-GOP video needs extra seeking,
presentation-frame indexing and decode-cache evaluation before adoption.

Retain recoverable originals while preparing the decided RGB profile. Compression
and caching must preserve source identity, accepted sample membership, image
information bounds, labels and masks. The existing loader requires original TGA
paths and hashes; migration needs a separate prepared-dataset manifest and reader.
Validate the accepted source snapshot on preparation/open, verify decoded entries
on first use, and reuse only immutable verified buffers. Modified archives or
transforms invalidate the cache and stop that run. Do not weaken acceptance to
make a storage format fit. The approved HUD setup policy remains in effect;
this design adds no recurring HUD inspection.

The [storage assessment](../data/validation/storage-options-001/README.md) records
19.67 GB of raw images for 5,336 frames and projected 640x360 RGB PNG at 2.04 GB.
The projection is a small-sample size estimate, not a completed conversion.
Archive plus prepared cache consumes both sizes.
The [loading experiment](../data/validation/storage-options-001/loading/README.md)
measured 64.45 ms per eight-frame history from TGA versus 169.51 ms with repeated
full-resolution zlib decoding. It used warm file caches and excluded training,
worker overlap and frame reuse; PNG decoding was not measured.

## Baseline training and progress review

Train and evaluate the decided 640x360 RGB profile once independent held-out
series and the baseline trainer exist. Keep accepted examples, series splits,
action masks, training exposure and run seeds recorded so learning progress is
interpretable. There is no upfront color/resolution comparison grid.

Report masked aim errors, per-button precision/recall and false-positive behavior,
including quiet progression and combat separately. Also measure end-to-end
training examples/second, GPU data-wait time, queue starvation, cache hit rate,
bytes read, peak CPU/GPU memory and runtime inference latency. Compare cold start
and steady-state runs on a dataset larger than the configured cache. Check
behavior in the existing local evaluation workflow; offline action loss alone
does not establish reliable play. Choose quality tolerances on validation data,
keep the test series untouched for final evaluation, and document uncertainty.

The present nine clips total 166.75 seconds and are from one BO3; they support
correctness and throughput experiments but cannot establish general playing
ability. Do not repeatedly train only a resident cache subset, favor short/easy
chunks, or discard normal progression to improve throughput or headline loss.

If learning does not progress, investigate the recorded losses, target/mask
behavior, data coverage and gameplay failures before selecting an adjustment.
Document the evidence for any subsequent format change and version it explicitly;
do not silently vary the baseline between runs.

## Implementation completion checks

- Cached and uncached readers agree for the same prepared profile, including
  labels, masks, ordering, boundary rejections and deterministic rounding.
- Cache reuse reduces decode counts; overlap/context frames do not duplicate
  sample ownership. Future prefetched frames never enter earlier observations.
- Parallel scheduling preserves split membership and seeded epoch coverage;
  resume stores schedule/RNG/optimizer position, not decoded cache contents.
- Tiny-budget, cancellation, corrupt-file and transform-change cases terminate
  cleanly without stale cache hits, unbounded queues or partially published data.
- Measure the complete loading-plus-model path before claiming hidden decode
  latency or a training speedup. Record resource settings and startup costs.

The RGB archive/cache tests now verify lossless pixels, preserved labels/masks,
overlapping-frame reuse, byte-budget eviction and corruption failures. Complete
trainer scheduling, asynchronous prefetch, legacy migration and model accuracy
checks remain planned work.
