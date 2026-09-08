from copy import deepcopy
import json
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace
import zlib

import pytest

from cs2_data import hud_review as hud


def png(width=1280, height=1440):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind+data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + b"\0"*(width*3))*height)) + chunk(b"IEND", b""))


@pytest.fixture
def capture(tmp_path, monkeypatch):
    run = tmp_path/"capture"
    (run/"frames").mkdir(parents=True)
    frames = []
    for n in range(11):
        image = run/"frames"/f"{n}.tga"
        image.write_bytes(b"original pixels" + bytes([n]))
        frames.append({"capture_index": n, "archived_name": image.name, "sha256": hud.sha256_file(image)})
    inventory = run/"capture_frame_files.json"
    inventory.write_text(json.dumps({"frames": frames}))
    ledger = run/"capture_ledger.jsonl"
    ledger.write_text("original native capture")
    render = {"capture_frame_files": inventory.name, "capture_frame_files_sha256": hud.sha256_file(inventory),
        "capture_ledger": ledger.name, "capture_ledger_sha256": hud.sha256_file(ledger),
        "plugin_sha256": "a"*64, "clip_id": "capture", "hud_override": {"override_resource_sha256": "b"*64}}
    (run/"capture.render.json").write_text(json.dumps(render))
    def sheets(frames, out, ffmpeg):
        result = []
        for start in range(0, len(frames), 8):
            group = frames[start:start+8]
            path = out/f"frames-{start:06d}-{group[-1]['frame_index']:06d}.png"
            path.write_bytes(png())
            result.append({"path": str(path), "sha256": hud.sha256_file(path),
                "frame_indices": [f["frame_index"] for f in group]})
        return result, {"profile": hud.GENERATION_PROFILE, **hud.LAYOUT, "ffmpeg_processes": 1,
            "hard_links": len(frames), "copies": 0}
    monkeypatch.setattr(hud, "_make_sheets", sheets)
    executable = tmp_path/"ffmpeg.exe"; executable.write_bytes(b"fixture executable")
    monkeypatch.setattr(hud.shutil, "which", lambda name: str(executable))
    return run, tmp_path/"review"


def test_partial_final_sheet_covers_originals_without_approval(capture):
    run, out = capture
    bundle = hud.prepare_hud_review(run, out)
    assert [s["frame_indices"] for s in bundle["contact_sheets"]] == [list(range(8)), [8, 9, 10]]
    assert not bundle["visual_acceptance_verified"] and not bundle["all_frames_reviewed"]
    assert bundle["status"] == "pending_visual_review"
    assert hud.validate_hud_review_bundle(out) == bundle


@pytest.mark.parametrize("change", ["image", "ledger", "sheet", "approval", "coverage", "render"])
def test_resume_rechecks_sources_sheets_and_cannot_self_approve(capture, change):
    run, out = capture
    bundle = hud.prepare_hud_review(run, out)
    if change == "image": (run/"frames/0.tga").write_bytes(b"changed")
    elif change == "ledger": (run/"capture_ledger.jsonl").write_text("changed")
    elif change == "render": (run/"capture.render.json").write_text(json.dumps({**json.loads((run/"capture.render.json").read_text()), "clip_id": "different"}))
    elif change == "sheet": __import__("pathlib").Path(bundle["contact_sheets"][0]["path"]).write_bytes(b"changed")
    else:
        if change == "approval": bundle["visual_acceptance_verified"] = True
        else: bundle["contact_sheets"][-1]["frame_indices"].pop()
        (out/"hud_review_bundle.json").write_text(json.dumps(bundle))
    with pytest.raises(ValueError): hud.validate_hud_review_bundle(out)


@pytest.mark.parametrize("name", ["../outside.tga", "0.tga"])
def test_inventory_rehash_does_not_allow_escape_or_duplicate(capture, name):
    run, out = capture
    inventory = run/"capture_frame_files.json"
    contents = json.loads(inventory.read_text())
    contents["frames"][1]["archived_name"] = name
    inventory.write_text(json.dumps(contents))
    path = run/"capture.render.json"
    render = json.loads(path.read_text())
    render["capture_frame_files_sha256"] = hud.sha256_file(inventory)
    path.write_text(json.dumps(render))
    with pytest.raises(ValueError, match="escapes archive or repeats"):
        hud.prepare_hud_review(run, out)
    assert not out.exists()


def test_existing_review_material_is_not_overwritten(capture):
    run, out = capture
    hud.prepare_hud_review(run, out)
    before = (out/"hud_review_bundle.json").read_bytes()
    with pytest.raises(FileExistsError): hud.prepare_hud_review(run, out)
    assert (out/"hud_review_bundle.json").read_bytes() == before


def test_original_change_while_generating_sheets_prevents_publication(capture, monkeypatch):
    run, out = capture
    original = hud._make_sheets
    def edit(group, path, ffmpeg):
        result = original(group, path, ffmpeg)
        (run/"frames/0.tga").write_bytes(b"changed during review prep")
        return result
    monkeypatch.setattr(hud, "_make_sheets", edit)
    with pytest.raises(ValueError, match="sources changed"):
        hud.prepare_hud_review(run, out)
    assert not (out/"hud_review_bundle.json").exists()


def test_index_contains_every_original_and_sheet_without_claiming_approval(capture):
    run, out = capture
    bundle = hud.prepare_hud_review(run, out)
    page = (out/"index.html").read_text(encoding="utf-8")
    assert "all 11 original frames" in page and "0–10" in page and "2 sheets" in page
    assert "5 black grid slots are padding" in page
    assert "Pending manual review" in page and "<script" not in page
    for frame in bundle["frames"]:
        assert page.count(Path(frame["path"]).as_uri()) == 1
    for sheet in bundle["contact_sheets"]:
        assert page.count('src="' + Path(sheet["path"]).name + '"') == 1
    assert page.count('href="#sheet-1"') == 2
    assert hud.validate_hud_review_bundle(out) == bundle


@pytest.mark.parametrize("change", ["index_bytes", "rehashed_index", "index_escape", "sheet_escape", "sheet_swap",
    "dimensions", "layout", "producer", "tool", "missing_index", "removed_metadata", "empty_sheet", "bool_index", "repeated_sheet"])
def test_index_generation_and_paths_reject_mutations(capture, change):
    run, out = capture
    bundle = hud.prepare_hud_review(run, out)
    index = out/"index.html"
    if change in ("index_bytes", "rehashed_index"):
        index.write_text("<script>alert('untrusted')</script>", encoding="utf-8")
        if change == "rehashed_index": bundle["review_index"]["sha256"] = hud.sha256_file(index)
    elif change == "index_escape":
        elsewhere = out.parent/"elsewhere.html"; shutil.copyfile(index, elsewhere)
        bundle["review_index"]["path"] = str(elsewhere)
    elif change == "sheet_escape":
        elsewhere = out.parent/"elsewhere.png"; shutil.copyfile(bundle["contact_sheets"][0]["path"], elsewhere)
        bundle["contact_sheets"][0]["path"] = str(elsewhere)
    elif change == "sheet_swap": bundle["contact_sheets"].reverse()
    elif change == "dimensions":
        path = Path(bundle["contact_sheets"][0]["path"]); path.write_bytes(png(32, 32))
        bundle["contact_sheets"][0]["sha256"] = hud.sha256_file(path)
    elif change == "layout": bundle["generation"]["columns"] = 1
    elif change == "producer": bundle["generation"]["profile"] = "arbitrary"
    elif change == "tool": Path(bundle["generation"]["ffmpeg_path"]).write_bytes(b"different executable")
    elif change == "missing_index": bundle.pop("review_index")
    elif change == "removed_metadata": bundle.pop("generation"); bundle.pop("review_index")
    elif change == "empty_sheet": bundle["contact_sheets"].append({**bundle["contact_sheets"][0], "frame_indices": []})
    elif change == "bool_index": bundle["contact_sheets"][0]["frame_indices"][0] = False
    elif change == "repeated_sheet": bundle["contact_sheets"][1]["path"] = bundle["contact_sheets"][0]["path"]
    (out/"hud_review_bundle.json").write_text(json.dumps(bundle))
    with pytest.raises(ValueError): hud.validate_hud_review_bundle(out)


def test_historical_v1_bundle_without_optional_index_still_validates(capture):
    run, out = capture
    bundle = hud.prepare_hud_review(run, out)
    bundle.pop("generation"); bundle.pop("review_index")
    (out/"index.html").unlink()
    (out/"hud_review_bundle.json").write_text(json.dumps(bundle))
    assert hud.validate_hud_review_bundle(out) == bundle


def test_html_escapes_clip_identity_and_percent_encodes_source_paths(capture):
    run, out = capture
    manifest = run/"capture.render.json"
    value = json.loads(manifest.read_text()); value["clip_id"] = '<img src=x onerror="evil">&'
    manifest.write_text(json.dumps(value))
    inventory = run/"capture_frame_files.json"
    records = json.loads(inventory.read_text())
    source = run/"frames/0.tga"; renamed = source.with_name("ampersand & apostrophe ' hash #.tga")
    source.rename(renamed); records["frames"][0]["archived_name"] = renamed.name
    inventory.write_text(json.dumps(records))
    value["capture_frame_files_sha256"] = hud.sha256_file(inventory); manifest.write_text(json.dumps(value))
    hud.prepare_hud_review(run, out)
    page = (out/"index.html").read_text(encoding="utf-8")
    assert '<img src=x onerror="evil">' not in page
    assert '&lt;img src=x onerror=&quot;evil&quot;&gt;&amp;' in page
    assert '%26' in page and '%27' in page and '%23' in page and '%20' in page
    hud.validate_hud_review_bundle(out)


def test_staging_has_numeric_order_and_falls_back_to_copies_without_altering_originals(tmp_path, monkeypatch):
    out = tmp_path/"review"; out.mkdir()
    original = tmp_path/"source ' name.tga"; original.write_bytes(b"original")
    frames = [{"path": str(original)}] * 3
    def no_links(*a): raise OSError("cross-device fixture")
    monkeypatch.setattr(hud.os, "link", no_links)
    with hud._sequence_files(frames, out) as (directory, counts):
        assert counts == {"hard_links": 0, "copies": 3}
        assert sorted(p.name for p in directory.iterdir()) == [f"frame-{i:08d}.tga" for i in range(3)]
        assert all(p.read_bytes() == b"original" for p in directory.iterdir())
    assert not directory.exists() and original.read_bytes() == b"original" and list(out.iterdir()) == []


def test_failed_sequence_does_not_publish_or_leave_staging_files(tmp_path, monkeypatch):
    out = tmp_path/"review"; out.mkdir()
    source = tmp_path/"original.tga"; source.write_bytes(b"original")
    monkeypatch.setattr(hud.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="fixture failure"))
    with pytest.raises(ValueError, match="sequence generation failed"):
        hud._make_sheets([{"path": str(source), "frame_index": 0}], out, "ffmpeg")
    assert list(out.iterdir()) == [] and source.read_bytes() == b"original"


def test_executable_change_during_generation_prevents_bundle_publication(capture, monkeypatch):
    run, out = capture
    original = hud._make_sheets
    def changed(frames, directory, executable):
        result = original(frames, directory, executable)
        Path(executable).write_bytes(b"changed during generation")
        return result
    monkeypatch.setattr(hud, "_make_sheets", changed)
    with pytest.raises(ValueError, match="FFmpeg changed during generation"):
        hud.prepare_hud_review(run, out)
    assert not (out/"hud_review_bundle.json").exists()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_success_exit_with_incomplete_or_extra_sheet_output_is_rejected(tmp_path, monkeypatch, count):
    out = tmp_path/"review"; out.mkdir()
    path = tmp_path/"original.tga"; path.write_bytes(b"original")
    frames = [{"path": str(path), "frame_index": index} for index in range(11)]
    def run(*a, **k):
        for index in range(count): (out/f".sheet-{index:06d}.png").write_bytes(png())
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(hud.subprocess, "run", run)
    with pytest.raises(ValueError, match="exactly every contact sheet"):
        hud._make_sheets(frames, out, "ffmpeg")
    assert not list(out.glob(".hud-sequence-*"))


def test_one_invocation_has_fixed_sequence_mapping_and_bounded_complete_output(tmp_path, monkeypatch):
    out = tmp_path/"review"; out.mkdir()
    frames = []
    for index in range(11):
        path = tmp_path/f"source {index}.tga"; path.write_bytes(bytes([index]))
        frames.append({"path": str(path), "frame_index": index})
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        for number in range(2): (out/f".sheet-{number:06d}.png").write_bytes(png())
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(hud.subprocess, "run", run)
    sheets, generation = hud._make_sheets(frames, out, "ffmpeg.exe")
    assert len(calls) == 1 and generation["ffmpeg_processes"] == 1
    argv, options = calls[0]
    assert argv.count("-i") == 1 and argv[argv.index("-frames:v")+1] == "2"
    assert "frame-%08d.tga" in argv[argv.index("-i")+1] and "-n" in argv
    assert "tile=layout=2x4:nb_frames=8" in argv[argv.index("-vf")+1]
    assert options.get("shell") is None and options["timeout"] == 120
    assert [s["frame_indices"] for s in sheets] == [list(range(8)), [8, 9, 10]]
    assert not list(out.glob(".hud-sequence-*"))


def test_real_sequence_preserves_row_major_order_and_partial_padding(tmp_path):
    project = Path(__file__).resolve().parents[1]
    executables = list((project/".tools/ffmpeg").glob("*/bin/ffmpeg.exe"))
    executable = Path(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else executables[0] if executables else None
    if executable is None: pytest.skip("Real FFmpeg is unavailable")
    frames, colors = [], [(20+i*15, 220-i*12, 35+i*8) for i in range(11)]
    for index, (r, g, b) in enumerate(colors):
        path = tmp_path/f"original-{index}.tga"
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 4, 4, 24, 0x20)
        path.write_bytes(header + bytes([b, g, r])*16)
        frames.append({"path": str(path), "frame_index": index})
    # Literal printf-like syntax in an owned directory must not become another
    # sequence counter or redirect files outside the expected directory.
    out = tmp_path/"review %05d & ' literal"; out.mkdir()
    sheets, _ = hud._make_sheets(frames, out, executable)
    for number, sheet in enumerate(sheets):
        result = hud.subprocess.run([str(executable), "-v", "error", "-i", sheet["path"], "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, timeout=120, creationflags=getattr(hud.subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0 and len(result.stdout) == 1280*1440*3
        for tile in range(8):
            x, y = (tile%2)*640+320, (tile//2)*360+180
            index = number*8+tile
            actual = tuple(result.stdout[(y*1280+x)*3:(y*1280+x)*3+3])
            assert actual == (colors[index] if index < len(colors) else (0, 0, 0))
