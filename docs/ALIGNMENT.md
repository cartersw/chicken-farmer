# Measured replay timing and alignment

Rendering requires CS2 and a compatible replay renderer. A `.dem` contains no
raster frames. The Python pipeline cannot fabricate them or use nominal FPS as
evidence of synchronization. The current upstream renderer does not provide the
per-frame measured replay clock this contract requires. Its unverified render
manifest is intentionally rejected by the aligner.

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
