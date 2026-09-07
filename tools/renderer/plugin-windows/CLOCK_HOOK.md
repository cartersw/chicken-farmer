# Native network clock evidence

`clock_trace.inc` extends the existing opt-in `-chicken-capture-log` ledger.
It records messages actually delivered inside the owned replay process and reads
the current network client's clocks at movie submission, pixel readback, and the
next native movie endpoint. It does not use file timestamps, fitted demo packet
offsets, or the unrelated `GetDemoStartTick()` playback epoch.

The first instrumented build compiled successfully with x64 MSVC on 2026-09-07:

`a84cb2fa01cb0e24422a0e02a6560543af3907d76983158b146cbfd0dfb200c4`

Trial `windows-timing-014` completed 160 frames and exited cleanly, with settings
verification and cleanup succeeding. UserCommands envelopes and their no-op
target were observed. Tick/entity clocks correctly remained unavailable: the
first build expected the derived `_t` helper vtables, while native deserialization
produces their `CNetMessagePB` base wrappers. The ledger reported actual tables
`0x533e48` and `0x52cf60`. RTTI independently identifies both wrappers' protobuf
subobjects at `+0x30` with exactly the same serializers; the correction changes
only their required vtable identities. The first build's DLL/PDB/MAP is preserved
in `timing-014-a84cb2fa/`. Trial `windows-timing-015` then completed 160 frames
and exited cleanly. The independent ledger audit matched all 161 image/endpoint
clock observations to the original demo messages, with no unhealthy clocks;
pixel and POV reconciliation also passed. Its DLL/PDB/MAP is preserved under
`timing-015-56ced82d/` before the packet instrumentation below.
The corrected build's DLL SHA256 is
`56ced82d07e8c4357ad68d3fd830d366e79fb052734a9438150fcd938056cae2`.

The previously clean `windows-settings-012` DLL,
PDB, and MAP were preserved under the ignored build directory
`settings-012-0f9c1f0c/`; that DLL's SHA256 is
`0f9c1f0c4c4a6275960d48d2df8a9b1b5684040a490f9cd9ad484989d1c3823c`.
The settings isolation, mandatory `-insecure`, and disabled optional console
command registration are unchanged by this instrumentation.

## Exact binary evidence

The existing capture guard hashes the loaded `engine2.dll` as
`26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac`.
The observation guard separately identifies the installed `client.dll` as
`b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4`.
All locations below are RVAs in those exact local CS2 1.41.7.8 binaries, obtained
with the read-only PE/RTTI helper and MSVC `dumpbin /disasm`.

| Location | Verified behavior |
| --- | --- |
| Engine `NetworkClientService_001`, vtable `0x573890`, slot23 `0x1d2bc0` | Getter directly returns pointer member `+0xa0`, the current network game client. The instrumentation reads that member after checking the service and getter vtables. |
| `CNetworkGameClient` main vtable `0x5335d8`, slot88 `0x6a2d0` | `ProcessTick` reads incoming message `+0x50` into EDI, labels that value as server tick in diagnostic strings, and writes it unchanged into client `+0x37c` at `0x6a76c`. Negative ticks are intentionally ignored during demo playback. Diagnostic string `0x537148` labels `+0x37c` as server tick and `+0x378` as client tick. |
| Deserialized `CNetMessagePB<..., CNETMsg_Tick, ...>` wrapper vtable `0x533e48`; PB subobject `+0x30`, vtable `0x533e78` | The generated serializer `0x174c80` reads PB `+0x20` and emits protobuf tag `0x08` at `0x174cae..0x174cb5`: field1, `tick`. Wrapper `+0x40` has-bit `0x02` records presence. Thus the handler's `+0x50` is proved against the wire field. The derived `_t` helper's separate vtable is `0x533d88`. |
| Game client slot111 `0x6afe0`, slot129 `0x47a00` | Packet entity dispatch and internal/application entries. Both preserve the native two-argument `bool(self, message)` ABI. At `0x47a23..0x47a41`, the effective snapshot tick defaults to client `+0x37c`, with wrapper `+0xc0` overriding it when has-bit15 is set. Dispatch can skip application; their events stay distinct. |
| Deserialized `CNetMessagePB<..., CSVCMsg_PacketEntities, ...>` wrapper vtable `0x52cf60`; PB `+0x30`, vtable `0x52ced0` | Serializer `0x1607c0` reads PB `+0x90` at `0x160b61` and emits tag `0x60`: field12, `server_tick`. This proves the optional wrapper `+0xc0` snapshot clock. The derived `_t` helper has a different vtable, `0x52f3f8`. |
| UserCommands registration `0x7ca4e..0x7cac0` | Message76 binds thunk `0x9b060`, which jumps through game-client slot128. Its target `0x89340` forwards the original message to engine global `+0x688d80`, virtual slot181, then returns true. |
| `CSource2Client` vtable `0x1b1a2f8`, slot181 `0xb344a0` in client.dll | Function body is exactly `C2 00 00` (`ret 0`), with no message read or execution. At runtime the instrumentation checks that the actual engine target equals the existing `Source2Client002` factory object, with this exact vtable, function, and three bytes. |

The wire names are present in the binaries' generated protobuf descriptors and
match the primary extracted
[networkbasetypes.proto](https://github.com/SteamDatabase/Protobufs/blob/master/csgo/networkbasetypes.proto)
and [netmessages.proto](https://github.com/SteamDatabase/Protobufs/blob/master/csgo/netmessages.proto).
The offsets above are established from the installed serializers, not inferred
from the order of fields in a `.proto` file.

### Recorded command envelopes

UserCommands wrapper vtable is `0x539f70`; its protobuf subobject begins at
`+0x30`. Serializer `0x14c200..0x14c256` reads count at wrapper `+0x48` and storage
pointer at `+0x50`, then visits envelope pointers at storage `+8+i*8`.
The read is bounded to 4096 envelopes and checks every pointer and vtable.

`CMsgServerUserCmd` vtable is `0x560f50`. Serializer `0x16d170` proves:

| Recorded field | Envelope offset | Presence bit in `+0x10` | Wire tag |
| --- | --- | --- | --- |
| `command_number` | `0x28` | `0x04` | `0x10`, field2 |
| `player_slot` | `0x34` | `0x20` | `0x18`, field3 |
| `server_tick_executed` | `0x2c` | `0x08` | `0x20`, field4 |
| `client_tick` | `0x30` | `0x10` | `0x28`, field5 |

Missing fields remain JSON null. The logger observes the envelope's recorded
execution tick without reconstructing its command protobuf or executing it.
The checked no-op target establishes why these envelopes are not themselves a
native command-execution callback in this replay client.

## Ledger contract and safeguards

All records share the capture ledger and its engine-thread identity. New events:

- `network_clock_installed`: hooks installed after the header and before queued
  replay commands are dispatched.
- `network_clock_epoch`: active client pointer changed or a delivered nonnegative
  NET_Tick regressed. `client_generation` increases and accumulated tick maxima
  reset. Faults remain sticky across resets.
- `network_message`: paired `entry` and `return` records identified by
  `invocation_index`. Kinds are `net_tick`, `packet_entities_dispatch`,
  `packet_entities_apply`, and `user_commands`. Each records active-client status,
  layout validation, thread identity, generation, before/after network and replay
  clocks, original return result, and handlers in flight. UserCommands additionally
  records its ordered envelope fields and the checked no-op target before/after.
- `network_clock_before_commands`: state sampled at FRAME_START before command
  queues run. Repeated identical replay-cursor/generation samples are omitted.

Every `movie_frame`, `movie_end`, and `pixel_readback` contains `native_clock`.
Movie submissions and pixel readbacks also contain `native_clock_after`.
The `native_clock` schema1 object contains:

- `status`, `phase`, `healthy`, `reason`, `client_generation`, `client_address`;
- `replay_demo_tick`, network `client_tick`, network `server_tick`;
- `last_delivered_net_tick`, `max_delivered_net_tick`;
- `max_observed_entity_tick`, including offered entity data even if later rejected;
- `max_observed_user_command_execution_tick` and
  `max_observed_user_command_execution_ticks_by_slot` (string slot keys);
- `net_tick_returns`, `entity_dispatch_returns`, `entity_apply_returns`,
  `user_command_returns`, `handlers_in_flight`, and `last_completed_invocation`
  (the largest returned invocation index; nested return order is permitted);
- `user_commands_target_is_noop`, `user_commands_observed`,
  `execution_clock_verified:false`, and `causality_bound_verified:false`.

`observed_message_clock` requires a readable active client, a playing demo,
an engine-thread observation, no handler in flight, no recorded fault, and at
least one completed nonnegative NET_Tick in this client generation. Negative
ignored ticks do not make the clock available. Each completed nonnegative tick
must match the network client's actual server-tick member after the native call.
Other-thread dispatch, unexpected message layout, unreadable pointers, or a
changed UserCommands target makes subsequent clock status unavailable. Original
message calls still receive their original arguments and return their original
results. No engine commands or virtual clock calls run off the engine thread.

## What the evidence can and cannot establish

The intended validation compares native message values and ordering against
independently extracted raw NET_Tick, PacketEntities, and UserCommands envelopes
from the same hashed demo. This avoids fitting an execution epoch to demo packet
headers. Actual pixel identity still comes from the existing readback/TGA hash
reconciliation; a network event by itself is not an image association.

A possible separate training policy can select whole commands whose entire
execution support starts strictly after a demonstrated upper bound on actual
input-bearing state delivered before those pixels. Such samples would have a
documented conservative observation-to-action delay. That is different from
certifying an exact fractional observation time or immediate next-frame actions.
The new fields do not automatically enable that policy: runtime evidence must
show hook coverage, stable generations and clocks across submission/readback,
matching raw messages, and the relevant delivery/application phase. Unobserved
message consumers or buffering remain limitations until independently accounted
for. A clock value that merely looks plausible is insufficient.

`EventClientOutput_t.m_flRenderTime`, pawn `m_flSimulationTime`, and the replay
cursor remain separately recorded. None is silently equated to
`server_tick_executed`; `GetDemoStartTick()` remains a playback-loop epoch.
Precise subtick visual phase, movement/button interpretation, and first-person
identity are separate acceptance obligations.

## Complete returned packet transactions

`packet_trace.inc` adds an independent `CDemoPlayer::ReadPacket` transaction
ledger. Its first build compiled successfully with x64 MSVC and completed the
protected `windows-timing-016` capture with 160 frames and clean exit/settings
verification. DLL SHA256:

`ad60f7eb5165aa2ee1a207cae1aea69e5176144f18435275f2eaec3e4aa9de8c`

The guard checks the same full engine hash and the exact main vtable `0x52db68`:
slot22 must point to `0x2b820`, while demo clock getters at slots2/3 must still
point to `0x35a80`/`0x35a90`. The hook forwards the native one-argument
`void* (self)` ABI and returns the exact original pointer.

### Byte and source-tick evidence

- Wrapper `0x2b820` calls `InternalReadPacket` at `0x2b8b0`, preserves its returned
  packet pointer, performs playback bookkeeping, then returns the same pointer.
- Internal reader `0x2bf4f..0x2bf5c` selects a 24-byte decoded entry. Its command
  enum is at `+0`, source demo header tick at `+4`, decoded protobuf pointer at
  `+8`, and packet pointer at `+16`. There is no enum-number subtraction.
- At `0x2bfea..0x2bff4`, the selected entry tick is copied into player `+0x22c`.
  Packet selection at `0x2c56a` transfers entry `+16` into player `+0x1e0`; the
  return path at `0x2cb98` returns that packet. The hook checks returned pointer
  equality with the player's `+0x1e0` member before reading any packet payload.
- At `0x2c846..0x2c85f`, the native reader passes packet `+0x50` as the input
  buffer and `+0x74` as its byte count to the `bf_read` constructor `0x3fc940`.
  That constructor stores the byte count and multiplies it by eight to obtain
  the default bit count. These offsets therefore identify the exact network
  packet bitstream bytes, not the outer `CDemoPacket` protobuf serialization.
- The instrumentation copies the buffer through bounded `ReadProcessMemory`
  calls before native execution resumes and computes SHA256 over exactly that
  byte count. Lengths outside `[0, 16 MiB]`, unreadable bytes, or hash failures
  make the packet evidence unavailable. No packet data is modified.
- The seek path `0x2b9de..0x2baa6` can return a prior/reconstructed packet, and
  filtering at `0x29bd0` can alter packet contents. A non-null return does not
  by itself prove a unique new source packet. Independent original-demo hash,
  source tick, and order matching must establish that association.

### Scheduling gate and its limits

The first switch at `0x2c007..0x2c019` uses tables `0x2cda0`/`0x2cd94`. Only
command enums7 (`DEM_Packet`),9 (`DEM_ConsoleCmd`),12 (`DEM_UserCmd`), and13
(`DEM_FullPacket`) enter the scheduling check at `0x2c01b`. Other enum values
bypass it into `0x2c103`. In particular, class info5, string tables6, signon8,
custom data10, and custom callbacks11 are not universally bounded by this gate.
Full-packet decoding/expansion also needs source-order accounting; its envelope
is not automatically equivalent to an ordinary returned packet.

The gated path reads the network service tick at `0x2c025`, subtracts player
`+0x204` at `0x2c033`, optionally clamps to an end tick, and compares the selected
entry tick at `0x2c064`. During ordinary connected playback a future entry exits
before the processing switch: `0x2c07f` replaces player `+0x22c` with the current
playback tick and `0x2cd14` returns null. Seeking and alternate mode branches can
bypass this wait. Thus a null return's `+0x22c` is explicitly recorded as a raw
scheduling member, never mislabeled as a consumed source packet.

The ledger records both ends of every transaction, including null returns, and
raw before/after seek, filter, alternate-mode, and playback-epoch members.
These checks do not prove a maximum tick for every command executed inside a
transaction. The source scanner must account for all demo records between
matched packet boundaries and reject unsupported paths. For the initial Dust2
capture range, the independent original-file inventory finds only ordinary
`DEM_Packet` records after tick5000 through6400, which permits a narrower
validation without assuming the ungated commands cannot affect images.

### Packet ledger schema1

`demo_packet_hook_installed` precedes queued replay commands. Each
`demo_packet_read` has paired `entry`/`return` phases with a one-based
`read_invocation`, engine-thread identity, demo-player address, replay cursor,
`player_state` and `player_state_after`, and `packet_trace` snapshots.
Return rows also have `returned_packet`, its address, and `payload_readable`.
A non-null return has a `packet` object containing:

- zero-based `returned_packet_index`, `read_invocation`, `source_demo_tick`;
- `byte_count`, `payload_sha256`, and `payload_readable`.

The hash is intended to match the original demo's decompressed
`CDemoPacket.data` byte string (or a proved nested full-packet source), not the
outer protobuf or compressed demo record. Packet bytes are not duplicated into
the ledger. Repeated native returns retain distinct indices even when hashes
are equal. Unreadable fields remain null and the error is sticky.

Every `native_clock` and `network_message` entry includes `packet_trace`;
message returns add `packet_trace_after`. Its fields are:

- `status`, `healthy`, `reason`, `read_invocations`,
  `last_completed_read_invocation`, `reads_in_flight`, `active_read_invocation`;
- `returned_packets`, `null_returns`, `latest_returned_packet`;
- `process_lifetime_max_returned_source_demo_tick`, which is never cleared by
  client changes, network clock regression, or a seek;
- `source_packet_association_verified:false`, `causality_bound_verified:false`.

`observed_returned_packet_bytes` requires at least one returned packet, no
transaction in flight, and no recorded fault. Other-thread calls, overlapping
reads, unexpected object layout, or unreadable payloads invalidate evidence
while preserving original behavior. No instrumentation mutex is held across
the original call. A message's latest packet reference is a candidate source
association until independent wire contents and ordered dispatch agree; a
message inside a read instead retains the active read transaction ID. These
fields extend source coverage evidence without claiming a complete image
information bound or promoting training readiness by themselves.

### Independent validation of trial016

`src/cs2_data/packet_bounds.py` reconstructs all 7,045 paired native reads and
their counters. It follows each observation's explicit
`last_completed_read_invocation`, rather than assuming a ledger row was written
at the time of its observation. Both movie and pixel callbacks' before/after
snapshots contribute to the ceiling; their callbacks need not have a total order.

All 2,879 non-null returns match the original demo: 2,690 match the source packet
bytes exactly, and 189 match the independently reproduced native seek filter.
That filter preserves selected original message bit spans, then zero-pads the
final byte; it does not synthesize payloads or reserialize protobuf messages.
The scanner identifies it as `cs2-14178-seek-message-filter-v1` with the exact
engine/client hashes. The auditor accepts this variant only when both native
filter flags are1. No unmatched earlier seek packet is discarded from history.
All 160 image bounds and the following endpoint pass the narrow source-prefix
audit. The first image's ceiling is source demo tick6000/server tick16703; the
endpoint's is6320/17023. These are conservative information ceilings, not a
measurement of the exact fractional visual phase.

The source prefix also contains negative-tick `DEM_Recovery` records18.
Their native handling was checked separately: queue refill `0x2d100` indexes
switch table `0x2d6f4` using `enum+1` at `0x2d20d..0x2d222`. Recovery18 maps
to `0x2d254`, where the file reader obtains the payload length and then calls
reader slot5 at `0x2d2d0..0x2d2dc` with a null destination and that byte count.
Execution loops back to `0x2d130`; there is no Recovery protobuf construction,
queue entry, or client dispatch on this path. Thus these bytes are skipped,
rather than being an unobserved delivery of future command information.
The embedded Recovery descriptor at `0x55a004` names initial spawn-group and
spawn-group-message fields, but the descriptor alone is not the skip proof.

The source-prefix auditor retains earlier checkpoint/bootstrap packets and
process-lifetime maxima. It computes server-clock ceilings from original
NET_Tick, explicit PacketEntities ticks, and recorded UserCommands envelopes
through the conservative prefix, together with observed native maxima. It
requires an ordinary Packet7 boundary with no active seek or alternate gate.
Its `verified` result is scoped to that evidence contract. Acceptance still
requires independently checked pixels, observed POV, native message clocks,
and the demonstrated command-support profile; it never grants training
readiness by itself.
