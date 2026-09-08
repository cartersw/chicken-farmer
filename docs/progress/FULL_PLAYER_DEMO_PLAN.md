# Full player POV feasibility and proposed run

Updated: 2026-09-08. Selected by the user: **Ckanic on Dust2**,
Steam ID `76561198323592528`, source demo SHA-256
`f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773`.

A complete player POV is a useful next engineering milestone. The current
worker can render and resume bounded clips, but needs an exhaustive queue and
boundary handling before it can claim complete coverage of one player's match.
This document is a feasibility estimate and proposed implementation/run plan.
No full-demo capture or model training was started for this investigation.

## Actual source coverage and raw frame cost

The [source-bound scheduling estimate](../../data/validation/full-demo-feasibility-001/player-window-estimates.json)
uses the existing alive-window, competitive-phase and command-coverage checks.
All 24 competitive alive windows for this player pass the current scheduling
command-gap check; pause state is observed. This does not establish that every
event or eventual image/control sample will pass acceptance.

| Quantity | Ckanic, Dust2 |
| --- | --- |
| Competitive alive windows | 24 |
| Eligible source duration | 1,889.53125 seconds: 31 minutes 29.53 seconds |
| Nominal images at 32 FPS | About 60,465, before boundary rounding and overlap |
| Capture dimensions | 1280 x 720 |
| Current original TGA size | 3,686,418 bytes per image |
| Raw original images per captured minute | 7.078 GB / 6.592 GiB |
| Raw originals for all eligible source time | About 222.90 GB / 207.59 GiB |

GB denotes decimal billions of bytes; GiB denotes powers of 1024. The estimate
is for competitive alive play. Setup/freeze time, pauses, dead-player spectating
and post-round footage are excluded by the existing eligibility rules.
Additional context frames, native capture traces, synchronization evidence,
acceptance manifests, previews and review sheets require additional space.
Frames and eligible source time are not counts of accepted training samples.

## Total retained storage and working budget

The [measured storage and runtime report](../../data/validation/full-demo-feasibility-001/storage-runtime.json)
shows that four recent ten-second jobs retained about 7.73 GB across render,
processing, synchronization, HUD review and acceptance outputs. Of that,
4.72 GB was original TGA images, 1.65 GB was repeated full-demo copies,
0.66 GB was native capture ledgers and 0.46 GB was HUD review material.
These are measured file sizes, not a compressed-image estimate.

The observed total was about 11.59 GB per captured minute across both maps.
Dust2 jobs alone used about 1.96-1.98 GB per ten-second capture. Applied to
this player's duration, with roughly 201 launches to preserve tails, a useful
initial estimate is **about 380 GB retained** (377.23-380.80 GB using the
observed Dust2 range and tail-adjusted frame lengths). This is a projection, not an
upper bound: native traces include startup/seeking and repeated source-prefix
information, and retries or context overlap add cost. The existing source and
parsed data are already on disk and are shared inputs, not additional per-minute
costs.

Reserve **450 GB** for the proposed run, and implement a separate free-space
floor before starting. The inspected drive had about **1,067 GB free** during
this investigation. Recheck immediately before execution. Stop and revise the
estimate if the initial rounds exceed the projection; do not assume this reserve
guarantees that every future attempt will fit.

Current per-capture copies of this Dust2 demo would themselves use about
**88 GB across 201 launches**. Sharing a verified immutable source instead of
retaining a complete copy for every clip is a worthwhile storage optimization,
but requires checking the staging/restoration and source-path contracts first.
The estimate above retains the current behavior.

The [recorded capture-stage performance](../../data/validation/action-coverage-001/capture-stage-performance.json)
contains about 447 seconds of stage execution/verification for 40 seconds of
footage. The later acceptance invocation adds about 665 seconds of acceptance
execution/verification and 86 seconds of resume rechecks. Together, the two
invocations measured about 1,198 seconds of machine work for those four clips.
Journal elapsed times overlap these measurements and are not added again.

Scaling by source duration or about 201 launches gives a rough **16-17 hours
of machine processing**, excluding human review, earlier parsing and tests.
The roughly six-hour projection for capture stages alone omits final acceptance.
This extrapolation is not demonstrated sustained throughput: startup, seeking,
cache reuse, tail duration and repeated source validation can change the total.
The initial two rounds should establish an end-to-end estimate. The earlier
packet-scan microbenchmark does not establish the same speedup for complete
collection or acceptance.

## Why the existing planner is insufficient

With ten-second clips, the current fixed-length splitting policy yields 177
captures and drops 119.53125 seconds of round tails. Its roughly 208.80 GB of
raw originals therefore covers only 29 minutes 30 seconds. With twenty-second
clips it yields 82 captures and drops 249.53125 seconds. Increasing clip length
alone does not solve full coverage.

A simple tail-preserving estimate is about 201 captures at a ten-second maximum,
or 106 at a twenty-second maximum, before context overlap and boundary rules.
The smallest ten-second-policy tail is only 30 ticks, below the current
32-tick minimum. The planner must redistribute or overlap a neighboring capture
and account for its targets explicitly. Independent clips also lose their first
seven history positions under the eight-image contract. Round, death and target
boundaries can make some samples legitimately unusable.

The current collection selects a bounded subset; it has no persistent registry
of completed intervals across an entire demo. A batch plan is bounded to 24 jobs,
clock extraction is bounded, and one coverage report accepts at most 128
publications. A full-player coordinator should partition work and reports while
retaining those bounds. Existing stage journals and accepted-target duplicate
checks provide useful components, but do not constitute that coordinator.

## Proposed implementation and first run

1. Add a persistent plan for this exact demo and Steam ID, enumerating every
   eligible alive interval. Record pending, captured, awaiting review, accepted,
   rejected and deliberately excluded coverage with reasons. Resume verified
   completed stages and distinguish retries from additional coverage.
2. Define variable tail lengths, history overlap and target ownership across
   adjacent clips. Stay within one alive window and POV; prevent duplicate
   training targets. Account for intervals that cannot yield a valid history
   or future target instead of silently dropping them.
3. Add total run storage/time budgets and a free-space floor, checked before
   each capture. Keep the existing per-clip size guard, idle-game requirement,
   private settings, restoration journals and compatibility checks. Prepare
   bounded clock evidence and reports per group.
4. Run the first two competitive rounds through the complete pipeline. They
   contain 188.78125 eligible seconds, about 22.27 GB of raw originals before
   overlap. Measure actual disk growth, startup/seeking, verification and review
   cost. Check round endings and interruption/resume behavior.
5. Continue the same coverage queue through the remaining rounds once that
   group validates the estimates and boundaries. Report all accepted/rejected
   intervals and verify a real tensor batch from the resulting corpus.

Ten-second clips have real capture evidence. Twenty-second clips are supported
and covered by automated tests, but need a live trial before using them as the
default. Fewer launches could improve throughput; the full-pipeline benefit
still needs measurement.

The current acceptance profile requires visual review of every original image.
Captures can remain pending review, but cannot be called training-ready without
it. For unattended publication, implement and validate automatic visual checks
with review of flagged cases and representative spot checks first; account for
legitimate flashes, smoke and scoped views. This replacement is proposed, not
implemented. Automatic clock, packet, pixel, POV and control checks remain
required, including rejection of the unresolved checkpoint ambiguity.

## Storage optimization and training scope

The original TGA files dominate frame storage; this is the current representation,
not an inherent cost of imitation learning. Lossless frame storage is a possible
later optimization, but its size and throughput must be measured and the loader
and evidence contracts updated. Existing acceptance references exact original
files: deleting those files and keeping a preview MP4 breaks the current proof.

Do not pre-export every overlapping eight-image sample as a separate tensor
file. Keep each frame once and let the loader assemble histories. The previous
small `.pt` artifact was a loader demonstration, not a required second copy of
the entire dataset.

One full player POV would test the collection pipeline at a useful scale. It is
still one match in the same BO3 as the current corpus; independent series are
needed for validation/test and broader training coverage. No estimate here
establishes exact within-tick input timing or guarantees that all frames will
be accepted.

Related: [current verified milestone](ACTION_COVERAGE_AND_THROUGHPUT.md),
[bounded batch runner](../COMPETITIVE_BATCH.md),
[training loader](../TRAINING_DATASET.md).
