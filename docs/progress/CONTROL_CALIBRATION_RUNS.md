# Controlled calibration implementation and run evidence

Work resumed after the September 7 checkpoint. The 32 Hz control schema,
diagnostic candidate audit CLI, protected local calibration worker, native
calibration mode, diagnostic analyzer and protected replay wrapper are now
implemented. Calibration remains separate from competitive training acceptance.

The installed game changed to patch 1.41.8.0. A separately inspected calibration
profile handles its changed engine offsets and pins engine, client, server,
schema, filesystem, network, DX11 and tier0 binaries. Ordinary historical replay
retains its existing profile. All eight inspected modules (91,274,944 bytes)
are archived with independently checked hashes at
`data/native-profiles/calibration-14180-v1/binary_archive.json`.
This preserves inspection inputs; it is not a complete runnable game backup.
Inspected code ranges remain in their audit directories. See the
[compatibility audit](../../tools/renderer/plugin-windows/NATIVE_COMPATIBILITY_14180.md).

The historical 129-sample acceptance artifact remains intact. Its loader cannot
currently reproduce the original server proof because the inspected 1.41.7.8
Valve server binary is no longer available. The new controller audit refuses to
publish real candidate counts from that failed revalidation. This does not mean
its saved samples were silently relabeled or that the new game binary is treated
as proof of the old one.

| Run | Result | Finding or evidence |
| --- | --- | --- |
| `control-001` | Failed before actions | Map getter was still empty during startup. Binary and native settings guards passed. |
| `control-002` | Timed out before actions | Startup `+map` did not take effect; added a one-time fixed local map request on the engine thread. |
| `control-003` | Failed during setup | `jointeam` uses the server client-command handler, rather than the ordinary client command registry. |
| `control-004` | Timed out before actions | A living player has no observer-service object; added explicit readable-null evidence instead of inferring observer mode zero. |
| `control-005` | Failed before actions | Movement buttons use the separate InputService registry; added guarded registry/type/active-input checks. |
| `control-006` | Complete recording; command reconstruction failed | 321 images, 14 actions, clean exit. All images matched native RGB hashes. The demo contained 706 delta payloads with no initial command baseline. |
| `control-007` | Failed before recording | The existing complete-command option is development-only and unavailable through normal console name lookup. Added validated access to its original engine reference. |
| `control-008` | Complete recording and command recovery | 321 images, 14 actions, 711 full commands reconstructed; zero delta payloads, parser warnings or protobuf projection errors. |
| `control-008-replay` | Complete five-second replay | 160 images from demo ticks 256 through 576, including the shot, crouch and turn. Clean process exit. |

All nine runs restored the 39 selected personal settings files with zero restore
operations and moved their staged plugins out of CS2. No Steam mode or Cloud
preference changes were made. Failed recordings retain their failure status
even though their settings restoration passed.

`control-006-analysis/report.json` reports all 14 planned dispatches, seven
press/release pairs, and simulation-clock dispatch lateness of 9.375 to 28.125 ms.
`pixels.json` independently matches every original TGA to a distinct native RGB
readback. Its 642 FRAME_START observations represent 322 unique local tick-base
values; the 321 movie frames advance by two ticks each. These are dispatch and
image observations, not exact client input-consumption or physical-device times.

Local setup now disables `sv_cq_delta_encode_svc_usercmds` and verifies its value
before `tv_record`. The inspected exporter selects complete payloads, and actual
extraction of `control-008` independently confirms the result. Old missing
baselines are never synthesized. The generic competitive validator still rejects
the controlled one-human match and its initial unresolved identity; this is
diagnostic calibration data, not a new competitive training partition.

## Measured control-008 evidence

- `data/calibration/control-008-analysis/report.json`: 14/14 dispatched actions,
  seven press/release pairs, simulation dispatch lateness 0 to 25 ms. QPC call
  duration was 0.1274 to 0.1476 ms; this is time spent in dispatch, not input
  latency. All 642 FRAME_START observations cover 321 unique tick bases, each
  observed twice. The 321 movie frames advance exactly two ticks each.
- `data/calibration/control-008-analysis/pixels.json`: all 321 original TGAs
  independently decode to the native readback RGB hashes; all frames are
  distinct and their local-player snapshots are stable through readback.
- `data/calibration/control-008-command-analysis/calibration_commands.json`:
  all 711 full commands reconstruct. All 14 dispatch boundaries have one
  matching recorded last-processed command number for the same player.
  The next command shows the corresponding raw change. In this run,
  `server_tick_executed - demo_tick = 1331` for all 711 rows. This is an observed
  numeric clock relationship, not proof of an input-consumption instant.
- Plane 2 occurs on both button press and release; plane 3 is absent throughout.
  The 12 non-turn subtick records have `when = 0.0`. Turn controls change a raw
  button bit and yaw without subtick records. Input-history fractions are a
  distinct field and are not converted into physical timestamps.

The source demo SHA256 is
`2543ea417181d95a09bec91910b46c4d3cc8af3eb56d7939bc9a23dad4932f8b`.
Both successful captures use native plugin SHA256
`d2fcd836aeb758eefb60d3eaebd1ace211b2677493fee9eca8f4b469d4aa07c5`.
Raw ledgers, commands, original images, MP4s, settings proofs and the run-owned
plugin archives remain available under the respective run directories.

Visual spot review of source frame 113 and replay frame 20 shows the expected
Dust2 first-person Glock scene. The original player HUD and replay HUD differ:
the replay retains the observer name/weapon strip and different HUD colors.
This spot review does not establish pixel equality between the two captures or
full HUD acceptance. Native readback matching verifies each saved image against
its own render, rather than claiming that original and replay images are equal.

Exact within-tick consumption timing, physical keyboard/mouse latency and
general button semantics remain unverified. All new controller and calibration
reports retain `training_ready=false` and `live_control_ready=false`.

## Original/replay comparison

`data/calibration/control-008-replay-analysis/report-v2.json` independently checks
both captures. All 160 replay TGAs match their own native RGB readbacks and are
distinct. Every replay frame passes the recorded in-eye Steam/reciprocal-target
identity checks and preserves the same observed POV through readback.

All 160 replay frames have a unique original frame with the same Steam identity
and numeric controller tick. At those pairs, ammo, ducked state, duck amount,
ground state and last-shot time all agree. The sampled shot change occurs
between controller ticks 1657 and 1659 in both captures; crouch state changes
occur between 1733 and 1735, and between 1753 and 1755, in both.

The numeric pairing does not make their observation phases equivalent.
Recorded pawn positions differ by up to 0.874 game units, and recorded eye
angles differ by up to 9.375 degrees during the turn. Render-time values differ
by approximately 11.106 ms. That render-time difference is descriptive; it has
not been established as a universal correction or input latency. The report
retains the original camera/state fields and separate clocks for further study.
The rendered camera itself differs by about 5.977 degrees during steady turning,
with a 6.402-degree initial difference and a -0.425-degree final difference.
These distinct eye-angle and camera measurements do not support simply shifting
all images by one 32 Hz frame. The adjacent `report-v2.md` summarizes the results;
the preliminary `report.json` remains unchanged.

The controlled recording/replay workflow is now implemented and exercised.
The next calibration work is repeatable trials at varied phases and per-action
consumption/presentation analysis, particularly turning, before promoting
semantic labels. Competitive packet/command proof for the current game build
also remains separate work.

## Final verification

- Full Python suite: **895 passed in 41.06 seconds**.
- The native Release plugin built successfully and completed both live 008
  sessions with exit code zero.
- The final comparison JSON SHA256 is
  `310611a4e011580710491e0aa73441ed515b88ca38ccda1e2ae9aa2a4566cd50`.
- All nine settings journals retain 39 verified original/post files and zero
  restore operations; every run restored gameinfo and removed its staged plugin.
  No CS2 process remained at the final check.
- `git diff --check` passed. No model was trained or new competitive samples
  accepted during this milestone.

Implementation details and commands: [controller](../CONTROL_CONTRACT.md),
[calibration](../CALIBRATION.md), [server support](../SERVER_COMMAND_SUPPORT.md).
