# Replay rendering

The renderer adapter prepares **one player interval** from the canonical extractor's job JSON and drives Reka's existing CS2 replay capture. The Go extractor is the action source. Rendering needs a compatible CS2 installation and graphics environment; a `.dem` contains replay state, not stored video frames.

## Current result and local blocker

Source was downloaded and pinned to [`02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22`](https://github.com/reka-ai/cs2-dem-renderer/tree/02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22). The adapter can validate/print a job without opening the game. **No replay has been rendered or timing-calibrated in this Windows workspace.**

The Go adapter built successfully and its three targeted tests passed. A dry-run using `data/jobs/dust2-first.json` from the supplied Dust2 demo passed for round 1, player slot 2, ticks `[1279, 2458)`, at 32 fps. The Windows execution guard was also verified to reject before creating an output directory or opening Steam.

Read-only inspection on 2026-09-07 found Steam and native Windows CS2 installed at `C:/Program Files (x86)/Steam`. `game/csgo/steam.inf` reports `PatchVersion=1.41.7.8`, `ClientVersion=2000899`, `SourceRevision=10948930`. The pinned plugin targets **1.41.6.5**, and its build/launcher/encoder require Linux. Updating that pin alone does not establish compatibility. The plugin must be rebuilt/adapted and verified against the target CS2 build before rendering. A Linux worker also needs the Linux game installation; pointing WSL at `cs2.exe` does not supply it.

```powershell
python tools/renderer/doctor.py
```

The doctor reads files and PATH only. It reports dependencies and version/platform blockers.

## What is included

- `tools/renderer/upstream.lock.json`: upstream commit and known plugin target.
- `tools/renderer/setup.py`: clone/check the exact commit, apply the narrow patch, copy the maintained Go adapter, optionally build. It refuses conflicting source changes and never installs into CS2 or launches Steam.
- `tools/renderer/overlay/chicken_job.go`: `dem-render job --spec ... --output ...`, with dry-run as the default and `--execute` for a configured Linux worker.
- `tools/renderer/patches/0001-runtime-profile-and-clean-worker.patch`: HUD/viewmodel enabled, x-ray disabled, no interruption of an existing CS2 session, no blanket deletion of existing movie captures, and encoder shutdown on failed jobs.

The adapter avoids upstream's v5 parser, inferred action metadata, and scoped-round exclusion by feeding one canonical interval directly into its capture function. It checks the source SHA-256 before execution, stages a symlink so the source demo directory is unchanged, refuses nonempty output directories, requires exactly one encoded clip, and records the video hash and decoded PTS.

## Prepare the source and build

Run from the inner repository directory containing this document's `docs` parent:

```sh
python tools/renderer/setup.py --build
```

`--go /path/to/go` selects a toolchain outside PATH. This can compile the Go adapter on Windows for dry-run validation. The real minimum is **Go 1.25.4** in the pinned [`go.mod`](https://github.com/reka-ai/cs2-dem-renderer/blob/02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22/dem-render/go.mod), despite upstream README's older 1.21 requirement.

On a **dedicated Linux replay worker**, after resolving game/plugin compatibility:

```sh
python tools/renderer/setup.py --build --build-plugin
# This explicit install step changes the worker's CS2 files/search path:
bash tools/renderer/upstream/install-plugin.sh tools/renderer/build/plugin/libserver.so
```

The installer copies the plugin and changes `game/csgo/gameinfo.gi`. Use a separate replay installation and retain its configuration backup. Its default library is `~/.steam/steam/steamapps/common/Counter-Strike Global Offensive`. Steam must already be running and the game must be closed before executing a job. A graphical Linux session or configured virtual display is required. The renderer launches **offline** replay with `-insecure` and exits the game after the interval.

The pinned encoder requires ffmpeg `hevc_vaapi`, `/dev/dri/renderD128`, and hardcodes `LIBVA_DRIVER_NAME=radeonsi` (AMD). An Intel/NVIDIA worker needs an encoder change. CMake needs 3.14+, a C++17 compiler, and network access for its pinned SDK and JSON dependencies. These are source-verified requirements in [CMakeLists.txt](https://github.com/reka-ai/cs2-dem-renderer/blob/02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22/cs2-server-plugin/CMakeLists.txt), [render.go](https://github.com/reka-ai/cs2-dem-renderer/blob/02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22/dem-render/render.go), and [encode.go](https://github.com/reka-ai/cs2-dem-renderer/blob/02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22/dem-render/encode.go).

## One-player job and invocation

The dataset tooling emits JSONL jobs. Select one line and save it as `job.json`; the adapter accepts **one JSON object**, not the complete JSONL queue. Paths in a job are resolved from the invocation working directory; use absolute `demo_path` when transferring a job to a worker.

```json
{
  "schema_version": 1,
  "demo_id": "<64 lowercase SHA-256 hex characters>",
  "demo_path": "/data/demos/match.dem",
  "clip_id": "<stable hex clip ID>",
  "round_id": 1,
  "steam_id": "76561198000000000",
  "player_slot": 3,
  "spectator_user_id": 4,
  "start_demo_tick": 1000,
  "end_demo_tick": 1100,
  "fps": 32,
  "width": 640,
  "height": 360,
  "map": "de_mirage",
  "team": 2,
  "timing_clock": "demo_tick"
}
```

`spectator_user_id` comes from the extractor's `UserID & 255`; it is distinct from `player_slot`. Upstream issues `spec_player spectator_user_id+1`. That requested selection still needs visual verification. Clip IDs must have only letters, digits, and hyphens because upstream splits filenames at underscores.

```sh
# Safe plan: validates the demo header and prints the requested manifest.
tools/renderer/build/dem-render job --spec job.json --output data/rendered/clip
# Real replay on a configured, compatible Linux worker:
tools/renderer/build/dem-render job --spec job.json --output data/rendered/clip --execute
```

On Windows the executable is `tools/renderer/build/dem-render.exe`. It rejects `--execute` with a platform explanation. Use 32 fps initially; 64 fps is also accepted. The range is `[start_demo_tick, end_demo_tick)`. Starts below tick 71 are rejected instead of silently clamped by upstream's seek setup; trim those jobs explicitly. Scheduling commands at these boundaries does **not** prove captured frame boundaries match them. Upstream includes two minutes of replay warmup.

Execution produces `<clip_id>.mp4`, `<clip_id>.render.json`, `<clip_id>.pts.json`, and available engine/plugin logs. Status `video_ready_timing_unverified` means only the video exists and decodes. `pov_verified=false` and `timing_status=unverified` remain set. No invented frames, original-player inputs, or measured timing sidecar are emitted by this adapter.

## Timing contract and remaining capture work

The extractor's `demo_tick` is demoinfocs `GameState().IngameTick()` from the demo packet header, the clock used by replay scheduling. `demo_frame` is a parser frame ordinal. `server_tick_executed` is separately preserved from the user command and must not be silently substituted for the demo clock.

For a user-command row, `demo_tick` timestamps packet delivery. It does not prove that command executed at that instant. Even after measuring frame timing, training-grade action alignment needs a validated mapping from command execution time into the replay clock. A delivery-tick join may be used for diagnostics only while that mapping is unverified.

The original renderer computes nominal frame labels from `(demo_tick - spawn_tick) * fps / 64`; its plugin only logs command execution ticks. Encoded video PTS proves video presentation times, **not** demo time. File modification times and a `startmovie` tick plus `frame_index * 64/fps` are also insufficient evidence of exact capture timing.

The aligner requires a verified clip manifest:

```text
schema_version=1
clip_id, demo_id, round_id, steam_id (decimal string), player_slot
video_uri, video_sha256, num_frames, fps, width, height
timing_status="measured", timing_clock="demo_tick", pov_verified=true
renderer_commit, plugin_commit, cs2_build, renderer_profile, ffmpeg_version
capture_method (identify the actual instrumented/calibrated capture path)
```

Its frame JSONL/Parquet sidecar requires:

```text
schema_version=1
clip_id, demo_id, round_id, steam_id, player_slot
frame_index (contiguous, zero-based)
pts_seconds (from the decoded video)
source_demo_tick_start, source_demo_tick_end (measured, end exclusive)
```

Every row represents one frame and its following action interval. Supply all boundaries, including the final frame's explicit end. Preserve fractional replay ticks if the capture clock exposes them. At 32 fps, intervals nominally span two 64-Hz demo ticks, but dropped/duplicate frames, interpolation, seeks, or pauses must be detected instead of hidden by a ratio assumption.

The remaining renderer milestone is to record replay tick/fraction and capture index at the actual movie-frame hook, verify the frame-stage/capture offset with first-frame and firing/rotation/movement checks, reconcile every capture record to encoded PTS, and validate the player's POV. Only then produce the measured sidecar and mark those fields verified. Do not merely edit `timing_status` to bypass the gate. The current plugin has no proven hook-to-TGA correspondence, so this work is explicitly incomplete.

## Adapter checks

After setup/build:

```sh
cd tools/renderer/upstream/dem-render
go test -run 'TestChicken|TestRuntimeSequence|TestEncoderCleanup' .
```

These exercise invalid identity/clock/interval rejection, generated POV and visual commands, exact requested start/end scheduling, and encoder cleanup. They do not establish game ABI compatibility, render fidelity, or frame alignment.
