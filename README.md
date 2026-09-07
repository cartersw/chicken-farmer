# CS2 replay training data

Track completed work, current blockers, and the next milestones in [docs/progress](docs/progress/README.md).

This repository starts the pipeline described in [the project handoff](CS2_Vision_Imitation_Learning_Project_Handoff.md): professional `.dem` recordings → reconstructed player commands and state → player POV replay video → auditable frame/action alignment.

The Windows extractor and Python data tools work locally. **All three supplied demos have been processed, yielding 4,933,316 reconstructed commands. They are diagnostic outputs, not yet a training-ready dataset:** validation found missing initial command baselines and negative subtick timestamps. [The initial run report](docs/INITIAL_RUN.md) records the results and remaining work.

**No real replay frames have been generated yet.** Replay video needs a compatible Linux CS2/renderer worker and measured capture timing. A demo contains game state and commands; obtaining pixels requires replaying it in CS2. See [rendering setup and current blockers](docs/RENDERING.md).

## Included

- Go extractor pinned to `demoinfocs-golang/v6 v6.0.0-alpha.0`, explicitly using `UserCmdParsingFull`.
- Streamed Zstandard Parquet: commands, available player-state snapshots, rounds, and gameplay events. Full reconstructed protobuf bytes preserve fields beyond the flattened schema.
- SHA-256 demo identity, shared match identity, parser/schema versions, output checksums, structured progress logs, and quality validation.
- Python angular normalization, alive player-round render jobs, measured frame interval alignment, and a local video/action debug viewer.
- Pinned Reka renderer checkout/setup, a tested one-job adapter, and environment diagnostics.

This milestone prepares and inspects data. Model training and live controls are later handoff milestones.

## Quick start on this machine

Run commands from the **inner** `chicken-farmer` folder containing this README. The local compiler, virtual environment, and executable are already set up here:

```powershell
cd C:\Users\carte\source\repos\chicken-farmer\chicken-farmer
.\bin\cs2-extract.exe --help
.\.venv\Scripts\cs2-data.exe --help
```

Extract all three supplied ESL demos, keeping the maps under the same match identity:

```powershell
.\bin\cs2-extract.exe extract --config configs/parser/esl.json
```

The extractor creates `data/parsed/v2-audited/<demo SHA256>/`. It refuses existing outputs; select a new `--out` directory when intentionally reprocessing. A parse failure remains in a `.partial-*` directory and is never published as a completed dataset. A quality failure returns a nonzero exit code while retaining the extracted evidence and `validation.json`.

Validate or inspect one result:

```powershell
$parsed = 'data/parsed/v2-audited/f3695a7131a4c70eeae3dbdaab63a0e1d2510c987f2a75a092071983c747c773'
.\bin\cs2-extract.exe validate --parsed $parsed
.\.venv\Scripts\cs2-data.exe normalize --parsed $parsed --out 'data/normalized/new-run/<demo SHA256>'
.\.venv\Scripts\cs2-data.exe render-jobs --parsed $parsed --out 'data/jobs/first.json' --limit 1 --width 640 --height 360
```

`first.json` contains one job object. To request all eligible intervals, omit `--limit 1` and use a `.jsonl` filename. These jobs exclude warmup, freeze time, dead intervals, and known pauses; unobserved pause state is explicitly flagged.

Prepare/check the Reka integration:

```powershell
python tools/renderer/doctor.py
python tools/renderer/setup.py --go .tools/go/bin/go.exe --build
tools/renderer/build/dem-render.exe job --spec data/jobs/first.json --output data/rendered/first
```

The last command is a dry run. A checked-in-source implementation and a real-demo dry run are verified; game rendering remains untested. Follow [RENDERING.md](docs/RENDERING.md) for execution on a compatible Linux worker and the remaining capture instrumentation. The installed Windows game is newer than the pinned plugin's supported build.

Once a real clip has verified POV, measured capture boundaries, and recorded video PTS:

```powershell
.\.venv\Scripts\cs2-data.exe align --parsed $parsed --clip data/rendered/clip.json --timing data/rendered/frames.jsonl --normalized 'data/normalized/v2/<demo SHA256>' --out data/aligned/v1/clip
.\.venv\Scripts\cs2-data.exe viewer --aligned data/aligned/v1/clip --out data/viewer/clip.html
```

Open the generated HTML and select its local MP4. Unverified renderer outputs cannot pass the alignment gate. Even measured packet-time alignment needs execution-clock and visual checks before it becomes training-ready; see [ALIGNMENT.md](docs/ALIGNMENT.md).

## Fresh environment

Install Go 1.27+ and Python 3.10+ (3.11/3.12 recommended). Python 3.10 compatibility lets the supplied machine run preparation without replacing its Python installation.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
go -C tools/usercmd-extractor build -o ../../bin/cs2-extract.exe ./cmd/cs2-extract
```

`scripts/build.ps1` also uses a repository-local `.tools/go/bin/go.exe` when present, and keeps Go caches inside `.cache/`. PowerShell execution policies may require invoking the script with the appropriate local script policy; the direct `go build` command above works without it. `ffmpeg`/`ffprobe` are required for video rendering and validation, and the Linux worker has additional requirements in [RENDERING.md](docs/RENDERING.md).

## Checks and data rules

```powershell
go -C tools/usercmd-extractor test ./...
.\.venv\Scripts\python.exe -m pytest tests -q
```

The tests exercise protobuf/Parquet preservation, corrupt-demo publication prevention, quality checks, yaw wrap and reset boundaries, frame interval assignment, invalid timing/identity rejection, render interval splitting, and the viewer data path. Synthetic video-probe fixtures do not replace a CS2 render integration test.

Raw demos and canonical tables are immutable. Optional protobuf field presence is preserved, including sparse fields whose protobuf default is zero. Demo packet ticks, parser frame ordinals, and executed server ticks have separate meanings. Available GOTV state is never silently interpolated to 64 Hz. Exact command row IDs are retained when several commands fall in a frame interval; no nearest-tick labels or manufactured frames are produced.

All three maps share `match_id=esl-s52-eu-cup6-misa-mouz-nxt` so a later train/test split can keep this series together. Large demos, datasets, toolchains, and third-party source checkouts are ignored by Git. [DATA_SCHEMA.md](docs/DATA_SCHEMA.md) describes the artifact contracts.
