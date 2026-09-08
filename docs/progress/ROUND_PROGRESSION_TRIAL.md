# Ckanic consecutive-round recording trial

Updated: 2026-09-08. The user limited this trial to **nine completed captures**:
166.75 seconds and 5,336 original images. The session stopped after the ninth
clip finished processing and cleanup. The final two planned clips were
intentionally left unrecorded. This is not a recording of two complete rounds.
The complete player-match scheduler and model training remain future work.

After viewing the nine clips, the user said they look fine and explicitly
approved trusting this capture HUD setup without recurring manual reviews,
automated overlay detectors or spot checks. The current pipeline records that
setup assumption as `user_approved_capture_setup`, with no claim of complete
image inspection. Historical journals and review material below are unchanged.
Independent acceptance evaluation and a tensor batch remain pending; source,
timing, POV and original-pixel integrity checks still apply.
The later [policy verification](../../data/validation/trusted-hud-001/nine-capture-policy.json)
generated and validated nine trusted-setup receipts in 0.137 seconds without
reading original images, generating sheets or launching CS2.

## What this collection adds

The [round collection workflow](../ROUND_COLLECTION.md) collects ordinary round
progression without scoring action hints. The source is the same Dust2 demo,
with Ckanic's exact Steam ID `76561198323592528`. Parser round IDs 3 and 4 are
its first two competitive rounds after setup. Their eligible alive windows are
`[4906, 10459)` and `[12187, 18716)`, totaling 188.78125 seconds.

The [immutable collection plan](../../data/collections/round-progression-001/round_collection.json)
contains eleven chronological captures with a twenty-second maximum: nine
twenty-second clips and tails of 6.75 and 2 seconds. Four ordered standard
batches preserve the existing protected worker. Planned source coverage is
188.75 seconds and 6,040 nominal images at 32 FPS. The last odd demo tick of
each source window is explicitly excluded, totaling 2/64 seconds. Freeze time,
pauses, dead-player spectating and post-round footage remain ineligible.

Every clip remains an independent capture. Its first seven image positions
lack the loader's eight-image history; this trial does not stitch histories
across clips or preserve recurrent model state across a round. Recording more
ordinary footage and training with longer temporal context are separate steps.
The existing round-3 pilot `[6000, 6320)` overlaps this collection; accepted
source/player targets must be deduplicated before combining publications.

The sequential session coordinator owns the launcher's project lock, verifies
existing stages when resuming, and checks cumulative time, retained bytes and
free space before each new clip. Stop requests finish the current clip and
cleanup before pausing. The actual plan has a 60 GB storage budget, a 90-minute
active-time budget and a 100 GB free-space floor. Its approximately 48.25 GB
projection is an engineering estimate, not measured storage or a hard quota;
manual review and final acceptance time are excluded from the runtime estimate.

## First live twenty-second result

The first capture `[4906, 6186)` completed with **640 original images** and
`matched_message_clocks`. Processing, synchronization and review-bundle creation
passed through the protected batch runner; the journal recorded pending visual
review under the policy then in use.
The [direct cleanup audit](../../data/validation/round-progression-001/first_cleanup.json)
verifies all 39 selected settings files, eight installed binary hashes and HUD
resource/restoration cleanup. The successful trial was checked before the
remaining ten clips were started.

The [initial visual sample](../../data/validation/round-progression-001/first_trial_visual_sample.json)
records inspection of 24 of the 640 images, showing spawn, navigation, waiting
and weapon changes. This is explicitly a sample: it does not approve the
remaining images or grant training acceptance. The retained
[source-state characterization](../../data/validation/round-progression-001/source_characterization.json)
describes walking, movement, slower states and weapon presence in the planned
intervals; those descriptive counts are not accepted keyboard labels.

The [desktop app](../DESKTOP_APP.md) now offers 5, 10 or 20 seconds per sample
clip, with 10 seconds still the default. A narrow regression test checks that
20 seconds dispatches `--clip-ticks 1280` with the exact selected player ID.
Initiating a twenty-second recording through the GUI has not been separately
tested; the live trial used the protected command-line session.

## Final recording result and remaining validation

The [ordered recording viewer](../../data/collections/round-progression-001/index.html)
and [machine-readable report](../../data/collections/round-progression-001/round_report.json)
show current journal-bound status, video links, source intervals, gaps and
review sheets. The report is for inspection and does not independently accept
training samples.

The user requested stopping at nine clips. The persistent stop request allowed
the ninth clip to finish its processing and cleanup, then prevented the tenth
launch. The retained eleven-clip plan remains unchanged and resumable, but the
remaining twenty-second and two-second captures are intentionally unrecorded.
Their combined 22 seconds must not be counted as captured data.

| Measured result | Nine-capture session |
| --- | --- |
| Captured source duration | 166.75 seconds: 2 minutes 46.75 seconds |
| Original images | 5,336 at 1280 x 720 |
| Captures | Eight twenty-second clips and one 6.75-second tail |
| Round-3 coverage | `[4906, 10458)`: 86.75 seconds |
| Round-4 coverage | `[12187, 17307)`: 80 seconds |
| Retained collection bytes at finish | 28,089,110,106 bytes: approximately 28.09 GB |
| Cumulative active processing time | 2,081.797 seconds: 34 minutes 41.8 seconds |
| Publication state at recording finish | Nine clips pending visual review under the earlier policy; no acceptance evaluation |

The [final cleanup audit](../../data/validation/round-progression-001/final_cleanup.json)
passes for all nine captures. Its 351 saved-backup comparisons cover the 39
selected settings files across those captures, and the eight installed binary
hashes and HUD resources remain unchanged. CS2 was closed at the final audit,
with no renderer staging or held session locks remaining. The
[final evidence summary](../../data/validation/round-progression-001/final_evidence.json)
binds the retained results and validation artifacts. GB values are decimal;
later report/evidence refreshes can add small files beyond the finish snapshot.

One captured source-clock ambiguity remains explicit in the
[native clock diagnostic](../../data/validation/round-progression-001/native_clock_exception.json).
In round 3's `[7466, 8746)` clip, frame 107 has duplicate source-clock records
at server tick 18,383. The other 639 frame associations and the movie endpoint
match. Under the current eight-image contract, histories ending at indices
107-114 would reject for the unverified association. No acceptance evaluation
has run, so those eight expected rejections are not published sample counts.
Recapturing does not remove the duplicate records in the original demo; the
clip is retained and the report flags the exception.

The user-approved setup policy removes the recurring original-image review
requirement. Accepted/rejected sample counts and a tensor batch from this
collection remain pending. No model training has run.

The focused suites passed **123 tests**: 57 planning/session/batch, 22 report,
and 44 launcher/player backend checks. Test evidence is retained under
[`data/validation/round-progression-001/`](../../data/validation/round-progression-001/).
No GUI or native game windows were opened by these fixture tests.
