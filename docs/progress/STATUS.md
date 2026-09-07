# Current implementation status

**Checkpoint: 2026-09-07**

The working Dust2 pilot produces **160 video frames and 320 aligned future commands**, with no empty frame intervals, under `data/datasets/dust2-competitive-006/`. Native readback hashes match every raw image; timing uses actual render-time values and a recorded final endpoint. The unmasked v5 HUD profile and the shot transition were visually checked. Phase audits exclude setup rounds in all three supplied demos. The capture/alignment/viewer path is built and tested; broader execution-epoch, POV and subtick-quality acceptance remains scoped and explicit, so the dataset retains `training_ready=false`.

## Component inventory

| Component | Built so far | Verification and limits | Location |
| --- | --- | --- | --- |
| Repository and environment | Go and Python project structure, package/CLI entry points, build helper, local toolchain/virtual environment, ignored large artifacts | Local extractor and Python commands run on Windows | [Build helper](../../scripts/build.ps1), [Python package](../../pyproject.toml), [configuration](../../configs/parser/esl.json) |
| Demo ingestion | File or recursive directory input, SHA256 identity, shared match identity, source metadata and output versioning | All three supplied recordings processed without modification; existing canonical output paths are refused | [Extractor CLI](../../tools/usercmd-extractor/cmd/cs2-extract/main.go) |
| Full UserCmd extraction | Explicit full-parser mode; player identity, command/client/server/demo clocks, raw mouse, angles, movement, masks, weapon selection, subticks, input history and complete reconstructed protobuf | Retains every reconstructed event; cannot reconstruct missing initial baselines | [Extraction](../../tools/usercmd-extractor/internal/demo/extract.go) |
| Canonical storage | Streamed compressed Parquet for commands, available player-state snapshots, rounds and gameplay events; manifest/checksums; failure staging | Real output files written and their integrity checked; snapshots are not invented between available observations | [Schema](../../tools/usercmd-extractor/internal/schema/schema.go), [schema guide](../DATA_SCHEMA.md) |
| Input-presence handling | Nullable raw fields plus explicit base/button/view-angle message presence | Preserves absent versus explicit-zero encoding; separate normalized effective values apply protobuf defaults only when the parent is known present | [Normalizer](../../src/cs2_data/normalize.py) |
| Quality validator | Streaming schema/hash/count/protobuf checks, coverage/rejection accounting, identity/activity/continuity metrics and subtick diagnostics | Works on all three real outputs; correctly reports quality failures rather than accepting them as training-ready | [Validator](../../tools/usercmd-extractor/internal/validate/validate.go) |
| Aim normalization | Wrapped yaw differences, pitch differences, reset/death/gap masks, effective mouse counts and bounded mouse-scale diagnostics | Ran on all three demos; valid angle transitions are not yet validated visual training samples | [Normalizer](../../src/cs2_data/normalize.py) |
| Render-job planner and phase audit | Alive intervals, canonical coverage, paired tick trimming and hash-bound phase sidecars; live game rules and retained scores required by default | All three demos classify as 24 competitive and 2 setup rounds. The selected competitive Dust2 job has 320 commands for 320 ticks; pause availability remains uncertain | [Job planner](../../src/cs2_data/jobs.py), [phase guide](../PHASES.md) |
| Reka integration | Pinned source, setup/build tool, environment doctor, one-job adapter and narrow capture/profile/cleanup patches | Go adapter builds and passes dry run; real capture uses the maintained Windows adaptation below | [Renderer tooling](../../tools/renderer), [rendering guide](../RENDERING.md) |
| Native Windows worker | x64 plugin; bounded launcher; isolated staging/recovery; TGA archive and H.264/PTS validation; native movie/readback ledger with render time, final endpoint and native replay epoch | Competitive run 006 has 160 archived images matching native readback hashes. Native replay epoch is kept separate from command execution calibration | [Windows guide](../WINDOWS_RENDERING.md), [worker](../../tools/renderer/windows.py), [plugin](../../tools/renderer/plugin-windows/README.md) |
| Replay HUD profile | Native team-count/chat/radar controls, `hidehud 128`, and six seconds of UI settling; v5 worker has no encoding masks | Run-006 first/shot-adjacent/last raw previews retain money/health/ammo/crosshair/viewmodel and contain no stale announcement; encoded manifest confirms no masks | [HUD evidence](../HUD_PROFILE.md) |
| Timing and execution alignment | Observed `EventClientOutput_t.m_flRenderTime` intervals, recorded endpoint, pixel hashes, native replay epoch, and future-action `(start,end]` joins | Real run 006 produces 160 frames / 320 commands / 0 empty frames, with normalized labels. Execution epoch and training eligibility remain scoped assumptions | [Timing](../../src/cs2_data/timing.py), [calibration](../../src/cs2_data/calibration.py), [alignment guide](../ALIGNMENT.md) |
| Debug viewer | Standalone HTML with video/frame stepping, observed timing and raw/normalized inputs | Real run-006 viewer and aligned data are produced; raw frames 50/51 verify the selected shot transition. Broader POV/action acceptance remains separate | [Viewer](../../src/cs2_data/viewer.py) |
| Artifact integrity | Source checksums and identity checks, incomplete-stage rejection, exclusive output locks and staged publication for derived tables | Covered by automated checks; canonical data remains separate from derived labels | [Shared I/O](../../src/cs2_data/io.py) |

## Results from the supplied demos

These figures were checked against the existing audited manifests, validation reports and normalization reports when this tracker was created.

| Map | Reconstructed commands | Reconstruction coverage | Missing-baseline payloads | Out-of-range subtick values | Valid alive angular transitions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dust2 | 1,416,312 | 56.86% | 1,074,675 | 1,756 | 1,030,560 |
| Nuke | 1,870,193 | 85.79% | 309,650 | 2,336 | 1,296,544 |
| Cache | 1,646,811 | 75.88% | 523,357 | 2,153 | 1,184,546 |
| **Total** | **4,933,316** | — | **1,907,682** | **6,245** | **3,511,650** |

Coverage means reconstructed commands divided by eligible recorded command payloads. Every eligible payload was reconciled as reconstructed or explicitly rejected; there were zero unaccounted payloads. All files reached the end of parsing, but **all three full-demo quality reports have `passed=false`**. This is distinct from the automated code tests passing.

Available player-state rows total 6,840,962. Each recording yielded 26 round-start events and 12 human Steam identities over the recording. Independent phase audits now identify **24 competitive rounds and 2 setup rounds per map**. The identity count spans the session, rather than 12 simultaneous competitors. Phase acceptance does not waive input-quality or timing requirements.

## What currently prevents a training-ready dataset

### Latest real dataset and native HUD evidence

Current job: `data/jobs/dust2-competitive-shot-001.json`, Ckanic (slot 9), canonical round 3 / competitive round 1, requested ticks `[6000,6320)`. The interval has all 320 command ticks and includes one isolated Glock-18 fire event at tick 6102, raw attack-button observations at 6102-6107, substantial aim change, and lateral movement. The `.selection.json` companion records these observations and source hashes.

Run `windows-competitive-006` captured 160 frames / 5 seconds at 1280x720 and 32 FPS. Its timing/alignment/viewer pipeline completed under `data/datasets/dust2-competitive-006/`: 160 frame rows, 320 command rows, zero empty frames, and two future commands per frame, with normalized labels included. Timing uses actual `EventClientOutput_t.m_flRenderTime` values, not a fraction invented from nominal FPS. The action interval convention is `(start,end]`.

Three two-second `pause_playback` actions after seeking give the UI six seconds to settle. With `hidehud 128`, independently inspected run-006 raw previews at frames 0, 50, 51 and 159 contain no MATCH START notice and retain money/weapon selection as well as health/ammo/crosshair/viewmodel. Extra team panels and chat remain removed. The confirmed `windows-pilot-v5-native-player-hud` manifest records no encoding masks. Run 005's existing v4 MP4 remains a historical masked artifact. See [HUD source evidence and trials](../HUD_PROFILE.md).

Run 006's 160 raw images all match native pixel-readback hashes, with 160 distinct images and no repeats. Its observed render-time interval is 260.9952697753906 through the native final endpoint at 265.9952697753906 seconds; integer replay cursor values span 6002 through that endpoint's 6322. The native `IDemoFile.GetDemoStartTick` value is **-5546**. This is a replay/client epoch value, not the extracted execution-versus-packet difference of **10703**, and is deliberately not substituted for it.

The isolated shot provides a concrete pilot check: frame 50 still has 20 rounds and its future targets include the attack at demo tick 6102; frame 51 shows the muzzle flash and 19 rounds. The mapping therefore produces the intended before-action association for this observed transition. This does not establish universal clock equivalence, original-client POV fidelity or all subtick semantics. The pipeline retains `timing_status=observed_movie_submission`, `diagnostic_alignment=true`, `pov_verified=false`, and `training_ready=false`; the execution calibration remains a candidate with no independent execution anchors. One invalid subtick observation at demo tick 6007 remains preserved for explicit filtering/acceptance.

Latest completed dataset: `data/datasets/dust2-competitive-006/pipeline_manifest.json`; viewer: `viewer/inspect.html` within that directory. Video: `data/rendered/windows-competitive-006/dd3ea5022ae36523398b97ca.mp4`, SHA256 `9d5f1ed08258fc97a101d733cd8500a3e79b292ceb4fdf576aca4c5ada698046`. Ledger SHA256: `25bdab7d8bc04379078645a2695577cec22a89e91d96f78f2892edb56c9c5ddf`. DLL SHA256: `4b52bd78efe574d837d155ff91921ca57d0b284bfd497457b3b01293737b18c1`. CS2 exited with code 0 and restored the original gameinfo hash.

Earlier competitive runs 003/004 established native removal of extra panels/chat and the first 160/160 native pixel-hash match. MATCH START persisted in 003; `hud_reloadscheme` in 004 did not clear it before recording. Their broader/partial masks are historical diagnostics, superseded by the native settling profile.

### Earlier Windows capture history

The installed game is native Windows CS2 patch `1.41.7.8`. A native Windows pilot worker now exists alongside the original Linux Go adapter. The Windows path uses an x64 MSVC plugin and software H.264; it requires neither Linux nor VAAPI. CMake 4.2.3, the missing Visual Studio C++ components/Windows SDK, and checksum-verified FFmpeg 9.0.1 tools have been installed locally. Software encoding passed; this FFmpeg's NVENC path requires a newer driver than the installed one.

The upstream plugin targets `1.41.6.5`, so current-game ABI compatibility is being established through bounded real attempts. Initial trials found and diagnosed an obsolete `ICvar` layout. The Windows adaptation backports the relevant upstream SDK correction and records startup stages. It also includes a bounded client-hook fallback and the corrected shutdown callback signature. The original Linux adapter retains its platform guard.

Successful pilots now contain raw TGA frames, H.264 MP4, decoded video PTS, hashes and logs. Runs `windows-pilot-004` and `005` each captured **64 frames / 2 seconds**, requesting ticks `[1279,1407)`. Runs `windows-pilot-006` and `007` each captured **160 frames / 5 seconds**, requesting `[1279,1599)`. All use **32 fps at 1280x720** and the same player, ay0k, slot 3. The separate `render32-v1.json` profile describes 640x360 and is not applied automatically.

CS2 exited with code 0 after each successful run. All completed attempts, including the initial failures, restored the original `gameinfo.gi` SHA256 `b1391e73dbec2e078bdbaf7279c2b955084cf2b38a47e3d8181662e7948679b8`. The DLL hash for these earlier pilots was `b5b889759d5f147ed309eaa42a8be3ad2a2f352d6f6b574fa338ca7d13fb4afe`; later instrumentation DLL hashes are recorded in their own run manifests.

First/middle/last images from pilot 004 visibly show ay0k, a knife and 100 HP, matching the selected identity/state. They also contain a white spectator/HUD panel already present in the raw TGA; this needs visual-profile cleanup. These first-round images show knife play, so this is a rendering diagnostic, not an accepted competitive training window. Manifests retain `pov_verified=false`, `interval_verified=false`, `timing_status=unverified` and `training_ready=false`. Repeated frame counts match; video hashes differ, and deterministic pixels or measured timing repeatability have not been established.

Pilot 007 also has a screenshot of the actual foreground CS2 window, `window-color-reference.png`, and decoded preview images at frames 0, 80 and 159. The live window and decoded video agree qualitatively on color and orientation; no red/blue swap is appropriate for these Windows captures. The white HUD panel is transient and is absent in the middle/last previews. Historical pilot-007 clip: `data/rendered/windows-pilot-007/65d673da8b15e089b40700b9.mp4`; its video SHA256 is `f5bd800eb15908fa88d32697036fafbf1c82c8e35050bf74e6a68b8289c0c810`.

Initial attempts are retained as diagnostic history: 001/002 crashed at an obsolete CVar interface slot; 003 loaded the plugin but timed out at the menu because command/demo interface slots had moved. The current adaptation applies evidence-backed SDK/interface corrections; see [the plugin notes](../../tools/renderer/plugin-windows/README.md).

### Some inputs lack an initial baseline

Delta commands need a previous complete state. Dust2's missing payloads are explained by each player's initial unavailable prefix; decoded demo ticks are contiguous after that player's first full baseline. Later covered intervals can still be used for diagnosis and future rendering. The same detailed prefix conclusion has not been established for every player in the other maps.

The planner excludes intervals without sufficient canonical command coverage. It does not invent initial inputs. Use `dust2-competitive-shot-001.json` for the current competitive diagnostic. The earlier `dust2-audited-first.json` has complete commands but selects knife/setup play and is retained only for capture history. The still older `dust2-first.json` selected an interval without commands and is obsolete.

### Negative subtick timestamps need interpretation

Dust2 contains negative `subtick_moves.when` values from `-1.8125` through `-0.0078125`, including during live play. They are not one uniform sentinel. They are retained and flagged; no evidence yet justifies treating them as normal in-tick times or clamping them. Nuke and Cache also fail the fraction checks.

### Pilot clock evidence and remaining execution acceptance

The maintained Windows plugin associates native movie submissions/readbacks with archived pixels, verified by hashes through run 006. Actual render-time values and the native final endpoint complete the real interval table. Run 006 also records the native replay epoch without treating it as an execution calibration. Integer replay-cursor ticks remain separate from the fractional rendered-state observation clock.

Dust2 has an observed command-clock offset of `server_tick_executed - demo_tick = 10,703`; its relationship to rendered pixels still needs validation. Calibration code now separates a candidate inferred from packet arrival from independently measured execution anchors, and diagnostic alignment retains that distinction. Both normalization and alignment deliberately remain `training_ready=false`. Real execution/capture calibration and per-clip acceptance still need evidence; editing a manifest flag will not complete this work.

### Other label-quality work remains

- Pause state is unavailable through the presently verified properties, and jobs flag that uncertainty.
- Nuke has 25,278 masked angular transitions involving unavailable view-angle messages.
- Button meanings currently come from pinned library constants; they have not been validated against visible replay actions.
- Mouse-scale estimates are diagnostics, not recovered player DPI or guaranteed sensitivity.
- Per-clip acceptance rules must combine command coverage, state filters, input quality, POV and verified execution/capture timing.
- Dedicated diagnostic plots and a recorded acceptance check that integrates angle deltas back to the source view trajectory have not been implemented/established.

## Verification recorded so far

| Check | Result | What it proves |
| --- | --- | --- |
| Final Python suite | **92 passed** | Dataset/phase/calibration/timing checks, native worker recovery and staged-DLL consistency, actual FFmpeg image/PTS checks and real pipeline contracts |
| Final Go suites | **All passed** | Extractor/validator preservation and quality checks, phase failed-parse/immutable-output checks, and renderer job/schedule checks |
| Actual audited Dust2 job dry run | Passed | Adapter accepts the real demo/job and schedules an interval with recorded commands |
| Full-demo data-quality checks | **Failed for all three maps** | Identifies the known baseline and subtick issues; prevents treating parse success as data acceptance |
| Native Windows replay capture | **Passed for short Dust2 pilots** | Actual TGA/MP4 output, expected frame counts, decoded PTS/dimensions, normal process exit and byte-exact gameinfo restoration |
| Native pixel/archive correspondence | **160/160 hashes match in competitive run 006** | Every archived TGA image matches its native readback; execution epoch and observation semantics retain explicit scope |
| Real render-to-dataset pilot | **160 frames / 320 commands / 0 empty frames in run 006** | Observed render-time intervals, endpoint, future-action join, normalized labels and viewer export execute on actual artifacts |
| Isolated-shot visual check | **Frame 50 before attack; frame 51 muzzle flash and ammo decrease** | Supports the intended future-target association for this selected transition |
| Broad epoch/POV/subtick acceptance | **Still required** | The working pilot retains `training_ready=false`; one visible action does not establish all training requirements |

The final 92-test Python run and all Go suites passed after the run-006 implementation. Final review also added a staged-DLL hash check that rejects a concurrent rebuild before activating the game configuration. The retained run-006 DLL independently matches its manifest. Historical checkpoint counts remain in the changelog. Phase extraction and derived datasets do not modify canonical schema or data.

## Versions and artifact locations

| Item | Recorded version or path, relative to repository root |
| --- | --- |
| Demo parser | `demoinfocs-golang/v6 v6.0.0-alpha.0` |
| Extractor / canonical schema | `0.1.1` / schema `2` |
| Python package / normalizer | `0.1.0` / normalizer `2`; PyArrow `21.0.0` |
| Reka source commit | `02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22` |
| Local toolchain | `.tools/go/bin/go.exe` (Go 1.27.1), `.venv/` (Python 3.10.11) |
| Entry points | `bin/cs2-extract.exe`, `.venv/Scripts/cs2-data.exe`, `tools/renderer/build/dem-render.exe` |
| Native Windows entry/plugin | `tools/renderer/windows.py`, `tools/renderer/build/plugin-windows/Release/server.dll` |
| Windows tools | CMake 4.2.3, MSVC 19.51, Windows SDK 10.0.26100; FFmpeg under `.tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/` |
| Latest completed competitive capture | `data/rendered/windows-competitive-006/`; native frame ledger, archive inventory, raw TGA, MP4/PTS, manifest, logs and recovery journal |
| Latest real diagnostic dataset | `data/datasets/dust2-competitive-006/`; timing, calibration, aligned Parquet, viewer and pipeline manifest |
| Earlier Windows attempts | `data/rendered/windows-pilot-001/` through `007/`, and earlier `windows-competitive-*` trials; retain for history |
| Audited canonical data | `data/parsed/v2-audited/<demo SHA256>/` |
| Canonical files per demo | `usercmd.parquet`, `player_state.parquet`, `rounds.parquet`, `events.parquet`, `manifest.json`, `validation.json` |
| Normalized diagnostics | `data/normalized/v2/<demo SHA256>/normalized_actions.parquet` and `normalization_report.json` |
| Competitive phase evidence | `data/phases/{dust2,nuke,cache}-phase-audit.json`; producer `tools/usercmd-extractor/cmd/cs2-phases` |
| Current covered competitive job | `data/jobs/dust2-competitive-shot-001.json` and `.selection.json` companion |
| Current native HUD | `windows-pilot-v5-native-player-hud`, six-second UI settling, no masks; [HUD guide](../HUD_PROFILE.md) |
| Obsolete/development diagnostics | `data/parsed/v1/`, `data/parsed/v2/`, `data/diagnostics/`, and older render jobs; retain for history, do not use as current inputs |
| Complete demo hashes and input names | [Initial run artifact inventory](../INITIAL_RUN.md#local-artifacts) |

Large recordings, generated outputs, dependencies and local executables are ignored by Git. The documentation records their local locations; cloning source alone will not reproduce those files. All three maps use `match_id=esl-s52-eu-cup6-misa-mouz-nxt` for a future split that keeps this series together.

## Remaining acceptance and future implementation

- Independently accepted observation-clock semantics and execution epoch; actual fractional render-time values, final endpoint and native pixel correspondence now exist.
- Broad command execution/POV/subtick acceptance beyond the working isolated-shot pilot; the real pipeline, aligned data and viewer are built and tested.
- A training-window loader, deterministic match-level split builder, dataset packaging/sharding and training statistics pipeline.
- Verified semantic button decoding and a final fixed-horizon action-tensor builder; currently buttons remain integer masks and command targets remain records.
- A trained behavioral-cloning model, offline evaluation and trivial-baseline comparisons.
- Runtime screen capture, policy inference, action-controller integration and local/private evaluation.
- A persistent distributed render queue, worker fleet and corpus-scale orchestration.
- Checked-in CI automation, a small versioned real-demo golden fixture, repeated-render reproducibility checks and real browser/video acceptance tests.

See [Next steps](NEXT_STEPS.md) for the order and evidence needed to complete these milestones.
