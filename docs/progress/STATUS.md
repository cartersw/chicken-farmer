# Current implementation status

**Checkpoint: 2026-09-07 — local settings protection verified; Steam online testing remains.**

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
**No real samples are accepted for training yet:** execution-clock and observation-phase proof remains incomplete.

## What works

| Component | Current behavior and evidence |
| --- | --- |
| Canonical extraction | All three demos retain 4,933,316 reconstructed commands, full protobufs, identity/presence fields, state, rounds and events. Missing baselines and invalid fractions remain visible. |
| Windows rendering | The previous capture build renders 1280x720 at 32 FPS, retaining raw TGA, MP4, PTS, native readback hashes and the final capture endpoint. Those launches restored `gameinfo.gi`, not all personal preferences. |
| Settings protection | Trial 012, with Steam offline as subsequently reported by the user, verifies the native interface/path guard, unchanged bytes/mtime/read-only flags for 39 selected files, released locks and plugin cleanup. Online Cloud/reconnection behavior is unverified. |
| Competitive filtering / HUD | Phase sidecars identify 24 competitive and 2 setup rounds in each demo. Native HUD controls and settling remove earlier stale announcements without image masks. |
| Exact POV | Native observer handles resolve SteamID and reciprocal pawn/controller identity. Validation checks in-eye mode, view overrides and camera stability across pixel readback. All 480 latest frames pass. |
| Frame/action alignment | Each latest clip contains 160 frames and 320 commands with no empty intervals, normalized aim and an interactive inspector. Future interval assignments remain diagnostic. |
| Broader action checks | Captures cover firing, dynamic aim, movement, jump/landing and crouch transitions across Ckanic, Nikodeon and ay0k. |
| Sample acceptance | Configurable image history, previous actions and future intervals; exact raw IDs; phase, pause, death, continuity, normalized-label and subtick checks. Local failed evidence rejects affected windows. |
| Integrity / context | Hashes and source identities are checked; validation is recomputed before acceptance. Independent state/weapon audits reject ambiguous observations and untrusted parser warnings. Originals remain intact. |

Guides: [Validation](../VALIDATION.md), [Acceptance](../ACCEPTANCE.md),
[Windows rendering](../WINDOWS_RENDERING.md), [State context](../STATE_CONTEXT.md).

## Latest settings-protection trial

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

## Latest real campaign

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

## What remains unverified

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

## Full-demo quality

| Map | Reconstructed commands | Missing-baseline payloads | Out-of-range fractions |
| --- | ---: | ---: | ---: |
| Dust2 | 1,416,312 | 1,074,675 | 1,756 |
| Nuke | 1,870,193 | 309,650 | 2,336 |
| Cache | 1,646,811 | 523,357 | 2,153 |

All full-demo quality reports retain their failures. Corrected Dust2 parsing
completed successfully; its nonzero quality-check exit does not mean parsing failed.

## Verification

Verification: **226 Python tests pass**, including 28 settings-transaction and
35 worker-isolation/recovery cases. The preceding capture checkpoint also passed
all extractor/renderer Go suites; those components are unchanged by this task.
The new native Release plugin builds successfully and passed live trial 012.
Its hash, trial-011 crash diagnosis and exact binary requirements are recorded in
[SETTINGS_ISOLATION.md](../../tools/renderer/plugin-windows/SETTINGS_ISOLATION.md).

## Future work

A tensor/window loader, match-level splits, model, training loop and held-out
evaluation remain unimplemented. All three supplied maps belong to one match;
more matches are needed for independent evaluation. See [NEXT_STEPS.md](NEXT_STEPS.md).
Earlier results remain in [CHANGELOG.md](CHANGELOG.md). Large recordings,
outputs, dependencies and binaries are local ignored artifacts.
