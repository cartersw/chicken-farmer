"""Player names are display metadata; the exact selected ID reaches the planner."""
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cs2_data import launcher_backend as app
from test_launcher_backend import project, source, task, write


def roster_sources(runner, monkeypatch, tables):
    sources = []
    for index, rows in enumerate(tables):
        parsed = runner.run_dir / f"parsed-{index}"
        parsed.mkdir()
        pq.write_table(pa.Table.from_pylist(rows), parsed / "player_state.parquet")
        sources.append({"parsed": str(parsed), "demo": f"demo-{index}.dem"})
    path = runner.run_dir / "sources.json"
    write(path, {"sources": sources})
    monkeypatch.setattr(app, "prepare_sources", lambda *args: path)


def test_roster_keeps_distinct_ids_aliases_and_demo_counts(project, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    row = lambda sid, name, team=2: {"steam_id": sid, "player_name": name, "team": team}
    roster_sources(runner, monkeypatch, [
        [row(10, "Same name"), row(10, "Same name"), row(20, "Same name"), row(0, "Bot"), row(30, "Observer", 1)],
        [row(10, "Renamed\nplayer", 3), row(20, "Same name", 3)],
    ])
    players = app.load_players(runner, [])
    assert {p["steam_id"] for p in players} == {"10", "20"}
    assert all(p["demo_count"] == 2 for p in players)
    assert next(p for p in players if p["steam_id"] == "10")["name"] == "Renamed player / Same name"
    assert app.read_object(runner.run_dir / "players.json")["training_ready"] is False


def test_roster_requires_playing_team_and_honors_stop(project, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    roster_sources(runner, monkeypatch, [[{"steam_id": 10, "player_name": "Spectator", "team": 1}]])
    with pytest.raises(ValueError, match="No players"):
        app.load_players(runner, [])
    runner.stop.set()
    with pytest.raises(app.StopRequested):
        app.load_players(runner, [])
    assert not (runner.run_dir / "players.json").exists()


def test_player_plan_forwards_only_the_requested_id(project, source, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    commands = []

    def command(label, arguments):
        commands.append(arguments)
        write(Path(arguments[arguments.index("--out") + 1]) / "batch/batch_plan.json", {"fixture": True})
        return 0

    monkeypatch.setattr(runner, "command", command)
    assert app.plan_captures(runner, [source.demo], steam_id="76561198323592528").is_file()
    assert commands[0][commands[0].index("--steam-id") + 1] == "76561198323592528"
    assert "--execute" not in commands[0]


def test_invalid_player_fails_before_preparing_sources(project, tmp_path, monkeypatch):
    runner = task(project, tmp_path)
    monkeypatch.setattr(app, "prepare_sources", lambda *args: pytest.fail("Invalid player must not start preparation"))
    with pytest.raises(ValueError, match="Steam ID"):
        app.plan_captures(runner, [], steam_id="0")
