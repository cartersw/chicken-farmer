# Recording sessions in the demo queue

The default queue now records a whole demo/player schedule with one CS2
process. Physical recordings follow eligibility windows; two-minute training
shard cuts reuse recorded history without restarting the movie or seeking back.
Validation begins after recording closes and settings are restored.

The native controller uses acknowledged movie starts and endpoints, a continuous
32 FPS tick grid, target-player checks and bounded storage/progress guards.
The separately built session DLL is pinned in `cs2_data.session_profile`; the
legacy single-clip DLL retains its original pin. All new captured data remains
640×360 RGB8 for training, with eight-frame histories and lossless compression.

Shared native logs, their SQLite index and original TGA images are archived once
per session. Logical views keep global packet/clock IDs and native filenames.
A training-file boundary explicitly references a real subsequent observation;
it never fabricates a native `movie_end`. Validators run in separate processes
(1–4, default 2), with ordered training archive publication and guarded cleanup.

Stop during recording finishes the current physical run and saves it for resume.
Resume reuses closed recordings and schedules only missing intervals. Failed
native attempts remain diagnostic evidence. Completed training and shared archive
dependencies are checked before reuse or cleanup.

## Verification

- The three-interval live test completed in one CS2 process. All 480 logical
  frames passed pixel/timing checks; acceptance produced 457 examples. The 23
  rejected candidates include the required initial history and unsupported
  boundary candidates; rejection does not cause a new recording.
- All three training archives and the single shared evidence archive passed
  compression checks. Their combined size is 606,053,720 bytes. Temporary work
  and the completed shared session directory were released only afterward.
- Reopening actual training data returned eight 691,200-byte RGB frames per
  history. Two adjacent histories required nine decodes, with 5,529,600 bytes
  retained in the bounded eight-frame cache. Queue resume returned the same
  457 examples without recapture.
- The full Ckanic/Dust2 capture completed all 24 physical runs (26 logical
  segments) in one process, including round/half transitions and continuous
  recordings longer than two minutes. It captured 60,508 native frames and
  matching readbacks with 24 endpoints. Capture and protected shutdown took
  approximately 25.72 minutes; source eligible play totals approximately
  31.49 minutes. Exit code was zero and settings/game staging were restored.
- Full-session indexing passed for 6,547,314 native records. The original ledger
  is 11,461,402,539 bytes and its SQLite index is 5,637,165,056 bytes. The shared
  evidence archive is 37,612,988,646 bytes and passed its compression round trip.
- All 26 full-demo segments passed validation and packaging, producing 59,848
  unique accepted examples and 626 rejected candidates from 60,474 logical
  training frames. A complete scan found no duplicate action targets.
- Both internal training-file splits were checked in the compressed output.
  Each pair came from one physical recording and shares seven source images
  with byte-identical decoded RGB. The final accepted history also loaded
  correctly. Results are in `full-demo-verification.json`.
- Training archives total 29,446,498,010 bytes; per-clip evidence totals
  1,370,758,744 bytes. Including the shared archive, compressed output totals
  68,430,245,400 bytes. All clip working directories and the common recording
  workspace were released after verification; the original demo remains.
- Full-demo processing resumed its saved captures after a verifier optimization
  and again to exercise four validation workers. Game capture did not repeat.
  A final completed-queue resume returned the same 59,848 examples without
  recapture or revalidation (`resume-verification.json`). That check completed
  across an interrupted assistant session, so its elapsed wall time is not
  presented as an uninterrupted throughput benchmark.
- The final core suite passed 597 tests, including recovery, storage and shared
  archive checks. The updated real Tk suite passed all
  27 tests, and the desktop shortcut was used to reload and inspect the queue.

Evidence: `data/validation/recording-session-001/` and
`data/validation/recording-session-full-001/`. The user's fresh desktop queue
remains separate from these engineering runs.

A 12 MB native-frame sample compressed 2.3× faster at DEFLATE level 1 than level
6, using approximately 3% more space. Shared session archives now use level 1.
This benchmark is a small representative sample, not a full-demo speed guarantee.
Admission budgets include raw data, logs, index, shared/training archives and
workspace coexisting; they do not assume a favorable compression ratio.

Shared evidence hashes are reused only under Windows handles that deny writes
and deletion, with a file identity check on each use. The guard tests confirm
mutation is blocked, read-only SQLite remains usable, incorrect hashes fail,
nested guards work and handles release on exceptions. This reduces repeated
multi-gigabyte reads without changing the numerical acceptance requirements.
The integrated index/view check also passes with garbage collection disabled:
fresh evidence can be locked, a partial reader can close, and the database can
then be renamed immediately on Windows.
