# Native POV and camera observations

This extends the existing capture ledger; it does not assert that every sampled
entity field describes the pixels' simulation phase. Every observation names its
actual sampling boundary. The plugin never uses the requested `spec_player`,
player name, or inferred packet offset to populate the observed Steam identity.

The first build is `e2094626aac1cd6e8cdf055ef662b2f189f5969fbd7d889c2701251b9549b4c3`.
The next build, adding the runtime-verified controller step and command/weapon
fields, is `ed246f2a8096eb856b1b7b741244ae8eced2bcfe667965ee8987e8abfbb7abfd`.
The previous tested build is preserved in ignored build output as
`server-known-working-4b52bd78.dll`. Trial `windows-validation-007` completed with
160 frames using the first build. It resolved 453 runtime schema fields and
recorded matching view-source/matrix-input camera vectors. It identified the
local pointer as `CCSPlayerController`; the first build correctly reported
unavailable POV rather than interpreting controller memory as a pawn. This
evidence motivated the explicit controller-to-pawn step in the next build.

Trial `windows-validation-008` then completed with 160 movie frames, 160 pixel
readbacks and one endpoint. All 160 frames resolved Steam ID
`76561198323592528`, mode 2, and reciprocal target pawn/controller handles.
Complete POV and camera dictionaries, including the matrix-built frame counter,
matched between movie submission and both sides of pixel readback for all 160
frames. The last-executed command number/tick and simulation tick were -1
throughout; movement's processed command number was 0. These remain unavailable
execution evidence, not clock anchors.

## Exact binary evidence

These SHA256 checks are mandatory whenever capture evidence is enabled:

| Module | SHA256 |
| --- | --- |
| client.dll | `b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4` |
| schemasystem.dll | `e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512` |
| engine2.dll | `26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac` |

The primary source used is HLAE, commit
[`03f972b4710ab9a8b1601fc0c34ce33c9c0268cc`](https://github.com/advancedfx/advancedfx/tree/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2):
`SchemaSystem.cpp/.h`, `ClientEntitySystem.cpp/.h`, and `main.cpp`.
Disassembly below is from the installed, fingerprinted binaries, inspected with
MSVC `dumpbin /disasm /range:...`; numbers are RVAs.

* HLAE's schema-registration pattern matches schemasystem.dll `0xd9e7`.
  Its RIP-relative target is the schema-system object at `0x75730`.
  The bounded reader uses HLAE's scope array/count and declared-class/field
  layouts. Offsets are resolved from named runtime schema fields, not copied
  from a dump for a different game release. The complete resolved field map is
  included in the ledger header (or a `native_schema` event if initialized later).
* The first HLAE split-screen-player pattern matches client.dll `0x93aae0`.
  For slot zero, the function directly reads pointer array `0x23a0f30`.
  There is another match at `0xc96c00`, using a different array. This is why the
  reader records the runtime class; trial 007 identified the selected object as
  `CCSPlayerController`. The reader follows its schema `m_hPawn` handle to the
  local pawn. It does not treat pattern similarity as sufficient identity proof.
* The native entity-system pointer is `0x23ad958`, from the unique entity-count
  function pattern at `0xad6c20`. Native `GetEntityFromIndex`, `0x9be990`, reads
  page pointers at system+`0x10`, uses nine low index bits per page, and uses
  `0x70` byte identity entries. The native function validates the low fifteen
  handle bits; this reader also checks the complete serial-bearing handle
  against the identity entry and the entity's schema-resolved identity pointer.
* The observer chain follows schema fields:
  local `CBasePlayerController.m_hPawn` →
  `C_BasePlayerPawn.m_pObserverServices` →
  `CPlayer_ObserverServices.m_hObserverTarget` → target pawn →
  `C_BasePlayerPawn.m_hController` → controller →
  `CBasePlayerController.m_steamID`.
  Runtime MSVC RTTI must identify the target as a player pawn and controller as
  `CCSPlayerController`. No player-slot mapping is assumed.
* The installed client's embedded `ObserverMode_t` schema enum table independently
  proves `OBS_MODE_IN_EYE = 2`: member entry `0x1af2220` points to name string
  `0x1af22c0`, followed by uint64 value 2. Adjacent 32-byte entries give NONE=0,
  FIXED=1, CHASE=3 and ROAMING=4. The enum registration at `0x20a86b8` references
  this table. A selected target handle alone does not prove first-person view.
  An unset camera view-entity handle is `UINT32_MAX`, the SDK invalid handle;
  this was the normal value in all frames of trial 008.
* `CViewRender` RTTI identifies vtable `0x1b331c0`. Constructor `0xbb0830`
  installs it; initializer `0xeaaf0` passes static object `0x23d9c90` to that
  constructor. Every camera read verifies that object's vtable.
* Its matrix-building method, vtable slot 4 at `0xbc1ba0`, passes camera origin
  at this+`0x4b0`, angles at this+`0x4c8`, and FOV at this+`0x4a8` to matrix setup
  function `0x829490`. That function copies exactly three floats from each
  vector to this+`0x10` / this+`0x1c`. Both source and matrix-input vectors are
  recorded separately. The method stores the client frame counter in global
  `0x23cb500`; that counter is also observed, not inferred from the movie index.
* The already verified `IDemoFile` vtable slot 12 is `0x35a50` in engine2.dll,
  matching HLAE's `IsDemoPaused`. Its address is checked before calling. No new
  virtual calls or hooks are used for entity, schema, or camera observation.

## Ledger contract

`movie_frame` and `movie_end` each contain `native_observation`.
`pixel_readback` contains `native_observation` before the native readback and
`native_observation_after` after it completes. The existing pixel-hash matching
must still establish the TGA's association with a movie index.

An observation contains:

* `schema_version: 1`, `source_phase`, `clock_on_engine_thread`, and an actual
  `replay_demo_tick` read at that boundary.
* `observed_pov.status`: `observed` or `unavailable`; partial class/handle
  evidence and a reason survive unsuccessful resolution.
* On success, `observed_pov.steam_id` is a decimal string; full pawn/controller
  handles, controller entity index, local pawn handle/class, observer mode and
  target handle, camera view-entity handle, and reciprocal controller pawn
  handle remain available for independent checks.
* `observed_pov.pawn_state` contains schema-sourced origin, eye angles, velocity,
  flags, health, simulation time/tick, ground handle, and duck fields where present.
  Missing fields are null. These are state reads at the observation boundary;
  pawn eye angles are never labeled as camera angles.
  `on_ground` uses the pinned SDK `FL_ONGROUND = (1u << 0)`.
  `last_executed_command_number`, `last_executed_command_tick`, and
  `movement_last_command_number_processed` are reads of the named schema fields,
  not converted cursor values. The parent POV object additionally records
  `controller_tick_base`. Weapon services resolve the full active weapon handle
  before `ammo_clip` and `last_shot_time` are read from the weapon schema fields.
* `rendered_camera` contains the current native view's `origin`,
  `angles: [pitch, yaw, roll]`, `fov`, `matrix_input_origin`,
  `matrix_input_angles`, and `matrix_built_framecount`. Its
  `pixel_camera_equivalence_verified` remains false until independent evidence
  establishes the causal association.
* `demo_paused` is the guarded native getter result when available.

All reflection/entity reads use bounded `ReadProcessMemory`, validate counts and
string termination, and return missing evidence for invalid reads. They do not
dereference an arbitrary pointer through a speculative virtual interface. Reads
and the pause getter only occur on the existing engine thread; worker-thread
readbacks record unavailability instead of calling engine code.

## Timing limits

Native Steam identity, camera state, and simulation time strengthen validation;
they do not by themselves establish the epoch and phase of executed UserCmds.
`GetDemoStartTick` is a local playback epoch and must not be equated with the
parsed execution offset. Schema `m_flSimulationTime` is a pawn state field, not
an executed-command callback. No new training-ready or timing-ready assertion
is emitted by this instrumentation.
