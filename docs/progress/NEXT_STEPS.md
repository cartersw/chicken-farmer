# Remaining work

**Updated: 2026-09-07.** These items describe work still to do. Check them off only when the stated evidence exists. Current implementation and real-demo results are in [STATUS.md](STATUS.md).

## Completed foundation

- [x] Establish repository structure, command-line tools and local build environment.
- [x] Implement full-mode UserCmd extraction and canonical Parquet/protobuf preservation.
- [x] Add identity, clock, parent-presence, version and checksum metadata.
- [x] Implement validation, including both parser warning channels and rejected-payload accounting.
- [x] Process all three supplied demos and retain their actual quality reports.
- [x] Normalize all three command streams with explicit target masks and effective scalar defaults.
- [x] Implement render planning that checks canonical command coverage.
- [x] Pin/build the Reka adapter and dry-run a covered real-demo job.
- [x] Implement measured-frame alignment and the viewer, verified with synthetic fixtures.
- [x] Document results, blockers, artifacts and remaining work.
- [x] Build the native Windows plugin/worker and capture real two- and five-second Dust2 clips, including repeat runs and a live-window color check.

## 1. Produce the first real replay clip

- [x] **Choose and prepare a rendering environment.** Native Windows CS2, x64 MSVC/SDK, CMake and software FFmpeg encoding are installed and exercised. Linux is unnecessary for this worker.
- [x] **Establish basic local game/plugin compatibility.** Corrected the demonstrated CVar and replay interface mismatches; the plugin now loads, renders short supplied-demo intervals and exits successfully on CS2 1.41.7.8. Versions/hashes are recorded. Other game versions remain unverified.
- [x] **Capture a short covered Dust2 pilot.** Two-second and five-second runs produce 64/160 frames, correct dimensions/PTS and the requested ay0k POV. Repeated runs have matching counts; the actual game-window screenshot confirms qualitative colors/orientation.
- [ ] **Accept a clean competitive interval and expand capture.** Remove/avoid the transient spectator HUD panel, exclude the initial knife/setup phase, and validate longer jobs. The current worker deliberately caps captures at five seconds and the full `[1279,2458)` source job has not been rendered as one clip.
- [ ] **Check repeatability.** Repeat the same requested interval into a new output location. Completion: POV, visual profile and measured timing are reproducible within documented tolerances.

A decodable clip completes capture, not synchronization. Keep its timing/POV claims unverified until the relevant checks pass. Detailed setup: [RENDERING.md](../RENDERING.md).

Run evidence: `data/rendered/windows-pilot-004/` through `007/`. Failures 001–003 remain documented and were resolved by concrete SDK/interface fixes. All completed attempts restored `gameinfo.gi`; normal CS2 preference changes are outside that transaction.

## 2. Establish correct frame and action timing

- [ ] **Instrument frame capture.** Record the actual replay tick/fraction and frame index at a proven capture hook, including the final interval boundary. Completion: every decoded frame has traceable measured boundaries reconciled with video PTS; seeks, drops and duplicates are detected.
- [ ] **Implement execution-clock alignment.** Determine the relationship between command execution, packet arrival, replay interpolation and captured pixels. Use Dust2's observed 10,703-tick offset as evidence to investigate, not a universal constant. Completion: the calibrated mapping is versioned, implemented and tested across relevant players/rounds and demos.
- [ ] **Exercise the real aligner and viewer.** Inspect isolated shots, aim changes, movement starts/stops and jumps/crouches. Completion: a real clip passes video/timing checks and a recorded visual review demonstrates that labels match the displayed actions.
- [ ] **Define when a clip becomes training-ready.** Implement explicit acceptance criteria and quality masks; preserve the current diagnostic outputs. Completion: accepted clips carry evidence for identity, coverage, state eligibility and timing, while invalid clips fail predictably.

Do not mark these complete by modifying `timing_status`, `pov_verified` or `training_ready` on an unverified output. Contract and tests: [ALIGNMENT.md](../ALIGNMENT.md).

## 3. Resolve and filter input-quality issues

This work can run alongside rendering setup.

- [ ] **Characterize missing baselines in every demo.** Confirm each player's coverage and choose valid later intervals. Any recovery method needs game/upstream evidence and fixtures. Completion: missing intervals are explicitly excluded or recovered by a validated method, never filled with invented commands.
- [ ] **Determine negative subtick semantics.** Investigate the recorded negative `when` values and establish a documented interpretation/filter. Completion: fixtures and validation rules reflect evidence; raw data remains unchanged.
- [ ] **Verify pause and gameplay-state filtering.** Resolve pause availability or exclude uncertain windows, and check warmup/freeze/death/reconnect boundaries. Completion: accepted clips cannot silently span unsupported states.
- [ ] **Verify button meanings and input completeness.** Compare library button constants against replay/events and retain masks for absent angle messages and reset transitions. Completion: relevant actions have visual fixtures and unsupported targets are explicitly masked.
- [ ] **Check angular reconstruction.** Integrate accepted angular deltas and compare against the original view trajectory with reset boundaries respected. Completion: diagnostics and fixtures catch offset, wrap and target-attachment mistakes.
- [ ] **Run one real data-path regression case.** Completion: a supplied or small authorized fixture goes through parse, render, measured alignment and real viewer inspection, with expected counts/quality evidence recorded.
- [ ] **Preserve regression evidence.** Add a small versioned real-demo fixture/expected summary and CI for checks that do not require CS2; document a dedicated render integration run. Completion: parser/library upgrades can be checked against the recorded evidence.

The whole-demo validator can continue to fail incomplete recordings while a future explicit per-clip acceptance layer admits verified intervals. Do not weaken global checks simply to make a report green.

## 4. Build an actual training dataset

Depends on verified frames, execution alignment and accepted clip quality.

- [ ] Build an indexed temporal-window loader for video plus previous actions and future command targets; retain the native variable command stream and define slot/mask behavior explicitly.
- [ ] Add verified semantic button targets and a future-action tensor builder, then extend the real data-path regression to load a complete training batch.
- [ ] Implement deterministic splits by entire match/series, keeping all three current maps together. The shared `match_id` exists; split-building code does not.
- [ ] Add dataset manifests and sampling/quality statistics. Start with MP4 plus Parquet; add shards only if throughput requires them.
- [ ] Increase to a small validated corpus before scaling. Follow the handoff's initial 10-demo/manual-inspection milestone; only three input demos are currently supplied.

Completion: sample batches contain the intended visual history and future actions, tests rule out split leakage, and real clips have documented visual checks. Millions of extracted command rows alone do not satisfy this milestone.

## 5. Train and evaluate the first policy

- [ ] Implement the small CNN/GRU behavioral-cloning baseline and separate aim/movement/button heads described in the handoff.
- [ ] Train on accepted samples and compare held-out action metrics against trivial baselines.
- [ ] Implement modular screen capture, inference, abstract action output and telemetry.
- [ ] Evaluate only in a local/offline or explicitly authorized private environment, after offline data/timing checks pass.

Completion: reproducible training/evaluation results and a recorded local rollout demonstrate the intended visual control behavior. Training, runtime control and evaluation are currently unimplemented.

## 6. Scale after correctness is established

- [ ] Add persistent worker jobs, retries/status reporting and renderer orchestration.
- [ ] Benchmark decoding/storage throughput and 32 versus 64 fps before expanding the corpus.
- [ ] Add further demos, workers, dataset packaging and larger models as justified by the validated baseline.

Do not scale rendering before synchronization is visually verified. Use the [handoff](../../CS2_Vision_Imitation_Learning_Project_Handoff.md) for later model and research milestones.
