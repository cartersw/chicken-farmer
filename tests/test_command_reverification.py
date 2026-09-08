"""Source reconstruction is independent of canonical metadata and trust flags."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import command_reverification as verification


def rows():
    return [{"command_row_id": i, "demo_tick": i+10, "command_number": i+500,
        "command_protobuf": bytes([10, i]), "view_yaw": float(i),
        "buttons_pb": None, "is_warmup": False} for i in range(3)]


def test_exact_rows_preserve_typed_null_and_raw_bytes():
    original = rows()
    result = verification.compare_command_rows(original, deepcopy(original))
    assert list(result) == [0, 1, 2]
    assert len(set(result.values())) == 3
    assert all(len(digest) == 64 for digest in result.values())


@pytest.mark.parametrize("field,value", [("view_yaw", 1), ("buttons_pb", 0),
    ("command_protobuf", b"different"), ("is_warmup", 0), ("demo_tick", 40),
    ("command_number", 999)])
def test_changed_scalar_type_or_source_value_rejects(field, value):
    original, fresh = rows(), rows()
    fresh[1][field] = value
    with pytest.raises(ValueError, match="differs.*row 1"):
        verification.compare_command_rows(original, fresh)


@pytest.mark.parametrize("kind", ["empty", "missing", "reordered", "repeated", "offset", "bool_id", "extra_column"])
def test_missing_or_ambiguous_source_rows_reject(kind):
    original, fresh = rows(), rows()
    if kind == "empty": original, fresh = [], []
    elif kind == "missing": fresh.pop()
    elif kind == "reordered": fresh.reverse()
    elif kind == "repeated": fresh[1] = deepcopy(fresh[0])
    elif kind == "offset": fresh[0]["command_row_id"] = 42
    elif kind == "bool_id": fresh[0]["command_row_id"] = False
    elif kind == "extra_column": fresh[1]["extra"] = None
    with pytest.raises(ValueError):
        verification.compare_command_rows(original, fresh)


@pytest.fixture
def prefix(tmp_path):
    original, fresh = tmp_path/"canonical", tmp_path/"fresh"
    original.mkdir(); fresh.mkdir()
    manifest = {"demo_id": "a"*64, "sha256": "a"*64, "match_id": "match-a", "tick_rate": 64,
        **verification.DECODER_FIELDS, "parse_status": "partial", "partial": True, "command_count": 3}
    for path in (original, fresh):
        pq.write_table(pa.Table.from_pylist(rows()), path/"usercmd.parquet")
    for name in ("player_state.parquet", "rounds.parquet", "events.parquet"):
        pq.write_table(pa.Table.from_pylist([{"demo_tick": 12}]), fresh/name)
    def publish():
        manifest["files"] = {name: verification.sha256_file(fresh/name) for name in
            ("usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet")}
        (fresh/"manifest.json").write_text(json.dumps(manifest))
    publish()
    return original, fresh, manifest, publish


def test_stop_tick_is_excluded_even_if_its_last_packet_differs(prefix):
    original, fresh, manifest, publish = prefix
    changed = rows(); changed[-1]["command_protobuf"] = b"incomplete last tick"
    pq.write_table(pa.Table.from_pylist(changed), fresh/"usercmd.parquet"); publish()
    result = verification._check_prefix(original, fresh, manifest, 12)
    assert result["compared_command_count"] == 2
    assert list(result["command_row_digests"]) == [0, 1]
    assert result["source_demo_tick_end_exclusive"] == 12
    assert result["fresh_partial"] is True


@pytest.mark.parametrize("change", ["source", "match", "tick_rate", "decoder", "failed", "short", "count", "hash", "changed_row"])
def test_partial_status_does_not_authorize_incomplete_or_changed_prefix(prefix, change):
    original, fresh, canonical, publish = prefix
    expected = deepcopy(canonical)
    if change == "source": canonical["demo_id"] = "b"*64
    elif change == "match": canonical["match_id"] = "other"
    elif change == "tick_rate": canonical["tick_rate"] = 128
    elif change == "decoder": canonical["parser_version"] = "unreviewed"
    elif change == "failed": canonical["parse_status"] = "failed"
    elif change == "short": pq.write_table(pa.Table.from_pylist([{"demo_tick": 11}]), fresh/"player_state.parquet")
    elif change == "count": canonical["command_count"] = 2
    elif change == "changed_row":
        changed = rows(); changed[1]["command_protobuf"] = b"wrong"
        pq.write_table(pa.Table.from_pylist(changed), fresh/"usercmd.parquet")
    publish()
    if change == "hash": (fresh/"events.parquet").write_bytes(b"modified")
    with pytest.raises(ValueError):
        verification._check_prefix(original, fresh, expected, 12)


@pytest.mark.parametrize("returncode", [0, 1])
def test_expected_diagnostic_exit_uses_fixed_argv_and_never_implies_row_acceptance(monkeypatch, tmp_path, returncode):
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(verification.subprocess, "run", run)
    result = verification._extract(Path("source with spaces.dem"), "match-id", 6400, tmp_path)
    assert result["exit_code"] == returncode and "status" not in result
    argv, options = calls[0]
    assert argv[-2:] == ["--max-demo-tick", "6400"]
    assert argv[3] == "source with spaces.dem"
    assert options.get("shell") is None and options["timeout"] == 300


def test_other_process_exit_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(verification.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2))
    with pytest.raises(ValueError, match="did not complete"):
        verification._extract(Path("source.dem"), "match", 12, tmp_path)


def test_owned_temporary_prefix_is_cleaned_even_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(verification, "PROJECT", tmp_path)
    sibling = tmp_path/"keep.txt"; sibling.write_text("untouched")
    with pytest.raises(RuntimeError):
        with verification._temporary_prefix() as temporary:
            (temporary/"nested").mkdir()
            (temporary/"nested"/"file").write_text("owned")
            raise RuntimeError("fixture failure")
    assert not temporary.exists() and sibling.read_text() == "untouched"


@pytest.mark.parametrize("bound", [0, -1, 2**31, 12.0, True])
def test_out_of_scope_prefix_never_starts_extractor(monkeypatch, tmp_path, bound):
    def forbidden():
        pytest.fail("Decoder should not be touched for an invalid bound")
    monkeypatch.setattr(verification, "_decoder_files", forbidden)
    with pytest.raises(ValueError, match="positive signed-int32"):
        verification.reverify_command_prefix(tmp_path/"source.dem", tmp_path, bound)


@pytest.fixture
def invocation(prefix, monkeypatch, tmp_path):
    original, fixture, manifest, publish = prefix
    source = tmp_path/"original.dem"; source.write_bytes(b"original fixture bytes")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.update(demo_id=source_hash, sha256=source_hash)
    publish()
    full = deepcopy(manifest); full.update(parse_status="complete", partial=False)
    (original/"manifest.json").write_text(json.dumps(full))
    implementation = tmp_path/"inspected-decoder.exe"; implementation.write_bytes(b"fixture decoder")
    pins = {str(implementation): verification.sha256_file(implementation)}
    monkeypatch.setattr(verification, "_decoder_files", lambda: dict(pins))
    monkeypatch.setattr(verification, "PROJECT", tmp_path)
    calls = []
    def extract(actual_source, match_id, through, output):
        calls.append((actual_source, match_id, through, output))
        shutil.copytree(fixture, output/source_hash)
        return {"exit_code": 1, "exit_code_meaning": "fixture expected partial quality rejection"}
    monkeypatch.setattr(verification, "_extract", extract)
    return source, original, calls, implementation, pins, extract


def test_public_reverification_repeats_fresh_decode_with_stable_proof(invocation):
    source, original, calls, *_ = invocation
    first = verification.reverify_command_prefix(source, original, 12)
    second = verification.reverify_command_prefix(source, original, 12)
    assert first == second and len(calls) == 2 and calls[0][3] != calls[1][3]
    assert all(not call[3].exists() for call in calls)
    assert first["provenance"]["ignored_command_columns"] == []
    assert first["command_sources"][0]["live_packet_envelope_association_required"] is True
    assert first["recorded_server_build_support_verified"] is False
    assert first["training_ready"] is False
    assert set(first["command_row_digests"]) == {0, 1}


def test_published_exit_one_without_source_tables_cannot_pass(invocation, monkeypatch):
    source, original, *_ = invocation
    monkeypatch.setattr(verification, "_extract", lambda *args: {"exit_code": 1})
    with pytest.raises(ValueError, match="did not publish"):
        verification.reverify_command_prefix(source, original, 12)


def test_source_bytes_changed_after_extraction_reject(invocation, monkeypatch):
    source, original, _, implementation, _, extract = invocation
    def changing(*args):
        result = extract(*args)
        implementation.write_bytes(b"changed decoder")
        return result
    monkeypatch.setattr(verification, "_extract", changing)
    with pytest.raises(ValueError, match="Source/decoder changed"):
        verification.reverify_command_prefix(source, original, 12)


def test_wrong_original_demo_never_starts_decode(invocation):
    source, original, calls, *_ = invocation
    source.write_bytes(b"another source")
    with pytest.raises(ValueError, match="Original demo bytes"):
        verification.reverify_command_prefix(source, original, 12)
    assert calls == []
