# Demo Processor desktop app

Open **Chicken Farmer - Demo Processor** from the desktop shortcut, or run
[Launch Demo Processor.cmd](../Launch%20Demo%20Processor.cmd) in the inner
repository. The app uses the existing `.venv` and remembers your settings.

## Process a demo

1. In **Demos**, browse to your demo folder. **Refresh** rescans it; pressing
   Enter in the folder field does the same.
2. Select a demo and click **Load players** (or double-click the demo).
   Missing source data is prepared automatically.
3. Select a player and click **Add to queue**. Repeat for more demos.
4. In **Queue**, click **Start queue**, or **Resume queue** for unfinished work.
5. Select a queued demo and use **View report** for coverage and results.
   **Open output** opens the queue's output directory.

The normal view has only Demos and Queue. Queue status refreshes as processing
advances and whenever you return to the tab. Completed work is reused on resume.
The player list shows names, adding Steam IDs only when needed to distinguish
names. The exact selected Steam ID still determines the recorded perspective.

## Settings and tools

**Settings** contains the output folder, available disk space, validation worker
count (1-4), optional series ID, and the subfolder search option. **Done** saves
settings and closes the tab. Changing Output selects the queue in that folder
and clears the current player selection. Returning to an earlier output folder
restores its queue.

Training remains fixed at **640x360, full-color RGB, 8 bits per channel, 32 FPS,
eight-frame histories, and lossless compression**. The fixed format is displayed
in Settings without a redundant single-option selector. Rolling decompression
and trainer prefetch are documented in [TRAINER_DESIGN.md](TRAINER_DESIGN.md).

The optional Series ID groups maps from the same match/BO3. Leave it blank to
preserve existing identity. Independent series should stay separate for training
and evaluation; the app refuses to silently change an existing series ID.

The **More** menu provides:

- **Prepare source data**: prepare the selected demos without queuing capture.
  Select up to eight using Ctrl-click, Shift-click or Ctrl+A.
- **Open source results**: open the selected demo's parsed directory, including
  `manifest.json` and `validation.json`.
- **Sample captures**: open the optional sample planning and capture tools.
- **Activity log**: open the live log and access the latest run folder. Logging
  continues while this tab is hidden; task failures open it automatically.
- **Quick guide**: open this document.

Logs, settings, and sample tools open only when needed. **Close tab** hides a
secondary view without discarding its content. Returning to sample captures
refreshes the saved batch status.

## Capture and output

One CS2 session records all eligible intervals for the selected player before
parallel validation begins. Shared evidence is indexed, and validation workers
prepare lossless training shards. Normal alive progression and combat are both
included. Setup/warmup, freeze time, pauses, dead time and unsupported source
intervals are excluded, with reasons recorded in the coverage report.

Training files split at up to two minutes, without restarting CS2 at internal
file boundaries. History overlap does not duplicate action targets. Processing
requires no manual advance or routine HUD review. See
[FULL_DEMO_PROCESSING.md](FULL_DEMO_PROCESSING.md) for the exact contract.

The queue lives at `<Output>/full-demo-queue/queue.json`. Each demo has reports
and progress under `jobs/<id>/`. Completed segments retain `training.zip` and
`receipt.json`; `session-packages` holds compact session receipts in lean mode.
New plans default to lean retention: training archives and compact receipts are
kept, while temporary validation evidence is released after all dependent segments
are verified and packaged. **Keep full debug evidence** in Settings opts newly
queued demos into retaining the original frames, native logs and evidence ZIPs.
Existing plans keep their saved policy; changing the setting does not delete or
convert existing output. Temporary capture work is released only after archive
verification. Retain the
source demo and parsed tables alongside the output packages.

**Stop** requests a graceful stop. During recording, it finishes the current
physical recording interval and closes CS2. During validation, it finishes
already admitted clips. Closing a busy app requests the same stop before exiting.
Reopen the app and use **Resume queue** to continue. Missing intervals require
a new CS2 session; closed recordings and completed packages are reused.

Insufficient disk space or processing failures retain a specific diagnostic.
Inspect the report or **More > Activity log**, resolve the cause, then resume.
Only one launcher instance owns the project at a time.

## Optional sample captures

Choose demos and an optional player in Demos, then open **More > Sample captures**.
Choose 1-8 clips and 5, 10 or 20 seconds, and click **Plan from selection**.
Leaving the player unselected lets the sample planner choose automatically.
The full-demo queue always requires a named player.

**Load batch...** opens an existing `batch_plan.json`. **Run next clip** advances
one clip using the protected batch runner. Close any normal CS2 session first.
**Open folder** provides access to retained batch results and any historical
review artifacts. Routine captures generate no review sheets.

The displayed accepted count comes from the saved batch summary. Malformed plans
leave the loaded batch intact; unsupported source/game profiles still fail the
existing compatibility checks. See [COMPETITIVE_BATCH.md](COMPETITIVE_BATCH.md)
and [WINDOWS_RENDERING.md](WINDOWS_RENDERING.md) for recovery details.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/test_launcher_backend.py tests/test_launcher_players.py -q
$env:CS2_TEST_GUI = '1'
.venv/Scripts/python.exe -m pytest tests/test_desktop_ui.py tests/test_desktop_players.py -q
```

The Tk tests use fixture processing callbacks and do not launch CS2. They cover
queue dispatch/resume, player identity, source preparation, settings persistence,
secondary views, logs, graceful stopping and all screens at 960x600.

Settings are stored in `data/launcher/settings.json`. Launcher tasks retain
`run.json` and `activity.log` under `<Output>/runs/`. Startup errors go to
`data/launcher/startup-error.txt`.
