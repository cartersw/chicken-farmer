package main

// This adapter deliberately produces no frame-to-tick table. The upstream
// startmovie schedule is not evidence of which demo tick each encoded frame shows.
import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const chickenRendererCommit = "02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22"
const chickenCS2Patch = "1.41.6.5"

type chickenJob struct {
	SchemaVersion   int    `json:"schema_version"`
	DemoID          string `json:"demo_id"`
	DemoPath        string `json:"demo_path"`
	ClipID          string `json:"clip_id"`
	RoundID         int    `json:"round_id"`
	SteamID         string `json:"steam_id"`
	PlayerSlot      int    `json:"player_slot"`
	SpectatorUserID *int   `json:"spectator_user_id"`
	StartDemoTick   int    `json:"start_demo_tick"`
	EndDemoTick     int    `json:"end_demo_tick"`
	FPS             int    `json:"fps"`
	Width           int    `json:"width"`
	Height          int    `json:"height"`
	Map             string `json:"map"`
	Team            int    `json:"team"`
	Clock           string `json:"timing_clock"`
}

func (j chickenJob) validate() error {
	if j.SchemaVersion != 1 || j.Clock != "demo_tick" {
		return fmt.Errorf("schema_version=1 and timing_clock=demo_tick are required")
	}
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(j.DemoID) {
		return fmt.Errorf("demo_id must be SHA-256 hex")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9-]{1,128}$`).MatchString(j.ClipID) {
		return fmt.Errorf("clip_id must contain only letters, digits, and hyphens (upstream splits on underscore)")
	}
	if j.DemoPath == "" || strings.ContainsAny(j.DemoPath, "\r\n\"") {
		return fmt.Errorf("invalid demo_path")
	}
	if _, err := strconv.ParseUint(j.SteamID, 10, 64); err != nil || j.SteamID == "0" {
		return fmt.Errorf("steam_id must be a nonzero decimal string")
	}
	if j.RoundID < 1 || j.PlayerSlot < 0 || j.PlayerSlot > 255 || j.SpectatorUserID == nil || *j.SpectatorUserID < 0 || *j.SpectatorUserID > 255 {
		return fmt.Errorf("round/player identity and spectator_user_id are required")
	}
	if j.StartDemoTick < firstActionsTick+7 || j.EndDemoTick <= j.StartDemoTick+2 || j.EndDemoTick > 2147483500 {
		return fmt.Errorf("require start_demo_tick >= 71, end > start+2, end <= 2147483500; early clips must be explicitly trimmed")
	}
	if (j.FPS != 32 && j.FPS != 64) || j.Width < 320 || j.Width > 7680 || j.Height < 180 || j.Height > 4320 || j.Width%2 != 0 || j.Height%2 != 0 {
		return fmt.Errorf("require 32/64 fps and even resolution between 320x180 and 7680x4320")
	}
	return nil
}

func chickenSHA(path string) (string, error) {
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

func chickenPreflight() (string, error) {
	if runtime.GOOS != "linux" {
		return "", fmt.Errorf("render execution requires Linux: pinned upstream launch, plugin build, and VAAPI encoder do not support native Windows")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	base := filepath.Join(home, ".steam/steam/steamapps/common/Counter-Strike Global Offensive/game")
	info, err := os.ReadFile(filepath.Join(base, "csgo/steam.inf"))
	if err != nil {
		return "", err
	}
	version := ""
	for _, line := range strings.Split(string(info), "\n") {
		if strings.HasPrefix(line, "PatchVersion=") {
			version = strings.TrimSpace(strings.TrimPrefix(line, "PatchVersion="))
		}
	}
	if version != chickenCS2Patch {
		return "", fmt.Errorf("installed CS2 patch %q differs from upstream plugin target %s; compatibility must be established before updating the pin", version, chickenCS2Patch)
	}
	if _, err = os.Stat(filepath.Join(base, "csgo/dem-render/bin/linuxsteamrt64/libserver.so")); err != nil {
		return "", fmt.Errorf("renderer plugin is not installed: %w", err)
	}
	gameinfo, err := os.ReadFile(filepath.Join(base, "csgo/gameinfo.gi"))
	if err != nil {
		return "", err
	}
	if !strings.Contains(string(gameinfo), "csgo/dem-render") {
		return "", fmt.Errorf("plugin search path missing; install plugin on dedicated worker first")
	}
	for _, cmd := range []string{"steam", "pgrep", "ffmpeg", "ffprobe"} {
		if _, err := exec.LookPath(cmd); err != nil {
			return "", err
		}
	}
	if exec.Command("pgrep", "-x", "cs2").Run() == nil {
		return "", fmt.Errorf("CS2 is already running; dedicate this worker before rendering")
	}
	if _, err := os.Stat("/dev/dri/renderD128"); err != nil {
		return "", fmt.Errorf("upstream AMD VAAPI render device missing: %w", err)
	}
	if os.Getenv("DISPLAY") == "" && os.Getenv("WAYLAND_DISPLAY") == "" {
		return "", fmt.Errorf("Linux graphical display required")
	}
	if exec.Command("pgrep", "-x", "steam").Run() != nil {
		return "", fmt.Errorf("Steam must already be running")
	}
	return base, nil
}

func chickenWriteJSON(path string, data any) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	err = enc.Encode(data)
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}

func chickenRun(args []string) error {
	fs := flag.NewFlagSet("dem-render job", flag.ContinueOnError)
	spec := fs.String("spec", "", "One render job JSON (not JSONL)")
	output := fs.String("output", "", "New or empty directory for this clip")
	execute := fs.Bool("execute", false, "Launch local replay on configured Linux worker; default only prints plan")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *spec == "" || *output == "" || fs.NArg() != 0 {
		return fmt.Errorf("usage: dem-render job --spec job.json --output output/clip [--execute]")
	}
	data, err := os.ReadFile(*spec)
	if err != nil {
		return err
	}
	var job chickenJob
	if err := json.Unmarshal(data, &job); err != nil {
		return err
	}
	if err := job.validate(); err != nil {
		return err
	}
	demo, err := filepath.Abs(job.DemoPath)
	if err != nil {
		return err
	}
	if err := validateCS2DemoFile(demo); err != nil {
		return err
	}
	out, err := filepath.Abs(*output)
	if err != nil {
		return err
	}
	manifest := map[string]any{
		"schema_version": 1, "clip_id": job.ClipID, "demo_id": job.DemoID, "round_id": job.RoundID,
		"steam_id": job.SteamID, "player_slot": job.PlayerSlot, "spectator_user_id": *job.SpectatorUserID,
		"fps": job.FPS, "width": job.Width, "height": job.Height, "video_uri": job.ClipID + ".mp4",
		"requested_start_demo_tick": job.StartDemoTick, "requested_end_demo_tick": job.EndDemoTick,
		"renderer_commit": chickenRendererCommit, "renderer_profile": "chicken-render-v1-hud-viewmodel",
		"plugin_commit": chickenRendererCommit, "plugin_target_patch": chickenCS2Patch,
		"timing_clock": "demo_tick", "timing_status": "unverified", "pov_verified": false,
		"capture_method": "cs2-startmovie-tga", "render_status": "planned",
		"timing_note": "Requested boundaries are scheduling inputs, not per-frame measurements. Requires capture timing validation before alignment.",
	}
	if !*execute {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(manifest)
	}
	base, err := chickenPreflight()
	if err != nil {
		return err
	}
	digest, err := chickenSHA(demo)
	if err != nil {
		return err
	}
	if digest != job.DemoID {
		return fmt.Errorf("demo bytes do not match demo_id")
	}
	if entries, err := os.ReadDir(out); err == nil && len(entries) != 0 {
		return fmt.Errorf("output directory must be empty: %s", out)
	} else if err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.MkdirAll(out, 0755); err != nil {
		return err
	}
	// Stage a symlink so upstream's temporary .dem.json never touches source-demo storage.
	stagedDemo := filepath.Join(out, "input.dem")
	if err := os.Symlink(demo, stagedDemo); err != nil {
		return err
	}
	defer os.Remove(stagedDemo)
	priorDir, err := os.Getwd()
	if err != nil {
		return err
	}
	if err := os.Chdir(out); err != nil {
		return err
	}
	defer os.Chdir(priorDir)
	manifestPath := filepath.Join(out, job.ClipID+".render.json")
	manifest["render_status"] = "rendering"
	manifest["started_at"] = time.Now().UTC().Format(time.RFC3339)
	manifest["cs2_build"] = chickenCS2Patch
	if err := chickenWriteJSON(manifestPath, manifest); err != nil {
		return err
	}
	steamID, _ := strconv.ParseUint(job.SteamID, 10, 64)
	interval := playerRoundInfo{UUID: job.ClipID, SteamId: steamID, UserId: *job.SpectatorUserID, RoundNumber: job.RoundID, SpawnTick: job.StartDemoTick, DeathTick: job.EndDemoTick + 1, Duration: float64(job.EndDemoTick-job.StartDemoTick) / 64, VideoFile: job.ClipID + ".mp4"}
	// A nil PlayerRounds list prevents guessed upstream Parquet action labels.
	config := RenderConfig{DemoPath: stagedDemo, DemofileName: strings.TrimSuffix(filepath.Base(demo), filepath.Ext(demo)), Width: job.Width, Height: job.Height, Framerate: job.FPS, OutputDir: out}
	count, _, renderErr := RenderIntervals([]playerRoundInfo{interval}, config)
	for name, path := range map[string]string{"plugin.log": filepath.Join(base, "bin/linuxsteamrt64/dem-render.log"), "console.log": filepath.Join(base, "csgo/dem-render/console.log")} {
		if logData, readErr := os.ReadFile(path); readErr == nil {
			if writeErr := os.WriteFile(filepath.Join(out, name), logData, 0644); writeErr != nil && renderErr == nil {
				renderErr = writeErr
			}
		}
	}
	manifest["render_status"] = "failed"
	if renderErr == nil && count != 1 {
		renderErr = fmt.Errorf("expected exactly one encoded clip, got %d", count)
	}
	if renderErr == nil {
		video := filepath.Join(out, job.ClipID+".mp4")
		videoSHA, hashErr := chickenSHA(video)
		if hashErr != nil {
			renderErr = hashErr
		} else {
			manifest["video_sha256"] = videoSHA
		}
		probe, probeErr := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,width,height", "-of", "json", video).Output()
		if probeErr != nil {
			renderErr = probeErr
		} else {
			var frames struct {
				Frames []json.RawMessage `json:"frames"`
			}
			if err := json.Unmarshal(probe, &frames); err != nil {
				renderErr = err
			} else if len(frames.Frames) == 0 {
				renderErr = fmt.Errorf("encoded video has no decoded frames")
			} else {
				manifest["num_frames"] = len(frames.Frames)
				if err := os.WriteFile(filepath.Join(out, job.ClipID+".pts.json"), probe, 0644); err != nil {
					renderErr = err
				}
			}
		}
		if version, versionErr := exec.Command("ffmpeg", "-version").Output(); versionErr == nil {
			manifest["ffmpeg_version"] = strings.SplitN(string(version), "\n", 2)[0]
		}
	}
	if renderErr != nil {
		manifest["error"] = renderErr.Error()
	} else {
		manifest["render_status"] = "video_ready_timing_unverified"
	}
	manifest["finished_at"] = time.Now().UTC().Format(time.RFC3339)
	if err := chickenWriteJSON(manifestPath, manifest); err != nil {
		return err
	}
	return renderErr
}

func init() {
	if len(os.Args) > 1 && os.Args[1] == "job" {
		if err := chickenRun(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "render job failed:", err)
			os.Exit(1)
		}
		os.Exit(0)
	}
}
