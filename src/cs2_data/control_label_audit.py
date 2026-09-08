"""Source-checked local control profiles and independent 32 Hz label diagnostics.

Profiles are issued only after replaying the frozen mouse and keyboard evidence
checks. Their in-process identity cannot be replaced by a JSON verified flag.
The evidence domain remains controlled local recordings, never competition.
"""
from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
import weakref

from .calibration_analysis import _read_ledger, _read_object
from .calibration_pixels import _owned, audit_calibration_pixels
from .io import batches, sha256_file
from .synthetic_input_analysis import analyze_synthetic_input
from .synthetic_keyboard_analysis import analyze_synthetic_keyboard


PROFILE = "cs2-local-measured-controls-v1"
DOMAIN = "local_controlled_calibration"
BUTTON_MASKS = {"forward": 8, "back": 16, "left": 512, "right": 1024,
                "crouch": 4, "jump": 2, "attack1": 1, "reload": 8192}
MEASURED_BINARY_PROFILE = MappingProxyType({
    "bin/win64/tier0.dll": "b9bf7c956bb31e11be649c31b096ea8813601748b9d984d39f10a9eb4458fb75",
    "bin/win64/rendersystemdx11.dll": "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500",
    "bin/win64/engine2.dll": "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07",
    "csgo/bin/win64/client.dll": "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae",
    "csgo/bin/win64/server.dll": "cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c",
    "bin/win64/networksystem.dll": "fc33c097eca4ab0049590985a772b5b2f556523933d0482f86217889e1283127",
    "bin/win64/schemasystem.dll": "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512",
    "bin/win64/filesystem_stdio.dll": "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef",
})
_ISSUED = weakref.WeakKeyDictionary()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _typed(value):
    """A full canonical row digest preserves scalar types, raw bytes and presence."""
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is int:
        return ["int", str(value)]
    if type(value) is float:
        _require(math.isfinite(value), "Nonfinite canonical control row")
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, list):
        return ["list", [_typed(item) for item in value]]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ["dict", [[key, _typed(value[key])] for key in sorted(value)]]
    raise ValueError("Unsupported canonical control row value")


def canonical_row_sha256(row):
    _require(isinstance(row, dict), "Expected complete canonical command row")
    return hashlib.sha256(json.dumps(_typed(row), separators=(",", ":")).encode()).hexdigest()


class MeasuredControlProfile:
    """Immutable, process-local proof issued by load_measured_control_profile.

    JSON serialization is descriptive evidence and cannot recreate this object.
    Executors check this once at construction; label builders additionally bind
    every full input row to a freshly revalidated original-demo command.
    """
    __slots__ = ("_data", "_row_digests", "_source_files", "_pixel_stats", "__weakref__")

    def __init__(self, *args, **kwargs):
        raise TypeError("MeasuredControlProfile must be issued by load_measured_control_profile")

    def __setattr__(self, name, value):
        raise AttributeError("Measured control profiles are immutable")

    @property
    def profile_id(self):
        return PROFILE

    @property
    def domain(self):
        return DOMAIN

    @property
    def degrees_per_mouse_count(self):
        return self._data["degrees_per_mouse_count"]

    @property
    def mouse_counts_per_degree(self):
        return self._data["mouse_counts_per_degree"]

    @property
    def binary_profile(self):
        return self._data["binary_profile"]

    @property
    def sensitivity(self):
        return self._data["sensitivity"]

    @property
    def provenance(self):
        return self._data["provenance"]

    @property
    def provenance_sha256(self):
        return self._data["provenance_sha256"]

    @property
    def supported_button_masks(self):
        return self._data["supported_button_masks"]

    @property
    def ready_requirements(self):
        return self._data["ready_requirements"]

    def require_checked(self):
        registered = _ISSUED.get(self)
        _require(registered is not None and len(registered) == 4 and
                 all(current is original for current, original in zip(
                     (getattr(self, "_data", None), getattr(self, "_row_digests", None),
                      getattr(self, "_source_files", None), getattr(self, "_pixel_stats", None)), registered)),
                 "Control profile was not issued by source verification or has been replaced")

    def require_command_rows(self, rows):
        self.require_checked()
        _require(bool(rows), "Control labels need canonical command rows")
        for row in rows:
            _require(canonical_row_sha256(row) in self._row_digests,
                     "Canonical command row is outside the checked local calibration evidence")

    def verify_sources_unchanged(self):
        """Rehash metadata/ledgers/commands/DLL; use saved file metadata for pixels."""
        self.require_checked()
        for path, digest in self._source_files.items():
            _require(sha256_file(Path(path)) == digest, "Measured control source changed: " + path)
        for path, expected in self._pixel_stats.items():
            metadata = Path(path).stat()
            _require((metadata.st_size, metadata.st_mtime_ns) == expected,
                     "Previously checked calibration pixel file changed: " + path)

    def to_dict(self):
        self.require_checked()
        return _thaw(self._data)


def _issue_profile(data, row_digests, source_files, pixel_stats):
    profile = object.__new__(MeasuredControlProfile)
    attributes = (_freeze(data), frozenset(row_digests), MappingProxyType(dict(source_files)),
                  MappingProxyType(dict(pixel_stats)))
    for name, value in zip(("_data", "_row_digests", "_source_files", "_pixel_stats"), attributes):
        object.__setattr__(profile, name, value)
    _ISSUED[profile] = attributes
    return profile


def _cells(label):
    for axis, cell in label["aim_delta_deg"].items():
        yield "aim_delta_deg." + axis, cell
    for name, button in label["buttons"].items():
        for field, cell in button.items():
            if field == "per_command_net_changed":
                for index, value in enumerate(cell):
                    yield f"buttons.{name}.{field}.{index}", value
            else:
                yield f"buttons.{name}.{field}", cell
    for name in ("exact_button_event_count", "exact_button_event_order", "exact_button_event_offsets_ns"):
        yield name, label[name]


def audit_label_rows(rows, profile):
    """Produce nonoverlapping two-target windows without repairing missing rows.

    Canonical row zero is the initial predecessor. A discontinuity is retained
    as a masked candidate instead of closing the gap by shifting timestamps.
    """
    from .control_labels import build_control_label, EXACT_CHANNELS
    profile.require_command_rows(rows)
    labels, counts, reasons = [], {}, Counter()
    for start in range(0, len(rows) - 2, 2):
        chunk = rows[start:start + 3]
        label = build_control_label(chunk, profile=profile)
        _require(all(label[name]["valid"] is False and label[name]["value"] is None for name in EXACT_CHANNELS),
                 "Label builder incorrectly exposed uncalibrated exact-event channels")
        _require(all(label[name] is False for name in ("training_ready", "live_control_ready",
                 "exact_input_timing_verified", "competitive_source_verified", "image_alignment_verified")),
                 "Local label builder incorrectly promoted readiness")
        label["audit_candidate_index"] = len(labels)
        labels.append(label)
        reasons.update(label["reason_codes"])
        for name, cell in _cells(label):
            counter = counts.setdefault(name, Counter())
            counter["valid" if cell["valid"] else "masked"] += 1
            if cell["valid"] and type(cell["value"]) is bool:
                counter["true" if cell["value"] else "false"] += 1
    targets = {}
    for label in labels:
        for index, raw in enumerate(label["raw_command_provenance"][1:]):
            number = raw["command_number"]
            _require(number not in targets, "Duplicate target command number in local label source")
            targets[number] = (label, index)
    stats = {"canonical_rows": len(rows), "candidates": len(labels),
             "target_commands": 2 * len(labels), "unused_tail_rows": max(0, len(rows) - 1 - 2 * len(labels)),
             "label_status_counts": dict(Counter(label["status"] for label in labels)),
             "field_counts": {key: dict(value) for key, value in sorted(counts.items())},
             "chunk_reason_counts": dict(reasons)}
    return labels, targets, stats


def _keyboard_label_checks(report, targets):
    controls = {"W": "forward", "S": "back", "A": "left", "D": "right", "SPACE": "jump",
                "LCTRL": "crouch", "mouse_left": "attack1", "R": "reload"}
    edges = {}
    for edge in report["edges"]:
        name = controls.get(edge["control"])
        _require(name is not None, "Keyboard response contains an unsupported measured control")
        checks = []
        for number in edge["plane_change_consistency_command_numbers"]:
            if number <= edge["observed_already_processed_before_insertion"]:
                continue
            target = targets.get(number)
            if target is None:
                checks.append({"command_number": number, "status": "outside_candidate_targets"})
                continue
            label, index = target
            button = label["buttons"][name]
            held = button["held_mid" if index == 0 else "held_end"]
            changed = button["per_command_net_changed"][index]
            activity = button["recorded_activity_present"]
            measured = held["valid"] and changed["valid"] and activity["valid"]
            agrees = measured and held["value"] is edge["pressed"] and changed["value"] is True and activity["value"] is True
            checks.append({"command_number": number, "candidate_index": label["audit_candidate_index"],
                           "target_index": index, "held": held, "net_changed_at_command": changed,
                           "recorded_activity_present": activity,
                           "status": "observed_edge_agrees" if agrees else "masked" if not measured else "contradiction"})
        edges[edge["id"]] = {"id": edge["id"], "control": name, "pressed": edge["pressed"],
            "status": "observed_edge_agrees" if checks and all(c["status"] == "observed_edge_agrees" for c in checks)
                      else "incomplete_or_inconsistent", "checks": checks,
            "exact_insertion_to_command_assignment_verified": False}
    phases = []
    for phase in report["phases"]:
        pair = [edges[phase[key]] for key in ("press_id", "release_id")]
        phases.append({"press_id": phase["press_id"], "release_id": phase["release_id"],
            "control": pair[0]["control"], "independently_recomputed_response_status": phase["status"],
            "observed_command_window_inclusive": phase["command_number_window_inclusive"],
            "edge_checks": pair,
            "status": "observed_response_and_labels_agree" if phase["status"] == "observed_response_evidence" and
                      all(edge["status"] == "observed_edge_agrees" for edge in pair) else "incomplete_or_inconsistent"})
    return phases


def _mouse_label_checks(report, targets, profile):
    from .normalize import effective_scalar
    cases = []
    matrix = profile.degrees_per_mouse_count
    for case in report["cases"]:
        diagnostics = case["recorded_command_diagnostics"]
        rows = diagnostics["commands"]
        values, missing = {"yaw": [], "pitch": []}, []
        counts = [0, 0]
        for row in rows:
            number = row["command_number"]
            target = targets.get(number)
            if target is None:
                missing.append(number)
                continue
            label, index = target
            for axis in values:
                cell = label["aim_delta_deg"][axis]
                deltas = label["per_command_aim_delta_deg"][axis]
                if not cell["valid"] or len(deltas) != 2:
                    missing.append(number)
                else:
                    values[axis].append(deltas[index])
            for index, field in enumerate(("mousedx_raw", "mousedy_raw")):
                value = effective_scalar(row, field, "base_present")
                if type(value) is not int:
                    missing.append(number)
                else:
                    counts[index] += value
        totals = [math.fsum(values[axis]) for axis in ("yaw", "pitch")]
        predicted = [math.fsum(matrix[axis][i] * counts[i] for i in (0, 1)) for axis in (0, 1)]
        residual = [totals[i] - predicted[i] for i in (0, 1)]
        # This is the existing frozen response tolerance, not fitted to labels.
        tolerance = [report["gain_matrix"]["absolute_tolerance_degrees"] +
                     report["gain_matrix"]["relative_tolerance"] * abs(value) for value in predicted]
        agrees = not missing and counts == [case["dx"], case["dy"]] and all(abs(residual[i]) <= tolerance[i] for i in (0, 1))
        cases.append({"id": case["id"], "split": case["split"], "command_count": len(rows),
            "command_number_window_inclusive": [diagnostics["before_last_processed_command"] + 1,
                                                 diagnostics["after_last_processed_command"]],
            "raw_mouse_count_sum_with_known_base_defaults": counts, "injected_count": [case["dx"], case["dy"]],
            "sum_recorded_label_yaw_pitch_degrees": totals, "measured_gain_prediction_yaw_pitch_degrees": predicted,
            "residual_yaw_pitch_degrees": residual, "frozen_tolerance_degrees": tolerance,
            "masked_or_missing_command_numbers": sorted(set(missing)),
            "status": "recorded_label_and_measured_gain_agree" if agrees else "incomplete_or_inconsistent",
            "exact_insertion_to_command_assignment_verified": False})
    return cases


def audit_control_labels(mouse_run, mouse_parsed, keyboard_run, keyboard_parsed, keyboard_protocol, *, output_dir):
    """Revalidate source calibration and publish local label candidates + audit."""
    output = Path(output_dir).resolve()
    _require(not output.exists(), "Control label audit requires a fresh output directory")
    sources = [Path(value).resolve() for value in (mouse_run, mouse_parsed, keyboard_run, keyboard_parsed)]
    _require(not any(output.is_relative_to(path) for path in sources), "Label output must be outside source artifacts")
    output.mkdir(parents=True, exist_ok=False)
    profile = load_measured_control_profile(*sources, keyboard_protocol, evidence_output=output / "profile-evidence")
    per_source, targets = {}, {}
    for name, parsed in (("mouse", sources[1]), ("keyboard", sources[3])):
        rows = [row for batch in batches(parsed / "usercmd.parquet") for row in batch]
        labels, targets[name], per_source[name] = audit_label_rows(rows, profile)
        path = output / (name + "-labels.jsonl")
        with path.open("x", encoding="utf-8") as handle:
            for label in labels:
                handle.write(json.dumps(label, separators=(",", ":"), allow_nan=False) + "\n")
        per_source[name]["labels_file"] = {"path": str(path), "sha256": sha256_file(path)}
    mouse = _read_object(output / "profile-evidence/mouse.json")
    keyboard = _read_object(output / "profile-evidence/keyboard.json")
    mouse_checks = _mouse_label_checks(mouse, targets["mouse"], profile)
    keyboard_checks = _keyboard_label_checks(keyboard, targets["keyboard"])
    complete = all(row["status"] == "recorded_label_and_measured_gain_agree" for row in mouse_checks) and all(
        row["status"] == "observed_response_and_labels_agree" for row in keyboard_checks)
    report = {"schema_version": 1, "profile": "cs2-local-control-label-audit-v1", "domain": DOMAIN,
        "status": "scoped_calibration_label_checks_agree" if complete else "incomplete_or_inconsistent",
        "measured_profile_sha256": profile.provenance_sha256,
        "measured_profile_file": str(output / "profile-evidence/profile.json"),
        "per_source": per_source, "mouse_case_checks": mouse_checks, "keyboard_phase_checks": keyboard_checks,
        "mouse_case_status_counts": dict(Counter(row["status"] for row in mouse_checks)),
        "keyboard_phase_status_counts": dict(Counter(row["status"] for row in keyboard_checks)),
        "historical_ready_sensitivity_directly_observed": False,
        "sensitivity_evidence": "requested_and_persisted_1_plus_empirical_gain; early_runtime_read_1.096426_retained_in_profile",
        "training_ready": False, "competitive_source_verified": False, "image_alignment_verified": False,
        "exact_input_timing_verified": False,
        "limits": ["Only the two freshly checked local demo sources can use this process-local profile.",
            "Candidate target pairs partition canonical commands; no model observation image is selected here.",
            "Recorded button parents may be absent, so unknown is not converted into no input.",
            "The measured count-to-degree conversion checks labels; recorded degree targets are not rescaled.",
            "Observed command neighborhoods do not establish exact OS insertion or physical device consumption times."]}
    profile.verify_sources_unchanged()
    with (output / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    with (output / "report.md").open("x", encoding="utf-8") as handle:
        handle.write("# Local control label audit\n\nStatus: `" + report["status"] + "`.\n\n")
        for name, stats in per_source.items():
            handle.write(f"- {name}: {stats['candidates']} two-command candidates; {stats['label_status_counts']}.\n")
        handle.write(f"\nMouse cases: {report['mouse_case_status_counts']}. Keyboard phases: {report['keyboard_phase_status_counts']}.\n\n")
        handle.write("Historical sensitivity 1 was requested and persisted; the early runtime read was 1.096426. "
                     "The profile preserves that limitation and requires actual ready-time settings for future execution.\n\n")
        handle.write("These are scoped local diagnostic labels. Training acceptance, competitive applicability, image alignment, "
                     "and exact event timing remain unverified.\n")
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("mouse-run", "mouse-parsed", "keyboard-run", "keyboard-parsed", "keyboard-protocol", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--profile-only", action="store_true", help="Recompute and retain the profile without generating labels")
    args = parser.parse_args(argv)
    values = (args.mouse_run, args.mouse_parsed, args.keyboard_run, args.keyboard_parsed, args.keyboard_protocol)
    if args.profile_only:
        result = load_measured_control_profile(*values, evidence_output=args.output).to_dict()
        print(json.dumps({"profile": result["profile"], "provenance_sha256": result["provenance_sha256"], "training_ready": False}))
    else:
        report = audit_control_labels(*values, output_dir=args.output)
        print(json.dumps({"status": report["status"], "per_source": {key: value["label_status_counts"] for key, value in report["per_source"].items()},
                          "mouse_case_status_counts": report["mouse_case_status_counts"],
                          "keyboard_phase_status_counts": report["keyboard_phase_status_counts"], "training_ready": False}))


def _pixel_stats_before_audit(run):
    archive = _read_object(_owned(run, "capture_frame_files.json"))
    rows = archive.get("frames")
    _require(isinstance(rows, list) and 0 < len(rows) <= 20000, "Invalid bounded calibration pixel inventory")
    result = {}
    for row in rows:
        path = _owned(run, "frames/" + row["archived_name"])
        metadata = path.stat()
        result[str(path.resolve())] = (metadata.st_size, metadata.st_mtime_ns)
    return result


def _configuration(run, worker):
    rows = _read_ledger(_owned(run, "calibration_ledger.jsonl"))
    configurations = [row for row in rows if row["event"] == "synthetic_input_configuration"]
    _require(len(configurations) == 1, "Missing unique calibration setup configuration")
    config = configurations[0]
    required = {"bind w +forward", "bind a +left", "bind s +back", "bind d +right", "bind SPACE +jump",
                "bind CTRL +duck", "bind r +reload", "bind MOUSE1 +attack", "sensitivity 1"}
    _require(required <= set(config.get("binding_setup_commands", [])), "Calibration input setup bindings changed")
    path = _owned(run, "replay-settings/cfg/cs2_user_convars_0_slot0.vcfg")
    config_bytes = path.read_bytes()
    text = config_bytes.decode("utf-8-sig")
    import re
    values = {}
    for name, expected in (("sensitivity", 1.0), ("m_yaw", .022), ("m_pitch", .022), ("sensitivity_y_scale", 1.0)):
        matches = re.findall(r'"' + re.escape(name) + r'"\s+"([^"\r\n]+)"', text)
        _require(len(matches) == 1, "Missing unique persisted mouse setting: " + name)
        try:
            actual = float(matches[0])
        except ValueError as error:
            raise ValueError("Invalid persisted mouse setting: " + name) from error
        _require(actual == expected, "Persisted calibration mouse setting changed: " + name)
        values[name] = matches[0]
    plugin = _owned(run, "renderer-sandbox/bin/win64/server.dll")
    _require(sha256_file(plugin) == worker.get("plugin_sha256"), "Archived calibration plugin bytes changed")
    return {"requested_sensitivity": 1.0, "persisted_settings": values,
            "early_setup_observed_convars": config.get("observed_convars"),
            "historical_ready_sensitivity_directly_observed": False,
            "setting_evidence_strength": "requested_and_persisted_setting_plus_empirically_measured_response",
            "early_read_note": "The immediate setup read precedes queued command application; it is not rewritten as a ready-time observation.",
            "config_file": str(path), "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "plugin_file": str(plugin), "plugin_sha256": worker["plugin_sha256"]}


def load_measured_control_profile(mouse_run, mouse_parsed, keyboard_run, keyboard_parsed, keyboard_protocol,
                                  *, evidence_output):
    """Recompute and retain frozen local evidence, then issue an immutable profile.

    This is deliberately an initial loading operation. Per-decision execution
    uses the issued snapshot; verify_sources_unchanged checks metadata and pixel
    file statistics before launch without hashing gigabytes of pixels again.
    """
    mouse_run, mouse_parsed, keyboard_run, keyboard_parsed, keyboard_protocol, output = [Path(value).resolve()
        for value in (mouse_run, mouse_parsed, keyboard_run, keyboard_parsed, keyboard_protocol, evidence_output)]
    _require(not output.exists() and not any(output.is_relative_to(path) for path in
             (mouse_run, mouse_parsed, keyboard_run, keyboard_parsed)), "Control profile requires fresh evidence output outside source runs")
    mouse_worker = _read_object(_owned(mouse_run, "calibration.json"))
    keyboard_worker = _read_object(_owned(keyboard_run, "calibration.json"))
    _require(mouse_worker.get("binary_profile") == keyboard_worker.get("binary_profile") == MEASURED_BINARY_PROFILE and
             mouse_worker.get("plugin_sha256") == keyboard_worker.get("plugin_sha256"),
             "Mouse and keyboard calibration builds/plugins disagree")
    configurations = [_configuration(run, worker) for run, worker in
                      ((mouse_run, mouse_worker), (keyboard_run, keyboard_worker))]
    pixel_stats = {**_pixel_stats_before_audit(mouse_run), **_pixel_stats_before_audit(keyboard_run)}
    output.mkdir(parents=True, exist_ok=False)
    mouse = analyze_synthetic_input(mouse_run, mouse_parsed, output / "mouse.json")
    keyboard = analyze_synthetic_keyboard(keyboard_run, keyboard_parsed, keyboard_protocol, output / "keyboard.json")
    keyboard_pixels = audit_calibration_pixels(keyboard_run, output / "keyboard-pixels.json")
    _require(mouse.get("status") == "bounded_mouse_gain_calibrated" and not mouse.get("issues") and
             keyboard.get("status") == "response_evidence_observed_for_all_phases" and
             keyboard.get("edge_status_counts") == {"response_record_observed": 20} and
             keyboard.get("phase_status_counts") == {"observed_response_evidence": 10} and
             keyboard_pixels.get("status") == "all_archived_pixels_match_readback",
             "Frozen local mouse/keyboard response or pixel evidence did not pass")
    _require(all(case["recorded_command_diagnostics"].get("status") == "measured_raw_command_neighborhood" and
                 all(item.get("sum_matches_injected_count") is True for item in
                     case["recorded_command_diagnostics"]["mouse_count_sums"].values()) for case in mouse["cases"]),
             "Recorded mouse counts disagree with injected calibration counts")
    source_files = {}
    for name, digest in mouse["source_hashes"].items():
        source_files[str(mouse_run / name)] = digest
    for name, digest in mouse["source_pixel_audit"]["source_hashes"].items():
        source_files[str(mouse_run / name)] = digest
    for name, digest in mouse["parsed_hashes"].items():
        source_files[str(mouse_parsed / name)] = digest
    for source in keyboard["sources"].values():
        source_files[str(Path(source["path"]).resolve())] = source["sha256"]
    for name, digest in keyboard_pixels["source_hashes"].items():
        source_files[str(keyboard_run / name)] = digest
    for config in configurations:
        for kind in ("config", "plugin"):
            source_files[config[kind + "_file"]] = config[kind + "_sha256"]
    for filename in ("mouse.json", "mouse.md", "keyboard.json", "keyboard-pixels.json"):
        source_files[str(output / filename)] = sha256_file(output / filename)
    # Bind the code used to derive this process-local snapshot as well as data.
    implementation_files = ("control_label_audit.py", "control_labels.py", "synthetic_input_analysis.py",
        "synthetic_keyboard_analysis.py", "calibration_pixels.py", "calibration_analysis.py",
        "calibration_buttons.py", "causal_acceptance.py", "normalize.py", "io.py")
    implementation_hashes = {}
    for name in implementation_files:
        path = Path(__file__).parent / name
        source_files[str(path.resolve())] = implementation_hashes[name] = sha256_file(path)
    row_digests, demo_ids, counts = set(), [], []
    for parsed in (mouse_parsed, keyboard_parsed):
        manifest = _read_object(parsed / "manifest.json")
        demo_ids.append(manifest["demo_id"])
        count = 0
        for batch in batches(parsed / "usercmd.parquet"):
            for row in batch:
                _require(row.get("demo_id") == manifest["demo_id"], "Canonical label source demo identity changed")
                row_digests.add(canonical_row_sha256(row))
                count += 1
        counts.append(count)
    provenance = {"bound_demo_ids": demo_ids, "canonical_command_counts": counts,
                  "calibration_inputs": {"mouse_run": str(mouse_run), "mouse_parsed": str(mouse_parsed),
                      "keyboard_run": str(keyboard_run), "keyboard_parsed": str(keyboard_parsed),
                      "keyboard_protocol": str(keyboard_protocol)},
                  "source_files": source_files, "configuration_evidence": configurations,
                  "calibration_plugin_sha256": mouse_worker["plugin_sha256"],
                  "mouse_frozen_plan_sha256": mouse["frozen_plan_sha256"],
                  "keyboard_frozen_protocol": keyboard["frozen_protocol"],
                  "pixel_counts": [len(mouse["source_pixel_audit"]["frames"]), len(keyboard_pixels["frames"])],
                  "angle_case_counts": {"fit": 4, "held_out": 8},
                  "implementation_sha256": implementation_hashes,
                  "historical_ready_sensitivity_directly_observed": False,
                  "future_execution_requires_actual_ready_convars": True,
                  "pixel_recheck_policy": "initial_full_RGB_and_file_hash_audit; subsequent_size_mtime_checks",
                  "label_source_scope": "only_complete_rows_from_these_two_checked_local_demos"}
    digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    data = {"schema_version": 1, "profile": PROFILE, "domain": DOMAIN, "provenance_sha256": digest,
            "degrees_per_mouse_count": mouse["gain_matrix"]["degrees_per_mouse_count"],
            "mouse_counts_per_degree": mouse["gain_matrix"]["mouse_counts_per_degree"],
            "binary_profile": mouse_worker["binary_profile"], "sensitivity": 1.0,
            "ready_requirements": {"sensitivity": "1", "m_yaw": "0.022", "m_pitch": "0.022", "sensitivity_y_scale": "1"},
            "supported_button_masks": BUTTON_MASKS, "provenance": provenance,
            "exact_input_timing_verified": False, "training_ready": False, "live_control_ready": False}
    profile = _issue_profile(data, row_digests, source_files, pixel_stats)
    profile.verify_sources_unchanged()
    with (output / "profile.json").open("x", encoding="utf-8") as handle:
        json.dump(profile.to_dict(), handle, indent=2, allow_nan=False)
        handle.write("\n")
    return profile


if __name__ == "__main__":
    # Keep the sealed class identity canonical under ``python -m``. Otherwise
    # this module's __main__ copy differs from the class imported by labels.
    from cs2_data.control_label_audit import main as canonical_main
    canonical_main()
