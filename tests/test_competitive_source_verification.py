"""Mutable eligibility claims cannot substitute for fresh original demo output."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import competitive_source_verification as verification
from cs2_data import command_reverification as commands
from cs2_data.io import sha256_file


def publish(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture(autouse=True)
def clear_cache():
    verification._COMPLETED.clear()
    yield
    verification._COMPLETED.clear()


@pytest.fixture
def source(tmp_path, monkeypatch):
    original = tmp_path/"match.dem"
    original.write_bytes(b"original source bytes")
    source_id = sha256_file(original)
    canonical, reconstructed = tmp_path/"canonical", tmp_path/"decoder-output"
    canonical.mkdir(); reconstructed.mkdir()
    rows = [{"command_row_id": i, "demo_id": source_id, "demo_tick": 10+i,
        "command_protobuf": bytes([i]), "is_freeze_time": False, "alive": True,
        "buttonstate1": None, "view_yaw": float(i)} for i in range(3)]
    for name, values in (("usercmd.parquet", rows),
        ("player_state.parquet", [{"demo_tick": 10, "alive": True, "is_paused": False}]),
        ("rounds.parquet", [{"round_id": 1, "start_tick": 0, "freeze_end_tick": 5, "end_tick": 30}]),
        ("events.parquet", [{"demo_tick": 15, "kind": "weapon_fire"}])):
        pq.write_table(pa.Table.from_pylist(values), canonical/name)
    manifest = {"demo_id": source_id, "sha256": source_id, "file_name": original.name,
        "file_size": original.stat().st_size, "source_path": str(original), "match_id": "series-match",
        "tick_rate": 64, "parse_status": "complete", "partial": False,
        **commands.DECODER_FIELDS, "command_count": 3, "state_count": 1, "round_count": 1,
        "warnings": {"usercmd_baseline_missing": 2}, "ingested_at": "earlier",
        "files": {name: sha256_file(canonical/name) for name in verification.TABLES}}
    publish(canonical/"manifest.json", manifest)
    for path in canonical.iterdir(): shutil.copy(path, reconstructed/path.name)
    fresh_manifest = deepcopy(manifest); fresh_manifest.update(ingested_at="fresh", source_path="old descriptive location")
    publish(reconstructed/"manifest.json", fresh_manifest)
    phase = {"schema_version": 1, "producer": "cs2-phases-v1", "demo_id": source_id,
        "source_demo_sha256": source_id, "source_demo_path": str(original), "parse_status": "complete",
        "partial": False, "timing_clock": "demo_tick", "parser_version": "v6.0.0-alpha.0",
        "events": [{"kind": "round_start", "demo_tick": 0, "is_warmup": False}, {"kind": "demo_end", "demo_tick": 40}]}
    context = {**{k: v for k, v in phase.items() if k != "events"}, "schema_version": 2, "producer": "cs2-context-v2",
        "warning_policy": "rule-and-shot-evidence-v1", "evidence_loss_warnings": 0,
        "segments": [{"start_demo_tick": 0, "end_demo_tick": 40, "is_paused": False}], "weapon_fire_observations": []}
    phase_path, context_path = tmp_path/"phase.json", tmp_path/"context.json"
    publish(phase_path, phase); publish(context_path, context)
    tool = tmp_path/"pinned.exe"; tool.write_bytes(b"pinned decoder fixture")
    monkeypatch.setattr(verification, "_tool_files", lambda: {str(tool): sha256_file(tool)})
    monkeypatch.setattr(commands, "PROJECT", tmp_path)
    calls = []
    def extract(actual_source, match_id, through, output):
        assert actual_source == original and match_id == "series-match" and through == 0
        calls.append(output)
        shutil.copytree(reconstructed, output/source_id)
        return {"exit_code": 1}
    def sidecar(kind, source, output):
        value = deepcopy(phase if kind == "phase" else context)
        value["source_demo_path"] = "descriptive fresh location"
        publish(output, value)
    monkeypatch.setattr(commands, "_extract", extract)
    monkeypatch.setattr(verification, "_run_sidecar", sidecar)
    return SimpleNamespace(source=original, canonical=canonical, reconstructed=reconstructed, manifest=manifest,
        phase=phase, context=context, phase_path=phase_path, context_path=context_path, rows=rows,
        calls=calls, tool=tool, extract=extract, sidecar=sidecar)


def verify(fixture, rows=None):
    return verification.reverify_competitive_source(fixture.source, fixture.canonical,
        fixture.phase_path, fixture.context_path, command_rows=rows)


def test_full_reconstruction_preserves_failures_without_promoting_build_or_training(source):
    result = verify(source, source.rows[1:])
    assert len(source.calls) == 1 and not source.calls[0].exists()
    assert result["status"] == "original_competitive_source_reconstructed_exactly"
    assert result["provenance"]["comparison"]["fresh_warning_counts"] == {"usercmd_baseline_missing": 2}
    assert set(result["command_row_digests"]) == {1, 2}
    assert result["recorded_server_build_support_verified"] is False
    assert result["button_semantics_verified"] is False and result["training_ready"] is False
    assert all(v["live_packet_envelope_association_required"] for v in result["command_sources"].values())
    assert set(result["provenance"]["comparison"]["tables"]) == set(verification.TABLES)


def test_only_process_local_unchanged_proof_reuses_decode_and_outputs_are_detached(source):
    first = verify(source)
    first["provenance"]["comparison"]["tables"]["rounds.parquet"]["row_count"] = 999
    second = verify(source, source.rows)
    assert len(source.calls) == 1
    assert second["provenance"]["comparison"]["tables"]["rounds.parquet"]["row_count"] == 1
    assert len(second["command_row_digests"]) == 3


def test_returned_decoder_profile_cannot_mutate_a_later_verification(source):
    expected = deepcopy(commands.DECODER_FIELDS)
    first = verify(source)
    first["provenance"]["decoder_profile"].clear()
    assert commands.DECODER_FIELDS == expected
    assert verify(source)["provenance"]["decoder_profile"] == expected


def test_failed_selected_row_verification_does_not_publish_a_new_cache_entry(source):
    invalid = deepcopy(source.rows)
    invalid[0]["alive"] = False
    with pytest.raises(ValueError, match="Requested command row differs"):
        verify(source, invalid)
    assert not verification._COMPLETED
    verify(source, source.rows)
    assert len(source.calls) == 2


def test_completed_source_cache_has_a_bounded_lru_and_never_loads_saved_proofs(source, monkeypatch):
    monkeypatch.setattr(verification, "MAX_COMPLETED_SOURCES", 2)
    for label in ("first", "second", "first", "third"):
        manifest = {**source.manifest, "ingested_at": label}
        publish(source.canonical/"manifest.json", manifest)
        verify(source)
    assert len(verification._COMPLETED) == 2 and len(source.calls) == 3
    publish(source.canonical/"manifest.json", {**source.manifest, "ingested_at": "first"})
    verify(source)
    assert len(source.calls) == 3
    publish(source.canonical/"manifest.json", {**source.manifest, "ingested_at": "second"})
    verify(source)
    assert len(source.calls) == 4 and len(verification._COMPLETED) == 2


@pytest.mark.parametrize("table,field,value", [("player_state.parquet", "alive", False),
    ("player_state.parquet", "is_paused", True), ("rounds.parquet", "freeze_end_tick", 1),
    ("events.parquet", "demo_tick", 14), ("usercmd.parquet", "command_protobuf", b"invented"),
    ("usercmd.parquet", "is_freeze_time", True)])
def test_edited_and_rehashed_eligibility_or_raw_source_rejects(source, table, field, value):
    verify(source)  # Establish a valid cached proof before mutation.
    values = pq.read_table(source.canonical/table).to_pylist(); values[0][field] = value
    pq.write_table(pa.Table.from_pylist(values), source.canonical/table)
    manifest = deepcopy(source.manifest); manifest["files"][table] = sha256_file(source.canonical/table)
    publish(source.canonical/"manifest.json", manifest)
    with pytest.raises(ValueError, match="differs from original-demo reconstruction"):
        verify(source)
    assert len(source.calls) == 2


@pytest.mark.parametrize("kind", ["phase", "context"])
def test_edited_and_rehashed_sidecar_cannot_change_competitive_eligibility(source, kind):
    verify(source)
    if kind == "phase":
        value = deepcopy(source.phase); value["events"][0]["is_warmup"] = True
        publish(source.phase_path, value)
    else:
        value = deepcopy(source.context); value["segments"][0]["is_paused"] = True
        publish(source.context_path, value)
    with pytest.raises(ValueError, match="eligibility differs"):
        verify(source)


@pytest.mark.parametrize("field,value", [("warnings", {}), ("command_count", 2), ("map", "de_other")])
def test_changed_manifest_semantics_reject_even_when_all_table_hashes_match(source, field, value):
    changed = deepcopy(source.manifest); changed[field] = value
    publish(source.canonical/"manifest.json", changed)
    with pytest.raises(ValueError, match="metadata/counts/warnings differ"):
        verify(source)


@pytest.mark.parametrize("change", ["alter", "missing", "repeat", "bool_id"])
def test_requested_row_subset_must_match_full_source_and_exact_types(source, change):
    rows = deepcopy(source.rows)
    if change == "alter": rows[0]["alive"] = 1
    elif change == "missing": rows[0]["command_row_id"] = 500
    elif change == "repeat": rows[1] = deepcopy(rows[0])
    else: rows[0]["command_row_id"] = False
    with pytest.raises(ValueError, match="Requested"):
        verify(source, rows)


def test_changed_original_bytes_do_not_reuse_completed_proof(source):
    verify(source)
    source.source.write_bytes(b"different source bytes")
    with pytest.raises(ValueError, match="Original demo bytes|Source manifest identity"):
        verify(source)
    assert len(source.calls) == 1


def test_source_cache_rehashes_original_bytes_even_with_unchanged_size_and_mtime(source):
    verify(source)
    before = source.source.stat()
    source.source.write_bytes(b"original SOURCE bytes")
    os.utime(source.source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.source.stat().st_size == before.st_size
    assert source.source.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(ValueError, match="Original demo bytes"):
        verify(source)


def test_tool_mutation_during_reconstruction_rejects_and_never_caches(source, monkeypatch):
    def mutation(*args):
        result = source.extract(*args)
        source.tool.write_bytes(b"changed decoder fixture")
        return result
    monkeypatch.setattr(commands, "_extract", mutation)
    with pytest.raises(ValueError, match="changed during verification"):
        verify(source)
    assert not verification._COMPLETED


def test_partial_output_cannot_be_accepted_even_with_exit_one(source):
    value = deepcopy(source.manifest); value.update(parse_status="partial", partial=True)
    publish(source.reconstructed/"manifest.json", value)
    with pytest.raises(ValueError, match="complete, nonpartial"):
        verify(source)


@pytest.mark.parametrize("change", ["scalar_type", "negative_zero", "nested_negative_zero", "presence", "order"])
def test_arrow_comparison_preserves_types_presence_float_bits_and_order(tmp_path, change):
    a, b = tmp_path/"a.parquet", tmp_path/"b.parquet"
    original = [{"n": 1, "angle": 0.0, "history": [{"fraction": 0.0}], "parent": None},
        {"n": 2, "angle": 1.0, "history": [], "parent": None}]
    fresh = deepcopy(original)
    if change == "scalar_type": fresh[0]["n"] = 1.0
    elif change == "negative_zero": fresh[0]["angle"] = -0.0
    elif change == "nested_negative_zero": fresh[0]["history"][0]["fraction"] = -0.0
    elif change == "presence": fresh[0]["parent"] = 0
    else: fresh.reverse()
    pq.write_table(pa.Table.from_pylist(original), a); pq.write_table(pa.Table.from_pylist(fresh), b)
    with pytest.raises(ValueError): verification.compare_tables(a, b)


def test_different_parquet_compression_can_match_every_typed_value(tmp_path):
    a, b = tmp_path/"a.parquet", tmp_path/"b.parquet"
    table = pa.Table.from_pylist([{"command_row_id": i, "nullable": None, "nested": [{"x": -0.0}]} for i in range(4)])
    pq.write_table(table, a, compression="none"); pq.write_table(table, b, compression="zstd", row_group_size=1)
    result = verification.compare_tables(a, b, require_sequential_commands=True)
    assert result["comparison"] == "all_typed_values_bitwise_floats_and_source_order"


def test_sidecar_commands_are_fixed_arguments_and_require_exit_zero(monkeypatch, tmp_path):
    calls = []
    output = tmp_path/"out.json"
    def run(argv, **kwargs):
        calls.append((argv, kwargs)); output.write_text("{}")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(verification.subprocess, "run", run)
    verification._run_sidecar("context", Path("source with spaces.dem"), output)
    assert calls[0][0][-4:] == ["--input", "source with spaces.dem", "--out", str(output)]
    assert calls[0][1].get("shell") is None
    monkeypatch.setattr(verification.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="decoder failed"):
        verification._run_sidecar("context", Path("source.dem"), output)


def test_full_and_late_prefix_invocations_have_explicit_longer_timeout(monkeypatch, tmp_path):
    calls = []
    def run(*a, **kwargs): calls.append(kwargs); return SimpleNamespace(returncode=1)
    monkeypatch.setattr(commands.subprocess, "run", run)
    commands._extract(Path("source.dem"), "match", 0, tmp_path)
    commands._extract(Path("source.dem"), "match", 22330, tmp_path)
    assert [c["timeout"] for c in calls] == [3600, 3600]


def test_historical_absent_phase_producer_still_compares_every_event(source):
    value = deepcopy(source.phase); value.pop("producer")
    publish(source.phase_path, value)
    assert verify(source)["status"] == "original_competitive_source_reconstructed_exactly"
    value["events"][0]["is_warmup"] = True
    publish(source.phase_path, value)
    with pytest.raises(ValueError, match="eligibility differs"):
        verify(source)


def test_wrong_present_phase_producer_is_never_ignored(source):
    value = deepcopy(source.phase); value["producer"] = "unreviewed"
    publish(source.phase_path, value)
    with pytest.raises(ValueError, match="Unsupported reconstructed phase"):
        verify(source)
