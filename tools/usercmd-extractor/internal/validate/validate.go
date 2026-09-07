// Package validate checks extraction integrity independently of parser success.
package validate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"chicken-farmer/tools/usercmd-extractor/internal/schema"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	"github.com/parquet-go/parquet-go"
	"google.golang.org/protobuf/proto"
)

const duplicateWindow = 4096

type Report struct {
	Passed                    bool              `json:"passed"`
	DemoID                    string            `json:"demo_id"`
	SchemaVersion             string            `json:"schema_version"`
	ParseStatus               string            `json:"parse_status"`
	Errors                    []string          `json:"errors"`
	Warnings                  []string          `json:"warnings"`
	CommandCount              int64             `json:"command_count"`
	EligiblePayloadCount      int64             `json:"eligible_payload_count"`
	IneligiblePayloadCount    int64             `json:"ineligible_payload_count"`
	RejectedPayloadCount      int64             `json:"rejected_payload_count"`
	UnaccountedPayloadCount   int64             `json:"unaccounted_payload_count"`
	ReconstructionCoveragePct *float64          `json:"reconstruction_coverage_pct"`
	StateCount                int64             `json:"state_count"`
	RoundCount                int64             `json:"round_count"`
	EventCount                int64             `json:"event_count"`
	HumanPlayers              int               `json:"human_players"`
	FilesVerified             int               `json:"files_verified"`
	ProtobufInvalidRows       int64             `json:"protobuf_invalid_rows"`
	ProtobufMismatchRows      int64             `json:"protobuf_mismatch_rows"`
	InvalidFractionCount      int64             `json:"invalid_fraction_count"`
	InvalidNumericRows        int64             `json:"invalid_numeric_rows"`
	InvalidIdentityRows       int64             `json:"invalid_identity_rows"`
	CommandRowOrderErrors     int64             `json:"command_row_order_errors"`
	DuplicateWindowCommands   int               `json:"duplicate_window_commands"`
	Players                   []PlayerReport    `json:"players"`
	MetricNotes               map[string]string `json:"metric_notes"`
}

type PlayerReport struct {
	PlayerKey                     string   `json:"player_key"`
	SteamID                       *uint64  `json:"steam_id"`
	PlayerSlot                    int32    `json:"player_slot"`
	PlayerName                    string   `json:"player_name"`
	Commands                      int64    `json:"commands"`
	FirstCommandDemoTick          *int64   `json:"first_command_demo_tick"`
	LastCommandDemoTick           *int64   `json:"last_command_demo_tick"`
	FirstCommandServerTick        *int32   `json:"first_command_server_tick"`
	LastCommandServerTick         *int32   `json:"last_command_server_tick"`
	ActiveCommands                int64    `json:"active_commands"`
	MissingMouse                  int64    `json:"missing_mouse"`
	MissingBaseMessages           int64    `json:"missing_base_messages"`
	MissingViewangleMessages      int64    `json:"missing_viewangle_messages"`
	MissingButtonMessages         int64    `json:"missing_button_messages"`
	MissingMousedx                int64    `json:"missing_mousedx"`
	MissingMousedy                int64    `json:"missing_mousedy"`
	MissingViewangles             int64    `json:"missing_viewangles"`
	MissingButtons                int64    `json:"missing_buttons"`
	MissingButtonstate2           int64    `json:"missing_buttonstate2"`
	MissingButtonstate3           int64    `json:"missing_buttonstate3"`
	ZeroMouse                     int64    `json:"zero_mouse"`
	NonzeroMouse                  int64    `json:"nonzero_mouse"`
	MouseXVariance                float64  `json:"mouse_x_variance"`
	MouseYVariance                float64  `json:"mouse_y_variance"`
	ViewAngleChanges              int64    `json:"view_angle_changes"`
	NonzeroMovement               int64    `json:"nonzero_movement"`
	NonzeroButtons                int64    `json:"nonzero_buttons"`
	AttackCommands                int64    `json:"attack_commands"`
	WeaponFireEvents              int64    `json:"weapon_fire_events"`
	CommandNumberGaps             int64    `json:"command_number_gaps"`
	MaxCommandNumberGap           int64    `json:"max_command_number_gap"`
	MaxCommandGapTicks            int64    `json:"max_cmd_gap_ticks"`
	AliveCommandGapsOverOneSecond int64    `json:"alive_command_gaps_over_one_second"`
	ServerTicksOutOfOrder         int64    `json:"server_ticks_out_of_order"`
	CommandNumbersOutOfOrder      int64    `json:"command_numbers_out_of_order"`
	DuplicateCommands             int64    `json:"duplicate_commands"`
	SubtickRecords                int64    `json:"num_subtick_records"`
	InputHistoryEntries           int64    `json:"num_input_history_entries"`
	InvalidFractions              int64    `json:"invalid_fractions"`
	InvalidSubtickFractions       int64    `json:"invalid_subtick_fractions"`
	InvalidHistoryFractions       int64    `json:"invalid_history_fractions"`
	MinOutOfRangeSubtickWhen      *float32 `json:"min_out_of_range_subtick_when"`
	MaxOutOfRangeSubtickWhen      *float32 `json:"max_out_of_range_subtick_when"`
	ObservedAliveSeconds          float64  `json:"observed_alive_seconds"`
	CommandsPerAliveSecond        *float64 `json:"cmds_per_alive_second"`
	PctZeroMouse                  float64  `json:"pct_zero_mouse"`
	PctMissingMouse               float64  `json:"pct_missing_mouse"`
	PctMissingViewangles          float64  `json:"pct_missing_viewangles"`
	PctMissingButtons             float64  `json:"pct_missing_buttons"`
}

type moments struct {
	n        int64
	mean, m2 float64
}

func (m *moments) add(x float64) {
	m.n++
	d := x - m.mean
	m.mean += d / float64(m.n)
	m.m2 += d * (x - m.mean)
}
func (m moments) variance() float64 {
	if m.n < 2 {
		return 0
	}
	return m.m2 / float64(m.n-1)
}

type cmdKey struct{ round, command, tick int32 }
type player struct {
	PlayerReport
	x, y             moments
	previous         *schema.UserCmd
	state            *schema.PlayerState
	recent           map[cmdKey]int
	ring             [duplicateWindow]cmdKey
	position, filled int
}

// Validate returns failed reports for data problems, including damaged tables.
// Only manifest access/decoding failures use the error return.
func Validate(dir string) (Report, error) {
	r := Report{SchemaVersion: schema.Version, Errors: []string{}, Warnings: []string{}, Players: []PlayerReport{}, DuplicateWindowCommands: duplicateWindow}
	b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return r, fmt.Errorf("read manifest: %w", err)
	}
	var m schema.Manifest
	if err = json.Unmarshal(b, &m); err != nil {
		return r, fmt.Errorf("decode manifest: %w", err)
	}
	r.DemoID, r.ParseStatus = m.DemoID, m.ParseStatus
	r.EligiblePayloadCount = m.EligiblePayloadCount
	r.IneligiblePayloadCount = m.IneligiblePayloadCount
	for _, key := range []string{"usercmd_delta_decode_failed", "usercmd_baseline_missing", "usercmd_baseline_mismatch"} {
		r.RejectedPayloadCount += m.Warnings[key]
	}
	r.MetricNotes = map[string]string{
		"missing_fields":          "Counts encoded nullable scalar omissions, separately from missing parent protobuf messages; omission inside a verified present message can have a protobuf default.",
		"pct_zero_mouse":          "Explicitly present zero dx/dy pairs divided by fully present dx/dy pairs, times 100; omitted fields are excluded.",
		"nonzero_mouse":           "Commands with at least one explicitly encoded nonzero mouse axis, even when the other axis is omitted.",
		"mouse_variance":          "Sample variance of explicitly encoded scalar values only; omitted protobuf defaults are excluded.",
		"cmds_per_alive_second":   "Active command count divided by observed unpaused alive state duration; state intervals exceeding one second are excluded. This is a coverage diagnostic, not proof of clock alignment.",
		"fraction_range":          "Subtick/history values outside [0,1] or nonfinite are flagged for investigation and never clamped; this does not establish whether an upstream format supports signed offsets.",
		"reconstruction_coverage": "Reconstructed rows divided by eligible wire envelopes. Each eligible envelope must be accounted for by one output row or one reconstruction-rejection warning; full+delta field counts may overlap.",
		"command_coverage_ticks":  "First/last ticks describe the emitted stream, which may begin after the demo because an initial delta baseline is unavailable. Continuity metrics apply between emitted commands and do not measure that unavailable prefix.",
	}
	if m.DemoID == "" {
		r.Errors = append(r.Errors, "manifest demo_id is empty")
	}
	if m.ParserSchemaVersion != schema.Version {
		r.Errors = append(r.Errors, fmt.Sprintf("unsupported schema version %q", m.ParserSchemaVersion))
	}
	if m.ParseStatus != "complete" || m.Partial {
		r.Errors = append(r.Errors, "extraction is partial or incomplete; not eligible for production training")
	}
	if m.Map == "" {
		r.Errors = append(r.Errors, "map is missing")
	}
	if m.TickRate <= 0 || math.IsNaN(m.TickRate) || math.IsInf(m.TickRate, 0) {
		r.Errors = append(r.Errors, "tick_rate must be finite and positive")
	}
	if !validHash(m.SHA256) {
		r.Errors = append(r.Errors, "source SHA256 is missing or malformed")
	}
	if m.ButtonMappingValidation != "validated" {
		r.Warnings = append(r.Warnings, "button meanings need event/visual verification; raw masks remain authoritative")
	}
	warningKeys := make([]string, 0, len(m.Warnings))
	for key := range m.Warnings {
		warningKeys = append(warningKeys, key)
	}
	sort.Strings(warningKeys)
	for _, key := range warningKeys {
		if m.Warnings[key] == 0 {
			continue
		}
		message := fmt.Sprintf("parser warning %s: %d", key, m.Warnings[key])
		if strings.Contains(key, "usercmd") && (strings.Contains(key, "fail") || strings.Contains(key, "missing") || strings.Contains(key, "mismatch")) {
			r.Errors = append(r.Errors, message+"; full command reconstruction is incomplete")
		} else {
			r.Warnings = append(r.Warnings, message)
		}
	}
	for _, name := range []string{"usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"} {
		expected, ok := m.Files[name]
		if !ok || !validHash(expected) {
			r.Errors = append(r.Errors, name+": missing or malformed manifest SHA256")
			continue
		}
		actual, err := hashFile(filepath.Join(dir, name))
		if err != nil {
			r.Errors = append(r.Errors, fmt.Sprintf("%s: %v", name, err))
			continue
		}
		if !strings.EqualFold(actual, expected) {
			r.Errors = append(r.Errors, name+": SHA256 differs from manifest")
		} else {
			r.FilesVerified++
		}
	}
	players := map[string]*player{}
	getPlayer := func(steam *uint64, slot int32, name string) *player {
		key := playerKey(steam, slot)
		p := players[key]
		if p == nil {
			p = &player{PlayerReport: PlayerReport{PlayerKey: key, SteamID: steam, PlayerSlot: slot, PlayerName: name}, recent: map[cmdKey]int{}}
			players[key] = p
		}
		if name != "" {
			p.PlayerName = name
		}
		return p
	}
	var previousRowID int64
	haveRow := false
	r.CommandCount, err = stream[schema.UserCmd](filepath.Join(dir, "usercmd.parquet"), func(c schema.UserCmd) {
		name := ""
		if c.PlayerName != nil {
			name = *c.PlayerName
		}
		p := getPlayer(c.SteamID, c.PlayerSlot, name)
		if c.DemoID != m.DemoID {
			r.InvalidIdentityRows++
		}
		if haveRow && c.CommandRowID != previousRowID+1 {
			r.CommandRowOrderErrors++
		}
		previousRowID = c.CommandRowID
		haveRow = true
		p.observeCommand(c, m.TickRate, m.ButtonMasks["attack1"])
		if !numericValid(c) {
			r.InvalidNumericRows++
		}
		var pb msg.CSGOUserCmdPB
		if len(c.CommandProtobuf) == 0 || proto.Unmarshal(c.CommandProtobuf, &pb) != nil || pb.Base == nil {
			r.ProtobufInvalidRows++
		} else if !commandMatches(c, &pb) {
			r.ProtobufMismatchRows++
		}
	})
	if err != nil {
		r.Errors = append(r.Errors, "usercmd.parquet: "+err.Error())
	}
	steamBySlot := map[int32]uint64{}
	slotChanges := 0
	r.StateCount, err = stream[schema.PlayerState](filepath.Join(dir, "player_state.parquet"), func(s schema.PlayerState) {
		if s.DemoID != m.DemoID {
			r.InvalidIdentityRows++
		}
		p := getPlayer(s.SteamID, s.PlayerSlot, s.PlayerName)
		if s.SteamID != nil && *s.SteamID != 0 {
			if old, ok := steamBySlot[s.PlayerSlot]; ok && old != *s.SteamID {
				slotChanges++
			}
			steamBySlot[s.PlayerSlot] = *s.SteamID
		}
		if previous := p.state; previous != nil && previous.RoundID == s.RoundID && activeState(*previous) && activeState(s) && previous.DemoTimeSeconds != nil && s.DemoTimeSeconds != nil {
			delta := *s.DemoTimeSeconds - *previous.DemoTimeSeconds
			if delta > 0 && delta <= 1 {
				p.ObservedAliveSeconds += delta
			}
		}
		p.state = &s
	})
	if err != nil {
		r.Errors = append(r.Errors, "player_state.parquet: "+err.Error())
	}
	if slotChanges > 0 {
		r.Warnings = append(r.Warnings, fmt.Sprintf("player-slot identities changed %d times; review reconnects/substitutions", slotChanges))
	}
	liveRounds, invalidRounds := int64(0), int64(0)
	seenRounds := map[int32]bool{}
	r.RoundCount, err = stream[schema.Round](filepath.Join(dir, "rounds.parquet"), func(round schema.Round) {
		if round.DemoID != m.DemoID {
			r.InvalidIdentityRows++
		}
		if !round.IsWarmup {
			liveRounds++
		}
		if seenRounds[round.RoundID] || (round.EndTick != nil && *round.EndTick < round.StartTick) || (round.FreezeEndTick != nil && *round.FreezeEndTick < round.StartTick) {
			invalidRounds++
		}
		seenRounds[round.RoundID] = true
	})
	if err != nil {
		r.Errors = append(r.Errors, "rounds.parquet: "+err.Error())
	}
	r.EventCount, err = stream[schema.GameEvent](filepath.Join(dir, "events.parquet"), func(event schema.GameEvent) {
		if event.DemoID != m.DemoID {
			r.InvalidIdentityRows++
		}
		if event.Kind == "weapon_fire" && event.PlayerSlot != nil {
			getPlayer(event.SteamID, *event.PlayerSlot, "").WeaponFireEvents++
		}
	})
	if err != nil {
		r.Errors = append(r.Errors, "events.parquet: "+err.Error())
	}
	if r.CommandCount != m.CommandCount {
		r.Errors = append(r.Errors, fmt.Sprintf("command count mismatch: manifest=%d table=%d", m.CommandCount, r.CommandCount))
	}
	r.UnaccountedPayloadCount = r.EligiblePayloadCount - r.CommandCount - r.RejectedPayloadCount
	if r.EligiblePayloadCount > 0 {
		coverage := 100 * float64(r.CommandCount) / float64(r.EligiblePayloadCount)
		r.ReconstructionCoveragePct = &coverage
	}
	if r.EligiblePayloadCount < 0 || r.IneligiblePayloadCount < 0 || r.RejectedPayloadCount < 0 {
		r.Errors = append(r.Errors, "negative payload audit count")
	}
	if r.UnaccountedPayloadCount != 0 {
		r.Errors = append(r.Errors, fmt.Sprintf("eligible payload accounting mismatch: eligible=%d reconstructed=%d rejected=%d unaccounted=%d", r.EligiblePayloadCount, r.CommandCount, r.RejectedPayloadCount, r.UnaccountedPayloadCount))
	}
	if r.StateCount != m.StateCount {
		r.Errors = append(r.Errors, fmt.Sprintf("state count mismatch: manifest=%d table=%d", m.StateCount, r.StateCount))
	}
	if r.RoundCount != int64(m.RoundCount) {
		r.Errors = append(r.Errors, fmt.Sprintf("round count mismatch: manifest=%d table=%d", m.RoundCount, r.RoundCount))
	}
	if r.CommandCount == 0 {
		r.Errors = append(r.Errors, "no reconstructed UserCmd rows; no canonical input labels available")
	}
	if r.StateCount == 0 {
		r.Errors = append(r.Errors, "no player-state rows")
	}
	if liveRounds == 0 {
		r.Errors = append(r.Errors, "no competitive rounds")
	}
	if invalidRounds > 0 {
		r.Errors = append(r.Errors, fmt.Sprintf("%d invalid or duplicate rounds", invalidRounds))
	}
	if m.DeltaPayloadCount > 0 && r.CommandCount == 0 {
		r.Errors = append(r.Errors, "delta payloads exist without retained reconstructed commands")
	}
	for _, p := range players {
		p.finish()
		if p.SteamID != nil && *p.SteamID != 0 {
			r.HumanPlayers++
		}
		r.InvalidFractionCount += p.InvalidFractions
		if p.Commands > 0 {
			if p.MissingBaseMessages == p.Commands {
				r.Errors = append(r.Errors, p.PlayerKey+": all base command messages are missing")
			}
			if p.MissingViewangleMessages == p.Commands {
				r.Errors = append(r.Errors, p.PlayerKey+": all view-angle messages are missing")
			}
			if p.MissingButtonMessages == p.Commands {
				r.Errors = append(r.Errors, p.PlayerKey+": all button messages are missing")
			}
			if p.MissingMouse > 0 || p.MissingViewangles > 0 || p.MissingButtons > 0 {
				r.Warnings = append(r.Warnings, p.PlayerKey+": encoded scalar omissions retained; derive protobuf defaults only within verified present messages")
			}
			if p.Commands < 1000 {
				r.Warnings = append(r.Warnings, p.PlayerKey+": fewer than 1000 commands; insufficient for match-scale quality acceptance")
			}
			if p.Commands >= 1000 && p.NonzeroMouse == 0 {
				r.Errors = append(r.Errors, p.PlayerKey+": no nonzero mouse activity across at least 1000 commands")
			}
			if p.Commands >= 1000 && p.ViewAngleChanges == 0 {
				r.Errors = append(r.Errors, p.PlayerKey+": view angles never change across at least 1000 commands")
			}
			if p.Commands >= 1000 && p.NonzeroMovement == 0 && p.NonzeroButtons == 0 {
				r.Errors = append(r.Errors, p.PlayerKey+": no movement or button activity across at least 1000 commands")
			}
			if p.MouseXVariance == 0 && p.MouseYVariance == 0 {
				r.Warnings = append(r.Warnings, p.PlayerKey+": raw mouse variance is zero")
			}
			if p.AliveCommandGapsOverOneSecond > 0 || p.ServerTicksOutOfOrder > 0 || p.DuplicateCommands > 0 {
				r.Warnings = append(r.Warnings, p.PlayerKey+": command continuity requires review; demo/server clocks are not assumed equal")
			}
		} else if p.ObservedAliveSeconds > 1 {
			r.Errors = append(r.Errors, p.PlayerKey+": alive player has no reconstructed commands")
		}
		if p.WeaponFireEvents > 0 && p.AttackCommands == 0 {
			r.Warnings = append(r.Warnings, p.PlayerKey+": weapon-fire events without attack bits; validate mapping and timing")
		}
		r.Players = append(r.Players, p.PlayerReport)
	}
	sort.Slice(r.Players, func(i, j int) bool { return r.Players[i].PlayerKey < r.Players[j].PlayerKey })
	var subtickFlags, historyFlags int64
	var minWhen, maxWhen *float32
	for _, p := range r.Players {
		subtickFlags += p.InvalidSubtickFractions
		historyFlags += p.InvalidHistoryFractions
		if p.MinOutOfRangeSubtickWhen != nil && (minWhen == nil || *p.MinOutOfRangeSubtickWhen < *minWhen) {
			minWhen = p.MinOutOfRangeSubtickWhen
		}
		if p.MaxOutOfRangeSubtickWhen != nil && (maxWhen == nil || *p.MaxOutOfRangeSubtickWhen > *maxWhen) {
			maxWhen = p.MaxOutOfRangeSubtickWhen
		}
	}
	r.MetricNotes["fraction_findings"] = fmt.Sprintf("Flagged subtick when values: %d; flagged input-history fractions: %d.", subtickFlags, historyFlags)
	if minWhen != nil && maxWhen != nil {
		r.MetricNotes["fraction_findings"] += fmt.Sprintf(" Finite out-of-range subtick when spans [%g, %g]; values are preserved unchanged.", *minWhen, *maxWhen)
	}
	if r.HumanPlayers < 2 {
		r.Errors = append(r.Errors, "fewer than two human player identities observed")
	}
	for _, check := range []struct {
		name string
		n    int64
	}{
		{"invalid/missing reconstructed protobuf rows", r.ProtobufInvalidRows},
		{"rows whose nullable action fields differ from their protobuf", r.ProtobufMismatchRows},
		{"invalid subtick/history fractions", r.InvalidFractionCount},
		{"rows with non-finite action numbers", r.InvalidNumericRows},
		{"rows with inconsistent demo identity", r.InvalidIdentityRows},
		{"nonsequential command row IDs", r.CommandRowOrderErrors},
	} {
		if check.n > 0 {
			r.Errors = append(r.Errors, fmt.Sprintf("%d %s", check.n, check.name))
		}
	}
	r.Warnings = append(r.Warnings, "extraction checks do not validate POV frames, clock alignment, or visual action timing", fmt.Sprintf("duplicate detection covers each player's most recent %d commands", duplicateWindow))
	sort.Strings(r.Errors)
	sort.Strings(r.Warnings)
	r.Passed = len(r.Errors) == 0
	return r, nil
}

func (r Report) String() string {
	status := "FAIL"
	if r.Passed {
		status = "PASS"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s: %s; commands=%d states=%d rounds=%d human_players=%d\n", status, r.DemoID, r.CommandCount, r.StateCount, r.RoundCount, r.HumanPlayers)
	if r.ReconstructionCoveragePct != nil {
		fmt.Fprintf(&b, "Reconstruction coverage: %.2f%% (%d/%d eligible payloads); rejected=%d unaccounted=%d\n", *r.ReconstructionCoveragePct, r.CommandCount, r.EligiblePayloadCount, r.RejectedPayloadCount, r.UnaccountedPayloadCount)
	}
	for _, message := range r.Errors {
		fmt.Fprintf(&b, "ERROR: %s\n", message)
	}
	for _, message := range r.Warnings {
		fmt.Fprintf(&b, "WARNING: %s\n", message)
	}
	return strings.TrimSpace(b.String())
}

func playerKey(steam *uint64, slot int32) string {
	if steam != nil && *steam != 0 {
		return fmt.Sprintf("steam:%d", *steam)
	}
	return fmt.Sprintf("slot:%d", slot)
}
func activeCommand(c schema.UserCmd) bool {
	return c.Alive != nil && *c.Alive && !c.IsWarmup && !c.IsFreezeTime
}
func activeState(s schema.PlayerState) bool {
	return s.Alive && !s.IsWarmup && !s.IsFreezeTime && (s.IsPaused == nil || !*s.IsPaused)
}

func (p *player) observeCommand(c schema.UserCmd, tickRate float64, attackMask uint64) {
	p.Commands++
	if p.FirstCommandDemoTick == nil {
		demoTick, serverTick := c.DemoTick, c.ServerTickExecuted
		p.FirstCommandDemoTick = &demoTick
		p.FirstCommandServerTick = &serverTick
	}
	demoTick, serverTick := c.DemoTick, c.ServerTickExecuted
	p.LastCommandDemoTick = &demoTick
	p.LastCommandServerTick = &serverTick
	if !c.BasePresent {
		p.MissingBaseMessages++
	}
	if !c.ViewanglesPresent {
		p.MissingViewangleMessages++
	}
	if !c.ButtonsPresent {
		p.MissingButtonMessages++
	}
	if activeCommand(c) {
		p.ActiveCommands++
	}
	if c.MousedxRaw == nil {
		p.MissingMousedx++
	} else {
		p.x.add(float64(*c.MousedxRaw))
	}
	if c.MousedyRaw == nil {
		p.MissingMousedy++
	} else {
		p.y.add(float64(*c.MousedyRaw))
	}
	if c.MousedxRaw == nil || c.MousedyRaw == nil {
		p.MissingMouse++
	}
	if (c.MousedxRaw != nil && *c.MousedxRaw != 0) || (c.MousedyRaw != nil && *c.MousedyRaw != 0) {
		p.NonzeroMouse++
	} else if c.MousedxRaw != nil && c.MousedyRaw != nil {
		p.ZeroMouse++
	}
	if c.ViewPitch == nil || c.ViewYaw == nil {
		p.MissingViewangles++
	}
	if c.Buttonstate1 == nil {
		p.MissingButtons++
	} else {
		if *c.Buttonstate1 != 0 {
			p.NonzeroButtons++
		}
		if attackMask != 0 && *c.Buttonstate1&attackMask != 0 {
			p.AttackCommands++
		}
	}
	if c.Buttonstate2 == nil {
		p.MissingButtonstate2++
	}
	if c.Buttonstate3 == nil {
		p.MissingButtonstate3++
	}
	if nonzero(c.Forwardmove) || nonzero(c.Leftmove) || nonzero(c.Upmove) {
		p.NonzeroMovement++
	}
	p.SubtickRecords += int64(len(c.SubtickMoves))
	p.InputHistoryEntries += int64(len(c.InputHistory))
	for _, s := range c.SubtickMoves {
		if invalidFraction(s.When) {
			p.InvalidFractions++
			p.InvalidSubtickFractions++
			if !math.IsNaN(float64(*s.When)) && !math.IsInf(float64(*s.When), 0) {
				if p.MinOutOfRangeSubtickWhen == nil || *s.When < *p.MinOutOfRangeSubtickWhen {
					value := *s.When
					p.MinOutOfRangeSubtickWhen = &value
				}
				if p.MaxOutOfRangeSubtickWhen == nil || *s.When > *p.MaxOutOfRangeSubtickWhen {
					value := *s.When
					p.MaxOutOfRangeSubtickWhen = &value
				}
			}
		}
	}
	for _, h := range c.InputHistory {
		if invalidFraction(h.RenderTickFraction) {
			p.InvalidFractions++
			p.InvalidHistoryFractions++
		}
		if invalidFraction(h.PlayerTickFraction) {
			p.InvalidFractions++
			p.InvalidHistoryFractions++
		}
	}
	if prev := p.previous; prev != nil {
		if c.ViewanglesPresent && prev.ViewanglesPresent && (defaultFloat(c.ViewPitch) != defaultFloat(prev.ViewPitch) || defaultFloat(c.ViewYaw) != defaultFloat(prev.ViewYaw)) {
			p.ViewAngleChanges++
		}
		if c.RoundID == prev.RoundID {
			gap := int64(c.ServerTickExecuted) - int64(prev.ServerTickExecuted)
			if gap < 0 {
				p.ServerTicksOutOfOrder++
			}
			if gap > p.MaxCommandGapTicks {
				p.MaxCommandGapTicks = gap
			}
			if tickRate > 0 && float64(gap) > tickRate && activeCommand(c) && activeCommand(*prev) {
				p.AliveCommandGapsOverOneSecond++
			}
			commandGap := int64(c.CommandNumber) - int64(prev.CommandNumber)
			if commandGap < 0 {
				p.CommandNumbersOutOfOrder++
			}
			if commandGap > 1 {
				p.CommandNumberGaps++
				if commandGap-1 > p.MaxCommandNumberGap {
					p.MaxCommandNumberGap = commandGap - 1
				}
			}
		}
	}
	key := cmdKey{c.RoundID, c.CommandNumber, c.ServerTickExecuted}
	if p.recent[key] > 0 {
		p.DuplicateCommands++
	}
	if p.filled == duplicateWindow {
		old := p.ring[p.position]
		p.recent[old]--
		if p.recent[old] == 0 {
			delete(p.recent, old)
		}
	} else {
		p.filled++
	}
	p.ring[p.position] = key
	p.position = (p.position + 1) % duplicateWindow
	p.recent[key]++
	// Hold only the previous scalar fields, not variable-sized protobuf buffers.
	p.previous = &schema.UserCmd{RoundID: c.RoundID, CommandNumber: c.CommandNumber, ServerTickExecuted: c.ServerTickExecuted, ViewanglesPresent: c.ViewanglesPresent, ViewPitch: c.ViewPitch, ViewYaw: c.ViewYaw, Alive: c.Alive, IsWarmup: c.IsWarmup, IsFreezeTime: c.IsFreezeTime}
}

func (p *player) finish() {
	p.MouseXVariance, p.MouseYVariance = p.x.variance(), p.y.variance()
	if p.Commands > 0 {
		n := float64(p.Commands)
		p.PctMissingMouse = 100 * float64(p.MissingMouse) / n
		p.PctMissingViewangles = 100 * float64(p.MissingViewangles) / n
		p.PctMissingButtons = 100 * float64(p.MissingButtons) / n
	}
	if present := p.Commands - p.MissingMouse; present > 0 {
		p.PctZeroMouse = 100 * float64(p.ZeroMouse) / float64(present)
	}
	if p.ObservedAliveSeconds > 0 {
		value := float64(p.ActiveCommands) / p.ObservedAliveSeconds
		p.CommandsPerAliveSecond = &value
	}
}
func nonzero(v *float32) bool { return v != nil && *v != 0 }
func defaultFloat(v *float32) float32 {
	if v != nil {
		return *v
	}
	return 0
}
func invalidFraction(v *float32) bool {
	return v != nil && (math.IsNaN(float64(*v)) || math.IsInf(float64(*v), 0) || *v < 0 || *v > 1)
}
func numericValid(c schema.UserCmd) bool {
	values := []*float32{c.Forwardmove, c.Leftmove, c.Upmove, c.ViewPitch, c.ViewYaw, c.ViewRoll}
	for _, s := range c.SubtickMoves {
		values = append(values, s.AnalogForwardDelta, s.AnalogLeftDelta, s.PitchDelta, s.YawDelta)
	}
	for _, h := range c.InputHistory {
		values = append(values, h.ViewPitch, h.ViewYaw)
	}
	for _, v := range values {
		if v != nil && (math.IsNaN(float64(*v)) || math.IsInf(float64(*v), 0)) {
			return false
		}
	}
	return true
}
func validHash(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == sha256.Size
}
func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// Physical-schema equality is checked before GenericReader: its default schema
// conversion would otherwise silently fill absent columns with zero values.
func stream[T any](path string, visit func(T)) (count int64, err error) {
	defer func() {
		if value := recover(); value != nil {
			err = fmt.Errorf("parquet read failed: %v", value)
		}
	}()
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return 0, err
	}
	pf, err := parquet.OpenFile(f, info.Size())
	if err != nil {
		return 0, err
	}
	var zero T
	if !parquet.EqualNodes(pf.Schema(), parquet.SchemaOf(zero)) {
		return 0, fmt.Errorf("physical schema differs from expected v%s", schema.Version)
	}
	reader := parquet.NewGenericReader[T](pf)
	defer reader.Close()
	buffer := make([]T, 256)
	for {
		n, readErr := reader.Read(buffer)
		for _, row := range buffer[:n] {
			visit(row)
			count++
		}
		clear(buffer)
		if readErr == io.EOF {
			return count, nil
		}
		if readErr != nil {
			return count, readErr
		}
		if n == 0 {
			return count, io.ErrNoProgress
		}
	}
}

func same[T comparable](a, b *T) bool {
	return a == nil && b == nil || a != nil && b != nil && *a == *b
}
func commandMatches(c schema.UserCmd, pb *msg.CSGOUserCmdPB) bool {
	b := pb.Base
	if b == nil || c.BasePresent != (b != nil) || c.ButtonsPresent != (b.ButtonsPb != nil) || c.ViewanglesPresent != (b.Viewangles != nil) {
		return false
	}
	if !same(c.MousedxRaw, b.Mousedx) || !same(c.MousedyRaw, b.Mousedy) || !same(c.Forwardmove, b.Forwardmove) || !same(c.Leftmove, b.Leftmove) || !same(c.Upmove, b.Upmove) || !same(c.Weaponselect, b.Weaponselect) || !same(c.Impulse, b.Impulse) || !same(c.PawnEntityHandle, b.PawnEntityHandle) {
		return false
	}
	if b.ButtonsPb == nil {
		if c.Buttonstate1 != nil || c.Buttonstate2 != nil || c.Buttonstate3 != nil {
			return false
		}
	} else if !same(c.Buttonstate1, b.ButtonsPb.Buttonstate1) || !same(c.Buttonstate2, b.ButtonsPb.Buttonstate2) || !same(c.Buttonstate3, b.ButtonsPb.Buttonstate3) {
		return false
	}
	if b.Viewangles == nil {
		if c.ViewPitch != nil || c.ViewYaw != nil || c.ViewRoll != nil {
			return false
		}
	} else if !same(c.ViewPitch, b.Viewangles.X) || !same(c.ViewYaw, b.Viewangles.Y) || !same(c.ViewRoll, b.Viewangles.Z) {
		return false
	}
	if !same(c.Attack1StartHistoryIndex, pb.Attack1StartHistoryIndex) || !same(c.Attack2StartHistoryIndex, pb.Attack2StartHistoryIndex) || len(c.SubtickMoves) != len(b.SubtickMoves) || len(c.InputHistory) != len(pb.InputHistory) {
		return false
	}
	for i, s := range c.SubtickMoves {
		e := b.SubtickMoves[i]
		if e == nil {
			return false
		}
		if !same(s.Button, e.Button) || !same(s.Pressed, e.Pressed) || !same(s.When, e.When) || !same(s.AnalogForwardDelta, e.AnalogForwardDelta) || !same(s.AnalogLeftDelta, e.AnalogLeftDelta) || !same(s.PitchDelta, e.PitchDelta) || !same(s.YawDelta, e.YawDelta) {
			return false
		}
	}
	for i, h := range c.InputHistory {
		e := pb.InputHistory[i]
		if e == nil {
			return false
		}
		if !same(h.RenderTickCount, e.RenderTickCount) || !same(h.RenderTickFraction, e.RenderTickFraction) || !same(h.PlayerTickCount, e.PlayerTickCount) || !same(h.PlayerTickFraction, e.PlayerTickFraction) || !same(h.FrameNumber, e.FrameNumber) {
			return false
		}
		if e.ViewAngles == nil {
			if h.ViewPitch != nil || h.ViewYaw != nil {
				return false
			}
		} else if !same(h.ViewPitch, e.ViewAngles.X) || !same(h.ViewYaw, e.ViewAngles.Y) {
			return false
		}
	}
	return true
}
