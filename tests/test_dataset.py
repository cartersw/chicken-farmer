from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data.align import align, assign_commands, validate_timing
from cs2_data.jobs import alive_windows, command_coverage, render_jobs
from cs2_data.normalize import effective_scalar, normalize, transition, wrap_angle
from cs2_data.viewer import viewer


def command(row_id=10, tick=100, number=1, **changes):
    return dict(demo_id="demo-hash", command_row_id=row_id, round_id=1, steam_id=76561198000000001,
                player_slot=2, demo_tick=tick, command_number=number, client_tick=tick + 400,
                pawn_entity_handle=91, view_yaw=179.0, view_pitch=2.0, mousedx_raw=10, mousedy_raw=2,
                **changes)


def changed(base, **changes):
    return {**base, **changes}


def clip():
    return dict(schema_version=1, clip_id="abc123", demo_id="demo-hash", round_id=1, steam_id="76561198000000001",
                player_slot=2, timing_status="measured", timing_clock="demo_tick", pov_verified=True,
                num_frames=3, video_uri="clip.mp4", video_sha256="test-hash", width=1280, height=720)


def frames():
    identity = {key: clip()[key] for key in ("clip_id", "demo_id", "round_id", "steam_id", "player_slot")}
    return [{**identity, "frame_index": i, "pts_seconds": i * 0.03,
             "source_demo_tick_start": 100 + i * 2, "source_demo_tick_end": 102 + i * 2}
            for i in range(3)]


def write_parsed(tmp_path, commands):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    table = pa.Table.from_pylist(commands)
    # Steam identifiers are unsigned in the canonical extractor schema.
    index = table.schema.get_field_index("steam_id")
    table = table.set_column(index, "steam_id", table["steam_id"].cast(pa.uint64()))
    pq.write_table(table, parsed / "usercmd.parquet")
    refresh_manifest(parsed)
    return parsed


def refresh_manifest(parsed, **changes):
    manifest = {"demo_id": "demo-hash", "parse_status": "complete", "partial": False,
                "parser_schema_version": "2", "files": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in parsed.glob("*.parquet")}}
    manifest.update(changes)
    (parsed / "manifest.json").write_text(json.dumps(manifest))


def test_yaw_wrap_and_current_command_attachment():
    assert wrap_angle(-358) == 2
    assert wrap_angle(358) == -2
    assert wrap_angle(180) == -180
    previous = command()
    current = changed(command(99, 101, 2), view_yaw=-179.0, view_pitch=5.0)
    assert transition(previous, current) == (2.0, 3.0, None)


@pytest.mark.parametrize("changes,reason", [
    ({"round_id": 2}, "changed_round_id"),
    ({"steam_id": 76561198000000002}, "changed_steam_id"),
    ({"pawn_entity_handle": 92}, "changed_pawn_entity_handle"),
    ({"command_number": 9}, "command_number_discontinuity"),
    ({"demo_tick": 130}, "demo_tick_discontinuity"),
    ({"demo_tick": 99}, "demo_tick_discontinuity"),
    ({"client_tick": 20}, "client_tick_reset"),
    ({"view_yaw": None}, "missing_viewangles"),
    ({"view_yaw": float("nan")}, "missing_viewangles"),
    ({"alive": False}, "not_alive"),
])
def test_discontinuities_are_masked(changes, reason):
    assert transition(command(), changed(command(99, 101, 2), **changes)) == (None, None, reason)


def test_normalize_preserves_noncontiguous_raw_ids_and_raw_file(tmp_path):
    raw = [command(), changed(command(99, 101, 2), view_yaw=-179.0), command(150, 102, 3)]
    parsed = write_parsed(tmp_path, raw)
    before = (parsed / "usercmd.parquet").read_bytes()
    out = tmp_path / "normalized"
    report = normalize(parsed, out)
    derived = pq.read_table(out / "normalized_actions.parquet").to_pylist()
    assert report["command_count"] == 3
    assert derived[0]["aim_valid"] is False
    assert derived[1]["command_row_id"] == 99
    assert derived[1]["previous_command_row_id"] == 10
    assert derived[1]["delta_yaw_deg"] == 2
    assert (parsed / "usercmd.parquet").read_bytes() == before
    with pytest.raises(ValueError, match="overwrite"):
        normalize(parsed, out)


def test_scalar_defaults_require_known_parent_presence(tmp_path):
    assert effective_scalar({"mousedx_raw": None}, "mousedx_raw", "base_present") is None
    assert effective_scalar({"mousedx_raw": None, "base_present": False}, "mousedx_raw", "base_present") is None
    assert effective_scalar({"mousedx_raw": None, "base_present": True}, "mousedx_raw", "base_present") == 0
    prior = changed(command(), view_yaw=1, view_pitch=1, viewangles_present=True)
    current = changed(command(99, 101, 2), view_yaw=None, view_pitch=None, viewangles_present=True)
    assert transition(prior, current) == (-1, -1, None)
    raw = [changed(command(), base_present=True, mousedx_raw=None, mousedy_raw=None)]
    parsed = write_parsed(tmp_path, raw)
    normalize(parsed, tmp_path / "norm")
    row = pq.read_table(tmp_path / "norm" / "normalized_actions.parquet").to_pylist()[0]
    assert row["mousedx_raw"] is None
    assert row["mousedx_effective"] == 0


def test_half_open_intervals_multiple_commands_and_empty_frame():
    recorded = frames()
    validate_timing(recorded, clip())
    cmds = [command(5, 100, 1), command(44, 100, 2), command(81, 104, 3), command(300, 105, 4)]
    groups, assignment = assign_commands(recorded, cmds)
    assert groups == [[5, 44], [], [81, 300]]
    assert assignment == [0, 0, 2, 2]
    groups, assignment = assign_commands(recorded, [command(10, 100, 1), command(55, 102, 2), command(99, 104, 3)])
    assert groups == [[10], [55], [99]]
    with pytest.raises(ValueError, match="outside"):
        assign_commands(recorded, [command(10, 100, 1), command(55, 102, 2), command(99, 106, 3)])


@pytest.mark.parametrize("mutate,match", [
    (lambda c, f: c.update(timing_status="unverified"), "measured"),
    (lambda c, f: c.update(timing_clock="server_tick_executed"), "measured"),
    (lambda c, f: c.update(pov_verified=False), "pov_verified"),
    (lambda c, f: c.update(num_frames=4), "count"),
    (lambda c, f: f[1].update(source_demo_tick_start=103), "contiguous"),
    (lambda c, f: f[1].update(source_demo_tick_start=101), "contiguous"),
    (lambda c, f: f[1].update(frame_index=3), "contiguous"),
    (lambda c, f: f[1].update(pts_seconds=0), "strictly"),
    (lambda c, f: f[1].update(steam_id="123"), "identity"),
    (lambda c, f: f[1].update(source_demo_tick_end=None), "finite"),
])
def test_bad_capture_timing_rejected(mutate, match):
    c, f = clip(), frames()
    mutate(c, f)
    with pytest.raises(ValueError, match=match):
        validate_timing(f, c)


def test_command_gaps_and_duplicates_rejected():
    with pytest.raises(ValueError, match="gap"):
        assign_commands(frames(), [command(10, 100), command(20, 105)])
    with pytest.raises(ValueError, match="Duplicate"):
        assign_commands(frames(), [command(10, 100), command(10, 101)])


def test_real_parquet_alignment_join_and_viewer(tmp_path, monkeypatch):
    parsed = write_parsed(tmp_path, [command(10, 100, 1), command(30, 102, 2), command(90, 104, 3), command(120, 106, 4)])
    norm = tmp_path / "norm"
    normalize(parsed, norm)
    clip_path, timing = tmp_path / "clip.json", tmp_path / "frames.jsonl"
    clip_path.write_text(json.dumps(clip()))
    timing.write_text("\n".join(json.dumps(frame) for frame in frames()))
    monkeypatch.setattr("cs2_data.align.verify_video", lambda *args: tmp_path / "clip.mp4")
    out = tmp_path / "aligned"
    report = align(parsed, timing, clip_path, out, normalized=norm)
    aligned_frames = pq.read_table(out / "frame_alignment.parquet").to_pylist()
    assert [f["command_row_ids"] for f in aligned_frames] == [[10], [30], [90]]
    assert [(f["clip_command_start"], f["clip_command_end"]) for f in aligned_frames] == [(0, 1), (1, 2), (2, 3)]
    aligned_cmds = pq.read_table(out / "aligned_commands.parquet").to_pylist()
    assert len(aligned_cmds) == 3  # Right clip boundary command 120 is retained raw, excluded from this clip.
    assert aligned_cmds[1]["delta_yaw_deg"] == 0
    assert report["training_ready"] is False
    viewer(out, tmp_path / "viewer.html")
    assert "requestVideoFrameCallback" in (tmp_path / "viewer.html").read_text()


def state(tick, **changes):
    return dict(demo_id="demo-hash", round_id=1, player_slot=2, steam_id=76561198000000001,
                demo_tick=tick, alive=True, spectator_user_id=7, **changes)


def test_alive_jobs_split_on_death_pause_and_state_gap():
    rounds = {1: {"freeze_end_tick": 100, "end_tick": 112, "map": "de_mirage"}}
    rows = [state(99), state(100), state(101), changed(state(102), alive=False), state(103),
            changed(state(104), is_paused=True), state(105), state(108), state(109), state(112)]
    windows = alive_windows(rows, rounds, "demo-hash")
    assert [(row["start_demo_tick"], row["end_demo_tick"]) for row in windows] == [(100, 102), (103, 104), (105, 106), (108, 110)]


def test_render_candidate_command_coverage_requires_correct_identity_and_all_boundaries():
    windows = [{"player_slot": 2, "round_id": 1, "steam_id": "76561198000000001", "start_demo_tick": 100, "end_demo_tick": 110},
               {"player_slot": 3, "round_id": 1, "steam_id": "76561198000000002", "start_demo_tick": 100, "end_demo_tick": 110}]
    rows = [command(10, 100), command(20, 102), command(30, 108),
            changed(command(40, 109), player_slot=3)]  # Correct slot but wrong Steam ID must not count.
    command_coverage(windows, rows, "demo-hash")
    assert windows[0]["command_coverage"]["max_gap_demo_ticks"] == 6
    assert windows[0]["command_coverage"]["complete_within_gap_limit"] is False
    assert windows[1]["command_coverage"]["command_count"] == 0
    assert windows[1]["command_coverage"]["complete_within_gap_limit"] is False
    command_coverage(windows, [command(10, tick) for tick in range(100, 110)], "demo-hash")
    assert windows[0]["command_coverage"]["complete_within_gap_limit"] is True
    assert windows[0]["command_coverage"]["observed_tick_fraction"] == 1


def test_jobs_resolve_renderer_identity_and_stable_clip_id(tmp_path):
    parsed = write_parsed(tmp_path, [command()])
    pq.write_table(pa.Table.from_pylist([state(100), state(101)]), parsed / "player_state.parquet")
    pq.write_table(pa.Table.from_pylist([{"demo_id": "demo-hash", "round_id": 1, "freeze_end_tick": 100, "end_tick": 103}]), parsed / "rounds.parquet")
    demo = tmp_path / "example.dem"
    demo.write_bytes(b"synthetic fixture")
    refresh_manifest(parsed, sha256=hashlib.sha256(demo.read_bytes()).hexdigest())
    out = tmp_path / "jobs.jsonl"
    report = render_jobs(parsed, out, demo=demo, min_ticks=1, allow_unverified_phase=True)
    job = json.loads(out.read_text())
    assert report["job_count"] == 1
    assert job["spectator_user_id"] == 7
    assert job["steam_id"] == "76561198000000001"
    assert job["end_demo_tick"] == 102
    assert job["clip_id"].isalnum()
    assert job["phase_evidence"]["phase_verified"] is False


@pytest.mark.parametrize("changes", [{"parse_status": "failed"}, {"partial": True}, {"parser_schema_version": "99"}])
def test_incomplete_and_unsupported_sources_rejected(tmp_path, changes):
    parsed = write_parsed(tmp_path, [command()])
    refresh_manifest(parsed, **changes)
    with pytest.raises(ValueError):
        normalize(parsed, tmp_path / "out")
    assert not (tmp_path / "out" / "normalized_actions.parquet").exists()


def test_altered_source_and_failed_stage_cannot_publish(tmp_path):
    parsed = write_parsed(tmp_path, [command()])
    pq.write_table(pa.Table.from_pylist([command(99)]), parsed / "usercmd.parquet")
    with pytest.raises(ValueError, match="hash"):
        normalize(parsed, tmp_path / "out")
    refresh_manifest(parsed)
    raw = [command(99), command(10, 101, 2)]  # Invalid canonical row order fails inside the streaming writer.
    pq.write_table(pa.Table.from_pylist(raw), parsed / "usercmd.parquet")
    refresh_manifest(parsed)
    with pytest.raises(ValueError, match="command_row_id"):
        normalize(parsed, tmp_path / "failed")
    assert not (tmp_path / "failed" / "normalized_actions.parquet").exists()
    assert not (tmp_path / "failed" / "normalization_report.json").exists()
    assert not (tmp_path / "failed" / ".cs2-data.lock").exists()


def test_output_lock_prevents_competing_writer(tmp_path):
    parsed = write_parsed(tmp_path, [command()])
    out = tmp_path / "locked"
    out.mkdir()
    (out / ".cs2-data.lock").write_text("other worker")
    with pytest.raises(ValueError, match="Another stage"):
        normalize(parsed, out)
    assert (out / ".cs2-data.lock").read_text() == "other worker"
