"""Small local desktop front end: python -m cs2_data.desktop."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import launcher_backend as backend

AUTO_PLAYER = "Automatic player selection"
TRAINING_PRESET = "640×360 · RGB · 8 bits/channel · 32 FPS · lossless compression"


@contextmanager
def application_lock(path):
    """Release automatically on process exit, including an unclean exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("The demo launcher is already open for this project.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DemoLauncher:
    def __init__(self, root, *, project=backend.PROJECT, auto_scan=True):
        self.root, self.project = root, Path(project).resolve()
        self.settings_path = self.project / "data/launcher/settings.json"
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.close_when_idle = False
        self.started = 0.0
        self.controls = []
        self.demos = {}
        self.batch = None
        self.run_dir = None
        self.issue_count = 0
        self.players = {}
        self.player_selection = ()
        settings = {}
        try:
            settings = backend.read_object(self.settings_path)
        except (OSError, ValueError):
            pass
        if isinstance(settings.get("last_run"), str) and settings["last_run"]:
            self.run_dir = Path(settings["last_run"])
        default_folder = self.project
        try:
            candidate = self.project / backend.read_object(self.project / "configs/parser/esl.json")["input"]
            if candidate.is_dir():
                default_folder = candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.folder = tk.StringVar(value=str(settings.get("demo_folder", default_folder)))
        self.output = tk.StringVar(value=str(settings.get("output_folder", self.project / "data/desktop")))
        self.series = tk.StringVar(value=str(settings.get("series_id", "")))
        self.recursive = tk.BooleanVar(value=settings.get("recursive", True) is True)
        self.clips = tk.StringVar(value=str(settings.get("clips", 4)))
        self.seconds = tk.StringVar(value=str(settings.get("clip_seconds", 10)))
        self.plan_path = tk.StringVar(value=str(settings.get("batch_path", "")))
        self.status = tk.StringVar(value="Ready")
        self.elapsed = tk.StringVar(value="One worker")
        self.disk = tk.StringVar()
        self.selection_text = tk.StringVar(value="Choose a folder, then select the demos to prepare.")
        self.batch_text = tk.StringVar(value="Load an existing batch or plan sample captures from the Demos tab.")
        self.player = tk.StringVar(value=AUTO_PLAYER)
        self.output_preset = tk.StringVar(value=TRAINING_PRESET)
        self.queue_text = tk.StringVar(value="Queue one demo and player at a time, then start automatic processing.")
        self.player_note = tk.StringVar(value="Load players to choose a POV. Missing source data will be prepared first.")
        root.title("Chicken Farmer - Demo Processing")
        root.geometry("1120x880")
        root.minsize(1000, 760)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh_queue()
        self._update_disk()
        self._poll_token = root.after(100, self._drain)
        if self.plan_path.get():
            try:
                self.load_batch(Path(self.plan_path.get()))
            except (OSError, ValueError, KeyError, TypeError):
                self.plan_path.set("")
        if auto_scan:
            root.after(200, self.scan)

    def _button(self, parent, label, command, *, while_busy=False, **pack):
        widget = ttk.Button(parent, text=label, command=command)
        widget.pack(**pack)
        if not while_busy:
            self.controls.append(widget)
        return widget

    def _build(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Subtle.TLabel", foreground="#546477", font=("Segoe UI", 10))
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        main = ttk.Frame(self.root, padding=(22, 16))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        heading = ttk.Frame(main)
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="Chicken Farmer", style="Title.TLabel").pack(side="left")
        ttk.Button(heading, text="Quick guide", command=lambda: self.open_path(
            self.project / "docs/DESKTOP_APP.md")).pack(side="right")
        ttk.Label(main, text="Local demo preparation and competitive POV captures", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 12))

        paths = ttk.LabelFrame(main, text="Folders", padding=10)
        paths.grid(row=2, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate((("Demos", self.folder), ("Output", self.output))):
            ttk.Label(paths, text=label, width=8).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(paths, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
            self.controls.append(entry)
            button = ttk.Button(paths, text="Browse...", command=lambda v=variable: self.browse_folder(v))
            button.grid(row=row, column=2, padx=(4, 0))
            self.controls.append(button)
        ttk.Label(paths, textvariable=self.disk, style="Subtle.TLabel").grid(row=2, column=1, sticky="w", padx=8)
        self.output.trace_add("write", lambda *_: self._output_changed())

        self.tabs = ttk.Notebook(main)
        self.tabs.grid(row=3, column=0, sticky="nsew", pady=(14, 10))
        demos_tab = ttk.Frame(self.tabs, padding=12)
        self.capture_tab = ttk.Frame(self.tabs, padding=12)
        self.queue_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(demos_tab, text="  Demos  ")
        self.tabs.add(self.queue_tab, text="  Demo queue  ")
        self.tabs.add(self.capture_tab, text="  Capture batches  ")
        for tab in (demos_tab, self.capture_tab, self.queue_tab):
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(demos_tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._button(toolbar, "Scan folder", self.scan, side="left")
        recursive = ttk.Checkbutton(toolbar, text="Include subfolders", variable=self.recursive)
        recursive.pack(side="left", padx=12)
        self.controls.append(recursive)
        self._button(toolbar, "Select all", lambda: self.demo_tree.selection_set(self.demo_tree.get_children()), side="right")
        self.demo_tree = self._tree(demos_tab, ("Demo", "Map", "Size", "Preparation"), (415, 110, 95, 235), height=6)
        self.demo_tree.bind("<<TreeviewSelect>>", self._selected)
        self.folder.trace_add("write", lambda *_: self._folder_changed())
        ttk.Label(demos_tab, textvariable=self.selection_text, style="Subtle.TLabel", wraplength=940).grid(row=2, column=0, sticky="w", pady=(7, 10))
        options = ttk.Frame(demos_tab)
        options.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(options, text="Series ID (optional)").pack(side="left")
        series = ttk.Entry(options, textvariable=self.series, width=28)
        series.pack(side="left", padx=(8, 18))
        self.controls.append(series)
        for label, variable, values in (("Sample clips", self.clips, (1, 2, 4, 8)), ("Seconds / sample", self.seconds, (5, 10, 20))):
            ttk.Label(options, text=label).pack(side="left", padx=(0, 6))
            combo = ttk.Combobox(options, textvariable=variable, values=values, state="readonly", width=4)
            combo.pack(side="left", padx=(0, 14))
            self.controls.append(combo)
        player_bar = ttk.Frame(demos_tab)
        player_bar.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        player_bar.columnconfigure(1, weight=1)
        ttk.Label(player_bar, text="Player POV").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.player_combo = ttk.Combobox(player_bar, textvariable=self.player, values=(AUTO_PLAYER,), state="readonly")
        self.player_combo.grid(row=0, column=1, sticky="ew")
        self.player_combo.bind("<<ComboboxSelected>>", self._player_changed)
        self.controls.append(self.player_combo)
        self.load_players_button = ttk.Button(player_bar, text="Load players", command=self.load_players)
        self.load_players_button.grid(row=0, column=2, padx=(8, 0))
        self.controls.append(self.load_players_button)
        ttk.Label(player_bar, textvariable=self.player_note, style="Subtle.TLabel", wraplength=900).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        actions = ttk.Frame(demos_tab)
        actions.grid(row=5, column=0, sticky="ew")
        self._button(actions, "Prepare source data", lambda: self.prepare(False), side="left")
        self._button(actions, "Plan sample captures", lambda: self.prepare(True), side="left", padx=8)
        self._button(actions, "Open source results", self.open_source, side="right")
        full = ttk.Frame(demos_tab)
        full.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        preset = ttk.Combobox(full, textvariable=self.output_preset, values=(TRAINING_PRESET,), state="readonly", width=66)
        preset.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.controls.append(preset)
        self._button(full, "Queue entire demo", self.enqueue_demo, side="right")
        ttk.Label(demos_tab, text="Entire demo: select one demo and a named player. Eight-frame histories; eligible rounds run automatically.",
                  style="Subtle.TLabel", wraplength=900).grid(row=7, column=0, sticky="w", pady=(6, 0))

        ttk.Label(self.queue_tab, text="Entire demo → preprocessing → eligible player rounds → compressed training data",
                  style="Subtle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.queue_tree = self._tree(self.queue_tab, ("Demo", "Player", "Status", "Segments", "Accepted examples"),
                                    (350, 210, 155, 100, 145), height=6)
        ttk.Label(self.queue_tab, textvariable=self.queue_text, style="Subtle.TLabel", wraplength=930).grid(row=2, column=0, sticky="w", pady=8)
        queue_bar = ttk.Frame(self.queue_tab)
        queue_bar.grid(row=3, column=0, sticky="ew")
        self._button(queue_bar, "Start / resume queue", self.process_queue, side="left")
        self._button(queue_bar, "Refresh queue", self.refresh_queue, while_busy=True, side="left", padx=8)
        self._button(queue_bar, "Open coverage report", self.open_queue_report, while_busy=True, side="right")
        self._button(queue_bar, "Open output", lambda: self.open_path(self.queue_path().parent), while_busy=True, side="right", padx=8)
        ttk.Label(self.queue_tab, text="Runs continuously across all eligible rounds. Internal segments last up to two minutes.\n"
                  "Dead time, pauses and setup are excluded and reported. Stop finishes the current segment, compression and cleanup.",
                  style="Subtle.TLabel", wraplength=930).grid(row=4, column=0, sticky="w", pady=(10, 0))

        bar = ttk.Frame(self.capture_tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        entry = ttk.Entry(bar, textvariable=self.plan_path, state="readonly")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(bar, "Load batch...", self.browse_batch, side="left")
        self.batch_tree = self._tree(self.capture_tab, ("Map / source", "Round", "Player Steam ID", "Seconds", "Last recorded status"),
                                     (225, 75, 200, 80, 270), height=6)
        ttk.Label(self.capture_tab, textvariable=self.batch_text, style="Subtle.TLabel", wraplength=940).grid(row=2, column=0, sticky="w", pady=8)
        bar = ttk.Frame(self.capture_tab)
        bar.grid(row=3, column=0, sticky="ew")
        self._button(bar, "Run / resume next clip", self.capture, side="left")
        self._button(bar, "Refresh status", self.refresh_batch, side="left", padx=8)
        self._button(bar, "Optional frames / setup", self.open_review, side="right")
        self._button(bar, "Open batch folder", lambda: self.open_path(self.batch.parent if self.batch else None), side="right", padx=8)
        ttk.Label(self.capture_tab, text="Run launches CS2. Close your normal game first. Each task advances at most one clip.\n"
                  "Timing and settings guards remain active. The approved HUD setup needs no routine visual review.",
                  style="Subtle.TLabel").grid(row=4, column=0, sticky="w", pady=(10, 0))

        bar = ttk.Frame(main)
        bar.grid(row=4, column=0, sticky="ew")
        ttk.Label(bar, text="Activity", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(bar, text="Open last run", command=lambda: self.open_path(self.run_dir)).pack(side="right")
        self.log_widget = ScrolledText(main, height=3, background="#152032", foreground="#dbe7f5",
                                      insertbackground="white", font=("Consolas", 9), wrap="word", relief="flat", padx=10, pady=8)
        self.log_widget.grid(row=5, column=0, sticky="ew", pady=(6, 8))
        self.log_widget.configure(state="disabled")
        footer = ttk.Frame(main)
        footer.grid(row=6, column=0, sticky="ew")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="left", padx=(0, 10))
        ttk.Label(footer, textvariable=self.status, wraplength=580).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.elapsed, style="Subtle.TLabel").pack(side="left", padx=10)
        self.stop_button = ttk.Button(footer, text="Stop after current step", command=self.request_stop, state="disabled")
        self.stop_button.pack(side="right")

    def _tree(self, parent, columns, widths, height):
        holder = ttk.Frame(parent)
        holder.grid(row=1, column=0, sticky="nsew")
        tree = ttk.Treeview(holder, columns=columns, show="headings", height=height)
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=60, stretch=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        return tree

    def _update_disk(self):
        try:
            path = Path(self.output.get()).expanduser().resolve()
            while not path.exists() and path != path.parent:
                path = path.parent
            self.disk.set(f"{shutil.disk_usage(path).free / 1e9:,.1f} GB free on output drive  |  New runs keep existing outputs intact")
        except (OSError, ValueError):
            self.disk.set("Choose an output folder to check available space.")

    def _output_changed(self):
        self._update_disk()
        self._reset_players()
        if hasattr(self, "queue_tree"):
            self.refresh_queue()

    def _reset_players(self):
        self.players.clear()
        self.player.set(AUTO_PLAYER)
        self.player_combo.configure(values=(AUTO_PLAYER,))
        self.player_note.set("Load players to choose a POV. Missing source data will be prepared first.")

    def _player_changed(self, _event=None):
        steam_id = self.players.get(self.player.get())
        self.player_note.set(f"Captures and full-demo processing will use only Steam ID {steam_id}."
                             if steam_id else "The planner will choose players across the selected demos.")

    def _save(self):
        backend.save_settings(self.settings_path, {"demo_folder": self.folder.get(), "output_folder": self.output.get(),
            "recursive": self.recursive.get(), "series_id": self.series.get(), "clips": self.clips.get(),
            "clip_seconds": self.seconds.get(), "batch_path": self.plan_path.get(),
            "last_run": str(self.run_dir) if self.run_dir else ""})

    def queue_path(self):
        return Path(self.output.get()).expanduser().resolve()/"full-demo-queue/queue.json"

    def enqueue_demo(self):
        if self.busy:
            return
        selected = [self.demos[key].path for key in self.demo_tree.selection()]
        steam_id = self.players.get(self.player.get())
        if len(selected) != 1 or steam_id is None or tuple(str(p) for p in selected) != self.player_selection:
            self.error("Select exactly one demo, load its players, and choose a named player first.")
            return
        if self.output_preset.get() != TRAINING_PRESET or not self.output.get().strip():
            self.error("Choose the 640×360 RGB output preset and an output folder.")
            return
        try:
            from .full_demo import enqueue
            enqueue(self.queue_path(), selected[0], steam_id, self.player.get().split("  |  ")[0], match_id=self.series.get().strip())
            self._save(); self.refresh_queue(); self.tabs.select(self.queue_tab)
            self.status.set("Entire demo queued - ready to start")
        except (OSError, ValueError) as error:
            self.error(str(error))

    def refresh_queue(self):
        try:
            from .full_demo import load_queue
            doc = load_queue(self.queue_path())
            selected = self.queue_tree.selection()
            self.queue_tree.delete(*self.queue_tree.get_children())
            for job in doc["jobs"]:
                self.queue_tree.insert("", "end", iid=job["id"], values=(Path(job["demo"]).name, job["player_name"],
                    job["status"].replace("_", " "), f"{job.get('completed_segments',0)}/{job.get('segment_count',0) or '—'}",
                    f"{job.get('accepted_samples',0):,}"))
            self.queue_tree.selection_set([key for key in selected if self.queue_tree.exists(key)])
            self.queue_text.set(f"{len(doc['jobs'])} demos · 640×360 RGB8 · 32 FPS · lossless training shards and evidence archives. "
                                "Completed segments resume without recapture.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.queue_text.set("Queue needs attention: "+str(error))

    def process_queue(self):
        try:
            from .full_demo import load_queue, run_queue
            path = self.queue_path()
            doc = load_queue(path)
            if not any(job["status"] not in ("complete", "cancelled") for job in doc["jobs"]):
                self.error("Queue a demo and player first. Completed demos are already retained.")
                return
            self._start("full-demo", lambda task: run_queue(task, path))
        except (OSError, ValueError) as error:
            self.error(str(error))

    def open_queue_report(self):
        selected = self.queue_tree.selection()
        if len(selected) != 1:
            self.error("Select one queued demo to open its coverage report.")
            return
        try:
            from .full_demo import load_queue
            job = next(row for row in load_queue(self.queue_path())["jobs"] if row["id"] == selected[0])
            self.open_path(Path(job["report"]) if job.get("report") else None)
        except (OSError, ValueError, KeyError, StopIteration) as error:
            self.error(str(error))

    def browse_folder(self, variable):
        selected = filedialog.askdirectory(parent=self.root, initialdir=variable.get() or str(self.project))
        if selected:
            variable.set(selected)
            if variable is self.folder:
                self.scan()

    def _folder_changed(self):
        if not self.busy:
            self.demo_tree.delete(*self.demo_tree.get_children())
            self.demos.clear()
            self.player_selection = ()
            self._reset_players()
            self.selection_text.set("Folder changed. Scan it to select demos from this location.")

    def _emit(self, kind, value):
        if kind != "log" or self.events.qsize() < 500:
            self.events.put((kind, value))

    def _start(self, kind, function, *, tracked=True):
        if self.busy:
            return
        if not self.output.get().strip():
            self.error("Choose an output folder.")
            return
        try:
            self._save()
        except OSError as error:
            self.error(str(error))
            return
        output = Path(self.output.get())
        self.busy = True
        self.stop.clear()
        self.issue_count = 0
        self.started = time.monotonic()
        self.status.set(kind.capitalize() + "...")
        for widget in self.controls:
            widget.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.progress.start(12)

        def work():
            task = None
            try:
                task = backend.TaskRunner(output, kind, self._emit, self.stop, project=self.project) if tracked else None
                result = function(task)
                if task:
                    task.finish("finished", result=result)
                self._emit("finished", {"status": "finished", "kind": kind, "result": result})
            except Exception as error:
                status = "stopped" if isinstance(error, backend.StopRequested) else "failed"
                if task:
                    try:
                        task.log(traceback.format_exc() if status == "failed" else str(error))
                        task.finish(status, error=str(error))
                    except OSError:
                        pass
                self._emit("finished", {"status": status, "kind": kind, "error": str(error)})

        threading.Thread(target=work, name="demo-launcher-worker", daemon=False).start()

    def scan(self):
        folder, output, recursive = Path(self.folder.get()), Path(self.output.get()), self.recursive.get()

        def work(_):
            found = backend.scan_demos(folder, output, recursive=recursive, project=self.project)
            self._emit("demos", found)
            return {"demo_count": len(found)}

        self._start("scan", work, tracked=False)

    def _selected(self, _event=None):
        selected = [key for key in self.demo_tree.selection() if key in self.demos]
        identity = tuple(str(self.demos[key].path) for key in selected)
        if identity != self.player_selection:
            self.player_selection = identity
            self._reset_players()
        if len(selected) == 1:
            self.selection_text.set(str(self.demos[selected[0]].path))
        else:
            self.selection_text.set(f"{len(selected)} selected / {len(self.demos)} demos. Prepare up to eight at a time.")

    def load_players(self):
        selected = [self.demos[key].path for key in self.demo_tree.selection()]
        if not 1 <= len(selected) <= 8:
            self.error("Select one to eight demos first.")
            return
        series = self.series.get().strip()

        def work(task):
            return {"players": backend.load_players(task, selected, series), "demos": [str(path) for path in selected]}

        self._start("players", work)

    def prepare(self, plan):
        selected = [self.demos[key].path for key in self.demo_tree.selection()]
        if not 1 <= len(selected) <= 8:
            self.error("Select one to eight demos first.")
            return
        try:
            clips, seconds = int(self.clips.get()), int(self.seconds.get())
        except ValueError:
            self.error("Choose a clip count and duration from the lists.")
            return
        series = self.series.get().strip()
        steam_id = None
        if plan and self.player.get() != AUTO_PLAYER:
            steam_id = self.players.get(self.player.get())
            if steam_id is None or tuple(str(path) for path in selected) != self.player_selection:
                self.error("Load players for the selected demos before choosing a POV.")
                return

        def work(task):
            if plan:
                path = backend.plan_captures(task, selected, match_id=series, clips=clips, clip_seconds=seconds,
                                             steam_id=steam_id)
                return {"batch_plan": str(path)}
            return {"sources": str(backend.prepare_sources(task, selected, series))}

        self._start("plan" if plan else "prepare", work)

    def browse_batch(self):
        selected = filedialog.askopenfilename(parent=self.root, title="Select batch_plan.json",
            initialdir=str(self.project / "data"), filetypes=[("Batch plan", "batch_plan.json"), ("JSON", "*.json")])
        if selected:
            try:
                self.load_batch(Path(selected))
                self._save()
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.error(str(error))

    def load_batch(self, path):
        path, plan, summary = backend.read_batch_display(path)
        statuses = {row["job_id"]: row for row in summary.get("jobs", [])}
        rows = []
        for item in plan["jobs"]:
            job = item["job"]
            seconds = (job["end_demo_tick"] - job["start_demo_tick"]) / 64
            recorded = statuses.get(item["job_id"], {})
            status = recorded.get("display_status", recorded.get("status", "planned"))
            label = {"pending_visual_review": "historical visual review pending",
                     "hud_setup_trusted": "HUD setup trusted"}.get(status, status.replace("_", " "))
            rows.append((item["job_id"], (job.get("map", item["source_id"]), job["round_id"],
                str(job["steam_id"]), f"{seconds:g}", label)))
        selected = self.batch_tree.selection() if self.batch == path else ()
        self.batch_tree.delete(*self.batch_tree.get_children())
        for key, values in rows:
            self.batch_tree.insert("", "end", iid=key, values=values)
        self.batch = path
        self.plan_path.set(str(path))
        self.batch_tree.selection_set([key for key in selected if self.batch_tree.exists(key)])
        self.batch_text.set(f"{len(rows)} clips  |  Last reported accepted samples: {summary.get('accepted_sample_count', 0):,}. "
                            "Running checks timing and sample eligibility; the approved HUD setup is trusted.")

    def refresh_batch(self):
        try:
            if self.batch:
                self.load_batch(self.batch)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(str(error))

    def capture(self):
        if self.batch is None:
            self.error("Plan sample captures or load an existing batch first.")
            return
        plan = self.batch
        self._start("capture", lambda task: {"batch_plan": str(plan), "summary": backend.run_next_capture(task, plan)})

    def open_source(self):
        selected = self.demo_tree.selection()
        if len(selected) != 1:
            self.error("Select one demo to open its results.")
            return
        self.open_path(self.demos[selected[0]].parsed)

    def open_review(self):
        if not self.batch:
            self.error("Load a capture batch first.")
            return
        selected = self.batch_tree.selection()
        if len(selected) != 1:
            self.error("Select one clip to open its optional frames or setup details.")
            return
        try:
            path, plan, _ = backend.read_batch_display(self.batch)
            state = backend.read_object(path.parent / "batch_state.json")
            if (state.get("schema_version") != 1 or state.get("profile") != plan["profile"]
                    or state.get("plan_sha256") != backend.hash_file(path)
                    or not isinstance(state.get("jobs"), dict)
                    or set(state["jobs"]) != {item["job_id"] for item in plan["jobs"]}):
                raise ValueError("The capture journal does not match this batch. Refresh or resume the batch first.")
            attempts = state["jobs"][selected[0]]["stages"]["hud_review"]
            if not isinstance(attempts, list) or not attempts:
                raise ValueError("This clip has no optional frames or setup details yet. Capture and process it first.")
            latest = attempts[-1]
            if not isinstance(latest, dict) or latest.get("status") != "completed":
                raise ValueError("The latest review step is incomplete. Resume the batch or inspect its activity log first.")
            expected = (path.parent / "runs" / selected[0] / "hud_review" / f"attempt-{len(attempts):03d}").resolve()
            directory = Path(latest["out"])
            if not directory.is_absolute():
                directory = path.parent / directory
            directory = directory.resolve()
            if (latest.get("attempt") != len(attempts) or directory != expected
                    or not directory.is_relative_to(path.parent)):
                raise ValueError("Invalid review directory in the capture journal.")
            receipt = directory / "hud_policy.json"
            index = directory / "index.html"
            if receipt.is_file() and latest.get("result", {}).get("status") == "hud_setup_trusted":
                recorded = latest.get("files", {})
                if not isinstance(recorded, dict) or recorded.get(str(receipt)) != backend.hash_file(receipt):
                    raise ValueError("The setup record changed since the capture journal was saved.")
                self.open_path(receipt)
            elif index.is_file():
                recorded = latest.get("files", {})
                if not isinstance(recorded, dict) or recorded.get(str(index)) != backend.hash_file(index):
                    raise ValueError("The review page changed since the capture journal was saved. Resume the batch to check its evidence.")
                self.open_path(index)
            elif (directory / "hud_review_bundle.json").is_file():
                # Historical review bundles predate the HTML index.
                self.open_path(directory)
            else:
                raise ValueError("The recorded review files are unavailable. Inspect the batch folder and activity log.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(str(error))

    def open_path(self, path):
        if path is None or not Path(path).exists():
            self.error("No saved results are available here yet.")
            return
        try:
            os.startfile(str(Path(path).resolve()))
        except (OSError, AttributeError) as error:
            self.error(str(error))

    def error(self, message):
        messagebox.showerror("Demo processing", message, parent=self.root)

    def _log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text + "\n")
        if int(self.log_widget.index("end-1c").split(".")[0]) > 1200:
            self.log_widget.delete("1.0", "300.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _drain(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(value)
            elif kind == "status":
                if not self.stop.is_set():
                    self.status.set(value)
            elif kind == "queue":
                self.refresh_queue()
            elif kind == "run":
                self.run_dir = Path(value)
                self._log("Run folder: " + value)
                try:
                    self._save()
                except OSError as error:
                    self._log(f"Could not remember the last run: {error}")
            elif kind == "demos":
                self.demo_tree.delete(*self.demo_tree.get_children())
                self.demos = {str(i): demo for i, demo in enumerate(value)}
                for key, demo in self.demos.items():
                    self.demo_tree.insert("", "end", iid=key, values=(demo.path.name, demo.map_name or "-",
                        f"{demo.size / 1e6:,.1f} MB", demo.recorded_status))
                self._selected()
            elif kind == "prepared":
                if not value["quality_passed"]:
                    self.issue_count += 1
                for key, demo in self.demos.items():
                    if str(demo.path) == value["demo"]:
                        status = "Prepared; quality issues" if not value["quality_passed"] else "Source prepared"
                        try:
                            doc = backend.read_object(Path(value["parsed"]) / "manifest.json")
                        except (OSError, ValueError) as error:
                            self._log(f"Could not display prepared source: {error}")
                            continue
                        self.demos[key] = backend.DemoFile(demo.path, demo.size, doc.get("map", ""), status, Path(value["parsed"]))
                        self.demo_tree.item(key, values=(demo.path.name, doc.get("map", "-"), f"{demo.size / 1e6:,.1f} MB", status))
            elif kind == "finished":
                self._finished(value)
                if self.close_when_idle:
                    self.root.destroy()
                    return
        if self.busy:
            seconds = int(time.monotonic() - self.started)
            self.elapsed.set(f"{seconds // 60:02d}:{seconds % 60:02d}")
        self._poll_token = self.root.after(100, self._drain)

    def _finished(self, value):
        self.busy = False
        self.progress.stop()
        self.progress["value"] = 0
        self.stop_button.state(["disabled"])
        for widget in self.controls:
            widget.state(["!disabled"])
        self._update_disk()
        result = value.get("result", {})
        if value["status"] != "finished":
            self.status.set("Stopped" if value["status"] == "stopped" else "Needs attention - see activity log")
            self._log(value.get("error", "Task did not complete."))
            if value["kind"] == "capture":
                self.refresh_batch()
            if value["kind"] == "full-demo":
                self.refresh_queue()
            return
        if result.get("batch_plan"):
            try:
                self.load_batch(Path(result["batch_plan"]))
                self.tabs.select(self.capture_tab)
                self._save()
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.status.set("Needs attention - could not load the batch")
                self._log(str(error))
                return
        if value["kind"] == "full-demo":
            self.refresh_queue()
            status = result.get("status")
            self.status.set("Queue stopped after current segment" if self.stop.is_set() or status == "stopped" else
                            "Queue paused: low disk space - free space, then resume" if status == "paused_low_disk" else
                            "Queue complete - open the coverage report" if status == "complete" else
                            "Queue run finished - see demo statuses and coverage reports")
        elif value["kind"] == "capture":
            rows = result.get("summary", {}).get("jobs", [])
            ready = any(row.get("display_status", row.get("status")) == "ready_for_acceptance" for row in rows)
            self.status.set("Capture recorded - ready for acceptance" if ready else "Capture task finished - see recorded batch status")
        elif value["kind"] == "scan":
            self.status.set(f"Found {result['demo_count']} demos")
        elif value["kind"] == "plan":
            self.status.set("Batch planned - ready to inspect in Capture batches")
        elif value["kind"] == "players":
            current = tuple(str(self.demos[key].path) for key in self.demo_tree.selection())
            if tuple(result["demos"]) == current:
                self.player_selection = current
                self.players = {f"{row['name'][:70]}  |  {row['steam_id']}  |  {row['demo_count']}/{len(current)} demos":
                                row["steam_id"] for row in result["players"]}
                previous = self.player.get()
                self.player_combo.configure(values=(AUTO_PLAYER, *self.players))
                self.player.set(previous if previous in self.players else AUTO_PLAYER)
                self.player_note.set("Choose a player above, then queue the entire demo or plan sample captures.")
                self.status.set(f"Loaded {len(self.players)} players - choose a POV")
            else:
                self._reset_players()
                self.status.set("Demo selection changed - load players for the current selection")
        else:
            self.status.set("Sources prepared; quality issues retained" if self.issue_count else "Source preparation finished")
        self._log(self.status.get())

    def request_stop(self):
        if self.busy:
            self.stop.set()
            self.stop_button.state(["disabled"])
            self.status.set("Finishing current step before stopping; keep the app open")

    def close(self):
        if self.busy:
            self.close_when_idle = True
            self.request_stop()
            self.status.set("Closing after the current step and renderer cleanup attempt finish")
        else:
            try:
                self._save()
            except OSError:
                pass
            self.root.destroy()


def main():
    root = None
    try:
        with application_lock(backend.PROJECT / "data/launcher/application.lock"):
            root = tk.Tk()
            DemoLauncher(root)
            root.mainloop()
        return 0
    except Exception as error:
        path = backend.PROJECT / "data/launcher/startup-error.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(traceback.format_exc().encode("utf-8"))
        except OSError:
            pass
        if root is None:
            root = tk.Tk()
            root.withdraw()
        messagebox.showerror("Demo launcher", f"{error}\n\nDetails: {path}", parent=root)
        root.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
