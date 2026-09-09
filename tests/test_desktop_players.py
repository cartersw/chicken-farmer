"""Opt-in Tk player selection workflow tests with fixture processing callbacks."""
import os
import threading

import pytest

if os.environ.get("CS2_TEST_GUI") != "1":
    pytest.skip("Set CS2_TEST_GUI=1 for a desktop session", allow_module_level=True)

from cs2_data import desktop
from cs2_data import launcher_backend as backend
from test_desktop_ui import ui, tk_root, pump, populate, make_plan


def load_roster(ui, monkeypatch):
    demo = populate(ui)
    monkeypatch.setattr(backend, "load_players", lambda *args: [
        {"steam_id": "76561198323592528", "name": "Ckanic", "demo_count": 1},
        {"steam_id": "76561198323592529", "name": "Ckanic", "demo_count": 1},
    ])
    ui.load_players()
    pump(ui.root, lambda: not ui.busy)
    return demo


def test_load_choose_and_plan_exact_player(ui, monkeypatch):
    demo = load_roster(ui, monkeypatch)
    assert len(ui.players) == 2 and ui.player.get() == desktop.AUTO_PLAYER
    ui.player.set(next(label for label, sid in ui.players.items() if sid == "76561198323592528"))
    ui._player_changed()
    assert "76561198323592528" in ui.player_note.get()
    calls = []
    path = make_plan(ui)
    monkeypatch.setattr(backend, "plan_captures", lambda task, demos, **kw: calls.append((demos, kw)) or path)
    ui.prepare(True)
    pump(ui.root, lambda: not ui.busy)
    assert calls[0][0] == [demo.resolve()]
    assert calls[0][1]["steam_id"] == "76561198323592528"
    assert not ui.test_errors


def test_demo_or_output_change_clears_player_scope(ui, monkeypatch):
    load_roster(ui, monkeypatch)
    ui.player.set(next(iter(ui.players)))
    ui.demo_tree.selection_remove("0")
    ui.root.update()
    assert not ui.players and ui.player.get() == desktop.AUTO_PLAYER
    ui.demo_tree.selection_set("0")
    ui.root.update()
    ui.load_players()
    pump(ui.root, lambda: not ui.busy)
    assert ui.players
    ui.output.set(str(ui.project / "another-output"))
    assert not ui.players and ui.player.get() == desktop.AUTO_PLAYER


def test_slow_roster_does_not_apply_to_changed_selection(ui, monkeypatch):
    populate(ui)
    entered, release = threading.Event(), threading.Event()

    def load(*args):
        entered.set()
        assert release.wait(5)
        return [{"steam_id": "123", "name": "Fixture", "demo_count": 1}]

    monkeypatch.setattr(backend, "load_players", load)
    ui.load_players()
    assert entered.wait(2)
    assert ui.player_combo.instate(["disabled"])
    ui.demo_tree.selection_remove("0")
    ui.root.update()
    release.set()
    pump(ui.root, lambda: not ui.busy)
    assert not ui.players and "selection changed" in ui.status.get()
    assert ui.player_combo.instate(["readonly", "!disabled"])


def test_invalid_player_cannot_silently_plan_automatic(ui, monkeypatch):
    populate(ui)
    ui.player.set("A stale player")
    monkeypatch.setattr(backend, "plan_captures", lambda *args, **kw: pytest.fail("Invalid scope must not plan"))
    ui.prepare(True)
    assert not ui.busy and ui.test_errors


def test_player_controls_fit_minimum_window(ui):
    ui.root.geometry("1000x760")
    ui.root.deiconify()
    ui.root.update()
    for widget in (ui.player_combo, ui.load_players_button, ui.stop_button, ui.demo_tree):
        assert widget.winfo_ismapped() and widget.winfo_height() >= 20
        assert widget.winfo_rooty() + widget.winfo_height() <= ui.root.winfo_rooty() + ui.root.winfo_height()
    ui.root.withdraw()


def test_unique_player_names_stay_short_and_keep_exact_id(ui, monkeypatch):
    populate(ui)
    monkeypatch.setattr(backend, "load_players", lambda *args: [
        {"steam_id": "76561198323592528", "name": "Ckanic", "demo_count": 1},
    ])
    ui.load_players()
    pump(ui.root, lambda: not ui.busy)
    assert ui.players == {"Ckanic": "76561198323592528"}
    ui.player.set("Ckanic")
    assert ui.enqueue_button.instate(["!disabled"])
