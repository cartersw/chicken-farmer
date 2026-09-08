# Native compatibility: installed 1.41.8.0

The current competitive path was added on 2026-09-08 as
`cs2-14180-competitive-replay-v1`, selected only by
`-chicken-competitive-replay`. Calibration and competitive flags cannot be
combined. The Windows worker checks all eight registered binary hashes before
staging and again before launch; source-command acceptance remains a separate
original-demo audit. Unmarked replay keeps the historical profile.

The direct reader/filter comparison against the recovered original binaries is
retained at
`data/native-profiles/current-competitive-replay-v1/binary_comparison.json`
(SHA-256 `6f5b3484bb48fee81ec9aae0a3df0d1c786e66827413d95687c62e047f7c8cc0`).
Eight compared ranges account for every changed byte with 39 target relocations;
the independently recovered filter drop sets agree. This supports the selected
packet-reader/filter contract, not whole-game behavior or latency equivalence.
Fresh packet bytes and companion frame/POV evidence are still required.

Competitive runs also emit `competitive_resource_paths` once after capture
initialization. The diagnostic uses the pinned filesystem interface's
`RelativePathToFullPath` slot 41 (`0x5d2d0`) and already checked `GetSearchPath`
slot 43 (`0x54480`) to report GAME/MOD selection. It reads no more than 1 MiB
when hashing a resolved loose file, changes no search path, and does not claim
that Panorama consumed a resource. Actual HUD images require separate review.

## Original calibration inspection

The installed engine and client changed after the historical 1.41.7.8 replay
trials. A read-only inspection on 2026-09-07 established the specific contracts
below for the new pair. This supports a separate guarded calibration experiment;
it does not extend the old replay acceptance proof or establish successful
calibration, pixel correspondence, input consumption, or physical-device latency.

`cs2-14180-calibration-v1` is selected only by `-chicken-calibration-plan` or
`-chicken-calibration-replay`. Ordinary replay retains the historical engine/client
hashes and RVAs. Unknown hashes still abort. The settings, thread, vtable,
message-layout, pointer-readability, and caller checks remain required.

## Binary identities and reproducible evidence

| Module | Inspected SHA-256 |
| --- | --- |
| engine2.dll | `1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07` |
| client.dll | `809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae` |
| schemasystem.dll | `e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512` |
| filesystem_stdio.dll | `a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef` |
| rendersystemdx11.dll | `45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500` |

Schema and filesystem whole-file hashes equal their historical guarded values.
The calibration worker and native calibration profile also require the renderer
hash above. The native guard checks readback slot 74 at `0x3f190` and texture-info
slot 90 at `0x30190` before replacing the readback entry.

[audit_compatibility.py](audit_compatibility.py) reads PE files without loading
them, archives the exact bytes inspected, and runs MSVC dumpbin on those copies.
It records hashes, RTTI/vtable targets, selected instruction bytes, import names,
strings, and bounded function disassemblies. Run it with a fresh output directory:

```powershell
.venv/Scripts/python.exe tools/renderer/plugin-windows/audit_compatibility.py `
  --profile calibration14180 `
  --game 'C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive/game' `
  --output .cache/native-compatibility-review-new `
  --dumpbin 'C:/Program Files/Microsoft Visual Studio/18/Community/VC/Tools/MSVC/14.51.36231/bin/Hostx64/x64/dumpbin.exe'
```

The completed audit is in
`.cache/native-compatibility-calibration14180-20260907-v2/`: 57 selected checks
passed, with 53 function/range disassemblies and five binary snapshots. Its
`audit.json` SHA-256 is
`ac80971c8fb61e5d86beb34f5654b1de0007514b61d09178b06a06771ee0a8a5`.
The report deliberately retains `runtime_compatibility_verified: false`.
No complete archived legacy engine/client pair was available for a whole-function
byte comparison. “Same contract” below means the listed operands, layouts, and
control flow were checked against the earlier evidence; it does not mean every
byte or behavior of the game stayed identical.

## Engine locations that moved

Every address is an RVA. Only individually inspected locations use the new value;
there is no module-wide subtraction rule in the native implementation.

| Contract | Historical RVA | Calibration RVA |
| --- | --- | --- |
| CMovieRecorder slot 9 | `0x100af0` | `0x100ae0` |
| CMovieRecorder slot 2 | `0x100280` | `0x100270` |
| OnClientOutput movie-call return | `0x1e5b58` | `0x1e5b48` |
| Synchronous screenshot readback-call return | `0x1e88c6` | `0x1e88b6` |
| CNetworkClientService slot 23 getter | `0x1d2bc0` | `0x1d2bb0` |
| Config RemoteStorage context initializer | `0x1f2430` | `0x1f2420` |

The CMovieRecorder table remains `0x54c460`. The new frame function reads state
`+0x168`, prefix `+0x130`, counter `+0x138`, and word flags `+0x14c`; its counter
increment is at `0x100e74`. The end function still transitions the same recording
state. `CRenderService::OnClientOutput` now begins at `0x1e5840`; it preserves the
event argument in R14 at `0x1e5876`, reads event floats `+0x28/+0x2c`, and forwards
the same event at `0x1e5b42` before the slot-9 call at `0x1e5b45` (`FF 50 48`).
This preserves the render-time field argument described in [CAPTURE_HOOK.md](CAPTURE_HOOK.md).

The synchronous screenshot path still branches on request byte `+0x20`. Its
slot-74 function load is at `0x1e8896`, followed by `call r10` at `0x1e88b3`.
The ninth argument is written as a DWORD at `0x1e887e`. In the inspected renderer,
vtable `0x3ec3f8` still has readback slot 74 at `0x3f190` and texture-info slot 90
at `0x30190`. The callee loads the ninth DWORD at `0x3f21d` and stores it into
queued-command `+0x48` at `0x3f224`. The corrected full-width wrapper remains
necessary. The native caller still reads width/height as words and format at
texture-info `+0x0c`. Fresh TGA/RGB hash reconciliation is required after a run.

## Settings and startup contracts

The config context remains `0x60f1f0`, with counter `+8` and cached interface
`+16`. Its new initializer calls unchanged IAT `0x482698`, verified to be
`steam_api64.dll!SteamInternal_FindOrCreateUserInterface`, with the exact
`STEAMREMOTESTORAGE_INTERFACE_VERSION016` string, and stores the result.

Config-read context acquisition is now at `0x1f3cc3`; the null check at
`0x1f3cd0` bypasses the subsequent Steam calls. Config-write context acquisition
is now at `0x1f3ffa`; its null check at `0x1f4007` follows the failure-return path
without a Steam storage call. USRLOCAL setup now starts at `0x20a7e0`: it formats
`USRLOCAL%s`, calls getenv at `0x20a841`, and takes the supplied path to filesystem
AddSearchPath slot 31 at `0x20a90b`. The Steam-userdata fallback is only taken when
the environment value is absent. The unchanged filesystem whole-file identity
preserves the inspected search/write query implementations.

These are the same scoped protections described in
[SETTINGS_ISOLATION.md](SETTINGS_ISOLATION.md). Startup cache-zero checks, the
live import-pointer check, native USRLOCAL verification, DLL pinning through
shutdown, and the external settings transaction must still pass. This audit
adds no claim about online Steam Cloud, reconnection, or Steam-client isolation.

## Observation and clock contracts retained

The following current-client contracts were independently inspected a second
time; `.cache/current-client-observation-disassembly.txt` preserves that review:

- Local-controller getter references array `0x23a0f30` at `0x93ab03`; entity-count
  function `0xad6c20` references system global `0x23ad958`. Resolver `0x9be990`
  retains page array `+0x10`, nine-bit page/index splitting, stride `0x70`, and
  identity handle `+0x10`. Full serial-bearing handle checks remain in our reader.
- CViewRender table `0x1b331c0`, object `0x23d9c90`, constructor `0xbb0830`, and
  slot-4 method `0xbc1ba0` remain at their inspected locations. The method supplies
  FOV `+0x4a8`, origin `+0x4b0`, and angles `+0x4c8` to matrix setup `0x829490`;
  it stores frame count to `0x23cb500`. The in-eye schema enum remains value 2.
- Named pawn/controller/weapon fields continue to be resolved dynamically from
  the runtime schema. A new client can register a different field set despite
  the unchanged schema-reader DLL; missing/invalid values remain unavailable.
  Pawn state and rendered-camera state remain distinct observations.
- CDemoPlayer table `0x52db68`, pause/start/current getters `0x35a50/0x35a80/0x35a90`,
  and ReadPacket slot 22 at `0x2b820` are retained. Packet data `+0x50`, byte count
  `+0x74`, selected source tick `+0x22c`, and return-object `+0x1e0` accesses remain
  present in the bounded reader disassembly. Selected seek/filter member accesses
  remain at the documented locations.
- CNetworkGameClient table `0x5335d8` retains handlers `0x6a2d0`, `0x6afe0`,
  `0x89340`, and `0x47a00`. Server-tick commit is still self `+0x37c` at `0x6a76c`.
  Service table `0x573890` slot 23 now directly returns self `+0xa0` from
  `0x1d2bb0`. Message RTTI tables and the read member layouts remain unchanged.
  The UserCommands client destination remains slot 181, `0xb344a0`, with exact
  bytes `C2 00 00`; receipt is not reconstructed-command execution.

The protobuf serializer proof locations moved from `0x174c80/0x1607c0/0x14c200/0x16d170`
to `0x174c70/0x1607b0/0x14c1f0/0x16d160`. These are archived inspection locations,
not additional native calls. The old [CLOCK_HOOK.md](CLOCK_HOOK.md) information
ceiling and source-filter acceptance profiles remain pinned to their original
binaries. Inspecting these contracts does not automatically certify them for a
new replay version.

## First protected startup result and remaining checks

`data/calibration/control-001/` reached the current profile's observation,
ReadPacket, network-clock, and capture initialization. It resolved 518 schema
fields, observed and denied one engine RemoteStorage016 request, and verified
the cloned USRLOCAL path. It then aborted before action dispatch with
`Calibration requires de_dust2`. It is a failed run, not a calibration result.

CEngineClient table `0x538290` has valid map getters in slots 64 and 65:
`0x75b50` reads client string `+0x210`; `0x75bb0` reads `+0x218`. They return empty
strings before a connected client exists, and dedicated-server strings on their
dedicated branch. A bounded stage-zero wait for the requested map is appropriate;
the observed map still must match before any controls are dispatched.

A successful fresh run must independently demonstrate clean process exit,
settings verification, correct local player/map, scheduled dispatch evidence,
complete frames, and matched pixel hashes. Simulation scheduling time, QPC wall
time, command dispatch, actual input consumption, and replay observations remain
separate clocks/events. The compatibility audit alone proves none of their
latencies or synchronization, and never sets training or live-control readiness.

## Input-value registry and button command support

Trial `control-005` reached a living first-person local pawn, then rejected
`+forward` because `ICvar::FindConCommand` did not find it. Inspection established
that this is the wrong registry for these input values; removing the availability
check without replacing its evidence would be insufficient.

Client initializer `0xcdb50` passes the bare `forward` string (`0x1b16c30`) and
descriptor `0x20b3b08` to constructor `0x119c3e0`. The constructor stores the name
at descriptor `+0`, initializes type `+8` to zero, and registers through the
`InputService_001` pointer at client `0x25eaa48`, slot 37. Engine RTTI identifies
the service table as `0x570208`; slot 37 is `0x1c1440`. Its diagnostic strings
explicitly identify `CInputService::RegisterInputValue`, and it rejects a name
that already belongs to a ConCommand or ConVar. Registration stores the descriptor
in the service's separate input-value registry.

The ordinary engine command dispatcher `0x1c4400` calls input parsing
`0x1b91b0` at `0x1c44fa`, before ICvar lookup. The parser checks `+` at `0x1b91f8`
and `-` at `0x1b9202`, skips that character at `0x1b920d`, and looks up the bare
name using service `+0x32f98`. It obtains the descriptor through entries
`+0x32f88` at `0x1b93b6`, using 16-byte records. A type-zero descriptor accepts
the `+/-` form with up to three command arguments; `+` selects float `+1` and
`-` selects float `-1` before helper `0x18c890`. These are native parser values,
not a claim that the negative value is a physical-key timestamp or an analog
movement target. The other descriptor route uses helper `0x18cb50`.
The name lookup at `0x1b923a` calls IAT `0x483ba8`, whose exact imported symbol
is `tier0.dll!CUtlSymbolTable::Find(const char*)`. It searches the separate
input symbol table; stripping the prefix and querying ICvar would still use
the wrong registry.

A supported runtime availability check can use bounded engine-thread reads of
the actual service pointer, table, slot-37 target, registry count/array, exact
descriptor name, and type. The engine's own descriptor enumeration at `0x1c1740`
uses count `+0x32fb8` and array `+0x32f88`; the allocated logical extent at
`+0x32f80` provides an additional bound. Missing, duplicate, unreadable, or
wrong-type entries must remain unavailable. Ordinary console commands continue
to use their appropriate ICvar availability checks. The dispatch string remains
`+forward`/`-forward`; stripping the prefix is only for the registry lookup.

The parser can report a recognized action without applying it: service byte
`+0x3ec4c == 0` takes that path at `0x1b9279`. A runtime experiment must check
that state separately and retain subsequent input/state observations. Registry
membership is therefore **command support**, not proof of input consumption,
movement effects, matching physical button semantics, or training readiness.

The six relevant bounded disassemblies are retained under
`.cache/input-value-dispatch-14180/`, linked to the archived engine/client hashes.
Its `audit.json` SHA-256 is
`288fcc167cb8beaf90a7a1c0762968cbf04294cb46b8c134ee50de16e40d54d0`.
The static parser route was independently identified by a second reviewer.

## Complete UserCmd recording for controlled probes

Trial `control-006` recorded 706 delta payloads without an initial baseline;
none could be independently reconstructed. The retained recording is a negative
case. The calibration setup now selects the existing local-server option
`sv_cq_delta_encode_svc_usercmds = false` before starting the recording.

On the pinned Valve server binary, registration at `0x102b2c` creates the
existing reference at `0x202b8c8` with flags `2` (development-only). Recipient
partitioning at `0xde9aba` and payload writes at `0xde9d11` / `0xde9f15` show
that false selects full command serialization. The initial ordinary lookup in
trial `control-007` failed: tier0's `CCvar::FindConVar`, slot 11 at `0x6c6b0`,
filters this flag combination for both values of its lookup Boolean.

The current implementation hashes the original Valve server module and tier0,
then validates the existing reference's access index, registered index, cached
data pointer, exact name and Boolean type. It uses that nonowning reference and
the engine's `SetBool(false)` API, with readback, in the controlled local server.
It does not register a replacement variable or change the variable's flags.
The setting is confined to this process and is not written to personal configs.
The complete static evidence is in
`.cache/calibration-usercmd-delta-14180/audit.json` and its bounded disassemblies.

Trial `control-008` supplies the independent runtime check: the demo contains
711 full payloads, zero delta payloads and 711 reconstructed commands without
parser warnings or protobuf projection errors. This establishes recording
coverage for that run. It does not establish physical-device timing, general
button-plane semantics, or training acceptance.

The eight modules pinned by the calibration worker are also retained at
`data/native-profiles/calibration-14180-v1/`, with original game-relative paths
and verified SHA256 values in `binary_archive.json`. This 91,274,944-byte local
archive preserves the inspection inputs across game updates; it does not
contain a complete runnable game installation.
