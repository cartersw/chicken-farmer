# Full-demo processing

Implemented 2026-09-08. The desktop **Demo queue** preprocesses a complete demo
and automatically processes every eligible interval for one selected player.
It does not run a trainer. The previous sample-batch limits remain local to the
sample-capture workflow.

The default is [one recording session per demo/player](RECORDING_SESSION_DESIGN.md).
CS2 records all planned intervals before parallel validation begins. The sample
capture tab retains its separate bounded single-clip workflow.

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
- `evidence.zip`: clip processing evidence, timing results and accepted/rejected
  partitions. Its index references the shared session archive for original TGA
  images, native timing/packet history, settings and game lifecycle evidence.
  Original native frames and the common log are compressed once per session.
- `session-packages/<session>/receipt.json` and `evidence-*.zip`: the lossless
  shared session archive, including the native ledger, SQLite index and frames.
  Clip receipts identify this dependency by path and SHA-256.
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
workspace, extract the referenced shared session archive to its recorded root,
restore each clip's frame references from the shared archive index, and restore
omitted staged demo references from the unchanged source;
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
There is no 20-second manual advance or session time/clip-count limit.

The recording schedule joins overlapping training shards into physical recording
intervals. Short excluded gaps can remain inside a recording; only explicitly
eligible frame ranges become training shards. There is one initial seek, then
forward playback, native start/stop acknowledgements and one final game exit.
A missed boundary, wrong player, frame-counter discontinuity or tick regression
stops the attempt and retains evidence. No timing offset is invented to recover it.

After capture closes and settings are restored, the app indexes and compresses
shared native evidence. The UI's **Validation workers** setting accepts 1-4 and
defaults to **2**. Each worker prepares bounded logical clips, encodes a diagnostic
preview, verifies frames and inputs, and runs numerical acceptance. Native clock
and packet invocation IDs retain their full process history across movie starts.
A logical shard endpoint references a real subsequent frame or native endpoint;
it never claims that CS2 stopped recording at a training-file split.

One archive worker publishes validated RGB shards in source order so duplicate
action targets receive deterministic ownership. At most `validation_workers + 1`
clips are admitted to validation/compression at a time. All already recorded raw
data remains on disk until its dependent shards are safely packaged. Original
TGA files are stored once in the session evidence archive, and training pixels
remain individually addressable, losslessly compressed RGB8 members.

Before launch the app estimates the whole recording's raw frames, bounded native
logs, staged demo, index/archive workspace and a 15 GB reserve. At this baseline,
raw BGRA capture uses about 1.77 GB per recorded minute. The live recorder monitors
free space and log size, requests an orderly stop when limits approach, and uses
an emergency bound for a stalled process or critically low disk space. Recording
first needs more temporary disk than the old rolling capture pipeline.

During capture, **Stop** finishes the current physical recording interval, observes
its endpoint, closes CS2 and saves progress. Heavy validation is deferred until
resume. During validation, Stop drains admitted work and preserves the remaining
recordings. Resume verifies immutable source/plan bindings and saved archives,
reuses closed recordings, and captures only still-missing intervals in a new
session. A failed native session remains retained for investigation and is never
treated as accepted training data. Replaying earlier ticks requires a fresh process.

Completed package receipts are saved before cleanup. The common session workspace
is released only after every dependent segment has verified training/evidence
archives. Cleanup can resume after interruption. Old completed packages remain
usable; existing capture files outside this queue are not migrated or deleted.
The original demo and parsed source must remain alongside the compressed output.

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
