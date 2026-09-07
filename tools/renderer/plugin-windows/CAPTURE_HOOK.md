# Native capture evidence experiment

`-chicken-capture-log <absolute-new-file.jsonl>` enables opt-in instrumentation.
The file must not exist. Without this argument, the capture hooks are not installed.
The plugin continues to require `-insecure`.

This experiment does **not** certify frame/input synchronization. Its header sets
`pixel_correspondence_validated: false`, `fraction_available: false`, and
`training_ready: false`. Retained TGA pixels and the ledger must be reconciled
before promoting any association. Both integer replay cursors and floating-point
render seconds are observed. Their mapping to executed user commands and precise
action phase still require independent validation.

## Evidence and version guard

The hook refuses to install unless the loaded `engine2.dll` file SHA256 is exactly
`26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac`
(locally installed CS2 1.41.7.8 on 2026-09-07). These offsets are observations of
that local binary, not a public Valve interface or an assumption about later builds.

The read-only [inspect_pe.py](inspect_pe.py) locates MSVC RTTI/vtables.
MSVC `dumpbin /disasm /range:<start>,<end> engine2.dll` established:

| Native location (RVA) | Evidence |
| --- | --- |
| CMovieRecorder vtable `0x54c460`, slot 9 / function `0x100af0` | Native per-movie-frame function. While recording TGA, formats `%s%08llu.tga` from object prefix `+0x130` and counter `+0x138`; calls screenshot request `0x1e6fa0`; increments the same counter once. |
| CMovieRecorder slot 2 / `0x100280` | End request transitions recording state at `+0x168` to 2, then flushes/finishes via `0x100370`. Ledger observes the tick before this transition. |
| Screenshot request `0x1e6fa0` | Queues a request containing KeyValues `filename` and `useexactfilename`. Request `+0x20` defaults to false. This is a queue submission, not pixel capture. |
| Screenshot processing `0x1e8510` | Consumes requests. False `+0x20` takes synchronous texture-pixel readback; true takes a separate asynchronous callback path. |
| Call at `0x1e88c3`, return `0x1e88c6` | Synchronous TGA-path readback through render-system vtable slot 74. The hook records only this caller, after invoking the original readback. |
| Render-system global `0x688c68`, slot 90 | Same native screenshot caller obtains texture dimensions/format through this method. Hook uses it to determine readback extents and rejects unsupported formats/extents for hashing. |

The matching `rendersystemdx11.dll` CRenderDeviceDx11 vtable `0x3ec3f8`,
slot 74 / function `0x3f190`, reads its ninth argument as a full DWORD and
copies it to queued command field `+0x48`. It must be forwarded as `int32`,
even when the native screenshot caller passes zero. An initial experimental
wrapper used `bool`; its byte-width stack write left upper bytes uninitialized
and caused the render worker's copy to fault in trial `windows-competitive-002`.
The corrected wrapper forwards all native scalar widths and only hashes the
observed default ninth-argument value zero. Texture metadata is queried after
the original synchronous readback completes.

Actual movie calls and readbacks are logged independently. Each readback includes
the last observed submission as a **candidate**, not a proved mapping. Its RGB
pixel SHA256 and vertically flipped RGB SHA256 let a retained TGA prove or refute
pixel identity without trusting queue order, FPS, or filesystem timestamps.
Both before/after integer `IDemoFile::GetDemoTick()` values are obtained only when
the callback runs on the known engine thread; otherwise the tick is null.
The movie event fields at `+0x28` and `+0x2c` are also logged as
`render_time_seconds` and `real_time_seconds` after establishing the call path below.
The conversion from those seconds to executed-command ticks still requires an
independent calibration of epoch and tick rate. No executed-command calibration is invented.

### Render-time field proof

The exact HLAE [addresses.cpp](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/addresses.cpp)
pattern for `CRenderService::OnClientOutput`
uniquely locates function RVA `0x1e5850` in the guarded engine binary. Its profiler
strings are `OnClientOutput` (RVA `0x576a08`) and `RenderService::OnClientOutput`
(`0x5767c0`). Instruction `0x1e5886` preserves the original event argument from
RDX in R14. At `0x1e5b52`, that unchanged argument is passed in RDX to
CMovieRecorder slot 9 (`call` at `0x1e5b55`, return `0x1e5b58`). The hook verifies
that return address at runtime before assigning field semantics.

[AlliedModders SDK IEngineService.h](https://github.com/alliedmodders/hl2sdk/blob/bd17582be4bc18970e4fe4c518359872fda8276d/public/engine/IEngineService.h)
defines `EventClientOutput_t` with `m_LoopState`, `m_flRenderTime`, and
`m_flRealTime`. Its
[EngineLoopState_t](https://github.com/alliedmodders/hl2sdk/blob/bd17582be4bc18970e4fe4c518359872fda8276d/public/iloopmode.h)
occupies `0x28` bytes on Windows x64 (three handles, four int32 values), placing
the two floats at `0x28` and `0x2c`. The pinned June SDK has the same declarations.
This establishes a render clock at the actual movie submission. It does not
establish the execution-clock epoch, player POV, or interpolation policy by itself.

### Native demo epoch observation

The adapted HLAE interface already declares `GetDemoStartTick()` in slot 2.
For the guarded binary, CDemoPlayer vtable `0x52db68` has slot 2 at `0x35a80`
(direct return of int member `+0x204`) and slot 3 at `0x35a90`. The latter
calls CNetworkClientService slot 56 and subtracts **that same** `+0x204` member
before returning `GetDemoTick()`. CNetworkClientService slot 56, `0x1d6630`,
directly returns its current tick member `+0x90`.

Every recorded frame and endpoint observes `demo_start_tick` through that getter;
the two getter vtable addresses are checked before calling. A downstream validator
can establish whether this observed native epoch is constant and agrees with the
canonical demo clock. This does not on its own prove executed UserCmd sub-tick
causality or permit training readiness.

The public HLAE implementation informed the distinction between engine and render
thread timing. At commit `03f972b4710ab9a8b1601fc0c34ce33c9c0268cc`,
[RenderServiceHooks.cpp](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/RenderServiceHooks.cpp)
delimits engine render passes, and
[RenderSystemDX11Hooks.cpp](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/RenderSystemDX11Hooks.cpp)
queues GPU capture work for those passes. A generic frame-stage notification alone
does not establish that native `startmovie` wrote the corresponding pixels.

## Ledger

- `header`: schema 1, hook and engine identities, clock/validation limitations.
- `cvar`: first-frame readback of selected HUD CVar values; also written to plugin.log.
- `movie_frame`: basename `movie_name`, native full prefix, counter `capture_index`,
  basename `tga_filename`, before/after replay tick, raw native event values,
  `counter_after`.
- `pixel_readback`: readback index, candidate submission, actual before/after tick,
  dimensions, Source image format, success, RGB and vertically flipped RGB hashes.
- `movie_end`: last observed counter-after and actual replay/render times at the
  first native movie callback after recording has stopped. The explicit phase is
  `first_movie_callback_after_recording_stopped`. It is not extrapolated from FPS.
- `movie_stop_request`: optional native end request observation. The engine's
  console command can bypass that virtual entry, so it is not the final endpoint.

Only successful synchronous byte-channel readbacks have pixel hashes. RGB888,
BGR888, RGBA8888, ABGR8888, ARGB8888, BGRA8888 and BGRX8888 are supported. Hashing
adds CPU work; this is an offline evidence run, not a real-time performance target.
Movie/render hooks remain installed for the lifetime of the owned CS2 process,
like the existing frame hook. Every ledger record is flushed immediately.

## Required runtime checks

1. The exact-binary guard passes and CS2 exits normally.
2. Every expected movie frame has one counter increment and a retained filename.
3. Every retained TGA decoded to RGB matches a successful native readback hash,
   without ambiguous duplicate-pixel associations being silently accepted.
4. Readback and submission clocks, thread IDs and order reveal the actual relation;
   missing or inconsistent events reject the association.
5. The subsequent native movie callback supplies a real endpoint after the last
   frame. No nominal FPS interval is substituted if it is missing. The command's
   before/after tick and native recorder state are additionally traced in plugin.log.
6. Fractional simulation/render time and executed-command calibration are validated
   separately before training readiness is possible.

The implementation builds with x64 MSVC. Trial `windows-competitive-002`
validated the native movie hook and HUD CVar readbacks, then exposed the readback
ABI bug described above. After the fix, `windows-competitive-004` completed with
160 movie submissions and 160 successful pixel readbacks, all on the engine
thread with stable before/after ticks. All 160 distinct retained TGA RGB hashes
matched their corresponding readback candidates. The first submission/readback
cursor was 6002 despite `startmovie` being issued at cursor 6000. The final frame's
cursor and the `endmovie` command cursor were both 6320; the native virtual end
hook did not fire because the command calls its implementation directly.

Trial `windows-competitive-005` recorded a subsequent native endpoint after all
160 frames: cursor 6322, render time 265.9949035644531, recorder state 0.
The console-command trace was still state 3/counter 159 immediately before and
after issuing `endmovie`, since execution was queued. The later native callback
provided the actual endpoint independently of that command dispatch.

Trial `windows-competitive-006` observes `demo_start_tick = -5546` after the seek,
with first-frame `GetDemoTick() = 6002`. The corresponding native client-service
tick is therefore 456. This is **not** the server/UserCmd execution tick near
16705, nor the canonical server/demo offset 10703. The native getter describes
a playback-loop epoch and must not be used to replace execution-clock calibration.
Its mismatching value is useful negative evidence against conflating those clocks.

Training readiness remains false; frame/input alignment must use a separately
justified clock mapping and verify POV/action interpretation.

The subsequent [network clock experiment](CLOCK_HOOK.md) observes delivered
NET_Tick, PacketEntities, and UserCommands envelopes at native handlers, with
their clocks sampled at submission/readback. It preserves these render clocks
and does not automatically certify synchronization or change acceptance flags.
