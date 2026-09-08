# Native replay HUD investigation

**Current result (2026-09-08):** the current-build private HUD archive removes
the spectator identity/weapon strip while preserving normal player HUD. All
480 original images across three Dust2/Nuke captures have a complete visual
review bound to their hashes. New batch contact sheets prepare every frame for
review; they do not grant approval automatically. The temporary mounts were
removed and installed HUD assets remain unchanged. See
[current evidence](progress/COMPETITIVE_EXPANSION.md) and the
[original current-build implementation](progress/COMPETITIVE_TRAINING_PILOT.md).

The investigation below records the earlier 14178 build and historical worker
profile; it is retained as source and implementation history.

This note records source evidence from the installed CS2 build. A command existing in the binary, or a matching stylesheet rule, does not establish its effect in an actual capture. Runtime capture results are recorded separately below. The investigation did not launch CS2 or change installed assets.

The historical worker profile is `windows-pilot-v5-native-player-hud`: native HUD settings plus six seconds of UI settling after seeking, with **no encoder masks**. Completed run 006 verifies this profile in a real five-second clip. Its first, shot-adjacent and last raw previews contain no stale announcement while preserving the player's money and HUD. Earlier masked clips remain historical artifacts.

## Installed source identity

Inspected on 2026-09-07 under:

```text
C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive/game/csgo/
```

`steam.inf` reports `PatchVersion=1.41.7.8`, `ClientVersion=2000899`, `SourceRevision=10948930`, and build date `Aug 28 2026 13:05:38`.

| Source, relative to the directory above | SHA256 |
| --- | --- |
| `bin/win64/client.dll` | `b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4` |
| `pak01_dir.vpk` | `4cb1f01a132b3ff86af9d831e58ce5436a0a876639f16383588a647723f24e32` |

The VPK v2 directory identifies the following compiled resources. These SHA256 values cover the individual resource bytes, not the entire numbered archive. Entries had no preload bytes. Archive offsets and lengths are decimal and specific to this installed build.

| Resource path inside VPK | Archive; offset; length | Resource SHA256 |
| --- | --- | --- |
| `panorama/styles/hud/hudteamcounter.vcss_c` | `pak01_392.vpk`; 94553920; 47433 | `c2a2d6206ed6716a0ffa9da3c6db0f747719a4b3a63f370f8fe05d08c799e5eb` |
| `panorama/styles/hud/hudteamcounter-equipmentinfo.vcss_c` | `pak01_392.vpk`; 94601360; 11463 | `2e7a7aac5c513497c9cb0e6ef8a55145e23c26ef7cc070cc2855531df92bf106` |
| `panorama/layout/hud/hudteamcounter.vxml_c` | `pak01_392.vpk`; 94547024; 6886 | `24fbcd290c5ec458df307c593c5c1915f8456a631416f9cce5d11c803661cde5` |
| `panorama/styles/hud/hudchat.vcss_c` | `pak01_394.vpk`; 68046688; 5733 | `d8785df43830d2e742452f5d00f907d574f407efc107490af8c053b741c2702a` |
| `panorama/styles/hud/hudalerts.vcss_c` | `pak01_394.vpk`; 68030944; 9568 | `9071d0b8c58d7ea6a6c01ae2b764b484a9eebef0b330d460d7900eaf28a8491f` |
| `panorama/layout/hud/hudalerts.vxml_c` | `pak01_394.vpk`; 68029296; 1648 | `a1158d10ccbede9f8aa1c9ebda6914e183e047142fcfa426cea5e1b3f7fa361e` |
| `panorama/styles/hud/hudwinpanel.vcss_c` | `pak01_373.vpk`; 1489888; 22717 | `c57b9220f4f7f07519231c0c58ebbcf3d39bb5704ac376ba2983f326366e5ba5` |
| `panorama/scripts/hud/hudwinpanel.vts_c` | `pak01_373.vpk`; 751344; 50046 | `2ee588443f2ffe065217a464f615f05175e98d5916d11211fd2a9efc0becff13` |
| `panorama/scripts/hud/hudteamcounter.vts_c` | `pak01_394.vpk`; 92725360; 5694 | `b6281aa7735d1f0aa99c83911f0bffe33ed487dc1918ee6b66ace4f7c6d32599` |

The compiled CSS and TypeScript resources contain readable CSS/JavaScript payloads. Inspection used direct reads of these owned game files; no resource override or patch was applied. A future game update requires new hashes and verification before relying on these offsets or UI rules.

## Command rationale before runtime verification

| Command | Installed-source evidence and intended effect | What remains unproven |
| --- | --- | --- |
| `hidehud 128` | Native `hidehud` help assigns bit 128 to chat. | Whether the current Panorama chat respects that bit during demo playback. |
| `hidehud 192` | Combines chat (128) and miscellaneous HUD (64). Health (8), crosshair (256), weapon selection (1), and hide-all (4) bits stay unset. | Which Panorama elements count as miscellaneous; own HUD preservation requires pixel inspection. |
| `cl_teamcounter_playercount_instead_of_avatars 1` | Variable exists in `client.dll`. In `hudteamcounter.vcss_c`, `.PlayerCountInsteadOfAvatars` sets `#TeamLargeCT` and `#TeamLargeT` opacity to zero and `.TeamLarge__PlayerCount` opacity to one. | Whether the spectator mode used for recording applies that class and hides the full equipment subtree. |
| `cl_show_equipment_value 0` | Variable exists in `client.dll`; its name concerns equipment value. | Which equipment/value panel it changes. No broad panel suppression is inferred from the variable name. |
| `cl_showtextmsg 0` | Its installed help describes enabling/disabling text messages printed on screen. | Whether demo chat or match announcements use that message path. |
| `spec_cameraman_ui 0` | Variable exists beside the caster UI control localization strings. | Whether it affects the current first-person observer UI, rather than a broadcaster camera's UI selection. |
| `cl_disable_round_end_report 1` | Variable exists. The teamcounter and winpanel scripts contain post-round damage/report handlers. | It does not establish control over the separate MATCH START alert. |

The full installed `hidehud` bitmask description identifies weapon selection=1, flashlight=2, all=4, health=8, player dead=16, needs suit=32, miscellaneous=64, chat=128, crosshair=256, vehicle crosshair=512, and in vehicle=1024. Treat this as a bitmask, not an enumeration.

The teamcounter CSS shows why the top observer equipment panel must be examined separately from `cl_spec_stats`: `.SHOW-EQUIPINFO` expands the health bar and reveals numeric health inside the avatar subtree. The player-count class hides the two large team containers themselves. This provides a specific source basis for trying the player-count setting; it does not prove that every spectator-only detail has been removed.

## MATCH START belongs to the native alerts panel

The installed `client.dll` contains these related identifiers: `CCSGO_HudMatchAlerts`, `csgo_hudalerts`, `file://{resources}/layout/hud/hudalerts.xml`, and `#CSGO_Notice_Alert_Match_Start`. The compiled alert stylesheet contains `.MatchStartAlert` and styles the `CSGOHudAlerts` element through `AlertHidden`, `AlertVisible`, `FlashAnim`, and `HideFlash` classes.

`csgo_hudalerts` is an internal HUD element name in this evidence. It has **not** been identified as a console command. The resource index contains native alert layout/CSS but no corresponding `panorama/scripts/hud/hudalerts` script. The visible alert state therefore appears to be controlled by the native HUD implementation; this is an inference from the inspected resources and identifiers, not a disassembly of the full control flow.

The stylesheet's ordinary show/hide transitions last fractions of a second, typically 0.25 seconds. A persistent MATCH START panel cannot be explained by a long CSS transition alone. Demo seeking may leave native announcement lifetime or state pending; that hypothesis needs a capture trial or native state inspection.

`hudwinpanel.vts_c` handles round results, MVP information, and post-round reports. Its event hooks include `HudWinPanel_ShowRoundEndReport`, `Player_Hurt`, and `Player_Death`. The `HudWinPanel_Hide` name found in the native binary relates to this other panel. Neither that event nor `cl_disable_round_end_report` is evidence of a supported MATCH START suppression command.

No dedicated, supported MATCH START-off console command was established by this inspection. `hidehud`'s miscellaneous bit was the strongest initial native lead; `cl_showtextmsg` had a weaker connection because this is a native HUD announcement rather than necessarily a general text message. The runtime trial below shows that this combination did not remove the notice. A direct Panorama class override or native panel manipulation would require a separate, version-specific implementation and validation.

## Runtime observations

The `windows-competitive-003` trial produced 160 frames at 1280×720 and 32 FPS. Its manifest records the command bundle above, including `hidehud 192`, `spec_cameraman_ui 0`, `cl_teamcounter_playercount_instead_of_avatars 1`, `cl_show_equipment_value 0`, and `cl_showtextmsg 0`, with 128 observer-settling ticks. The first and last raw-frame previews were independently inspected. These are observations of the combined profile; the trial does not isolate each command's contribution.

| Element | Observation in both inspected raw previews |
| --- | --- |
| Ten-player avatar, health, money, and equipment panels | Removed; top center shows only alive counts, timer, and score. |
| Chat | No chat is visible. |
| Radar | The circular radar remains with limited markers; no all-player spectator overlay is visible in the sampled frames. This does not establish exact original-client visibility for every tick. |
| Observed player's health, armor, ammunition, crosshair, viewmodel | Retained. The Glock ammunition counter changes from 20 to 19 between the samples. |
| Observer name/weapon strip at bottom center | Still present, displaying Ckanic and the observed weapon. |
| MATCH START notice | Still present near the lower center in both samples, despite the five-second interval and the native suppression commands. |

Evidence is under `data/rendered/windows-competitive-003/`:

| Artifact | SHA256 |
| --- | --- |
| `b88a620f26c43c80c33e33b6.render.json` | `7ba5f9fb9a9d4e15d21be129b6c2971815cd8d67b78888d6f4983b6a2fe58d55` |
| `raw-preview-01.png` | `5438f4c4f647d74817f84183a25ebec8b19ca4c53e38d425172d2e6bcf9ccc4b` |
| `raw-preview-03.png` | `9ea066fd2bfbda5049623cc08489f9c127ad5bc6d764da15b1c48c674ff0a1a4` |

The trial's MP4 applied `spectator-mask-v1`; its manifest says `raw_frames_masked=false`. The raw previews therefore establish that native settings removed the top private panels and chat. The masked MP4 alone could not establish this result. Those now-redundant masks are unnecessary for this verified command bundle/build; a different build or HUD profile requires a new check.

The `windows-competitive-004` trial ran `hud_reloadscheme` after seeking, 64 ticks before the requested capture start. MATCH START remained visible in its first raw preview. It is absent from the last raw preview inspected for this update, so the test did not establish suppression before recording; it should not be described as persistent through every frame of this particular run. The worker subsequently removed the reload command.

Run 004 produced another 160 frames, and all 160 archived TGA RGB payloads independently matched the native readback SHA256 records, with 160 distinct hashes and no repeated pixel frames. This establishes the recorded pixel correspondence, not fractional replay time, a measured final interval endpoint, or synchronization with command execution. Its manifest retains unverified timing and training status.

| Additional evidence under `data/rendered/windows-competitive-004/` | SHA256 |
| --- | --- |
| `8a0a3cc884649f6c8d847478.render.json` | `a4cfdd7792fc8a2e5289a0767ed5e7b240e2682171bd5fb7a8f9fc54e4a4bc64` |
| `capture_ledger.jsonl` | `a6caac490843e6156ec7f905b376304d0424ab134562bea3f466cdc5a3f67e38` |
| `raw-preview-01.png` | `0d00b376b306f3417a95501e902fca466b6fe3d63c9477508f52edc0411f0411` |
| `raw-preview-03.png` | `3bf911a499ccc6a11efb03fba80a2295a7671ea9483d0a726789989f3fa4145f` |

## Run 005: native settling clears the announcement

The worker added three `pause_playback` actions after the seek, at requested start minus 96, 64 and 32 demo ticks. Each existing pause/resume action waits two seconds, giving six additional seconds for the real-time HUD to settle before recording. These waits do not create training frames. The full sequence is used when the interval permits the 128-tick observer pre-roll; near the beginning of a recording the shortened sequence needs separate acceptance.

The setting also changed from `hidehud 192` to `hidehud 128`. The latter keeps the chat bit set while leaving the miscellaneous bit clear. In run 005, the independently inspected first and last raw previews have no MATCH START notice, no top private-stat panels and no visible chat. They retain the limited radar, alive count/timer/score, money, weapon selection, health/armor/ammunition, crosshair, and viewmodel. The observer name/weapon strip remains. This result supports allowing UI lifetime to settle rather than masking a stale announcement or disabling unrelated native HUD elements.

Evidence under `data/rendered/windows-competitive-005/`:

| Artifact | SHA256 |
| --- | --- |
| `267cffdd3187e4fb38b55755.render.json` | `875240c7a7b01dcd6959c4d1ad1ce8afa3be74d30568a058653de0c32a812558` |
| `raw-preview-01.png` | `908815f4edd5042fb080daf582e3d2430d9f142684b9f774a87956d967717735` |
| `raw-preview-04.png` | `9ed2f524b72b4aa4a6e1146c46a9879026936f9903c625bfd1234f2f1f6a10e9` |

Run 005 was recorded before the profile version changed: its manifest still says `windows-pilot-v4-player-hud` and its MP4 applies the old `round-announcement-mask-v1` rectangle. Its raw TGA files are unmasked and establish the native success. This historical video has not been overwritten or relabeled.

## Current native observation profile

The current worker profile is `windows-pilot-v5-native-player-hud`. It uses the successful native command bundle, `hidehud 128`, and the six-second pause sequence, and removes **all encoder masking code**. No additional black rectangle or inference-time mask is required by this profile. Run 006 completed with `encoded_mask_profile=none`, an empty `encoded_masks` list, and `raw_frames_masked=false`.

The four run-006 previews correspond to frames 0, 50, 51 and 159. They were independently inspected: no MATCH START notice, private team panels or chat; money, radar, alive count/timer/score, weapon selection and own health/ammo/crosshair/viewmodel remain. Frame 50 shows 20 rounds in the Glock, and frame 51 shows a muzzle flash and 19 rounds. The observer name/weapon strip remains and is not an original-client HUD equivalence claim.

Evidence under `data/rendered/windows-competitive-006/`:

| Artifact | SHA256 |
| --- | --- |
| `dd3ea5022ae36523398b97ca.mp4` | `9d5f1ed08258fc97a101d733cd8500a3e79b292ceb4fdf576aca4c5ada698046` |
| `dd3ea5022ae36523398b97ca.render.json` | `7731e3c0cfdb3f3dd25cd997779a9a9c7b11a74b0bc999009c27db4c4a12d4ee` |
| `capture_ledger.jsonl` | `25bdab7d8bc04379078645a2695577cec22a89e91d96f78f2892edb56c9c5ddf` |
| `raw-preview-01.png` | `f63afea0d3403b5943a54fd128c38de5d6b81774a913f84f085718824ca9ee7f` |
| `raw-preview-02.png` | `30b509e45b9bf50a20179a184c7b7d64f543675d96d878c85e0c0751179652d1` |
| `raw-preview-03.png` | `d43c9f2719d25a3015095c4597c9b318049a48cdd5d6a3ff64a693ef61da0b63` |
| `raw-preview-04.png` | `62df0147c6f389868ac2a1fd40ffcfe38385545ef5bd39071cabc7d054ed12b9` |

The run used DLL SHA256 `4b52bd78efe574d837d155ff91921ca57d0b284bfd497457b3b01293737b18c1`, captured 160 frames, exited normally and restored the original gameinfo hash. Its completed dataset and viewer are under `data/datasets/dust2-competitive-006/`. Native pixel correspondence, render-time intervals and the final endpoint are recorded. The execution-clock epoch, broader POV/visual acceptance and subtick eligibility retain explicit limitations; the pilot works without declaring the whole output training-ready.

Earlier `spectator-mask-v1` and `round-announcement-mask-v1` outputs remain historical diagnostics. The latter covered `(534,494,212,38)` in the 1280x720 layout, including scene pixels where a notice was absent. Those transformations should not be confused with the current native profile or silently mixed into an unmasked dataset.

The visual checks cover the installed 1280x720 layout with `hud_scaling=1`. Recheck after game, resolution or HUD configuration changes. Native spectator rendering still differs from an original client recording, and HUD cleanup alone does not prove timing or training readiness. Preserve raw captures and the exact profile/commands per run.

The earlier captures above restored only `gameinfo.gi` and could persist console
settings. The current [protected worker](WINDOWS_RENDERING.md#keeping-normal-play-separate)
clones preferences, enforces the native path/Cloud guard, verifies selected
personal-file restoration and removes its staged plugin after exit. Protected
clock trial 016 preserved all 39 selected files. Its sampled frames show the
cleaned native HUD, with the observer name/weapon strip still visible; see
`data/rendered/windows-timing-016/visual-review.json`. Online Cloud testing is
deferred. The accepted future-command profile does not claim original-client
HUD equivalence.

## Spectator strip override candidates, 2026-09-08

The installed 1.41.8.0 assets retain the directory SHA above. The bottom strip's
separate `.HudSpecplayer__Bg` rule is in
`panorama/styles/hud/hudhealthammocenter.vcss_c`, in `pak01_463.vpk` at byte
73,694,528, length 60,245. Its original SHA256 is
`080e80a8559ff5e66c2bed167cf4529765094dd455b1f4d4c30d1f4aa233463d`.
No dedicated strip-off console command was established by the current native
string inspection.

The first [`hud_override.py`](../tools/renderer/hud_override.py) candidate was
`spectator-strip-private-css-v1`. Before the protected replay search path was
activated, it stages a modified compiled stylesheet only under that run's
`csgo/chicken-render-<run-id>/panorama/styles/hud/` directory. The outer worker
owns the process and gameinfo/settings leases and archives this directory after
exit. The helper rejects another source hash, another VPK entry, an unowned
destination, reparse points, and an existing destination. It never edits an
installed archive or normal configuration.

The change replaces only the strip panel's 25-byte `world-blur: hudWorldBlur;`
declaration at file offset 26,562 with `opacity: 0;` and padding. The panel's
existing blur is thereby removed with its opacity. Other CSS rules and compiled
block offsets remain byte-identical. The DATA checksum field at offset 1,872 is
updated. Reversing these two edits must recover every original byte.

The original stored DATA CRC is `54d85505`, while its minified text has CRC
`a937624b`. This known source mismatch is verified explicitly. The primary
[ValveResourceFormat Panorama reader](https://github.com/ValveResourceFormat/ValveResourceFormat/blob/master/ValveResourceFormat/Resource/ResourceTypes/Panorama.cs)
documents that minified styles containing a `SrMa` source-map block can have
this mismatch. The candidate instead stores its recomputed text CRC
`5ec18bc8`. Its full compiled SHA256 is
`9d49df1dad91771953a22767cfcdfa7d3392742179be43719e3849dc8b8a47a5`.

The actual installed source passed the pure resource transformation and
round-trip checks; 23 fixture tests cover resource checksums, malformed blocks,
ambiguous selectors, unchanged unrelated CSS, isolated staging and refusal to
overwrite. The subsequent protected `windows-competitive-current-001` render
failed the visual goal: all four inspected raw-frame previews still showed the
name, avatar and weapon strip. Its staged file matched the expected hash, and
its process/plugin logs contained no stylesheet or resource error. Staging
therefore did not establish that Panorama consumed or honored the override.
This historical output remains unchanged.

Candidate `spectator-strip-private-css-v2` retained the pinned source and added
three narrow edits: zero opacity for the separate spectator avatar, and
`visibility:collapse;` in both rules that previously made spectator panels
visible. The four declaration regions remain exactly the original lengths;
undoing these changes and the DATA CRC restores the entire original resource.
Own health/ammo rules and every other resource byte remain unchanged.

The installed compiled layout, `hudhealthammocenter.vxml_c` (SHA256
`a5877c66b6fb9a2dac852f89ec854be0b4dc8527a1f04f8978e247cd8c5a34cb`),
was independently decoded using the layout of the primary
[ValveResourceFormat binary KV3 reader](https://github.com/ValveResourceFormat/ValveResourceFormat/blob/master/ValveResourceFormat/Resource/ResourceTypes/BinaryKV3.cs).
All type, object-length and value streams were consumed. The resulting layout
confirms that `jsHudSpecplayer__Bg` contains the name, team logo and weapon name,
while the avatar is a separate panel. This establishes the selector scope; it
does not establish runtime style precedence.

That helper also created a one-entry inline VPK v2 archive at the unique
mod's `pak01_dir.vpk`, containing the identical modified stylesheet. The loose
file remains for direct inspection. The archive tree, file range and entry CRC
must round-trip to the exact patched bytes; it contains no unrelated assets.
Both files are archived with `renderer-sandbox` and removed by the existing
mod lease after CS2 exits. No normal archive or resource is replaced.

| Candidate v2 evidence | Value |
| --- | --- |
| Modified compiled stylesheet SHA256 | `3c29e34ae294676cb9d9e7dd8c6ec675484db72171020d9dfe60d3abaa110721` |
| Modified DATA CRC32 | `e249fadd` |
| Private VPK SHA256 | `7ff5ab5f9bb367c46dfb82186103698d229100e326b31860cb8fdfea35e4cca4` |
| Private VPK size | 60,341 bytes |
| Fixture checks | 34 helper tests; 48 including the existing worker tests |

The protected `windows-competitive-current-002` attempt exited with code 1
before the plugin loaded. Its minidump identifies a filesystem fatal error
reporting private `pak01.vpk` as unexpected. The initial inference that its
inline payload caused a failed file open was incorrect: candidate v3 below
failed at the same manifest identity check. Separately, v2 omitted the
48-byte internal MD5 footer emitted by the primary
[ValvePak v2 writer](https://github.com/ValveResourceFormat/ValvePak/blob/master/ValvePak/ValvePak/Package.Save.cs).
The staged package remains archived as failed evidence; it produced no frames.

Candidate `spectator-strip-private-css-v3` used the same four CSS
changes with the standard external-chunk layout: a 172-byte `pak01_dir.vpk`
referenced a 60,245-byte `pak01_000.vpk`. Its 28-byte chunk MD5 section and
48-byte tree/section/whole-file MD5
footer are independently recomputed during staging. Directory SHA256 is
`22042d692b0ed6e2e7210cd18a1e914af4aab05bb0e8d9e45140f1e8dd4f8ec7`;
the chunk's SHA is the unchanged candidate-v2 stylesheet hash above. The helper
refuses any existing loose file, directory archive or chunk before it writes.
All three files remain inside the owned mod lease. There are 36 helper tests,
or 50 including the worker tests.

The `windows-competitive-current-003` attempt also exited before plugin load.
An independent byte inspection confirmed its header, one-entry tree, resource
CRC and every MD5 checksum matched the standard writer layout. Disassembly of
the pinned `filesystem_stdio.dll` established the actual failure: RVA `51D50`
looks up an archive basename in a manifest table. The known `pak01` name has
no expected entry for the private mod directory; with a non-null loaded archive,
the branch at `51F14` reaches the `unexpected` error at `51F71`. The dump's
`51F80` return address confirms that branch. The displayed `pak01.vpk` name was
not evidence of a failed file-open syscall.

The current candidate, `spectator-strip-private-css-v4`, retains the independently
verified v3 bytes and uses a custom basename: `pakchicken_hud_dir.vpk` and
`pakchicken_hud_000.vpk`.
The loader's absent-name branch permits custom names that do not match its
reserved `pak` plus two digits form (`51DDF` through `51F54`). No manifest,
signature check or installed archive is changed. The profile identifier changes
with the delivery policy so this attempt receives a distinct renderer identity.

The protected `windows-competitive-current-004` run completed 160 frames, but
the strip remained visible. Its native `competitive_resource_paths` record
showed the custom archive absent from both `GAME` and `MOD` search paths. The
stylesheet resolved to the official `csgo/pak01.vpk` archive. Automatic discovery
therefore did not mount this custom basename; merely staging the archive did
not establish resource delivery.

The protected `windows-competitive-current-005` attempt explicitly mounted
`pakchicken_hud_dir.vpk` under both `Game` and `Mod`. Its retained minidump
confirms that both search paths contained the private archive, but startup
failed when its resource read derived the nonexistent chunk filename
`pakchicken_hud_dir_000.vpk`. It produced no frames. This is a path-normalization
error distinct from the reserved-name failures in 002 and 003.

The corrected delivery policy explicitly mounts the private archive under both
`Game` and `Mod` before startup, through the worker's journaled gameinfo lease.
The logical path is `csgo/chicken-render-<run-id>/pakchicken_hud.vpk`;
the files on disk retain their `_dir.vpk` and `_000.vpk` names. In the pinned
filesystem DLL (SHA256
`a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef`),
`AddSearchPath` at `53770` reaches the `.vpk` extension check at `5F78B`.
The packed-store constructor calls imported `CBufferString::StripExtension`
at `122EAA`, appends `_dir.vpk` at `122EE0`–`122F20`, then opens that directory
archive at `122F56`. The logical base name therefore selects our actual
directory archive without relying on automatic discovery. This mount policy
has its own renderer-profile identity; the staged resource bytes remain the
same. The first `Game` entry remains the real owned directory for plugin and
default-write resolution; the explicit archive still precedes official asset
archives in the resource search order.

The protected `windows-competitive-current-006` run succeeded with this
`competitive-hud-archive-search-v2` policy: 160 original 1280×720 TGA frames at
32 FPS for Dust2/Ckanic, requested demo ticks 6000–6320. Both native `GAME` and
`MOD` diagnostics resolve the stylesheet to the owned `pakchicken_hud.vpk`.
The private directory archive and stylesheet/chunk match the hashes above.
The rendered clip ID is `5bd48c3f844705d469748840`, with renderer-profile SHA256
`1c49aa8a85a8e692247ba3ab22931e53813a88d945655fb8efcaf6d37e47a6ee`.

An independent review of all 40 frames numbered 40–79, using
`data/validation/dust2-hud-006-v1/contact-06.png` through `contact-10.png`,
confirms the spectator name/avatar/weapon strip is absent throughout this
partition. Player health/armor, money, ammo, radar, crosshair, round timer/team
counter and viewmodel remain. The shot, ammunition change from 20 to 19,
muzzle flash and ejected casing are visible without scene corruption. The
right-side player weapon inventory label remains intentionally. The full
160-frame review is recorded separately with the pilot's validation artifacts.
The current helper plus worker unit tests pass all 55 checks.

This result is scoped to this pilot and the pinned build, stylesheet and HUD
configuration. Native filesystem resolution alone does not prove Panorama's
applied style; the image review supplies that separate evidence. The staging
helper and immutable render manifest retain their pre-review resource-load and
rendered-HUD flags as false. No encoder mask is used, and HUD cleanup alone
grants no timing or training approval. Recheck other maps, player states,
resolutions or asset versions before extending the visual claim.
