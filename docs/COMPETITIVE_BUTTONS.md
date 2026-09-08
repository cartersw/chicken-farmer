# Recorded competitive button targets

The original 14178 source audit independently verifies the recorded meanings of
forward, back, left, right, crouch, jump, primary attack and reload. It uses the
recovered, exactly hashed server binary; current 14180 local calibration is not
an input to this proof. See `src/cs2_data/competitive_buttons.py` for the pinned
native ranges and the standalone audit in
`data/validation/dust2-buttons-14178-v1/`.

The binary's button-name enum establishes the control masks. Its eight-state
enum, transition table, protobuf parser and native/protobuf copy routines
establish the three exported planes:

| Plane | Supported recorded meaning |
| --- | --- |
| 1 | Button is held at the recorded command's ending boundary |
| 2 | Held state changed between that command's starting and ending boundaries |
| 3 | The retained code contains repeated changes within the command |

Plane 2 is a net change. An off/on/off tap can end released with no net change
while retaining activity in plane 3. The native table reduces longer histories
to earlier codes, so even these three planes cannot reconstruct exact event
counts or ordering. Exported subtick rows remain provenance and do not provide
a complete physical event log.

For each accepted three-command sequence, the label builder can expose held
start/mid/end states, net changed/pressed/released, per-command net changes,
retained activity and unresolved repeated activity. Every field has its own
allowlist and exact command-row proof. Reconstructed payloads must also match
unique live source envelopes. Missing parents, edited raw projections,
ambiguous origins and conflicting known boundaries keep affected fields masked.
An omitted optional scalar means zero only inside a verified present parent.

These targets describe recorded controls, not the original person's physical
keyboard bindings or mouse event times. The existing adapter can express the
same movement/button intent through synthetic keyboard and mouse controls.
Angular targets remain in degrees, independently of raw mouse-count sums.
The [competitive acceptance contract](COMPETITIVE_ACCEPTANCE.md) still requires
all contributing commands to be strictly after the image information bound.

Exact event count, order, subtick offsets, physical input-consumption times and
deployment latency remain unverified. The demo header identifies patch 14178,
but does not expose the original recording server DLL hash. This is the scoped
inspected producer profile, not a claim that the recording server's complete
binary identity was recovered.

The standalone command is useful for inspecting an early source interval:

```powershell
.venv/Scripts/python.exe -m cs2_data.competitive_buttons `
  --demo PATH-TO.dem --parsed PATH-TO-PARSED `
  --start-demo-tick 5000 --through-demo-tick 6400 --out FRESH-REPORT-DIRECTORY
```

Competitive replay acceptance composes this audit with the new full-source
verifier, including later rounds. The standalone prefix command retains its
legacy packet-audit scope. Neither entry point trains a model or sends input.
