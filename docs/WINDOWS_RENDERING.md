# Native Windows replay rendering

Use the native Steam/CS2 installation with the experimental worker in
`tools/renderer/windows.py`. Linux and WSL are not needed for this route.
The first target is a short, inspectable Dust2 player POV clip; producing video
does not establish that frames are aligned with command execution.

**Local result:** native capture succeeds on CS2 1.41.7.8 at 1280x720/32 fps.
The competitive pilot now uses Ckanic's first scored Dust2 round, including a
Glock shot and aim turn. The planner excludes the earlier knife/setup phase.
Native HUD controls remove spectator statistics and chat while preserving player
signals. Capture instrumentation records actual movie counters, pixel hashes and
render clocks. The latest output is
`data/rendered/windows-competitive-006/dd3ea5022ae36523398b97ca.mp4`;
`data/datasets/dust2-competitive-006/` contains 160 frame intervals, 320 commands,
normalized aim targets and `viewer/inspect.html`. See
[current evidence and limits](progress/STATUS.md).

## Local prerequisites

Run commands from `C:\Users\carte\source\repos\chicken-farmer\chicken-farmer`.
The worker uses Python 3.10+, Steam, native CS2, an x64 plugin, and FFmpeg/ffprobe.
Compiling the plugin also requires the Visual Studio C++ x64 build tools and
Windows SDK. Visual Studio 2026 requires CMake 4.2 or newer.

This machine's preparation installed CMake 4.2.3 in `.venv/` and added the
`Microsoft.VisualStudio.Component.VC.Tools.x86.x64` and
`Microsoft.VisualStudio.Component.Windows11SDK.26100` components to Visual Studio.
The component installation completed with exit code 0 after Visual Studio was closed.

FFmpeg 9.0.1 essentials is installed under
`.tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/`. Its archive matched the
publisher's SHA256; see [the tool record](../tools/renderer/windows-tools.lock.json).
Its **software `libx264` encoder passed a real 1280x720/32 fps synthetic smoke test**.
The NVIDIA encoder test failed: this build requires NVENC API 13.1/driver 610.00,
while the installed driver exposes API 13.0. Use software encoding initially.
That is an encoder dependency issue, not a failure to render CS2 on the GPU.

FFmpeg's own [download page](https://ffmpeg.org/download.html) links the
[Windows binary publisher](https://www.gyan.dev/ffmpeg/builds/). These local tools
and generated binaries are ignored by Git and must be installed again on another machine.

## Build and inspect

```powershell
.\.venv\Scripts\python.exe tools/renderer/setup.py --build-plugin --generator "Visual Studio 18 2026"
.\.venv\Scripts\python.exe tools/renderer/doctor.py --allow-version-mismatch
```

Setup prepares the pinned upstream source and compiles
`tools/renderer/build/plugin-windows/Release/server.dll`. It does not install the
plugin into CS2. Source adaptations are maintained in
[plugin-windows](../tools/renderer/plugin-windows/README.md); original upstream
files remain intact. Initial configuration downloads the pinned SDK and JSON dependency.

The installed CS2 patch is `1.41.7.8`; upstream targets `1.41.6.5`.
`--allow-version-mismatch` explicitly permits an experimental attempt. It does
not certify the hardcoded game interfaces. A recent
[upstream issue](https://github.com/akiver/cs-demo-manager/issues/1458) reports a
demo-start callback regression in 1.41.7.6. The Windows adaptation includes a
bounded fallback for installing the frame hook; its logs distinguish plugin
loading from frame-hook initialization. The local successful trials also needed
the documented ICvar and replay-command interface corrections; their primary
source revisions are linked in the plugin notes. This establishes a working
short-capture path on this build, not compatibility with future CS2 updates.

## Short pilot

Keep Steam running and close CS2 before starting. Use the covered competitive job:
`data/jobs/dust2-competitive-shot-001.json` (Ckanic, slot 9, canonical round 3,
ticks `[6000,6320)`). It has 320 recorded commands and one Glock shot at tick 6102.
The earlier `dust2-audited-first.json` is a knife/setup diagnostic, and
`dust2-first.json` also lacks command coverage. Neither is a competitive sample.

New jobs require a hash-bound match-phase sidecar by default. See
[phase extraction and job generation](PHASES.md). An explicit
`--allow-unverified-phase` allows setup/unknown jobs for diagnosis only.

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --spec data/jobs/dust2-competitive-shot-001.json --output data/rendered/windows-pilot --allow-version-mismatch
```

This is a dry run. Review the selected interval and launch command. Add
`--execute` to attempt capture. Use a new output directory for each attempt.
The initial pilot is capped to five seconds; consult `--help` for the interval,
warmup and timeout options before expanding it.

For the two-second diagnostic used during development, add `--max-ticks 128`.
The effective pilot job receives a distinct clip ID, and every attempt uses a
unique capture prefix so retries cannot mix existing images.

The actual run opens the game window and needs permission to write temporary
files inside the Steam game directory. It launches CS2 with `-insecure`, stages
an isolated plugin search path and demo sidecar, and records the process it owns.
Only that process may be terminated if the run times out. Existing game sessions
are refused. The original demo is never edited.

The runner saves the original `gameinfo.gi` bytes and a recovery journal before
changing the search path. Normal completion and handled failures restore those
bytes; unexpected external changes are preserved and reported for review.
Recovery instructions and the journal location are printed on failure. The
gameinfo transaction does not restore all CS2 user preferences: resolution and
console settings can be saved by CS2. Run-owned plugin folders, backups and
failure evidence are retained; after restoring the search path they are inactive.
Do not
copy the proxy DLL over CS2's real server DLL or use the Linux install script.

After an interrupted worker, close CS2 and restore using its recorded journal:

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --repair data/rendered/windows-pilot/gameinfo-recovery.json
```

Repair verifies the original and patched hashes and refuses unknown edits or
another run's lock. It never kills a process using a stale PID from a journal.

## Inspect the result

Successful capture must contain decodable video, frame provenance, actual video
PTS and logs. Check the requested player, HUD/viewmodel, resolution, colors and
interval visually. Raw TGA decoding uses FFmpeg's image decoder, so Windows TGA
channel order and image origin are handled without guessing raw pixel layout.

Raw TGAs remain unchanged. `capture_frame_files.json` records each original
native filename, archived filename and SHA256. The capture ledger adds movie
counters and pixel-readback evidence. The encoded visual profile is recorded in
the render manifest; see [HUD cleanup](HUD_PROFILE.md).

Process a completed instrumented run into a diagnostic frame/action dataset:

```powershell
$env:PATH = (Resolve-Path '.tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin').Path + ';' + $env:PATH
.\.venv\Scripts\python.exe -m cs2_data process-render --parsed data/parsed/v2-audited/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773 --render-dir data/rendered/windows-pilot --out data/datasets/dust2-pilot
```

This reconciles actual movie observations with retained images and decoded PTS,
fits a scoped command-clock calibration, joins future command intervals, and
writes `viewer/inspect.html`. Open the viewer and select the local MP4 it names.
Use a fresh output directory. Missing/changed frames, missing endpoints and
inconsistent clocks fail; earlier uninstrumented pilots cannot be upgraded by
guessing timestamps. Individual stage commands are documented in
[ALIGNMENT.md](ALIGNMENT.md).

Outputs remain `training_ready=false`. Measured image identity does not alone
prove the command/render clock epoch, fractional action timing or every player's
POV. The real inspector makes those assumptions visible for review.

The latest actual build/run outcome is recorded in
[the progress tracker](progress/STATUS.md). A dry run or successful plugin build
alone does not count as captured training data.
