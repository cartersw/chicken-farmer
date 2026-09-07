# Current implementation status

**Checkpoint: 2026-09-07**

The command-processing pipeline works on the three supplied ESL demos, and native Windows replay capture now produces real frames and MP4 clips. The complete training dataset remains unfinished: execution-time alignment still needs implementation/calibration, and the extracted inputs and rendered visual profile need quality acceptance.

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
| Render-job planner | Alive player-round intervals, identity checks, known freeze/warmup/pause exclusions, demo hash verification and canonical command-coverage checks | Produced a real Dust2 job with continuous recorded commands; pause availability remains uncertain | [Job planner](../../src/cs2_data/jobs.py) |
| Reka integration | Pinned source, setup/build tool, environment doctor, one-job adapter and narrow capture/profile/cleanup patches | Go adapter builds and passes dry run; real capture uses the maintained Windows adaptation below | [Renderer tooling](../../tools/renderer), [rendering guide](../RENDERING.md) |
| Native Windows worker | x64 C++ plugin adaptation; bounded Python replay launcher; isolated staging, recovery journal, byte-exact gameinfo restore; TGA archive and H.264/PTS validation | Native plugin builds; 10 new tests pass, including real FFmpeg color/origin/PTS and failure recovery. Actual game attempts are recorded below | [Windows guide](../WINDOWS_RENDERING.md), [worker](../../tools/renderer/windows.py), [plugin](../../tools/renderer/plugin-windows/README.md) |
| Frame/action aligner | Requires measured frame boundaries, verified POV and identity; checks actual video hash, dimensions, decoded PTS and frame count; preserves all commands and exact raw row IDs | Implemented and tested with synthetic fixtures and a mocked video verifier. Current command join uses packet-arrival ticks; `training_ready=false` is deliberate | [Aligner](../../src/cs2_data/align.py), [alignment guide](../ALIGNMENT.md) |
| Debug viewer | Standalone local HTML with video selection, frame stepping, timing, raw/normalized inputs, position and weapon/state details | Synthetic export/data-path tests pass; no real synchronized replay has been visually inspected | [Viewer](../../src/cs2_data/viewer.py) |
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

Available player-state rows total 6,840,962. Each recording yielded 26 round-start events and 12 human Steam identities over the recording. These are session-level counts, not claims of 26 accepted competitive rounds or 12 simultaneous competitors.

## What currently prevents a training-ready dataset

### Windows capture works; visual and timing acceptance remain

The installed game is native Windows CS2 patch `1.41.7.8`. A native Windows pilot worker now exists alongside the original Linux Go adapter. The Windows path uses an x64 MSVC plugin and software H.264; it requires neither Linux nor VAAPI. CMake 4.2.3, the missing Visual Studio C++ components/Windows SDK, and checksum-verified FFmpeg 9.0.1 tools have been installed locally. Software encoding passed; this FFmpeg's NVENC path requires a newer driver than the installed one.

The upstream plugin targets `1.41.6.5`, so current-game ABI compatibility is being established through bounded real attempts. Initial trials found and diagnosed an obsolete `ICvar` layout. The Windows adaptation backports the relevant upstream SDK correction and records startup stages. It also includes a bounded client-hook fallback and the corrected shutdown callback signature. The original Linux adapter retains its platform guard.

Successful pilots now contain raw TGA frames, H.264 MP4, decoded video PTS, hashes and logs. Runs `windows-pilot-004` and `005` each captured **64 frames / 2 seconds**, requesting ticks `[1279,1407)`. Runs `windows-pilot-006` and `007` each captured **160 frames / 5 seconds**, requesting `[1279,1599)`. All use **32 fps at 1280x720** and the same player, ay0k, slot 3. The separate `render32-v1.json` profile describes 640x360 and is not applied automatically.

CS2 exited with code 0 after each successful run. All completed attempts, including the initial failures, restored the original `gameinfo.gi` SHA256 `b1391e73dbec2e078bdbaf7279c2b955084cf2b38a47e3d8181662e7948679b8`. The successful DLL hash is `b5b889759d5f147ed309eaa42a8be3ad2a2f352d6f6b574fa338ca7d13fb4afe`.

First/middle/last images from pilot 004 visibly show ay0k, a knife and 100 HP, matching the selected identity/state. They also contain a white spectator/HUD panel already present in the raw TGA; this needs visual-profile cleanup. These first-round images show knife play, so this is a rendering diagnostic, not an accepted competitive training window. Manifests retain `pov_verified=false`, `interval_verified=false`, `timing_status=unverified` and `training_ready=false`. Repeated frame counts match; video hashes differ, and deterministic pixels or measured timing repeatability have not been established.

Pilot 007 also has a screenshot of the actual foreground CS2 window, `window-color-reference.png`, and decoded preview images at frames 0, 80 and 159. The live window and decoded video agree qualitatively on color and orientation; no red/blue swap is appropriate for these Windows captures. The white HUD panel is transient and is absent in the middle/last previews. Latest preview clip: `data/rendered/windows-pilot-007/65d673da8b15e089b40700b9.mp4`; its video SHA256 is `f5bd800eb15908fa88d32697036fafbf1c82c8e35050bf74e6a68b8289c0c810`.

Initial attempts are retained as diagnostic history: 001/002 crashed at an obsolete CVar interface slot; 003 loaded the plugin but timed out at the menu because command/demo interface slots had moved. The current adaptation applies evidence-backed SDK/interface corrections; see [the plugin notes](../../tools/renderer/plugin-windows/README.md).

### Some inputs lack an initial baseline

Delta commands need a previous complete state. Dust2's missing payloads are explained by each player's initial unavailable prefix; decoded demo ticks are contiguous after that player's first full baseline. Later covered intervals can still be used for diagnosis and future rendering. The same detailed prefix conclusion has not been established for every player in the other maps.

The planner excludes intervals without sufficient canonical command coverage. It does not invent initial inputs. The audited Dust2 job selects **slot 3, round 1, ticks `[1279,2458)`**, with **1,179 commands covering all 1,179 ticks**. Use `dust2-audited-first.json`; the older `dust2-first.json` job selected an interval without commands and is obsolete.

### Negative subtick timestamps need interpretation

Dust2 contains negative `subtick_moves.when` values from `-1.8125` through `-0.0078125`, including during live play. They are not one uniform sentinel. They are retained and flagged; no evidence yet justifies treating them as normal in-tick times or clamping them. Nuke and Cache also fail the fraction checks.

### Video timing and command execution are not calibrated

The upstream renderer does not yet provide proven per-frame capture anchors. Nominal FPS, a start tick and video PTS are insufficient by themselves. The aligner currently matches command **packet-arrival** `demo_tick`, while retaining `server_tick_executed` separately.

Dust2 has a measured command-clock offset of `server_tick_executed - demo_tick = 10,703`; its relationship to rendered pixels still needs validation. Both normalization and alignment deliberately remain `training_ready=false`. Calibrated execution-time alignment and eligibility logic must be implemented and tested; editing a manifest flag will not complete this work.

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
| Extractor/validator Go tests | Passed | Data preservation, failure handling, schema/hash checks, default/presence semantics, rejection accounting and continuity diagnostics |
| Python suite including Windows worker | **44 passed** | Existing 34 data-path checks plus 10 Windows tests, including transaction recovery and actual FFmpeg TGA color/origin/PTS validation |
| Targeted renderer Go tests | **3 passed** | Job validation, requested visual/POV/schedule commands and cleanup logic |
| Actual audited Dust2 job dry run | Passed | Adapter accepts the real demo/job and schedules an interval with recorded commands |
| Full-demo data-quality checks | **Failed for all three maps** | Identifies the known baseline and subtick issues; prevents treating parse success as data acceptance |
| Native Windows replay capture | **Passed for short Dust2 pilots** | Actual TGA/MP4 output, expected frame counts, decoded PTS/dimensions, normal process exit and byte-exact gameinfo restoration |
| Real frame/action synchronization and aligned viewer inspection | **Not completed** | No accepted end-to-end training sample exists yet |

The Python suite was rerun during the Windows implementation. Older Go/parser results remain the previously recorded checks; the Windows work did not change those components.

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
| Windows attempt evidence | `data/rendered/windows-pilot-001/` onward; each retains manifests, jobs, schedules, logs and recovery journals; successful runs also contain `frames/`, MP4 and PTS |
| Audited canonical data | `data/parsed/v2-audited/<demo SHA256>/` |
| Canonical files per demo | `usercmd.parquet`, `player_state.parquet`, `rounds.parquet`, `events.parquet`, `manifest.json`, `validation.json` |
| Normalized diagnostics | `data/normalized/v2/<demo SHA256>/normalized_actions.parquet` and `normalization_report.json` |
| Covered render job | `data/jobs/dust2-audited-first.json` |
| Obsolete/development diagnostics | `data/parsed/v1/`, `data/parsed/v2/`, `data/diagnostics/`, and older render jobs; retain for history, do not use as current inputs |
| Complete demo hashes and input names | [Initial run artifact inventory](../INITIAL_RUN.md#local-artifacts) |

Large recordings, generated outputs, dependencies and local executables are ignored by Git. The documentation records their local locations; cloning source alone will not reproduce those files. All three maps use `match_id=esl-s52-eu-cup6-misa-mouz-nxt` for a future split that keeps this series together.

## Not implemented yet

- Measured per-frame replay anchors and accepted visual profiles; short native Windows capture itself now works.
- Calibrated command execution alignment, verified sample acceptance and real synchronized viewer review.
- A training-window loader, deterministic match-level split builder, dataset packaging/sharding and training statistics pipeline.
- Verified semantic button decoding and a final fixed-horizon action-tensor builder; currently buttons remain integer masks and command targets remain records.
- A trained behavioral-cloning model, offline evaluation and trivial-baseline comparisons.
- Runtime screen capture, policy inference, action-controller integration and local/private evaluation.
- A persistent distributed render queue, worker fleet and corpus-scale orchestration.
- Checked-in CI automation, a small versioned real-demo golden fixture, repeated-render reproducibility checks and real browser/video acceptance tests.

See [Next steps](NEXT_STEPS.md) for the order and evidence needed to complete these milestones.
