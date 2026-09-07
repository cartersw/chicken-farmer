# Dataset artifacts

The Go extractor writes immutable `usercmd.parquet`, `player_state.parquet`,
`rounds.parquet`, and `manifest.json`. Run `cs2-extract validate` before producing
derived data. Every stage writes to a new location and refuses existing outputs.
Derived stages require complete, nonpartial parses of supported schema versions
and verify the source Parquet hashes against the extractor manifest. Output
directory locks prevent competing publishers. Files are written under temporary
names and the completion report/manifest is published last; consumers require
that report and verify its hashes. Failed runs can leave hidden `.partial-*`
files for diagnosis, which are never accepted as completed artifacts. A process
crash can leave `.cs2-data.lock`; remove a stale lock only after checking that
its producing process has stopped.

`demo_id` identifies the SHA256 of the source demo. `command_row_id` is the stable
int64 raw-command identity, increasing in file order. It is not a per-player row
index and can be noncontiguous after filtering. Steam IDs are uint64 in Parquet
and decimal strings in renderer JSON to avoid JavaScript integer truncation.

The raw command schema follows the engineering handoff and preserves nullable
fields, all three button masks, movement axes, mouse counts, view angles,
weapon selection, nested subtick moves, nested input history, and the reconstructed
protobuf. Consult the generated Parquet schema for exact parser column types.
Absent fields are null. Parser schema v2 also records `base_present`,
`buttons_present` and `viewangles_present`, distinguishing an absent message
from default-valued scalars inside a present message. The parser manifest records
the parser revision, schema version and provenance.

## Clocks

| Field | Meaning |
| --- | --- |
| `demo_tick` | `GameState.IngameTick()`: tick in the demo packet header; the replay clock |
| `demo_frame` | Parser frame counter; distinct from a tick and from video frames |
| `server_tick_executed` | Raw nonnullable UserCmd event execution tick; zero may represent an unavailable upstream value; a different clock until its relationship is validated |
| `client_tick` | Client command clock |
| `demo_time_seconds` | `demo_tick / tick_rate` computed by the extractor; not a measured video presentation timestamp |
| Round `start_tick`, `freeze_end_tick`, `end_tick` | Demo/replay tick clock |

Keep these clocks separate. Raw commands are aligned on their recorded packet
demo tick in this implementation. Command batching can make packet arrival differ
from execution. Output remains `training_ready: false` pending visual timing
validation; preserving these records does not establish execution-perfect labels.

## `normalized_actions.parquet`

One row for every canonical command. The output contains `demo_id`,
`command_row_id`, `round_id`, `steam_id`, `player_slot`, `demo_tick`,
`previous_command_row_id`, nullable float32 `delta_yaw_deg` / `delta_pitch_deg`,
`aim_valid`, `reset_reason`, unmodified `mousedx_raw` / `mousedy_raw`, and
`mousedx_effective` / `mousedy_effective`.
Parquet schema metadata identifies the demo, stage and processing version.

The angle difference is attached to the **current command**:

```text
delta_yaw[current] = wrap(yaw[current] - yaw[previous])  # [-180, 180)
delta_pitch[current] = pitch[current] - pitch[previous]
```

The first command, identity/round/pawn changes, missing angles, command-number
gaps, client-clock resets, known dead states and demo-tick gaps produce null targets with explicit
mask reasons. Repeated packet ticks are allowed because one packet can contain
multiple commands. The default maximum command gap is four demo ticks.
Do not interpret a masked target as zero motion. Respawns without a changed
pawn handle still require the alive-window filter before training when alive
metadata is unavailable.

Normalizer version 2 preserves nullable raw scalar presence and separately
applies protobuf's scalar default zero **only** when the parent message is known
present. For example, `mousedx_raw=null, base_present=true` yields
`mousedx_effective=0`; absent or unknown base presence yields a null effective
value. The same rule applies to angle components with `viewangles_present=true`
when computing angular differences. Null raw mouse fields are not themselves
evidence of missing/corrupt actions. Old schema v1 data cannot resolve these
cases and is treated conservatively.

`normalization_report.json` records reset counts and bounded, deterministic
reservoir estimates of median degrees per nonzero mouse count, median absolute
deviation and sample counts per player. Those estimates are diagnostic and do
not identify DPI or the player's exact sensitivity.

Button masks remain canonical integers. Semantic key decoding must be checked
against current replay behavior; library constants alone are not that check.

## Renderer jobs

`cs2-data render-jobs` writes one JSON object per eligible alive interval:

```json
{
  "schema_version": 1,
  "demo_id": "sha256-of-demo",
  "demo_path": "C:/demos/match.dem",
  "clip_id": "2bd83b604df54713a74aefda",
  "round_id": 1,
  "steam_id": "76561198000000001",
  "player_slot": 2,
  "spectator_user_id": 7,
  "start_demo_tick": 1000,
  "end_demo_tick": 2000,
  "fps": 32,
  "width": 1280,
  "height": 720,
  "timing_clock": "demo_tick",
  "renderer_profile": "render-v1",
  "training_ready": false
}
```

The end is exclusive. `spectator_user_id` is the parser's `UserID & 255` value;
it is distinct from the command player slot. The renderer adapter converts this
to its spectator command where needed. Stable hexadecimal clip IDs include demo,
player, round, interval and render profile. Jobs also contain optional map/team
and `pause_state_verified`. Unknown pause state remains visible in this flag.

Generation excludes warmup, freeze time, dead states, known pauses, unresolved
identity, incomplete rounds and state gaps. Missing freeze-end boundaries exclude
the round. By default state must be consecutive in demo ticks. Jobs with unknown
pause metadata require review. These jobs request rendering; they contain no
claim that a video exists or timing has been measured.

Every window also audits the reconstructed command stream for the exact player
and round. By default jobs require at least one command and no internal or
boundary gap exceeding four demo ticks. `command_coverage` records counts,
observed tick fraction, first/last command tick and maximum gap. Renderer debug
jobs can explicitly allow missing commands with `--allow-incomplete-commands`;
their incomplete coverage remains recorded. All jobs and normalized outputs
remain `training_ready: false`, with source parser warning/validation provenance.
Successful reconstruction of part of a demo does not mean the whole demo has
complete action coverage.

The first audited three-demo run produced 4,933,316 normalized command rows and
3,511,650 valid alive angular transitions. Those counts describe derived
diagnostics, not training-ready samples: all outputs remain
`training_ready: false`. Baseline-missing parser warnings, subtick/history
validation findings, and absent measured replay frames still require attention.
See [INITIAL_RUN.md](INITIAL_RUN.md) for per-demo results and limitations.

## Aligned outputs

`frame_alignment.parquet` stores clip/player/demo identity, `frame_index`,
`pts_seconds`, measured float64 `source_demo_tick_start` and
`source_demo_tick_end`, and `command_row_ids: list<int64>`.

`clip_command_start` and `clip_command_end` are half-open **dense local indexes**
into `aligned_commands.parquet`. They are never raw row IDs. The latter table
preserves all selected canonical columns, adds `clip_id`, `frame_index` and
`clip_command_index`, and optionally includes normalized aim targets. Empty
frames have an empty ID list and equal local start/end indexes. Multiple commands
per frame are retained; there is no assumed two-command limit.

`alignment_manifest.json` records versions, video identity, counts, the parsed
directory for debugging, the packet-arrival timing basis and pending validation.
See [ALIGNMENT.md](ALIGNMENT.md) for the capture contract and validation gates.
