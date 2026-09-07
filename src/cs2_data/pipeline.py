"""Build one inspectable, explicitly diagnostic dataset from a native capture."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .align import align
from .calibration import calibrate
from .io import exclusive_output, parsed_manifest, read_json, sha256_file, write_json
from .timing import prepare_timing
from .viewer import viewer


@exclusive_output()
def process_render(parsed: Path, render_dir: Path, out: Path,
                   normalized: Path | None = None) -> dict[str, Any]:
    if any(path.name != ".cs2-data.lock" for path in out.iterdir()):
        raise ValueError("process-render requires a fresh empty output directory")
    manifests = list(render_dir.glob("*.render.json"))
    if len(manifests) != 1:
        raise ValueError("Expected exactly one native render manifest")
    path = manifests[0]
    render = read_json(path)
    if render.get("render_status") != "video_ready_timing_unverified":
        raise ValueError("Native rendering and video validation must have completed")
    source = parsed_manifest(parsed)
    if source["demo_id"] != render["demo_id"]:
        raise ValueError("Native capture and canonical inputs belong to different demos")
    ledger = render_dir / render.get("capture_ledger", "capture_ledger.jsonl")
    if not ledger.is_file() or sha256_file(ledger) != render.get("capture_ledger_sha256"):
        raise ValueError("A hash-bound native capture ledger is required; rerender with the instrumented plugin")
    report = {"schema_version": 1, "status": "running", "training_ready": False,
              "demo_id": render["demo_id"], "clip_id": render["clip_id"],
              "source_render_manifest": str(path.resolve()), "source_render_sha256": sha256_file(path),
              "parsed_directory": str(parsed.resolve()), "stages": {}}
    try:
        report["stages"]["timing"] = prepare_timing(
            clip_path=path, ledger=ledger, pts_path=render_dir / (render["clip_id"] + ".pts.json"),
            frames_dir=render_dir / "frames", out=out / "timing")
        # Fit a slightly wider *recorded* interval to cover actual first/last movie
        # observations. The calibrator rejects gaps/resets and does not extrapolate.
        calibrate(parsed=parsed, out=out / "calibration", round_id=render["round_id"],
                  steam_id=int(render["steam_id"]), player_slot=render["player_slot"],
                  start_demo_tick=max(0, render["requested_start_demo_tick"] - 8),
                  end_demo_tick=render["requested_end_demo_tick"] + 8)
        report["stages"]["alignment"] = align(
            parsed=parsed, timing=out / "timing/frames.jsonl", clip_path=out / "timing/clip.json",
            out=out / "aligned", normalized=normalized, calibration=out / "calibration",
            diagnostic=True)
        report["stages"]["viewer"] = viewer(aligned=out / "aligned", out=out / "viewer/inspect.html")
        report["status"] = "complete"
    except (ValueError, OSError, KeyError, TypeError) as error:
        report.update(status="failed", error=str(error))
        write_json(out / "pipeline_manifest.json", report)
        raise
    write_json(out / "pipeline_manifest.json", report)
    return report
