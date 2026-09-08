"""Indexed immutable native session history and bounded logical movie views."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib
import uuid
import time
import shutil

from .io import read_json, sha256_file
from .launcher_backend import save_settings
from .session_profile import PROFILE, PLUGIN_SHA256, require
from .native_replay_profile import CURRENT_PROFILE, get_native_replay_profile, header_matches_profile

VIEW_PROFILE = "cs2-session-ledger-view-v1"
CONTEXT = ("header", "demo_packet_hook_installed", "demo_packet_read", "network_clock_epoch", "network_message")
_cache = OrderedDict()


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)


def index_session(root, *, emit=None):
    root = Path(root).resolve()
    manifests = list(root.glob("*.render.json"))
    require(len(manifests) == 1, "Session requires one lifecycle manifest")
    render = read_json(manifests[0])
    require(render.get("render_status") == "session_captured_timing_unverified" and
            render.get("recording_session", {}).get("profile") == PROFILE, "Session has not closed successfully")
    require(all(render.get(k) == PLUGIN_SHA256 for k in ("plugin_sha256", "plugin_source_sha256", "plugin_staged_sha256")),
        "Session plugin differs from the registered recorder")
    require(type(render.get("cs2_exit_code")) is int and render["cs2_exit_code"] == 0 and
        all(render.get(k) is True for k in ("settings_restored", "gameinfo_restored", "staged_plugin_removed_from_game")),
        "Session game lifecycle has not closed and restored")
    schedule = read_json(root/"session-plan.json")
    require(sha256_file(root/"session-plan.json") == render["recording_session"]["plan_sha256"], "Session schedule changed")
    receipt_path = root/"session-index.json"
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        verify_index(receipt)
        return receipt
    database = root/"native-index.sqlite"
    if database.exists():
        # A crash before publication must never turn a partial index into proof.
        retained = root/("incomplete-index-"+uuid.uuid4().hex+".sqlite")
        database.rename(retained)
        journal = Path(str(database)+"-journal")
        if journal.exists():
            journal.rename(Path(str(retained)+"-journal"))
    digest = hashlib.sha256()
    counts, closed, positions = {}, {}, {}
    last_notice = time.monotonic()
    storage = schedule.get("storage", {})
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE records (n INTEGER PRIMARY KEY, event TEXT, movie TEXT, frame INTEGER, tick INTEGER, payload BLOB)")
        with (root/"capture_ledger.jsonl").open("rb") as stream:
            for n, line in enumerate(stream):
                digest.update(line)
                row = json.loads(line)
                require(isinstance(row, dict), "Native session record is not an object")
                kind = row.get("event", "")
                if kind == "header":
                    require(header_matches_profile(row, native_profile=CURRENT_PROFILE), "Session native header has an unsupported profile")
                if kind == "session_fault":
                    raise ValueError("Session native fault: "+row.get("reason", "unknown"))
                candidate = row.get("submission_candidate", {}) if kind == "pixel_readback" else row
                movie = candidate.get("movie_name")
                frame = candidate.get("capture_index", candidate.get("next_capture_index"))
                db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (n, kind, movie, frame,
                    row.get("replay_demo_tick"), zlib.compress(line, 1)))
                counts[kind] = counts.get(kind, 0)+1
                if emit and time.monotonic()-last_notice > 15:
                    emit(f"Indexing native recording evidence: {n+1:,} records")
                    last_notice = time.monotonic()
                if n % 10000 == 0 and storage:
                    require(shutil.disk_usage(root).free >= storage["reserve_bytes"], "Not enough space to continue native indexing")
                    require(database.stat().st_size <= storage.get("max_index_bytes", storage["max_log_bytes"]+512_000_000),
                        "Native index exceeded its storage bound")
                if kind == "session_run_closed":
                    key = row["run_id"]
                    require(key not in closed and row.get("complete") is True, "Repeated or incomplete recording closure")
                    closed[key] = row
                if kind == "movie_frame":
                    previous = positions.get(movie)
                    require(type(frame) is int and frame == (0 if previous is None else previous[0]+1) and
                        row.get("counter_after") == frame+1 and
                        (previous is None or row["replay_demo_tick"]-previous[1] == 2), "Session frame counter/tick discontinuity")
                    positions[movie] = frame, row["replay_demo_tick"]
        require(digest.hexdigest() == render["capture_ledger_sha256"], "Native ledger changed before indexing")
        require(counts.get("header") == 1 and counts.get("movie_frame") == counts.get("pixel_readback"), "Session native frame coverage differs")
        expected_runs = schedule["runs"][:render["recording_session"]["completed_runs"]]
        require(set(closed) == {r["id"] for r in expected_runs} and counts.get("movie_end") == len(expected_runs), "Session endpoints do not cover its closed runs")
        for run in expected_runs:
            require(run["min_frames"] <= closed[run["id"]]["frames"] <= run["max_frames"] and
                positions[run["prefix"]+"_"][0]+1 == closed[run["id"]]["frames"], "Recording closure counter differs")
        db.execute("CREATE INDEX by_movie ON records(movie,event,frame)")
        db.execute("CREATE INDEX by_event ON records(event,n)")
    receipt = {"profile": PROFILE, "root": str(root), "render_manifest": str(manifests[0]),
        "render_sha256": sha256_file(manifests[0]), "database": str(database), "database_sha256": sha256_file(database),
        "ledger": str(root/"capture_ledger.jsonl"), "ledger_sha256": render["capture_ledger_sha256"],
        "schedule": str(root/"session-plan.json"), "schedule_sha256": sha256_file(root/"session-plan.json"),
        "record_count": sum(counts.values()), "event_counts": counts,
        "closed_runs": [run["id"] for run in expected_runs]}
    save_settings(receipt_path, receipt)
    return receipt


def verify_index(index):
    require(index.get("profile") == PROFILE, "Unsupported recording session index")
    root = Path(index["root"]).resolve()
    for name in ("database", "render_manifest", "ledger", "schedule"):
        path = Path(index[name]).resolve()
        require(path.is_relative_to(root), "Session index file escapes its owned root")
        digest = index["render_sha256" if name == "render_manifest" else name+"_sha256"]
        require(sha256_file(path) == digest, "Shared recording evidence changed: "+name)


def make_view(index, run, segment, destination):
    document = {"profile": VIEW_PROFILE, "session_index": str(Path(index["root"])/"session-index.json"),
        "session_index_sha256": sha256_file(Path(index["root"])/"session-index.json"),
        "run_id": run["id"], "segment_id": segment["id"], "movie_name": run["prefix"]+"_",
        "start_demo_tick": segment["start_demo_tick"], "end_demo_tick": segment["end_demo_tick"]}
    save_settings(destination, document)
    return SessionLedger(destination)


class SessionLedger(list):
    """A list-compatible, disk-backed prefix with bounded capture observations.

    Every context record from process start is retained. No native clock or
    packet IDs are reset. Only logical image indices are projected to a shard.
    """
    def __init__(self, path):
        self.path = Path(path).resolve()
        view = read_json(self.path)
        require(view.get("profile") == VIEW_PROFILE, "Unsupported session ledger view")
        self.view = view
        index_path = Path(view["session_index"])
        require(sha256_file(index_path) == view["session_index_sha256"], "Session view index changed")
        self.index_document = read_json(index_path)
        verify_index(self.index_document)
        self.database = Path(self.index_document["database"])
        self.cache_key = (sha256_file(self.path), self.index_document["database_sha256"], self.index_document["ledger_sha256"])
        schedule = read_json(Path(self.index_document["schedule"]))
        run = next((r for r in schedule["runs"] if r["id"] == view["run_id"]), None)
        require(run is not None and run["id"] in self.index_document["closed_runs"] and
            view["movie_name"] == run["prefix"]+"_" and view["segment_id"] in run["segments"], "View does not belong to a closed session run")
        start, end = view["start_demo_tick"], view["end_demo_tick"]
        require(type(start) is int and type(end) is int and start < end and (end-start) % 2 == 0 and
            any(e["start"] == start and e["end"] == end for e in run["eligible"]), "View interval differs from scheduled eligibility")
        self.selected = {}
        with connect(self.database) as db:
            frames = db.execute("SELECT n,payload FROM records WHERE movie=? AND event='movie_frame' AND tick>? AND tick<=? ORDER BY frame",
                                (view["movie_name"], start, end)).fetchall()
            require(len(frames) == (end-start)//2 and 2 <= len(frames) <= 10000, "Session view does not cover all planned frames")
            original = [json.loads(zlib.decompress(raw)) for _, raw in frames]
            first = original[0]["capture_index"]
            require([r["capture_index"] for r in original] == list(range(first, first+len(original))), "View frame indices are not contiguous")
            for (n, _), row in zip(frames, original):
                self.selected[n] = self._movie(row, first, n)
            pixels = db.execute("SELECT n,payload FROM records WHERE movie=? AND event='pixel_readback' AND frame>=? AND frame<? ORDER BY n",
                                (view["movie_name"], first, first+len(frames))).fetchall()
            require(len(pixels) == len(frames), "View readbacks do not cover its images")
            for n, raw in pixels:
                row = json.loads(zlib.decompress(raw))
                row["submission_candidate"] = self._movie(row["submission_candidate"], first, n)
                row["session_record_index"] = n
                self.selected[n] = row
            boundary = db.execute("SELECT n,payload,event FROM records WHERE movie=? AND frame=? AND event IN ('movie_frame','movie_end') ORDER BY n",
                                  (view["movie_name"], first+len(frames))).fetchall()
            require(len(boundary) == 1, "View needs one real subsequent observation boundary")
            n, raw, event = boundary[0]
            row = json.loads(zlib.decompress(raw))
            row["session_origin_event"] = event
            row["session_record_index"] = n
            row["native_capture_index"] = row.get("capture_index", row.get("next_capture_index"))
            row.pop("capture_index", None); row.pop("counter_after", None)
            row.update(event="session_boundary", next_capture_index=len(frames))
            self.selected[n] = row
            self.last_record = max(self.selected)
            self.header = json.loads(zlib.decompress(db.execute("SELECT payload FROM records WHERE event='header'").fetchone()[0]))
            self.count = db.execute("SELECT COUNT(*) FROM records WHERE n<=? AND event IN (?,?,?,?,?)", (self.last_record, *CONTEXT)).fetchone()[0]+len(self.selected)
        self.capture = [self.header]+[self.selected[n] for n in sorted(self.selected)]

    @staticmethod
    def _movie(row, first, n):
        row = dict(row)
        row["native_capture_index"] = row["capture_index"]
        row["capture_index"] -= first
        if "counter_after" in row:
            row["native_counter_after"] = row["counter_after"]
            row["counter_after"] -= first
        row["session_record_index"] = n
        return row

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        if key == 0:
            return self.header
        raise TypeError("Session history must be streamed, not sliced")

    def index(self, value, *args):
        return value.get("session_record_index", -1)

    def __iter__(self):
        return self.iter_events(None)

    def iter_events(self, kinds):
        wanted = set(kinds) if kinds is not None else set(CONTEXT)|{"movie_frame", "pixel_readback", "session_boundary"}
        selected = {n: row for n, row in self.selected.items() if row["event"] in wanted}
        context = sorted(wanted.intersection(CONTEXT))
        if not context:
            yield from (selected[n] for n in sorted(selected))
            return
        with connect(self.database) as db:
            # Several event kinds otherwise make SQLite sort millions of BLOBs
            # into a temporary tree. Scan the ordered primary key instead.
            table = "records NOT INDEXED" if len(context) > 1 else "records"
            rows = db.execute("SELECT n,payload FROM "+table+" WHERE n<=? AND event IN ("+",".join("?" for _ in context)+") ORDER BY n",
                              (self.last_record, *context))
            pending = iter(sorted(selected)); next_selected = next(pending, None)
            for n, raw in rows:
                while next_selected is not None and next_selected < n:
                    yield selected[next_selected]
                    next_selected = next(pending, None)
                yield json.loads(zlib.decompress(raw))
            while next_selected is not None:
                yield selected[next_selected]
                next_selected = next(pending, None)


def events(records, kinds):
    if isinstance(records, SessionLedger):
        return records.iter_events(kinds)
    return (r for r in records if r.get("event") in kinds)


def endpoints(records):
    return list(events(records, ("session_boundary",) if isinstance(records, SessionLedger) else ("movie_end",)))


def lifecycle_root(render, records, fallback, watch=None):
    """Bind a logical render to the one authenticated process lifecycle."""
    if not isinstance(records, SessionLedger):
        require("recording_session" not in render, "Session render requires an indexed native view")
        return Path(fallback)
    index = records.index_document
    shared = read_json(Path(index["render_manifest"]))
    require(render.get("recording_session") == shared.get("recording_session"), "Logical render changed its session identity")
    projected = {"clip_id", "round_id", "player_slot", "requested_start_demo_tick", "requested_end_demo_tick",
        "renderer_profile_sha256", "nominal_num_frames", "render_status", "capture_prefix", "source_job",
        "capture_ledger", "capture_ledger_sha256"}
    require(all(render.get(k) == v for k, v in shared.items() if k not in projected), "Logical render changed shared lifecycle evidence")
    view = records.view
    require(render.get("capture_prefix")+"_" == view["movie_name"] and
        render.get("requested_start_demo_tick") == view["start_demo_tick"] and
        render.get("requested_end_demo_tick") == view["end_demo_tick"] and
        render.get("source_job", {}).get("clip_id") == view["segment_id"], "Logical render disagrees with its native view")
    require(shared["source_job"]["demo_id"] == render["demo_id"] and
        str(shared["source_job"]["steam_id"]) == str(render["steam_id"]), "Session source/player identity changed")
    if watch:
        watch(Path(view["session_index"]), view["session_index_sha256"])
        for name in ("database", "render_manifest", "ledger", "schedule"):
            watch(Path(index[name]), index["render_sha256" if name == "render_manifest" else name+"_sha256"])
    return Path(index["root"])


def audit_cached(kind, records, source_key, compute):
    if not isinstance(records, SessionLedger):
        return compute()
    key = (kind, records.cache_key, source_key)
    if key not in _cache:
        result = compute()
        _cache[key] = zlib.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), 1)
        while len(_cache) > 4 or sum(len(v) for v in _cache.values()) > 64*1024**2:
            _cache.popitem(last=False)
        if key not in _cache:
            return result
    _cache.move_to_end(key)
    return json.loads(zlib.decompress(_cache[key]))
