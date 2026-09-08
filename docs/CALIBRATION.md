# Controlled Windows calibration

The subsequent turning/button investigation is complete; see
[its results and remaining limits](progress/TURN_AND_BUTTON_CALIBRATION.md).
The [synthetic input worker](SYNTHETIC_INPUT.md) now sends Windows keyboard and
relative-mouse events through the same protected lifecycle, with sent-event
receipts and measured local mouse response. This document describes the earlier
engine-console probes and replay comparison; its historical results remain
separate from the synthetic input experiments.

The next layer is the [32 Hz executor and scoped label builder](CONTROL_EXECUTION.md).
It uses the measured synthetic response, fractional mouse-count carry and
ready-time mouse-setting checks; the label masks retain unresolved event timing.

The protected Python worker, native calibration mode and independent diagnostic analyzer are working. `control-008` completed the first fully captured ten-second control probe on the reviewed CS2 1.41.8.0 build. Precise input-consumption timing and original-versus-replay timing still require additional evidence; this controlled dataset does not grant training acceptance.

## Completed control-008 probe

| Evidence | Observed result |
| --- | --- |
| Scheduled controls | All 14 planned dispatches recorded, in order |
| Original-client image archive | 321 frames; every decoded RGB image matches its native readback hash |
| Simulation span of image observations | Local tick base 1401 through 2041, a ten-second span including both endpoints |
| Recovered demo commands | 711 commands from 711 full payloads; no delta payloads, parser warnings, projection errors or duplicate command-number identity keys |
| Protected settings | All 39 selected original files verified after restoration; staged renderer removed from CS2 |
| Calibration acceptance | `training_ready: false`; exact input-consumption timing remains unverified |

See the [capture manifest](../data/calibration/control-008/calibration.json), [native dispatch diagnostics](../data/calibration/control-008-analysis/report.json), [full pixel comparison](../data/calibration/control-008-analysis/pixels.json), and [recovered-command diagnostics](../data/calibration/control-008-command-analysis/calibration_commands.json). The recorded demo SHA-256 is `2543ea417181d95a09bec91910b46c4d3cc8af3eb56d7939bc9a23dad4932f8b`. The pixel result verifies archived original-client images against their capture readbacks; it does not establish that a replay image shows the same causal moment.

The schedule's measured simulation lateness was 0–25 ms. Console-dispatch calls took approximately 0.127–0.148 ms of wall time. These measure different quantities: neither is the delay from a physical input to the game consuming it. `startmovie` can make wall time progress differently from the captured simulation clock.

The probe also exposed a reason to preserve raw button fields: the second raw plane contains the forward bit both at the apparent start and at the apparent end of the controlled forward hold. In commands 778 and 810, respectively, that plane contains `0x8`, while the first raw plane differs. This is evidence against treating the second plane as an independently proven press-only field. It is not yet a complete semantic decoder for every button, same-tick transition or subtick record. The command report deliberately keeps optional-field absence and raw transitions visible.

The worker is `tools/renderer/calibration_windows.py`. Dry-run requires no game or output changes:

```powershell
.venv/Scripts/python.exe tools/renderer/calibration_windows.py --output data/calibration/control-next
```

The default plan contains ten seconds of forward movement, a counter-strafe, a short attack press, crouching, turning and lateral movement. Custom plans may contain 1–128 whitelisted actions within 2–30 seconds, at 32 FPS and 1280×720 on Dust2. Presses require releases, action identifiers are unique, equal-time events retain their declared order, and the final 500 milliseconds remain idle. Plans cannot supply arbitrary server addresses, commands or launch arguments.

`--execute` is the explicit execution switch. It requires the native calibration marker and reviewed module fingerprints; `--allow-version-mismatch` cannot bypass the binary checks. The worker reuses the protected replay worker's settings snapshot, cloned configuration, native settings-isolation proof, gameinfo lease, owned process handle and verified restoration. It requires CS2 to be closed. Steam's online/offline mode, Cloud preference and normal launch options are not changed. Interrupted-run recovery uses the existing `tools/renderer/windows.py --repair <gameinfo-recovery.json>` workflow documented in the Windows rendering guide.

## Native interface

The worker passes `-chicken-calibration-plan <native-plan.json>` and `-chicken-calibration-ledger <calibration_ledger.jsonl>`, alongside existing protected renderer arguments. The JSON contains `schema_version: 1`, `producer: "cs2-controlled-calibration-plan-v1"`, `map`, `fps`, `width`, `height`, `duration_seconds`, and `actions` with `id`, `at_ms`, and `command`. The worker adds a unique `movie_name` and absolute `demo_path`.

The native mode owns deterministic local setup, spawn/readiness checks, recording, action dispatch, release, stop and quit. It verifies an alive local first-person pawn and the actual local loopback connection before producing `calibration_ready`. It checks that each command exists at runtime, records `action_dispatch` events, and emits `calibration_complete` after stopping recording. The worker requires exactly one ordered header/readiness/stop/completion sequence and an exact match to every planned action. Missing records, unexpected actions, reversed clocks, changed plans and incomplete restoration keep the run failed.

New recordings additionally require `CHICKEN_CALIBRATION_SETTLE_V1`. After the
six-second setup wait, the worker requires at least two continuous seconds,
32 callbacks and 64 advancing controller ticks. A gap over 250 ms or player/clock
discontinuity resets the window. Native capture startup and the Python verifier
require the same pawn and a nondecreasing tick through readiness. Raw
`startup_settle_sample` records independently support the summary; a single
delayed callback cannot satisfy this check. Older captures remain readable
without being relabeled as having passed the newer settling policy.

`startup_cadence_sample` records a maximum callback gap over each reporting
interval before capture. These observations exposed roughly five-second map
loading gaps and one-second configuration gaps in 009-011. Movie/readback gaps
stayed below 36 ms in those recordings. Wall time remains separate from
simulation progression, and these gaps do not certify display or input latency.

Repeatable plans and frozen hypotheses are under
[`tools/renderer/plans`](../tools/renderer/plans/README.md). The button
confirmation intentionally probes cases that can disprove the initial rules;
a failed diagnostic hypothesis does not mean settings restoration or command
extraction failed.

An alive local pawn can have a successfully read **null observer-services pointer**. Its `observer_mode` remains null; the worker does not invent mode zero. Readiness distinguishes this case through `observer_services_pointer_observed: true` and `observer_services_present: false`. An unreadable pointer is rejected. If the observer service exists, an explicitly observed mode zero is required. The initial stationary camera must have no view override (`UINT32_MAX`), be within two horizontal units of the own pawn, and be 24–76 units above its origin. The worker independently recomputes this camera check at readiness; movement and interpolation can change camera/pawn separation later, so this stationary bound is not applied to every moving frame.

The `demo_path` field names the final workspace archive. The game records a unique basename matching `movie_name`, because this installed `tv_record` rejects Windows drive-letter paths. After CS2 exits and the renderer mod has been relocated, the worker finds only that exact basename in its designated mod/game write roots and moves it to `controlled.dem`. Duplicate recordings are preserved and rejected. Existing personal demos are never selected by this archive operation.

Action `at_ms` values refer to `local_controller_tick_base_64hz`: elapsed simulation milliseconds are `(controller_tick_base - start_tick_base) * 15.625`. QPC timestamps bracket each console dispatch separately. `startmovie` may decouple simulation progression from wall time. The initial control source is native engine console dispatch, **not physical keyboard/mouse events**, so the result cannot establish physical device latency or exact client input-sampling time.

The frame archive is `frames/<movie_name>_00000000.tga` onward, with `capture_frame_files.json` recording file hashes and native source names. The viewing MP4 is `<movie_name>.mp4`. The original native capture ledger and separate calibration ledger retain their own clocks. Their presence alone is not pixel-to-input synchronization proof.

## Diagnostic analysis and replay

Analyze a completed or failed capture into a fresh report directory:

```powershell
.venv/Scripts/python.exe -m cs2_data.calibration_analysis --run-dir data/calibration/control-008 --output data/calibration/control-008-analysis-review
```

The analyzer separates simulation schedule lateness from the wall duration of a console-dispatch call. Missing or contradictory evidence is reported as incomplete. These diagnostics do not measure physical-device delay and do not grant training acceptance.

Once the controlled demo has been parsed, `tools/renderer/calibration_replay_windows.py` replays an explicitly selected window. It requires the source run's verified completion, plan/demo hashes, original player identity and settings-restoration evidence. Supply the actual demo tick interval and parser-resolved IDs; simulation milliseconds in the control plan are not demo ticks. For example, with those values assigned to the PowerShell variables:

```powershell
.venv/Scripts/python.exe tools/renderer/calibration_replay_windows.py --calibration-run data/calibration/control-008 --output data/calibration/control-008-replay-next --start-demo-tick $StartDemoTick --end-demo-tick $EndDemoTick --steam-id $SteamId --player-slot $PlayerSlot --spectator-user-id $SpectatorUserId --allow-version-mismatch
```

This command is a dry-run until `--execute` is supplied. Windows replay's normal protected process/settings/recovery workflow performs execution. The special `-chicken-calibration-replay` flag selects only the reviewed calibration binary profile; ordinary replay jobs retain their original profile. Each requested window is at most 320 demo ticks. The job explicitly identifies itself as controlled calibration diagnostics, and its `round_id: 1` is a required renderer placeholder rather than a claim of competitive round eligibility. `calibration_replay.json` preserves source provenance and keeps `comparison_verified: false` until a separate comparison establishes the result. Source files must remain unchanged throughout validation, and the native settings-isolation proof must match a valid owned process identity and the worker's saved restoration journal.

For the parsed `control-008` recording, the local player's Steam ID is `76561198845209628`, player slot 0 and spectator user ID 0. Its existing first replay job selects demo ticks `[256,576)`. These values belong to this recording; they must not be copied to an unrelated match.

Independently check original and replay image/readback associations and compare
their recorded state clocks, publishing fresh JSON and Markdown reports:

```powershell
.venv/Scripts/python.exe -m cs2_data.calibration_replay_analysis --source-run data/calibration/control-008 --replay-run data/calibration/control-008-replay --command-report data/calibration/control-008-command-analysis/calibration_commands.json --output data/calibration/control-008-replay-review.json
```

The completed comparison is at
`data/calibration/control-008-replay-analysis/report-v2.json` and `report-v2.md`.
All 160 replay images match their own native RGB readbacks, with stable in-eye
identity. All 160 have unique original frames at the same numeric controller
tick, and paired ammo/crouch/ground/last-shot state agrees. Pawn position differs
by up to 0.874 game units and eye angles by up to 9.375 degrees during turning;
the rendered camera has a separate measured yaw difference. Equal numeric ticks
are not certified equivalent input/render phases. Source and replay images are
not asserted to be pixel-identical, and their HUDs visibly differ.

The comparator preserves identity changes and duplicate/ambiguous tick keys,
and rechecks source and image hashes before publication. A linked command report
is explicitly identified as reported evidence, rather than being re-extracted
by this stage. Original worker manifests remain unchanged; the separate reports
describe the added diagnostic measurements without promoting training readiness.

## Local binary evidence

The following inspection used read-only MSVC `dumpbin /DISASM /RANGE` and `tools/renderer/plugin-windows/inspect_pe.py`, with the installed files as primary evidence. These are source interpretation checks; a live run must additionally satisfy the runtime guards.

| Module | Inspected SHA-256 |
| --- | --- |
| `game/bin/win64/engine2.dll` | `1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07` |
| `game/bin/win64/networksystem.dll` | `fc33c097eca4ab0049590985a772b5b2f556523933d0482f86217889e1283127` |
| `game/csgo/bin/win64/client.dll` | `809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae` |

- `CNetworkServerService` RTTI identifies vtable RVA `0x576058`. Slot 23, `0x1DEA60`, reads the local game-server pointer at service `+0x150`; its bytes are `48 8b 81 50 01 00 00 c3`. Slots 24 and 29 test whether this server exists and its state at `+0x38` is at least 3. The pinned SDK `public/iserver.h` agrees for these methods, but some later declarations are stale; the native guard therefore does not call an assumed later `GetGameServer` slot.
- `CEngineClient` vtable `0x538290`, slot 41 at `0x75C40`, reads client global `0x90D4B0`, returning the per-split-screen pointer at client `+0xF0 + 24 * slot`. Its complete bytes are `4c 8b 05 69 78 89 00 4d 85 c0 74 10 48 63 ca 48 8d 04 49 49 8b 84 c0 f0 00 00 00 c3 33 c0 c3`. A runtime object must match the `CNetChan` vtable before this pointer is treated as a channel.
- `CNetChan` in `networksystem.dll` has vtable `0x22AE08`. `INetChannelInfo::IsLoopback`, slot 7 at `0xB6600`, returns whether the address type at channel `+0x72A8` equals 4. Its bytes are `83 b9 a8 72 00 00 04 0f 94 c0 c3`. This agrees with the pinned SDK `public/inetchannelinfo.h`. The launch requests `map de_dust2 loopback=true`, while runtime verification separately checks the actual channel, active local server, expected map and `sv_lan`.
- The `tv_record` callback at engine RVA `0x130B90` rejects empty names and names containing two consecutive backslashes, a colon or `..`, through checks at `0x130D4E`–`0x130DA8`. Therefore an absolute `C:/...` argument is invalid. It applies the `.dem` extension and queues the basename recording. The native mode uses a fresh ASCII basename instead of changing the global demo directory.
- Installed engine strings include `tv_record_immediate` and the explanation that recording starts at command execution rather than `tv_delay` earlier. `CHLTVDemoRecorder::WriteFullFrame` references serialization of `CSVCMsg_UserCommands`. The resulting `control-008` demo was subsequently parsed and supplied all 711 eligible full command payloads, as reported above. A different recording still needs its own coverage check.
- The reviewed client still contains the documented observation getters: local controller array `0x23A0F30`, entity system `0x23AD958`, entity pages at `+0x10` with nine-bit indexing and `0x70`-byte identities, and `CViewRender` object `0x23D9C90`/vtable `0x1B331C0`. Its matrix method `0xBC1BA0` still passes origin `+0x4B0`, angles `+0x4C8`, FOV `+0x4A8` to `0x829490` and writes frame counter `0x23CB500`. This inspection does not independently validate all renderer hooks against a changed game build.

## Evidence still required

1. Establish how scheduled dispatches relate to actual command consumption separately for each action type. Recovered fields and nearby state changes alone do not prove exact subtick timing.
2. Resolve the original-client/replay phase differences measured by the completed diagnostic comparison, particularly turning. The first comparison verifies each image against its own readback and records state differences; it does not certify exact phase equivalence.
3. Repeat the probes with different tick phases, brief taps, overlapping controls, aim changes and weapons before declaring button semantics or a calibrated control horizon.
4. Quantify timing intervals and errors, then decide which narrower target contracts the evidence supports. Physical keyboard/mouse latency remains outside this engine-console-dispatch experiment.

The Python worker keeps `training_ready: false` and `timing_status: unverified`; it publishes recorded evidence only for subsequent independent analysis.

The worker's 81 focused tests cover command/path injection, bounded schedules, malformed and incomplete ledgers, QPC/order contradictions, alive/readiness checks, observed-null versus unreadable pointers, independent stationary-camera checks, file provenance, binary updates, prelaunch failures, and restoration after crashes, timeouts and interruption. A simulated successful lifecycle verifies that the unique demo and raw frames are archived after settings restoration. Twenty-one replay-wrapper tests additionally cover changed source evidence, source changes during verification, native-proof process identity, saved restoration-journal consistency, incomplete protection, identity mismatch, interval bounds, dry-run behavior, unchanged ordinary launches and delegation to the existing protected renderer.
