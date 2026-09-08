# Completion history

Record delivered work here in date order, newest first. Keep historical results intact; [STATUS.md](STATUS.md) describes the current state and [NEXT_STEPS.md](NEXT_STEPS.md) tracks unfinished work.

## 2026-09-08 - Named player selection and resilient batch display

Continued the desktop launcher's recorded stopping point with **Load players**
and **Player POV** controls. Rosters come from prepared source names and Steam
IDs; plans pass the exact chosen ID through the collection/discovery CLIs.
Player filtering precedes bounded ordinary/action sampling, with explicit scope
provenance and no fallback when the player lacks eligible clips. Changed demo
selections invalidate the roster, including results still loading in the worker.

Hardened batch display before Tk mutations, retained selected rows on refresh,
and bound review links to the current completed capture-journal attempt and
page checksum. Invalid plans, malformed summaries, changed review pages and
unjournaled attempt folders have regression coverage.

**136 backend/collection/batch tests and 20 real Tk tests pass.** Real GUI input
loaded Dust2's recorded roster and selected Ckanic. The actual planner/clock
tools produced four ten-second clips across rounds 7, 16, 18 and 23 (two ordinary,
two reload hints) in about 85 seconds. The source tables were reused and exact
player identity was checked in every job. Both the player-selection screen and
completed batch were inspected. Evidence: [desktop-app-002](../../data/validation/desktop-app-002/).
No CS2 capture, additional accepted training data or model training ran. The
full-player queue and a new capture initiated through the GUI remain unfinished.

## 2026-09-08 - Desktop demo launcher

Added `Launch Demo Processor.cmd`, a Tk desktop interface and separate background
orchestration. Users can choose demo/output folders, prepare up to eight sources,
plan bounded sample captures, inspect existing batches and run/resume one clip.
The UI retains full activity logs, remembers options/last run, reports source
quality issues and pending review, and prevents a second launcher for the project.
Stop/close waits for the current producer or protected batch invocation.

Verified 19 launcher backend tests, six real Windows Tk UI tests and 39 existing
collection/batch tests. Actual Dust2 preparation reused hashed source tables and
produced one ten-second plan using the real planner and clock executable.
Screenshots verified the demo list, recorded batch status and visible stop/status
controls. No live game capture or model training ran during this milestone.
The GUI capture callback uses the existing protected runner; a new live capture
through the GUI remains untested.

See [the app guide](../DESKTOP_APP.md) and the retained
[`desktop-app-001` evidence](../../data/validation/desktop-app-001/).
The proposed full-player scheduler, manual player selection, multiple game
instances and automatic visual acceptance are not implemented by this launcher.

## 2026-09-08 - Action coverage and collection throughput

Implemented balanced competitive action discovery, automatic selected clock
preparation and longer protected clips. Four ten-second captures produced 1,280
reviewed images and 1,236 accepted samples. Refreshing the earlier three clips
gives **1,692 accepted/68 rejected**, with positive reload/control fields.
The eight histories spanning one ambiguous Nuke checkpoint remain rejected.

Repeated late packet scans measured 63.351 to 5.302 seconds with identical output;
one-pass HUD sheets measured 5.508 to 2.852 seconds with identical PNGs. Four
ten-second clips need four launches instead of eight. Added bounded compressed
cache corruption checks and final-after-exit capture budget enforcement.

The real four-sample RGB batch `[4,8,3,180,320]` saves/reloads exactly, with
8 valid angular and 320 valid button fields (24 positives).
**2,030 tests passed in 90.37 seconds.** Settings, installed game/HUD bytes and
staging cleanup checks pass. All samples stay in one BO3; no training or Cloud
work ran. See [the milestone and artifacts](ACTION_COVERAGE_AND_THROUGHPUT.md).

## 2026-09-08 - Competitive button proof and repeatable two-map batch

Completed the approved original-source button investigation and expansion
beyond the fixed Dust2 pilot. Original 14178 native enums, transition table and
protobuf conversions support held/net/activity targets with per-field, exact
command-row proofs. Missing fields and exact event counts/order/timing remain
masked. Full original-demo reconstruction reproduces all four Dust2/Nuke
tables byte-for-byte and independently checks phase/context sidecars. Nuke
state was regenerated with extractor 0.1.2.

Added bounded resumable planning/running across maps, rounds and players,
current-profile packet scanning beyond tick 20,000, and contact sheets covering
every original frame. Actual protected captures produced 160 Dust2 round-5
frames and 160 Nuke round-3 frames; all were visually reviewed. An initial Nuke
zero-acceptance result exposed a 1,024-second absolute clock cap. The corrected
check retains finite, increasing timestamps and exact measured 32 Hz cadence.
The runner issued fresh proofs using the existing captures and retained the
rejected attempt.

The two new clips accept 150 and 153 samples. Reissuing the original clean
pilot under v2 adds 153, for **456 accepted / 24 rejected**, 912 available
angular fields and 31,616 available button fields. A real three-sample CPU
batch saved and reloaded exactly: RGB `[3,8,3,180,320]`, six valid angular fields,
184 valid button fields and 19 positive valid button fields. All samples stay
in the same BO3 training group; validation/test remain empty.

Both sessions restored all 39 selected personal files, GameInfo and renderer
staging. Final direct checks confirm installed game/HUD hashes unchanged and
CS2 closed. Steam mode and Cloud preferences were unchanged. **1,889 Python
tests passed in 66.77 seconds**; no native code changed in this milestone and
no model training ran. See [results, artifacts and remaining work](COMPETITIVE_EXPANSION.md).

## 2026-09-08 - Current competitive replay and verified tensor pilot

Delivered the approved current-build replay proof, spectator HUD cleanup and
competitive training interface. Trial 006 has 160 reviewed original images
and 153 accepted eight-image histories with two future recorded command
boundaries per 32 Hz target. Seven initial histories reject; one also retains
its negative raw fraction. Competitive buttons and exact event timing remain
masked. The first complete real CPU batch is saved and independently reloaded
from `data/training/first-competitive-batch-002/`; its RGB shape is
`[2,8,3,180,320]`, with four valid angular fields and no valid button fields.
All three supplied BO3 maps remain in the same training split (153/0/0 samples).

Recovered the exact originally inspected 14178 server/client/engine binaries
into the workspace, without changing installed game files or historical
acceptance. The new current native profile separately checks eight 14180
binaries and exact source packet bytes. Fresh command reconstruction and
independently pinned audited Dust2 eligibility facts prevent rehashed labels
or metadata from granting acceptance. The visual review is bound to these
exact 160 pilot images; general corpus automation remains unfinished.

HUD cleanup uses a private stylesheet and custom VPK pair, explicitly mounted
under a journaled Game/Mod policy. Retained failures establish stock resource
precedence, the reserved `pak01` identity rejection, and Source2's base-name
chunk lookup. No installed archive, manifest or signature check was modified.
All six attempts preserved the 39 selected personal files, restored gameinfo
and archived staging out of CS2. Final direct hashes confirm normal binaries,
HUD assets and settings are unchanged; CS2 is closed. Steam mode/Cloud
preferences were unchanged.

The first tensor artifact remains as a diagnostic after a one-ULP resize
overshoot was found. A saturated-image regression and explicit normalized
range clamp fix it in batch 002. Verification: **1,698 Python tests passed in
55.53 seconds**, native Release build passed, and the real batch's hashes,
shapes, masks and source indices passed independent reload. No model training
was performed. See [full evidence and remaining limits](COMPETITIVE_TRAINING_PILOT.md).

## 2026-09-08 - 32 Hz execution and source-checked partial labels

Implemented the approved executor and label builder. The executor uses an
independently issued measured profile, carries fractional mouse counts, checks
held-state continuity and compiles bounded Windows events. Its protected worker
checks four actual ready-time mouse scales before insertion. Historical early
sensitivity reads are retained explicitly, rather than treated as ready-time proof.

The label audit rechecked 770 original images and 1,680 full commands and produced
838 two-command candidates. Of these, 834 have some usable fields; 63 have all
scoped non-exact fields. All twelve mouse and ten keyboard checks agree. Exact
event count/order/timing and absent message parents remain masked. The new local
profile accommodates observed repeated/+2 client-generation ticks while requiring
consecutive command/demo/execution ticks; older acceptance profiles are unchanged.

Two live attempts stopped before any insertion. Improved error receipts identified
VS Code holding foreground focus in the second attempt. After the user focused
CS2, the unchanged program completed with 256 decisions, 82 OS events, 385 verified
images and 840 complete commands. All five phases show expected angular and
movement/crouch effects. Raw mouse-count sums omit each phase's first impulse,
whose angular effect remains in recorded view/history and native camera evidence.
The frozen overall equality audit remains incomplete; no exact timing claim is made.

All three attempts preserved 39 selected personal files with zero restore
operations, restored gameinfo and removed staging. Final direct hashes matched,
with no CS2 process or renderer staging remaining. Steam mode/Cloud preferences
were unchanged. **1,455 Python tests passed in 45.03 seconds**, native Release
build passed. See [full evidence](CONTROL_EXECUTION_AND_LABELS.md) and
[API/reproduction](../CONTROL_EXECUTION.md). No competitive acceptance or model
training was added.

## 2026-09-08 - Windows synthetic input adapter and measured local response

Completed the approved keyboard/mouse adapter and first protected calibration.
Added bounded synthetic plans, external Win32 SendInput, a passive native
local-state/simulation-clock bridge, complete insertion/cleanup receipts, a
scoped high-resolution polling timer and independent response analyzers.
Probe actions use OS input; console commands configure only the private session
and its recording. The existing settings and owned-process lifecycle is shared.

Mouse001 and keyboard004 produced **770 original frames and 1,680 full commands**,
all independently bound to native pixels/original demo bytes. Mouse gain is
approximately −0.022 yaw degrees/X count and +0.022 pitch degrees/Y count at the
isolated sensitivity of 1. Four fit and eight unchanged held-out cases pass.
All ten keyboard/button response phases and twenty event checks pass; firing
and reload have corroborating ammunition/weapon evidence.

Keyboard002 exposed a post-insertion deadline-check mistake; 003 exposed a real
missed short tap caused by coarse polling. Both failed artifacts are retained.
Pre-insertion deadline checks, a scoped high-resolution wait timer and disk work
away from input deadlines let the identical plan complete in 004. All four
attempts preserved 39 selected personal files with zero restore operations,
restored gameinfo and removed their staged plugins. Final checks found no CS2
process or renderer staging. Steam mode/Cloud preferences were unchanged.

Verification: **1,258 Python tests passed in 44.73 seconds**, native Release
build and `git diff --check` passed. See [run evidence](SYNTHETIC_INPUT_CALIBRATION.md)
and [API/reproduction](../SYNTHETIC_INPUT.md). Exact consumption/subtick timing,
the 32 Hz contract executor and semantic training acceptance remain unfinished;
no model was trained or competitive acceptance changed.

## 2026-09-07 - Independent turning/button tests and startup settling

Completed the approved turning investigation and button probes, incorporating
the user's observed startup freezes and final synthetic keyboard/mouse goal.
Added native startup cadence, a continuous settling requirement and independent
Python verification through capture readiness. A review found and fixed a gap
that allowed a different pawn or reset clock between settling and capture.

Recordings009-011 produced 1,155 original images and 2,519 reconstructed full
commands; replay009 adds 160 images. All archived images match their own native
readbacks. All four sessions exited normally, preserved all 39 selected
personal files, restored gameinfo and removed their staged plugins. Steam mode
and Cloud preferences were not changed.

The frozen turning candidate passes all 22 ordinary left/right turn checks in
recording009; a 175-degree step retains its failed 0.0362-degree residual.
Startup gaps occurred before capture, while measured capture gaps stayed below
36 ms. The button confirmation supports scoped plane-1 held state and plane-2
net change, but falsifies complete edge reconstruction: three/four console
transitions can yield fewer exported subtick records. No rule was retuned and
no semantic training acceptance was enabled.

Reusable diagnostics, frozen plans, measured limits and the next synthetic-input
adapter milestone are documented in
[turning/button results](TURN_AND_BUTTON_CALIBRATION.md) and
[the plans guide](../../tools/renderer/plans/README.md).

Verification: **987 Python tests passed in 42.01 seconds**; native Release build,
all four protected sessions and `git diff --check` passed. The final staging and
process checks were clear. Original artifacts and failed hypotheses remain
unchanged.

## 2026-09-07 - Controller CLI and protected controlled recording/replay

Resumed the two approved steps. Added the 32 Hz controller schema export and
reverified two-command diagnostic candidate audit. Added a bounded native
Windows control recorder, independent dispatch/command/pixel diagnostics,
and a protected wrapper for replaying its recorded demo. All calibration and
controller candidates retain training/live-control readiness as false.

Inspected the installed 1.41.8.0 binaries under a separate calibration profile,
including changed engine addresses, local loopback/server checks, the input
registry and the existing full-command recording option. The first complete
recording exposed a missing delta baseline; complete command serialization
fixed subsequent recordings without inventing the old baseline.

Run 008 produced 321 original images and 14 dispatched actions. Every image
matches its native RGB readback, and all 711 full UserCmd payloads reconstruct
without parser warnings or projected-field discrepancies. Its five-second
replay produced 160 images covering the shot, crouch and turn. Original and
replay sessions exited cleanly. Across eight local attempts and the replay,
all 39 protected personal files remained unchanged, gameinfo was restored and
the run-owned plugin was archived out of CS2. Steam mode/Cloud preferences
were not changed.

Original/replay diagnostics independently match all 160 replay images to native
readbacks and verify stable in-eye identity. Equal numeric controller-tick
pairs agree on ammo, crouch/ground state and last-shot time. Position differences
reach 0.874 game units and eye-angle differences reach 9.375 degrees during the
turn; exact render/input phase equivalence remains unverified.

Measured raw button evidence shows plane 2 on both press and release; plane 3
is unobserved. Command-number associations and state changes remain diagnostic,
with no claim of exact input-consumption timing or physical-device latency.
The original 129 accepted samples remain intact, but their current revalidation
is blocked by the missing original Valve server binary after the game update.
See [run evidence](CONTROL_CALIBRATION_RUNS.md), [controller](../CONTROL_CONTRACT.md)
and [calibration](../CALIBRATION.md) for reproduction and remaining work.

Final verification: **895 Python tests passed in 41.06 seconds**; native Release
build and protected live recording/replay passed; `git diff --check` passed.
The eight pinned game modules are retained with checked hashes under
`data/native-profiles/calibration-14180-v1/` to preserve this inspection evidence.

## 2026-09-07 - Controller and calibration implementation paused

Saved a proposed 32 Hz controller format, diagnostic two-command candidate
builder, protected Windows calibration worker and component guides. All 94
focused tests pass. Native calibration support, CLI integration, real-data
candidate auditing and original/replay calibration measurements remain
unfinished. No game was launched, no settings were changed and no new training
samples were accepted. Work stopped at the user's request; see the
[resume checkpoint](CONTROL_CALIBRATION_CHECKPOINT.md).

## 2026-09-07 - Packet synchronization and first accepted training subset

Completed the approved clock-proof and first-subset work, with Steam Cloud
testing deferred. Added `cs2-clocks`, independent protobuf/source-packet readers,
native message and complete packet traces, and audits that recompute paired
calls, seek filtering, source identity and image information bounds.

Protected trial 016 produced 160 frames at 1280x720/32 FPS. All images and the
final endpoint have verified packet bounds; all images pass pixel and native
first-person identity checks. The audit accounts for 7,045 paired packet reads,
2,690 exact payload matches and 189 independently reproduced seek-filter matches,
with no unmatched packets. The run exited cleanly, preserved all 39 protected
settings files, restored gameinfo and removed its staged plugin from CS2.

Added the scoped `recorded_future_server_command_v1` acceptance profile. Its
eight-image histories predict one future command; both adjacent commands used
for the aim difference begin strictly after the observation information bound.
Raw button planes and command protobufs are retained. Previous actions and
subtick trajectories are excluded from model inputs. Acceptance checks raw
clocks, pawn/action fields, repeated records and optional presence, and rejects
unsupported flags or fractions. Source changes during verification abort output.

The fresh public output `data/accepted/dust2-causal-016-v1/` contains **129 accepted
and 31 rejected samples**. Its loader independently rescanned and reproduced
the same evidence and exact records. Earlier strict campaign reports remain
unchanged at zero accepted samples. This pilot is not a large training corpus;
the tensor loader and model training remain future work. Sampled HUD review
still shows the replay observer name/weapon strip.

Verification: **619 Python tests passed**; extractor/renderer Go suites passed,
including five new clock-extractor tests; the native plugin built and passed
live trial 016. The final plugin hash and acceptance contract hash are recorded
in [STATUS.md](STATUS.md). The implementation and reproduction commands are in
[SYNCHRONIZATION.md](../SYNCHRONIZATION.md).

## 2026-09-07 — Steam Offline Mode context clarified

After confirming normal preferences, the user reported that Steam is in Offline
Mode. Scoped the settings-verification milestone accordingly: local settings,
native path/interface guards and cleanup passed; online Steam Cloud and behavior
after reconnecting are not yet tested. Added the context as a separate trial-012
record, preserving the original capture/verification manifests. Added online
verification to remaining work. No game settings, Steam mode or renderer code
changed for this clarification.

## 2026-09-07 — Live settings protection passed and game staging cleaned up

After the user restored normal preferences, protected trial 011 captured 64
frames and passed native startup/path/Cloud checks. Its 39 selected personal
files remained unchanged. A shutdown access violation was traced through the
matching dump/MAP to the optional `dem_render_info` command destructor, which
unregistered after the engine command registry had been destroyed. Disabled
that unused Windows-only registration while retaining the pinned Cloud guard.

Trial 012 then exited normally and encoded a **64-frame, two-second,
1280x720/32 FPS MP4**. All 64 original images match native readbacks. Independent
read-only checks confirmed that all 39 personal files matched their pre-render
bytes, sizes, timestamps and read-only flags, and the baseline matched trial 011.
Only two files in the private clone changed. Backups verified, locks released,
gameinfo restored and the staged plugin moved back into the workspace.

The successful output and evidence live in `data/rendered/windows-settings-012/`.
The tested DLL SHA256 is
`0f9c1f0c4c4a6275960d48d2df8a9b1b5684040a490f9cd9ad484989d1c3823c`.
Trial 011 remains marked failed and its DLL/debug files and diagnosis are retained.

Individually audited and archived all **17 historical** inactive plugin folders
into each original run's `renderer-sandbox/`. No renderer staging folders remained
in CS2; gameinfo retained its original hash. Exact mappings and owning-journal
hashes are in `data/settings-audits/historical-staging-2026-09-07.json`.
The user then confirmed that audio, video and HUD look correct when launching
CS2 normally through Steam.
The prior 226 Python tests remain the fixture checkpoint; the changed native
adaptation built and passed the real repeat trial. Training timing remains unverified.

## 2026-09-07 — Personal-settings isolation and recovery implemented

Added bounded config snapshots, byte/mtime/read-only restoration, durable root
locks, verified cloning and conflict-aware interrupted recovery. The worker
clones preferences into a run-owned profile before launching, requires the new
native policy marker and path/Cloud proof, restores selected files only after
CS2 stops, and relocates its staged plugin into the workspace before encoding.
Steam launch options and account Cloud preferences are not changed.

The native guard builds successfully and uses exact engine/filesystem hashes.
All **226 Python tests pass**, including 63 new settings/worker cases.
Fixture checks exercise launch failures, timeouts, KeyboardInterrupt, live-process
refusal, external edits, corrupt backups, partial recovery and malformed native
proof. A real protected launch is pending the user's normal-settings baseline;
this entry does not claim runtime isolation has passed.

Correction to earlier wording: historical "game configuration restored" meant
the original `gameinfo.gi` bytes, not a backup of audio/video/HUD preferences.
The new protection preserves the settings present at each run's start. It cannot
reconstruct unknown preferences from before earlier captures. Recovery and scope
are documented in [WINDOWS_RENDERING.md](../WINDOWS_RENDERING.md).

## 2026-09-07 — Multi-player validation and sample acceptance

Implemented `validate-clip`, `accept-samples` and `summarize-campaign`. Reports
recheck hashes, raw rows, frame evidence and current acceptance policy. Temporal
samples retain exact history/target/predecessor IDs; rejected samples explain
phase, pause, continuity, missing inputs, invalid fractions and evidence limits.
Local POV/action failures affect crossing windows; timing/integrity gates remain strict.

Native instrumentation now observes Steam identity through reciprocal observer
handles, in-eye mode, camera/matrix state, pause, pawn state and weapon clocks.
Trials 008–010 cover Ckanic, Nikodeon and ay0k in three competitive Dust2 rounds:
**480 images, 960 commands, all pixel and exact first-person identity checks passing**.
Firing, aim, movement, jump/landing and crouch observations are retained with scope.
All launches exited normally and restored game configuration.

Extractor 0.1.2 fixes the old pause prefix and off-by-one firearm ammo helper.
Dust2 was fully re-extracted into `data/parsed/v2-state-fixed/`; commands, rounds
and events stayed byte-identical. Context schema 2 records five pause flags,
unambiguous shot clocks and audited warning provenance. Ambiguous or loss-bearing
evidence cannot silently qualify a sample.

The revalidated campaign has **480 candidates, 456 complete temporal windows,
zero accepted samples**. Execution/observation timing remains unproven; the
report also retains missing button messages, invalid fractions, camera effects
and one crouch disagreement. These are explicit remaining blockers, not passing
training labels. Current artifacts and limitations are in [STATUS.md](STATUS.md).

Historical reports/captures remain untouched. Nuke/Cache state migration and the
loader, splits, model and training loop remain future work.

Final verification: **163 Python tests and all extractor/renderer Go suites
passed**, native Release and all three Go executables built, and the original
gameinfo SHA256 was restored. The campaign recomputed all three acceptance
partitions before publishing its counts.

## 2026-09-07 — Unmasked run-006 pilot completed and tested

Completed `data/rendered/windows-competitive-006/` and its real dataset under `data/datasets/dust2-competitive-006/`: **160 frames, 320 future commands, two commands per frame, zero empty frames, normalized inputs and `viewer/inspect.html`**. The v5 native HUD profile uses six seconds of UI settling and no encoder masks. Independently inspected raw frames 0, 50, 51 and 159 preserve money/health/ammo/crosshair/viewmodel and show no stale MATCH START notice, chat or private team-stat panels.

The isolated-shot check is concrete: frame 50 still shows 20 Glock rounds and its future commands include the attack at demo tick 6102; frame 51 shows a muzzle flash and 19 rounds. Native readback hashes match all 160 archived images. Actual `EventClientOutput_t.m_flRenderTime` values and a native endpoint define the real timing intervals, using future `(start,end]` targets.

Added native replay-epoch evidence: `IDemoFile.GetDemoStartTick=-5546`. This is deliberately preserved separately from the parser's 10703 execution-versus-packet tick difference. The working capture/alignment/viewer pilot retains `training_ready=false` while broader epoch/POV/subtick acceptance is completed; one invalid subtick observation at demo tick 6007 remains preserved. Callback instrumentation and real alignment are implemented and tested, rather than left as an unimplemented milestone.

Final video SHA256: `9d5f1ed08258fc97a101d733cd8500a3e79b292ceb4fdf576aca4c5ada698046`. Final DLL SHA256: `4b52bd78efe574d837d155ff91921ca57d0b284bfd497457b3b01293737b18c1`. CS2 exited normally and restored the original gameinfo hash. **92 Python tests and all Go suites passed.** Final review added a staged-DLL hash check and a regression test: concurrent rebuilds now fail before game configuration changes, and run 006's retained DLL matches its manifest. Updated the current status, HUD evidence and remaining acceptance work; earlier masked and knife/setup runs remain unchanged as history.

## 2026-09-07 — First real diagnostic dataset and native HUD settling

Completed the real capture-to-dataset pipeline for `windows-competitive-005`: **160 frames, 320 future commands, zero empty frame intervals**, aligned Parquet files and a local viewer under `data/datasets/dust2-competitive-005/`. Native pixel hashes match all 160 archived images. Timing uses observed `EventClientOutput_t.m_flRenderTime` values and the recorded first movie callback after capture stops; future targets use `(start,end]` intervals.

The current execution mapping still assumes the observed render-time clock shares the `server_tick_executed` epoch at the canonical 64 Hz tick rate. Its calibration remains `inferred_from_packet_arrival`, with no independent execution anchors and `execution_timing_verified=false`. Real output counts do not validate that assumption; the dataset remains `training_ready=false` pending epoch and visual synchronization acceptance.

Resolved the stale MATCH START notice without modifying pixels: three existing two-second pause/resume actions after seeking provide six seconds for real-time UI settling. Changing `hidehud 192` to `hidehud 128` also retains money and weapon selection. Run-005 raw first/last previews show no announcement, extra team panels or chat while preserving the player's HUD. The new default `windows-pilot-v5-native-player-hud` removes all encoding-mask code.

Run 005's already-written v4 MP4 still contains its historical announcement mask, and remains unchanged. A subsequent v5 repeat will validate unmasked encoding and newer native epoch instrumentation. Updated the HUD guide, current inventory and next steps to distinguish these artifact versions and completed versus unverified work.

## 2026-09-07 — Competitive phase filtering, native HUD controls and pixel correspondence

Added the lightweight `cs2-phases` Go command to record game-rule phase, match/warmup state, score progression and restart evidence without decoding UserCmd payloads again. The independent, source-hashed sidecars leave canonical Parquet data unchanged and refuse to overwrite existing evidence. Default render planning now requires live competitive phase evidence and a retained scored result; unknown/setup phases require an explicit diagnostic override. Optional paired tick bounds recompute command coverage after trimming.

All three supplied demos contain **24 competitive and 2 setup rounds** under these checks. The initial knife round had `IsMatchStarted=true` and `IsWarmup=false`, but `GamePhase=Pregame`; using only warmup state had incorrectly admitted it. Ordinary `cs_pre_restart` events are not treated as aborted matches because they occur before normal rounds. Captured Dust2 event fixtures test this distinction and score rollback. **45 dataset/phase Python tests and 2 phase-extractor Go tests passed** at this checkpoint.

Selected `dust2-competitive-shot-001.json`: Ckanic, slot 9, canonical round 3 / competitive round 1, ticks `[6000,6320)`, **320 commands covering 320 ticks**, and one isolated Glock-18 shot at tick 6102. The selection companion records input/event/aim/movement evidence and source hashes.

Native replay trials `windows-competitive-003` and `004` produced **160 frames / 5 seconds at 1280x720 and 32 FPS**. Raw previews confirm that the native command bundle removes the ten-player equipment panels and chat while retaining alive counts, timer, limited radar, and own health/ammo/crosshair/viewmodel. MATCH START remained in run 003. A `hud_reloadscheme` trial did not suppress it before capture in run 004; its last preview no longer shows the notice. The reload command was removed, and the current encoding fallback is only `round-announcement-mask-v1`, a small rectangle whose occlusion is documented. Broader masks were removed; all raw TGA frames remain available.

Run 004 adds native movie-submission and pixel-readback evidence. **All 160 archived TGA RGB hashes match native readback records, with 160 distinct images and no repeated pixel frames.** The run exited normally and restored gameinfo. This proves pixel correspondence but does not complete fractional replay-clock interpretation, final interval measurement, command execution calibration, or real synchronization review. Candidate clock calibration and diagnostic alignment code are being completed separately; outputs remain training-unverified.

Added [phase usage/evidence](../PHASES.md) and [installed HUD asset hashes, commands and runtime trials](../HUD_PROFILE.md). Updated the current inventory and next steps; earlier knife-rendering attempts below remain historical diagnostics.

## 2026-09-07 — Native Windows replay capture delivered

Implemented `tools/renderer/windows.py` and the x64 C++ adaptation under `plugin-windows/`. Installed local CMake/FFmpeg and the missing Visual Studio C++/Windows SDK components. The worker validates the demo/job, stages an isolated plugin, bounds the owned CS2 process and disk use, archives raw TGA frames, encodes H.264 and verifies decoded PTS/count/dimensions. It records a recovery journal and restores the original gameinfo bytes after both success and failure.

Diagnosed the initial real-game failures using startup logs and crash dumps. Backported the upstream July ICvar layout correction and current replay interface positions, corrected the shutdown callback signature and added bounded frame-hook initialization. The final DLL renders successfully on local CS2 1.41.7.8.

- Four successful real captures: two runs of **64 frames / 2 seconds** and two of **160 frames / 5 seconds**, all **1280x720 at 32 fps**.
- Inspected ay0k's POV and compared encoded previews with an actual CS2 window screenshot; color/orientation agreed qualitatively.
- Confirmed normal CS2 exit and byte-exact gameinfo restoration; failure artifacts 001–003 remain available.
- **44 Python tests passed**, including 10 Windows tests with real FFmpeg color/origin/PTS and transaction-failure checks.
- Added [Windows setup/usage](../WINDOWS_RENDERING.md) and updated this tracker.

Remaining: measured frame/action timing, competitive-phase selection, transient HUD cleanup and longer jobs. Software H.264 works; this FFmpeg's NVENC requires a newer driver. Matching repeated counts do not establish deterministic pixels or timing. All render outputs remain training-unverified.

## 2026-09-07 — Progress tracking established

Created `docs/progress/` with an index, implementation/status inventory, ordered remaining work and this completion log. Cross-checked the real audited parse/validation/normalization reports and current source inventory. Added a link from the repository README. Corrected older alignment examples to use audited parser outputs and updated the rendering guide to identify the covered slot-3 job.

This update is documentation only. It did not launch CS2, capture frames, train a model or rerun the existing test suites.

## 2026-09-07 — Initial data pipeline implemented and audited

### Delivered

- Go project and `cs2-extract extract` / `validate` commands; pinned full-mode demoinfocs v6 parsing.
- Compressed canonical command/state/round/event Parquet tables, full reconstructed protobuf storage, nullable fields and explicit parent-message presence.
- Demo and shared-match identity, separate clocks, processing versions, file checksums, structured logs and guarded output publication.
- Streaming validator for integrity, schema, reconstruction coverage, input activity, continuity and subtick/history values.
- Python `cs2-data normalize`, `render-jobs`, `align` and `viewer` commands.
- Normalization with angle wrap/reset masks and separate effective mouse values; render planning with identity and command-coverage checks.
- Pinned Reka checkout/setup, environment doctor, compiled one-job adapter, intended HUD/viewmodel settings and cleanup protections.
- Measured-frame alignment and local HTML debug viewer with synthetic integration coverage.
- Setup, schema, rendering, alignment and initial-run documentation.

### Real execution and recorded checks

- Parsed all three supplied ESL recordings to completion: **4,933,316 reconstructed commands** and **6,840,962 available player-state rows**.
- Normalized all three recordings: **3,511,650 valid alive-player angle transitions**; raw rows and explicit invalid-target masks retained.
- Verified output integrity and reconciled all eligible payloads with reconstructed/rejected counts.
- Built and dry-ran `dust2-audited-first.json`, a slot-3 interval with 1,179 commands covering 1,179 demo ticks.
- Extractor/validator Go tests passed; **34 Python tests** and **3 targeted renderer tests** passed.
- Complete-demo data-quality validation **failed for all three recordings**, for the input issues below. No real render/alignment integration passed.

### Findings and decisions retained for future work

- Added both parser warning handlers after discovering that the pinned prerelease routes some reconstruction warnings through its net-message dispatcher. Earlier apparently warning-free results were incomplete diagnostics.
- Retained **1,907,682 missing-baseline rejection reports** across the three demos. Dust2's losses are unavailable initial prefixes; later decoded windows have continuous demo-tick coverage. Baselines were not fabricated.
- Kept **6,245 out-of-range subtick values** unchanged and flagged their unresolved meaning.
- Added schema-2 parent presence so omitted protobuf scalars with known zero defaults are distinguishable from unavailable parent messages.
- Preserved the Dust2 observed **10,703-tick execution-versus-packet offset** as a diagnostic, not a proved frame alignment rule.
- Replaced the unsuitable initial slot-2 render candidate with the audited covered slot-3 job. Earlier data/job artifacts remain historical diagnostics.
- Kept normalization and alignment outputs explicitly `training_ready=false`.

### Unfinished at this checkpoint

No replay pixels captured: the local Windows CS2 build and pinned Linux renderer/plugin are incompatible. Capture anchors, calibrated execution alignment, real viewer inspection, subtick interpretation, clip acceptance, dataset loading/splitting, model training and runtime evaluation remain unfinished.

Authoritative local outputs: `data/parsed/v2-audited/`, `data/normalized/v2/`, and `data/jobs/dust2-audited-first.json`. Versions, per-demo counts and detailed evidence are in [STATUS.md](STATUS.md) and [INITIAL_RUN.md](../INITIAL_RUN.md).
