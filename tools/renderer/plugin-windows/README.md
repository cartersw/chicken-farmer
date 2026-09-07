# Experimental Windows replay plugin

The current build requires the runner's cloned settings profile and native
isolation arguments described in [SETTINGS_ISOLATION.md](SETTINGS_ISOLATION.md).
The guarded build passed `windows-settings-012`: 64 frames, encoded MP4, clean
CS2 exit, verified local paths, and one denied engine config Cloud acquisition.
All 39 selected settings files matched their baseline without restore writes.
Steam Offline Mode is user-reported session context, not independently recorded
by the trial; online Cloud synchronization and reconnection remain untested.
The native interface block was observed directly, independently of that report.
The previous shutdown fault and bounded fix remain documented in the linked
notes. Older DLLs lack these guards and fail isolation preflight.

Opt-in native movie and pixel-readback evidence is described in
[CAPTURE_HOOK.md](CAPTURE_HOOK.md). It requires `-chicken-capture-log` with a new
absolute output path and refuses unknown engine binaries. This evidence does not
automatically make a capture ready for training.

This builds an x64 Windows adaptation of the pinned Reka plugin. It is a
development capture component: successful compilation does not verify its
hardcoded game interfaces against the installed CS2 version. The plugin records
per-frame native capture evidence; command-execution alignment remains a
separate acceptance requirement.

Local checkpoint (2026-09-07): pilot 004 on Windows CS2 1.41.7.8 captured
64 frames at 1280x720/32 fps, encoded a two-second clip, and exited with code
0. The runner restored the original game configuration. Artifacts are in
`data/rendered/windows-pilot-004/`. Capture timing, POV acceptance, and action
execution alignment remain separate verification requirements.

Requirements: a complete Visual Studio C++ x64 toolchain, Windows SDK, Python
3.10+, Git, and CMake supporting the installed Visual Studio version. Visual
Studio 2026 requires CMake 4.2 or later. From the repository root:

```powershell
.venv/Scripts/cmake.exe -S tools/renderer/plugin-windows -B tools/renderer/build/plugin-windows -G "Visual Studio 18 2026" -A x64
.venv/Scripts/cmake.exe --build tools/renderer/build/plugin-windows --config Release
```

CMake fetches the same JSON version and Source 2 SDK commit as the pinned
renderer. `prepare_source.py` generates adapted sources in the build directory
without editing the upstream checkout. Its guarded replacements fail when the
expected upstream source changes. The DLL is emitted at
`tools/renderer/build/plugin-windows/Release/server.dll`.

The adaptation explicitly exports the factory as `CreateInterface`, handles
missing interfaces without dereferencing null, avoids hooking a repeated
factory result twice, forwards the app-system pointer through the shutdown
hook, and requires `-insecure`. Errors write to the log and
terminate instead of waiting for a modal dialog. The runner must pass
`-chicken-render-log <absolute-log-path>` to direct the plugin log into the
worker's output directory. The parent directory must exist; the log argument
is required to avoid touching another session's log. The plugin
consumes commands from the JSON sidecar beside the `+playdemo` file.

A 60-second startup worker locates `Source2Client002` using the loaded
`client.dll`'s exported factory and installs the frame hook once. It shares a
mutex with the original `ClientFullyConnect` path, queues commands for the
engine thread, and is stopped/joined before shutdown. This addresses the
demo-startup lifecycle reported in [CS Demo Manager issue 1458](https://github.com/akiver/cs-demo-manager/issues/1458)
for CS2 1.41.7.6. The issue author's successful capture is upstream evidence;
the local pilot subsequently captured successfully on 1.41.7.8. Factory lookup and vtable installation from the
startup worker remain an experimental integration point.

The generated SDK `icvar.h` backports [AlliedModders' July 9 ICvar layout update](https://github.com/alliedmodders/hl2sdk/commit/11089e8737dd20a830d301fe81cf3bb5cb23bd88).
The first two local trials crashed at the pinned SDK's initial
`GetConCommandData` call: slot 46 resolved into non-executable `tier0.dll` data.
That upstream update removes two obsolete virtual entries, moving this call
to slot 44, and relocates the split-screen slot count member. Both the plugin
and its compiled `convar.cpp` use the same generated header. This addresses
the demonstrated startup fault, and pilot 003 passed that initialization.
Builds retain a MAP file and PDB for native crash diagnosis.

The third local trial passed plugin initialization but stayed in the main
menu: the old command dispatch did not start playback. The generated client
header now follows [HLAE's pinned interface definitions](https://github.com/advancedfx/advancedfx-prop/blob/2e1366353c50d40150276c5d580b0eecb183881b/cs2/sdk_src/public/cdll_int.h):
`ExecuteClientCmd` slot 51, demo-file accessor slot 69, and Windows demo-tick
accessor slot 3. `IsPlayingDemo` remains slot 42. These definitions supply an
evidence-based compatibility change; pilot 004 then reached `startmovie`,
`endmovie`, and `quit` and produced the first clip. General compatibility and
training alignment must still be checked per accepted game build and capture.

On Windows the plugin never restores or deletes a `gameinfo.gi.backup`.
The Windows runner owns staging, the temporary search path, and restoration of
the exact original game configuration. Do not copy this DLL over the real
game server DLL. CMake does not install into CS2 or launch the game.

Upstream copyright/license information remains in
`../upstream/LICENSE` and `../upstream/THIRD_PARTY_LICENSES`; the transformed
sources and plugin continue to depend on those components.
