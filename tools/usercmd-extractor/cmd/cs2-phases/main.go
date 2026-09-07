// cs2-phases produces independent match-phase evidence without reconstructing user commands.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"chicken-farmer/tools/usercmd-extractor/internal/demo"
	dem "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
)

func main() {
	input := flag.String("input", "", "source .dem file")
	out := flag.String("out", "", "new phase evidence JSON file; existing files are never replaced")
	flag.Parse()
	if err := run(*input, *out); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(input, out string) error {
	if input == "" || out == "" || flag.NArg() != 0 {
		return fmt.Errorf("--input and --out are required with no positional arguments")
	}
	if _, err := os.Stat(out); !os.IsNotExist(err) {
		return fmt.Errorf("output must be new: %s", out)
	}
	id, err := demo.HashFile(input)
	if err != nil {
		return err
	}
	f, err := os.Open(input)
	if err != nil {
		return err
	}
	defer f.Close()
	config := dem.DefaultParserConfig
	config.UserCmdParsing = dem.UserCmdParsingDisabled
	config.MsgQueueBufferSize = 0
	p := dem.NewParserWithConfig(f, config)
	defer p.Close()
	roundID := 0
	records := []map[string]any{}
	mapName := ""
	record := func(kind string, detail any) {
		gs := p.GameState()
		r := map[string]any{"kind": kind, "demo_tick": gs.IngameTick(), "round_id": roundID,
			"total_rounds_played": gs.TotalRoundsPlayed(), "is_match_started": gs.IsMatchStarted(),
			"is_warmup": gs.IsWarmupPeriod(), "game_phase": int(gs.GamePhase()),
			"score_ct": gs.TeamCounterTerrorists().Score(), "score_t": gs.TeamTerrorists().Score()}
		if detail != nil {
			r["detail"] = detail
		}
		records = append(records, r)
	}
	p.RegisterNetMessageHandler(func(h *msg.CDemoFileHeader) { mapName = h.GetMapName() })
	p.RegisterEventHandler(func(e events.RoundStart) { roundID++; record("round_start", e) })
	p.RegisterEventHandler(func(e events.RoundFreezetimeEnd) { record("freeze_end", nil) })
	p.RegisterEventHandler(func(e events.RoundEnd) {
		record("round_end", map[string]any{"reason": int(e.Reason), "winner": int(e.Winner), "message": e.Message})
	})
	p.RegisterEventHandler(func(e events.RoundEndOfficial) { record("round_end_official", nil) })
	p.RegisterEventHandler(func(e events.MatchStart) { record("match_start", nil) })
	p.RegisterEventHandler(func(e events.AnnouncementMatchStarted) { record("announcement_match_started", nil) })
	p.RegisterEventHandler(func(e events.AnnouncementWinPanelMatch) { record("win_panel_match", nil) })
	p.RegisterEventHandler(func(e events.MatchStartedChanged) { record("match_started_changed", e) })
	p.RegisterEventHandler(func(e events.IsWarmupPeriodChanged) { record("warmup_changed", e) })
	p.RegisterEventHandler(func(e events.GamePhaseChanged) { record("game_phase_changed", e) })
	p.RegisterEventHandler(func(e events.ScoreUpdated) {
		record("score_updated", map[string]int{"old": e.OldScore, "new": e.NewScore})
	})
	p.RegisterEventHandler(func(e events.ChatMessage) { record("chat", e.Text) })
	p.RegisterEventHandler(func(e events.GenericGameEvent) {
		if strings.Contains(e.Name, "restart") || e.Name == "round_announce_warmup" || e.Name == "begin_new_match" {
			detail := map[string]any{}
			for key, value := range e.Data {
				detail[key] = value
			}
			record("raw_"+e.Name, detail)
		}
	})
	if err = p.ParseToEnd(); err != nil {
		return err
	}
	record("demo_end", nil)
	afterHash, err := demo.HashFile(input)
	if err != nil {
		return err
	}
	if afterHash != id {
		return fmt.Errorf("source .dem changed during phase extraction")
	}
	absolute, err := filepath.Abs(input)
	if err != nil {
		return err
	}
	result := map[string]any{"schema_version": 1, "demo_id": id, "source_demo_sha256": id,
		"source_demo_path": absolute, "parse_status": "complete", "partial": false,
		"parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0",
		"producer": "cs2-phases-v1", "timing_clock": "demo_tick", "map": mapName, "events": records}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(out), 0755); err != nil {
		return err
	}
	output, err := os.CreateTemp(filepath.Dir(out), ".cs2-phases-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(output.Name())
	_, err = output.Write(append(data, '\n'))
	closeErr := output.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	// Link publishes only a fully written file and fails if another writer won.
	// Temp and destination share a filesystem; no existing artifact is replaced.
	return os.Link(output.Name(), out)
}
