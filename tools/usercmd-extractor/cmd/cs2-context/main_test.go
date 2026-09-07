package main

import (
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	"os"
	"path/filepath"
	"testing"
)

func TestPauseRequiresAllObservedFlags(t *testing.T) {
	yes, no := true, false
	flags := map[string]*bool{}
	for _, name := range pauseNames {
		flags[name] = &no
	}
	if got := pauseState(flags); got == nil || *got {
		t.Fatal("observed unpaused state lost")
	}
	flags["m_bGamePaused"] = &yes
	if got := pauseState(flags); got == nil || !*got {
		t.Fatal("global pause was ignored")
	}
	flags["m_bTechnicalTimeOut"] = nil
	if pauseState(flags) != nil {
		t.Fatal("missing flag manufactured complete evidence")
	}
}

func TestAliasedShotsAndObservationTickMismatchCannotProveTiming(t *testing.T) {
	rows := []shot{
		{Tick: 10, ObservedTick: 10, SteamID: "1", Weapon: "Glock"},
		{Tick: 10, ObservedTick: 10, SteamID: "1", Weapon: "Glock"},
		{Tick: 11, ObservedTick: 12, SteamID: "1", Weapon: "Glock"},
		{Tick: 13, ObservedTick: 13, SteamID: "1", Weapon: "Glock"},
		{Tick: 14, ObservedTick: 14, SteamID: "1", Weapon: "Glock", Ambiguous: true},
	}
	markAmbiguousShots(rows)
	for i, expected := range []bool{true, true, true, false, true} {
		if rows[i].Ambiguous != expected {
			t.Fatalf("shot %d ambiguity incorrect", i)
		}
	}
}

func TestLossBearingAndUnknownWarningsCannotPublishTrustedContext(t *testing.T) {
	for _, kind := range []events.WarnType{events.WarnTypeStringTableParsingFailure, events.WarnTypePacketEntitiesPanic, events.WarnTypeGameEventBeforeDescriptors, events.WarnTypeUnknownProtobufMessage, events.WarnTypeUndefined, events.WarnType(999)} {
		if contextWarningAllowed(kind) {
			t.Fatalf("loss-bearing warning %d allowed", kind)
		}
	}
	if !contextWarningAllowed(events.WarnTypeBombsiteUnknown) {
		t.Fatal("unrelated bombsite geometry rejected")
	}
}

func TestSegmentsRetainGapsAndRoundBoundaries(t *testing.T) {
	rows := appendSegment(nil, 10, state{RoundID: 3})
	rows = appendSegment(rows, 11, state{RoundID: 3})
	rows = appendSegment(rows, 14, state{RoundID: 3})
	rows = appendSegment(rows, 15, state{RoundID: 4})
	if len(rows) != 3 || rows[0].Start != 10 || rows[0].End != 12 || rows[1].Start != 14 || rows[2].RoundID != 4 {
		t.Fatalf("state gaps or boundaries were lost: %+v", rows)
	}
}

func TestBadDemoDoesNotPublishAndExistingEvidenceSurvives(t *testing.T) {
	dir := t.TempDir()
	input, output := filepath.Join(dir, "bad.dem"), filepath.Join(dir, "context.json")
	if err := os.WriteFile(input, []byte("not a demo"), 0600); err != nil {
		t.Fatal(err)
	}
	if run(input, output) == nil {
		t.Fatal("corrupt demo accepted")
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatal("failed parse published evidence")
	}
	if err := os.WriteFile(output, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	if run(input, output) == nil {
		t.Fatal("existing evidence accepted for overwrite")
	}
	data, err := os.ReadFile(output)
	if err != nil || string(data) != "original" {
		t.Fatal("existing evidence changed")
	}
}
