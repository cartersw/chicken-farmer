# Measured replay timing and alignment

Rendering requires CS2 and a compatible replay renderer. A `.dem` contains no
raster frames. The Python pipeline cannot fabricate them or use nominal FPS as
evidence of synchronization. The upstream renderer's unverified manifest is
rejected by the strict aligner. The experimental Windows instrumented capture
path can produce an auditable diagnostic association, as described below.

## Capture contract

Once a capture implementation measures and validates the replay tick at frame
capture, it must export a clip JSON manifest:

```json
{
  "schema_version": 1,
  "clip_id": "2bd83b604df54713a74aefda",
  "demo_id": "sha256-of-demo",
  "round_id": 1,
  "steam_id": "76561198000000001",
  "player_slot": 2,
  "video_uri": "clip.mp4",
  "video_sha256": "sha256-of-video",
  "num_frames": 2,
  "width": 1280,
  "height": 720,
  "fps": 32,
  "timing_status": "measured",
  "timing_clock": "demo_tick",
  "pov_verified": true
}
```

These verification fields record actual checks. Changing the fields on an
uncalibrated manifest does not establish timing. Record the capture method,
renderer/plugin build, replay build and evidence alongside the manifest.

For every decoded frame, write a JSONL record (or equivalent Parquet row):

```json
{"clip_id":"2bd83b604df54713a74aefda","demo_id":"sha256-of-demo","round_id":1,"steam_id":"76561198000000001","player_slot":2,"frame_index":0,"pts_seconds":0.0,"source_demo_tick_start":1000.25,"source_demo_tick_end":1002.25}
{"clip_id":"2bd83b604df54713a74aefda","demo_id":"sha256-of-demo","round_id":1,"steam_id":"76561198000000001","player_slot":2,"frame_index":1,"pts_seconds":0.03125,"source_demo_tick_start":1002.25,"source_demo_tick_end":1004.25}
```

Example values describe the schema; they are not measured output from the supplied
demos. Frame indexes start at zero. PTS is the actual decoded video timestamp.
Intervals use the measured replay **demo tick** clock and are half-open. Every
end, including the final frame's end, must be measured. Adjacent boundaries must
match exactly; reuse the recorded boundary value in both adjacent rows. If
capture drops frames or seeks, split the clip and investigate instead of filling
gaps by assuming 32 FPS. Fractional ticks are supported.

## Run the stages

```powershell
cs2-data normalize --parsed data/parsed/v2-audited/DEMO_ID --out data/normalized/new-run/DEMO_ID
cs2-data render-jobs --parsed data/parsed/v2-audited/DEMO_ID --out data/jobs/one.jsonl --limit 1
cs2-data align --parsed data/parsed/v2-audited/DEMO_ID --clip data/rendered/clip.json --timing data/rendered/frames.jsonl --normalized data/normalized/v2/DEMO_ID --out data/aligned/v1/CLIP_ID
cs2-data viewer --aligned data/aligned/v1/CLIP_ID --out data/aligned/v1/CLIP_ID/viewer.html
```

The aligner requires `ffprobe` on PATH. It verifies the local video SHA256,
decoded frame count, resolution and every frame's PTS. It rejects wrong demo or
player identity, missing measurements, nonincreasing timestamps, gaps/overlaps,
duplicate raw command IDs and command reversals or gaps beyond four demo ticks
(configurable with `--max-gap-ticks`). A normal frame may contain zero, one or
many commands. It keeps them all and uses exact canonical row IDs.

Open the exported HTML in a browser and select the manifest's local video using
the file picker. Playback uses `requestVideoFrameCallback` where available to
show labels for the displayed video frame. Pause and step with the buttons or
arrow keys. The panel shows measured boundaries, command identities, raw mouse,
button masks, movement, angles, optional normalized aim, and parsed position /
weapon / state. The inspector is local and requires no HTTP server.

## What remains to validate

Measured image time alone does not establish action execution time. The current
join uses command packet `demo_tick`; `server_tick_executed` is preserved but not
silently substituted. Compare isolated shots, camera turns, movement starts and
stops against replay frames, and establish any packet-to-execution relationship
or render interpolation delay before accepting training labels. The manifest
therefore remains `training_ready: false`. No model training is included in this
first data-tooling milestone.

Unit tests cover wraparound, masked resets, noncontiguous IDs, exact interval
boundaries, multiple commands, empty intervals, mismatched identity and invalid
timing. The Parquet integration test exercises normalization, alignment and
viewer export with synthetic commands and a mocked video verifier; it is not
evidence that a real CS2 replay has been synchronized.

## Native movie submission and pixel evidence

`prepare-timing` consumes the instrumented Windows renderer's original render
manifest, decoded PTS, native JSONL ledger, and retained TGA frames:

```powershell
cs2-data prepare-timing --clip data/rendered/CLIP/CLIP.render.json --ledger data/rendered/CLIP/capture.jsonl --pts data/rendered/CLIP/CLIP.pts.json --frames data/rendered/CLIP/frames --out data/timing/CLIP
cs2-data align --parsed data/parsed/v2-audited/DEMO_ID --clip data/timing/CLIP/clip.json --timing data/timing/CLIP/frames.jsonl --out data/aligned/CLIP --diagnostic
```

These are path templates. Actual renderer filenames are recorded in each run.
The ledger header identifies schema 1, the `CMovieRecorder.movie_frame_submit`
hook, engine SHA256, and `IDemoFile.GetDemoTick` clock. Each `movie_frame` row
records the movie name, native capture index, counter after submission, actual
TGA filename, and observed replay tick. Exactly one `movie_end` row must provide
the next capture index and an explicit final replay tick. No endpoint is inferred
from frame count, requested end tick, modification time, or FPS.

The reconciler checks every native counter/filename against
`capture_frame_files.json`, which records the runner's original-to-archived
filename mapping and SHA256 before moving each frame. It also verifies every
actual archived frame, decoded video frame count, dimensions, and PTS. Repeated
or reversed replay ticks, missing frames/endpoints, other movie identities, and
source changes are rejected.

An optional `pixel_readback` record supplies the native submission candidate and
SHA256 of tightly packed RGB24 pixels in native and reversed row order. When
present, every frame must have a unique candidate and match the actual decoded
TGA pixels. The decoder handles raw/RLE 24/32-bit TGA, BGR/BGRA channels, and both
origin axes. Missing, duplicated, failed, or mismatched readbacks stop publication.
Repeated identical image hashes are counted because pixels alone cannot
distinguish those frames.

Outputs are `clip.json`, `frames.jsonl`, and `frame_inventory.json`. Their timing
status remains **`observed_movie_submission`**, including when pixel bytes match.
The original integer replay cursor remains separate from rendered-state time.
`--diagnostic` is explicit: it permits unverified POV and submission timing, but
requires original trace/render/inventory hashes and rechecks all frame bytes and
boundaries. The ordinary alignment gate still requires measured timing and
verified POV. Every output remains `training_ready=false`.

## Execution-clock calibration and future actions

The command packet tick, command execution tick, original client's input-history
render tick, and replay image observation time are different quantities.
`calibrate` produces a versioned mapping for one contiguous player/round segment:

```powershell
cs2-data calibrate --parsed data/parsed/v2-audited/DEMO_ID --round-id ROUND --steam-id STEAM_ID --player-slot SLOT --start-demo-tick START --end-demo-tick END --out data/calibrated/SEGMENT
cs2-data align --parsed data/parsed/v2-audited/DEMO_ID --clip data/timing/CLIP/clip.json --timing data/timing/CLIP/frames.jsonl --calibration data/calibrated/SEGMENT --normalized data/normalized/v2/DEMO_ID --out data/aligned/CLIP --diagnostic
```

Without independent anchors, calibration fits a constant
`mapped_execution_tick = server_tick_executed + offset_demo_ticks` from paired
canonical packet/execution clocks. Its status is
`inferred_from_packet_arrival`, not measured execution. A changing offset,
missing/zero execution tick, command-number discontinuity, pawn/identity change,
clock reversal, or excessive gap requires a separate segment. Source SHA256,
exact row-ID bounds, fitted domain, clock distributions, and negative subtick
counts are retained in `execution_calibration.json`.

For example, the inspected Dust2 slot-3 round-1 segment at packet ticks
`[1279,2458)` contains 1,179 commands with `server_tick_executed-demo_tick=10703`.
Its input-history render tick is normally four or five ticks behind execution;
the history player tick is zero or one tick behind. Those original-client clocks
must not be substituted for a replay frame clock. The separate competitive
window `[5990,6340)` also has offset 10703 across 350 commands. These observations
are source-specific diagnostics, not a universal offset or pixel-timing proof.

Optional `--anchors anchors.json` accepts schema-1
`measurement_method="engine_command_execution_hook"` evidence with the same
demo/player/round identity, capture method, plugin SHA256, hashed raw evidence
files, and at least three anchors. Each anchor contains `command_row_id`,
`server_tick_executed`, `source_demo_tick`, and `uncertainty_ticks` (at most 0.5).
Anchors must bracket the entire segment, reference matching canonical commands,
increase without resets, and agree with a constant offset within their stated
uncertainty. This distinguishes measured execution-clock evidence from merely
matching packet clocks. It does not certify the image observation phase.

On every calibrated alignment, the fit is recomputed from the original commands;
anchor and evidence hashes are rechecked. Editing a mapping-status flag cannot
upgrade an inferred calibration. Frame action windows cannot extrapolate beyond
the calibrated domain, so calibrate a slightly wider command interval than the
captured clip, including its final observed endpoint.

Calibrated alignment preserves raw `demo_tick` and adds `execution_demo_tick`.
It reads commands by identity and execution domain, so packet delivery outside
the frame interval does not discard valid execution targets. Normalized actions
join by exact raw row IDs.

For causal prediction under the explicit **after-tick-state observation
assumption**, calibrated targets use **`(observation_tick, next_observation_tick]`**.
The command at the observation's own tick is excluded; the command at the next
observation boundary belongs to the preceding frame's future target. The legacy
arrival-clock join keeps `[start,end)` for diagnostics. Both interval conventions
are recorded in metadata. No fixed two-command limit is imposed, and neither
convention proves the renderer's true observation phase by itself.

### Fractional rendered-state observations

The native hook's `render_time_seconds` field is sourced from
`EventClientOutput_t.m_flRenderTime`, not a guessed interpretation of a float.
The SDK layout and the exact engine caller passing the same event into the movie
recorder are documented in [CAPTURE_HOOK.md](../tools/renderer/plugin-windows/CAPTURE_HOOK.md).
`prepare-timing` preserves both this fractional clock and `GetDemoTick` for every
frame. Its final render-time boundary must come from the observed first movie
callback after recording stops; neither clock is extrapolated from FPS.

When these fields and an execution calibration are available, the aligner uses
`render_time_seconds * canonical_demo_tick_rate + offset_demo_ticks` as the
observation coordinate for future-action assignment. It does not use the ahead
of time replay cursor for this join. The canonical parser's tick rate (64 Hz in
these demos) is distinct from capture FPS. The shared epoch between render time
and `server_tick_executed` remains an explicit scoped assumption; the calibration
is still recomputed from its canonical segment. The native playback epoch
measured in capture 006 does not establish this equivalence, as detailed below.

`frame_alignment.parquet` keeps original cursor bounds in
`source_demo_tick_start/end`, fractional observations in
`render_time_seconds_start/end`, and the actual command-join interval in
`action_window_demo_tick_start/end`. The viewer uses the latter for its available
player-state snapshot while displaying all clocks. No intermediate state is
invented.

The latest complete runs, `windows-competitive-005` and
`windows-competitive-006`, each produced 160 frames, 160 native readbacks and an
observed final movie callback endpoint. Their complete `process-render` outputs
include timing, calibration, normalized aligned commands and a local viewer.
An independent check of 006 rehashed the source artifacts and decoded every
retained TGA: all 160 RGB hashes matched their native readback candidates and all
160 images were distinct. Its 320 unique command rows give two future targets
per frame; every execution coordinate falls strictly after its observation and
at or before the next observation.

The frame-50/51 firing transition in 006 was also inspected visually. Frame 50
shows Ckanic holding a Glock with 20 rounds and no muzzle flash. Its inferred
action interval is `(6100.697265625,6102.697265625]`, which contains the firing
command at canonical tick 6102, raw row 22454. Frame 51 visibly shows muzzle
flash/recoil and 19 rounds; its targets start after 6102.697265625. This one
transition supports the intended future-action assignment under the stated
clock mapping. It does not certify all observation phases or players. The
independent report is
`data/datasets/dust2-competitive-006/visual_action_validation.json`.

Capture 006 also measured `IDemoFile.GetDemoStartTick=-5546` on all frames.
This is the native playback/seek epoch, while the canonical command segment has
`server_tick_executed-demo_tick=10703`. These values provide direct negative
evidence against substituting the playback epoch for the recorded execution
epoch. Both values are retained, and
`native_playback_epoch_evidence.execution_epoch_equivalence_verified=false`.
The render/execution epoch mapping remains inferred and `training_ready=false`.

Per-label quality still needs handling: 006 retains one subtick event at raw row
22074, tick 6007, frame 3 with `when=-0.0078125`. It is preserved and reported,
without clamping or inventing an absolute execution time. Further acceptance
also requires broader movement/camera/firing checks, exact POV identity, and
runtime HUD review. The raw 006 images have no masks, but a lower spectator
player banner remains visible.

For historical context, instrumented capture `windows-competitive-004` retained
160 frames and 160 native readbacks. Every TGA's RGB pixels matched its recorded
readback candidate, with 160 distinct hashes. Its first replay cursor was 6002,
while the observed event float gave render coordinate 6000.626953125 under the
source-specific 10703 offset. This demonstrates why cursor time cannot silently
stand in for rendered-state time. That run lacked a final endpoint, so it could
not produce a complete timing sidecar despite successful pixel verification.
