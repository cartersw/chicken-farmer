# Full-demo processing

Implemented 2026-09-08. The desktop **Demo queue** preprocesses a complete demo
and automatically processes every eligible interval for one selected player.
It does not run a trainer. The previous sample-batch limits remain local to the
sample-capture workflow.

## Output contract

The versioned `cs2-full-player-demo-v1` profile requires **640x360, full-color
RGB, 8 bits per channel, 32 FPS and eight consecutive history frames**. The UI
shows this fixed baseline preset. Capture runs at 640x360 directly. Original TGA
pixels are decoded to top-first RGB24; alpha is removed, with no palette
reduction, resizing or lossy video conversion in the training path.

Each completed segment publishes:

- `training.zip`: individually indexed, losslessly DEFLATE-compressed RGB24
  frame members, a manifest, accepted sample metadata, projected targets/masks
  and an explicit duplicate-target exclusion list. Each decompressed frame is
  exactly 691,200 bytes, or 675 KiB. ZIP64 supports larger archives.
- `evidence.zip`: losslessly compressed original TGA files, capture/process
  evidence, timing results, accepted/rejected partitions, logs and journals.
  `archive_index.json` records original paths, sizes and SHA-256 hashes.
- `receipt.json`: format, archive hashes, acceptance identity, frame/sample
  counts, rejection reasons and storage totals. A receipt is published only
  after both compression round trips pass.

The two archives deliberately retain a training-friendly RGB representation and
recoverable original evidence. Preview MP4 is diagnostic and is not used as model
input. Source demos and canonical parsed tables are shared external references.
The staged `input.dem` copy is omitted from the evidence ZIP only after its hash
matches the original; the archive index records that reference.

Only newly created queue working directories are released, after verifying both
archives and any shared demo references. Existing captures are not migrated or
removed. Retain the original demo and parsed source alongside the packages.
For a forensic reconstruction, extract evidence back to its recorded original
workspace and restore omitted staged demo references from the unchanged source;
the historical proof files contain absolute paths. Normal training reads the
RGB shard directly and does not unpack the evidence archive.

## Coverage and segmentation

Preprocessing validates source hashes, competitive round phases, player identity,
alive state and command continuity over the whole demo. It does not filter for
highlights. Both quiet progression and action enter the same eligibility rules.

Eligible windows end at round/death, pause, identity or state discontinuities.
Windows with incomplete command coverage are excluded and reported. The planner
splits longer windows into at most 7,680 source ticks (120 seconds at 64 Hz),
with 14 ticks/seven frames of history overlap inside the same eligible window.
Owned source intervals are contiguous and do not overlap. Packaging removes
duplicate target command pairs across segments, while preserving each accepted
example's original eight-image causal history and masks.

Captures need an even number of source ticks and at least 32 ticks. A single odd
final tick is explicitly excluded. An entire alive window below the capture
minimum is reported. A short tail after a long segment is accommodated by
shortening the preceding segment, so the final capture has at least 32 ticks.
History never bridges a death, pause or round boundary. Windows before the
protected replay start are explicitly excluded.

The HTML report accounts for every source tick, including setup/unverified
rounds, freeze time, pauses, death, post-round time, missing player state and
command gaps. Numerical acceptance can reject individual candidate histories
after capture; those counts and reasons are reported separately. A segment can
complete with zero accepted examples. Completion is not a claim of useful model
coverage or trained performance.

## Queue and recovery

One persistent queue owns its output directory; a project lock prevents two
full-demo queues from capturing simultaneously. Entries run in queue order.
There is no 20-second manual advance or session time/clip-count limit. Internal
segment length and a 15 GB free-space floor bound temporary work. Exhausting
available storage pauses the queue without deleting archived data.

Stop finishes the active segment through compression and cleanup. Resume verifies
the immutable plan/source and completed archives, rebuilds duplicate-target
membership, skips completed segments and resumes unfinished batch stages using
their journals. Interrupted preprocessing and package attempts remain inspectable.
A cleanup failure preserves the completed package receipt; resume retries cleanup
without recapturing it. Processing errors stop the queue with a specific error,
without silently accepting or skipping failed data.

## Rolling decompression and trainer handoff

`cs2_data.training_archive.RGBFrameCache` is the implemented per-shard reader.
It validates the archive/receipt on open, verifies decoded RGB members and
accepted causal history/target bindings, and holds a bounded LRU of uint8 bytes.
Adjacent histories reuse their seven shared frames. Eviction frees pixels;
it does not recompress them. The compressed source stays on disk.

The full trainer still needs the series split/index, seeded chunk scheduler,
background workers, bounded ready-batch queue, tensor conversion and model loop
described in [TRAINER_DESIGN.md](TRAINER_DESIGN.md). The cache is not itself a
complete corpus loader or prefetch scheduler. No training speedup or fully hidden
decompression latency is claimed until those components are measured together.
