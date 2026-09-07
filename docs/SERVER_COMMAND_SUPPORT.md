# Server command support evidence

Evidence profile: `server-command-support-v1`, inspected 2026-09-07. This is a
source and installed-binary audit, not a new recording-server experiment. It
supports a bounded **server command processing** target. It does not establish
the wall-clock instant when the original player moved a mouse, prove the exact
rendered camera time, or authorize training samples by itself.

The practical enclosing interval for an eligible command with recorded
`server_tick_executed = E` is **[E-1, E] in server tick coordinates**. The interval
contains the ordinary one-tick movement step and the inspected zero-duration
special case. It does not mean every field changed throughout that interval.
Out-of-range subtick fractions and unmodeled command flags are excluded from
this first profile. An independently verified ceiling on information already
consumed by the renderer is still required before calling the command future.

## Exact inspected sources

All native addresses below are RVAs, relative to the module load base. The
preferred PE image base is `0x180000000`.

| Artifact | SHA-256 / pinned revision |
| --- | --- |
| Installed `game/csgo/bin/win64/server.dll` | `9e5749d77dcb68883477feae751a3f28068d119ec145edcb0e4d48d15b538d36` |
| Installed `game/bin/win64/engine2.dll` | `26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac` |
| Installed `game/csgo/bin/win64/client.dll` | `b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4` |
| AlliedModders HL2SDK | `9ab16fa9fcdeeb30565dfdbf6fbb312356978a0b` |
| SDK `public/globalvars_base.h` | `1e714571fa15ac232069d0c888ec2dbce341c7df8bfcd37e86c95be1637c64f7` |
| SDK `public/eiface.h` | `8acdc5e80746170baac8f9435e9a959d1baaaf3de465b02220306ac68ae53fa5` |
| demoinfocs | `github.com/markus-wa/demoinfocs-golang/v6 v6.0.0-alpha.0` |
| Parser `pkg/demoinfocs/s2_commands.go` | `7986494416741df56e3642e1571a058b831df93109063f1c6ed94a5b719dd960` |
| CS2KZ SDK and movement metadata | `4c11294f792823059d3976a2fead220564e24304` |

The installed game root is
`C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive`.
The parser file hash was checked against the downloaded module ZIP as well as
the extracted module cache; the two files agree. The SDK is pinned by
`tools/renderer/plugin-windows/CMakeLists.txt`. These hashes describe the
inspected installation. They are not a claim that the supplied FACEIT recording
server used these identical DLLs. Reusing this profile after a game update
requires a new native audit and fresh runtime evidence.

The SDK defines `ISource2Server::SetGlobals(CGlobalVars*)` and places
`CGlobalVarsBase::tickcount` at byte offset `0x44` in the inspected x64 layout.
Its comments distinguish simulation, packet processing, and rendering uses of
these globals. Sources:
[server interface](https://github.com/alliedmodders/hl2sdk/blob/9ab16fa9fcdeeb30565dfdbf6fbb312356978a0b/public/eiface.h),
[global variables](https://github.com/alliedmodders/hl2sdk/blob/9ab16fa9fcdeeb30565dfdbf6fbb312356978a0b/public/globalvars_base.h).

## Native producer and movement path

The following observations come from read-only disassembly with VS
`dumpbin /disasm /range:...`, PE RTTI inspection, and embedded schema metadata.
`tools/renderer/plugin-windows/inspect_pe.py` supplies the PE/RTTI reader.

1. The `CSource2Server` main vtable is `0x18077B8`. Slot 12 is `0xD3D250`,
   matching the SDK's `SetGlobals`. Its store at `0xD3D26C` writes the supplied
   globals pointer to `server + 0x1C9F350`. The fallback pointer and warning
   callback are also initialized here. This identifies the pointer used by the
   exporter; it is not inferred only from nearby floating-point values.

2. Live command exporter `0xDE99E0` receives a `PlayerCommand`. At
   `0xDE9D11` / `0xDE9EAA`, the full and delta paths copy the command number.
   At `0xDE9D1D`–`0xDE9D2B` and `0xDE9EB3`–`0xDE9EBF`, they load
   `[server + 0x1C9F350]->tickcount` and store it in the outgoing
   `CMsgServerUserCmd` field at `+0x2C`. The embedded diagnostic string at
   `0x18432E0` labels the same value “HLTV UserCmd Server Tick”. The exporter
   copies client tick separately from the command base. Prediction offset and
   client tick do not replace the outgoing server tick in this path.

3. The exporter call at `0xDE3625` follows command processing in the same
   loop. The caller sets controller `m_nTickBase` to the current global tick
   minus one at `0xDE353D`–`0xDE3542`, sets the current command pointer, obtains
   its pawn, and invokes movement-service virtual slot 25 at `0xDE3579`.
   Embedded schema records independently identify pawn `+0xA70` as
   `m_pMovementServices` (record `0x1C37A30`) and controller `+0x4B8` as
   `m_nTickBase` (record `0x1C760A0`). Getter `0xB14B90` and setter
   `0xB35850` access the latter field.

4. `CCSPlayer_MovementServices` vtable `0x17A16C0`, slot 25, points to
   `0xAA4350`. This checks `sv_runcmds` through `0xA5E6F0`, then invokes
   base movement function `0xC24760`. The convar name is embedded at
   `0x179AB90`; its registration at `0xC45C0` uses default true. Independent
   maintained CS2KZ metadata names Windows slot 25 `PlayerRunCommand` and
   identifies it by this same convar. See
   [pinned movement metadata](https://github.com/KZGlobalTeam/cs2kz-metamod/blob/4c11294f792823059d3976a2fead220564e24304/gamedata/cs2kz-core.games.txt).

5. `0xC24760` advances ordinary controller tickbase by one, temporarily sets
   simulation globals to that tick, processes the command, and restores the
   caller's global tick/time before returning. The relevant advance is
   `0xC247B3`–`0xC247C1`; the temporary tick write is `0xC2495B` and its
   restoration is `0xC249D6`. Thus the subsequent exporter stamps the enclosing
   server step E, not a client sampling tick or a rendering tick.

6. The movement interval constructor `0xC07D40`, called from `0xC22EC4`,
   receives simulation tick Q. It writes move-data end tick `+0xD8 = Q`,
   start fraction `+0xDC = 0`, end fraction `+0xE0 = 1`, and ordinary start
   tick `+0xD4 = Q-1`. For the ordinary path above, Q equals E. A non-null
   native `PlayerCommand::parentcmd` at `+0x90` selects start tick Q and a
   zero-duration step; it also bypasses the earlier tickbase increment, so
   Q can remain E-1. Both cases are enclosed by `[E-1,E]`; the pointer is not
   present in the recorded protobuf. The pinned
   [PlayerCommand layout](https://github.com/KZGlobalTeam/cs2kz-metamod/blob/4c11294f792823059d3976a2fead220564e24304/src/sdk/usercmd.h)
   corroborates its native-only extension.

7. The subtick movement loop `0xC16F70` reads these start/end fields at
   `0xC170C6`–`0xC170E2`. It uses the substep fraction to establish temporary
   simulation tick/time at `0xC1710C`–`0xC17190`. Negative fractions explicitly
   decrement the tick and add one; fractions at least one increment the tick
   and subtract one. Consequently, negative raw `when` values cannot simply be
   called corrupt protocol. They are unsupported by the narrow `[E-1,E]`
   acceptance profile and must retain their original value and rejection reason.

This proves an execution-step interpretation of the ordinary server movement
path in this binary. It does not prove that every recorded key causes a world
effect: paused, dead, frozen, disabled-command, or other game rules can suppress
effects. The separate state and action-quality gates remain necessary.

## Full checkpoints and replay reception

There is another exporter, `CSource2Server` slot 83 at `0xD3F450`, which serializes
each controller's cached latest command and stamps it with the **current**
global tick (`0xD3F5B7`–`0xD3F5C7`). Therefore, a seek checkpoint's envelope tick
is not sufficient evidence for the cached command's original processing step.
The pinned parser explicitly skips `svc_UserCmds` embedded in full packets in
`s2_commands.go`. Its ordinary full-payload command is still eligible: a full
protobuf payload in the live packet stream is different from a demo full
checkpoint. Consumers must preserve that distinction and bind to the verified
parser/source artifacts.

Replay does not resimulate these recorded inputs. In the installed engine,
UserCommands message 76 reaches `CNetworkGameClient` slot 128 (`0x89340`), which
forwards to `Source2Client002` slot 181. The inspected client target
`0xB344A0` is an immediate return (`C2 00 00`). Runtime instrumentation verifies
the expected objects/vtables before reporting this observation. Receiving an
envelope establishes provenance/clock correspondence; it is not replay command
execution or a visual response measurement.

## Implementable first target contract

For each candidate command, require source/hash identity, complete reconstruction
and input presence, stable player/round identity, competitive live state,
known non-paused/non-freeze state, supported command flags, and finite subtick
fractions within `[0,1]`. Preserve the whole raw protobuf. Do not invent a missing
parent input message. Within a verified present protobuf parent, an omitted
optional scalar has its defined protobuf default; retain the original presence
information rather than calling that default an independently observed physical
input.

Record `command_support_profile = server-command-support-v1`,
`support_clock = server_tick`, `support_start = E-1`, `support_end = E`, with
both endpoints included. If a target feature depends on several commands, its
support is the union of those commands' supports.

Given an independently established inclusive information ceiling B for **all**
frames in a history window, require `support_start > B`. Equality fails closed.
For integer B, the earliest qualifying command has `E >= B+2`. This deliberate
margin avoids assigning a command that partly overlaps already available server
information. A matching render-time float, a receipt of NET_Tick, or a matching
PacketEntities clock alone does not establish a ceiling on every message
consumer, seek-restored state, event, effect, or retained buffer. That proof is
a separate capture-validation obligation.

| Label | Meaning and additional gate |
| --- | --- |
| Base analog axes | Recorded command axis values associated with step E; not measured displacement or a reconstructed analog trajectory. |
| Raw button planes | Preserve all three planes and presence. A key held at the end of a command and an intra-command press/release sequence are different labels. No semantic remapping is authorized by this audit alone. |
| Base pitch/yaw | Recorded command orientation associated with step E; not necessarily the last input-history angle or rendered camera angle. |
| Wrapped aim difference | Difference of adjacent command orientations, with valid predecessor identity/continuity. Include **both** command supports in the future test; otherwise mask this feature. It is not automatically equivalent to physical mouse delta or a pixel-space turn. |
| Raw `mousedx/mousedy` | Predict the recorded integer fields at command E when present. Do not claim a physical sampling duration or infer unknown sensitivity. |
| Subticks / input history | Retain as provenance. Exclude from the first training feature set unless a separate field-specific timing contract is implemented. |

`prediction_offset_ticks_x256` is a recorded prediction/history quantity, not a
replacement for the native exporter's server clock. In the 960 audited commands
from player captures 008–010 it was always present and ranged from 1029 to 1409.
Subtracting it did not yield an exact history-clock identity. `cmd_flags` was
absent/default in 959 commands and was 128 in one command (raw row 88235); that
command also contained older history. Its exact flag semantics remain unproven,
so this first profile excludes nonzero/unsupported flags. An absent optional
flag may be treated as protobuf default zero only through a verified protobuf
decoder; this does not waive the existing input-field presence requirements.

The movement engine consumes derived move data, and aiming/weapon systems can
apply recoil, constraints, lag compensation, and rules. This profile predicts
the recorded server command; it does not promise that an unmodified base value
is the exact final world effect or that every effect is confined to one tick.

## Cross-checks and current limits

The supplied Dust2 commands have a constant retained-command envelope offset
`E - demo_tick = 10703`. Across 512 competitive, unambiguous shot events with an
exact same-tick command and one valid attack press, 497 weapon last-shot clocks
match `E-1+when`; 15 have positive residuals of approximately 0.055–0.805 ticks.
Those results corroborate an execution clock but do not prove every shot's
effect time or the rendered camera phase. They are not used to manufacture a
causality pass.

The original Dust2 `CDemoFileHeader`, decoded directly from the source bytes,
records `patch_version=14178`, matching installed CS2 `1.41.7.8`,
`build_num=10896`, and `server_start_tick=10703`. Its retained protobuf SHA256 is
`21bd36883475d25d5dc3a1ccc2610291e9db63ca15361fc601af3f721e6c47e9`.
This provides recorded producer-version and clock-origin evidence in addition
to the independent live packet/envelope comparisons. It does not identify a
server DLL by hash; the native implementation audit remains scoped to the
explicitly inspected binary and the supported recording profile.

Other concrete counterexamples from captures 008–010: 463 changing base-angle
commands have no subtick move entries; final input-history angles often differ
from base angles; a jump press/release can share the same fraction. These rule
out treating base-angle changes as a complete subtick trajectory or reducing
all button activity to a single held-bit label.

This audit does not promote `training_ready`. Acceptance still needs a
hash-bound proof of the rendered POV, capture correspondence, complete
information ceiling including checkpoint/seek behavior, the chosen command
profile's field requirements, and all existing per-window state/quality gates.
The installed binary audit is explicitly version-scoped; compatibility with the
supplied recording's producer must be supported by its retained envelopes and
independent runtime/clock evidence, not assumed from the DLL name.
