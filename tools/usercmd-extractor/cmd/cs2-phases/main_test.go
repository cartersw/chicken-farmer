package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInvalidDemoPublishesNothing(t *testing.T) {
	dir := t.TempDir()
	input, out := filepath.Join(dir, "bad.dem"), filepath.Join(dir, "phase.json")
	if err := os.WriteFile(input, []byte("invalid demo"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := run(input, out); err == nil {
		t.Fatal("accepted an invalid demo")
	}
	if _, err := os.Stat(out); !os.IsNotExist(err) {
		t.Fatal("published a phase artifact from a failed parse")
	}
}

func TestExistingOutputRemainsByteExact(t *testing.T) {
	out := filepath.Join(t.TempDir(), "phase.json")
	original := []byte("existing evidence\n")
	if err := os.WriteFile(out, original, 0600); err != nil {
		t.Fatal(err)
	}
	if err := run("missing.dem", out); err == nil {
		t.Fatal("accepted an existing output")
	}
	got, err := os.ReadFile(out)
	if err != nil || string(got) != string(original) {
		t.Fatal("changed existing evidence")
	}
}
