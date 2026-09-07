package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func validChickenJob() chickenJob {
	userID := 4
	return chickenJob{SchemaVersion: 1, DemoID: strings.Repeat("a", 64), DemoPath: "match.dem", ClipID: "abc123", RoundID: 1, SteamID: "76561198000000000", PlayerSlot: 3, SpectatorUserID: &userID, StartDemoTick: 1000, EndDemoTick: 1100, FPS: 32, Width: 640, Height: 360, Clock: "demo_tick"}
}

func TestChickenJobRejectsAmbiguousOrUnsafeRequests(t *testing.T) {
	if err := validChickenJob().validate(); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*chickenJob){
		"wrong clock":               func(j *chickenJob) { j.Clock = "server_tick_executed" },
		"unavailable spectator":     func(j *chickenJob) { j.SpectatorUserID = nil },
		"encoder splits underscore": func(j *chickenJob) { j.ClipID = "bad_clip" },
		"console injection":         func(j *chickenJob) { j.ClipID = "clip;quit" },
		"path traversal":            func(j *chickenJob) { j.ClipID = "../clip" },
		"silent start clamp":        func(j *chickenJob) { j.StartDemoTick = 70 },
		"empty interval":            func(j *chickenJob) { j.EndDemoTick = j.StartDemoTick },
		"zero identity":             func(j *chickenJob) { j.SteamID = "0" },
	} {
		t.Run(name, func(t *testing.T) {
			j := validChickenJob()
			mutate(&j)
			if j.validate() == nil {
				t.Fatal("invalid render request was accepted")
			}
		})
	}
}

func TestRuntimeSequenceHasRequestedBoundariesAndVisuals(t *testing.T) {
	dir := t.TempDir()
	j := validChickenJob()
	interval := playerRoundInfo{UUID: j.ClipID, UserId: *j.SpectatorUserID, SpawnTick: j.StartDemoTick, DeathTick: j.EndDemoTick + 1}
	path, err := createActionsJSONForIntervals(filepath.Join(dir, "test.dem"), []playerRoundInfo{interval}, dir, j.FPS)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var seqs []Sequence
	if err = json.Unmarshal(data, &seqs); err != nil {
		t.Fatal(err)
	}
	if len(seqs) != 2 {
		t.Fatalf("expected warmup + one player interval, got %d", len(seqs))
	}
	commands := map[string]int{}
	for _, a := range seqs[1].Actions {
		commands[a.Cmd] = a.Tick
	}
	for cmd, tick := range map[string]int{"startmovie abc123_": 1000, "endmovie": 1100, "spec_player 5": 998, "cl_drawhud 1": 64, "r_drawviewmodel 1": 64, "spec_show_xray 0": 64, "host_framerate 32": 64} {
		got, ok := commands[cmd]
		if !ok || got != tick {
			t.Errorf("command %q: got tick %d present=%v, want %d", cmd, got, ok, tick)
		}
	}
}

func TestEncoderCleanupCanRunAfterExplicitStop(t *testing.T) {
	manager := NewVideoEncoderManager("", "", "", 32, 640, 360, nil)
	manager.Stop()
	manager.Stop()
}
