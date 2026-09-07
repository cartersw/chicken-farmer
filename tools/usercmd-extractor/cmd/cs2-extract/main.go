package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"chicken-farmer/tools/usercmd-extractor/internal/demo"
	"chicken-farmer/tools/usercmd-extractor/internal/validate"
)

type config struct {
	Input       string `json:"input"`
	Output      string `json:"output"`
	MatchID     string `json:"match_id"`
	MaxDemoTick int64  `json:"max_demo_tick"`
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, nil)))
	if err := run(os.Args[1:]); err != nil {
		slog.Error("failed", "error", err.Error())
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 || args[0] == "--help" || args[0] == "help" {
		fmt.Println("cs2-extract extract --input <demo.dem|directory> --out <parsed/v1> [--match-id ID] [--config file.json] [--max-demo-tick N]\ncs2-extract validate --parsed <parsed/v1/demo-id> [--json report.json]\nExisting outputs are immutable. --max-demo-tick produces a partial diagnostic artifact.")
		return nil
	}
	switch args[0] {
	case "extract":
		fs := flag.NewFlagSet("extract", flag.ContinueOnError)
		c := config{Output: "data/parsed/v2-audited"}
		var cfg string
		fs.StringVar(&c.Input, "input", "", "demo file or directory (recursive)")
		fs.StringVar(&c.Output, "out", c.Output, "output root; demo SHA256 subdirectories")
		fs.StringVar(&c.MatchID, "match-id", "", "shared match/series identity for dataset splits")
		fs.StringVar(&cfg, "config", "", "JSON config; explicit flags override values")
		fs.Int64Var(&c.MaxDemoTick, "max-demo-tick", 0, "stop at this demo tick for a diagnostic extraction")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if fs.NArg() != 0 {
			return errors.New("unexpected positional arguments")
		}
		if cfg != "" {
			data, err := os.ReadFile(cfg)
			if err != nil {
				return err
			}
			configured := config{Output: "data/parsed/v2-audited"}
			if err = json.Unmarshal(data, &configured); err != nil {
				return err
			}
			fs.Visit(func(f *flag.Flag) {
				switch f.Name {
				case "input":
					configured.Input = c.Input
				case "out":
					configured.Output = c.Output
				case "match-id":
					configured.MatchID = c.MatchID
				case "max-demo-tick":
					configured.MaxDemoTick = c.MaxDemoTick
				}
			})
			c = configured
		}
		if c.Input == "" {
			return errors.New("--input is required")
		}
		if c.MaxDemoTick < 0 {
			return errors.New("max-demo-tick must be nonnegative")
		}
		info, err := os.Stat(c.Input)
		if err != nil {
			return err
		}
		paths := []string{}
		if info.IsDir() {
			err = filepath.WalkDir(c.Input, func(path string, d os.DirEntry, e error) error {
				if e != nil {
					return e
				}
				if !d.IsDir() && strings.EqualFold(filepath.Ext(path), ".dem") {
					paths = append(paths, path)
				}
				return nil
			})
			if err != nil {
				return err
			}
		} else {
			paths = append(paths, c.Input)
		}
		if len(paths) == 0 {
			return errors.New("no .dem files found")
		}
		var failures []error
		for _, path := range paths {
			out, e := demo.Extract(demo.Options{Input: path, Output: c.Output, MatchID: c.MatchID, MaxDemoTick: c.MaxDemoTick})
			if e != nil {
				failures = append(failures, e)
				slog.Error("extraction_failed", "input", path, "error", e)
				continue
			}
			if e = validateOutput(out, filepath.Join(out, "validation.json")); e != nil {
				failures = append(failures, e)
			}
		}
		return errors.Join(failures...)
	case "validate":
		fs := flag.NewFlagSet("validate", flag.ContinueOnError)
		var parsed, report string
		fs.StringVar(&parsed, "parsed", "", "one parsed demo directory")
		fs.StringVar(&report, "json", "", "optional report output path")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if parsed == "" || fs.NArg() != 0 {
			return errors.New("--parsed is required, with no positional arguments")
		}
		return validateOutput(parsed, report)
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func validateOutput(dir, reportPath string) error {
	report, err := validate.Validate(dir)
	if err != nil {
		return err
	}
	fmt.Println(report.String())
	if reportPath != "" {
		if err = demo.WriteJSON(reportPath, report); err != nil {
			return err
		}
	}
	if !report.Passed {
		return fmt.Errorf("data quality checks failed: %s (see validation report)", dir)
	}
	return nil
}
