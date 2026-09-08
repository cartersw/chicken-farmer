# CS2 Visual Imitation Learning — Engineering Handoff

**Status:** implementation specification  
**Research focus:** professional CS2 demos from HLTV, high-fidelity `UserCmd` extraction, synchronized first-person rendering, behavioral cloning, and real-time visual control  
**Last updated:** 2026-09-05  

**Current trainer decisions (2026-09-08):** see
[the trainer design](docs/TRAINER_DESIGN.md) and
[implemented tensor contract](docs/TRAINING_DATASET.md). They supersede this
handoff's illustrative trainer input/storage choices. The decided baseline is
640x360, full-color RGB, 8 bits per channel, eight consecutive image-only frames
at 32 FPS, with rolling decompression, frame reuse and background prefetching.
Train this baseline first; format adjustments depend on a lack of learning
progress and observed failures. The original milestone plan below remains historical.

---

## 0. Executive summary

The project is to train a model that observes a **first-person CS2 video stream** and predicts the actions a human player would issue: mouse/camera movement, movement controls, firing, jumping, crouching, walking, reloading, weapon selection, and other discrete actions.

The training source is **professional Counter-Strike 2 match demos downloaded from HLTV**. Each server demo contains the state and actions for all players in the match, so a single 5v5 match can yield up to ten synchronized player trajectories. The goal is not to infer actions from YouTube footage. The goal is to reconstruct the actual CS2 command stream from the modern demo format and align it with rendered first-person frames.

The central technical idea is:

```text
HLTV professional .dem
        |
        +-------------------------+
        |                         |
        v                         v
Full UserCmd parser         POV renderer
(demoinfocs v6)            (fork/adapt Reka)
        |                         |
        v                         v
64 Hz action stream         first-person video
+ subtick events            32/64 fps
+ world state                     |
        |                         |
        +-----------+-------------+
                    v
            synchronization layer
                    |
                    v
        high-fidelity training dataset
                    |
                    v
          temporal visual policy
                    |
                    v
        predicted CS2 action sequence
```

The most important design decision is that **the reconstructed `UserCmd` stream is the canonical action source**. Reka's `cs2-dem-renderer` is valuable for deterministic first-person rendering, worker orchestration, ffmpeg encoding, and player-round segmentation, but this project should not rely on a pre-existing per-frame mouse label if a full modern `UserCmd` can be recovered directly.

The policy should initially be trained with pure behavioral cloning. Reinforcement learning is optional later; it is not part of the first implementation.

### Research/deployment boundary

The desired research capability is a real-time policy that can observe the normal game image and produce mouse/keyboard-like actions. Development and evaluation should be performed in **local, offline, or explicitly authorized private CS2 environments**. This specification does not include anti-cheat bypass, concealment, or deployment automation for Valve public matchmaking. Valve's Steam terms prohibit unauthorized bots/automation in gameplay, so a coding agent should not implement anti-cheat evasion or public-matchmaking deployment logic.

---

# 1. Verified technical facts that drive the design

## 1.1 Modern CS2 demos can contain complete server-recorded user commands

`demoinfocs-golang` v6 introduced full CS2 user-command reconstruction. Its `events.UserCmd` represents a complete command reconstructed from either a full `CMsgServerUserCmd` payload or the newer delta-encoded `delta_data` payload.

A reconstructed command includes, among other fields:

- player / player slot
- command number
- client tick
- server tick executed
- button states
- view angles
- `forwardmove`
- `leftmove`
- `upmove`
- weapon selection
- `mousedx`
- `mousedy`
- subtick move records
- input history

This matters because recent CS2 demos increasingly use delta-encoded user-command payloads. A parser that only understands the older full payload can silently lose attack/movement/mouse information. The project should therefore standardize on **demoinfocs v6 full UserCmd parsing**, not an older parser path.

## 1.2 Valve's protobuf explicitly contains mouse and subtick fields

Current `CBaseUserCmdPB` contains:

```text
buttons_pb
viewangles
forwardmove
leftmove
upmove
impulse
weaponselect
mousedx
mousedy
subtick_moves
```

Each `CSubtickMoveStep` may contain:

```text
button
pressed
when
analog_forward_delta
analog_left_delta
pitch_delta
yaw_delta
```

`CSGOUserCmdPB` also contains `input_history`, including render/player tick information and view-angle history.

These fields should be preserved even if v1 of the model does not use all of them.

## 1.3 Reka already solved much of the POV-rendering problem

Reka's open-source `cs2-dem-renderer` currently performs:

1. demo parsing / player-round segmentation;
2. deterministic CS2 first-person replay;
3. frame capture;
4. ffmpeg video encoding;
5. Parquet metadata generation;
6. batch worker processing with deduplication.

It is therefore a strong base for the **rendering subsystem**.

However, the project's own parser must remain the authoritative action source. Treat Reka's action metadata as a convenience/validation channel, not the canonical label stream, until its exact derivation has been audited for the current CS2 build.

## 1.4 HLTV is an appropriate source of professional server demos

HLTV match pages provide downloadable GOTV/CS2 demos for professional matches. For this project, data acquisition should be terms-compliant and should not attempt to bypass authentication, rate limits, bot protection, or access controls. The pipeline itself should accept a directory of already-obtained `.dem` files.

---

# 2. Project objective

Train a policy:

```text
visual history + previous actions -> next action sequence
```

where the visual input is what a normal player could see on screen and the output approximates the player's control stream.

The final model should be capable of learning, to some useful degree:

- leaving spawn;
- navigating common routes;
- looking at relevant angles;
- maintaining plausible crosshair placement;
- moving with WASD;
- counter-strafing behavior;
- tracking/flicking toward visible opponents;
- shooting;
- crouching/jumping/walking where appropriate;
- reloading;
- switching weapons;
- participating in normal competitive-round flow.

The first milestone is **not** “strong CS2 AI.” The first milestone is a visually driven policy that behaves coherently for sustained periods in an authorized test environment.

---

# 3. Non-goals for v1

Do not block the project on these:

- full tactical team coordination;
- voice/audio understanding;
- grenade lineup optimization;
- economy planning across many rounds;
- perfect bomb strategy;
- self-play reinforcement learning;
- direct memory reading from the CS2 client;
- network packet manipulation;
- anti-cheat interaction or evasion;
- public matchmaking deployment automation.

Those are separate phases or out of scope.

---

# 4. Recommended repository structure

Use a monorepo so data tooling and model code remain versioned together.

```text
cs2-visual-policy/
|
|-- README.md
|-- PROJECT_SPEC.md                 # copy/rename this document
|-- .gitignore
|-- .env.example
|-- Makefile
|-- docker-compose.yml              # optional local services only
|
|-- configs/
|   |-- parser/
|   |   `-- default.yaml
|   |-- renderer/
|   |   |-- render-32fps.yaml
|   |   `-- render-64fps.yaml
|   |-- dataset/
|   |   `-- v1.yaml
|   `-- train/
|       |-- bc-small.yaml
|       |-- bc-base.yaml
|       `-- bc-large.yaml
|
|-- data/
|   |-- raw-demos/                  # local dev only; normally external storage
|   |-- parsed/
|   |-- rendered/
|   |-- aligned/
|   |-- shards/
|   `-- manifests/
|
|-- tools/
|   |-- usercmd-extractor/          # Go 1.27+
|   |   |-- cmd/cs2-extract/
|   |   |-- internal/demo/
|   |   |-- internal/schema/
|   |   |-- internal/validate/
|   |   |-- go.mod
|   |   `-- go.sum
|   |
|   |-- renderer/                   # fork/submodule of Reka renderer
|   |   |-- dem-render/
|   |   |-- cs2-server-plugin/
|   |   `-- patches/
|   |
|   |-- aligner/                    # Python preferred
|   |   |-- align.py
|   |   |-- schema.py
|   |   `-- validate_alignment.py
|   |
|   |-- dataset-builder/
|   |   |-- build_manifest.py
|   |   |-- shard.py
|   |   |-- split.py
|   |   `-- stats.py
|   |
|   `-- viewer/
|       `-- synchronized debug viewer
|
|-- training/
|   |-- datasets/
|   |   |-- video_action_dataset.py
|   |   `-- transforms.py
|   |-- models/
|   |   |-- visual_encoder.py
|   |   |-- temporal_policy.py
|   |   |-- action_heads.py
|   |   `-- policy.py
|   |-- losses/
|   |   `-- behavior_cloning.py
|   |-- train.py
|   |-- evaluate.py
|   `-- export.py
|
|-- runtime/
|   |-- capture/
|   |   `-- screen_capture.py
|   |-- policy_server/
|   |   `-- inference.py
|   |-- controller/
|   |   |-- interface.py            # abstract action sink
|   |   `-- local_test_adapter.py   # authorized/local testing only
|   `-- telemetry/
|       `-- recorder.py
|
|-- tests/
|   |-- fixtures/
|   |   `-- small-current-demo.dem
|   |-- parser/
|   |-- alignment/
|   |-- dataset/
|   `-- model/
|
`-- docs/
    |-- DATA_SCHEMA.md
    |-- RENDERING.md
    |-- ALIGNMENT.md
    |-- MODEL.md
    |-- EVALUATION.md
    `-- SOURCES.md
```

---

# 5. Technology stack

## 5.1 Demo parsing

**Language:** Go  
**Required parser:** `github.com/markus-wa/demoinfocs-golang/v6`  
**Go version:** follow v6's current minimum requirement; as of this document, the published v6 documentation requires Go 1.27.

Reasons:

- native support for recent CS2 demos;
- full `UserCmd` reconstruction;
- delta-encoded `CMsgServerUserCmd` support;
- game-state access;
- fast concurrent parsing;
- generated Valve protobuf types.

## 5.2 Rendering

Base implementation:

- Reka `cs2-dem-renderer` fork;
- Go renderer process;
- C++ CS2 server plugin;
- CMake;
- ffmpeg;
- hardware video encode where available;
- Steam/CS2 installed on each renderer worker.

Target operating system for a render farm: **Linux** unless there is a strong reason to standardize on Windows. Reka's renderer is designed primarily for Linux.

## 5.3 Dataset processing

**Python 3.11+ / 3.12+**  
Recommended libraries:

- `pyarrow`
- `polars` or `pandas`
- `numpy`
- `pydantic`
- `orjson`
- `webdataset`
- `fsspec`
- `s3fs` if using S3-compatible storage
- `ffmpeg` / `ffprobe`
- PyAV or another video reader for debugging

Prefer Polars/PyArrow for large metadata tables.

## 5.4 Model training

- PyTorch 2.x
- torchvision
- CUDA
- AMP with bf16 where supported
- DistributedDataParallel for multi-GPU
- `torch.compile` only after correctness is established
- TensorBoard or Weights & Biases for experiments
- Hydra/OmegaConf or simple YAML/Pydantic configuration

## 5.5 Training-data storage

Development:

- local NVMe SSD

Scale:

- S3-compatible object storage, e.g. S3/MinIO/R2-compatible system where economics make sense;
- manifest/index database can remain simple initially: Parquet + SQLite/PostgreSQL.

Do not store billions of independent PNG images.

---

# 6. End-to-end pipeline

## Phase A — ingest demos

Input:

```text
*.dem
```

Each demo receives a stable `demo_id`, preferably:

```text
sha256(file bytes)
```

The manifest should record:

```text
demo_id
source
source_match_id
source_url_or_reference
file_name
sha256
file_size
match_date
map
server_name
patch_version if detectable
ingested_at
parse_status
render_status
```

Do not encode the assumption that every demo is parseable forever. CS2 updates can make replay/render compatibility version-sensitive.

## Phase B — parse metadata and complete UserCmd streams

For each demo:

1. parse header;
2. identify all human players and player slots;
3. identify rounds;
4. identify spawn/death/alive windows;
5. configure demoinfocs with `UserCmdParsingFull`;
6. register the `events.UserCmd` handler;
7. write every reconstructed command;
8. sample relevant game state per server tick;
9. write round/event metadata;
10. run validation.

Do not discard delta-encoded commands. demoinfocs v6 should return a merged command snapshot.

## Phase C — render each player-round

For each valid player-round interval:

1. launch/load the demo through the renderer;
2. move the replay camera to that exact player's POV;
3. seek/start at the round/player interval;
4. render deterministic frames;
5. encode directly to video;
6. record exact clip timing metadata;
7. produce one clip per player-round.

Recommended v1 target:

```text
resolution: 640x360 or 1280x720 source, optionally train with resize
render FPS: 32
server action stream: retain native 64 Hz commands
codec: HEVC/H.264 depending decoder throughput
```

Why 32 fps first:

- CS2's server simulation is 64 tick;
- 32 visual frames/sec gives a clean nominal two command-ticks per frame;
- much cheaper than 64 fps;
- still materially more useful for mechanics than very low frame rates.

After v1, benchmark 64 fps rendering/training on a subset. Do not assume 64 fps is worth approximately double the visual compute until measured.

## Phase D — synchronize frames and commands

The alignment layer is one of the most important components in the project.

For every rendered frame, define a deterministic interval in demo time:

```text
frame f covers [time_f, time_f+1)
```

At 32 fps:

```text
frame duration = 31.25 ms
```

At a nominal 64 server ticks/sec, that interval spans approximately two server command ticks.

Do not merely join by nearest tick and discard the remainder. Preserve the original action stream and create derived training windows.

Recommended representation per video frame:

```text
frame_index
frame_pts_seconds
source_tick_start
source_tick_end
commands[]      # all reconstructed UserCmds executed in this frame interval
```

A v1 policy may predict two 64-Hz action slots for the next 32-Hz visual observation:

```text
image/history -> [action_t0, action_t1]
```

If exact rendering timing differs from the nominal tick mapping, the aligner must use measured/recorded timestamps from the render pipeline rather than assuming an ideal ratio.

## Phase E — package training samples

Convert long player-round clips into randomly sampleable temporal windows.

Example sample:

```text
context duration: 1.0 s
visual fps:       32
input frames:     32
pred horizon:     next 2 or 4 action ticks
```

The training loader should sample windows dynamically rather than creating every possible overlapping clip on disk.

---

# 7. Canonical data schema

The project should maintain **raw canonical tables** and separate **derived training tables**.

Never overwrite raw extracted information when changing a normalization strategy.

## 7.1 `usercmd.parquet`

One row per reconstructed UserCmd.

Recommended fields:

```text
demo_id: string
match_id: string | null
round_id: int
steam_id: uint64 | null
player_slot: int32
team: enum

command_number: int32
client_tick: int32
server_tick_executed: int32

demo_time_seconds: float64

buttonstate1: uint64
buttonstate2: uint64
buttonstate3: uint64

forwardmove: float32
leftmove: float32
upmove: float32

mousedx_raw: int32
mousedy_raw: int32

view_pitch: float32
view_yaw: float32
view_roll: float32

weaponselect: int32
impulse: int32
pawn_entity_handle: uint32

subtick_moves: list<struct{
    button: uint64?
    pressed: bool?
    when: float32?
    analog_forward_delta: float32?
    analog_left_delta: float32?
    pitch_delta: float32?
    yaw_delta: float32?
}>

input_history: list<struct{
    render_tick_count: int32?
    render_tick_fraction: float32?
    player_tick_count: int32?
    player_tick_fraction: float32?
    view_pitch: float32?
    view_yaw: float32?
    frame_number: int32?
}>

attack1_start_history_index: int32?
attack2_start_history_index: int32?
```

Also consider storing the serialized reconstructed protobuf bytes as an optional column or sidecar for future reprocessing.

## 7.2 `player_state.parquet`

One row per player per relevant server tick.

```text
demo_id
round_id
server_tick
steam_id
player_slot
team
alive

position_x
position_y
position_z

velocity_x
velocity_y
velocity_z

view_pitch
view_yaw

health
armor
helmet
scoped
flash_duration

active_weapon
ammo_clip
ammo_reserve

on_ground
crouching
walking
```

Only add state fields verified to be consistently parseable. Null is better than fake data.

## 7.3 `rounds.parquet`

```text
demo_id
round_id
round_number
map
start_tick
freeze_end_tick
end_tick
winner_team
bomb_planted
bomb_site
```

## 7.4 `clips.parquet`

```text
clip_id
demo_id
round_id
steam_id
player_slot
map
team
video_uri
fps
width
height
start_server_tick
end_server_tick
num_frames
duration_seconds
video_sha256
```

## 7.5 `frame_alignment.parquet`

One row per frame.

```text
clip_id
frame_index
pts_seconds
source_time_seconds
server_tick_start
server_tick_end
command_row_start
command_row_end
```

This table makes frame/action synchronization auditable.

---

# 8. Mouse-action representation and sensitivity normalization

This is a critical modeling issue.

## 8.1 Preserve raw mouse deltas

The demo can expose:

```text
mousedx_raw
mousedy_raw
```

These are valuable because they are closer to the actual player command than a camera-motion estimate.

However, raw mouse counts are **not automatically comparable between players** because players use different client sensitivities and physical mouse DPI. A normal server demo should not be assumed to contain the player's physical DPI, and sensitivity should not be assumed to be reliably recoverable as explicit metadata.

Therefore, do not use raw `mousedx/mousedy` as the only primary aim target across all players.

## 8.2 Store camera-space angular deltas as the canonical normalized aim target

For each consecutive reconstructed command:

```text
delta_yaw   = wrap_angle(view_yaw[t+1] - view_yaw[t])
delta_pitch = view_pitch[t+1] - view_pitch[t]
```

This yields a player-independent quantity:

```text
degrees of intended/observed camera rotation
```

Recommended dataset fields:

```text
mousedx_raw
mousedy_raw

delta_yaw_deg
delta_pitch_deg
```

The model can primarily learn:

```text
pixels/history -> delta_yaw_deg, delta_pitch_deg
```

while also predicting raw mouse deltas through an auxiliary head.

This preserves the high-fidelity UserCmd data without forcing the network to average incompatible sensitivities.

## 8.3 Estimate effective per-player sensitivity only as an optional derived statistic

For commands with nonzero mouse input and clean view transitions, estimate a robust relationship:

```text
yaw_delta ~= kx * mousedx_raw
pitch_delta ~= ky * mousedy_raw
```

Use robust median/regression over many commands, not a single command.

Store:

```text
estimated_deg_per_mouse_x
estimated_deg_per_mouse_y
estimate_confidence
num_samples
```

This is an **effective conversion factor**, not a claim about physical DPI or the exact player's settings.

## 8.4 Runtime conversion

For authorized/local testing, configure one fixed known control sensitivity and convert the model's predicted angular action into that environment's command/input scale.

Thus training can remain invariant to individual pro sensitivity settings.

---

# 9. Button and movement representation

Do not reduce all movement immediately to W/A/S/D booleans.

Preserve both:

```text
button masks
forwardmove
leftmove
upmove
```

Derived labels can include:

```text
move_forward
move_backward
move_left
move_right
jump
crouch
walk
attack1
attack2
reload
use
```

The first model may use discrete key/button heads, but the analog movement fields are useful validation/auxiliary signals.

Do not hard-code button-bit mappings from an old CS:GO reference without a current test. Build a small current-demo test fixture that verifies each decoded action against observable replay behavior.

---

# 10. Subtick strategy

Do not discard subtick information.

V1 does not need to predict every subtick event directly. Store it canonically, then derive simpler 64-Hz labels.

Recommended phases:

### V1

Predict 64-Hz command-level actions.

### V2

Add subtick auxiliary targets:

```text
button transition type
when in tick [0,1)
analog movement delta
pitch/yaw subtick delta
```

### V3

If measurable improvement exists, use an event-based action decoder that emits variable subtick events.

The reason to defer this is engineering complexity, not lack of value.

---

# 11. Renderer design

## 11.1 Fork Reka instead of rewriting replay capture from scratch

Start from:

```text
reka-ai/cs2-dem-renderer
```

Keep the fork narrow.

Required modifications are likely to include:

- support for the current CS2 build;
- output exact timing anchors required by this project's aligner;
- optional 32-fps and 64-fps presets;
- preserve the visual configuration desired at policy runtime;
- expose stable worker status/metrics;
- produce deterministic output filenames based on `demo_id/round/player` rather than random-only IDs;
- integrate with the project's manifest.

## 11.2 Match training visuals to inference visuals

This is important.

If the eventual policy sees:

- HUD;
- crosshair;
- viewmodel;
- ammo information;
- damage indicators;

then the training render should preserve those signals unless there is a deliberate reason not to.

A clean research dataset optimized for world modeling is not automatically optimized for a deployed visual control policy.

Create and lock a `visual_profile`:

```text
resolution
aspect ratio
FOV
HUD visibility/scale
crosshair style
viewmodel settings
brightness/gamma where controllable
anti-aliasing
motion blur
```

The runtime capture environment should be as close to this profile as possible.

## 11.3 Renderer compatibility is version-sensitive

Reka explicitly warns that CS2 updates can break the server plugin. Treat rendering as reproducible infrastructure with versioning:

```text
renderer_commit
cs2_build
plugin_commit
render_profile_version
ffmpeg_version
```

Store those in every clip manifest.

---

# 12. Render-worker architecture

Rendering is likely to be the main data-generation bottleneck.

Design it as a distributed queue from the start.

```text
                   render_jobs
                        |
              +---------+---------+
              |                   |
              v                   v
         render worker 1      render worker N
              |                   |
              +---------+---------+
                        |
                        v
                  object storage
                        |
                        v
                   validator
```

Each job:

```json
{
  "demo_id": "...",
  "demo_uri": "...",
  "round_id": 12,
  "steam_id": 123,
  "start_tick": 45678,
  "end_tick": 50123,
  "render_profile": "32fps-v1"
}
```

Possible queue technologies:

- simplest: PostgreSQL job table with `FOR UPDATE SKIP LOCKED`;
- Redis queue;
- RabbitMQ;
- cloud-native queue.

For a first local implementation, use **PostgreSQL or even a filesystem/SQLite queue**. Do not introduce Kubernetes before one-machine rendering is correct.

Worker state:

```text
pending
running
succeeded
failed_retryable
failed_terminal
```

A job must be idempotent.

---

# 13. Data-quality validation

Do not allow “parsed successfully” to mean “good training data.”

Every demo should pass a validation report.

## 13.1 Demo-level checks

- map recognized;
- expected match duration;
- valid rounds;
- multiple human players;
- stable player-slot to SteamID mapping where available;
- no catastrophic parser errors.

## 13.2 UserCmd checks

For each active player:

- command count is nontrivial;
- server ticks are monotonic or explainably ordered;
- raw mouse values have nonzero variance;
- view angles change;
- movement/button activity exists;
- attack events exist where weapon-fire events exist;
- delta-encoded commands are reconstructed;
- no long unexplained command gaps while alive.

Produce metrics such as:

```text
cmds_per_alive_second
pct_zero_mouse
pct_missing_viewangles
pct_missing_buttons
max_cmd_gap_ticks
num_subtick_records
num_input_history_entries
```

## 13.3 Render checks

- video decodes;
- expected frame count within tolerance;
- no black frame run;
- camera belongs to expected player;
- player-round interval correct;
- clip duration matches manifest.

## 13.4 Alignment checks

Build a visual/debug viewer showing simultaneously:

```text
video frame
server tick
mouse dx/dy
normalized yaw/pitch delta
WASD/button state
position
weapon
```

Manually inspect at least 50 random clips before large-scale rendering.

Also implement automated consistency tests:

- integrated predicted angular deltas should roughly track recorded view-angle trajectory;
- firing label should visually coincide with firing animation/events within timing tolerance;
- movement labels should correlate with velocity changes after accounting for physics.

---

# 14. Dataset filtering

Initial training should include only high-confidence intervals.

Exclude or separately label:

- warmup;
- freeze time;
- dead/spectating intervals;
- technical pauses;
- disconnects;
- invalid player mappings;
- parser gaps;
- render corruption.

Do **not** automatically throw away quiet gameplay. Competitive CS requires learning angle holding, waiting, rotations, and non-combat movement. If the goal is competitive play, those are valid demonstrations.

Create optional sampling weights rather than destructive filtering.

---

# 15. Dataset split strategy

Never randomly split individual frames from the same match between train and validation.

Minimum split unit:

```text
entire match/demo
```

Better:

```text
entire event/date block
```

This reduces leakage from identical teams, map situations, and repeated round structures.

Suggested starting split:

```text
train: 90%
val:    5%
test:   5%
```

Stratify by map where possible.

Keep an additional **future-patch holdout** for robustness testing.

---

# 16. Training data packaging

Raw storage should remain:

```text
MP4 + Parquet
```

For high-throughput training, create shards.

Recommended options:

### Option A — WebDataset

Tar shards containing:

```text
clip/sample metadata
encoded video chunk or image sequence
aligned action arrays
```

Advantages:

- sequential object-store reads;
- easy distributed sharding;
- common large-scale training pattern.

### Option B — MP4 player-round files + global Parquet index

Advantages:

- simplest to debug;
- avoids repacking video repeatedly.

Use this first unless I/O becomes the bottleneck.

Do not decode full rounds when training on 1-second windows if the chosen video reader can seek efficiently enough. Benchmark this; random MP4 seeking can itself become expensive.

A later optimization is pre-cut encoded chunks of 2–8 seconds.

---

# 17. Observation representation

V1 should be **visual-only plus policy history**, not privileged world state.

Input at timestep `t`:

```text
current and recent RGB frames
previous actions
optional previous hidden state
```

Do not feed enemy coordinates or parsed demo state into the deployed policy if that information will not exist at runtime.

Parsed world state is still extremely useful for:

- evaluation;
- filtering;
- auxiliary representation learning;
- teacher models;
- debugging.

## 17.1 Recommended visual context

Start with:

```text
visual fps: 32
history: 0.5–1.0 second
```

Do not initially feed 32 full-resolution frames through an expensive encoder independently. Practical approaches:

- encode a subset of recent frames;
- temporal stride;
- lightweight CNN per frame + temporal transformer/GRU;
- cache visual embeddings inside the batch when possible.

Example:

```text
frames at t-15, t-12, t-9, t-6, t-3, t
+ previous action history
```

Then expand context if necessary.

---

# 18. V1 model architecture

Use a simple architecture that makes errors easy to diagnose.

```text
RGB frame sequence
      |
      v
visual encoder
(ResNet-18 / ConvNeXt-Tiny class)
      |
      v
frame embeddings
      |
      +---- previous action embeddings
      |
      v
temporal module
(GRU or small Transformer)
      |
      v
shared policy representation
      |
      +----------------+----------------+----------------+
      |                |                |                |
      v                v                v                v
aim head        movement head     button heads     weapon head
```

Do not begin with a huge foundation model.

A small model is enough to validate:

- synchronization;
- loss design;
- aim-label normalization;
- action imbalance;
- temporal context;
- closed-loop behavior.

## 18.1 Visual encoder

V1 candidates:

- ResNet-18;
- ConvNeXt-Tiny;
- small ViT only if compute allows.

Pretrained initialization may accelerate basic visual feature learning, but domain-specific CS2 fine-tuning will dominate.

## 18.2 Temporal core

Start with one of:

- 1–2 layer GRU;
- 4–6 layer small causal Transformer.

A GRU is easier for the first closed-loop prototype. A transformer becomes more attractive as dataset/model scale grows.

---

# 19. Action target design

A single MSE head for every action is not sufficient.

Use separate heads.

## 19.1 Aim head

Primary target:

```text
delta_yaw_deg
delta_pitch_deg
```

Recommended v1 loss:

- Huber / Smooth L1 after clipping extreme outliers;
- separately normalize X/Y distributions.

Because aim actions are multimodal, evaluate a better head later:

- discretized angular bins;
- mixture-density/Gaussian-mixture head;
- autoregressive action tokens.

Plain regression may average across multiple plausible actions and produce weak “mean” camera motion.

## 19.2 Raw mouse auxiliary head

Predict:

```text
mousedx_raw
mousedy_raw
```

as auxiliary supervision, possibly conditioned on estimated per-player effective sensitivity during training.

Do not require this head at inference.

## 19.3 Movement head

Two possibilities:

### Discrete keys

```text
W A S D
```

independent Bernoulli logits.

### Analog command values

```text
forwardmove
leftmove
```

continuous regression.

Best v1 approach: predict both. Use discrete buttons for the controller and analog values as auxiliary supervision.

## 19.4 Button heads

Independent logits for:

```text
attack1
attack2
jump
crouch
walk
reload
use
```

Some actions are extremely imbalanced. Use class weighting/focal loss carefully; do not over-weight rare actions until their precision is checked.

## 19.5 Weapon selection

Start with either:

- no explicit weapon selection head and learn common key changes later; or
- categorical weapon-slot/action head.

Do not make v1 depend on perfect inventory modeling.

---

# 20. Behavioral-cloning loss

Conceptual loss:

```text
L =
    lambda_aim       * L_aim
  + lambda_mouse_aux * L_mouse_raw
  + lambda_move      * L_move
  + lambda_buttons   * L_buttons
  + lambda_weapon    * L_weapon
```

Track each term separately.

Do not optimize only aggregate loss. A model can reduce total loss by predicting “do nothing” because many game ticks have no attack/jump/reload action.

Report per-action precision/recall and conditional metrics.

---

# 21. Sequence target design

At 32 fps visual input and 64-Hz action labels, a convenient v1 target is:

```text
one visual observation step -> next two 64-Hz action slots
```

Example tensor:

```text
B x 2 x action_dim
```

Alternative:

```text
predict one integrated 32-Hz angular action
+ predict discrete state transitions inside the frame
```

Start with two explicit 64-Hz slots because it preserves the original action cadence better.

Subtick events remain auxiliary/canonical data for later.

---

# 22. Training sampling

The dataset will contain many correlated frames. Do not treat every adjacent starting frame as equally necessary.

Sample:

```text
random demo
random player-round
random alive timestamp
random context window
```

Possible weighting dimensions:

- map;
- player;
- team side;
- combat vs non-combat;
- weapon;
- action richness.

Avoid sampling only firefights. The model must learn normal navigation and waiting too.

A useful mix can oversample rare but important actions without deleting ordinary play.

---

# 23. Offline evaluation

Offline action accuracy is necessary but insufficient.

## 23.1 Aim metrics

- MAE in yaw degrees;
- MAE in pitch degrees;
- angular endpoint error over 2/4/8 ticks;
- mouse-direction accuracy;
- magnitude calibration;
- error conditioned on visible enemy / firing event if available from labels.

## 23.2 Movement metrics

- W/A/S/D F1;
- exact movement-state accuracy;
- forward/left analog MAE;
- transition timing error.

## 23.3 Buttons

Per-button:

- precision;
- recall;
- F1;
- timing tolerance around ground-truth event.

Attack should be measured at tight timing tolerance.

## 23.4 Sequence rollout metrics

Given ground-truth frames but model-generated previous actions, measure error accumulation.

This exposes dependence on teacher-forced previous actions.

---

# 24. Closed-loop evaluation

Closed-loop evaluation is the real test.

Use an authorized local/private environment.

Measure:

- time before getting stuck;
- fraction of time moving coherently;
- wall/floor staring rate;
- crosshair elevation distribution;
- route completion;
- enemy acquisition latency;
- shot timing;
- damage/kills against controlled bots;
- death rate;
- navigation coverage;
- action oscillation.

Create deterministic scenarios:

```text
Scenario 1: leave spawn and reach site
Scenario 2: walk a corridor and clear angles
Scenario 3: stationary target acquisition
Scenario 4: moving target tracking
Scenario 5: simple bot duel
Scenario 6: full round with bots
```

This is much more informative than immediately testing in uncontrolled online matches.

---

# 25. Runtime architecture

The runtime system should be modular.

```text
screen capture
     |
     v
preprocess/resize
     |
     v
frame history buffer
     |
     v
policy inference
     |
     v
action postprocessor
     |
     v
abstract controller interface
     |
     v
authorized/local environment adapter
```

## 25.1 Screen capture

The deployed policy must use normal rendered pixels, not privileged demo state.

Requirements:

- stable crop/resolution;
- predictable latency;
- capture timestamps;
- no accidental window borders/overlays;
- same visual profile used during dataset rendering.

On Windows, investigate Desktop Duplication / Windows Graphics Capture for low-latency capture. On Linux, use the appropriate compositor/display capture mechanism for the test environment.

## 25.2 Inference loop

Target initial rate:

```text
32 Hz observations
```

Latency budget example:

```text
capture:       < 5 ms target
preprocess:    < 2 ms
model:         < 15 ms
postprocess:   < 2 ms
remaining:     scheduling/controller
```

The exact budget is hardware-dependent.

## 25.3 Action postprocessing

The network should not directly own every controller quirk.

Postprocessor responsibilities:

- convert predicted angular delta to canonical local control scale;
- clamp absurd actions;
- preserve button state between predicted transitions;
- apply the two predicted 64-Hz slots at the intended cadence;
- log all executed actions.

Do not add heavy handcrafted aim logic; that would obscure whether behavioral cloning is actually working.

## 25.4 Controller interface

Define a platform-neutral interface such as:

```text
set_move(forward, left)
set_button(action, pressed)
apply_look_delta(yaw_deg, pitch_deg)
select_weapon(action)
flush(timestamp)
```

Keep the game/environment-specific adapter separate. For this research implementation, only implement and test it in authorized local/private environments.

---

# 26. Training/inference distribution matching

This is one of the most important failure modes.

A model trained on rendered demos can fail closed-loop if the rendering differs from real-time capture.

Match:

- resolution;
- aspect ratio;
- FOV;
- crosshair;
- HUD;
- viewmodel;
- graphics settings;
- color/gamma;
- motion blur;
- framerate characteristics.

Use visual augmentations sparingly. Color/brightness/compression augmentation can help robustness, but geometry-altering transforms may invalidate aim labels.

Never horizontally flip frames unless corresponding yaw/action labels and map-handedness implications are correctly transformed.

---

# 27. Dataset scale strategy

Do not begin by rendering thousands of matches.

## Stage 0 — parser validation

```text
1–3 recent HLTV demos
```

Goal: prove full UserCmd extraction.

## Stage 1 — complete pipeline

```text
10 demos
```

Goal: parse -> render -> align -> view -> train a tiny model.

## Stage 2 — first meaningful policy

```text
50–100 demos
```

At 40 minutes and ~10 players, 100 matches contain a theoretical upper bound around 667 player-hours before dead/freeze filtering.

Goal: coherent closed-loop movement in controlled scenarios.

## Stage 3 — scaling experiment

```text
500 demos
```

Goal: measure data scaling curves.

## Stage 4 — large corpus

```text
1,000–5,000+ demos
```

Only after the model and alignment are validated.

A 5,000-match corpus at 40 minutes and 10 players corresponds to roughly:

```text
5,000 * 40/60 * 10 = ~33,333 player-hours
```

before filtering.

That is extremely large. Training should be step/sample based; there is no requirement to iterate every frame every epoch.

---

# 28. Rendering and storage economics

Rendering all POVs is far more expensive than parsing.

The parser can process demos substantially faster than real time. Rendering requires running CS2 and generating pixels.

Therefore:

1. parse everything first;
2. validate UserCmd quality;
3. generate render jobs only for valid player-rounds;
4. parallelize rendering horizontally;
5. do not render unusable intervals.

At 32 fps, 33,333 POV-hours still implies billions of frames. This is feasible only as a distributed data project, not as one sequential desktop job.

Keep compressed video, not individual image files.

Storage estimates must be measured from the selected codec/profile on a 10–20 hour pilot before provisioning large storage.

---

# 29. Training compute strategy

## 29.1 First model

One modern NVIDIA GPU is sufficient to validate the pipeline if the model and resolution are small.

Example development configuration:

```text
input resize: 384x216 or 512x288
context: 8–16 sampled frames
visual encoder: ResNet-18
GRU hidden: 512
mixed precision: bf16/fp16
```

Once correctness is established, increase to 640x360 or higher if small targets require it.

## 29.2 Scale training

Use:

- multiple GPUs with DDP;
- local NVMe cache;
- hardware video decode;
- large sequential shards;
- prefetching;
- pinned memory;
- asynchronous CPU decoding where applicable.

Profile GPU utilization. If GPU utilization is low, video decode/I/O is probably the bottleneck.

---

# 30. Optional privileged teacher

Because demos provide world state, the project can later train a privileged teacher:

```text
parsed world state -> human action
```

This teacher is not the final policy. It can be used for:

- distillation;
- auxiliary labels;
- detecting ambiguous visual states;
- estimating an upper bound on action predictability;
- debugging whether failures come from perception or decision-making.

This is optional and should not delay the visual baseline.

---

# 31. Behavioral cloning limitations

Pure imitation learning has known failure modes:

## Covariate shift

The model eventually enters states that humans rarely entered in the dataset. Once off-distribution, its errors can compound.

Potential later mitigations:

- dataset aggregation in authorized environments;
- recovery-state data;
- stronger temporal model;
- offline RL;
- self-play / RL fine-tuning.

## Multimodality

At many states, multiple human actions are valid. A pure MSE aim/movement head may average them.

Later approaches:

- discretized action tokens;
- mixture-density heads;
- sequence-policy transformers;
- latent-action modeling.

## Partial observability

Pixels alone omit information a human may infer from audio and memory.

The first policy should compensate with visual/action history. Audio can be added later as a second modality.

---

# 32. Optional reinforcement-learning phase

Do not call behavioral cloning “reward training.” Pure BC is supervised imitation.

If the BC policy becomes coherent, a later phase may fine-tune in an authorized simulation/private environment with reward signals.

Possible high-level objectives:

- round outcome;
- damage dealt/taken;
- survival;
- objective completion.

Reward engineering is dangerous because optimizing kill count alone may encourage behavior that is poor competitive play.

This phase is explicitly after the BC baseline.

---

# 33. Implementation milestones

## Milestone 1 — current-demo UserCmd extractor

Deliverables:

- Go CLI `cs2-extract`;
- full `UserCmd` parsing enabled;
- Parquet/Arrow output;
- player-slot resolution;
- validation report;
- unit tests on one recent professional demo.

CLI example:

```text
cs2-extract parse \
  --demo input.dem \
  --output ./parsed/<demo_id>/
```

Acceptance:

- thousands of commands per active player;
- nonzero `mousedx/mousedy` distributions;
- all expected players represented;
- subtick entries preserved where present;
- reconstructed commands remain available for newer delta-encoded demos.

## Milestone 2 — sensitivity/aim normalizer

Deliverables:

- angle wrapping;
- per-command yaw/pitch deltas;
- robust per-player effective mouse->angle estimates;
- plots/statistics comparing raw and normalized actions.

Acceptance:

- integrated normalized yaw approximately reconstructs view-yaw trajectory;
- no wrap errors near -180/+180 degrees.

## Milestone 3 — renderer fork runs one player-round

Deliverables:

- current CS2-compatible renderer;
- deterministic POV selection;
- 32-fps output;
- frame timestamps;
- reproducible render profile.

Acceptance:

- correct player's POV;
- exact expected round interval;
- video can be re-rendered reproducibly enough for alignment.

## Milestone 4 — frame/UserCmd aligner

Deliverables:

- `frame_alignment.parquet`;
- command ranges per frame;
- synchronized debug viewer.

Acceptance:

- firing/movement/camera changes visually match labels over sampled clips;
- no systematic one-frame/two-frame lag.

## Milestone 5 — dataset builder

Deliverables:

- match/player-round manifest;
- train/val/test split;
- random temporal-window loader;
- data-quality filters.

## Milestone 6 — tiny BC baseline

Deliverables:

- small visual encoder;
- temporal core;
- aim/movement/button heads;
- training metrics;
- checkpoint export.

Acceptance:

- validation loss beats trivial baselines;
- attack/movement metrics are meaningful;
- aim predictions correlate strongly with ground truth.

## Milestone 7 — authorized closed-loop prototype

Deliverables:

- screen capture;
- inference loop;
- abstract/local controller adapter;
- telemetry recording;
- scenario test suite.

Acceptance:

- agent can move for sustained periods;
- does not immediately oscillate/stare at walls;
- shows learned crosshair/navigation behavior.

## Milestone 8 — scale corpus and model

Only now:

- increase HLTV demo count;
- add render workers;
- move to object storage;
- train larger temporal models;
- compare 32 vs 64 fps;
- add subtick/action-token experiments.

---

# 34. First tasks for the coding agent

The coding agent should start in this exact order.

## Task 1

Create repository skeleton and configuration system.

## Task 2

Create `tools/usercmd-extractor` in Go using demoinfocs v6.

Requirements:

- custom parser config with full UserCmd parsing;
- register `events.UserCmd`;
- collect every reconstructed command;
- map command to player slot and SteamID when available;
- write Arrow/Parquet;
- record parser version and demo hash.

## Task 3

Build a validator command:

```text
cs2-extract validate --parsed <dir>
```

Output JSON + human-readable report.

## Task 4

Implement angular normalization and diagnostics in Python.

## Task 5

Fork/clone Reka renderer and run it on exactly one current HLTV demo.

Do not modify architecture yet. Establish a known-good baseline.

## Task 6

Add render timing metadata needed for frame-to-tick synchronization.

## Task 7

Build a synchronized local viewer before training any network.

Viewer must show video plus:

```text
frame
server tick
raw mousedx/mousedy
normalized yaw/pitch delta
movement
buttons
position
```

## Task 8

Render 10 demos and manually inspect random clips.

## Task 9

Implement a tiny PyTorch behavioral-cloning baseline.

## Task 10

Run closed-loop evaluation only after offline synchronization and prediction metrics pass.

---

# 35. Engineering invariants

The coding agent should treat these as non-negotiable.

1. **Raw demo files are immutable.**
2. **Raw reconstructed UserCmd data is immutable.**
3. Derived labels are versioned, never overwritten silently.
4. Every artifact contains `demo_id` and processing-version metadata.
5. Never infer mouse labels when actual UserCmd exists without preserving both.
6. Never use raw mouse counts across players without accounting for sensitivity ambiguity.
7. Never randomly split frames from the same match across train/test.
8. Never scale rendering before alignment is visually validated.
9. Never delete subtick/input-history data merely because v1 ignores it.
10. The final visual policy must not depend on privileged demo-state fields unavailable during runtime.
11. All runtime experiments stay local/private/authorized; do not implement anti-cheat bypass or concealment.

---

# 36. Data-versioning strategy

Every processing stage should have an explicit version.

Example:

```text
parser_schema_version: 1
normalizer_version: 1
renderer_profile: render32-v1
alignment_version: 1
dataset_version: bc-v1
```

Artifact layout:

```text
s3://bucket/
  demos/<demo_id>.dem
  parsed/v1/<demo_id>/usercmd.parquet
  parsed/v1/<demo_id>/player_state.parquet
  rendered/render32-v1/<demo_id>/<clip_id>.mp4
  aligned/v1/<demo_id>/<clip_id>.parquet
  manifests/bc-v1/train.parquet
```

This will matter because CS2, parsers, renderer plugins, and normalization logic will evolve.

---

# 37. Logging and observability

Every long-running job should emit structured logs.

Minimum fields:

```text
timestamp
job_id
demo_id
stage
worker_id
status
elapsed_ms
error_type
error_message
```

Metrics worth tracking:

### Parser

```text
demos/hour
commands/sec
commands/player
parser failures
missing-player mappings
```

### Renderer

```text
POV-hours rendered / wall-clock hour
failed renders
average clip duration
GPU encoder utilization
CS2 crashes
```

### Trainer

```text
samples/sec
video decode ms
GPU utilization
loss by head
validation metrics
```

---

# 38. Testing strategy

## Unit tests

- yaw wrap;
- pitch delta;
- button decoding;
- schema serialization;
- command-to-frame interval assignment;
- train/test split determinism.

## Golden fixture

Keep at least one small recent demo fixture and a versioned expected summary:

```text
players
rounds
UserCmd counts
mouse statistics
subtick counts
```

If a parser/library upgrade changes these unexpectedly, CI should fail.

## Integration test

One fixture should run:

```text
demo -> parse -> render short clip -> align -> load one training batch
```

The renderer part may be excluded from ordinary CI if Steam/CS2 is unavailable; run it on a dedicated integration worker.

---

# 39. Experiments to run before scaling

## Experiment A — true UserCmd vs camera-derived aim labels

Train two otherwise identical small models:

```text
A: normalized aim derived from reconstructed UserCmd view trajectory + raw UserCmd auxiliary
B: renderer/frame camera-delta labels only
```

Measure offline aim error and closed-loop aim stability.

## Experiment B — 32 fps vs 64 fps

Use the same 20–50 demos.

Measure:

- rendering cost;
- training throughput;
- aim precision;
- target acquisition;
- action-timing metrics.

Do not assume 64 fps is superior enough to justify cost.

## Experiment C — raw mouse target vs normalized angular target

Compare:

```text
pixels -> raw mousedx/mousedy
```

against:

```text
pixels -> delta_yaw/delta_pitch
```

and a multitask version predicting both.

Expected hypothesis: normalized angular action is more stable across players; raw mouse is useful auxiliary supervision.

## Experiment D — visual history length

Compare:

```text
0.25 s
0.5 s
1.0 s
2.0 s
```

CS2 is partially observable, so some temporal context should materially improve behavior.

## Experiment E — previous-action conditioning

Compare models with and without previous executed actions. Previous-action history should help with spraying, movement continuity, and state transitions.

---

# 40. Risks and likely failure modes

## Risk: newer demo parses but UserCmd fields are empty

Cause:

- wrong parser mode;
- parser regression;
- unsupported CS2 protocol change.

Mitigation:

- explicit `UserCmdParsingFull`;
- validation thresholds;
- pin parser commit/version;
- keep raw demos for reprocessing.

## Risk: perfect offline loss, poor closed-loop behavior

Cause:

- covariate shift;
- teacher forcing;
- frame/action lag;
- insufficient temporal context.

Mitigation:

- alignment viewer;
- action-history conditioning;
- local recovery-state collection later;
- sequence-policy model.

## Risk: aim looks weak/blurry

Cause:

- MSE averaging multimodal actions;
- low resolution;
- low frame rate;
- target normalization bug.

Mitigation:

- mixture/discretized aim head;
- resolution/fps experiment;
- true UserCmd validation.

## Risk: model learns “do nothing”

Cause:

- action imbalance.

Mitigation:

- per-head metrics;
- weighted sampling/loss;
- preserve ordinary play while oversampling informative transitions.

## Risk: rendered video differs from runtime visuals

Mitigation:

- locked visual profile;
- domain augmentation;
- re-render subset using runtime-like HUD/viewmodel settings.

## Risk: CS2 update breaks renderer

Mitigation:

- pin build where possible;
- version renderer/plugin;
- keep compatibility branches;
- render in batches and validate after every game update.

---

# 41. Suggested first production schema for a training window

A single model sample can look like:

```python
{
    "frames": float32[T, 3, H, W],
    "prev_actions": float32[T, A],

    "target": {
        "yaw_pitch_deg": float32[2, 2],      # 2 future 64-Hz slots
        "raw_mouse": int32[2, 2],
        "move_buttons": bool[2, 4],
        "move_analog": float32[2, 2],
        "buttons": bool[2, K],
        "weapon_action": int64[2]
    },

    "metadata": {
        "demo_id": str,
        "clip_id": str,
        "steam_id": int,
        "map": str,
        "round_id": int,
        "start_tick": int
    }
}
```

`metadata` is for debugging and should not normally be fed to the visual policy.

---

# 42. Recommended v1 training configuration

A concrete starting point:

```text
render fps:              32
command targets:         64 Hz, two steps per visual frame
train image size:        512x288 initially
context frames:          8
context stride:          2–4 frames
visual encoder:          ResNet-18
visual embedding:        512
previous-action embed:   128
temporal core:           2-layer GRU, hidden 512

heads:
  yaw/pitch delta        continuous Smooth L1
  raw mouse aux          continuous/robust regression
  WASD                   4 Bernoulli logits
  analog forward/left    Smooth L1
  attack/jump/etc        Bernoulli logits

optimizer:               AdamW
mixed precision:         bf16 if supported
```

This is intentionally conservative. The first objective is to discover data and closed-loop problems, not win an architecture benchmark.

---

# 43. When to move to a transformer/action-token model

Move beyond the GRU baseline when:

- alignment has been proven;
- at least hundreds of hours are training cleanly;
- the baseline exhibits multimodal averaging or long-horizon failures;
- throughput is understood.

A later architecture can tokenize actions:

```text
LOOK_X_BIN
LOOK_Y_BIN
W_ON
A_OFF
ATTACK_ON
...
```

and autoregressively predict an action sequence conditioned on visual tokens.

This may handle multimodal behavior better than independent regression heads.

Do not begin there.

---

# 44. Why professional demos are a strong primary dataset

Professional competitive demos match the target domain better than Deathmatch because they naturally contain:

- real 5v5 pacing;
- map navigation;
- holds;
- executes;
- rotations;
- retakes;
- clutches;
- saves;
- bomb interactions;
- weapon/economy context;
- elite mechanics.

The downside is that pro behavior assumes coordinated teammates and is narrower than general matchmaking behavior. That can be addressed later by adding other authorized/available competitive corpora. It does not justify making Deathmatch the primary v1 source.

---

# 45. Why use the new UserCmd path instead of only RekaCS2-10k

RekaCS2-10k is extremely useful, but this project has a specific reason to create a custom corpus:

**action fidelity.**

The desired dataset should preserve:

```text
full reconstructed UserCmd
raw mousedx/mousedy
buttons
movement axes
view angles
weapon selection
subtick moves
input history
server/client tick linkage
```

This provides richer supervision and future-proofs the dataset for action-timing research.

Reka's renderer remains valuable because it solves a difficult and expensive problem: reliable first-person replay rendering at scale.

Thus the project should combine:

```text
Reka rendering infrastructure
+
demoinfocs v6 full UserCmd extraction
+
custom alignment / normalization / validation
```

rather than choosing only one.

---

# 46. Source references verified for this specification

These are the primary references the coding agent should read before implementation.

## demoinfocs v6

- Package/docs: `https://pkg.go.dev/github.com/markus-wa/demoinfocs-golang/v6`
- UserCmd event docs: `https://pkg.go.dev/github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events`
- v6.0.0-alpha.0 release summary: `https://newreleases.io/project/github/markus-wa/demoinfocs-golang/release/v6.0.0-alpha.0`

Relevant verified behavior:

- full reconstructed CS2 commands;
- full and delta-data support;
- `UserCmdParsingFull` mode;
- button-only mode is the default low-overhead mode, so the extractor must explicitly request full parsing.

## Valve/SteamTracking protobufs

- `usercmd.proto`: `https://github.com/SteamTracking/GameTracking-CS2/blob/master/Protobufs/usercmd.proto`
- `cs_usercmd.proto`: `https://github.com/SteamTracking/GameTracking-CS2/blob/master/Protobufs/cs_usercmd.proto`

These define the mouse, movement, button, subtick, and input-history fields.

## Reka renderer

- `https://github.com/reka-ai/cs2-dem-renderer`
- `https://reka.ai/news/cs2-10k-a-large-scale-egocentric-counter-strike-2-dataset`

Verified design:

- two-pass parse;
- per-player-round rendering;
- CS2 replay;
- ffmpeg encoding;
- MP4 + Parquet;
- worker mode;
- current-version plugin compatibility is a maintenance concern.

## HLTV demos

- HLTV match pages expose downloadable professional demos.
- Historical confirmation of match-page demo downloads: `https://www.hltv.org/news/35174/download-limits-for-demos-removed`

Use the demos in accordance with applicable site/tournament terms.

## Steam terms / runtime boundary

- Steam Subscriber Agreement: `https://store.steampowered.com/subscriber_agreement/`

Do not implement anti-cheat evasion, concealment, or unauthorized public matchmaking automation.

---

# 47. Coding-agent handoff prompt

The following can be pasted into a coding agent after it reads this file:

```text
You are implementing the CS2 Visual Imitation Learning project described in PROJECT_SPEC.md.

Do not redesign the project from scratch. Follow the specification and work incrementally.

Primary data source:
- recent professional CS2 .dem files obtained from HLTV.

Canonical action source:
- demoinfocs-golang v6 with UserCmdParsingFull.
- Preserve reconstructed full/delta UserCmd data, including raw mouse dx/dy,
  view angles, movement axes, buttons, subtick moves, input history, client tick,
  and server tick executed.

Rendering:
- fork/adapt reka-ai/cs2-dem-renderer rather than building a replay renderer from zero.
- Use the renderer for deterministic first-person frames.
- The project's own UserCmd extractor is authoritative for action labels.

Synchronization:
- create an auditable frame-to-command mapping.
- initially render at 32 fps while preserving 64-Hz UserCmds.
- keep two 64-Hz command targets per nominal 32-Hz visual step.
- never discard raw commands or subtick information.

Aim normalization:
- preserve raw mousedx/mousedy.
- primary cross-player aim target is normalized delta_yaw/delta_pitch in degrees.
- do not assume player DPI/sensitivity is explicitly known.
- optional effective mouse->angle scale may be estimated robustly per player.

Training:
- first baseline is supervised behavioral cloning, not RL.
- visual input + action history -> next action sequence.
- start with a small CNN + GRU/temporal model and separate action heads.
- privileged parsed state is for labels/evaluation/teachers, not direct policy input.

Validation:
- build a synchronized viewer before large-scale rendering or training.
- validate that firing, mouse, movement, and video timing match.

Scaling:
- start with 1–3 demos for parser validation, 10 demos for complete pipeline,
  then 50–100 for first meaningful training.
- do not render thousands until the closed-loop baseline works.

Runtime:
- implement modular screen capture -> inference -> abstract action controller.
- test only in local/offline/explicitly authorized private environments.
- do not implement anti-cheat bypass, concealment, or public matchmaking automation.

First implementation target:
1. repo skeleton;
2. Go full-UserCmd extractor;
3. Parquet schema;
4. validator;
5. aim normalizer;
6. run Reka renderer on one current demo;
7. frame/UserCmd aligner;
8. synchronized debug viewer;
9. tiny PyTorch BC baseline.

When uncertain, preserve more raw information rather than discarding it.
All derived schemas and processing stages must be versioned.
```

---

# 48. Definition of success for the first major version

The first major project version is successful when all of the following are true:

1. Recent professional CS2 demos can be parsed automatically.
2. Full reconstructed `UserCmd` streams are retained for all relevant players.
3. Raw mouse deltas and normalized angular deltas are both available.
4. First-person player-round video can be rendered reproducibly.
5. Every training frame can be mapped to the correct command interval.
6. A synchronized viewer demonstrates correct labels visually.
7. A PyTorch temporal policy trains on the resulting dataset.
8. Offline action metrics beat strong trivial baselines.
9. In a local/private authorized test environment, the policy can consume live screen frames and sustain recognizable learned movement/camera behavior.
10. The pipeline can be horizontally scaled to hundreds/thousands of demos without changing the data model.

That is the correct foundation. Strategic sophistication, audio, RL, multi-agent coordination, and larger models come afterward.
