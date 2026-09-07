# Initial implementation and demo audit

This records the initial checkpoint before the native Windows port. Subsequent
Windows rendering now produces real short clips; see [current status](progress/STATUS.md)
and [Windows rendering](WINDOWS_RENDERING.md). The extraction findings below remain applicable.

Run date: 2026-09-07. The input recordings are the three supplied MOUZ NXT vs Misa ESL demos in the repository. They were read without modification. Final audited outputs are in `data/parsed/v2-audited/`; earlier `v1`, `v2`, and `data/diagnostics/` outputs are retained development diagnostics and should not be used for dataset preparation.

## Actual extraction results

| Map | Reconstructed UserCmds | Available state snapshots | Missing-baseline command payloads | Out-of-range subtick values |
| --- | ---: | ---: | ---: | ---: |
| Dust2 | 1,416,312 | 2,490,975 | 1,074,675 | 1,756 |
| Nuke | 1,870,193 | 2,179,831 | 309,650 | 2,336 |
| Cache | 1,646,811 | 2,170,156 | 523,357 | 2,153 |

There are **4,933,316 retained reconstructed commands**. All three files parsed to the end. Each recorded 26 round-start events and 12 human Steam identities across the recording; those counts include the recorded session and are not assertions about eligible competitive rounds or ten simultaneous competitors. The tables preserve warmup/dead states for audit, while render jobs apply filtering.

The parser is pinned to `demoinfocs-golang/v6 v6.0.0-alpha.0`; extractor revision is `0.1.1`, parser schema is `2`. Every output includes the demo SHA256, schema/parser metadata and file checksums. Go writes raw nullable scalar presence, explicit parent-message presence, nested subticks/history, and a deterministic serialization of the full reconstructed protobuf.

The manifest and `validation.json` in each directory contain detailed errors, per-player metrics, and artifact checks. **The quality validator intentionally fails all three complete-demo datasets.** A successful file parse alone is not sufficient evidence of usable training labels.

The final validator verified all four output-file hashes and reconciled every eligible payload as either reconstructed or explicitly rejected: there are zero unaccounted payloads. Reconstruction coverage is 56.86% for Dust2, 85.79% for Nuke, and 75.88% for Cache.

All three diagnostic normalization outputs are in `data/normalized/v2/<demo SHA256>/`. They retain every raw command row and provide 3,511,650 valid alive-player angular transitions: Dust2 1,030,560; Nuke 1,296,544; Cache 1,184,546. Other transitions are masked with explicit reasons, including 25,278 Nuke transitions with unavailable view-angle messages. These counts describe angle-target availability, not validated frame/action training samples.

## Findings that affect training

The pinned parser sends some reconstruction warnings through its net-message dispatcher. The extractor listens to both warning channels; otherwise it would incorrectly report no warnings while commands were omitted.

Dust2's 1,074,675 missing-baseline payloads are explained by unavailable initial prefixes. The first decoded tick varies substantially by player; after that first baseline, each player's emitted demo ticks are contiguous through their final tick. For example, slot 2 starts decoding at tick 66,965, so rendering that player's first round would produce a video with no canonical commands. The job planner now checks command coverage and skips such intervals.

Upstream deliberately rejects commands without established baselines. No newer released v6 or decoder change fixing these prefixes was found during this run. Initializing invented command state would depart from the parser's game-decoder-validated behavior. [Upstream reconstruction implementation and discussion](https://github.com/markus-wa/demoinfocs-golang/pull/669)

Dust2's 1,756 out-of-range values occur in `subtick_moves.when`, not input-history fractions. They range from -1.8125 to -0.0078125, mostly in increments of 1/128. They appear during live play and are not one uniform sentinel. Their precise game semantics remain unresolved, so they are preserved and flagged rather than clamped or treated as valid in-tick timing.

For every retained Dust2 command, `server_tick_executed - demo_tick = 10,703`. This is an observed clock relationship, not proof of pixel/action alignment. Commands are delivered in demo packets; the engine's capture stage and interpolation still need calibration against the executed command clock.

Most raw mouse fields are absent in the protobuf because default-zero scalars can be cleared during delta reconstruction. Schema 2 records parent presence so the normalizer can produce separate effective zero values when justified. The original nullable values and protobuf remain intact. Missing parent messages remain unavailable. Mouse-scale diagnostics do not identify player DPI.

State snapshot ticks in Dust2 happen to have one-tick cadence. No fake intermediate state was generated. Pause properties were unavailable through the currently verified fields, so candidate jobs record that pause state has not been verified.

## Rendering and alignment status

The pinned Reka renderer and maintained one-job adapter compile, their targeted tests pass, and an actual demo job passes dry-run validation. The adapter uses the extractor's intervals and does not substitute Reka's inferred actions for canonical commands.

`data/jobs/dust2-audited-first.json` selects slot 3, round 1, ticks `[1279,2458)`, with 1,179 recorded commands covering all 1,179 demo ticks. Its actual-demo dry run passed; the planner skipped 109 candidate windows lacking sufficient action coverage. The job requests 32 fps at 1280×720 and remains unrendered.

**Zero CS2 replay frames were captured.** The local game is a native Windows installation with patch 1.41.7.8. The pinned renderer/plugin requires Linux and targets patch 1.41.6.5; its encoder also assumes AMD VAAPI. Game/plugin compatibility must be established on a suitable worker. The adapter refuses execution on this Windows environment before launching Steam or changing the game installation.

The aligner and standalone viewer are implemented and tested with synthetic fixtures. The aligner requires real video, verified POV, matching identity/hash, decoded PTS, and measured frame boundaries. The upstream renderer does not yet supply proven frame-capture anchors. Packet-arrival alignment remains explicitly `training_ready=false` even after measured video timing is supplied.

Next work is to validate a compatible rendering worker, instrument/calibrate capture timing, inspect covered player intervals visually, and resolve negative subtick semantics. The existing later command windows can support those investigations without inventing the missing initial inputs. No neural network was trained in this implementation milestone.

## Verification

- Extractor Go tests pass, including nullable nested Parquet/protobuf preservation, corrupt-input handling, output immutability, checksums, omitted/default input semantics, rejection accounting, and continuity diagnostics.
- All 34 Python tests pass, including source integrity, incomplete-output rejection, concurrent-output exclusion, normalization, command coverage, frame interval assignment, and synthetic Parquet-to-viewer integration.
- Three targeted renderer tests pass; the adapter builds and validates the actual covered Dust2 job.
- Real data quality validation returns a failure exit status for each demo for the documented baseline and subtick issues. No complete game-render/alignment integration test has passed yet.

## Local artifacts

| Map | Demo SHA256 / output directory name |
| --- | --- |
| Dust2 | `f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773` |
| Nuke | `51166a86ceb5a7318d958329349e743c10358a7ccc162f39dfd1bc74de321a1c` |
| Cache | `e357150ceed26e9b3683d2cd5efa42e8bfc8eafd2f0f43a29d85ec72bc99495a` |

The Windows executable is `bin/cs2-extract.exe`. Python commands are installed in `.venv/Scripts/`. Generated data and downloaded dependencies are ignored by Git; code and documentation remain in the working tree for review.
