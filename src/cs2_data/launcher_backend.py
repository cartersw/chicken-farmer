"""Desktop orchestration; existing producers and verifiers remain authoritative.

This module deliberately has no GUI imports. Saved launcher metadata is a
convenience index, never an alternative training-acceptance path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid


PROJECT = Path(__file__).resolve().parents[2]
SOURCE_PROFILE = "cs2-competitive-collection-sources-v1"
TABLES = ("usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet")


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def save_settings(path: Path, value: dict) -> None:
    """Atomic replacement is only for launcher-owned mutable metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes((json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DemoFile:
    path: Path
    size: int
    map_name: str = ""
    recorded_status: str = "Not prepared"
    parsed: Path | None = None


def _parsed_paths(project: Path, output: Path):
    # Restrict discovery to known layouts; never walk all frame/capture trees.
    paths = set((project / "data/parsed").glob("*/*/manifest.json"))
    paths.update((output / "runs").glob("*/parsed/*/manifest.json"))
    return sorted(paths, key=lambda p: str(p))


def scan_demos(folder: Path, output: Path, *, recursive=True, project=PROJECT) -> list[DemoFile]:
    folder, output = folder.expanduser().resolve(), output.expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("Choose an existing demo folder.")
    recorded = {}
    for path in _parsed_paths(project, output):
        try:
            doc = read_object(path)
            if doc.get("parse_status") != "complete" or doc.get("partial") is not False:
                continue
            source = str(Path(doc["source_path"]).resolve())
            current = doc.get("extractor_version") == "0.1.2"
            if source not in recorded or current:
                recorded[source] = (path.parent, doc, current)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    found = []

    def walk_error(error):
        raise error

    for directory, dirs, files in os.walk(folder, onerror=walk_error, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink()) if recursive else []
        for name in sorted(files):
            path = Path(directory, name)
            if path.suffix.lower() != ".dem" or path.is_symlink():
                continue
            size = path.stat().st_size
            parsed, doc, current = recorded.get(str(path.resolve()), (None, {}, False))
            status = "Not prepared"
            if parsed and doc.get("file_size") == size:
                status = "Recorded parse" if current else "Older parse; update needed"
            else:
                parsed, doc = None, {}
            found.append(DemoFile(path.resolve(), size, doc.get("map", ""), status, parsed))
            if len(found) > 10000:
                raise ValueError("More than 10,000 demos found; choose a smaller folder.")
    return sorted(found, key=lambda item: str(item.path).lower())


class StopRequested(Exception):
    pass


class TaskRunner:
    """One task at a time; cooperative stop never kills a game/restore process."""

    def __init__(self, output: Path, kind: str, emit, stop: threading.Event, *, project=PROJECT):
        self.project = project.resolve()
        self.output = output.expanduser().resolve()
        self.emit, self.stop = emit, stop
        self.run_dir = self.output / "runs" / (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + kind + "-" + uuid.uuid4().hex[:8])
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.journal = {"schema_version": 1, "kind": kind, "status": "running", "commands": [],
                        "run_dir": str(self.run_dir), "training_ready": False}
        self.save()
        self.emit("run", str(self.run_dir))

    def save(self):
        save_settings(self.run_dir / "run.json", self.journal)

    def log(self, message):
        message = str(message).rstrip()
        with (self.run_dir / "activity.log").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(message + "\n")
        # Full text stays on disk; bound a single UI event even for a JSON line.
        self.emit("log", message[:4000])

    def checkpoint(self):
        if self.stop.is_set():
            raise StopRequested("Stopped between tasks. Completed outputs have been retained.")

    def tool(self, name):
        path = self.project / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if not path.is_file():
            raise ValueError(f"Missing {path.name}. Run scripts/build.ps1 first.")
        return str(path)

    def python(self):
        # pythonw has no usable standard streams for these command-line tools.
        path = Path(sys.executable)
        if path.name.lower() == "pythonw.exe":
            path = path.with_name("python.exe")
        return str(path)

    def command(self, label, arguments):
        self.checkpoint()
        self.emit("status", label)
        self.log(label)
        self.log(subprocess.list2cmdline([str(arg) for arg in arguments]))
        item = {"label": label, "argv": [str(arg) for arg in arguments], "status": "running"}
        self.journal["commands"].append(item)
        self.save()
        environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        environment["PYTHONPATH"] = str(self.project / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        with subprocess.Popen(item["argv"], cwd=self.project, env=environment, shell=False,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            # Deliberately wait for normal completion even after Stop/Close.
            for line in process.stdout:
                self.log(line)
            code = process.wait()
        item.update(status="finished", exit_code=code)
        self.save()
        return code

    def finish(self, status, **details):
        self.journal.update(status=status, **details)
        self.save()


def _complete_manifest(path: Path, demo_id: str):
    doc = read_object(path / "manifest.json")
    if not (doc.get("demo_id") == doc.get("sha256") == demo_id
            and doc.get("parse_status") == "complete" and doc.get("partial") is False
            and str(doc.get("parser_schema_version")) == "2" and doc.get("extractor_version") == "0.1.2"):
        raise ValueError(f"A complete current extraction is required: {path}")
    for name in TABLES:
        if doc.get("files", {}).get(name) != hash_file(path / name):
            raise ValueError(f"Parsed source changed: {path / name}")
    return doc


def _find_parsed(task, demo_id, match_id):
    for manifest in reversed(_parsed_paths(task.project, task.output)):
        task.checkpoint()
        try:
            doc = read_object(manifest)
        except (OSError, ValueError):
            continue
        if doc.get("demo_id") != demo_id or doc.get("extractor_version") != "0.1.2":
            continue
        if doc.get("parse_status") != "complete" or doc.get("partial") is not False:
            continue
        if match_id and doc.get("match_id") != match_id:
            raise ValueError(f"Existing data uses series ID '{doc.get('match_id')}'. "
                             "Use that ID or leave the series field empty to preserve it.")
        task.log(f"Checking retained source tables: {manifest.parent}")
        _complete_manifest(manifest.parent, demo_id)
        return manifest.parent
    return None


def _sidecar(task, demo, demo_id, kind):
    folder = "phases" if kind == "phases" else "context"
    candidates = list((task.project / "data" / folder).glob("*.json"))
    candidates += list((task.output / "runs").glob(f"*/{folder}/{demo_id}.json"))
    for path in sorted(candidates, key=str):
        try:
            doc = read_object(path)
        except (OSError, ValueError):
            continue
        if (doc.get("demo_id") == doc.get("source_demo_sha256") == demo_id
                and doc.get("parse_status") == "complete" and doc.get("partial") is False
                and doc.get("schema_version") == (1 if kind == "phases" else 2)
                and (kind == "phases" or doc.get("producer") == "cs2-context-v2")):
            task.log(f"Reusing source sidecar: {path}")
            return path.resolve()
    path = task.run_dir / folder / f"{demo_id}.json"
    path.parent.mkdir(exist_ok=True)
    code = task.command(f"Reading {folder}: {demo.name}",
                        [task.tool("cs2-" + kind), "--input", str(demo), "--out", str(path)])
    if code != 0 or not path.is_file():
        raise ValueError(f"{folder.capitalize()} extraction failed. See the activity log.")
    doc = read_object(path)
    if doc.get("source_demo_sha256") != demo_id or doc.get("parse_status") != "complete" or doc.get("partial") is not False:
        raise ValueError(f"Incomplete or mismatched {folder} evidence: {path}")
    return path


def prepare_sources(task: TaskRunner, demos: list[Path], match_id="") -> Path:
    if not 1 <= len(demos) <= 8:
        raise ValueError("Select one to eight demos per task.")
    if len(match_id) > 200 or any(ord(c) < 32 for c in match_id):
        raise ValueError("Series ID must be at most 200 characters without control characters.")
    sources, seen = [], set()
    # Check the complete toolchain before starting a costly parse.
    for name in ("cs2-extract", "cs2-phases", "cs2-context"):
        task.tool(name)
    for index, demo in enumerate(demos, 1):
        task.checkpoint()
        demo = Path(demo).resolve()
        if demo.suffix.lower() != ".dem" or not demo.is_file():
            raise ValueError(f"Demo is unavailable: {demo}")
        task.emit("status", f"Checking demo {index}/{len(demos)}: {demo.name}")
        demo_id = hash_file(demo)
        if demo_id in seen:
            task.log(f"Skipping duplicate demo bytes: {demo}")
            continue
        seen.add(demo_id)
        parsed = _find_parsed(task, demo_id, match_id)
        code = 0
        if parsed is None:
            arguments = [task.tool("cs2-extract"), "extract", "--input", str(demo),
                         "--out", str(task.run_dir / "parsed")]
            if match_id:
                arguments += ["--match-id", match_id]
            code = task.command(f"Parsing demo {index}/{len(demos)}: {demo.name}", arguments)
            parsed = task.run_dir / "parsed" / demo_id
            if not (parsed / "manifest.json").is_file():
                raise ValueError("Parsing did not complete. The activity log and any partial outputs were retained.")
        _complete_manifest(parsed, demo_id)
        validation = read_object(parsed / "validation.json")
        if validation.get("demo_id") != demo_id or type(validation.get("passed")) is not bool:
            raise ValueError(f"Missing or mismatched extraction-quality report: {parsed}")
        # The extractor intentionally exits nonzero for retained quality issues.
        if code != 0 and not (validation["passed"] is False and validation.get("errors")):
            raise ValueError("Extractor failed beyond its retained quality report. See the activity log.")
        if not validation["passed"]:
            task.log("Parsed with quality issues; later per-sample checks still decide eligibility.")
            for error in validation.get("errors", []):
                task.log("  " + str(error))
        task.checkpoint()
        phase = _sidecar(task, demo, demo_id, "phases")
        task.checkpoint()
        context = _sidecar(task, demo, demo_id, "context")
        if hash_file(demo) != demo_id:
            raise ValueError(f"Demo changed during preparation: {demo}")
        sources.append({"source_id": "demo-" + demo_id[:16], "demo": str(demo), "parsed": str(parsed),
                        "phase_manifest": str(phase), "state_context": str(context)})
        task.emit("prepared", {"demo": str(demo), "parsed": str(parsed),
                               "quality_passed": validation["passed"]})
    path = task.run_dir / "sources.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump({"schema_version": 1, "profile": SOURCE_PROFILE, "sources": sources}, stream, indent=2)
        stream.write("\n")
    return path


def load_players(task: TaskRunner, demos, match_id=""):
    """Read a display roster from prepared tables; planning checks eligibility."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    sources = prepare_sources(task, demos, match_id)
    roster = {}
    for source in read_object(sources)["sources"]:
        task.checkpoint()
        task.emit("status", "Reading players: " + Path(source["demo"]).name)
        with pq.ParquetFile(Path(source["parsed"]) / "player_state.parquet") as reader:
            columns = ["steam_id", "player_name", "team"]
            if not set(columns) <= set(reader.schema_arrow.names):
                raise ValueError("Player names are missing from the prepared source; prepare a current extraction.")
            for batch in reader.iter_batches(batch_size=65536, columns=columns):
                task.checkpoint()
                # Collapse repeated per-tick names in Arrow before Python iteration.
                rows = pa.Table.from_batches([batch]).group_by(columns).aggregate([]).to_pylist()
                for row in rows:
                    steam_id = row["steam_id"]
                    if row["team"] not in (2, 3) or type(steam_id) is not int or not 0 < steam_id < 2**64:
                        continue
                    entry = roster.setdefault(str(steam_id), {"names": set(), "demos": set()})
                    name = row["player_name"]
                    if isinstance(name, str) and name.strip():
                        entry["names"].add(" ".join(name.split())[:100])
                    entry["demos"].add(source["demo"])
                    if len(roster) > 1024 or len(entry["names"]) > 128:
                        raise ValueError("Player roster exceeds the display limit; choose fewer demos.")
    players = [{"steam_id": steam_id, "name": " / ".join(sorted(entry["names"], key=str.casefold)) or "Unnamed player",
                "demo_count": len(entry["demos"])} for steam_id, entry in roster.items()]
    players.sort(key=lambda row: (row["name"].casefold(), row["steam_id"]))
    if not players:
        raise ValueError("No players were recorded on a playing team in the selected demos.")
    save_settings(task.run_dir / "players.json", {"sources": str(sources), "players": players, "training_ready": False})
    task.log(f"Loaded {len(players)} players. Capture planning separately checks competitive eligibility.")
    return players


def plan_captures(task: TaskRunner, demos, *, match_id="", clip_seconds=10, clips=4, steam_id=None):
    from .competitive_coverage import normalize_steam_id

    steam_id = normalize_steam_id(steam_id)
    if type(clips) is not int or not 1 <= clips <= 8 or clip_seconds not in (5, 10):
        raise ValueError("Choose 1-8 clips of 5 or 10 seconds.")
    task.tool("cs2-clocks")
    sources = prepare_sources(task, demos, match_id)
    destination = task.run_dir / "collection"
    arguments = [task.python(), "-m", "cs2_data.competitive_collection", "--sources", str(sources),
                 "--out", str(destination), "--clip-ticks", str(clip_seconds * 64), "--max-jobs", str(clips)]
    if steam_id is not None:
        arguments += ["--steam-id", steam_id]
        task.log(f"Planning only player Steam ID {steam_id}.")
    code = task.command("Selecting competitive clips and checking their clocks (may take several minutes)", arguments)
    plan = destination / "batch" / "batch_plan.json"
    if code != 0 or not plan.is_file():
        raise ValueError("Clip planning failed. Sources and diagnostics were retained; see the log.")
    return plan


def read_batch_display(path: Path):
    """Fast last-recorded status for display only; execution revalidates the plan."""
    path = path.expanduser().resolve()
    if path.is_dir():
        path /= "batch_plan.json"
    doc = read_object(path)
    if doc.get("profile") != "cs2-bounded-competitive-batch-v1" or not isinstance(doc.get("jobs"), list) or not 1 <= len(doc["jobs"]) <= 24:
        raise ValueError("Choose a batch_plan.json produced by this project's competitive planner.")
    ticks = doc.get("clip_ticks")
    if type(ticks) is not int or not 32 <= ticks <= 1280 or ticks % 2:
        raise ValueError("The batch has an unsupported clip length.")
    ids = set()
    for item in doc["jobs"]:
        if not isinstance(item, dict) or not isinstance(item.get("job"), dict):
            raise ValueError("The batch contains an invalid clip record.")
        key, job = item.get("job_id"), item["job"]
        if (not isinstance(key, str) or not 1 <= len(key) <= 128
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in key) or key in ids):
            raise ValueError("The batch contains an invalid or duplicate clip ID.")
        ids.add(key)
        steam_id = job.get("steam_id")
        valid_steam_id = (type(steam_id) is int and 0 < steam_id < 2**64) or (
            isinstance(steam_id, str) and steam_id.isascii() and steam_id.isdigit()
            and len(steam_id) <= 20 and 0 < int(steam_id) < 2**64)
        if (not isinstance(item.get("source_id"), str) or not item["source_id"]
                or not isinstance(job.get("map", ""), str)
                or type(job.get("round_id")) is not int or job["round_id"] <= 0
                or not valid_steam_id
                or type(job.get("start_demo_tick")) is not int or job["start_demo_tick"] < 0
                or type(job.get("end_demo_tick")) is not int
                or job["end_demo_tick"] > 2147483500
                or job["end_demo_tick"] - job["start_demo_tick"] != ticks):
            raise ValueError("The batch contains invalid clip display fields.")
    summary_path = path.parent / "batch_summary.json"
    try:
        summary = read_object(summary_path) if summary_path.is_file() else {}
    except (OSError, ValueError):
        summary = {}
    if summary.get("plan_sha256") != hash_file(path):
        summary = {}
    if summary:
        rows, counts = summary.get("jobs", []), summary.get("job_status_counts", {})
        valid = isinstance(rows, list) and isinstance(counts, dict)
        seen = set()
        for row in rows if isinstance(rows, list) else []:
            if (not isinstance(row, dict) or not isinstance(row.get("job_id"), str)
                    or row["job_id"] not in ids or row["job_id"] in seen
                    or not isinstance(row.get("status"), str) or not row["status"]):
                valid = False
                break
            seen.add(row["job_id"])
        for value in (summary.get("accepted_sample_count", 0), summary.get("rejected_sample_count", 0),
                      *(counts.values() if isinstance(counts, dict) else ())):
            if type(value) is not int or value < 0:
                valid = False
        if not valid:
            summary = {}
    return path, doc, summary


def capture_arguments(task, plan):
    path, doc, _ = read_batch_display(plan)
    tools = {"plugin": task.project / "tools/renderer/build/plugin-windows/Release/server.dll"}
    for name in ("ffmpeg", "ffprobe"):
        matches = sorted((task.project / ".tools/ffmpeg").glob(f"*/bin/{name}.exe"))
        if not matches:
            raise ValueError(f"Bundled {name} is missing; see docs/WINDOWS_RENDERING.md.")
        tools[name] = matches[-1]
    for name, file in tools.items():
        if not file.is_file():
            raise ValueError(f"Missing renderer {name}: {file}")
    ticks = doc.get("clip_ticks")
    if type(ticks) is not int or not 32 <= ticks <= 1280 or ticks % 2:
        raise ValueError("The batch has an unsupported clip length.")
    # Working estimate for one next clip, not a full-demo storage guarantee.
    reserve = max(5 * 1024**3, int(ticks / 2 * 3686418 * 2.5) + 1024**3)
    if shutil.disk_usage(path.parent).free < reserve:
        raise ValueError(f"Not enough working space on the batch drive; need about {reserve / 1e9:.1f} GB free.")
    args = [task.python(), "-m", "cs2_data.competitive_batch", "run", "--plan", str(path),
            "--execute", "--max-jobs", "1"]
    for key, value in tools.items():
        args += ["--" + key, str(value)]
    return path, args


def run_next_capture(task, plan):
    path, arguments = capture_arguments(task, plan)
    if task.command("Running the next clip through protected capture and settings restoration", arguments) != 0:
        raise ValueError("Capture command failed. See the log and retained recovery journals.")
    _, _, summary = read_batch_display(path)
    if not summary:
        raise ValueError("The runner did not publish a matching batch summary.")
    failures = {"failed", "retry_required", "recovery_required", "artifact_changed", "evidence_invalid", "revalidation_required"}
    failed = [job for job in summary.get("jobs", []) if job.get("status") in failures]
    if failed:
        raise ValueError("Batch needs attention: " + "; ".join(
            f"{job.get('stage', 'stage')}: {job.get('error') or job['status']}" for job in failed))
    return summary
