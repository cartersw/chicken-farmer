"""Prepare source-bound contact sheets for manual competitive HUD review.

This creates review material, never an approval. Acceptance separately requires
an explicitly registered review of every original image in the capture.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from urllib.parse import quote

from .io import read_json, sha256_file

PROFILE = "cs2-competitive-hud-review-bundle-v1"
GENERATION_PROFILE = "cs2-hud-contact-sequence-v1"
INDEX_PROFILE = "cs2-hud-static-review-index-v1"
LAYOUT = {"frames_per_sheet": 8, "columns": 2, "rows": 4,
          "tile_width": 640, "tile_height": 360, "final_sheet_padding": "black"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _capture_sources(render_dir):
    run = Path(render_dir).resolve()
    manifests = list(run.glob("*.render.json"))
    _require(len(manifests) == 1, "HUD review requires one render manifest")
    render_path = manifests[0]
    render = read_json(render_path)
    source_files = {str(render_path): sha256_file(render_path)}
    inventory_path = Path(render["capture_frame_files"])
    if not inventory_path.is_absolute():
        inventory_path = run/inventory_path
    ledger_path = Path(render["capture_ledger"])
    if not ledger_path.is_absolute():
        ledger_path = run/ledger_path
    for path, expected in ((inventory_path, render["capture_frame_files_sha256"]),
                           (ledger_path, render["capture_ledger_sha256"])):
        path = path.resolve()
        _require(path.is_relative_to(run) and sha256_file(path) == expected,
                 "HUD review inventory/ledger escapes its run or changed")
        source_files[str(path)] = expected
    inventory = read_json(inventory_path)["frames"]
    _require(isinstance(inventory, list) and 0 < len(inventory) <= 10000,
             "HUD review requires a bounded original frame inventory")
    frames = []
    seen = set()
    for n, frame in enumerate(inventory):
        _require(type(frame.get("capture_index")) is int and frame["capture_index"] == n,
                 "HUD review inventory indices are not consecutive")
        path = (run/"frames"/frame["archived_name"]).resolve()
        _require(path.is_relative_to(run/"frames") and path not in seen,
                 "HUD review frame path escapes archive or repeats")
        digest = sha256_file(path)
        _require(digest == frame["sha256"], "HUD review original image changed")
        seen.add(path)
        source_files[str(path)] = digest
        frames.append({"frame_index": n, "path": str(path), "sha256": digest})
    return render, frames, source_files


def _unchanged(source_files):
    _require(all(sha256_file(Path(path)) == digest for path, digest in source_files.items()),
             "HUD review sources changed")


def _make_sheet(frames, out, ffmpeg):
    """Historical one-process-per-sheet renderer, retained for comparison only."""
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
    for frame in frames:
        command += ["-i", frame["path"]]
    filters = [f"[{n}:v]scale=640:360:flags=area[s{n}]" for n in range(len(frames))]
    inputs = "".join(f"[s{n}]" for n in range(len(frames)))
    if len(frames) == 1:
        filters.append("[s0]null[out]")
    else:
        layout = "|".join(f"{(n%2)*640}_{(n//2)*360}" for n in range(len(frames)))
        filters.append(f"{inputs}xstack=inputs={len(frames)}:layout={layout}:fill=black[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(out)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _require(result.returncode == 0 and out.is_file(), "HUD contact-sheet generation failed: " + result.stderr[-2000:])


@contextmanager
def _sequence_files(frames, out):
    """Own a bounded sequence of links, with copies only across filesystems.

    Numeric aliases prevent input globs or FFmpeg concat-file path parsing from
    changing source order. Existing capture names never become filter syntax.
    """
    root = out.resolve()
    temporary = Path(tempfile.mkdtemp(prefix=".hud-sequence-", dir=root)).resolve()
    _require(temporary.parent == root, "HUD sequence escapes its new output directory")
    counts = {"hard_links": 0, "copies": 0}
    try:
        for index, frame in enumerate(frames):
            path = Path(frame["path"])
            _require(path.suffix.lower() == ".tga", "HUD sequence requires original TGA frames")
            destination = temporary/f"frame-{index:08d}.tga"
            try:
                os.link(path, destination)
                counts["hard_links"] += 1
            except OSError:
                # Copy is limited to this one explicitly verified original.
                _require(not destination.exists(), "HUD sequence alias already exists")
                shutil.copyfile(path, destination)
                counts["copies"] += 1
        yield temporary, counts
    finally:
        # Recursive removal applies only to our freshly created staging folder.
        _require(temporary.resolve() == temporary and temporary.parent == root and root == out.resolve(),
                 "HUD sequence path changed before cleanup")
        shutil.rmtree(temporary)


def _make_sheets(frames, out, ffmpeg):
    """Generate all 2x4 sheets in one bounded FFmpeg image-sequence pass."""
    with _sequence_files(frames, out) as (sequence, aliases):
        # image2 expands printf tokens in the entire path, including directory
        # names. Escape literal percent signs before appending our sole counter.
        pattern = str(out).replace("%", "%%") + os.sep + ".sheet-%06d.png"
        sequence_pattern = str(sequence).replace("%", "%%") + os.sep + "frame-%08d.tga"
        filters = "scale=640:360:flags=area,tile=layout=2x4:nb_frames=8:padding=0:margin=0:color=black"
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-threads", "1", "-framerate", "32", "-start_number", "0", "-i", sequence_pattern,
            "-filter_threads", "1", "-vf", filters, "-frames:v", str((len(frames)+7)//8),
            "-fps_mode", "passthrough", "-start_number", "0", str(pattern)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(120, len(frames)//5),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _require(result.returncode == 0, "HUD sequence generation failed: " + result.stderr[-2000:])
    generated = sorted(out.glob(".sheet-*.png"))
    expected = [out/f".sheet-{index:06d}.png" for index in range((len(frames)+7)//8)]
    _require(generated == expected, "HUD sequence did not produce exactly every contact sheet")
    sheets = []
    for number, start in enumerate(range(0, len(frames), 8)):
        group = frames[start:start+8]
        path = out/f"frames-{start:06d}-{group[-1]['frame_index']:06d}.png"
        _require(not path.exists(), "HUD contact sheet destination already exists")
        generated[number].rename(path)
        _require(_png_dimensions(path) == (1280, 1440), "HUD sheet has an unexpected grid size")
        sheets.append({"path": str(path), "sha256": sha256_file(path),
                       "frame_indices": [frame["frame_index"] for frame in group]})
    return sheets, {"profile": GENERATION_PROFILE, **LAYOUT, "ffmpeg_processes": 1, **aliases}


def _png_dimensions(path):
    with Path(path).open("rb") as handle:
        header = handle.read(24)
    _require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and
             header[8:16] == b"\x00\x00\x00\rIHDR", "HUD contact sheet is not a PNG image")
    return struct.unpack(">II", header[16:24])


def _index_bytes(bundle):
    """Deterministic, escaped, script-free local navigation for every source."""
    sheets, frames = bundle["contact_sheets"], bundle["frames"]
    escape = html.escape
    title = "HUD review — " + str(bundle["clip_id"])
    links = []
    sections = []
    for number, sheet in enumerate(sheets):
        indices = sheet["frame_indices"]
        label = f"Frames {indices[0]}–{indices[-1]}"
        links.append(f'<a href="#sheet-{number}">{label}</a>')
        previous = f'<a href="#sheet-{number-1}">Previous sheet</a>' if number else '<span>First sheet</span>'
        following = f'<a href="#sheet-{number+1}">Next sheet</a>' if number+1 < len(sheets) else '<span>Last sheet</span>'
        originals = []
        for index in indices:
            frame = frames[index]
            # Absolute file URIs permit a review directory on another drive;
            # original paths were already confined to the capture frame archive.
            uri = escape(Path(frame["path"]).as_uri(), quote=True)
            originals.append(f'<li><a href="{uri}">Frame {index}: original full-resolution TGA</a></li>')
        padding = f'<p>{8-len(indices)} black grid slots are padding, not additional frames.</p>' if len(indices) < 8 else ''
        image = escape(quote(Path(sheet["path"]).name, safe=""), quote=True)
        sections.append(f'<section id="sheet-{number}" aria-labelledby="title-{number}">\n'
            f'<h2 id="title-{number}">{label}</h2><nav>{previous} · {following} · <a href="#coverage">All sheets</a></nav>\n'
            f'<img src="{image}" alt="{label}, two columns in row-major order" width="1280" height="1440" loading="lazy">\n'
            f'{padding}<ol class="originals">{"".join(originals)}</ol>\n</section>')
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src file: 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:1320px;margin:24px auto;padding:0 16px;background:#13171c;color:#eef2f6}}a{{color:#8dcfff}}nav a{{margin-right:12px}}section{{margin:36px 0;padding-top:8px;border-top:1px solid #45505d}}img{{display:block;max-width:100%;height:auto;margin-top:16px}}.sheets{{display:flex;flex-wrap:wrap;gap:8px 18px}}.originals{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding-left:24px}}.pending{{padding:12px;border:1px solid #e2b364;background:#302819}}@media(max-width:600px){{.originals{{display:block}}}}</style>
</head><body><h1>{escape(title)}</h1>
<p class="pending">Pending manual review. This page does not approve any frame or mark it reviewed.</p>
<p>Coverage: all {len(frames)} original frames, numbered 0–{len(frames)-1}, appear exactly once across {len(sheets)} sheets.</p>
<p>Inspect every tile from left to right, then top to bottom. Confirm the spectator identity/weapon strip is absent and the ordinary player HUD is preserved. Record unclear frames or visual failures separately.</p>
<p>Original links open the unmodified full-resolution TGA files. Browsers without TGA support may download them for a local image viewer.</p>
<nav id="coverage" class="sheets" aria-label="All contact sheets">{' '.join(links)}</nav>
{''.join(sections)}
</body></html>
'''
    return document.encode("utf-8")


def prepare_hud_review(render_dir: Path, out: Path, *, ffmpeg: Path | None = None):
    """Publish an immutable, unreviewed bundle after checking every original hash."""
    render, frames, sources = _capture_sources(render_dir)
    executable = ffmpeg or shutil.which("ffmpeg")
    _require(executable is not None, "FFmpeg is required to prepare HUD contact sheets")
    executable = Path(executable).resolve()
    _require(executable.is_file(), "HUD review FFmpeg executable does not exist")
    executable_sha256 = sha256_file(executable)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    sheets, generation = _make_sheets(frames, out, executable)
    bundle = {"schema_version": 1, "profile": PROFILE, "status": "pending_visual_review",
        "visual_acceptance_verified": False, "all_frames_reviewed": False,
        "capture_ledger_sha256": render["capture_ledger_sha256"],
        "capture_frame_files_sha256": render["capture_frame_files_sha256"],
        "hud_override_sha256": render["hud_override"]["override_resource_sha256"],
        "plugin_sha256": render["plugin_sha256"], "clip_id": render["clip_id"],
        "render_dir": str(Path(render_dir).resolve()), "frames": frames,
        "source_files": sources, "contact_sheets": sheets,
        "generation": {**generation, "ffmpeg_path": str(executable), "ffmpeg_sha256": executable_sha256},
        "review_instructions": ["Inspect every image in row-major order; frame indices are in each sheet entry.",
            "Confirm spectator-only identity/weapon strips are absent and the player's ordinary HUD is preserved.",
            "Inspect original resolution where a sheet is unclear; record failures without modifying the images.",
            "Publish and register a source-bound review only after the actual visual review."]}
    index = out/"index.html"
    with index.open("xb") as handle:
        handle.write(_index_bytes(bundle))
    bundle["review_index"] = {"profile": INDEX_PROFILE, "path": str(index), "sha256": sha256_file(index)}
    _unchanged(sources)
    _require(sha256_file(executable) == executable_sha256, "HUD review FFmpeg changed during generation")
    with (out/"hud_review_bundle.json").open("x", encoding="utf-8") as handle:
        json.dump(bundle, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return bundle


def validate_hud_review_bundle(out: Path):
    """Recheck sources and full sheet coverage; this still does not approve HUD."""
    out = Path(out).resolve()
    bundle = read_json(out/"hud_review_bundle.json")
    _require(bundle.get("profile") == PROFILE and bundle.get("status") == "pending_visual_review" and
             bundle.get("visual_acceptance_verified") is False and bundle.get("all_frames_reviewed") is False,
             "A review bundle cannot claim visual approval")
    render, frames, sources = _capture_sources(bundle["render_dir"])
    _require(bundle.get("frames") == frames and bundle.get("source_files") == sources and
             all(bundle.get(key) == render.get(key) for key in
                 ("capture_ledger_sha256", "capture_frame_files_sha256", "plugin_sha256", "clip_id")) and
             bundle.get("hud_override_sha256") == render["hud_override"]["override_resource_sha256"],
             "HUD review bundle disagrees with the original capture")
    indices = []
    sheets = bundle.get("contact_sheets")
    _require(isinstance(sheets, list) and 0 < len(sheets) <= len(frames), "HUD sheets are missing or unbounded")
    seen_sheets = set()
    for sheet in sheets:
        _require(isinstance(sheet, dict) and isinstance(sheet.get("frame_indices"), list) and
                 0 < len(sheet["frame_indices"]) <= 8, "HUD contact sheet has an invalid frame group")
        path = Path(sheet["path"]).resolve()
        _require(path.parent == out and path not in seen_sheets and sha256_file(path) == sheet["sha256"],
                 "HUD contact sheet changed, repeats or escapes bundle")
        seen_sheets.add(path)
        indices.extend(sheet["frame_indices"])
    _require(all(type(index) is int for index in indices), "HUD sheet frame indices must be integers")
    _require(indices == list(range(len(frames))), "HUD sheets do not cover every original exactly once")
    if "generation" in bundle or "review_index" in bundle or (out/"index.html").exists():
        generation = bundle.get("generation", {})
        _require(generation.get("profile") == GENERATION_PROFILE and
                 all(type(generation.get(key)) is type(value) and generation.get(key) == value for key, value in LAYOUT.items()) and
                 type(generation.get("ffmpeg_processes")) is int and generation["ffmpeg_processes"] == 1,
                 "Unsupported HUD sequence generation profile")
        _require(all(type(generation.get(key)) is int and generation[key] >= 0 for key in ("hard_links", "copies")) and
                 generation["hard_links"] + generation["copies"] == len(frames), "HUD sequence source coverage differs")
        executable = Path(generation["ffmpeg_path"])
        _require(executable.is_absolute() and sha256_file(executable) == generation["ffmpeg_sha256"], "HUD review FFmpeg changed")
        for number, sheet in enumerate(sheets):
            expected = list(range(number*8, min(number*8+8, len(frames))))
            _require(sheet["frame_indices"] == expected and
                     Path(sheet["path"]).name == f"frames-{expected[0]:06d}-{expected[-1]:06d}.png" and
                     _png_dimensions(sheet["path"]) == (1280, 1440), "HUD sheet layout or frame assignment differs")
        index = bundle.get("review_index", {})
        path = Path(index.get("path", "")).resolve()
        _require(index.get("profile") == INDEX_PROFILE and path == out/"index.html" and
                 path.is_file() and sha256_file(path) == index.get("sha256"), "HUD review index changed or escapes bundle")
        _require(path.read_bytes() == _index_bytes(bundle), "HUD review index does not match its complete source coverage")
    _unchanged(sources)
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args(argv)
    result = prepare_hud_review(args.render_dir, args.out, ffmpeg=args.ffmpeg)
    print(json.dumps({"status": result["status"], "frames": len(result["frames"]),
                      "bundle": str(args.out/"hud_review_bundle.json")}))


if __name__ == "__main__":
    main()
