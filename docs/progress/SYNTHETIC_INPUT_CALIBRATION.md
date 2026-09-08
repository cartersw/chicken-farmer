# Synthetic keyboard/mouse calibration

**Updated: 2026-09-08.** This milestone implements the user's requested Windows
keyboard and mouse route and measures it in the protected local recorder.
These single-player probes are diagnostics; the intended training corpus remains
competitive match footage. Earlier accepted/failed competitive reports remain intact.

## Implemented

- A reusable Windows `SendInput` adapter for scan-code keys, mouse buttons and
  relative mouse counts, with owned-process identity, foreground and fresh native
  local-session checks before/after each batch.
- A separate bounded synthetic plan schema, an external scheduling worker and a
  passive native bridge. Console commands configure the isolated session and
  recording; the probe actions use only the Windows input API.
- Sent-event receipts with QPC brackets, requested/inserted counts, native frame
  identity and cleanup outcomes. Failed insertion disables new actions and
  releases controls the adapter may have pressed.
- Independent mouse and keyboard response analyzers retaining raw demo bytes,
  nullable fields, native state, fixed observation windows and unverified timing.

The worker shares the existing settings snapshot, private configuration,
native isolation guard, owned process and renderer-directory cleanup lifecycle.
See [the API and reproduction guide](../SYNTHETIC_INPUT.md).

## Mouse result: synthetic-001

The 12-second recording produced **385 images and 840 complete UserCmds**.
All 385 images match native RGB readbacks; all 385 original camera angles match
their uniquely associated recorded input-history clocks. Every reconstructed
UserCmd matches its original demo protobuf bytes.

Four predeclared fit cases use isolated ±40-count axis movements. Eight held-out
cases use ±20 and ±80 counts. All pass the unchanged protocol, with no excluded
cases, no observed cross-axis response and a maximum held-out angular error of
0.00000763 degrees. At the isolated sensitivity setting of 1:

| Injected relative input | Observed response |
| --- | --- |
| Positive X count | Approximately −0.022 degrees yaw |
| Positive Y count | Approximately +0.022 degrees pitch |

All 12 fixed command neighborhoods have raw mouse-count sums equal to their
injected counts. Each active axis appears in one command; omitted scalars remain
raw nulls and contribute zero only under verified protobuf base-message presence.
Those mouse commands contain no subtick movement records, so their exact input
consumption times are still unavailable.

The second report strengthens the check that both the entire pre-plateau movie
submission and its pixel readback finished before insertion. It preserves the
original report and gives the same result without changing windows or refitting
held-out cases.

Evidence: [mouse report](../../data/calibration/synthetic-001-analysis/report-v2.md),
[full JSON](../../data/calibration/synthetic-001-analysis/report-v2.json),
[capture manifest](../../data/calibration/synthetic-001/calibration.json).
Demo SHA256: `fcab7de82ed3cf8a5a38754d122379f54f4ad0bb4cf72a633c393b5c808aaef1`.

## Keyboard scheduling attempts

`synthetic-002` reached its shot press, but a post-insertion scheduling check
mistook natural clock advancement during `SendInput` for an unsent batch
collapsing with the following release. The failure receipt retains the inserted
press and successful cleanup release. The worker now applies deadline checks
before insertion and continues checking local scope and foreground afterward.

`synthetic-003` stopped before a short left tap because polling missed its
31 ms press/release window. This was a real missed scheduling boundary; no tap
was sent. A direct host measurement found Python 3.10's requested 2 ms sleep
taking a median 15.55 ms. The native frames around that tap remained approximately
27 ms apart in wall time. A scoped high-resolution Windows wait timer and disk
checks outside short input windows address this polling problem.

Both failed runs are retained unchanged. Both preserved all 39 selected personal
files with zero restore operations, restored gameinfo, removed their staged
plugins and closed the owned game. The same frozen keyboard plan and response
protocol are used for the repeat; failures do not become accepted results.

## Keyboard result: synthetic-004

The unchanged 12-second plan completed all **20 sent events and ten response
phases**, producing another **385 images and 840 complete UserCmds**. Independent
audits match all 385 original frames to native pixels and all 840 live command
payloads to original demo bytes. All ten fixed response checks pass:

- W/A/S/D holds and the two short movement taps have recorded button transitions
  and native movement effects.
- Crouch and jump have the expected observed crouch/ground-state changes.
- The left click reduces Glock ammunition from 20 to 19, advances last-shot time
  and has a matching `weapon_fire` event.
- R reload increases the same weapon's clip from 19 to 20. Its raw button-plane
  changes are retained, while no reload subtick edge is invented.

The 18 exported subtick edges all contain `when=0.078125`. This establishes a
recorded value in this experiment; it does not identify its exact relationship
to Windows insertion time. Missing reload subticks and known rapid-console
counterexamples still prevent a universal exact-event reconstruction claim.

Maximum observed schedule lateness was 28.125 simulation milliseconds; the
largest retained native guard age was 19.668 wall milliseconds. The largest
observed press/release API bracket was 0.411 wall seconds. The new timer's
separate 64-wait host check measured a 2.50 ms median for a 2 ms request; Windows
scheduling and synchronous work still make this a measured polling loop rather
than a real-time guarantee.

Evidence: [keyboard response report](../../data/calibration/synthetic-004-analysis/report.md),
[full response JSON](../../data/calibration/synthetic-004-analysis/report.json),
[independent pixel audit](../../data/calibration/synthetic-004-pixels/report.json),
[capture manifest](../../data/calibration/synthetic-004/calibration.json).
Demo SHA256: `1e0e8d1127fa0172732bfbf5fff8817a005b78d3c407a1c12a223e2de9a1324e`.

## Protection and verification

Across all four attempts, every run verified the same **39 selected personal
settings files with zero restore operations**, restored gameinfo and removed
its staged plugin. Final read-only checks after 004 again matched current files
to the snapshot, found byte-identical original gameinfo, no owned renderer folder
and no running CS2 process. Steam mode and Cloud preferences were not changed.

Both successful recordings used native plugin SHA256
`f996b15d32518f51579a3fc99477e3a9f6e845090326effc47414bac4742d638`.
The native Release build passed. The extractor reconstructed every live payload
in both recordings, but its generic competitive-match quality check returns a
failure for these deliberately short, single-human probes; that is separate
from reconstruction or calibration success.

Final regression: **1,258 Python tests passed in 44.73 seconds**; `git diff --check`
passed. This includes the timer, adapter, failed/logging cleanup and advancing
native-frame integration regressions, plus independent evidence tampering cases.

## Remaining limits

The measured gain applies to the tested local build, sensitivity and input path.
It is not a physical-mouse latency measurement or a universal subtick timing rule.
Windows insertion counts alone do not establish game response. Foreground checks
bound risk but cannot make the global desktop API atomically target a PID.

Next steps remain scoped semantic training labels, a controller executor for the
32 Hz action contract, additional controls/phase coverage, current-build
competitive source proof, the tensor loader and model training. This milestone
does not add accepted competitive training samples or certify live control.
