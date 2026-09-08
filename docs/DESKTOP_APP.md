# Demo Processor desktop app

Double-click **[Launch Demo Processor.cmd](../Launch%20Demo%20Processor.cmd)** in
the inner repository folder. It opens a local Windows desktop application using
the existing `.venv`; no additional UI package or web server is required.
The launcher remembers your folders and options between sessions.

From PowerShell, the equivalent is:

```powershell
.venv/Scripts/pythonw.exe -m cs2_data.desktop
```

Run this from the inner folder containing `pyproject.toml`. A future reinstall
with `pip install -e .` also adds the `cs2-data-ui` GUI entry point; reinstalling
is not required for the double-click launcher.

## Demos

### Process an entire demo for one player

1. Select one demo and click **Load players**. Select the named player in
   **Player POV**. Missing source tables and phase/context data are prepared
   automatically; current verified tables are reused.
2. Keep the output preset **640x360, RGB, 8 bits/channel, 32 FPS, lossless
   compression** and choose the **Output** folder. Histories contain eight
   consecutive frames. The sample clip count/duration controls do not affect
   this preset or full-demo coverage.
3. Click **Queue entire demo**. Repeat for other demo/player entries if needed.
4. In **Demo queue**, click **Start / resume queue** once. Preprocessing discovers
   all eligible rounds for that player, then capture, numerical acceptance and
   compression continue automatically through the queue.
5. **Open coverage report** shows planned intervals, excluded time, completion,
   accepted examples, rejected examples and their reason counts.

The queue includes ordinary alive round progression and combat. It excludes
setup/warmup, freeze time, pauses, dead time and unsupported source intervals.
Capture segments last up to two minutes and stop at eligibility boundaries;
they are internal work units and require no manual advance or HUD review.
Overlapping history at internal splits is deduplicated by action target identity.
Short final tails are redistributed rather than discarded. All exclusions are
recorded. See [FULL_DEMO_PROCESSING.md](FULL_DEMO_PROCESSING.md) for exact rules.

The persistent queue is `<Output>/full-demo-queue/queue.json`. Each entry has a
coverage report and progress journal under `jobs/<id>/`. Completed segments
retain lossless `training.zip` and `evidence.zip` packages; their temporary raw
working files and staged demo copy are released only after archive verification.
The original demo and shared parsed source are retained. Changing Output selects
another queue; returning to the original folder restores its entries.

**Stop after current step** finishes the current full-demo segment, including
acceptance, compression and cleanup, before stopping. Closing the busy launcher
requests that same graceful stop. Reopen it and use **Start / resume queue** to
continue. Completed segments are verified and skipped. Low disk space pauses
before the next capture; source or processing failures retain their journal and
show **needs attention**. A running queue uses one CS2 instance at a time.

### Prepare sources or plan sample captures

1. Choose the **Demos** folder and an **Output** folder. Use **Scan folder** to
   refresh the list; **Include subfolders** controls recursive discovery.
2. Select one to eight demos. Ctrl-click and Shift-click select multiple rows.
3. Use **Prepare source data** to extract commands/state and prepare the
   competitive-phase and state-context sidecars. Current retained source tables
   are checked against their hashes and reused when available. Old extractor
   output is preserved; a fresh current extraction goes into the new run.
4. To choose a POV, click **Load players**, then choose a name in **Player POV**.
   Loading prepares missing source data first and lists recorded names, exact
   Steam IDs and how many selected demos contain each player. Duplicate names
   remain separate by ID. Leave **Automatic player selection** to let the planner
   choose across players. The list is a recorded roster, not a promise of eligible
   competitive footage; coaches or other recorded participants may have none.
5. Use **Plan sample captures** to prepare any missing source data and create
   a bounded capture batch. Choose 1-8 clips of 5, 10 or 20 seconds; the default
   remains 10 seconds. The existing
   planner selects ordinary/action examples from the chosen player's eligible
   competitive play, or across players in automatic mode. A chosen player with
   no eligible clips produces an error and retained diagnostics; it never falls
   back to someone else.
   This step does not launch CS2.
6. **Open source results** opens the selected demo's parsed directory. Its
   `manifest.json` describes the extraction; `validation.json` records quality
   issues. **Open last run** opens the latest launcher task and full activity log.

Changing the selected demos or output folder clears the player list and returns
to automatic selection. Load players again to choose a POV for that selection.
Player choices also start in automatic mode when reopening the app; saved batch
plans retain their exact player IDs. This is bounded sample collection, not a
complete player-match capture queue.
For a larger action-blind recording of consecutive rounds, use the separate
[round collection workflow](ROUND_COLLECTION.md). Its standard sub-batches can
be opened in **Capture batches**; the session coordinator currently runs from
the command line and owns the project lock while recording.

The optional **Series ID** groups newly extracted maps from the same match/BO3.
Leave it blank to preserve the identity of existing data. The app refuses to
silently change an existing series ID. Group only maps belonging to the same
series; independent series are needed for validation/test.

**Recorded parse** is saved metadata shown for convenience, not a fresh audit.
Preparation rechecks the source bytes. **Prepared; quality issues** means a
complete parse exists with reported missing/invalid records. Those records are
retained as evidence; later per-sample checks decide which windows are eligible.
Neither status means that frames have been captured or training data accepted.

## Capture batches

A newly planned batch appears automatically in this tab. **Load batch...** can
also open an existing `batch_plan.json`, including batches created with the CLI.
The table shows the map, round, Steam ID, duration and last recorded status.
Displayed acceptance counts come from a summary bound to that plan; they are
not an independent revalidation performed by simply opening the window.
Malformed plans leave the current batch intact. Unreadable or malformed summaries
are ignored for display, and refreshing keeps the selected clip.

Close normal CS2, then click **Run / resume next clip**. This calls the existing
protected competitive batch runner with a one-job execution budget. It may
launch CS2, process the capture and advance it to acceptance. It retains
the worker's exact compatibility checks, settings backup/restoration, native
clock and pixel checks. Routine captures trust the user-approved current HUD
setup without generating review sheets or requiring human review. The app does not change
Steam Offline Mode or Cloud preferences.

For older captures with retained sheets, **Open review sheets** opens the saved
local diagnostic page. New routine captures do not generate these sheets.
Historical `pending visual review` statuses describe the earlier workflow;
supported captures can now advance using `user_approved_capture_setup` as their
HUD acceptance basis. It records the setup assumption, not per-frame human
inspection. The current **Ready for acceptance** display is separate from that
historical recorded status. Completed stages are rechecked and reused.
Review links come from the current completed step in the matching capture
journal, with the saved page checksum checked. Unjournaled or incomplete attempt
folders cannot silently replace that review. Older bundles without an HTML index
open their recorded review folder.

Failures, changed evidence and recovery requirements are shown as **Needs
attention** with details in the log, even when the batch command itself returns
exit code zero. Failed stages are not automatically retried and restoration is
not bypassed. Use the [batch guide](COMPETITIVE_BATCH.md) and
[Windows recovery instructions](WINDOWS_RENDERING.md) to resolve the cause.

## Stopping, output and limits

- **Stop after current step** prevents the next producer/process from starting.
  It lets the current extraction, planner or single-clip batch invocation finish.
  It is not an immediate process kill.
- Closing a busy window requests the same stop, then closes when the task and
  any renderer cleanup attempt return. Recovery failures remain in the log and
  worker journals. Do not use Task Manager as the normal stop mechanism.
- One launcher window owns the project at a time, with one background task.
  The UI remains responsive while the task runs. No parallel game-instance
  setting is exposed in this version.
- New launcher work goes into `<Output>/runs/<time>-<task>-<id>/`. Each task
  retains `run.json` and `activity.log`, including failed/partial attempts.
  Existing captures keep their batch's output directory when resumed, even if
  the launcher's Output field points elsewhere.
- Folder/options preferences live in `data/launcher/settings.json`. Startup
  failures are recorded in `data/launcher/startup-error.txt`.
- Legacy 720p sample originals cost about **1.18 GB per ten-second clip**, with
  additional demo copies, traces and review files. The app checks working space
  on the batch drive before capture; this is not a whole-demo budget or guarantee.

The launcher supports the **complete demo queue for one selected player per
entry** as well as bounded sample batches. Parallel game instances remain future
work. Source preparation can read arbitrary demos, but rendering and training
acceptance remain limited to the project's supported source/game profiles.

## Verification

The launcher has separate orchestration tests and opt-in real Tk interface tests:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_launcher_backend.py tests/test_launcher_players.py -q
$env:CS2_TEST_GUI = '1'
.venv/Scripts/python.exe -m pytest tests/test_desktop_ui.py tests/test_desktop_players.py -q
```

The interface tests use fixture processing callbacks and do not launch CS2.
A separate real-source smoke run reused Dust2's canonical tables and produced
one ten-second competitive plan with the actual planner/clock tools, without
capturing. A new live capture initiated through the GUI has not been tested;
the callback delegates to the previously verified protected batch runner.
Twenty-second capture has now passed a live trial through that protected runner,
retaining 640 original frames with matched message clocks and verified settings,
binary and HUD cleanup. The GUI now exposes the same 20-second option; initiating
a 20-second capture through the GUI has not been separately tested.
Evidence and app screenshots are under
[`data/validation/desktop-app-001/`](../data/validation/desktop-app-001/).
The player-selection follow-up has **136 backend/collection/batch checks and 20
real Tk checks passing**, including changing selections during roster loading,
exact player dispatch and malformed batch/review handling. Follow-up evidence
is under [`data/validation/desktop-app-002/`](../data/validation/desktop-app-002/).
A real GUI run loaded the Dust2 roster, selected Ckanic and planned four
ten-second clips for rounds 7, 16, 18 and 23 in about 85 seconds. All jobs retain
his exact Steam ID; the plan has two ordinary and two reload examples. The app
was visually inspected with that result loaded. No live capture was started.
