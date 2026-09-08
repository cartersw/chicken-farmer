"""Refresh a local progress viewer without accepting or modifying batch evidence.

Small journal-bound metadata is hashed for display. Original image/video bytes,
timing, HUD correctness and training acceptance are deliberately not revalidated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
from urllib.parse import quote

from . import competitive_batch as batch
from .hud_policy import policy_allows_capture, trusted_hud_policy
from .io import read_json, sha256_file

PROFILE = "cs2-round-progression-report-v1"
COLLECTION_PROFILE = "cs2-round-progression-collection-v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _owned(root, value):
    _require(isinstance(value, (str, Path)) and str(value), "Missing report artifact path")
    root = Path(root).resolve()
    path = Path(value)
    path = path if path.is_absolute() else root/path
    _require(not path.is_symlink(), "Report artifact may not be a symlink")
    resolved = path.resolve()
    _require(resolved != root and resolved.is_relative_to(root), "Report artifact path escapes its owned directory")
    _require(not any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)),
             "Report artifact may not use a symlink directory")
    return resolved


def _hash_matches(path, expected):
    _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) and
             path.is_file() and sha256_file(path) == expected, "Report metadata hash mismatch: "+str(path))


def _journal_metadata(attempt, path):
    _hash_matches(path, attempt.get("files", {}).get(str(path)))
    return read_json(path)


def _latest(stages, name):
    attempts = stages[name]
    return attempts[-1] if attempts else None


def _url(root, path):
    return quote(path.relative_to(root).as_posix(), safe="/")


def _display_render(root, attempt, item):
    """Check metadata binding and media presence, not media bytes or acceptance."""
    out = Path(attempt["out"])
    manifests = list(out.glob("*.render.json"))
    _require(len(manifests) == 1, "Completed render needs exactly one render manifest")
    path = _owned(out, manifests[0])
    render = _journal_metadata(attempt, path)
    job = item["job"]
    _require(render.get("render_status") == "video_ready_timing_unverified" and render.get("source_job") == job,
             "Render manifest does not bind the planned job")
    _require(all(str(render.get(key)) == str(job[key]) for key in
                 ("demo_id", "round_id", "steam_id", "player_slot", "fps", "width", "height")) and
             render.get("requested_start_demo_tick") == job["start_demo_tick"] and
             render.get("requested_end_demo_tick") == job["end_demo_tick"], "Render identity or interval changed")
    _require(attempt.get("result", {}).get("status") == "verified_render_artifacts" and
             Path(attempt["result"].get("render_manifest", "")).resolve() == path,
             "Completed journal does not identify this render manifest")
    count = render.get("num_frames")
    _require(type(count) is int and 0 < count <= 10000 and attempt["result"].get("frame_count") == count,
             "Invalid or inconsistent captured frame count")
    inventory_path = _owned(out, render.get("capture_frame_files"))
    inventory = _journal_metadata(attempt, inventory_path)
    _hash_matches(inventory_path, render.get("capture_frame_files_sha256"))
    frames = inventory.get("frames")
    _require(isinstance(frames, list) and len(frames) == count, "Render inventory count differs from captured frames")
    seen = set()
    for index, frame in enumerate(frames):
        frame_path = _owned(out/"frames", frame.get("archived_name"))
        _require(frame.get("capture_index") == index and frame_path not in seen and
                 frame_path.is_file() and frame_path.stat().st_size > 0 and
                 attempt.get("files", {}).get(str(frame_path)) == frame.get("sha256") and
                 isinstance(frame.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", frame["sha256"]),
                 "Render inventory is missing, repeated, or not journal-bound")
        seen.add(frame_path)
    video = _owned(out, render.get("video_uri"))
    _require(video.suffix.lower() == ".mp4" and video.is_file() and video.stat().st_size > 0 and
             isinstance(render.get("video_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", render["video_sha256"]) and
             attempt.get("files", {}).get(str(video)) == render["video_sha256"],
             "Render video is missing or not journal-bound")
    restoration = {name: render.get(name) for name in
                   ("settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")}
    for name in ("settings", "gameinfo"):
        journal = out/(name+"-recovery.json")
        restoration[name+"_journal_state"] = (_journal_metadata(attempt, journal).get("state")
                                               if journal.is_file() else None)
    return {"render_manifest": str(path), "render_dir": str(out), "capture_clip_id": render.get("clip_id"),
            "video": str(video), "video_url": _url(root, video), "video_filename": video.name,
            "captured_frames": count, "captured_seconds": count/job["fps"],
            "hud_policy_current": trusted_hud_policy(render),
            "restoration_claims_recorded": restoration, "display_metadata_verified": True,
            "media_bytes_rehashed": False}


def _legacy_hud_bundle(attempt, capture):
    out = Path(attempt["out"])
    bundle_path = _owned(out, attempt.get("result", {}).get("bundle", out/"hud_review_bundle.json"))
    bundle = _journal_metadata(attempt, bundle_path)
    _require(bundle.get("profile") == "cs2-competitive-hud-review-bundle-v1" and
             Path(bundle.get("render_dir", "")).resolve() == Path(capture["render_dir"]) and
             bundle.get("clip_id") == capture["capture_clip_id"], "HUD review belongs to another capture")
    return out, bundle


def _review_link(root, attempt, capture):
    out, bundle = _legacy_hud_bundle(attempt, capture)
    entry = bundle.get("review_index", {})
    index = _owned(out, entry.get("path"))
    _require(index == out/"index.html" and entry.get("profile") == "cs2-hud-static-review-index-v1",
             "Unexpected HUD review index")
    _hash_matches(index, entry.get("sha256"))
    _hash_matches(index, attempt.get("files", {}).get(str(index)))
    return {"review_index": str(index), "review_url": _url(root, index)}


def _recorded_hud_policy(root, attempt, capture):
    out = Path(attempt["out"])
    path = _owned(out, out/"hud_policy.json")
    if not path.is_file() and attempt.get("result", {}).get("bundle"):
        # Historical bundles remain optional; the current policy is independent
        # of their old visual-review status and generated contact sheets.
        _legacy_hud_bundle(attempt, capture)
        return {}
    receipt = _journal_metadata(attempt, path)
    render = read_json(Path(capture["render_manifest"]))
    _require(receipt.get("schema_version") == 1 and receipt.get("profile") == "cs2-batch-hud-setup-policy-v1" and
             receipt.get("clip_id") == capture["capture_clip_id"] and receipt.get("training_ready") is False and
             Path(receipt.get("render_manifest", "")).resolve() == Path(capture["render_manifest"]) and
             receipt.get("render_manifest_sha256") == sha256_file(Path(capture["render_manifest"])) and
             receipt.get("capture_ledger_sha256") == render.get("capture_ledger_sha256") and
             receipt.get("capture_frame_files_sha256") == render.get("capture_frame_files_sha256") and
             receipt.get("policy") == capture["hud_policy_current"], "Recorded setup policy belongs to another capture or policy")
    return {"hud_policy_receipt_recorded": str(path), "hud_policy_receipt_url": _url(root, path)}


def _recorded_synchronization(root, attempt, capture):
    """Display a bound saved audit; do not recompute timing or acceptance."""
    out = Path(attempt["out"])
    path = _owned(out, out/"synchronization_audit.json")
    audit = _journal_metadata(attempt, path)
    _require(audit.get("producer") == "cs2-synchronization-audit-v1" and audit.get("status") == "complete" and
             audit.get("clip_id") == capture["capture_clip_id"] and
             audit.get("num_frames") == capture["captured_frames"] and
             audit.get("native_profile") == batch.CURRENT_PROFILE,
             "Recorded synchronization audit does not identify this capture")
    inputs = audit.get("inputs", {}).get("capture_evidence", {})
    _require(Path(inputs.get("render_manifest_path", "")).resolve() == Path(capture["render_manifest"]) and
             inputs.get("render_manifest_sha256") == sha256_file(Path(capture["render_manifest"])),
             "Recorded synchronization audit belongs to another render manifest")
    native = audit.get("native_message_clock_audit", {})
    status, observations = native.get("status"), native.get("frames")
    _require(status in ("matched_message_clocks", "unavailable") and isinstance(observations, list) and
             len(observations) <= capture["captured_frames"]+1 and
             attempt.get("result", {}).get("status") == "verified_synchronization_artifacts" and
             attempt["result"].get("native_clock_status") == status,
             "Recorded synchronization journal differs from its audit")
    movie_frames = {}
    for observation in observations:
        _require(observation.get("event") in ("movie_frame", "movie_end") and
                 observation.get("status") in ("matched_message_clocks", "unavailable"),
                 "Invalid recorded native clock observation")
        if observation["event"] == "movie_frame":
            index = observation.get("frame_index")
            _require(type(index) is int and 0 <= index < capture["captured_frames"] and index not in movie_frames,
                     "Repeated or invalid recorded native clock frame")
            movie_frames[index] = observation
    unavailable = [index for index in range(capture["captured_frames"])
                   if movie_frames.get(index, {}).get("status") != "matched_message_clocks"]
    _require(status != "matched_message_clocks" or (not unavailable and observations and
             all(row["status"] == "matched_message_clocks" for row in observations)),
             "Recorded native clock summary contradicts its frame observations")
    reasons = sorted({reason for row in [native, *observations] for reason in row.get("reason_codes", [])})
    return {"synchronization_recorded": {"status": status, "audit": str(path), "audit_sha256": sha256_file(path),
                "audit_url": _url(root, path), "observation_count": len(observations),
                "matched_captured_frames": capture["captured_frames"]-len(unavailable),
                "unavailable_captured_frames": len(unavailable), "unavailable_frame_indices": unavailable,
                "reason_codes": reasons, "recomputed": False,
                "scope": "Saved journal-bound native message clock associations; not timing or training acceptance."}}


def _recorded_acceptance(root, attempt, capture):
    """Display saved counts without loading partitions or reevaluating samples."""
    out = Path(attempt["out"])
    path = _owned(out, out/"competitive_acceptance.json")
    result = attempt.get("result", {})
    acceptance = _journal_metadata(attempt, path)
    _require(acceptance.get("schema_version") == 1 and
             acceptance.get("profile") == "cs2-competitive-masked-control-acceptance-v2" and
             acceptance.get("status") == "complete" and
             acceptance.get("clip_id") == capture["capture_clip_id"] and
             all(str(acceptance.get(key)) == str(capture[key]) for key in ("demo_id", "round_id", "steam_id", "player_slot")) and
             Path(result.get("acceptance", "")).resolve() == path,
             "Recorded acceptance does not identify this capture")
    counts = {key: acceptance.get(key) for key in ("accepted_count", "rejected_count")}
    _require(all(type(value) is int and value >= 0 and result.get(key) == value for key, value in counts.items()) and
             acceptance.get("candidate_count") == sum(counts.values()), "Recorded acceptance counts are inconsistent")
    return {"acceptance_recorded": {**counts, "report": str(path), "report_url": _url(root, path),
                                   "recomputed": False},
            "status": "accepted_recorded" if counts["accepted_count"] else "no_accepted_samples_recorded"}


def _load_collection(root):
    collection_path = _owned(root, root/"round_collection.json")
    collection = read_json(collection_path)
    _require(collection.get("schema_version") == 1 and collection.get("profile") == COLLECTION_PROFILE,
             "Unsupported round collection profile")
    _require(isinstance(collection.get("round_ids"), list) and collection["round_ids"] and
             all(type(value) is int and value > 0 for value in collection["round_ids"]) and
             len(set(collection["round_ids"])) == len(collection["round_ids"]), "Invalid collection rounds")
    entries = collection.get("batches")
    _require(isinstance(entries, list) and 1 <= len(entries) <= batch.MAX_JOBS, "Invalid collection batch list")
    loaded, intervals, seen = [], [], set()
    for entry in entries:
        path = _owned(root, entry.get("plan"))
        _require(path.name == "batch_plan.json" and path not in seen, "Duplicate or invalid collection batch path")
        seen.add(path)
        _hash_matches(path, entry.get("plan_sha256"))
        plan = read_json(path)
        _require(plan.get("schema_version") == 1 and plan.get("profile") == batch.PROFILE and
                 plan.get("stage_order") == list(batch.STAGES), "Unsupported collection batch plan")
        jobs, ticks = plan.get("jobs"), plan.get("clip_ticks")
        _require(isinstance(jobs, list) and 1 <= len(jobs) <= batch.MAX_JOBS and len(jobs) == entry.get("job_count") and
                 type(ticks) is int and 32 <= ticks <= batch.MAX_CLIP_TICKS and ticks % 2 == 0 and
                 ticks == entry.get("clip_ticks"), "Collection batch bounds differ from plan")
        for item in jobs:
            job, job_id = item["job"], item["job_id"]
            _require(isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{24}", job_id) and job.get("clip_id") == job_id,
                     "Invalid planned clip identity")
            spec = _owned(path.parent, item.get("spec"))
            _hash_matches(spec, item.get("spec_sha256"))
            _require(read_json(spec) == job, "Planned job differs from retained specification")
            a, b = job.get("start_demo_tick"), job.get("end_demo_tick")
            _require(type(a) is int and type(b) is int and a >= 199 and b-a == ticks and
                     job.get("fps") == 32 and job.get("width") == 1280 and job.get("height") == 720 and
                     job.get("competitive_replay_profile") == batch.CURRENT_PROFILE and
                     str(job.get("steam_id")) == str(collection.get("steam_id")) and
                     job.get("demo_id") == collection.get("source", {}).get("demo_id") and
                     job.get("round_id") in collection["round_ids"], "Planned clip differs from collection identity or bounds")
            intervals.append((job["round_id"], a, b))
        _require(entry.get("planned_source_seconds") == len(jobs)*ticks/64, "Collection batch duration differs from plan")
        loaded.append((path, plan, batch._load_state(path.parent, plan, entry["plan_sha256"])))
    chronological = sorted(intervals, key=lambda row: row[1])
    _require(all(left[2] <= right[1] for left, right in zip(chronological, chronological[1:])),
             "Collection source intervals overlap")
    windows = collection.get("windows")
    _require(isinstance(windows, list) and windows, "Collection needs eligible windows")
    eligible_ticks, excluded_ticks = 0, 0
    for window in windows:
        a, b = window.get("start_demo_tick"), window.get("end_demo_tick")
        _require(type(a) is int and type(b) is int and 199 <= a < b and
                 window.get("round_id") in collection["round_ids"] and window.get("eligible_ticks") == b-a,
                 "Invalid eligible collection window")
        planned, exclusions = window.get("planned_intervals"), window.get("excluded_intervals")
        _require(isinstance(planned, list) and isinstance(exclusions, list), "Invalid window coverage lists")
        spans = []
        for span in planned:
            _require(isinstance(span, list) and len(span) == 2 and all(type(value) is int for value in span) and
                     a <= span[0] < span[1] <= b, "Planned interval escapes its eligible window")
            spans.append(span)
        _require(window.get("planned_ticks") == sum(end-start for start, end in spans), "Window planned tick count changed")
        for gap in exclusions:
            start, end = gap.get("start_demo_tick"), gap.get("end_demo_tick")
            _require(type(start) is int and type(end) is int and a <= start < end <= b and
                     isinstance(gap.get("reason"), str) and gap["reason"], "Invalid excluded source interval")
            spans.append([start, end])
            excluded_ticks += end-start
        spans.sort()
        _require(spans and spans[0][0] == a and spans[-1][1] == b and
                 all(left[1] == right[0] for left, right in zip(spans, spans[1:])),
                 "Window intervals do not account for every eligible tick")
        eligible_ticks += b-a
    ordered_windows = sorted(windows, key=lambda row: row["start_demo_tick"])
    _require(all(left["end_demo_tick"] <= right["start_demo_tick"] for left, right in zip(ordered_windows, ordered_windows[1:])),
             "Eligible collection windows overlap")
    expected = sorted(((window["round_id"], a, b) for window in windows for a, b in window["planned_intervals"]),
                      key=lambda row: row[1])
    _require(chronological == expected, "Collection windows differ from planned clips")
    coverage = collection.get("coverage", {})
    planned_ticks = sum(b-a for _, a, b in intervals)
    _require(collection.get("job_count") == len(intervals) and coverage.get("planned_ticks") == planned_ticks and
             coverage.get("eligible_ticks") == eligible_ticks and coverage.get("excluded_ticks") == excluded_ticks and
             eligible_ticks == planned_ticks+excluded_ticks and
             collection.get("planned_source_seconds") == planned_ticks/64 and
             coverage.get("history_images") == 8 and coverage.get("missing_initial_history_positions_per_clip") == 7 and
             coverage.get("cross_clip_history_stitching") is False, "Collection coverage or history contract changed")
    return collection, loaded


def _time_label(seconds):
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes)}:{seconds:06.3f}".rstrip("0").rstrip(".")


def _html(report):
    escape = lambda value: html.escape(str(value), quote=True)
    clips = report["clips"]
    windows = report["eligible_windows"]

    def clip_label(row):
        window = next(window for window in windows if window["round_id"] == row["round_id"] and
                      window["start_demo_tick"] <= row["start_demo_tick"] < row["end_demo_tick"] <= window["end_demo_tick"])
        start = (row["start_demo_tick"]-window["start_demo_tick"])/64
        end = (row["end_demo_tick"]-window["start_demo_tick"])/64
        return f'Source round {row["round_id"]} · {_time_label(start)}–{_time_label(end)}'

    options = "".join(f'<option value="{escape(row["video_url"])}">{escape(clip_label(row))}</option>'
                      for row in clips if row.get("video_url"))
    rows = []
    for row in clips:
        review = f'<a href="{escape(row["review_url"])}">Optional frame viewer</a>' if row.get("review_url") else "Not generated"
        video = f'<a href="{escape(row["video_url"])}">Open video</a>' if row.get("video_url") else "—"
        restoration = row.get("restoration_claims_recorded", {})
        restored = (all(restoration.get(name) is True for name in
                        ("settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")) and
                    all(restoration.get(name) == "restored" for name in ("settings_journal_state", "gameinfo_journal_state")))
        claims = "Restoration recorded" if restored else "Needs check" if restoration else "Not recorded"
        status = {"ready_for_acceptance": "Ready for acceptance", "unsupported_capture_setup": "Unsupported capture setup",
                  "processing": "Processing",
                  "accepted_recorded": "Acceptance recorded", "no_accepted_samples_recorded": "No accepted samples recorded",
                  "failed": "Needs attention", "unrecorded": "Not recorded"}[row["status"]]
        synchronization = row.get("synchronization_recorded", {})
        native_status = synchronization.get("status")
        timing = ("Matched in saved audit" if native_status == "matched_message_clocks" else
                  f'Unavailable for {synchronization["unavailable_captured_frames"]} frame(s)' if native_status == "unavailable" and
                  synchronization["unavailable_captured_frames"] else "Unavailable in saved audit" if native_status == "unavailable" else "Not recorded")
        if synchronization.get("audit_url"):
            timing = f'<a href="{escape(synchronization["audit_url"])}">{timing}</a>'
        rows.append(f'<tr><td>{escape(clip_label(row))}</td>'
                    f'<td>{row["captured_seconds"]:.3f} / {row["planned_seconds"]:.3f}</td>'
                    f'<td>{row["captured_frames"]} / {row["planned_frames"]}</td><td>{status}</td>'
                    f'<td>{video}</td><td>{review}</td><td>{timing}</td><td>{escape(claims)}</td><td>{escape(row.get("error", ""))}</td></tr>')
    gap_reasons = {"outside_selected_eligible_alive_windows": "Between selected eligible windows",
                   "remaining_span_below_32_tick_batch_minimum": "Remainder shorter than the capture minimum",
                   "odd_final_tick_outside_32fps_capture_grid": "Final source tick omitted by frame sampling"}
    gaps = "".join(f'<li>Demo time {_time_label(gap["start_demo_tick"]/64)}–{_time_label(gap["end_demo_tick"]/64)}: '
                   f'{escape(gap_reasons.get(gap["reason"], gap["reason"].replace("_", " ")))} '
                   f'<small>(source ticks [{gap["start_demo_tick"]}, {gap["end_demo_tick"]}))</small></li>'
                   for gap in report["source_gaps"])
    rounds = "".join(f'<li>Source round {row["round_id"]}: {row["captured_seconds"]:.3f} / {row["planned_seconds"]:.3f} '
                     f'seconds, {row["captured_frames"]} / {row["planned_frames"]} frames captured/planned.</li>'
                     for row in report["rounds"])
    timestamp = escape(report["generated_at_utc"].replace("T", " ").replace("+00:00", " UTC"))
    timing_note = (f'<p><strong>Timing review needed:</strong> {report["recorded_timing_exception_clips"]} captured clip(s) '
                   f'have unavailable timing associations in their saved audits, affecting '
                   f'{report["recorded_timing_exception_frames"]} captured frame(s). '
                   'These clips remain viewable; affected image histories require rejection during sample acceptance.</p>'
                   if report["recorded_timing_exception_clips"] else "")
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Round progression recording</title><style>
body{{font:16px system-ui,sans-serif;background:#111820;color:#e8edf3;margin:2rem;line-height:1.5}}
a{{color:#8bd5ff}} video{{width:min(100%,1000px);display:block;background:black;margin:1rem 0}}
select,button{{font:inherit;padding:.5rem;max-width:100%}} table{{border-collapse:collapse;font-size:.9rem}}
td,th{{border:1px solid #415062;padding:.5rem;text-align:left;vertical-align:top}} .scroll{{overflow:auto}}
</style><h1>Round progression recording</h1>
<p>Player {escape(report["steam_id"])} · {report["captured_source_seconds"]:.3f} / {report["planned_source_seconds"]:.3f}
seconds captured/planned · {report["captured_frames"]} / {report["planned_frames"]} frames.</p>
<p><small>Report generated {timestamp}. <a href="round_report.json">Detailed report and source records</a>.</small></p>
{timing_note}
<p>The approved capture setup is trusted. Routine captures need no manual HUD review or automated overlay scan.
Existing frame viewers remain optional. A different capture setup needs its own policy.</p>
<p>This viewer reports recorded progress and the current capture policy. It does not verify training acceptance,
timing or current media bytes. Settings restoration reflects the saved capture records.</p>
<p>Clips have independent eight-image histories. The first seven image positions in each clip lack full history.
Cross-clip history is not stitched. No model training or sample acceptance is performed by this report.</p>
<label for="clips">Captured clip</label> <select id="clips">{options or '<option value="">No completed captures</option>'}</select>
<button id="next" type="button">Next clip</button><video id="player" controls preload="metadata"></video>
<h2>Selected round windows</h2>
<p>{len(windows)} eligible windows across {len(report["rounds"])} source rounds ·
{report["coverage"]["eligible_ticks"]/64:.3f} seconds of eligible alive competitive time ·
{report["coverage"]["excluded_ticks"]/64:.3f} seconds omitted from those windows.</p>
<p>Source round numbers follow the parsed demo records. Clip times start at the beginning of each eligible alive window.
Freeze time, pauses, dead-player time and post-round time are outside this scope.</p>
<ul>{rounds}</ul><h2>Clips in source order</h2><div class="scroll"><table><thead><tr>
<th>Clip</th><th>Seconds captured/planned</th><th>Frames captured/planned</th><th>Status</th>
<th>Video</th><th>Optional frames</th><th>Timing association</th><th>Settings</th><th>Issue</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Source gaps outside planned clips</h2><ul>{gaps or '<li>No omitted ticks within the reported eligible windows.</li>'}</ul>
<p>Clips with no captured frames are additional gaps in recorded coverage. Acceptance status does not change captured coverage.
Source tick intervals exclude the ending tick and use 64 ticks per second.</p>
<script>const clips=document.getElementById('clips'),player=document.getElementById('player');
function select(){{if(clips.value){{player.src=clips.value;player.load();}}}}
clips.addEventListener('change',select);document.getElementById('next').addEventListener('click',()=>{{
if(clips.selectedIndex+1<clips.options.length){{clips.selectedIndex++;select();}}}});select();</script></html>'''


def summarize_round_collection(collection_dir: Path, out: Path | None = None, *, include_disk_usage=False):
    """Write only round_report.json and index.html in the collection itself."""
    root = Path(collection_dir).resolve()
    _require(out is None or Path(out).resolve() == root, "Round reports must stay in the collection directory")
    collection, loaded = _load_collection(root)
    clips = []
    for path, plan, state in loaded:
        for item in plan["jobs"]:
            job = item["job"]
            row = {"job_id": item["job_id"], "batch_plan": str(path), "round_id": job["round_id"],
                   "demo_id": job["demo_id"], "steam_id": job["steam_id"], "player_slot": job["player_slot"],
                   "start_demo_tick": job["start_demo_tick"], "end_demo_tick": job["end_demo_tick"],
                   "planned_seconds": (job["end_demo_tick"]-job["start_demo_tick"])/64,
                   "planned_frames": (job["end_demo_tick"]-job["start_demo_tick"])//2,
                   "captured_seconds": 0, "captured_frames": 0, "status": "unrecorded", "training_ready": False}
            stages = state["jobs"][item["job_id"]]["stages"]
            render = _latest(stages, "render")
            row["stage_statuses_recorded"] = {name: (_latest(stages, name) or {}).get("status", "unrecorded")
                                              for name in batch.STAGES}
            if render and render["status"] == "completed":
                try:
                    row.update(_display_render(root, render, item))
                    row["status"] = ("processing" if policy_allows_capture(row["hud_policy_current"])
                                     else "unsupported_capture_setup")
                    synchronization = _latest(stages, "synchronization")
                    if synchronization and synchronization["status"] == "completed":
                        row.update(_recorded_synchronization(root, synchronization, row))
                    hud = _latest(stages, "hud_review")
                    hud_complete = False
                    if hud and hud["status"] == "completed":
                        row["hud_stage_result_recorded"] = hud.get("result", {}).get("status")
                        if hud.get("result", {}).get("status") == "hud_setup_trusted":
                            row.update(_recorded_hud_policy(root, hud, row))
                            hud_complete = True
                        elif hud.get("result", {}).get("status") in ("pending_visual_review", "visual_review_verified"):
                            _legacy_hud_bundle(hud, row)
                            hud_complete = True
                        if hud.get("result", {}).get("bundle"):
                            try:
                                row.update(_review_link(root, hud, row))
                            except (ValueError, OSError, KeyError, TypeError) as error:
                                row["optional_review_unavailable_reason"] = str(error)
                    prerequisites_complete = (hud_complete and
                        row["stage_statuses_recorded"]["process"] == "completed" and
                        row["stage_statuses_recorded"]["synchronization"] == "completed")
                    if prerequisites_complete and row["status"] == "processing":
                        row["status"] = "ready_for_acceptance"
                    acceptance = _latest(stages, "acceptance")
                    if (row["status"] == "ready_for_acceptance" and acceptance and acceptance["status"] == "completed" and
                            acceptance.get("result", {}).get("status") == "accepted_partition_verified"):
                        row.update(_recorded_acceptance(root, acceptance, row))
                except (ValueError, OSError, KeyError, TypeError) as error:
                    row.update(status="failed", error=str(error))
            elif render and render["status"] in ("failed", "interrupted"):
                row.update(status="failed", error=str(render.get("error", render.get("revalidation_error", "Render incomplete"))))
            failures = [attempt for name in batch.STAGES if (attempt := _latest(stages, name)) and
                        attempt["status"] in ("failed", "interrupted")]
            if failures:
                row.update(status="failed", error=str(failures[-1].get("error", "Recorded stage failure")))
            clips.append(row)
    clips.sort(key=lambda row: (row["start_demo_tick"], row["round_id"], row["job_id"]))
    windows = sorted(collection["windows"], key=lambda row: row["start_demo_tick"])
    gaps = [{"round_id": window["round_id"], **gap} for window in windows for gap in window["excluded_intervals"]]
    for left, right in zip(windows, windows[1:]):
        if left["end_demo_tick"] < right["start_demo_tick"]:
            gaps.append({"start_demo_tick": left["end_demo_tick"], "end_demo_tick": right["start_demo_tick"],
                         "reason": "outside_selected_eligible_alive_windows"})
    gaps.sort(key=lambda row: row["start_demo_tick"])
    rounds = []
    for round_id in dict.fromkeys(row["round_id"] for row in clips):
        rows = [row for row in clips if row["round_id"] == round_id]
        rounds.append({"round_id": round_id, "source_intervals": [[row["start_demo_tick"], row["end_demo_tick"]] for row in rows],
                       **{key: sum(row[key] for row in rows) for key in
                          ("planned_seconds", "captured_seconds", "planned_frames", "captured_frames")}})
    report = {"schema_version": 1, "profile": PROFILE, "collection_manifest": str(root/"round_collection.json"),
              "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "collection_sha256": sha256_file(root/"round_collection.json"), "steam_id": collection["steam_id"],
              "round_ids": collection["round_ids"], "coverage": collection["coverage"], "rounds": rounds,
              "eligible_windows": windows, "clips": clips, "source_gaps": gaps,
              "job_status_counts": dict(Counter(row["status"] for row in clips)),
              "planned_source_seconds": sum(row["planned_seconds"] for row in clips),
              "captured_source_seconds": sum(row["captured_seconds"] for row in clips),
              "planned_frames": sum(row["planned_frames"] for row in clips),
              "captured_frames": sum(row["captured_frames"] for row in clips),
              "accepted_sample_count_recorded": sum(row.get("acceptance_recorded", {}).get("accepted_count", 0) for row in clips),
              "rejected_sample_count_recorded": sum(row.get("acceptance_recorded", {}).get("rejected_count", 0) for row in clips),
              "hud_policy_status_counts": dict(Counter(
                  row.get("hud_policy_current", {}).get("status", "unrecorded") for row in clips)),
              "synchronization_status_counts_recorded": dict(Counter(
                  row.get("synchronization_recorded", {}).get("status", "unrecorded") for row in clips)),
              "recorded_timing_exception_clips": sum(row.get("synchronization_recorded", {}).get("status") == "unavailable" for row in clips),
              "recorded_timing_exception_frames": sum(row.get("synchronization_recorded", {}).get("unavailable_captured_frames", 0) for row in clips),
              "training_ready": False, "acceptance_evaluated": False, "model_training_performed": False,
              "verification_scope": "Display metadata hashes, journal binding and media presence only; media bytes, timing, HUD and acceptance are not revalidated."}
    if include_disk_usage:
        report["collection_disk_bytes"] = sum(path.stat().st_size for path in root.rglob("*")
                                               if path.is_file() and not path.is_symlink() and path.name not in ("round_report.json", "index.html"))
    # Only these two presentation products may be replaced; immutable evidence is
    # read above and is never passed to batch execution or acceptance providers.
    for name, content in (("round_report.json", json.dumps(report, indent=2, allow_nan=False)+"\n"),
                          ("index.html", _html(report))):
        destination = _owned(root, root/name)
        destination.write_text(content, encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--include-disk-usage", action="store_true")
    args = parser.parse_args(argv)
    report = summarize_round_collection(args.collection, include_disk_usage=args.include_disk_usage)
    print(json.dumps({key: report[key] for key in ("planned_source_seconds", "captured_source_seconds", "job_status_counts")}))


if __name__ == "__main__":
    main()
