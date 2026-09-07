package main

import (
	"bytes"
	"testing"

	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	"google.golang.org/protobuf/proto"
)

func TestRecordDoesNotRetainMutableParserScalar(t *testing.T) {
	value := &msg.CNETMsg_Tick{Tick: proto.Uint32(100)}
	r, err := messageRecord(1, 1, 1, value)
	if err != nil {
		t.Fatal(err)
	}
	*value.Tick = 200
	if *r.Tick != 100 {
		t.Fatal("retained evidence changed when parser message was reused")
	}
}

func TestWindowsRejectAmbiguityAndBoundScope(t *testing.T) {
	for _, value := range []string{"", "2:1", "-1:2", "1:3,2:4", "1:1", "0:20001", "a:b"} {
		if _, err := parseWindows(value); err == nil {
			t.Fatalf("accepted %q", value)
		}
	}
	windows, err := parseWindows("100:110,1:3")
	if err != nil {
		t.Fatal(err)
	}
	if !contains(windows, 1) || contains(windows, 3) || !contains(windows, 109) || contains(windows, 110) {
		t.Fatal("half-open scope failure")
	}
}

func TestNetworkTickPreservesIndependentClockAndPresence(t *testing.T) {
	value := &msg.CNETMsg_Tick{Tick: proto.Uint32(16705), HltvReplayFlags: proto.Uint32(1)}
	r, err := messageRecord(5, 6002, 6120, value)
	if err != nil {
		t.Fatal(err)
	}
	if r.DemoTick != 6002 || *r.Tick != 16705 {
		t.Fatal("network epoch was replaced with packet clock")
	}
	decoded := &msg.CNETMsg_Tick{}
	if err := proto.Unmarshal(r.Raw, decoded); err != nil || !proto.Equal(value, decoded) {
		t.Fatal("protobuf evidence does not round trip")
	}
	absent, err := messageRecord(6, 6003, 6121, &msg.CNETMsg_Tick{})
	if err != nil || absent.Tick != nil {
		t.Fatal("missing network tick was invented")
	}
}

func TestCommandEnvelopeDoesNotReconstructOrDiscardDelta(t *testing.T) {
	value := &msg.CMsgServerUserCmd{CmdNumber: proto.Int32(55), PlayerSlot: proto.Int32(9), ServerTickExecuted: proto.Int32(16705), DeltaData: []byte{0x80, 0x01, 0x02}}
	r, err := messageRecord(6, 6002, 6120, value)
	if err != nil {
		t.Fatal(err)
	}
	decoded := &msg.CMsgServerUserCmd{}
	if err := proto.Unmarshal(r.Raw, decoded); err != nil || !proto.Equal(value, decoded) {
		t.Fatal("envelope changed")
	}
	if !bytes.Equal(decoded.DeltaData, value.DeltaData) || r.Client != nil || *r.Executed != 16705 {
		t.Fatal("missing defaults or delta payload changed")
	}
}

func TestSnapshotTickIsSeparateFromNetworkAndDemoClock(t *testing.T) {
	value := &msg.CSVCMsg_PacketEntities{ServerTick: proto.Uint32(16704), EntityData: []byte{1, 2, 3}}
	r, err := messageRecord(9, 6002, 6002, value)
	if err != nil {
		t.Fatal(err)
	}
	if r.Tick != nil || *r.SnapshotTick != 16704 || r.DemoTick != 6002 {
		t.Fatal("distinct snapshot clock lost")
	}
	decoded := &msg.CSVCMsg_PacketEntities{}
	if err := proto.Unmarshal(r.Raw, decoded); err != nil || !proto.Equal(value, decoded) {
		t.Fatal("snapshot protobuf did not round trip")
	}
}
