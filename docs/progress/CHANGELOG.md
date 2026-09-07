# Completion history

Record delivered work here in date order, newest first. Keep historical results intact; [STATUS.md](STATUS.md) describes the current state and [NEXT_STEPS.md](NEXT_STEPS.md) tracks unfinished work.

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
