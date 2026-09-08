# Consecutive round collection

A demo is first parsed into command and player-state tables. The expensive
pixel recording step then replays selected intervals in CS2. The desktop
sample planner reserves half its selections for ordinary play and uses the
remainder for action coverage; those samples are not continuous rounds.

`cs2_data.round_collection` adds a separate **action-blind, chronological**
policy for selected rounds and one player. It records eligible alive play in
order, including walking, waiting, rotations and fights. Freeze time, observed
pauses, death/spectating and post-round footage remain outside the existing
control-training eligibility contract.

## Plan a larger trial

Use a one-demo `sources.json` from the desktop preparation step. Planning uses
the same source, phase, command-coverage and clock checks as existing batches.

```powershell
.venv/Scripts/python.exe -m cs2_data.round_collection --sources PATH/TO/sources.json --out data/collections/round-progression-NEW --steam-id 76561198323592528 --round-ids 3 4 --clip-ticks 1280 --storage-budget-gb 60 --time-budget-minutes 90 --free-space-floor-gb 100
```

Round IDs are parser IDs. For this Dust2 demo, IDs 3 and 4 are the first two
competitive rounds after setup. Their eligible Ckanic windows total
188.78125 seconds. A twenty-second maximum produces eleven captures covering
188.75 seconds, including 6.75-second and 2-second tails. Each window's odd
final demo tick is explicitly excluded to satisfy the even-tick, 32 FPS batch
contract; the total exclusion is 2/64 seconds. No rounding is represented as
captured footage.

The manifest lists every planned interval and exclusion. Four ordered standard
batches hold the full-length and shorter ending clips. Bounds remain one
source, one player, at most 24 captures, and at most twenty seconds per capture.
This is a bounded round collection, not an exhaustive full-demo scheduler.

## Execute, pause and resume

Close the desktop launcher and normal CS2 first. The session owns the same
project lock as the launcher and uses the protected batch runner unchanged.
Omitting `--execute` only inspects the plan.

```powershell
.venv/Scripts/python.exe -u -m cs2_data.round_session run --collection data/collections/round-progression-NEW --execute --max-new-clips 1 --plugin tools/renderer/build/plugin-windows/Release/server.dll --ffmpeg .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffmpeg.exe --ffprobe .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffprobe.exe
```

The one-clip invocation supports testing a longer capture before scaling up.
After checking the result, repeat against the same collection with a larger
`--max-new-clips` value. Existing completed stages are independently checked and
reused. Failures stop the session for investigation rather than triggering
automatic retries. Renderer settings/restoration and game compatibility checks
remain active.

To request a stop from another terminal:

```powershell
.venv/Scripts/python.exe -m cs2_data.round_session stop --collection data/collections/round-progression-NEW
```

The current clip and its cleanup finish before the next step is stopped. A stop
request persists; add `--resume-after-stop` to the next run when ready. Do not
kill the renderer as the normal stop mechanism.

Before each one-clip batch invocation, the session checks cumulative active
time, retained bytes and free space with a working reserve. These are checks
before starting the next clip, not an immediate process kill or a filesystem
quota. The worker retains its own capture-size guard. The current trial's
conservative storage projection is about 48.25 GB; actual usage is measured
separately. The retained planning runtime projection excludes final acceptance
and the manual review used when that plan was created. Routine captures no
longer require manual HUD review. Budgets are bound to the immutable plan.

## Inspect what was recorded

```powershell
.venv/Scripts/python.exe -m cs2_data.round_report --collection data/collections/round-progression-NEW --include-disk-usage
```

Open the resulting `index.html` for an ordered video playlist, source intervals,
gaps, captured frame counts and links to any retained diagnostic review sheets. The report reads
recorded evidence for display; it does not independently accept training samples.
Each underlying batch can also be opened in the desktop **Capture batches** tab.

Routine captures use the user's approved current HUD setup and proceed without
generating sheets or waiting for visual review. This is recorded as
`user_approved_capture_setup`, not as complete-image inspection. Automatic
timing, source, first-person identity, original-pixel integrity and restoration
checks remain required. The [HUD review tool](HUD_REVIEW.md) is optional for
diagnosis. The report distinguishes the current **Ready for acceptance** setup
decision from the recorded earlier pending-review status. Existing capture
journals remain unchanged until a normal processing invocation advances them.

## Recording duration and model context are separate

Chunk duration is an engineering limit for capture, recovery and resource use.
Consecutive chunks preserve the order of ordinary round footage. The current
[training loader](TRAINING_DATASET.md), however, still constructs eight-image
histories at 32 FPS, approximately a quarter second of observations. Each capture
loses its first seven history positions; this trial does not stitch histories
across captures or preserve recurrent state through a round.

Learning decisions over longer round context will require sequence sampling,
recurrent-state handling and explicit reset boundaries in addition to larger
recordings. Ordinary progression should remain the base distribution; targeted
action examples can supplement underrepresented controls. Whole-series train,
validation and test separation also remains necessary.

This Ckanic collection includes the existing round-3 pilot interval
`[6000, 6320)`. Before combining accepted partitions, deduplicate overlapping
source/player targets. Recapturing an interval does not create another
independent episode or validation series.

## Recorded trial result

The user stopped `round-progression-001` after nine captures. Those captures
contain 166.75 seconds and 5,336 original images: the planned first-round
coverage plus the first 80 seconds of the second round. The last two planned
clips (20 and 2 seconds) remain unrecorded, with a persistent stop request.
The original eleven-clip plan is retained unchanged.

The session recorded 2,081.797 seconds of active processing and about 28.09 GB
of retained files. The final direct audit verified settings, installed binaries
and HUD restoration, with CS2 closed and no staging or settings locks left.
Eight clips have matched native clock audits; one has a source association
ambiguity at frame 107. Under current acceptance rules, the eight image histories
ending at frames 107 through 114 would reject that frame. No sample acceptance
or model training was performed. The user subsequently viewed the nine clips,
said they look fine, and approved trusting this capture setup without recurring
HUD checks. That removes the manual-review requirement; accepted/rejected
samples still need their independent numerical/source evaluation.

See the [playback page](../data/collections/round-progression-001/index.html),
[final evidence](../data/validation/round-progression-001/final_evidence.json)
and [recorded milestone](progress/ROUND_PROGRESSION_TRIAL.md).
