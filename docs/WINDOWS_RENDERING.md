# Native Windows replay rendering

Use the native Steam/CS2 installation with the experimental worker in
`tools/renderer/windows.py`. Linux and WSL are not needed for this route.
The current build route is an explicit competitive replay profile for CS2
1.41.8.0, with separate source-command, frame-bound and HUD verification.
See [competitive acceptance](COMPETITIVE_ACCEPTANCE.md),
[the tensor loader](TRAINING_DATASET.md),
[resumable batches](COMPETITIVE_BATCH.md), and
[the current expansion milestone](progress/COMPETITIVE_EXPANSION.md)
for current results and pending checks.

[Action-aware collection](COMPETITIVE_COLLECTION.md) now prepares competitive
clips and their clock evidence automatically. The standalone worker still
defaults to five seconds; the explicit current competitive profile permits
`--max-ticks` up to 1280 (20 seconds). Longer captures retain the same settings,
frame-count and source checks, with a BGRA disk budget sized for the bounded
duration. Historical profiles remain capped at 320 ticks.

**Historical local result:** native capture succeeded on CS2 1.41.7.8 at 1280x720/32 fps.
Protected clock trial `windows-timing-016` produced 160 images, verified packet
information bounds and **129 accepted training samples**, with 31 rejected.
All 39 selected personal settings files were preserved and the staged plugin
was removed from CS2. See [the synchronization workflow](SYNCHRONIZATION.md)
for the accepted profile and its limits. Steam Cloud testing is deferred.
The settings-isolation build passed the live `windows-settings-012` trial:
64 frames, a two-second MP4, clean CS2 exit, and all 39 selected personal settings
files unchanged. Earlier captures used the previous build, which restored
`gameinfo.gi` but did not back up personal preferences.
The user subsequently reported Steam **Offline Mode**. Online Steam Cloud and
reconnection behavior have not been validated; the worker did not measure Steam's
network mode during this trial.
The competitive pilot uses Ckanic's first scored Dust2 round, including a
Glock shot and aim turn. The planner excludes the earlier knife/setup phase.
Native HUD controls remove spectator statistics and chat while preserving player
signals. Capture instrumentation records actual movie counters, pixel hashes and
render clocks. The historical trial-016 video is
`data/rendered/windows-timing-016/dd3ea5022ae36523398b97ca.mp4`;
`data/datasets/dust2-timing-016/` contains diagnostic alignment and
`viewer/inspect.html`. Accepted targets are a separate partition under
`data/accepted/dust2-causal-016-v1/`; the diagnostic viewer does not display that
new target selection. See
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

The installed CS2 patch is `1.41.8.0`; upstream targets `1.41.6.5`.
`--allow-version-mismatch` explicitly permits an experimental attempt. It does
not bypass binary verification or select a native profile. The current route
requires the job field `competitive_replay_profile` shown below, while jobs
without it retain the historical 14178 profile. A historical
[upstream issue](https://github.com/akiver/cs-demo-manager/issues/1458) reports a
demo-start callback regression in 1.41.7.6. The Windows adaptation includes a
bounded fallback for installing the frame hook; its logs distinguish plugin
loading from frame-hook initialization. The local successful trials also needed
the documented ICvar and replay-command interface corrections; their primary
source revisions are linked in the plugin notes. This establishes a working
short-capture path on the inspected builds, not compatibility with future CS2 updates.

## Short pilot

Keep Steam running and close CS2 before starting. Use the current competitive job:
`data/jobs/dust2-competitive-current-001.json` (Ckanic, slot 9, canonical round 3,
ticks `[6000,6320)`). It has 320 recorded commands and one Glock shot at tick 6102.
The earlier `dust2-audited-first.json` is a knife/setup diagnostic, and
`dust2-first.json` also lacks command coverage. Neither is a competitive sample.

The complete current job includes:

```json
{"competitive_replay_profile": "cs2-14180-competitive-replay-v1"}
```

The worker converts that field to `-chicken-competitive-replay` and verifies all
eight current binary hashes before launch. It cannot be combined with the
controlled-calibration replay profile. Matching current binaries alone do not
make local calibration evidence a competitive capture. The historical
`dust2-competitive-shot-001.json` job remains unchanged.

New jobs require a hash-bound match-phase sidecar by default. See
[phase extraction and job generation](PHASES.md). An explicit
`--allow-unverified-phase` allows setup/unknown jobs for diagnosis only.

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --spec data/jobs/dust2-competitive-current-001.json --output data/rendered/windows-pilot-NEW --allow-version-mismatch
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

The runner saves the original `gameinfo.gi` bytes and selected personal settings
before launch. It clones the user's configuration into a private render profile,
requires the native settings guard, and restores verified settings after the
owned CS2 process exits. This protection passed the bounded live trial described
below. Do not copy the proxy DLL over CS2's real server DLL or use the Linux
install script.

### Private HUD assets

The current competitive route stages its spectator-HUD override only inside
`game/csgo/chicken-render-<run-id>/`. The private-archive candidate contains the
modified stylesheet at `panorama/styles/hud/hudhealthammocenter.vcss_c` and a
small `pakchicken_hud_dir.vpk` index and `pakchicken_hud_000.vpk` containing that
resource. This archive pair belongs to the
temporary mod; the installed `game/csgo/pak01_dir.vpk` and original game assets
are read as source evidence and are never overwritten.
The journaled gameinfo lease mounts the archive in `Game` and `Mod` using its
logical `pakchicken_hud.vpk` name; its physical files retain the `_dir.vpk` and
`_000.vpk` suffixes.

The existing cleanup moves the entire owned mod, including its HUD assets,
into `<run>/renderer-sandbox/` after restoring gameinfo. Native TGA frames remain
unchanged; this is a renderer resource change, not a black rectangle applied
to saved images or a change to personal HUD preferences.

Correctly staged resource bytes do not prove that the engine loaded the override
or that the resulting HUD is suitable. The first current replay's loose-file
override failed visual inspection. Both private `pak01` archive candidates
failed the engine's reserved-name identity check; the current candidate uses
the distinct `pakchicken_hud` name. Trial006 loaded that override, and all 160
frames passed the recorded visual review. The fixed review covers that exact
capture; later clips still require their own HUD evidence.
See [HUD_PROFILE.md](HUD_PROFILE.md) and
[the competitive pilot milestone](progress/COMPETITIVE_TRAINING_PILOT.md) for the
current result; a failed visual attempt does not become accepted training data.

## Keeping normal play separate

Before the first protected trial, set CS2's audio, video and HUD to your normal
preferences, then close it. There is no complete backup from before the earlier
renders, so the new worker preserves the preferences present when each run starts.
Keep Steam running. Leave CS2 closed and avoid editing its settings while a
render or recovery is in progress; launch it normally through Steam afterward.

Every executed run now performs this sequence:

1. Refuse an existing CS2 process and resolve the Steam client/account. If
   discovery is ambiguous, use `--steam-dir` and `--steam-user-id` explicitly.
2. Save selected local/remote config and installation config files under the
   run's `settings-backup/`, with hashes, timestamps, read-only flags and a
   durable `settings-recovery.json`. Root locks prevent overlapping renderers.
3. Clone the saved local configuration into `replay-settings/cfg`. Only the
   launched CS2 process receives `USRLOCALCSGO=<run>/replay-settings`; the parent
   environment, Steam launch options and Steam Cloud preference are not changed.
4. Require a compatible native guard. It checks the real settings read/write
   paths and blocks the engine's config RemoteStorage interface inside the owned
   process. It refuses startup if that Cloud interface was already used or the
   exact game binaries are unsupported. Old plugin DLLs fail preflight.
5. After the owned process stops, restore `gameinfo.gi`, archive the observed
   post-run settings, verify backups, and restore changed selected files exactly.
   Restore removed originals and remove selected files absent from the pre-render
   snapshot.
   Normal completion, launch failure, timeout and handled interruption all run
   cleanup. Changes detected before launch or after the poststate is sealed are
   preserved and reported as conflicts. Changes made during capture cannot be
   attributed reliably; restoring the baseline also rolls back those changes.
6. Move this run's staged plugin directory out of CS2 into
   `<run>/renderer-sandbox/`, preserving raw captures and diagnostic evidence.
   Encoding begins only after cleanup and native proof verification succeed.

The selected roots are `Steam/userdata/<id>/730/local/cfg`,
`Steam/userdata/<id>/730/remote`, and `game/csgo/cfg`. Selection covers CS2 user,
machine and key VCFG files, local video settings/backups, and CFG files in those
roots (plus `remote/cfg`). The exact selectors and exclusions are recorded in
the journal. Inventory/voice caches, `trustedlaunch.cfg`, Steam metadata and
account-wide settings are outside this restoration scope. The separate Steam
client can still update its own metadata or synchronize files; the native guard
does not claim to control it. See the [native isolation evidence and limits](../tools/renderer/plugin-windows/SETTINGS_ISOLATION.md).

All 17 historical inactive renderer folders were individually verified and moved
into their original workspace run's `renderer-sandbox/`. The archival report is
`data/settings-audits/historical-staging-2026-09-07.json`; it records each source,
destination and owning journal hash. No `chicken-render-*` folders remained in
the game installation after cleanup. New cleanup does not sweep unrelated directories.
No renderer DLL is copied over the original game DLL. The renderer's `-insecure`
flag and hooks belong to its process and end when that process exits.

### Live protection verification

After the user restored normal preferences, trial `windows-settings-011` passed
the native isolation checks and retained all 39 selected personal files unchanged.
It captured 64 frames but crashed during DLL shutdown. The crash dump identified
the optional `dem_render_info` command's destructor unregistering after the
engine command registry was destroyed. The Windows adaptation now omits that
unused registration; the process-lifetime Cloud guard remains active.

Repeat trial `data/rendered/windows-settings-012/` exited with code zero and
produced `329360cba39babc8ea2d661c.mp4`: **64 frames, 1280x720, 32 FPS, two seconds**.
All 64 retained images match native pixel-readback hashes. The native handshake
verified the clone's actual read/write paths before capture commands and retained
the config Cloud guard through its shutdown observation.

An independent read-only comparison in `settings-verification.json` confirmed
all 39 personal files match their backups in bytes, size, timestamps and read-only
flags. The baseline also matches trial 011. No selected personal files needed
restoration writes; only the clone's machine/video configuration changed.
Original/post backups verified, locks released, and gameinfo and plugin cleanup
completed. This verifies the selected files and exact installed binaries; it
does not establish training timing or cover every Steam subsystem.
The user also launched CS2 normally through Steam afterward and confirmed that
audio, video and HUD preferences look correct.
They then clarified that Steam is in Offline Mode. The native guard's blocked
interface acquisition remains measured evidence, while actual online Cloud
synchronization and post-reconnection settings require a separate test. The
later user report is retained as `windows-settings-012/validation-context.json`;
the original capture and verification records remain unchanged.

## Interrupted-run recovery

After an interrupted worker, close CS2 and restore using its recorded journal:

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --repair data/rendered/windows-pilot/gameinfo-recovery.json
```

Repair restores gameinfo, recovers a sibling settings journal if present, and
moves that run's plugin directory back into the workspace. It verifies hashes
and lock ownership and never kills a process using a stale PID from a journal.
Completed settings recovery is idempotent: rerunning it does not undo later
normal preferences.

If the worker was killed before recording the settings poststate, automatic
rollback is withheld. Review the journal, close CS2 and use the explicit recovery
flag to preserve the current state as additional evidence before restoring the
pre-render baseline:

```powershell
.\.venv\Scripts\python.exe tools/renderer/windows.py --repair data/rendered/windows-pilot/gameinfo-recovery.json --seal-current-settings
```

This can undo preferences changed after the interrupted render, so it is never
enabled automatically. If startup failed before creating a gameinfo journal,
use `--repair-settings <run>/settings-recovery.json` instead, with the same
explicit sealing flag when an unsealed complete snapshot requires it. Keep
backups and locks if a restore reports corrupted evidence or an external change;
do not delete them to bypass the conflict. A machine shutdown or force-killed
worker cannot execute its normal cleanup code.

## Inspect the result

Successful capture must contain decodable video, frame provenance, actual video
PTS and logs. Check the requested player, HUD/viewmodel, resolution, colors and
interval visually. Video encoding and probing use FFmpeg. Native pixel checks
and training tensors use the standard-library TGA decoder, which preserves RGB
channel order and image origin from the verified file bytes.

Raw TGAs remain unchanged. `capture_frame_files.json` records each original
native filename, archived filename and SHA256. The capture ledger adds movie
counters and pixel-readback evidence. The encoded visual profile is recorded in
the render manifest; see [HUD cleanup](HUD_PROFILE.md).

### Native POV and camera evidence

Trial `data/rendered/windows-validation-008/` completed with 160 movie frames,
160 pixel readbacks and one measured endpoint. Every movie frame resolved
Ckanic's Steam ID `76561198323592528` through the native local controller,
observer pawn, observer-target handle, target pawn and target controller. Full
entity handles are checked, including their serial bits; the plugin does not
populate identity from the requested slot or player name. Observer mode was 2
throughout this trial; the installed binary's schema enum identifies 2 as
`OBS_MODE_IN_EYE`. The controller entity index was 10; this is recorded as
an entity index and is not assumed to be the demo's player slot.

All 160 frames contained the current `CViewRender` camera position and angles;
its independently read matrix-input position and angles matched those values.
Pawn eye angles and feet position are separate fields. Each observation names
its actual boundary: movie submission, movie endpoint, or before/after native
pixel readback. Camera state agreement is evidence to examine; it does not
alone prove that the camera and target input share a simulation phase.
The complete POV and camera records also matched movie submission and both
sides of pixel readback for all 160 frames.

Native weapon state changed at frame 51 from 20 to 19 Glock rounds, with
`m_fLastShotTime = 262.56298828125` seconds. That frame's measured render time
was `262.58892822265625` seconds. These independent values permit checking
whether a shot preceded an image rather than assuming an integer command tick
is sufficient. The controller's first observed tick base was 16703 and the
pawn's simulation time was `260.984375` seconds. The pawn's native last-executed
command number/tick were both **-1 for all 160 frames**, its simulation tick
was -1, and the movement service's processed command number was 0. These
sentinels provide no usable execution-time anchor.

The observation reader requires exact SHA256 matches for installed client,
schema-system and engine binaries. It resolves named runtime schema fields and
uses bounded `ReadProcessMemory` reads, validated counts and strings, runtime
class checks and complete entity-handle checks. Missing or unreadable fields
remain unavailable; worker-thread observations never call engine interfaces.
The guarded native pause getter is recorded separately from match pause state.
Source evidence, binary RVAs, field names and limitations are documented in
[the native observation contract](../tools/renderer/plugin-windows/OBSERVATION_HOOK.md).

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

These diagnostic outputs remain `training_ready=false`. Measured image identity does not alone
prove the command/render clock epoch, fractional action timing or every player's
POV. The real inspector makes those assumptions visible for review.

For historical 14178 captures, [the separate causal acceptance command](SYNCHRONIZATION.md#accept-and-load-the-first-training-subset)
publishes samples under its original single-command contract. It remains separate
from the current competitive two-command profile below.

### Current competitive acceptance and first batch

After preparing a current competitive capture with `process-render`, explicitly
select its profile when auditing synchronization:

```powershell
.venv/Scripts/python.exe -m cs2_data audit-synchronization `
  --parsed data/parsed/PARSED-DEMO --dataset data/datasets/NEW-CLIP `
  --network-clock data/clocks/SOURCE-CLOCK.json `
  --native-profile cs2-14180-competitive-replay-v1 `
  --out data/validation/NEW-SYNCHRONIZATION
```

Publish the separately verified competitive partition, then materialize a small
image-only batch:

```powershell
.venv/Scripts/python.exe -m cs2_data accept-competitive-controls `
  --parsed data/parsed/PARSED-DEMO --dataset data/datasets/NEW-CLIP `
  --network-clock data/clocks/SOURCE-CLOCK.json `
  --state-context data/context/SOURCE-CONTEXT.json `
  --out data/acceptance/NEW-COMPETITIVE-PARTITION

.venv/Scripts/python.exe -m cs2_data.training_dataset `
  --acceptance data/acceptance/NEW-COMPETITIVE-PARTITION `
  --series-groups data/training/esl-misa-mouz-series-v1.json `
  --output data/training/first-competitive-batch-NEW `
  --batch-size 2 --height 180 --width 320
```

Replace the source paths with their actual hash-bound artifacts and use fresh
outputs. Keep FFmpeg on PATH for revalidation. Opening the partition recomputes
its proof; the tensor loader independently checks the eight-image history and
future supports before producing `batch.pt` and `batch_report.json`.

Only fields with true masks are usable targets. Unknown competitive button
semantics stay masked, and raw commands or private game state are not model
observations. The whole supplied BO3 stays in one split, so independent series
are still needed for validation and test data. See
[COMPETITIVE_ACCEPTANCE.md](COMPETITIVE_ACCEPTANCE.md) for source evidence and
[TRAINING_DATASET.md](TRAINING_DATASET.md) for tensor shapes, masks and dependency
details. Materializing a batch does not run model training.

The latest actual build/run outcome is recorded in
[the progress tracker](progress/STATUS.md). A dry run or successful plugin build
alone does not count as captured training data.
