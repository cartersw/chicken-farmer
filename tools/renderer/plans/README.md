# Controlled calibration plans

These plans test recorded command fields and timing with native engine console
dispatches in the protected local renderer. They do not implement the final
keyboard and relative-mouse controller. Loading a plan is not a training-data
acceptance decision.

The button experiment uses two separate recordings:

| Plan | Recording | Purpose |
| --- | --- | --- |
| `button-probe-020-v1.json` | control-010 | 20 seconds, 40 actions: holds, taps, overlaps, crouch, jump, firing, reload and sprint |
| `button-confirmation-008-v1.json` | control-011 | 8 seconds, 12 actions: same-boundary triples and a quadruple, with deliberately off-grid scheduled times |
| `button-hypotheses-010-v1.json` | Frozen before control-011 | Rules and masks from control-010, plus hashes of the prior report and confirmation plan |

The frozen artifact is copied byte-for-byte from
`data/calibration/button-hypotheses-010-v1.json`; SHA-256 is
`b1e55563ba9d68acdb9b3d2467343f707fc1178a7ca07ffd20e224fcb75a725f`.
Its source paths refer to the original local evidence. The file remains unchanged;
moving the evidence requires an explicit provenance migration, not silently
changing a frozen experiment.

## Results

Control-010 provided 1,351 canonical commands. The analyzer re-read the original
demo envelopes and verified every canonical protobuf byte against a full live
payload: 1,351 full, zero delta, with one additional non-live full-packet
checkpoint kept separate. Over 1,349 consecutive command pairs, these hypotheses
had no mismatches within tested mask `0x61f`:

- Plane 1 equals the previous plane 1 after applying the ordered raw subtick edges.
- Plane 2 equals the XOR of consecutive plane-1 values.
- Plane 3 equals the intersection of bits pressed and released in the raw subticks.

The third rule had three positive examples, not just all-zero agreement. A
forward press/release in N1002, held-back release/press in N1060, and attack
press/release in N1482 distinguished plane 2 from an "any transition" rule.
Consistent isolated-dispatch mask candidates were attack `1`, jump `2`, duck `4`,
forward `8`, back `16`, left `512`, and right `1024`. Reload and sprint produced
other plane bits without corresponding raw subtick edges in this probe and are
outside the tested mask.

**The independent control-011 confirmation failed.** Its 584 canonical commands
also matched complete full live payloads, with zero delta. Over 582 consecutive
pairs, the plane-1 and plane-2 rules still had zero mismatches. The frozen plane-3
rule had two mismatches. Three of five dispatch batches failed the required
one-for-one ordered edge check:

| Candidate command / demo tick | Known console dispatches | Recorded subtick edges | Raw planes 1 / 2 / 3 |
| --- | --- | --- | --- |
| N907 / 136 | forward press, release, press | press | 8 / 8 / 8 |
| N997 / 226 | back release, press, release | release | omitted / 16 / 16 |
| N1099 / 328 | attack press, release, press, release | press, release | omitted / omitted / 1 |

The two isolated forward-release and back-press batches matched. The frozen
confirmation mask was `0x19`; it did not shrink to whichever bits happened to
appear in control-011. The rules were not adjusted after seeing these failures.

This shows that these exported command records do not preserve every known
console dispatch as a separate subtick edge. It does not locate the reduction
within engine consumption, command construction or demo export. Plane 3 cannot
generally be reconstructed as "both directions appear in the exported subtick
list." It remains raw and masked for semantic training. The observed plane-1 and
plane-2 consistency is useful scoped evidence, but does not validate physical
keyboard states, exact device timing, or arbitrary competitive-demo builds.

A Glock-18 `weapon_fire` event occurs at control-011 demo tick 328, alongside the
quadruple attack candidate whose first two planes are omitted. The generic
validator's "weapon-fire events without attack bits" warning remains intact.
This is a concrete reason to retain event information separately from end-state
held bits. These intentionally single-player, stationary calibration recordings
also do not pass the general competitive-match quality validator.

## Presence and timing limits

Raw button-parent and scalar presence are preserved. The separate numeric
diagnostic view uses the pinned protobuf getter defaults only after standalone
full-payload and projection verification. A missing numeric field has a getter
default of zero; that does not independently prove the gameplay meaning
"released." See the [Protocol Buffers field-presence documentation](https://protobuf.dev/programming-guides/field_presence/).

The dispatch comparison uses an explicit hypothesis: command N+1 after the
native snapshot reports last-processed N, with matching observed controller and
execution clocks. It is not proof of an exact input-consumption timestamp.
Control-011 scheduled 1001, 1429, 2003, 2407 and 4009 ms; native observed elapsed
times were 1015.625, 1453.125, 2015.625, 2421.875 and 4015.625 ms. The analyzer
retains those observations, QPC call bounds and the raw subtick fractions
(all zero here), without converting them to invented physical event times.

The reports stay diagnostic: `training_ready`, `live_control_ready`,
`button_semantics_verified` and `exact_input_timing_verified` remain false.
Keyboard bindings, Windows input delivery and relative-mouse sensitivity still
need a separate measured input adapter. No existing accepted training artifact
was modified by this experiment.

## Re-running the analysis

The report output must be a fresh path. From the project root:

```powershell
.venv/Scripts/python.exe -m cs2_data.calibration_buttons --run data/calibration/control-010 --parsed data/calibration/control-010-parsed/020db6f4e1e6f533594801e8573330daeffc5b52b3445539c12ee5cde3bd380c --out data/calibration/control-010-button-analysis/report-new.json
.venv/Scripts/python.exe -m cs2_data.calibration_buttons --run data/calibration/control-011 --parsed data/calibration/control-011-parsed/7b7713d8d9083ebf9a811e18daed6d30ae2850600854b2900ff4ff13819cc10c --out data/calibration/control-011-button-analysis/report-new.json --frozen-hypotheses tools/renderer/plans/button-hypotheses-010-v1.json
.venv/Scripts/python.exe -m pytest tests/test_calibration_buttons.py -q
```

The original reports are
`data/calibration/control-010-button-analysis/report-v1.json` and
`data/calibration/control-011-button-analysis/report-v1.json`.
