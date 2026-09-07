// cs2-context audits observed game-rule state and network action clocks without
// changing canonical extraction outputs or guessing unavailable properties.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strconv"

	"chicken-farmer/tools/usercmd-extractor/internal/demo"
	dem "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	st "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/sendtables"
)

var pauseNames = []string{"m_bGamePaused", "m_bMatchWaitingForResume", "m_bTerroristTimeOutActive", "m_bCTTimeOutActive", "m_bTechnicalTimeOut"}

const rulePrefix = "m_pGameRules."

type state struct {
	RoundID        int              `json:"round_id"`
	Paused         *bool            `json:"is_paused"`
	Freeze         *bool            `json:"is_freeze_time"`
	Warmup         *bool            `json:"is_warmup"`
	Started        *bool            `json:"match_started"`
	Phase          int              `json:"game_phase"`
	MatchStartTime *float64         `json:"match_start_time_seconds"`
	PauseFlags     map[string]*bool `json:"pause_flags"`
	Ambiguous      bool             `json:"ambiguous_tick"`
}
type segment struct {
	Start int `json:"start_demo_tick"`
	End   int `json:"end_demo_tick"`
	state
}
type shot struct {
	Tick         int      `json:"demo_tick"`
	ObservedTick int      `json:"observed_demo_tick"`
	RoundID      int      `json:"round_id"`
	SteamID      string   `json:"steam_id"`
	Slot         int      `json:"player_slot"`
	Weapon       string   `json:"weapon"`
	LastShot     *float64 `json:"weapon_last_shot_time_seconds"`
	Ambiguous    bool     `json:"ambiguous_observation"`
}

func contextWarningAllowed(kind events.WarnType) bool {
	// These concern bombsite bounding boxes / grenade model names, neither
	// of which supplies the rule state or active-weapon shot clock audited here.
	return kind == events.WarnTypeBombsiteUnknown || kind == events.WarnTypeUnknownGrenadeModel
}

func markAmbiguousShots(shots []shot) {
	counts := map[string]int{}
	key := func(s shot) string { return fmt.Sprintf("%d:%s:%d:%s", s.ObservedTick, s.SteamID, s.Slot, s.Weapon) }
	for _, s := range shots {
		counts[key(s)]++
	}
	for i := range shots {
		if counts[key(shots[i])] != 1 || shots[i].Tick != shots[i].ObservedTick {
			shots[i].Ambiguous = true
		}
	}
}

func boolean(entity st.Entity, name string) *bool {
	if entity == nil {
		return nil
	}
	value, ok := entity.PropertyValue(name)
	if !ok {
		return nil
	}
	result, ok := value.Any.(bool)
	if !ok {
		return nil
	}
	return &result
}
func number(entity st.Entity, name string) *float64 {
	if entity == nil {
		return nil
	}
	value, ok := entity.PropertyValue(name)
	if !ok || value.Any == nil {
		return nil
	}
	var result float64
	switch value := value.Any.(type) {
	case float32:
		result = float64(value)
	case float64:
		result = value
	default:
		return nil
	}
	if math.IsNaN(result) || math.IsInf(result, 0) {
		return nil
	}
	return &result
}
func pauseState(values map[string]*bool) *bool {
	paused := false
	for _, name := range pauseNames {
		v := values[name]
		if v == nil {
			return nil
		}
		paused = paused || *v
	}
	return &paused
}
func appendSegment(segments []segment, tick int, value state) []segment {
	if len(segments) > 0 {
		last := &segments[len(segments)-1]
		if last.End == tick && reflect.DeepEqual(last.state, value) {
			last.End = tick + 1
			return segments
		}
	}
	return append(segments, segment{Start: tick, End: tick + 1, state: value})
}

func main() {
	input := flag.String("input", "", "source .dem file")
	out := flag.String("out", "", "new context JSON; existing files are never replaced")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(1)
	}
	if err := run(*input, *out); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run(input, out string) error {
	if input == "" || out == "" {
		return fmt.Errorf("--input and --out are required")
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
	segments := []segment{}
	shots := []shot{}
	warnings := map[string]int{}
	var warningErr error
	warningHandler := func(e events.ParserWarn) {
		warnings[strconv.Itoa(int(e.Type))]++
		if !contextWarningAllowed(e.Type) {
			warningErr = fmt.Errorf("context evidence invalidated by parser warning %d: %s", e.Type, e.Message)
		}
	}
	p.RegisterEventHandler(warningHandler)
	p.RegisterNetMessageHandler(warningHandler)
	lastTick := -1
	lastObservedTick := -2147483648
	var pending state
	type pendingShot struct {
		row    shot
		weapon st.Entity
	}
	pendingShots := []pendingShot{}
	p.RegisterEventHandler(func(events.RoundStart) { roundID++ })
	p.RegisterEventHandler(func(e events.WeaponFire) {
		if e.Shooter == nil || e.Weapon == nil {
			return
		}
		pendingShots = append(pendingShots, pendingShot{row: shot{Tick: p.GameState().IngameTick(), RoundID: roundID,
			SteamID: fmt.Sprint(e.Shooter.SteamID64), Slot: e.Shooter.EntityID - 1, Weapon: e.Weapon.String()}, weapon: e.Weapon.Entity})
	})
	var frameErr error
	p.RegisterEventHandler(func(events.FrameDone) {
		if frameErr != nil {
			return
		}
		gs := p.GameState()
		tick := gs.IngameTick()
		repeatedTick := tick == lastObservedTick
		if repeatedTick {
			for i := len(shots) - 1; i >= 0 && shots[i].ObservedTick == tick; i-- {
				shots[i].Ambiguous = true
			}
		}
		for _, s := range pendingShots {
			s.row.ObservedTick = tick
			s.row.LastShot = number(s.weapon, "m_fLastShotTime")
			s.row.Ambiguous = repeatedTick || s.row.Tick != tick
			shots = append(shots, s.row)
		}
		lastObservedTick = tick
		pendingShots = nil
		if tick < 0 {
			return
		}
		if tick < lastTick {
			frameErr = fmt.Errorf("context demo clock reversed from %d to %d", lastTick, tick)
			return
		}
		entity := gs.Rules().Entity()
		flags := map[string]*bool{}
		for _, name := range pauseNames {
			flags[name] = boolean(entity, rulePrefix+name)
		}
		current := state{RoundID: roundID, Paused: pauseState(flags), Freeze: boolean(entity, rulePrefix+"m_bFreezePeriod"),
			Warmup: boolean(entity, rulePrefix+"m_bWarmupPeriod"), Started: boolean(entity, rulePrefix+"m_bHasMatchStarted"),
			Phase: int(gs.GamePhase()), MatchStartTime: number(entity, rulePrefix+"m_fMatchStartTime"), PauseFlags: flags}
		if tick == lastTick {
			if !reflect.DeepEqual(current, pending) {
				current.Ambiguous = true
				current.Paused = nil
				pending = current
			}
			return
		}
		if lastTick >= 0 {
			segments = appendSegment(segments, lastTick, pending)
		}
		lastTick = tick
		pending = current
	})
	if err = p.ParseToEnd(); err != nil {
		return err
	}
	if frameErr != nil {
		return frameErr
	}
	if warningErr != nil {
		return warningErr
	}
	if len(pendingShots) != 0 {
		return fmt.Errorf("shot events have no completed frame observation")
	}
	markAmbiguousShots(shots)
	if lastTick >= 0 {
		segments = appendSegment(segments, lastTick, pending)
	}
	after, err := demo.HashFile(input)
	if err != nil {
		return err
	}
	if after != id {
		return fmt.Errorf("source changed during context audit")
	}
	absolute, err := filepath.Abs(input)
	if err != nil {
		return err
	}
	result := map[string]any{"schema_version": 2, "producer": "cs2-context-v2", "demo_id": id, "source_demo_sha256": id,
		"source_demo_path": absolute, "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0",
		"parse_status": "complete", "partial": false, "timing_clock": "demo_tick", "tick_rate": p.TickRate(),
		"property_prefix": rulePrefix, "required_pause_flags": pauseNames, "segments": segments, "weapon_fire_observations": shots,
		"shot_observation_policy": "unique-weapon-event-per-observed-tick-v1", "warnings": warnings,
		"warning_policy": "rule-and-shot-evidence-v1", "evidence_loss_warnings": 0}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(out), 0755); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(out), ".cs2-context-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	_, err = file.Write(append(data, '\n'))
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Link(file.Name(), out)
}
