// cs2-clocks preserves network tick messages and command-envelope clocks.
// It never reconstructs inputs, calibrates from correlations, or changes demos.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"chicken-farmer/tools/usercmd-extractor/internal/demo"
	dem "github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/events"
	"github.com/markus-wa/demoinfocs-golang/v6/pkg/demoinfocs/msg"
	"google.golang.org/protobuf/proto"
)

const maxRecords = 200000
const maxBytes = 128 * 1024 * 1024

type window struct {
	Start int `json:"start_demo_tick"`
	End   int `json:"end_demo_tick"`
}

type record struct {
	Index        int     `json:"clock_event_index"`
	DemoTick     int     `json:"demo_tick"`
	DemoFrame    int     `json:"demo_frame"`
	Type         string  `json:"message_type"`
	Tick         *uint32 `json:"network_tick,omitempty"`
	SnapshotTick *uint32 `json:"snapshot_server_tick,omitempty"`
	Slot         *int32  `json:"player_slot,omitempty"`
	Command      *int32  `json:"command_number,omitempty"`
	Executed     *int32  `json:"server_tick_executed,omitempty"`
	Client       *int32  `json:"client_tick,omitempty"`
	Raw          []byte  `json:"protobuf_reencoded"`
	Hash         string  `json:"protobuf_sha256"`
}

func parseWindows(value string) ([]window, error) {
	var result []window
	total := 0
	for _, item := range strings.Split(value, ",") {
		parts := strings.Split(item, ":")
		if len(parts) != 2 {
			return nil, fmt.Errorf("--windows requires START:END[,START:END], with exclusive ends")
		}
		start, a := strconv.Atoi(parts[0])
		end, b := strconv.Atoi(parts[1])
		if a != nil || b != nil || start < 0 || end <= start || end > 10000000 {
			return nil, fmt.Errorf("invalid clock window %q", item)
		}
		result = append(result, window{start, end})
		total += end - start
	}
	if len(result) > 32 || total > 20000 {
		return nil, fmt.Errorf("clock windows exceed 32 windows or 20000 selected ticks")
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Start < result[j].Start })
	for i := 1; i < len(result); i++ {
		if result[i].Start < result[i-1].End {
			return nil, fmt.Errorf("clock windows overlap")
		}
	}
	return result, nil
}

func contains(windows []window, tick int) bool {
	for _, w := range windows {
		if tick >= w.Start && tick < w.End {
			return true
		}
	}
	return false
}

func copyScalar[T ~int32 | ~uint32](value *T) *T {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func messageRecord(index, tick, frame int, value proto.Message) (record, error) {
	raw, err := (proto.MarshalOptions{Deterministic: true}).Marshal(value)
	if err != nil {
		return record{}, err
	}
	if len(raw) > 4*1024*1024 {
		return record{}, fmt.Errorf("oversized clock evidence message")
	}
	hash := sha256.Sum256(raw)
	r := record{Index: index, DemoTick: tick, DemoFrame: frame, Raw: raw, Hash: hex.EncodeToString(hash[:])}
	switch value := value.(type) {
	case *msg.CNETMsg_Tick:
		r.Type = "CNETMsg_Tick"
		r.Tick = copyScalar(value.Tick)
	case *msg.CSVCMsg_PacketEntities:
		r.Type = "CSVCMsg_PacketEntities"
		r.SnapshotTick = copyScalar(value.ServerTick)
	case *msg.CMsgServerUserCmd:
		r.Type = "CMsgServerUserCmd"
		r.Slot = copyScalar(value.PlayerSlot)
		r.Command = copyScalar(value.CmdNumber)
		r.Executed = copyScalar(value.ServerTickExecuted)
		r.Client = copyScalar(value.ClientTick)
	default:
		return record{}, fmt.Errorf("unsupported clock evidence type")
	}
	return r, nil
}

func run(input, out, requested string) error {
	windows, err := parseWindows(requested)
	if err != nil {
		return err
	}
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
	rows := []record{}
	index, retainedBytes, netCount, commandCount, snapshotCount := 0, 0, 0, 0, 0
	warnings := map[string]int{}
	var evidenceErr error
	add := func(value proto.Message) {
		index++
		if evidenceErr != nil || !contains(windows, p.GameState().IngameTick()) {
			return
		}
		r, err := messageRecord(index, p.GameState().IngameTick(), p.CurrentFrame(), value)
		if err != nil {
			evidenceErr = err
			return
		}
		retainedBytes += len(r.Raw)
		if len(rows) >= maxRecords || retainedBytes > maxBytes {
			evidenceErr = fmt.Errorf("clock evidence exceeded bounded output")
			return
		}
		rows = append(rows, r)
		if r.Type == "CNETMsg_Tick" {
			netCount++
		} else if r.Type == "CSVCMsg_PacketEntities" {
			snapshotCount++
		} else {
			commandCount++
		}
	}
	p.RegisterNetMessageHandler(func(value *msg.CNETMsg_Tick) { add(value) })
	p.RegisterNetMessageHandler(func(value *msg.CSVCMsg_PacketEntities) { add(value) })
	p.RegisterNetMessageHandler(func(value *msg.CSVCMsg_UserCommands) {
		for _, command := range value.Commands {
			if command == nil {
				evidenceErr = fmt.Errorf("nil command envelope")
				return
			}
			add(command)
		}
	})
	warning := func(e events.ParserWarn) {
		warnings[strconv.Itoa(int(e.Type))]++
		if e.Type != events.WarnTypeBombsiteUnknown && e.Type != events.WarnTypeUnknownGrenadeModel {
			evidenceErr = fmt.Errorf("clock evidence invalidated by parser warning %d: %s", e.Type, e.Message)
		}
	}
	p.RegisterEventHandler(warning)
	p.RegisterNetMessageHandler(warning)
	lastTick := -1
	for {
		more, err := p.ParseNextFrame()
		if err != nil {
			return err
		}
		if evidenceErr != nil {
			return evidenceErr
		}
		lastTick = p.GameState().IngameTick()
		if lastTick >= windows[len(windows)-1].End {
			break
		}
		if !more {
			return fmt.Errorf("demo ended before requested window coverage")
		}
	}
	if netCount == 0 || commandCount == 0 || snapshotCount == 0 {
		return fmt.Errorf("requested windows lack network tick, snapshot, or command-envelope evidence")
	}
	after, err := demo.HashFile(input)
	if err != nil {
		return err
	}
	if after != id {
		return fmt.Errorf("demo changed during clock extraction")
	}
	absolute, err := filepath.Abs(input)
	if err != nil {
		return err
	}
	report := map[string]any{
		"schema_version": 1, "producer": "cs2-clocks-v1", "status": "complete", "demo_id": id,
		"source_demo_path": absolute, "source_demo_sha256": id, "parser": "demoinfocs-golang/v6", "parser_version": "v6.0.0-alpha.0",
		"scope": "complete_requested_windows_only", "windows": windows, "parsed_through_demo_tick": lastTick,
		"tick_rate": p.TickRate(), "warnings": warnings, "warning_policy": "network-clock-envelope-v1", "evidence_loss_warnings": 0,
		"protobuf_encoding": "deterministic_reserialization_of_decoded_messages_not_original_wire_offsets",
		"demo_tick_source":  "demo_command_header_via_parser_ingame_tick", "event_order": "parser_dispatch_order_for_tick_and_command_envelopes",
		"network_tick_records": netCount, "command_envelope_records": commandCount, "snapshot_records": snapshotCount, "records": rows,
		"training_ready": false, "execution_render_epoch_verified": false,
	}
	data, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return err
	}
	if len(data) > 256*1024*1024 {
		return fmt.Errorf("encoded clock report exceeds 256 MiB")
	}
	if err = os.MkdirAll(filepath.Dir(out), 0755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(out), ".cs2-clocks-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	_, err = temporary.Write(append(data, '\n'))
	if err == nil {
		err = temporary.Sync()
	}
	closeErr := temporary.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Link(temporary.Name(), out); err != nil {
		return err
	}
	fmt.Printf("clock evidence: %d NET_Tick messages, %d command envelopes -> %s\n", netCount, commandCount, out)
	return nil
}

func main() {
	input := flag.String("input", "", "immutable source .dem")
	out := flag.String("out", "", "new clock evidence JSON")
	windows := flag.String("windows", "", "bounded START:END[,START:END] demo tick windows; exclusive ends")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(1)
	}
	if err := run(*input, *out, *windows); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
