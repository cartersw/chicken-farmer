# Current implementation status

**Latest work (2026-09-08): trust the approved capture HUD setup.**
The user viewed the nine round-progression clips, said they look fine, and
explicitly chose to trust the current setup without recurring manual review,
overlay detectors or spot checks. Routine batches now use a small policy receipt
bound to the approved renderer/plugin/resource metadata and do not generate HUD
contact sheets. The HUD basis is `user_approved_capture_setup`, with
`visual_review_performed: false`; this does not claim every frame was inspected.
Timing, source reconstruction, exact first-person identity, original-pixel
integrity and restoration checks remain automatic. Unsupported metadata fails
normal compatibility checks. The manual HUD tool remains optional diagnostics.
Existing captures and historical reviews are preserved. No new sample acceptance
or model training has run as part of this policy change.

The source-proof profile is now `cs2-competitive-replay-source-proof-v3`; the
outer acceptance schema remains v2. Existing accepted publications bind older
proof/code hashes and need fresh numerical publications for the current loader.
The last published **1,692 accepted samples** were verified under the earlier
proof. This HUD policy change does not reverify or upgrade those partitions,
and their retained reports are unchanged.

**Verified:** [357 distinct regression tests pass](../../data/validation/trusted-hud-001/verification.json),
covering the core policy, numerical acceptance, dataset loading and UI/report
backends. The desktop GUI suite was not rerun. The refreshed round report shows
all nine captures ready for acceptance, with the two unused jobs still unrecorded.
The [nine-capture policy check](../../data/validation/trusted-hud-001/nine-capture-policy.json)
generated and validated all nine trusted-setup receipts in 0.137 seconds,
retaining 31,212 bytes. It read no original images, generated no contact sheets
and launched no game. This verifies the setup policy, not sample acceptance.

**Previous work: nine-clip round progression recording.**
The [round progression trial](ROUND_PROGRESSION_TRIAL.md) adds action-blind,
chronological collection for one player's selected competitive alive rounds,
using the existing protected renderer and standard batch journals. Ckanic's
first two Dust2 competitive rounds have eleven planned clips covering 188.75
seconds. Two excluded final demo ticks and the independent clips' initial
history losses are explicit. A sequential coordinator supplies bounded storage,
time and free-space checks, stop/resume, and a local video/status report.

The user stopped collection after **nine captures: 166.75 seconds and 5,336
original images**, retaining approximately 28.09 GB in 34 minutes 41.8 seconds
of active processing. The last twenty-second and two-second planned clips are
intentionally unrecorded; this does not cover two complete rounds. Final direct
checks verify all nine captures' settings restoration, eight installed binary
hashes and HUD cleanup, with CS2 closed and no held locks or staging remaining.

The nine retained journals record the earlier pending-review state; the approved
HUD setup now removes that manual gate. One source-clock ambiguity in round
3's third clip affects eight prospective histories, which remain unavailable
under the current acceptance rules; no new accepted/rejected counts are claimed.
The desktop sample planner now offers 5, 10 or 20 seconds, retaining the
10-second default; live GUI capture remains untested. **123 focused tests pass.**
No model training has run. See the [trial evidence](ROUND_PROGRESSION_TRIAL.md)
and [workflow](../ROUND_COLLECTION.md).

**Previous work: desktop player selection and batch/review recovery.**
The [desktop app](../DESKTOP_APP.md) now loads recorded player names and Steam
IDs from the selected demos and plans sample captures for that exact player.
The scope resets when demos/output change; late roster results cannot apply to
a different selection. Discovery filters before bounded sampling, records the
player scope, and fails without substitution if no eligible clips exist.
Invalid batch files leave the loaded batch intact, malformed summaries cannot
break UI polling, and review links use the current completed journal attempt
with a checked page checksum. Refresh preserves the selected clip.

**Verified:** 136 backend/collection/batch tests and 20 real Windows Tk tests
pass. A real GUI session loaded Dust2's 12 recorded roster entries, selected
**Ckanic (`76561198323592528`)**, and produced four ten-second plans for rounds
7, 16, 18 and 23: two ordinary and two reload examples. The real planner and
clock tools finished in about 85 seconds, with existing hashed source data reused.
The player controls and resulting batch were visually inspected. Evidence is in
[`desktop-app-002`](../../data/validation/desktop-app-002/); the real plan remains
under `data/desktop/runs/20260908-103707-plan-fa4bb6d6/collection/batch/`.
No capture or model training was started. Live GUI capture, parallel instances
and the exhaustive full-player queue remain unfinished.

**Previous work: desktop demo launcher.**
The [desktop app](../DESKTOP_APP.md) adds a double-click Windows launch file,
remembered demo/output folders, background source preparation, bounded clip
planning, live logs and one-clip protected run/resume controls. It preserves
quality failures and pending visual review, checks retained source tables before
reuse, and waits for the current process on stop/close. One UI owns the project
at a time. Player selection was added in the follow-up above; multiple instances
and the complete-player queue remain future work.

Validation covers 19 launcher backend tests, six real Tk interface tests and
39 existing collection/batch regressions. A real Dust2 smoke run reused canonical
source data and produced one ten-second plan without launching CS2. The app
also displayed all three supplied demos and the existing four-job batch; both
screens were inspected. Evidence is in
[`data/validation/desktop-app-001/`](../../data/validation/desktop-app-001/).
No new frames, accepted samples or model training were produced by this work.

**Previous milestone: action coverage and collection throughput.**
Four protected ten-second captures produced 1,280 reviewed frames and 1,236
accepted samples. The refreshed seven-clip corpus has **1,692 accepted /
68 rejected**, with 3,384 valid angular fields and 125,240 valid button
fields. Positive reload, attack, movement and crouch labels are present; rapid
jump activity retains its uncertainty. All samples remain in one BO3 training
group, with no validation/test series and no model training.

Collection now balances action examples with ordinary play, prepares selected
clock windows and preserves resumable stages. Four ten-second clips require
four launches instead of eight five-second launches. HUD sheet preparation
measured 1.93x faster; repeated late source scans measured 11.95x faster with
identical output. Exact source/settings/visual checks remain required.

The four-sample RGB batch `[4,8,3,180,320]` saves and reloads exactly, including
valid positive reload and attack fields. **2,030 tests pass.** Direct checks
confirm settings, installed game/HUD bytes and staging cleanup. One Nuke
checkpoint frame retains its ambiguity and eight affected histories reject.
See [the completed milestone](ACTION_COVERAGE_AND_THROUGHPUT.md),
[collection commands](../COMPETITIVE_COLLECTION.md) and
[remaining work](NEXT_STEPS.md).

**Previous milestone: competitive buttons and resumable batch processing.**
The original 14178 button audit supports held/net/activity fields with exact
per-command proofs. Full original-demo reconstruction replaces the fixed Dust2
eligibility pins and reproduces all four Dust2/Nuke tables byte-for-byte, plus
phase/context evidence. The batch runner plans multiple rounds/players/maps,
preserves completed stages and pauses for source-bound visual review.
Two new protected captures produced 320 reviewed frames; settings and installed
game/HUD hashes are unchanged. Together with the reverified original pilot,
that milestone produced **456 accepted samples and 24 rejected candidates**.
The real three-sample tensor batch passes exact reload with six valid angular
fields and 184 valid button fields, including 19 positive button fields.
The final suite passes **1,889 tests**.
See [the expansion milestone](COMPETITIVE_EXPANSION.md) for accepted counts,
the retained Nuke absolute-clock rejection and its fix, and tensor results.
Exact event timing, weapon-selection controls, broader corpus coverage and
independent validation/test series remain unfinished. No model has been trained.

**Previous work: current competitive replay and masked acceptance verified.**
Trial `windows-competitive-current-006` produced 160 original images with the
spectator strip removed and player HUD preserved. All 160 image, native clock,
POV and packet-bound checks pass. That pilot profile accepted **153 eight-image
samples** with two future recorded command boundaries per 32 Hz angular target.
Seven candidates lack complete history; one also retains an unsupported raw
fraction rejection. In that publication, competitive button fields and exact event times stayed masked.
See [the milestone and artifacts](COMPETITIVE_TRAINING_PILOT.md),
[acceptance contract](../COMPETITIVE_ACCEPTANCE.md) and
[tensor loader](../TRAINING_DATASET.md).
The real CPU batch is saved at `data/training/first-competitive-batch-002/` and
passes independent reload: RGB shape `[2,8,3,180,320]`, four valid angular
fields, no valid competitive button fields, and an exact normalized pixel
range within `[0,1]`. Whole-series splits are 153 train, 0 validation, 0 test.

The exact original 14178 server/client/engine binaries were recovered into the
workspace and match their previously recorded hashes. Current renderer support
is checked separately against eight 14180 binaries. Fresh prefix reconstruction
and pinned, previously audited Dust2 eligibility metadata close the pilot's
source boundary. That acceptance was restricted to the reviewed source/capture;
full-match automation and competitive button semantics were unfinished at that milestone.

Five earlier attempts are retained, including two rendered HUD failures and
three archive/mount startup failures. All six attempts preserved the 39 selected
personal files, restored gameinfo and removed staging. Final direct checks also
confirm the installed binaries/HUD bytes are unchanged, CS2 is closed and no
renderer folders remain in the game. Steam mode/Cloud preferences were unchanged.
Verification: **1,698 Python tests passed in 55.53 seconds** and native Release
build passed. No model has been trained.

**Previous milestone: 32 Hz executor and scoped label builder implemented.**
The executor carries fractional mouse counts, retains held keys and sends the
compiled program through the protected Windows adapter. The completed combined
test produced 256 decisions, 82 OS events, 385 verified images and 840 complete
commands. All five phases match the requested angular response and movement or
crouch behavior. A stricter raw demo mouse-count sum check fails in every phase;
the independent overall result remains incomplete, with the mismatch preserved.
See [results and limits](CONTROL_EXECUTION_AND_LABELS.md) and
[API/reproduction](../CONTROL_EXECUTION.md).

The new source-checked label export produces 838 two-command candidates from the
earlier local mouse/keyboard recordings: 834 have some usable fields, including
63 with all scoped non-exact fields observed. All twelve mouse and ten keyboard
cross-checks pass. Missing fields and exact event count/order/timing stay masked.
These local labels have no competitive image acceptance and no model was trained.

Two preflight attempts inserted no input; the second identified VS Code holding
foreground focus. The focused retry completed. All three preserved the 39
selected personal files, restored gameinfo and removed staging. Actual ready-time
mouse scales were verified; historical early sensitivity readbacks remain
explicitly distinguished from ready-time evidence. Steam mode/Cloud preferences
were unchanged. Verification: **1,455 Python tests passed in 45.03 seconds**,
native Release build passed, and final direct personal-file/gameinfo hashes match.

**Previous milestone: synthetic Windows keyboard/mouse control measured.**
The reusable adapter sends scan-code keys, mouse buttons and relative mouse
movement through Windows. The protected mouse and keyboard recordings produced
**770 verified original images and 1,680 complete UserCmds**. All four mouse fit
cases and eight held-out cases pass; all ten keyboard/button response phases and
20 event checks pass. See [the run evidence](SYNTHETIC_INPUT_CALIBRATION.md) and
[the API/reproduction guide](../SYNTHETIC_INPUT.md).

Two failed keyboard attempts exposed a post-insertion scheduling mistake and
coarse Python sleep timing. They remain preserved; a corrected pre-insertion
check, scoped high-resolution wait timer and disk work outside input deadlines
enabled the unchanged keyboard plan to complete. All four attempts preserved
the 39 selected personal files, restored gameinfo and removed the staged plugin.
Final checks found no running CS2 or renderer staging. Steam mode and Cloud
preferences were unchanged.

Verification: **1,258 Python tests passed in 44.73 seconds**, native Release
build passed, and both successful recordings passed their independent audits.
At that milestone, exact input-consumption timing, a 32 Hz action-contract executor
and general semantic training labels remained unfinished. No competitive samples were added
and no model was trained.

**Previous milestone: turning/button investigation and measured startup settling.**
Three new controlled recordings contain 1,155 verified original images and
2,519 fully reconstructed commands; a new turn replay adds 160 verified images.
The frozen interpolation candidate passes all 22 ordinary turn checks in the
independent recording, while its large angle step remains an outlier. Rapid
button tests confirm scoped held-state/net-change rules and show that exported
records do not reproduce every known console transition. Exact event labels
remain masked. See [the full results](TURN_AND_BUTTON_CALIBRATION.md).

Those earlier probes used console dispatch to diagnose timing and button fields.
Measured startup delays occurred before capture, and the
worker verifies a continuous settling period. No new competitive samples were
accepted and no model was trained.
Verification: **987 Python tests passed**, native Release build passed, and all
four sessions preserved the 39 protected files and removed their staged plugins.
CS2 was closed at the final check; Steam mode/Cloud preferences were unchanged.

**Earlier milestone: controller CLI and controlled Windows calibration implemented.**
The successful local probe has 321 verified images, 14 control events and all
711 full commands reconstructed without parser warnings. Its protected replay
produced 160 images over five seconds. Both processes exited cleanly, preserved
all 39 selected personal settings files and removed their staged plugins.
All 160 replay images match native readbacks, with stable in-eye identity.
Paired shot/crouch state agrees; turn eye angles differ by up to 9.375 degrees
at equal numeric controller ticks, so phase calibration remains incomplete.
See the [historical run evidence](CONTROL_CALIBRATION_RUNS.md). The historical pilot
below remains intact; at that checkpoint, revalidation was blocked by the
missing original Valve server binary, since recovered for the new pilot.
That calibration data remains diagnostic;
exact input-consumption timing and semantic button labels remain unverified.
Final verification: **895 Python tests passed**, native Release build succeeded,
and both successful 008 sessions exited cleanly. Steam Cloud testing remains
deferred; the user-reported Steam mode was not changed.

**Checkpoint: 2026-09-07 — packet clock bounds verified; first 129 training samples accepted.**

The new `recorded_future_server_command_v1` profile accepts **129 samples** from
protected capture `windows-timing-016`. Each sample contains eight image
references and one future command. Both commands used for the normalized aim
difference have support strictly after the image's verified information ceiling.
Of 160 candidate positions, 31 are rejected for incomplete temporal windows or
input-quality failures. This is a five-second pilot, not a training corpus of
sufficient size or a trained model.

The public outputs are under `data/accepted/dust2-causal-016-v1/`: accepted and
rejected JSONL, `causal_acceptance.json`, and the directly scanned source-packet
evidence. Acceptance and its loader recompute the source proof. See
[synchronization and target semantics](../SYNCHRONIZATION.md).

All 160 images pass pixel and first-person identity checks. The packet audit
reconstructs 7,045 paired reads: 2,690 match original packet bytes and 189 match
the independently reproduced native seek filter, with zero unmatched packets.
Earlier seek information remains in the bound. Trial 016 exited successfully,
preserved all 39 protected settings files, restored gameinfo, and removed its
staged plugin from CS2. **Steam Cloud testing is deferred at the user's request.**

The Windows worker now backs up selected preferences, launches with a cloned
settings profile, requires a native config Cloud/path guard, and restores
settings after process exit. It also moves the run-owned plugin folder out of
the game installation. Trial `windows-settings-012` passed with **64 frames,
a two-second MP4, clean CS2 exit, and all 39 selected personal settings files
unchanged**. A shutdown crash found in trial 011 was diagnosed and fixed.
All 17 historical renderer staging folders were also archived out of CS2.
The user subsequently launched CS2 normally through Steam and confirmed that
audio, video and HUD preferences look correct.
The user then clarified that Steam is in **Offline Mode**. Treat this as the
user-reported test context, not a worker-observed network measurement. These
results verify local settings and the native interface guard; online Steam Cloud
synchronization and behavior after reconnecting remain unverified.
Earlier runs restored `gameinfo.gi` only, and could save capture audio/video/HUD
settings. The new baseline cannot recover preferences from before those runs.

The Windows pipeline now has a real three-player, three-round validation campaign:
**480 captured frames, 960 aligned commands, and 480 exact first-person POV checks passing.**
It produces accepted/rejected sample manifests with specific reasons.
That historical strict campaign still accepts zero samples. Its original
fractional-trajectory assumptions are not promoted by the new, narrower
future-command profile, and none of its original artifacts were relabeled.

## What works

| Component | Current behavior and evidence |
| --- | --- |
| Synthetic Windows input | Protected SendInput adapter and bounded 32 Hz executor; combined aim and movement response observed, while raw mouse-count equality remains unresolved. Exact consumption timing is unverified. |
| Scoped control labels | Source-checked local aim, held-state/net-change and activity fields with per-field masks; 838 diagnostic candidates and twelve mouse/ten keyboard checks. Competitive image acceptance remains separate. |
| Canonical extraction | All three demos retain 4,933,316 reconstructed commands, full protobufs, identity/presence fields, state, rounds and events. Missing baselines and invalid fractions remain visible. |
| Windows rendering | Protected trial 016 renders 1280x720 at 32 FPS, retaining 160 raw TGA, MP4, PTS, native readback hashes, packet evidence and the final endpoint. |
| Settings protection | Trial 012 verified local isolation and cleanup; clock captures 014-016 also preserved all 39 selected files and removed staged plugins. Steam Offline Mode was reported by the user; online Cloud/reconnection behavior is unverified and deferred. |
| Competitive filtering / HUD | Phase sidecars identify 24 competitive and 2 setup rounds in each demo. Native HUD controls and settling remove earlier stale announcements without image masks. |
| Exact POV | Native observer handles resolve SteamID and reciprocal pawn/controller identity. Validation checks in-eye mode, view overrides and camera stability across pixel readback. All 480 historical campaign frames and all 160 trial-016 frames pass. |
| Frame/action alignment | The historical clips each contain 160 frames and 320 diagnostically aligned commands, normalized aim and an inspector. Trial 016 adds a separate accepted future-command partition. |
| Broader action checks | Captures cover firing, dynamic aim, movement, jump/landing and crouch transitions across Ckanic, Nikodeon and ay0k. |
| Sample acceptance | Configurable image history, previous actions and future intervals; exact raw IDs; phase, pause, death, continuity, normalized-label and subtick checks. Local failed evidence rejects affected windows. |
| Future-command acceptance | 129 actual samples from trial 016, using complete packet bounds, scoped server-command support, eight images, and a wholly future normalization pair. Previous-action features and subtick trajectories are excluded. |
| Integrity / context | Hashes and source identities are checked; validation is recomputed before acceptance. Independent state/weapon audits reject ambiguous observations and untrusted parser warnings. Originals remain intact. |

Guides: [Validation](../VALIDATION.md), [Acceptance](../ACCEPTANCE.md),
[Windows rendering](../WINDOWS_RENDERING.md), [State context](../STATE_CONTEXT.md).

## Settings-protection baseline

- Output: `data/rendered/windows-settings-012/329360cba39babc8ea2d661c.mp4`.
- Independent settings/video proof: `data/rendered/windows-settings-012/settings-verification.json`.
- Native path/Cloud proof: `data/rendered/windows-settings-012/settings-isolation.json`.
- Original and post-state backup: that run's `settings-recovery.json`, `settings-backup/` and `settings-post/`.
- Historical cleanup: `data/settings-audits/historical-staging-2026-09-07.json` records all 17 archived staging folders.

The 39 selected personal files were unchanged during both trials 011 and 012;
only the private clone's machine/video settings differed. Trial 012's 64 native
pixel readbacks match all 64 archived images. Its DLL hash is
`0f9c1f0c4c4a6275960d48d2df8a9b1b5684040a490f9cd9ad484989d1c3823c`.
These checks concern settings isolation and capture integrity; the separate
training campaign below retains its original counts and timing limitations.

## Historical strict validation campaign

All three clips use Dust2 and the same native DLL/HUD profile.

| Capture | Player / canonical round | Requested ticks | Frames / commands | Native transitions |
| --- | --- | --- | --- | --- |
| windows-validation-008 | Ckanic / 3 | [6000,6320) | 160 / 320 | 1 fire, 60 camera, 135 movement |
| windows-validation-009 | Nikodeon / 4 | [12340,12660) | 160 / 320 | 130 camera, 159 movement, 7 ground, 2 crouch |
| windows-validation-010 | ay0k / 5 | [22300,22620) | 160 / 320 | 28 camera, 159 movement, 7 ground, 2 crouch |

Ground changes include landings. Transition counts describe observations, not certified future input targets.
Canonical checks also find five jump takeoffs plus one crouch onset in 009 and three plus one in 010.

Latest paths, relative to the inner repository:

- Corrected Dust2 source: `data/parsed/v2-state-fixed/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773/`.
- Independent context: `data/context/dust2-context-v2.json`.
- Captures: `data/rendered/windows-validation-{008,009,010}/`.
- Alignment/viewers: `data/datasets/dust2-validation-{008,009,010}-state-fixed/`.
- Validation: `data/validation/dust2-validation-{008,009,010}-state-fixed-v2/clip_validation.json`.
- Accepted/rejected manifests: `data/accepted/dust2-validation-{008,009,010}-state-fixed-v2/`.
- Revalidated campaign: `data/validation-campaigns/dust2-three-player-v1/campaign_manifest.json` (456 complete windows among 480 candidate positions).

The acceptance runs evaluate 480 candidate positions and accept zero. Every
candidate encounters unresolved execution/observation timing. Additional
findings include 108 windows touching missing button messages, 45 touching
invalid subtick fractions, and nine touching one native crouch disagreement.
Counts overlap; manifests identify the exact affected windows.

## Bugs found and fixed

Extractor **0.1.2**, retaining canonical schema 2, reads five CS2 pause flags
under `m_pGameRules.`, including `m_bGamePaused`. The previous prefix made
pause state unknown. Fresh Dust2 state contains 2,237,103 unpaused and 253,872
paused snapshots, with no unknown pause snapshots.

The pinned library magazine helper subtracts one from `m_iClip1`. The raw
demo and native replay show 20 Glock rounds while that helper returned 19.
Extraction now preserves the recorded firearm count, including zero; unavailable
and non-magazine values remain null. Corrected run-008 ammo agrees on all 160 frames.

Dust2 was re-extracted completely into a new directory. Commands, rounds and
events remain **byte-identical**; only state and metadata changed. Existing aim
normalization remains usable through its command-file hash. Nuke and Cache's
historical state artifacts have not yet been migrated.

## Limits of the historical fractional alignment

The isolated shot's network time exactly matches
`server_tick_executed - 1 + subtick.when` and falls inside frame 50's measured
future interval. A broader source audit supports this for many attack presses,
with exceptions. It does not prove every control's execution phase.

Whole commands currently join by integer execution-end tick. Their fractional
events can straddle a frame observation. Native command-number/tick fields are
unset in these replays. Pawn simulation state, interpolated camera state and
camera recoil must remain distinct; discrepancies are recorded without widening
tolerances to force acceptance.

Independent diagnostic audits at `data/validation/dust2-native-simulation-{008,009,010}/audit.json`
record exact pawn eye-angle agreement on all 480 frames at the tested simulation
coordinate. They retain position/crouch discrepancies and do not certify timing.

The accepted profile instead uses complete source-packet information bounds and
the scoped server command interval documented in
[SERVER_COMMAND_SUPPORT.md](../SERVER_COMMAND_SUPPORT.md). It predicts recorded
command values with an explicit delay. Exact original human sampling times,
complete subtick trajectories, all visual effect times, and original-client HUD
equivalence remain outside that claim. The observer name/weapon strip remains
visible in the current replay HUD. Broader player, weapon, map and build coverage
still needs additional captures and validation.

## Full-demo quality

| Map | Reconstructed commands | Missing-baseline payloads | Out-of-range fractions |
| --- | ---: | ---: | ---: |
| Dust2 | 1,416,312 | 1,074,675 | 1,756 |
| Nuke | 1,870,193 | 309,650 | 2,336 |
| Cache | 1,646,811 | 523,357 | 2,153 |

All full-demo quality reports retain their failures. Corrected Dust2 parsing
completed successfully; its nonzero quality-check exit does not mean parsing failed.

## Verification

Verification: **619 Python tests pass**, including source-packet decoding,
native counter/clock audits, future-target selection, acceptance recomputation,
raw-protobuf projection checks and the existing settings/worker recovery cases.
All extractor/renderer Go suites passed, including the five new `cs2-clocks`
tests. The native Release plugin built and passed the protected live trial 016.
Its DLL SHA256 is
`ad60f7eb5165aa2ee1a207cae1aea69e5176144f18435275f2eaec3e4aa9de8c`.
The public acceptance run and independent loader both reproduced 129 accepted
and 31 rejected candidates. Contract SHA256:
`7150acbeaf33d882f43d6d5a49902103b2418c8539558c193e6894e548c336fa`.
See [CLOCK_HOOK.md](../../tools/renderer/plugin-windows/CLOCK_HOOK.md) and
[SETTINGS_ISOLATION.md](../../tools/renderer/plugin-windows/SETTINGS_ISOLATION.md)
for exact binary requirements and settings evidence.

## Future work

A tensor/window loader, match-level splits, model, training loop and held-out
evaluation remain unimplemented. All three supplied maps belong to one match;
more matches are needed for independent evaluation. See [NEXT_STEPS.md](NEXT_STEPS.md).
Earlier results remain in [CHANGELOG.md](CHANGELOG.md). Large recordings,
outputs, dependencies and binaries are local ignored artifacts.
