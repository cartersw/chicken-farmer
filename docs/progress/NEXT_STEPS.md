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
- [x] Add independent phase evidence and exclude setup/knife/restarted or unknown rounds by default; audit all three demos as 24 competitive plus 2 setup rounds each.
- [x] Select and capture a competitive five-second Dust2 interval with 320 commands covering 320 ticks and an isolated pistol shot.
- [x] Verify native removal of the ten-player equipment panels/chat and clear stale announcements using six seconds of UI settling; current v5 worker preserves money/health/ammo/crosshair without encoding masks.
- [x] Match all 160 archived TGA RGB payloads from competitive run 004 to native pixel-readback hashes.
- [x] Complete the real run-006 pipeline: 160 unmasked video frames, 320 future commands, zero empty intervals, actual render-time values, endpoint and native epoch evidence, normalized labels and a viewer.

## 1. Produce the first real replay clip

- [x] **Choose and prepare a rendering environment.** Native Windows CS2, x64 MSVC/SDK, CMake and software FFmpeg encoding are installed and exercised. Linux is unnecessary for this worker.
- [x] **Establish basic local game/plugin compatibility.** Corrected the demonstrated CVar and replay interface mismatches; the plugin now loads, renders short supplied-demo intervals and exits successfully on CS2 1.41.7.8. Versions/hashes are recorded. Other game versions remain unverified.
- [x] **Capture a short covered Dust2 pilot.** Two-second and five-second runs produce 64/160 frames, correct dimensions/PTS and the requested ay0k POV. Repeated runs have matching counts; the actual game-window screenshot confirms qualitative colors/orientation.
- [x] **Exclude setup phases and establish a competitive HUD profile.** Hash-bound game-rule/score evidence excludes the knife/setup rounds. Native settings remove extra team information/chat; three pauses allow the announcement to expire before capture. Run-006 raw and encoded evidence confirms the v5 profile without masks. See [phase filtering](../PHASES.md) and [HUD trials](../HUD_PROFILE.md).
- [x] **Verify the unmasked repeat.** Run 006 preserves native money, health/ammo, crosshair and viewmodel; first/shot-adjacent/last raw previews contain no stale notice or private team panels.
- [ ] **Expand capture.** Validate longer competitive intervals and additional players/rounds after addressing the current five-second cap and explicit acceptance limits.
- [ ] **Check repeatability.** Repeat the same requested interval into a new output location. Completion: POV, visual profile and measured timing are reproducible within documented tolerances.

A decodable clip completes capture, not synchronization. Keep its timing/POV claims unverified until the relevant checks pass. Detailed setup: [RENDERING.md](../RENDERING.md).

Current completed dataset: `data/datasets/dust2-competitive-006/`, derived from `data/rendered/windows-competitive-006/` and the Ckanic job `data/jobs/dust2-competitive-shot-001.json`. Run 006 uses the unmasked native v5 profile; run 005's historical v4 MP4 still contains the former mask. Earlier `windows-pilot-004/` through `007/` are knife/setup capture diagnostics. Completed attempts restored `gameinfo.gi`; normal CS2 preference changes are outside that transaction.

## 2. Establish correct frame and action timing

- [x] **Instrument native movie/readback correspondence.** Run 004 has 160 archived images matching 160 native readback hashes, with no repeated pixel frames.
- [x] **Record render-time intervals and the final endpoint.** Runs 005/006 record `EventClientOutput_t.m_flRenderTime` per image and the first movie callback after recording stops. The real timing stage reconciles 160 intervals with decoded PTS and native pixel hashes. Integer replay-cursor ticks remain separate from the observed rendered-state time.
- [x] **Record the native replay epoch separately.** Run 006 reads `IDemoFile.GetDemoStartTick=-5546`, distinct from the 10703 execution-versus-packet tick difference; no execution equivalence is asserted.
- [ ] **Validate execution-clock alignment.** Candidate calibration and diagnostic execution-clock joins are implemented; independently validate the relationship between command execution, packet arrival, replay interpolation and captured pixels. Use Dust2's observed 10,703-tick offset as evidence to investigate, not a universal constant. Completion: the mapping is backed by measured anchors or documented real visual acceptance over relevant players/rounds and demos, with uncertainty retained.
- [x] **Exercise the real aligner and viewer export.** Run 006 produces 160 frame rows, 320 commands and zero empty intervals, using future `(start,end]` action windows, actual render time and normalized labels. Artifacts retain explicit training limits.
- [x] **Check the pilot's isolated shot.** Frame 50's future commands contain the attack at demo tick 6102; frame 51 visibly flashes and the ammo counter changes from 20 to 19.
- [ ] **Broaden visual/clock acceptance.** Check more isolated shots, aim changes, movement starts/stops and jumps/crouches across players/rounds. Completion: the scoped execution epoch, observation semantics and POV assumptions are supported beyond one successful transition.
- [ ] **Define when a clip becomes training-ready.** Implement explicit acceptance criteria and quality masks; preserve the current diagnostic outputs. Completion: accepted clips carry evidence for identity, coverage, state eligibility and timing, while invalid clips fail predictably.

Do not mark these complete by modifying `timing_status`, `pov_verified` or `training_ready` on an unverified output. Contract and tests: [ALIGNMENT.md](../ALIGNMENT.md).

## 3. Resolve and filter input-quality issues

This work can run alongside rendering setup.

- [ ] **Characterize missing baselines in every demo.** Confirm each player's coverage and choose valid later intervals. Any recovery method needs game/upstream evidence and fixtures. Completion: missing intervals are explicitly excluded or recovered by a validated method, never filled with invented commands.
- [ ] **Determine negative subtick semantics.** Investigate the recorded negative `when` values and establish a documented interpretation/filter. The run-006 scoped input window still contains one invalid subtick observation at demo tick 6007. Completion: fixtures and validation rules reflect evidence; raw data remains unchanged.
- [ ] **Complete pause and gameplay-state acceptance.** Competitive phase filtering now has captured ESL fixtures and score-rollback checks. Resolve pause availability or exclude uncertain windows, and complete freeze/death/reconnect acceptance. Completion: accepted clips cannot silently span unsupported states.
- [ ] **Verify button meanings and input completeness.** Compare library button constants against replay/events and retain masks for absent angle messages and reset transitions. Completion: relevant actions have visual fixtures and unsupported targets are explicitly masked.
- [ ] **Check angular reconstruction.** Integrate accepted angular deltas and compare against the original view trajectory with reset boundaries respected. Completion: diagnostics and fixtures catch offset, wrap and target-attachment mistakes.
- [ ] **Run one real data-path regression case.** Completion: a supplied or small authorized fixture goes through parse, render, measured alignment and real viewer inspection, with expected counts/quality evidence recorded.
- [ ] **Expand regression evidence.** Captured initial Dust2 phase events already have a versioned fixture. Add a small authorized full-demo/expected-output case and CI for checks that do not require CS2; document a dedicated render integration run. Completion: parser/library upgrades can be checked against end-to-end evidence.

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
