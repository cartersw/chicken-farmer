package validate

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"chicken-farmer/tools/usercmd-extractor/internal/schema"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	"github.com/parquet-go/parquet-go"
	"google.golang.org/protobuf/proto"
)

func ptr[T any](v T) *T { return &v }

func sampleCommand(t *testing.T, steam uint64, row int64) schema.UserCmd {
	t.Helper()
	c := schema.UserCmd{DemoID: "test-demo", CommandRowID: row, RoundID: 1, SteamID: ptr(steam), PlayerSlot: int32(steam), CommandNumber: int32(row + 1), ServerTickExecuted: int32(row + 100), Alive: ptr(true), MousedxRaw: ptr(int32(row + 1)), MousedyRaw: ptr(int32(-1)), ViewPitch: ptr(float32(row)), ViewYaw: ptr(float32(10 + row)), Buttonstate1: ptr(uint64(1)), Forwardmove: ptr(float32(1))}
	c.SubtickMoves = []schema.Subtick{{When: ptr(float32(0.5)), Button: ptr(uint64(1)), Pressed: ptr(true)}}
	c.InputHistory = []schema.InputHistory{{RenderTickFraction: ptr(float32(0.25)), PlayerTickFraction: ptr(float32(0.75)), ViewPitch: ptr(float32(1)), ViewYaw: ptr(float32(2))}}
	marshalCommand(t, &c)
	return c
}

func marshalCommand(t *testing.T, c *schema.UserCmd) {
	t.Helper()
	c.BasePresent = true
	c.ViewanglesPresent = true
	c.ButtonsPresent = true
	pb := &msg.CSGOUserCmdPB{Base: &msg.CBaseUserCmdPB{
		Mousedx: c.MousedxRaw, Mousedy: c.MousedyRaw, Forwardmove: c.Forwardmove, Leftmove: c.Leftmove, Upmove: c.Upmove,
		Viewangles: &msg.CMsgQAngle{X: c.ViewPitch, Y: c.ViewYaw, Z: c.ViewRoll},
		ButtonsPb:  &msg.CInButtonStatePB{Buttonstate1: c.Buttonstate1, Buttonstate2: c.Buttonstate2, Buttonstate3: c.Buttonstate3},
		Impulse:    c.Impulse, Weaponselect: c.Weaponselect, PawnEntityHandle: c.PawnEntityHandle,
	}, Attack1StartHistoryIndex: c.Attack1StartHistoryIndex, Attack2StartHistoryIndex: c.Attack2StartHistoryIndex}
	for _, s := range c.SubtickMoves {
		pb.Base.SubtickMoves = append(pb.Base.SubtickMoves, &msg.CSubtickMoveStep{Button: s.Button, Pressed: s.Pressed, When: s.When, AnalogForwardDelta: s.AnalogForwardDelta, AnalogLeftDelta: s.AnalogLeftDelta, PitchDelta: s.PitchDelta, YawDelta: s.YawDelta})
	}
	for _, h := range c.InputHistory {
		pb.InputHistory = append(pb.InputHistory, &msg.CSGOInputHistoryEntryPB{RenderTickCount: h.RenderTickCount, RenderTickFraction: h.RenderTickFraction, PlayerTickCount: h.PlayerTickCount, PlayerTickFraction: h.PlayerTickFraction, ViewAngles: &msg.CMsgQAngle{X: h.ViewPitch, Y: h.ViewYaw}, FrameNumber: h.FrameNumber})
	}
	var err error
	c.CommandProtobuf, err = proto.Marshal(pb)
	if err != nil {
		t.Fatal(err)
	}
}

func writeTable[T any](t *testing.T, dir, name string, rows []T) {
	t.Helper()
	f, err := os.Create(filepath.Join(dir, name))
	if err != nil {
		t.Fatal(err)
	}
	writer := parquet.NewGenericWriter[T](f)
	if _, err = writer.Write(rows); err != nil {
		t.Fatal(err)
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err = f.Close(); err != nil {
		t.Fatal(err)
	}
}
func writeManifest(t *testing.T, dir string, m schema.Manifest) {
	t.Helper()
	b, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "manifest.json"), b, 0600); err != nil {
		t.Fatal(err)
	}
}
func fixture(t *testing.T, commands []schema.UserCmd) (string, schema.Manifest) {
	t.Helper()
	dir := t.TempDir()
	writeTable(t, dir, "usercmd.parquet", commands)
	writeTable(t, dir, "player_state.parquet", []schema.PlayerState{
		{DemoID: "test-demo", RoundID: 1, SteamID: ptr(uint64(1)), PlayerSlot: 1, Alive: true, DemoTimeSeconds: ptr(0.0)},
		{DemoID: "test-demo", RoundID: 1, SteamID: ptr(uint64(2)), PlayerSlot: 2, Alive: true, DemoTimeSeconds: ptr(0.0)},
		{DemoID: "test-demo", RoundID: 1, SteamID: ptr(uint64(1)), PlayerSlot: 1, Alive: true, DemoTimeSeconds: ptr(0.5)},
		{DemoID: "test-demo", RoundID: 1, SteamID: ptr(uint64(2)), PlayerSlot: 2, Alive: true, DemoTimeSeconds: ptr(0.5)},
	})
	writeTable(t, dir, "rounds.parquet", []schema.Round{{DemoID: "test-demo", RoundID: 1, StartTick: 0, EndTick: ptr(int64(100)), Map: "de_test"}})
	writeTable(t, dir, "events.parquet", []schema.GameEvent{{DemoID: "test-demo", RoundID: 1, Kind: "weapon_fire", SteamID: ptr(uint64(1)), PlayerSlot: ptr(int32(1))}})
	m := schema.Manifest{DemoID: "test-demo", ParserSchemaVersion: schema.Version, Map: "de_test", TickRate: 64, ParseStatus: "complete", SHA256: strings.Repeat("a", 64), CommandCount: int64(len(commands)), StateCount: 4, RoundCount: 1, Files: map[string]string{}, ButtonMasks: map[string]uint64{"attack1": 1}, ButtonMappingValidation: "validated"}
	for _, name := range []string{"usercmd.parquet", "player_state.parquet", "rounds.parquet", "events.parquet"} {
		sum, err := hashFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		m.Files[name] = sum
	}
	m.EligiblePayloadCount = int64(len(commands))
	writeManifest(t, dir, m)
	return dir, m
}
func hasError(r Report, part string) bool {
	for _, message := range r.Errors {
		if strings.Contains(message, part) {
			return true
		}
	}
	return false
}

func TestValidExtractionAndNullableProtobufIntegrity(t *testing.T) {
	commands := []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1), sampleCommand(t, 1, 2), sampleCommand(t, 2, 3)}
	dir, _ := fixture(t, commands)
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !r.Passed {
		t.Fatal(r.String())
	}
	if r.CommandCount != 4 || r.FilesVerified != 4 || r.HumanPlayers != 2 || r.ProtobufMismatchRows != 0 {
		t.Fatalf("unexpected report: %+v", r)
	}
	p := r.Players[0]
	if p.SubtickRecords != 2 || p.InputHistoryEntries != 2 || p.NonzeroMouse != 2 || p.MouseXVariance <= 0 || p.AttackCommands != 2 || p.WeaponFireEvents != 1 {
		t.Fatalf("unexpected player: %+v", p)
	}
	if p.CommandsPerAliveSecond == nil || *p.CommandsPerAliveSecond != 4 {
		t.Fatalf("invalid alive command rate: %+v", p)
	}
	if _, err = json.Marshal(r); err != nil {
		t.Fatal(err)
	}
}

func TestMissingMouseDoesNotBecomeZero(t *testing.T) {
	missing := sampleCommand(t, 1, 0)
	missing.MousedxRaw = nil
	missing.MousedyRaw = nil
	marshalCommand(t, &missing)
	zero := sampleCommand(t, 1, 1)
	zero.MousedxRaw = ptr(int32(0))
	zero.MousedyRaw = ptr(int32(0))
	marshalCommand(t, &zero)
	dir, _ := fixture(t, []schema.UserCmd{missing, zero, sampleCommand(t, 2, 2)})
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !r.Passed {
		t.Fatal(r.String())
	}
	p := r.Players[0]
	if p.MissingMouse != 1 || p.ZeroMouse != 1 || p.NonzeroMouse != 0 || p.PctMissingMouse != 50 || p.PctZeroMouse != 100 || r.ProtobufMismatchRows != 0 {
		t.Fatalf("missing values conflated with zero: %+v", p)
	}
}

func TestFabricatedZeroAndDroppedHistoryFailIntegrity(t *testing.T) {
	c := sampleCommand(t, 1, 0)
	c.MousedxRaw = nil
	marshalCommand(t, &c)
	c.MousedxRaw = ptr(int32(0))
	d := sampleCommand(t, 2, 1)
	d.InputHistory = nil
	dir, _ := fixture(t, []schema.UserCmd{c, d})
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if r.Passed || r.ProtobufMismatchRows != 2 {
		t.Fatal(r.String())
	}
}

func TestPartialAndReconstructionWarningsFail(t *testing.T) {
	for _, kind := range []string{"partial", "usercmd_delta_decode_failed", "usercmd_baseline_missing", "usercmd_baseline_mismatch"} {
		t.Run(kind, func(t *testing.T) {
			dir, m := fixture(t, []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1)})
			if kind == "partial" {
				m.Partial = true
				m.ParseStatus = "partial"
			} else {
				m.Warnings = map[string]int64{kind: 1}
			}
			writeManifest(t, dir, m)
			r, err := Validate(dir)
			if err != nil {
				t.Fatal(err)
			}
			if r.Passed {
				t.Fatal("accepted incomplete reconstruction")
			}
		})
	}
}

func TestEmptyStreamsFail(t *testing.T) {
	dir, m := fixture(t, nil)
	writeTable(t, dir, "player_state.parquet", []schema.PlayerState{})
	writeTable(t, dir, "rounds.parquet", []schema.Round{})
	m.StateCount = 0
	m.RoundCount = 0
	for _, name := range []string{"player_state.parquet", "rounds.parquet"} {
		m.Files[name], _ = hashFile(filepath.Join(dir, name))
	}
	writeManifest(t, dir, m)
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if r.Passed || !hasError(r, "no reconstructed") || !hasError(r, "no player-state") || !hasError(r, "no competitive") {
		t.Fatal(r.String())
	}
}

func TestHashCountAndPhysicalSchemaFailures(t *testing.T) {
	t.Run("hash and count", func(t *testing.T) {
		dir, m := fixture(t, []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1)})
		m.CommandCount = 999
		m.Files["events.parquet"] = strings.Repeat("0", 64)
		writeManifest(t, dir, m)
		r, err := Validate(dir)
		if err != nil {
			t.Fatal(err)
		}
		if r.Passed || !hasError(r, "command count mismatch") || !hasError(r, "SHA256 differs") {
			t.Fatal(r.String())
		}
	})
	t.Run("schema missing columns", func(t *testing.T) {
		dir, m := fixture(t, []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1)})
		type Incomplete struct {
			DemoID string `parquet:"demo_id,dict"`
		}
		writeTable(t, dir, "usercmd.parquet", []Incomplete{{DemoID: "test-demo"}})
		m.Files["usercmd.parquet"], _ = hashFile(filepath.Join(dir, "usercmd.parquet"))
		writeManifest(t, dir, m)
		r, err := Validate(dir)
		if err != nil {
			t.Fatal(err)
		}
		if r.Passed || !hasError(r, "physical schema differs") {
			t.Fatal(r.String())
		}
	})
	t.Run("corrupt parquet", func(t *testing.T) {
		dir, m := fixture(t, []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1)})
		if err := os.WriteFile(filepath.Join(dir, "events.parquet"), []byte("not parquet"), 0600); err != nil {
			t.Fatal(err)
		}
		m.Files["events.parquet"], _ = hashFile(filepath.Join(dir, "events.parquet"))
		writeManifest(t, dir, m)
		r, err := Validate(dir)
		if err != nil {
			t.Fatal(err)
		}
		if r.Passed || !hasError(r, "events.parquet:") {
			t.Fatal(r.String())
		}
	})
}

func TestInvalidFractionsAndNonfiniteNumbersFail(t *testing.T) {
	c := sampleCommand(t, 1, 0)
	c.SubtickMoves[0].When = ptr(float32(-0.1))
	c.InputHistory[0].PlayerTickFraction = ptr(float32(1.1))
	c.ViewPitch = ptr(float32(math.Inf(1)))
	marshalCommand(t, &c)
	dir, _ := fixture(t, []schema.UserCmd{c, sampleCommand(t, 2, 1)})
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if r.Passed || r.InvalidFractionCount != 2 || r.InvalidNumericRows != 1 {
		t.Fatal(r.String())
	}
	if _, err = json.Marshal(r); err != nil {
		t.Fatalf("report must remain JSON serializable: %v", err)
	}
}

func TestCommandContinuityAndBoundedDuplicateWindow(t *testing.T) {
	p := &player{recent: map[cmdKey]int{}}
	commands := []schema.UserCmd{
		{RoundID: 1, CommandNumber: 1, ServerTickExecuted: 1, Alive: ptr(true)},
		{RoundID: 1, CommandNumber: 4, ServerTickExecuted: 100, Alive: ptr(true)},
		{RoundID: 1, CommandNumber: 4, ServerTickExecuted: 100, Alive: ptr(true)},
		{RoundID: 1, CommandNumber: 3, ServerTickExecuted: 99, Alive: ptr(true)},
		{RoundID: 2, CommandNumber: 1, ServerTickExecuted: 0, Alive: ptr(true)},
	}
	for _, c := range commands {
		p.observeCommand(c, 64, 1)
	}
	if p.CommandNumberGaps != 1 || p.MaxCommandNumberGap != 2 || p.MaxCommandGapTicks != 99 || p.AliveCommandGapsOverOneSecond != 1 || p.DuplicateCommands != 1 || p.ServerTicksOutOfOrder != 1 || p.CommandNumbersOutOfOrder != 1 {
		t.Fatalf("unexpected continuity metrics: %+v", p.PlayerReport)
	}
	for i := int32(0); i < duplicateWindow*2; i++ {
		p.observeCommand(schema.UserCmd{RoundID: 3, CommandNumber: i, ServerTickExecuted: i}, 64, 1)
	}
	if len(p.recent) > duplicateWindow {
		t.Fatalf("unbounded duplicate memory: %d", len(p.recent))
	}
}

func TestMissingManifestReturnsError(t *testing.T) {
	if _, err := Validate(t.TempDir()); err == nil {
		t.Fatal("expected missing manifest error")
	}
}

func TestVerifiedParentPresenceDistinguishesOmission(t *testing.T) {
	c := sampleCommand(t, 1, 0)
	c.MousedxRaw = nil
	c.MousedyRaw = nil
	c.Buttonstate1 = nil
	c.ViewPitch = nil
	c.ViewYaw = nil
	marshalCommand(t, &c)
	d := sampleCommand(t, 2, 1)
	dir, _ := fixture(t, []schema.UserCmd{c, d})
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !r.Passed || r.Players[0].MissingMouse != 1 || r.Players[0].MissingBaseMessages != 0 {
		t.Fatal(r.String())
	}
	c.BasePresent = false
	dir, _ = fixture(t, []schema.UserCmd{c, d})
	r, err = Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if r.Passed || r.ProtobufMismatchRows != 1 {
		t.Fatal("fabricated parent presence accepted")
	}
}

func TestSingleEncodedMouseAxisAndOmittedAngleDefaults(t *testing.T) {
	c := sampleCommand(t, 1, 0)
	c.MousedyRaw = nil
	c.ViewPitch = nil
	c.ViewYaw = nil
	marshalCommand(t, &c)
	d := sampleCommand(t, 1, 1)
	d.MousedyRaw = nil
	d.ViewPitch = nil
	marshalCommand(t, &d)
	dir, _ := fixture(t, []schema.UserCmd{c, d, sampleCommand(t, 2, 2)})
	r, err := Validate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !r.Passed {
		t.Fatal(r.String())
	}
	p := r.Players[0]
	if p.MissingMouse != 2 || p.NonzeroMouse != 2 || p.ViewAngleChanges != 1 {
		t.Fatalf("omitted zero axis hid genuine activity: %+v", p)
	}
}

func TestEligiblePayloadAccountingRejectsSilentLoss(t *testing.T) {
	for _, accounted := range []bool{false, true} {
		t.Run(fmt.Sprint(accounted), func(t *testing.T) {
			dir, m := fixture(t, []schema.UserCmd{sampleCommand(t, 1, 0), sampleCommand(t, 2, 1)})
			m.EligiblePayloadCount = 3
			if accounted {
				m.Warnings = map[string]int64{"usercmd_baseline_missing": 1}
			}
			writeManifest(t, dir, m)
			r, err := Validate(dir)
			if err != nil {
				t.Fatal(err)
			}
			if r.Passed || r.ReconstructionCoveragePct == nil || math.Abs(*r.ReconstructionCoveragePct-200.0/3) > 1e-9 {
				t.Fatal(r.String())
			}
			if accounted {
				if r.RejectedPayloadCount != 1 || r.UnaccountedPayloadCount != 0 || hasError(r, "accounting mismatch") {
					t.Fatalf("rejected envelope was not reconciled: %+v", r)
				}
			} else if r.UnaccountedPayloadCount != 1 || !hasError(r, "accounting mismatch") {
				t.Fatalf("silent envelope loss accepted: %+v", r)
			}
		})
	}
}
