# Current implementation status

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
