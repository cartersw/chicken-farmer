# Windows synthetic input calibration

The reusable adapter sends bounded scan-code keyboard, mouse-button and relative-mouse events through Windows `SendInput`. The protected calibration worker pairs its receipts with native observations from one owned local CS2 session. A successful insertion does not prove that CS2 consumed the event, used a particular input binding, or displayed its effect in a particular image.

Implementation: [adapter](../tools/renderer/win32_input.py), [worker](../tools/renderer/synthetic_windows.py), [plan validator](../src/cs2_data/synthetic_input_plan.py), and [adapter tests](../tools/renderer/test_win32_input.py). The existing [calibration workflow](CALIBRATION.md) owns the game process, temporary plugin and settings restoration. The [plan and protocol guide](../tools/renderer/plans/SYNTHETIC_INPUT.md) specifies the frozen experiments and their limits.

## Measured mouse result: synthetic-001

The first protected mouse recording completed successfully: CS2 exited with code 0, all 12 planned impulses were inserted, and 385 original frames were retained and encoded at 32 FPS. All 385 archived images match their own native pixel readbacks, and all 385 observed camera angles match unique recorded input-history entries within the documented render-clock comparison. These checks preserve the distinction between image correspondence and input-consumption phase.

The [report](../data/calibration/synthetic-001-analysis/report.md) and [full JSON evidence](../data/calibration/synthetic-001-analysis/report.json) show a measured local gain of approximately:

| Injected relative axis | Observed camera response per count |
| --- | ---: |
| X | -0.02199993 degrees yaw |
| Y | +0.02200000 degrees pitch |

Four preregistered impulses at ±40 counts fit the matrix. All eight held-out impulses at ±20 and ±80 counts pass without refitting; the largest held-out component residual is approximately 0.00000763 degrees. Cross-axis response was zero in these cases. The analyzer uses fixed pre/post camera plateaus, requires pawn-eye agreement and excludes movement, pitch clamp, shot/weapon changes and unstable plateaus. This is a result for the recorded build/settings and tested isolated counts; an independent repeat remains necessary before generalizing it.

The audit bound all 840 canonical commands to complete live UserCmd payloads in the original demo, with zero delta payloads; one additional full checkpoint payload is counted separately. Report publication binds the original plan, ledgers, manifest, demo, parsed command table and frame hashes. An independent read-only review rechecked all 15 referenced metadata/source hashes, all 385 archived frame-file hashes and all 12 reported camera-plateau responses.

Startup settling also passed: its continuous window covered about 2.001 seconds, 743 callbacks and 128 advancing controller ticks; its largest callback gap was about 9.71 ms. That describes the measured settling window, not every startup interval. Ten input batches used the exact scheduled simulation boundary; two used observations 31.25 and 62.5 ms later. Native-observation-to-API-start intervals ranged from 0.675 to 19.678 ms. None of those intervals is a measured game-consumption or physical-device latency.

The run verified all 39 selected original settings files unchanged, requiring zero restore operations; gameinfo was restored and the staged plugin removed. The existing native local-settings/engine Cloud guard was required throughout. Steam Cloud online/reconnection behavior remains outside this experiment's scope.

Keyboard trial `synthetic-002` was stopped by a conservative scheduling check after a successful attack insertion crossed its following release boundary. Cleanup released the owned mouse button, the game stopped, all 39 selected settings files remained unchanged, and gameinfo/plugin cleanup completed. The worker now applies schedule eligibility to the pre-insertion guard; its post-insertion guard still checks fresh native scope and foreground identity. A regression test covers crossing a release boundary during the API call.

Trial `synthetic-003` correctly stopped before a short left tap: the latest observed simulation time was 8140.625 ms, beyond both its 8107 ms press and 8138 ms release. No event in that missed batch was attempted. The same 39-file and gameinfo/plugin cleanup checks passed. The worker now uses the scoped polling timer below and defers expensive frame-directory checks while controls are held or an input boundary is near. The [keyboard plan](../tools/renderer/plans/synthetic-keyboard-probe-012-v1.json) and [frozen protocol](../tools/renderer/plans/synthetic-keyboard-protocol-v1.json) have not been retuned.

## Measured keyboard result: synthetic-004

The unchanged keyboard plan completed with the revised polling worker: [run manifest](../data/calibration/synthetic-004/calibration.json), [input receipts](../data/calibration/synthetic-004/input_ledger.jsonl). All 20 events were fully inserted, all paired controls ended released, CS2 exited with code 0, and 385 frames plus the original demo were retained. The [response audit](../data/calibration/synthetic-004-analysis/report.md) and [machine-readable evidence](../data/calibration/synthetic-004-analysis/report.json) report all 10 paired phases and all 20 per-event record checks passing the unchanged frozen protocol. All 840 canonical commands match original full live protobuf payloads, with zero delta payloads; the extra checkpoint stays separate. The [pixel audit](../data/calibration/synthetic-004-pixels/report.json) matches all 385 archived images to their own native readbacks.

| Tested controls | Observed response in the frozen windows |
| --- | --- |
| Separate W, A, S, D holds and short W/A taps | Directional command values, movement and release records |
| LCTRL | Duck amount rose from 0 to 1; ducked state observed |
| SPACE | Grounded, airborne and landed; upward velocity observed |
| Left mouse | Same-weapon ammo 20→19, increased last-shot time and a Glock-18 `weapon_fire` event |
| R | Same-weapon ammo 19→20 and raw reload plane changes |

There are 18 raw subtick records: nine presses and nine releases, all at recorded fraction `0.078125`. Reload has no subtick edges, so its two checks use the separately declared plane-change hypothesis. The first 18 changes occur at observed scheduling command N+4; reload changes occur at N+2. These are retained observations, not new timing rules or physical event timestamps. This probe has no positive plane-3 multi-transition case, and does not resolve the earlier console-probe counterexample. LSHIFT and right click remain untested.

A read-only post-run check found no CS2 process, no staged run mod, byte-identical gameinfo, and all 39 selected settings files matching their original snapshot hashes. No settings restore operations were needed. Both final input-cleanup receipts report no remaining owned controls, and the recorded poll-timer source hash matches the implementation used in the review.

The maximum observed press-to-release API bracket was 0.410623 seconds. Maximum simulation scheduling lateness was 28.125 ms; the oldest native frame used by an insertion guard was 19.6673 ms. No input failure or missed-boundary error was recorded. Across 100 published polling samples the largest reported loop gap was 250 ms, in a window before any events had completed. Samples published between the first and last input had a maximum reported gap of 47 ms. The largest capture-budget check took about 78 ms. These measurements show why both deadline-aware work placement and stale-state guards remain necessary; the timer alone cannot bound the full worker loop.

## Reproducing the workflow

Run these commands from the project root. Outputs must use fresh paths. The first command is a dry run and does not launch CS2:

```powershell
.venv/Scripts/python.exe tools/renderer/synthetic_windows.py --plan tools/renderer/plans/synthetic-mouse-probe-012-v1.json --output data/calibration/synthetic-mouse-next
```

The execution form starts one protected local session. CS2 must initially be closed; during the run its owned window must have focus and the keyboard/mouse must be left idle. The worker does not force focus or bypass Windows input restrictions. It checks the installed binary profile and settings protection before recording:

```powershell
.venv/Scripts/python.exe tools/renderer/synthetic_windows.py --plan tools/renderer/plans/synthetic-mouse-probe-012-v1.json --output data/calibration/synthetic-mouse-next --execute --allow-version-mismatch --timeout 300
```

`--allow-version-mismatch` acknowledges the experimental renderer/runtime version difference; it cannot bypass exact native binary guards. Use the keyboard plan path for the separate keyboard probe. Steam path/account selectors are available through `--steam-dir` and `--steam-user-id` when discovery is ambiguous.

The mouse analyzer requires both the original completed capture and the canonical extraction of its `controlled.dem`. To independently rerun the completed synthetic-001 audit into fresh JSON/Markdown files:

```powershell
.venv/Scripts/python.exe -m cs2_data.synthetic_input_analysis --run-dir data/calibration/synthetic-001 --parsed data/calibration/synthetic-001-parsed/fcab7de82ed3cf8a5a38754d122379f54f4ad0bb4cf72a633c393b5c808aaef1 --output data/calibration/synthetic-001-analysis-review/report.json
```

The separate [keyboard analyzer](../src/cs2_data/synthetic_keyboard_analysis.py) requires the frozen protocol and a fresh report path. To repeat the completed synthetic-004 audit:

```powershell
.venv/Scripts/python.exe -m cs2_data.synthetic_keyboard_analysis --run data/calibration/synthetic-004 --parsed data/calibration/synthetic-004-parsed/1e0e8d1127fa0172732bfbf5fff8817a005b78d3c407a1c12a223e2de9a1324e --protocol tools/renderer/plans/synthetic-keyboard-protocol-v1.json --out data/calibration/synthetic-004-analysis-review/report.json
```

Reports retain nullable raw fields and bounded response evidence; they do not automatically verify complete button semantics or exact consumption timestamps.

## Adapter contract

```python
adapter = WindowsInputAdapter(
    owned_cs2_pid,
    run_id,
    scope_guard,  # Fresh native evidence; returns {"verified": True, ...}.
    expected_executable=absolute_cs2_exe_path,
)
with adapter:
    receipt = adapter.send([
        {"kind": "key", "key": "W", "pressed": True},
        {"kind": "mouse_move", "dx": 20, "dy": -10},
    ])
    adapter.send([{"kind": "key", "key": "W", "pressed": False}])
```

This illustrates the API, not a standalone authorized session: the worker must supply and continuously verify the native local-session scope.

| Input | Exact normalized fields | Limits |
| --- | --- | --- |
| Keyboard | `kind="key"`, `key`, `pressed` | W, A, S, D, SPACE, LCTRL, LSHIFT, R; boolean press/release |
| Mouse button | `kind="mouse_button"`, `button`, `pressed` | left or right; boolean press/release |
| Relative mouse | `kind="mouse_move"`, `dx`, `dy` | Integer counts; nonzero movement |

Each batch permits at most eight events and 200 total absolute mouse counts, computed as the sum of `abs(dx)+abs(dy)`. Scheduling IDs and `at_ms` remain in the immutable plan and worker ledger; the worker removes them before calling the adapter. Releases require an earlier owned press; duplicate presses while held are rejected. Plan validation separately requires balanced controls, bounded holds and idle intervals.

The real backend opens the supplied PID with query and synchronization access, retains the process handle, and pins its creation time and executable path. Before and after insertion it checks liveness, identity, foreground HWND/PID and the caller's native scope evidence. It does not bring windows forward or change settings. Initial OS key-state checks must show the supported controls, other mouse buttons, and left/right modifiers up.

Keyboard events use `KEYEVENTF_SCANCODE`, with `wVk=0`; releases add `KEYEVENTF_KEYUP`. This selects scan-code keys without using the Unicode text-input path. Event timestamps are left at zero for Windows to assign. See Microsoft's [KEYBDINPUT contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput).

## Receipt and cleanup evidence

A normal batch is successful only when `status="inserted"`, `success=true`, the requested and inserted counts agree, and both guards pass. Receipts retain the normalized events, run/PID, QPC frequency and API interval, Windows error code, and detached `guard_before`/`guard_after` evidence. `inserted_count=null` means the backend call failed without a known count; `insertion_attempted` distinguishes that case from a rejected precheck.

Zero/partial insertion, a lost guard or an adapter error disables new presses and movements. `InputAdapterError` carries the failed `receipt` and a separate `cleanup_receipt`. Cleanup attempts only releases for controls this adapter may have pressed; partial insertion conservatively retains every attempted press for cleanup. No press or movement is retried. `close()` and the context manager also attempt cleanup and close the process handle.

`SendInput` inserts into the desktop input stream; it is not addressed to a PID. A foreground change can occur between the check and insertion, and cleanup releases can reach a newly focused application. Windows reports insertion counts, not application consumption. UIPI can block injection into a higher-integrity process, without identifying UIPI as the specific cause in the return/error value. This workflow does not bypass that restriction. See Microsoft's [SendInput contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

Initial `GetAsyncKeyState` checks use the current-down high bit, not its unreliable recently-pressed bit. A zero can also mean an inaccessible input desktop or a failed query. These checks do not establish that subsequent input is exclusively synthetic, and cannot distinguish a physical key press from an injected one. See [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate).

## Clocks and mouse interpretation

The native bridge provides local controller tick-base observations at `FRAME_START`; the plan's scheduling clock is `(tick_base-start_tick_base)*15.625` milliseconds. QPC timestamps bracket the Windows API call and bound the age of the native observation. Wall time, controller ticks, rendered image time and OS event consumption are separate measurements. Windows supports QPC comparison on the same system, with a possible one-counter-tick ordering ambiguity across threads; its epoch is not UTC. See [Microsoft's QPC guidance](https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps).

The worker's 250 ms frame-age and 125 ms schedule-lateness limits reject stale input attempts. Its 2.5 second wall hold limit is monitored by the worker loop, not a hard real-time release guarantee: blocking I/O, scheduling delays or termination can delay cleanup. The receipts retain actual API brackets for later inspection.

Polling uses [WindowsPollTimer](../tools/renderer/win32_wait.py), an unnamed, noninheritable waitable timer created with `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`. It arms one relative timeout at a time, uses no completion callback, and closes its handle on exit or error. Unsupported APIs fail without a lower-resolution fallback; no `timeBeginPeriod` or global timer-setting change is used. Microsoft documents this high-resolution flag for Windows 10 version 1803 and later. See [CreateWaitableTimerExW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw) and [SetWaitableTimerEx](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-setwaitabletimerex).

The earlier Python 3.10 host diagnostic requested `time.sleep(0.002)` and observed a median wait of about 15.55 ms and maximum about 16.3 ms. An isolated scoped-timer check on the same host requested 64 waits of 2 ms: observed median 2.4981 ms, 95th-percentile sample 2.6575 ms and maximum 2.9372 ms. No game or input ran during those diagnostics. These are measured wait intervals under those tests' conditions, not guaranteed future polling deadlines. The worker also logs loop-gap and capture-budget-check duration observations.

Mouse events use relative movement, with no absolute-position flag. Positive X/Y means right/down. The Windows relative-mouse contract includes pointer-speed and threshold behavior; Raw Input has a distinct contract that excludes Control Panel mouse-speed effects. Neither establishes a universal conversion from `SendInput` counts to CS2 angles, nor guarantees CS2's raw-input consumer received a particular event. See [MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput) and [RAWMOUSE](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-rawmouse).

The installed SDL3 binary identifies revision `0fa422231`. Its corresponding [primary Windows event source](https://raw.githubusercontent.com/libsdl-org/SDL/0fa422231/src/video/windows/SDL_windowsevents.c) has separate raw and legacy mouse paths, requires active raw-mouse handling and keyboard focus for raw motion, and assigns timestamps while polling buffered raw events. That source makes the ordinary OS route a reasonable calibration target; it does not prove live delivery in this CS2 session. The adapter's run tag avoids SDL's touch-marker patterns while remaining explicitly synthetic input.

## Verification status

The adapter has 62 passing fixture tests covering ABI layouts, event bounds, process/focus failures, partial insertion, cleanup, interruption and exception preservation, including detailed failed-preflight guard evidence. Twelve additional [worker/adapter integration tests](../tests/test_synthetic_windows_runtime.py) cover final-guard frame advancement and cleanup despite log/timer errors. The [poll-timer fixture tests](../tools/renderer/test_win32_wait.py) add 24 cases covering native call arguments, unsupported platforms, watchdog failure and handle cleanup. A read-only check on the installed Windows host confirmed `INPUT=40`, `MOUSEINPUT=32`, `KEYBDINPUT=24` bytes and QPC frequency 10,000,000 Hz. Those checks sent no input.

Live synthetic-001 mouse response and synthetic-004 keyboard response now have the scoped measured evidence above. Mouse calibration and held-out events remain separate; the current held-out cases share the fitting recording. Physical-device latency, exact game input-consumption phase, general button semantics and training/live-control readiness remain unverified.

The [32 Hz executor and label guide](CONTROL_EXECUTION.md) covers the next layer:
fractional mouse-count carry, held-state continuity, effective ready-time scale
checks and partially masked local labels. Its combined executor-003 recording
shows the expected angular/movement response but incomplete raw base mouse-count
accounting. Historical immediate post-command sensitivity observations remain
distinct from the new direct ready-time readback.
