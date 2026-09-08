# One recording session per demo and player

Status: implemented in the demo queue, 2026-09-08. The three-interval live test
passed capture, numerical acceptance, compression, cleanup and resume. A full
demo also captured all 24 recording intervals in one CS2 process and completed
all 26 training packages with 59,848 accepted examples. See
[the verification record](progress/RECORDING_SESSION_2026_09_08.md) for measured
results and the status of full-demo processing.

The implementation uses the separately pinned `plugin-session` build, a native
forward replay controller, shared compressed SQLite/native evidence and bounded
logical views. Legacy single-movie captures retain their existing plugin contract.

## Decision

Make a complete demo/player recording session the default unit of capture:

1. Prepare the whole source, eligibility timeline, capture schedule and storage
   estimate before launching CS2.
2. Launch CS2 once. Replay forward, recording the selected player's eligible
   intervals and writing pixels and native timing evidence to disk.
3. Finish the last recording, observe its real native endpoint, close CS2 and
   seal the session's capture and settings-restoration evidence.
4. Verify the session and index its common evidence once. Then dispatch bounded
   clip validation workers and lossless compression until every clip is done.
5. Advance to the next demo only after this demo's capture and processing phases
   are complete. Retain progress and recovery information for both phases.

The existing 1-4 validation-worker setting remains useful in step 4. Heavy
acceptance, RGB conversion, preview encoding and archive compression do not run
while CS2 is capturing. Recording still writes timing and pixel evidence as it
happens; those observations cannot be reconstructed reliably after playback.

The model format remains **640×360, full-color RGB, 8 bits per channel, 32 FPS,
eight-frame histories**, with lossless archives and rolling decompression.

## Separate recording boundaries from training-file boundaries

A physical recording run is a contiguous stretch of playback. A training shard
is a bounded view of that recording. They must have separate identities.

- Record continuously through an uninterrupted eligible alive window, even if
  it lasts longer than the current two-minute training shard size.
- Split the resulting frame sequence into training shards after capture. The
  next shard references the preceding seven recorded frames for its history.
  It does not seek backward, recapture those frames or restart `startmovie`.
- Stop recording around meaningful excluded intervals, such as dead time,
  freeze/setup or a sufficiently long pause. Continue demo progression.
- For a gap too short to stop, observe the endpoint, settle and restart safely,
  keep the physical recording running through the gap. The eligibility mask
  still excludes it, and training history must restart after the discontinuity.
- Reacquire the planned Steam ID/observer target after respawn or identity
  changes. A stable player identity is required before the next eligible run.
  Do not assume a spectator slot identifies the same player throughout a demo.

The existing Ckanic/Dust2 plan has 26 logical segments but only 24 contiguous
eligible windows; the longest is 143.5 seconds. One session should replace the
26 game launches. The two internal training splits need no recording stop or
seek. Tiny-gap coalescing may reduce physical start/stop pairs further, while
preserving every exclusion. See the
[read-only estimate](../data/validation/recording-session-design-001/estimate.json).

## Advance on observed game state, not timers

Use a native controller on the existing engine callback thread, with a
precompiled schedule. The Python supervisor observes progress and handles
storage, cancellation and process lifecycle; it does not send timed keyboard
commands to drive the game.

Each physical recording run follows these states:

`approaching -> settling -> armed -> start requested -> recording -> stop requested
-> endpoint observed -> files closed -> recorded`

The session follows:

`prepared -> capturing -> closing -> captured -> verifying -> validating/compressing
-> complete`

Requirements for transitions:

- A start request binds the demo hash, session attempt, run ID, planned interval
  and selected player. Its unique filename prefix cannot collide with a prior
  attempt. The first actual native movie submission acknowledges capture start.
- Schedule with demo ticks and native callbacks. Never use wall-clock sleeps,
  file modification times or MP4 frame number as the frame/action clock.
  Wall-clock time is only a stall/timeout watchdog.
- Keep the proven capture cadence and record actual replay, render, packet and
  readback observations. The existing route can submit its first image at
  requested tick + 2; do not silently redefine that image as the requested tick.
  Expected counts and boundaries must follow the measured capture convention.
- `endmovie` is asynchronous. Wait for the later native `movie_end` observation
  and final counter, plus output completion, before seeking or starting another
  movie. A console-command return is not a completed-recording acknowledgement.
- At ordinary training-shard cuts there is no native `movie_end`. A shard ends
  at a real subsequent observed frame boundary within the same recording; the
  final shard uses the physical recording endpoint. Define this explicitly in
  the new evidence contract. Never fabricate native endpoint events to satisfy
  the old one-movie validator.
- Frame counters, dimensions, target identity, unexpected tick regression,
  native hook faults, progress and available disk remain lightweight runtime
  guards. A missed start or boundary produces an explicit incomplete range;
  never shift filenames or manufacture a timing offset to hide it.
- Store a journal checkpoint after each completed recording run and durable
  frame block. `recorded` means pixels/evidence retained, not training accepted.
  Files must not be handed to validation while their writer can still append.
- User Stop stops admission of new runs, finishes the current run and endpoint,
  closes CS2 and retains a resumable capture session. It does not launch heavy
  validation while the user is stopping capture.

The existing approved HUD setup remains the basis for recording. This change
does not introduce manual review, contact sheets or overlay detectors.

## Playback strategy

First establish one monotonic replay at the proven capture settings. The initial
seek to the first pre-roll is allowed. Do not replay the opening demo and seek
again for each clip. Do not seek backward for shard overlap or a failed run.

There is a specific reason to avoid backward seeks: the current packet auditor
tracks the maximum source information returned during the **whole process**.
Going backward does not make already delivered future information disappear.
Resetting counters at clip boundaries would weaken the causal training proof.

An optimization after the baseline passes is accelerated forward traversal of
long excluded intervals. It must return to the normal cadence sufficiently
before eligibility, finish all pending seek/clock transitions, and confirm POV
and readiness before arming capture. Short gaps stay at the established cadence.
Acceleration or a seek must never remain active during captured eligible play.
The controller must detect overshoot; a guessed constant sleep is insufficient.
Choose and benchmark an acceleration method on the actual engine before making
claims about its supported rate or speedup.

If a recording mistake requires replaying earlier ticks, end the session attempt
and put the affected range into a repair attempt with a fresh process/history.
Do not invalidate the normal forward path by seeking backward inside it.

## One shared source and evidence history

Create a separately versioned recording-session profile. Do not overload the
old per-clip profile or relax its checks.

Each session owns:

- One immutable plan and copied demo, one CS2 PID, one settings-isolation lease,
  one staged plugin/resource setup and one final restoration receipt.
- A session manifest binding source SHA, selected player, output format,
  schedule, renderer/native profile and all child recording attempts.
- Append-only native packet/clock records and per-run movie/readback records.
  Every event retains its session identity and original global invocation IDs.
- A frame index keyed by session/run/native counter, with source interval,
  dimensions, native pixel hash, file identity and later file hash.
- Logical shard views over those frames, including history references and
  eligibility/ownership boundaries. Views do not duplicate the whole session.

After the game exits, verify and seal global evidence. Streaming/indexed readers
must verify the full native transcript and retain its process-lifetime packet
history. Build shared immutable source/clock/packet indexes once, keyed by input
hashes and verifier version. Clip validators consume bounded frame views plus
that verified shared context. Later clips cannot start their packet history at
zero or accept an arbitrary filtered ledger with its earlier history omitted.

Legacy readers load single-movie JSONL. Session readers stream the shared SQLite
history and select bounded capture rows. Logical indices start at zero while
native counters/filenames remain explicitly recorded. `session_boundary` names a
real subsequent observation and is accepted only from a verified session view. Keep per-shard sample validation
bounded, including the existing eight-frame causal history checks and masks.

Common session evidence should be losslessly archived once, with a referenced
receipt, rather than copied into every clip's evidence ZIP. Per-shard training
archives retain independently addressable RGB frames for the rolling reader.
Keep shared raw inputs until every dependent shard has durable verified output
or retained failure evidence. Cleanup must understand shared ownership.

Windows validation processes hold read handles that deny writes and deletion
of the shared ledger/index. The coordinator also protects the shared archive.
Each process checks the SHA256 once while acquiring its handle, then reuses that
digest only while the lock and file identity remain valid. This avoids repeatedly
reading tens of gigabytes for each logical view. Code and unprotected files still
receive fresh hashes. Handles close before shared cleanup. Other platforms retain
uncached verification.

## Disk and memory budget

Recording first trades a larger temporary disk footprint for fewer game launches
and no competition with heavy validation during capture. It does not require
holding a demo's pixels in RAM: write frames and logs incrementally.

At 640×360 and 32 FPS, the current four-channel TGA capture path writes about
**1.77 GB per recorded minute**, before native logs. The model still receives
three-channel RGB8; the fourth capture channel is removed during packaging.

For the retained 31m29s Ckanic/Dust2 plan, 60,460 unique nominal frames cost about
**55.72 GB of raw TGA**, before logs, extra bridge/pre-roll frames, indexes and
compression workspace. Uncompressed model RGB for those frames is 41.79 GB.
Compression ratios vary; neither figure is a final compressed-size prediction.

Before launch, the UI must show and check a conservative whole-session estimate:
scheduled physical frames + budgeted native logs + one staged demo + bounded
validation/archive workspace + an emergency free-space reserve. Enforce planned
frame and log byte ceilings while running. The current 15 GB free-space floor
alone is insufficient for an all-recordings-first run.

If space falls below the reserve, stop at a safe recording boundary and retain
the session for resume. Do not silently switch to competing validation jobs or
delete completed evidence. Exact log/workspace allowances should be measured
in the session pilot, then included in the estimator; 55.72 GB is not a safe
whole-session reservation by itself.

## Recovery and user interface

Show separate progress for **Preparing**, **Recording the demo**, **Verifying
recording**, **Validating clips**, and **Compressing/complete**. During capture,
show current round, planned source progress, completed recording runs and disk
use. During processing, show worker counts and accepted/rejected samples.

Use one app button to start/resume the complete queue, with no manual transition
between phases. A stop or crash resumes from retained evidence and the first
unfinished run, using a new attempt identity if CS2 must relaunch. Preserve
completed runs; the interrupted run must be re-recorded if it lacks a trustworthy
endpoint. Record capture gaps explicitly.

Normal completion requires a successful session close and settings-restoration
receipt. After an abnormal exit, earlier closed runs are salvage candidates,
not automatically accepted data. Reconcile recovery journals, frame closure,
native transcript integrity and settings evidence before reusing them. Missing
proof blocks reuse; it must not be replaced by a claimed clean exit.

## Implementation and acceptance sequence

1. Add the versioned session plan, physical-run/logical-shard mapping, storage
   estimate and transition controller. Test interval coverage, short gaps,
   identity changes, start/end acknowledgements, overshoot and cancellation.
2. Add one-session native recording, shared lifecycle/evidence and immutable
   manifests. Preserve current profiles so old packages remain readable.
3. Add streaming session verification, indexed shard views, shared-evidence
   archive ownership and the recording-first queue/UI phase barrier.
4. Run one focused live test with at least three recording runs, including a
   round/death transition and two successive start/stop pairs. Include a
   greater-than-two-minute eligible window to test a logical shard cut without
   a physical recording restart. Check first/last frames and histories at every
   boundary, and compare numerical acceptance with the established route.
5. Test injected dropped/duplicate frames, missing endpoints, wrong player,
   clock changes, disk exhaustion and interrupted-session resume. These should
   fail explicitly or resume the incomplete range without mixing attempts.
6. Record one full demo for one player using a single PID. Require exhaustive
   interval accounting, verified timing/pixels/targets, bounded memory, final
   lossless archives and cleanup. Measure capture wall time and total processing
   time separately before making throughput claims.

The live boundary tests are engineering validation of the new mechanism, not a
recurring manual-review step in production. Accelerated gap traversal has its
own test before enablement. Fewer launches should reduce capture overhead;
recording-first's total end-to-end performance still needs measurement because
validation is intentionally deferred.

## Relevant current code

- [windows.py](../tools/renderer/windows.py): `make_sequence`, `run_capture`,
  `wait_for_game`, frame archival and settings lifecycle.
- [capture_trace.inc](../tools/renderer/plugin-windows/capture_trace.inc): native
  movie submissions, asynchronous endpoints, readback identity and hashes.
- [clock_trace.inc](../tools/renderer/plugin-windows/clock_trace.inc) and
  [packet_trace.inc](../tools/renderer/plugin-windows/packet_trace.inc): native
  epochs and process-lifetime packet observations.
- [timing.py](../src/cs2_data/timing.py),
  [packet_bounds.py](../src/cs2_data/packet_bounds.py), and
  [competitive_replay_proof.py](../src/cs2_data/competitive_replay_proof.py):
  existing one-movie and causal proof contracts.
- [full_demo.py](../src/cs2_data/full_demo.py),
  [demo_pipeline.py](../src/cs2_data/demo_pipeline.py), and
  [training_archive.py](../src/cs2_data/training_archive.py): scheduling,
  concurrency, deterministic ownership and compressed output.
