# Native Windows replay rendering

Use the native Steam/CS2 installation with the experimental worker in
`tools/renderer/windows.py`. Linux and WSL are not needed for this route.
The first target is a short, inspectable Dust2 player POV clip; producing video
does not establish that frames are aligned with command execution.

**Local result:** native capture succeeded repeatedly on CS2 1.41.7.8. Two-second
pilots produced 64 frames; five-second pilots produced 160 frames, all at
1280x720/32 fps. The latest local clip is
`data/rendered/windows-pilot-007/65d673da8b15e089b40700b9.mp4`, with raw images in
its `frames/` subdirectory. A live-window comparison confirmed qualitative color
and orientation. Exact input synchronization and visual-profile acceptance are
still pending; see [status](progress/STATUS.md).

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

Keep Steam running and close CS2 before starting. Use the covered job:
`data/jobs/dust2-audited-first.json` (slot 3, round 1, ticks `[1279,2458)`).
The older `dust2-first.json` does not have command coverage and should not be used.

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --spec data/jobs/dust2-audited-first.json --output data/rendered/windows-pilot --allow-version-mismatch
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

Outputs remain `training_ready=false`, `pov_verified=false` and timing-unverified.
Video PTS, scheduled ticks and file timestamps do not prove the replay time of
each rendered image. Next, instrument capture boundaries and calibrate the
command execution clock, then exercise [alignment and the viewer](ALIGNMENT.md).

The latest actual build/run outcome is recorded in
[the progress tracker](progress/STATUS.md). A dry run or successful plugin build
alone does not count as captured training data.
