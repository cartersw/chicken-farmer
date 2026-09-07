package demo

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"

	"chicken-farmer/tools/usercmd-extractor/internal/schema"
	dem "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/common"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	st "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/sendtables"
	"github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/compress/zstd"
	"google.golang.org/protobuf/proto"
)

type Options struct {
	Input, Output, MatchID string
	MaxDemoTick            int64
}

func HashFile(path string) (string, error) {
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

func WriteJSON(path string, value any) error {
	b, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(b, '\n'), 0644)
}

type table[T any] struct {
	file   *os.File
	writer *parquet.GenericWriter[T]
}

func newTable[T any](dir, name, id string) (*table[T], error) {
	f, err := os.OpenFile(filepath.Join(dir, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	w := parquet.NewGenericWriter[T](f, parquet.Compression(&zstd.Codec{}), parquet.MaxRowsPerRowGroup(8192),
		parquet.KeyValueMetadata("demo_id", id), parquet.KeyValueMetadata("parser_schema_version", schema.Version),
		parquet.KeyValueMetadata("parser_version", schema.ParserVersion))
	return &table[T]{f, w}, nil
}
func (t *table[T]) write(row T) error { _, err := t.writer.Write([]T{row}); return err }
func (t *table[T]) Close() error      { return errors.Join(t.writer.Close(), t.file.Close()) }
func ptr[T any](v T) *T               { return &v }
func steam(p *common.Player) *uint64 {
	if p == nil || p.SteamID64 == 0 || p.IsBot {
		return nil
	}
	return ptr(p.SteamID64)
}

// Extract streams every reconstructed command and each available demo snapshot.
// It never upsamples GOTV state to pretend it was observed every server tick.
// Successful output is published by rename; failed runs remain *.partial-*.
func Extract(opts Options) (output string, err error) {
	input, err := filepath.Abs(opts.Input)
	if err != nil {
		return "", err
	}
	id, err := HashFile(input)
	if err != nil {
		return "", err
	}
	output = filepath.Join(opts.Output, id)
	if _, e := os.Stat(output); e == nil {
		return "", fmt.Errorf("immutable output already exists: %s; select a new output version", output)
	} else if !os.IsNotExist(e) {
		return "", e
	}
	if err = os.MkdirAll(opts.Output, 0755); err != nil {
		return "", err
	}
	stage, err := os.MkdirTemp(opts.Output, id+".partial-")
	if err != nil {
		return "", err
	}
	f, err := os.Open(input)
	if err != nil {
		return "", err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", err
	}
	manifest := schema.Manifest{DemoID: id, MatchID: opts.MatchID, SHA256: id, SourcePath: input, FileName: filepath.Base(input), FileSize: info.Size(),
		IngestedAt: time.Now().UTC().Format(time.RFC3339), ParserVersion: schema.ParserVersion, ParserSchemaVersion: schema.Version,
		ExtractorVersion: "0.1.2", ParseStatus: "running", RenderStatus: "pending", ValidationStatus: "see_validation_report", Warnings: map[string]int64{}, WarningExamples: map[string]string{}, Files: map[string]string{},
		Clocks:      map[string]string{"demo_tick": "CS2 demo packet header via GameState.IngameTick; not a frame counter", "demo_frame": "parser CurrentFrame; multiple frames may have same demo_tick", "server_tick_executed": "raw events.UserCmd.ServerTickExecuted; independent clock, zero may mean unavailable", "state_cadence": "available demo snapshots only, no synthesized 64 Hz state", "usercmd_demo_tick": "packet delivery tick, not necessarily command execution time; exact alignment needs execution clock calibration"},
		ButtonMasks: map[string]uint64{"attack1": uint64(common.ButtonAttack), "attack2": uint64(common.ButtonAttack2), "jump": uint64(common.ButtonJump), "crouch": uint64(common.ButtonDuck), "move_forward": uint64(common.ButtonForward), "move_backward": uint64(common.ButtonBack), "move_left": uint64(common.ButtonMoveLeft), "move_right": uint64(common.ButtonMoveRight), "walk": uint64(common.ButtonSpeed), "reload": uint64(common.ButtonReload), "use": uint64(common.ButtonUse)}, ButtonMappingValidation: "library_constants_only"}
	defer func() {
		if failure := recover(); failure != nil {
			err = fmt.Errorf("parser panic: %v (partial output: %s)", failure, stage)
		}
		if err != nil {
			manifest.ParseStatus = "failed"
			_ = WriteJSON(filepath.Join(stage, "manifest.json"), manifest)
		}
	}()
	if err = WriteJSON(filepath.Join(stage, "manifest.json"), manifest); err != nil {
		return "", err
	}
	cmds, err := newTable[schema.UserCmd](stage, "usercmd.parquet", id)
	if err != nil {
		return "", err
	}
	states, err := newTable[schema.PlayerState](stage, "player_state.parquet", id)
	if err != nil {
		_ = cmds.Close()
		return "", err
	}
	roundsTable, err := newTable[schema.Round](stage, "rounds.parquet", id)
	if err != nil {
		_ = cmds.Close()
		_ = states.Close()
		return "", err
	}
	eventsTable, err := newTable[schema.GameEvent](stage, "events.parquet", id)
	if err != nil {
		_ = cmds.Close()
		_ = states.Close()
		_ = roundsTable.Close()
		return "", err
	}
	closed := false
	defer func() {
		if !closed {
			_ = cmds.Close()
			_ = states.Close()
			_ = roundsTable.Close()
			_ = eventsTable.Close()
		}
	}()
	config := dem.DefaultParserConfig
	config.UserCmdParsing = dem.UserCmdParsingFull
	config.MsgQueueBufferSize = 0
	p := dem.NewParserWithConfig(f, config)
	defer p.Close()
	var writeErr error
	check := func(e error) {
		if e != nil && writeErr == nil {
			writeErr = e
			p.Cancel()
		}
	}
	var roundID int32
	rounds := []schema.Round{}
	demoTime := func() *float64 {
		rate := p.TickRate()
		if rate <= 0 {
			return nil
		}
		return ptr(float64(p.GameState().IngameTick()) / rate)
	}
	p.RegisterNetMessageHandler(func(h *msg.CDemoFileHeader) { manifest.Map = h.GetMapName(); manifest.ServerName = h.GetServerName() })
	p.RegisterNetMessageHandler(func(m *msg.CSVCMsg_UserCommands) {
		for _, c := range m.Commands {
			if c == nil {
				continue
			}
			if c.CmdNumber != nil && c.GetPlayerSlot() >= 0 && (len(c.Data) > 0 || len(c.DeltaData) > 0) {
				manifest.EligiblePayloadCount++
			} else {
				manifest.IneligiblePayloadCount++
			}
			if len(c.Data) > 0 {
				manifest.FullPayloadCount++
			}
			if len(c.DeltaData) > 0 {
				manifest.DeltaPayloadCount++
			}
		}
	})
	warningHandler := func(e events.ParserWarn) {
		key := strconv.Itoa(int(e.Type))
		switch e.Type {
		case events.WarnTypeUserCommandDeltaDecodeFailed:
			key = "usercmd_delta_decode_failed"
		case events.WarnTypeUserCommandBaselineMissing:
			key = "usercmd_baseline_missing"
		case events.WarnTypeUserCommandBaselineMismatch:
			key = "usercmd_baseline_mismatch"
		}
		manifest.Warnings[key]++
		if _, ok := manifest.WarningExamples[key]; !ok {
			manifest.WarningExamples[key] = e.Message
		}
	}
	p.RegisterEventHandler(warningHandler)
	// v6 alpha.0 sends UserCmd decode warnings to the net-message dispatcher.
	// Listening on only the event dispatcher silently misses reconstruction loss.
	p.RegisterNetMessageHandler(warningHandler)
	p.RegisterEventHandler(func(e events.RoundStart) {
		roundID++
		rounds = append(rounds, schema.Round{DemoID: id, RoundID: roundID, RoundNumber: int32(p.GameState().TotalRoundsPlayed() + 1), Map: manifest.Map, StartTick: int64(p.GameState().IngameTick()), IsWarmup: p.GameState().IsWarmupPeriod()})
	})
	p.RegisterEventHandler(func(e events.RoundFreezetimeEnd) {
		if len(rounds) > 0 {
			rounds[len(rounds)-1].FreezeEndTick = ptr(int64(p.GameState().IngameTick()))
		}
	})
	p.RegisterEventHandler(func(e events.RoundEnd) {
		if len(rounds) > 0 {
			r := &rounds[len(rounds)-1]
			r.EndTick = ptr(int64(p.GameState().IngameTick()))
			r.WinnerTeam = ptr(int32(e.Winner))
		}
	})
	p.RegisterEventHandler(func(e events.BombPlanted) {
		if len(rounds) > 0 {
			r := &rounds[len(rounds)-1]
			r.BombPlanted = true
			if e.Site != events.BomsiteUnknown {
				r.BombSite = ptr(string(rune(e.Site)))
			}
		}
	})
	writeEvent := func(kind string, player *common.Player, weapon *common.Equipment) {
		r := schema.GameEvent{DemoID: id, RoundID: roundID, DemoTick: int64(p.GameState().IngameTick()), Kind: kind, SteamID: steam(player)}
		if player != nil {
			r.PlayerSlot = ptr(int32(player.EntityID - 1))
		}
		if weapon != nil {
			r.Weapon = ptr(weapon.Type.String())
		}
		check(eventsTable.write(r))
	}
	p.RegisterEventHandler(func(e events.WeaponFire) { writeEvent("weapon_fire", e.Shooter, e.Weapon) })
	p.RegisterEventHandler(func(e events.PlayerJump) { writeEvent("player_jump", e.Player, nil) })
	p.RegisterEventHandler(func(e events.Kill) { writeEvent("death", e.Victim, e.Weapon) })
	p.RegisterEventHandler(func(e events.UserCmd) {
		r, e2 := CommandRow(e)
		if e2 != nil {
			check(e2)
			return
		}
		r.DemoID = id
		if opts.MatchID != "" {
			r.MatchID = ptr(opts.MatchID)
		}
		r.CommandRowID = manifest.CommandCount
		r.RoundID = roundID
		r.DemoTick = int64(p.GameState().IngameTick())
		r.DemoFrame = int64(p.CurrentFrame())
		r.DemoTimeSeconds = demoTime()
		r.IsWarmup = p.GameState().IsWarmupPeriod()
		r.IsFreezeTime = p.GameState().IsFreezetimePeriod()
		check(cmds.write(r))
		manifest.CommandCount++
	})
	lastStateTick := int64(-1)
	lastLog := time.Now()
	p.RegisterEventHandler(func(e events.FrameDone) {
		tick := int64(p.GameState().IngameTick())
		if tick != lastStateTick && tick >= 0 {
			players := p.GameState().Participants().Playing()
			sort.Slice(players, func(i, j int) bool { return players[i].EntityID < players[j].EntityID })
			for _, player := range players {
				if player.IsBot || player.SteamID64 == 0 {
					continue
				}
				r := StateRow(player)
				r.DemoID = id
				r.RoundID = roundID
				r.DemoTick = tick
				r.DemoFrame = int64(p.CurrentFrame())
				r.DemoTimeSeconds = demoTime()
				r.IsWarmup = p.GameState().IsWarmupPeriod()
				r.IsFreezeTime = p.GameState().IsFreezetimePeriod()
				r.IsPaused = paused(p.GameState().Rules().Entity())
				check(states.write(r))
				manifest.StateCount++
			}
			lastStateTick = tick
		}
		if time.Since(lastLog) > 10*time.Second {
			slog.Info("parsing", "demo_id", id, "demo_tick", tick, "commands", manifest.CommandCount)
			lastLog = time.Now()
		}
		if opts.MaxDemoTick > 0 && tick >= opts.MaxDemoTick {
			manifest.Partial = true
			p.Cancel()
		}
	})
	parseErr := p.ParseToEnd()
	if writeErr != nil {
		return "", writeErr
	}
	if parseErr != nil && !(manifest.Partial && errors.Is(parseErr, dem.ErrCancelled)) {
		return "", fmt.Errorf("parse %s: %w; partial output: %s", input, parseErr, stage)
	}
	for _, r := range rounds {
		if err = roundsTable.write(r); err != nil {
			return "", err
		}
	}
	closeErr := errors.Join(cmds.Close(), states.Close(), roundsTable.Close(), eventsTable.Close())
	closed = true
	if closeErr != nil {
		return "", closeErr
	}
	manifest.TickRate = p.TickRate()
	manifest.RoundCount = len(rounds)
	manifest.ParseStatus = "complete"
	if manifest.Partial {
		manifest.ParseStatus = "partial"
	}
	for _, name := range []string{"usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"} {
		hash, e := HashFile(filepath.Join(stage, name))
		if e != nil {
			return "", e
		}
		manifest.Files[name] = hash
	}
	if err = WriteJSON(filepath.Join(stage, "manifest.json"), manifest); err != nil {
		return "", err
	}
	if err = os.Rename(stage, output); err != nil {
		return "", err
	}
	slog.Info("extracted", "demo_id", id, "commands", manifest.CommandCount, "output", output)
	return output, nil
}

// CommandRow retains nullable scalars and a lossless protobuf for future schema changes.
func CommandRow(e events.UserCmd) (schema.UserCmd, error) {
	r := schema.UserCmd{PlayerSlot: e.PlayerSlot, CommandNumber: e.CommandNumber, ClientTick: e.ClientTick, ServerTickExecuted: e.ServerTickExecuted, SteamID: steam(e.Player)}
	if e.Command == nil {
		return r, errors.New("nil reconstructed UserCmd")
	}
	if e.Player != nil {
		r.PlayerName = ptr(e.Player.Name)
		r.Team = ptr(int32(e.Player.Team))
		if e.Player.PlayerPawnEntity() != nil {
			r.Alive = ptr(e.Player.IsAlive())
		}
	}
	c := e.Command
	b := c.Base
	r.BasePresent = b != nil
	r.ButtonsPresent = b != nil && b.ButtonsPb != nil
	r.ViewanglesPresent = b != nil && b.Viewangles != nil
	r.Attack1StartHistoryIndex = c.Attack1StartHistoryIndex
	r.Attack2StartHistoryIndex = c.Attack2StartHistoryIndex
	if b != nil {
		r.Forwardmove = b.Forwardmove
		r.Leftmove = b.Leftmove
		r.Upmove = b.Upmove
		r.MousedxRaw = b.Mousedx
		r.MousedyRaw = b.Mousedy
		r.Weaponselect = b.Weaponselect
		r.Impulse = b.Impulse
		r.PawnEntityHandle = b.PawnEntityHandle
		if b.ButtonsPb != nil {
			r.Buttonstate1 = b.ButtonsPb.Buttonstate1
			r.Buttonstate2 = b.ButtonsPb.Buttonstate2
			r.Buttonstate3 = b.ButtonsPb.Buttonstate3
		}
		if b.Viewangles != nil {
			r.ViewPitch = b.Viewangles.X
			r.ViewYaw = b.Viewangles.Y
			r.ViewRoll = b.Viewangles.Z
		}
		for _, s := range b.SubtickMoves {
			r.SubtickMoves = append(r.SubtickMoves, schema.Subtick{Button: s.Button, Pressed: s.Pressed, When: s.When, AnalogForwardDelta: s.AnalogForwardDelta, AnalogLeftDelta: s.AnalogLeftDelta, PitchDelta: s.PitchDelta, YawDelta: s.YawDelta})
		}
	}
	for _, h := range c.InputHistory {
		x := schema.InputHistory{RenderTickCount: h.RenderTickCount, RenderTickFraction: h.RenderTickFraction, PlayerTickCount: h.PlayerTickCount, PlayerTickFraction: h.PlayerTickFraction, FrameNumber: h.FrameNumber}
		if h.ViewAngles != nil {
			x.ViewPitch = h.ViewAngles.X
			x.ViewYaw = h.ViewAngles.Y
		}
		r.InputHistory = append(r.InputHistory, x)
	}
	var err error
	r.CommandProtobuf, err = proto.MarshalOptions{Deterministic: true}.Marshal(c)
	return r, err
}

func boolProp(entity st.Entity, name string) *bool {
	if entity == nil {
		return nil
	}
	v, ok := entity.PropertyValue(name)
	if !ok {
		return nil
	}
	return ptr(v.BoolVal())
}
func intProp(entity st.Entity, name string) *int32 {
	if entity == nil {
		return nil
	}
	v, ok := entity.PropertyValue(name)
	if !ok {
		return nil
	}
	return ptr(int32(v.Int()))
}
func floatProp(entity st.Entity, name string) *float64 {
	if entity == nil {
		return nil
	}
	v, ok := entity.PropertyValue(name)
	if !ok {
		return nil
	}
	return ptr(float64(v.Float()))
}
func paused(entity st.Entity) *bool {
	// CS2 exposes these five flags through m_pGameRules. Missing/untyped
	// properties are unknown, never evidence that the match is unpaused.
	if entity == nil {
		return nil
	}
	result := false
	for _, name := range []string{"m_bGamePaused", "m_bMatchWaitingForResume", "m_bTerroristTimeOutActive", "m_bCTTimeOutActive", "m_bTechnicalTimeOut"} {
		v, exists := entity.PropertyValue("m_pGameRules." + name)
		value, typed := v.Any.(bool)
		if !exists || !typed {
			return nil
		}
		result = result || value
	}
	return &result
}

func magazineAmmo(entity st.Entity) *int32 {
	if entity == nil {
		return nil
	}
	v, exists := entity.PropertyValue("m_iClip1")
	count, typed := v.Any.(uint32)
	if !exists || !typed || count > 0x7fffffff {
		return nil
	}
	// The recorded CS2 property already contains the magazine count. The
	// pinned library helper subtracts one; native capture and the raw demo
	// both show 20 while that helper returns 19 for the same Glock snapshot.
	return ptr(int32(count))
}

func StateRow(p *common.Player) schema.PlayerState {
	r := schema.PlayerState{SteamID: steam(p), PlayerSlot: int32(p.EntityID - 1), UserID: int32(p.UserID), SpectatorUserID: int32(p.UserID & 255), PlayerName: p.Name, Team: int32(p.Team), Alive: p.IsAlive()}
	pawn := p.PlayerPawnEntity()
	if pawn == nil {
		return r
	}
	pos := pawn.Position()
	r.PositionX = ptr(pos.X)
	r.PositionY = ptr(pos.Y)
	r.PositionZ = ptr(pos.Z)
	r.Health = intProp(pawn, "m_iHealth")
	r.Armor = intProp(pawn, "m_ArmorValue")
	r.Helmet = boolProp(pawn, "m_pItemServices.m_bHasHelmet")
	r.Scoped = boolProp(pawn, "m_bIsScoped")
	r.Walking = boolProp(pawn, "m_bIsWalking")
	r.VelocityX = floatProp(pawn, "m_vecVelocity.m_vecX")
	r.VelocityY = floatProp(pawn, "m_vecVelocity.m_vecY")
	r.VelocityZ = floatProp(pawn, "m_vecVelocity.m_vecZ")
	if v, ok := pawn.PropertyValue("m_angEyeAngles"); ok {
		vec := v.R3Vec()
		r.ViewPitch = ptr(float32(vec.X))
		r.ViewYaw = ptr(float32(vec.Y))
	}
	if v, ok := pawn.PropertyValue("m_flFlashDuration"); ok {
		r.FlashDuration = ptr(v.Float())
	}
	if _, ok := pawn.PropertyValue("m_fFlags"); ok {
		flags := p.Flags()
		r.OnGround = ptr(flags.OnGround())
		r.Crouching = ptr(flags.Ducking())
	}
	if w := p.ActiveWeapon(); w != nil {
		r.ActiveWeapon = ptr(w.Type.String())
		if w.Class() != common.EqClassGrenade && w.Class() != common.EqClassEquipment {
			r.AmmoClip = magazineAmmo(w.Entity)
		}
	}
	return r
}
