package demo

import (
	"os"
	"path/filepath"
	"testing"

	"chicken-farmer/tools/usercmd-extractor/internal/schema"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	"github.com/parquet-go/parquet-go"
	"google.golang.org/protobuf/proto"
)

func TestCommandPreservesAbsenceZeroAndFullProto(t *testing.T) {
	command := &msg.CSGOUserCmdPB{Base: &msg.CBaseUserCmdPB{Mousedx: ptr(int32(0)), ButtonsPb: &msg.CInButtonStatePB{Buttonstate1: ptr(uint64(1) << 40)}, SubtickMoves: []*msg.CSubtickMoveStep{{Pressed: ptr(false), When: ptr(float32(0))}}}, InputHistory: []*msg.CSGOInputHistoryEntryPB{{FrameNumber: ptr(int32(123)), ViewAngles: &msg.CMsgQAngle{Y: ptr(float32(-179))}}}}
	// Preserve fields not yet represented in the flat schema as well.
	command.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x07})
	row, err := CommandRow(events.UserCmd{PlayerSlot: 2, CommandNumber: 3, Command: command})
	if err != nil {
		t.Fatal(err)
	}
	if row.MousedxRaw == nil || *row.MousedxRaw != 0 || row.MousedyRaw != nil || row.SteamID != nil {
		t.Fatal("missing values collapsed into zero")
	}
	if !row.BasePresent || !row.ButtonsPresent || row.ViewanglesPresent {
		t.Fatal("protobuf parent presence was not preserved")
	}
	dir := t.TempDir()
	table, err := newTable[schema.UserCmd](dir, "usercmd.parquet", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if err = table.write(row); err != nil {
		t.Fatal(err)
	}
	if err = table.Close(); err != nil {
		t.Fatal(err)
	}
	rows, err := parquet.ReadFile[schema.UserCmd](filepath.Join(dir, "usercmd.parquet"))
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].MousedyRaw != nil || rows[0].SubtickMoves[0].Pressed == nil || *rows[0].SubtickMoves[0].Pressed {
		t.Fatal("nullable nested parquet round trip failed")
	}
	if *rows[0].Buttonstate1 != uint64(1)<<40 {
		t.Fatal("64-bit buttons truncated")
	}
	decoded := new(msg.CSGOUserCmdPB)
	if err = proto.Unmarshal(rows[0].CommandProtobuf, decoded); err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(command, decoded) {
		t.Fatal("protobuf was not preserved")
	}
}

func TestBadDemoCannotPublishCompletedOutput(t *testing.T) {
	dir := t.TempDir()
	input := filepath.Join(dir, "bad.dem")
	if err := os.WriteFile(input, []byte("not a demo"), 0600); err != nil {
		t.Fatal(err)
	}
	id, err := HashFile(input)
	if err != nil {
		t.Fatal(err)
	}
	_, err = Extract(Options{Input: input, Output: filepath.Join(dir, "parsed")})
	if err == nil {
		t.Fatal("corrupt input accepted")
	}
	if _, err = os.Stat(filepath.Join(dir, "parsed", id)); !os.IsNotExist(err) {
		t.Fatal("failed artifact published")
	}
}

func TestExistingDatasetIsImmutable(t *testing.T) {
	dir := t.TempDir()
	input := filepath.Join(dir, "same.dem")
	os.WriteFile(input, []byte("unchanged"), 0600)
	id, _ := HashFile(input)
	output := filepath.Join(dir, "parsed", id)
	os.MkdirAll(output, 0700)
	marker := filepath.Join(output, "marker")
	os.WriteFile(marker, []byte("original"), 0600)
	if _, err := Extract(Options{Input: input, Output: filepath.Join(dir, "parsed")}); err == nil {
		t.Fatal("overwrote existing output")
	}
	b, _ := os.ReadFile(marker)
	if string(b) != "original" {
		t.Fatal("existing output modified")
	}
}
