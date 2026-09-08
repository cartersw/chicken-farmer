"""Independent proof composition must not turn staging or clock matches into trust."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from cs2_data import competitive_replay_proof as proof
from cs2_data.control_label_audit import canonical_row_sha256


@pytest.fixture
def frame_inputs():
    frames = [{"frame_index": i, "source_demo_tick_start": 6000+2*i, "source_demo_tick_end": 6002+2*i,
        "render_time_seconds_start": 100.+i/32, "render_time_seconds_end": 100.+(i+1)/32} for i in range(3)]
    inventory = [{"capture_index": i, "path": str(Path(f"frame-{i}.tga").resolve()), "sha256": str(i)*64} for i in range(3)]
    records = [{"event": "movie_frame", "capture_index": i, "native_clock": {"client_address": 999, "client_generation": 3},
        "native_clock_after": {"client_address": 999, "client_generation": 3},
        "demo_start_tick": -5544, "thread_id": 100, "native_observation": {"demo_paused": False,
            "observed_pov": {"steam_id": "123", "controller_handle": 50, "pawn_handle": 200}}} for i in range(3)]
    sync = {"pixel_correspondence": {"verified": True}, "native_message_clock_audit": {"frames": [
        {"event": "movie_frame", "frame_index": i, "status": "matched_message_clocks"} for i in range(3)]},
        "pov_evidence": [{"frame_index": i, "status": "passed"} for i in range(3)]}
    bounds = {"frames": [{"capture_index": i, "status": "verified", "verified": True,
        "upper_server_tick": 16703+2*i, "upper_source_demo_tick": 6000+2*i} for i in range(3)]}
    hud = {"status": "verified", "verified_frame_indices": [0, 1, 2]}
    return frames, inventory, records, sync, bounds, {"run_id": "owned-run"}, hud


def test_only_independently_agreeing_frame_proofs_produce_valid_images(frame_inputs):
    frames = proof._frame_proofs(*frame_inputs)
    assert all(frame["verified"] for frame in frames)
    assert len({f["clock_segment_id"] for f in frames}) == 1
    assert [f["observation_upper_execution_tick"] for f in frames] == [16703, 16705, 16707]
    assert [f["upper_source_demo_tick"] for f in frames] == [6000, 6002, 6004]


@pytest.mark.parametrize("epoch", [1051.774658203125, 20000.0, 1000000.0])
def test_cadence_is_measured_independently_of_absolute_native_clock_epoch(frame_inputs, epoch):
    for i, frame in enumerate(frame_inputs[0]):
        frame["render_time_seconds_start"] = epoch+i/32
        frame["render_time_seconds_end"] = epoch+(i+1)/32
    assert all(frame["verified"] for frame in proof._frame_proofs(*frame_inputs))


@pytest.mark.parametrize("start,end", [(float("nan"), 1051.8), (1051.8, float("inf")),
    (1051.8, 1051.8), (1051.8, 1051.84), (-1., -.96875)])
def test_absolute_clock_extension_keeps_finite_forward_32hz_cadence_required(frame_inputs, start, end):
    frame_inputs[0][1].update(render_time_seconds_start=start, render_time_seconds_end=end)
    result = proof._frame_proofs(*frame_inputs)[1]
    assert not result["verified"]
    assert "observed_32hz_render_cadence_unverified" in result["reason_codes"]


@pytest.mark.parametrize("change,reason", [("pixel", "pixel_correspondence_unverified"),
    ("native", "native_message_clock_association_unverified"), ("pov", "native_first_person_pov_unverified"),
    ("packet", "packet_information_bound_unknown"), ("numeric_unknown", "packet_information_bound_unknown"),
    ("pause", "native_demo_pause_unknown_or_active"), ("missing_pawn", "native_clock_or_pawn_segment_unavailable"),
    ("during_reset", "native_clock_segment_changed_during_submission"),
    ("cadence", "observed_32hz_render_cadence_unverified"), ("hud", "competitive_hud_visual_review_unavailable")])
def test_missing_companion_proof_is_not_replaced_by_other_matches(frame_inputs, change, reason):
    frames, _, records, sync, bounds, _, hud = frame_inputs
    if change == "pixel": sync["pixel_correspondence"]["verified"] = False
    elif change == "native": sync["native_message_clock_audit"]["frames"][1]["status"] = "unknown"
    elif change == "pov": sync["pov_evidence"][1]["status"] = "unknown"
    elif change == "packet": bounds["frames"][1]["status"] = "unknown"
    elif change == "numeric_unknown": bounds["frames"][1]["upper_server_tick"] = None
    elif change == "pause": records[1]["native_observation"]["demo_paused"] = True
    elif change == "missing_pawn": del records[1]["native_observation"]["observed_pov"]["pawn_handle"]
    elif change == "during_reset": records[1]["native_clock_after"]["client_generation"] += 1
    elif change == "cadence": frames[1]["render_time_seconds_end"] += .01
    elif change == "hud": hud["verified_frame_indices"].remove(1)
    result = proof._frame_proofs(*frame_inputs)
    assert not result[1]["verified"] and reason in result[1]["reason_codes"]


@pytest.mark.parametrize("field", ["pawn_handle", "controller_handle", "client_generation", "demo_start_tick"])
def test_native_reset_changes_history_segment_even_when_individual_frames_pass(frame_inputs, field):
    records = frame_inputs[2]
    if field in ("pawn_handle", "controller_handle"):
        records[1]["native_observation"]["observed_pov"][field] += 1
    elif field == "client_generation":
        records[1]["native_clock"][field] += 1; records[1]["native_clock_after"][field] += 1
    else: records[1][field] += 1
    result = proof._frame_proofs(*frame_inputs)
    assert all(frame["verified"] for frame in result)
    assert result[0]["clock_segment_id"] == result[2]["clock_segment_id"] != result[1]["clock_segment_id"]


@pytest.mark.parametrize("collection", ["movies", "native", "pov", "packets"])
def test_duplicate_or_reordered_audit_keys_do_not_silently_overwrite(frame_inputs, collection):
    if collection == "movies": rows = frame_inputs[2]
    elif collection == "native": rows = frame_inputs[3]["native_message_clock_audit"]["frames"]
    elif collection == "pov": rows = frame_inputs[3]["pov_evidence"]
    else: rows = frame_inputs[4]["frames"]
    rows.append(deepcopy(rows[1]))
    with pytest.raises(ValueError, match="Duplicate or invalid"):
        proof._frame_proofs(*frame_inputs)


@pytest.fixture
def command_inputs():
    row = {"command_row_id": 5, "command_number": 100, "server_tick_executed": 16703,
        "player_slot": 9, "client_tick": 20000, "demo_tick": 6000, "command_protobuf": b"original bytes", "alive": True}
    envelope = {"server_tick_executed": 16703, "player_slot": 9, "command_number": 100, "client_tick": 20000,
        "wire_index": 0, "envelope_index": 0, "protobuf_sha256": "b"*64}
    packet = {"demo_command_kind": 7, "demo_tick": 6000, "source_command_index": 10, "command_offset": 50,
        "packet_data_sha256": "c"*64, "command_envelopes": [envelope]}
    reconstruction = {"proof_sha256": "d"*64, "command_row_digests": {5: canonical_row_sha256(row)}}
    return [row], {"packets": [packet]}, reconstruction


def test_original_row_reconstruction_and_unique_live_envelope_are_both_required(command_inputs):
    result = proof._command_proofs(*command_inputs)[5]
    assert result["status"] == "verified" and result["source_envelope"]["demo_command_kind"] == 7


def test_composed_command_proof_reuses_an_already_derived_envelope_index(command_inputs, monkeypatch):
    expected = proof._command_proofs(*command_inputs)
    envelopes = proof._source_envelopes(command_inputs[1])
    def unexpected(*args):
        pytest.fail("The composed proof must build the envelope index only once")
    monkeypatch.setattr(proof, "_source_envelopes", unexpected)
    assert proof._command_proofs(*command_inputs, source_envelopes=envelopes) == expected


@pytest.mark.parametrize("change", ["raw", "state", "missing_baseline", "checkpoint", "ambiguous", "different_clock"])
def test_exact_clock_match_does_not_replace_original_command_payload_origin(command_inputs, change):
    rows, source, reconstruction = command_inputs
    if change == "raw": rows[0]["command_protobuf"] = b"another payload"
    elif change == "state": rows[0]["alive"] = False
    elif change == "missing_baseline": reconstruction["command_row_digests"] = {}
    elif change == "checkpoint": source["packets"][0]["demo_command_kind"] = 13
    elif change == "ambiguous": source["packets"].append(deepcopy(source["packets"][0]))
    elif change == "different_clock": source["packets"][0]["command_envelopes"][0]["client_tick"] += 1
    assert proof._command_proofs(*command_inputs)[5]["status"] == "unknown"


def test_changed_sources_cannot_receive_newer_snapshot_hashes(tmp_path):
    path = tmp_path/"evidence"; path.write_bytes(b"original")
    sources = proof._Sources(); sources.watch(path)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed during"):
        sources.watch(path)
    with pytest.raises(ValueError, match="changed during"):
        sources.finish()


def test_visual_acceptance_boolean_and_correct_staged_css_do_not_approve_unreviewed_images(tmp_path):
    render = {"capture_ledger_sha256": "a"*64, "hud_profile": {"visual_acceptance_verified": True},
        "hud_override": {"resource_load_verified": True, "rendered_hud_preservation_verified": True}}
    result = proof._hud_review(render, [], proof._Sources().watch)
    assert result["status"] == "unknown" and result["verified_frame_indices"] == []


@pytest.fixture
def hud_review(tmp_path, monkeypatch):
    render = {"capture_ledger_sha256": "a"*64, "capture_frame_files_sha256": "b"*64,
        "hud_override": {"override_resource_sha256": "c"*64}}
    inventory = [{"sha256": "d"*64}]
    review = {"profile": "cs2-competitive-pilot-hud-review-v1", "capture_ledger_sha256": "a"*64,
        "capture_frame_files_sha256": "b"*64, "hud_override_sha256": "c"*64, "plugin_sha256": proof.PLUGIN_SHA256,
        "frames": [{"frame_index": 0, "sha256": "d"*64}], "all_frames_reviewed": True,
        "spectator_only_information_absent": True, "player_visible_hud_preserved": True}
    path = tmp_path/"review.json"
    path.write_text(json.dumps(review))
    monkeypatch.setattr(proof, "PROJECT", tmp_path)
    monkeypatch.setattr(proof, "HUD_REVIEWS", {"a"*64: (Path("review.json"), proof.sha256_file(path))})
    return render, inventory, review, path


def test_fixed_review_is_bound_to_every_original_image_and_native_run(hud_review):
    render, inventory, _, _ = hud_review
    result = proof._hud_review(render, inventory, proof._Sources().watch)
    assert result["status"] == "verified" and result["verified_frame_indices"] == [0]


@pytest.mark.parametrize("change", ["image", "css", "archive", "review_bytes"])
def test_another_image_or_resource_cannot_reuse_a_fixed_visual_review(hud_review, change):
    render, inventory, review, path = hud_review
    if change == "image": inventory[0]["sha256"] = "e"*64
    elif change == "css": render["hud_override"]["override_resource_sha256"] = "f"*64
    elif change == "archive": render["capture_frame_files_sha256"] = "e"*64
    else: path.write_text(json.dumps({**review, "extra": True}))
    with pytest.raises(ValueError):
        proof._hud_review(render, inventory, proof._Sources().watch)


@pytest.fixture
def render_contract(tmp_path, monkeypatch):
    build, run = tmp_path/"build", tmp_path/"run"
    sandbox = run/"renderer-sandbox"
    plugin, css = sandbox/"bin/win64/server.dll", sandbox/"panorama/test.vcss_c"
    plugin.parent.mkdir(parents=True); css.parent.mkdir(parents=True)
    plugin.write_bytes(b"fixed native plugin"); css.write_bytes(b"staged css")
    (sandbox/"pakchicken_hud_dir.vpk").write_bytes(b"private archive directory")
    (sandbox/"pakchicken_hud_000.vpk").write_bytes(css.read_bytes())
    profile = {}
    for index, name in enumerate(proof.get_native_replay_profile(proof.CURRENT_PROFILE)["binary_profile"]):
        path = build/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(f"binary {index}".encode())
        profile[name] = proof.sha256_file(path)
    comparison = tmp_path/"comparison.json"
    comparison.write_text(json.dumps({"modules": {"fixture": {"path": str(build/name), "sha256": profile[name]}}}))
    monkeypatch.setattr(proof, "RENDER_BINARIES", build)
    monkeypatch.setattr(proof, "PLUGIN_SHA256", proof.sha256_file(plugin))
    monkeypatch.setattr(proof, "PROFILE_COMPARISON", comparison)
    monkeypatch.setattr(proof, "PROFILE_COMPARISON_SHA256", proof.sha256_file(comparison))
    monkeypatch.setattr(proof, "get_native_replay_profile", lambda name: {"binary_profile": profile})
    monkeypatch.setattr(proof, "header_matches_profile", lambda *a, **k: True)
    monkeypatch.setattr(proof, "_protected_archive", lambda *a: None)
    render = {"renderer_profile": proof.RENDERER_PROFILE, "fps": 32, "capture_method": "native-windows-cs2-startmovie-tga",
        "binary_profile": dict(profile), "plugin_sha256": proof.PLUGIN_SHA256, "plugin_source_sha256": proof.PLUGIN_SHA256,
        "plugin_staged_sha256": proof.PLUGIN_SHA256, "archived_game_mod_dir": str(sandbox), "cs2_exit_code": 0,
        "settings_restored": True, "gameinfo_restored": True, "staged_plugin_removed_from_game": True,
        "launch_arguments": ["-insecure", "-chicken-competitive-replay"], "hud_profile": {"raw_frames_masked": False},
        "hud_override": {"staged_relative_path": "panorama/test.vcss_c", "override_resource_sha256": proof.sha256_file(css),
            "delivery": "identical_loose_and_chunked_vpk_resource", "private_archive_relative_path": "pakchicken_hud_dir.vpk",
            "private_archive_sha256": proof.sha256_file(sandbox/"pakchicken_hud_dir.vpk"),
            "private_chunk_relative_path": "pakchicken_hud_000.vpk", "private_chunk_sha256": proof.sha256_file(css),
            "installed_resources_modified": False, "raw_frames_masked": False}}
    return render, run/"render.json", [{"event": "header"}], build, plugin, css


def test_archived_binary_profile_and_plugin_are_read_independently(render_contract):
    render, path, records, *_ = render_contract
    sources = proof._Sources()
    result = proof._render_contract(render, path, records, sources.watch)
    assert result["native_profile"] == proof.CURRENT_PROFILE
    assert len(sources.files) == 13  # eight Valve binaries, plugin, comparison, CSS and private archive/chunk
    assert result["hud_resource_load_verified_by_staging"] is False


@pytest.mark.parametrize("change", ["binary_bytes", "binary_claim", "plugin_bytes", "plugin_claim", "native_profile", "fps",
    "not_restored", "wrong_exit", "secure_launch", "css_bytes", "css_escape", "masked_raw"])
def test_changed_or_unscoped_render_evidence_cannot_receive_current_profile(render_contract, monkeypatch, change):
    render, path, records, build, plugin, css = render_contract
    if change == "binary_bytes": (build/next(iter(render["binary_profile"]))).write_bytes(b"changed")
    elif change == "binary_claim": render["binary_profile"][next(iter(render["binary_profile"]))] = "a"*64
    elif change == "plugin_bytes": plugin.write_bytes(b"changed")
    elif change == "plugin_claim": render["plugin_sha256"] = "a"*64
    elif change == "native_profile": monkeypatch.setattr(proof, "header_matches_profile", lambda *a, **k: False)
    elif change == "fps": render["fps"] = 64
    elif change == "not_restored": render["settings_restored"] = False
    elif change == "wrong_exit": render["cs2_exit_code"] = 1
    elif change == "secure_launch": render["launch_arguments"].remove("-insecure")
    elif change == "css_bytes": css.write_bytes(b"changed")
    elif change == "css_escape": render["hud_override"]["staged_relative_path"] = "../../outside"
    elif change == "masked_raw": render["hud_profile"]["raw_frames_masked"] = True
    with pytest.raises(ValueError):
        proof._render_contract(render, path, records, proof._Sources().watch)


@pytest.fixture
def eligibility_source(tmp_path, monkeypatch):
    parsed = tmp_path/"parsed"; parsed.mkdir()
    context, phase = tmp_path/"context.json", tmp_path/"phase.json"
    contents = {
        parsed/"player_state.parquet": {"alive": True, "is_freeze_time": False},
        parsed/"rounds.parquet": {"freeze_end_tick": 4906, "end_tick": 10459},
        context: {"is_paused": False}, phase: {"phase": "competitive"},
    }
    for path, value in contents.items(): path.write_text(json.dumps(value))
    canonical = {"demo_id": "a"*64, "source_path": str(tmp_path/"original.dem"), "match_id": "reviewed-match", "files": {
        path.name: proof.sha256_file(path) for path in contents if path.parent == parsed}}
    (parsed/"manifest.json").write_text(json.dumps(canonical))
    files = {str(path): proof.sha256_file(path) for path in (*contents, parsed/"manifest.json")}
    calls = []
    def reconstruct(source, actual_parsed, actual_phase, actual_context, *, command_rows):
        calls.append((source, actual_parsed, actual_phase, actual_context, command_rows))
        return {"source_files": files, "profile": "independent-fixture", "proof_sha256": "b"*64}
    monkeypatch.setattr(proof, "reverify_competitive_source", reconstruct)
    return parsed, canonical, phase, context, calls


def test_eligibility_calls_full_source_reconstruction_and_watches_all_inputs(eligibility_source):
    parsed, canonical, phase, context, calls = eligibility_source
    sources = proof._Sources()
    rows = [{"command_row_id": 1}]
    result = proof._source_eligibility(parsed, canonical, phase, context, rows, sources.watch)
    assert result["profile"] == "independent-fixture"
    assert calls == [(Path(canonical["source_path"]), parsed, phase, context, rows)]
    assert len(sources.files) == 5


@pytest.mark.parametrize("changed", ["player_state.parquet", "rounds.parquet", "context", "phase"])
def test_source_changes_after_independent_reconstruction_fail_composition(eligibility_source, changed):
    parsed, canonical, phase, context, _ = eligibility_source
    path = context if changed == "context" else phase if changed == "phase" else parsed/changed
    path.write_text(json.dumps({"edited_eligibility": True, "training_ready": True}))
    if path.parent == parsed: canonical["files"][path.name] = proof.sha256_file(path)
    (parsed/"manifest.json").write_text(json.dumps(canonical))
    with pytest.raises(ValueError):
        proof._source_eligibility(parsed, canonical, phase, context, [], proof._Sources().watch)


def test_missing_phase_never_reaches_source_reconstruction(eligibility_source):
    parsed, canonical, _, context, calls = eligibility_source
    with pytest.raises(ValueError, match="phase evidence is required"):
        proof._source_eligibility(parsed, canonical, None, context, [], proof._Sources().watch)
    assert not calls


@pytest.fixture
def mounted_gameinfo():
    original = b'GameInfo\n{\n  SearchPaths\n  {\n    Game csgo\n  }\n}\n'
    run_id = "a"*32
    directory = b"csgo/chicken-render-"+run_id.encode()
    insertion = b"  \tGame\t"+directory+b"\n  \tGame\t"+directory+b"/pakchicken_hud.vpk\n  \tMod\t"+directory+b"/pakchicken_hud.vpk\n"
    patched = original.replace(b"    Game csgo", insertion+b"    Game csgo")
    journal = {"mod_name": "chicken-render-"+run_id, "hud_archive_profile": proof.HUD_ARCHIVE_SEARCH_PROFILE,
        "original_sha256": hashlib.sha256(original).hexdigest(), "patched_sha256": hashlib.sha256(patched).hexdigest()}
    return original, journal, run_id


def test_private_hud_mount_is_reconstructed_from_original_bytes(mounted_gameinfo):
    result = proof._gameinfo_mount_proof(*mounted_gameinfo)
    assert result["archive_search_paths"] == ["Game", "Mod"]
    assert result["profile"] == "competitive-hud-archive-search-v2"
    assert result["archive_relative_path"] == "pakchicken_hud.vpk"
    assert result["mounting_alone_does_not_prove_resource_use"] is True


@pytest.mark.parametrize("change", ["profile", "directory", "patch", "original", "run"])
def test_edited_mount_claim_does_not_prove_protected_resource_search_order(mounted_gameinfo, change):
    original, journal, run_id = mounted_gameinfo
    if change == "profile": journal["hud_archive_profile"] = "another-profile"
    elif change == "directory": journal["mod_name"] = "chicken-render-"+"b"*32
    elif change == "patch": journal["patched_sha256"] = "b"*64
    elif change == "original": original += b"changed"
    elif change == "run": run_id = "b"*32
    with pytest.raises(ValueError):
        proof._gameinfo_mount_proof(original, journal, run_id)
