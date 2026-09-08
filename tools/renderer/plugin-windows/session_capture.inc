// One forward replay. Commands run only on FRAME_START, observations stay native.
namespace ChickenSession {
constexpr const char* Profile = "cs2-recording-session-v1";
bool loaded = false, enabled = false, failed = false, stopping = false, quitting = false;
json plan, run;
size_t runIndex = 0;
string phase = "startup", error, progressPath, stopPath;
int lastTick = -1, selectedIndex = -1, modeIndex = -1, modeTick = -1;
uint64_t frames = 0;
int frameTick = -1;
ULONGLONG phaseTime = 0, lastPublish = 0, lastStopPoll = 0;

void Publish(int tick, bool force = false) {
    auto now = GetTickCount64();
    if (!force && now - lastPublish < 1000) return;
    lastPublish = now;
    json row = {{"profile", Profile}, {"session_id", plan["session_id"]},
        {"phase", phase}, {"run_index", runIndex}, {"run_count", plan["runs"].size()},
        {"demo_tick", tick}, {"frame_count", frames}, {"error", error},
        {"stopping", stopping}, {"pid", GetCurrentProcessId()}};
    auto temporary = progressPath + ".tmp";
    { std::ofstream file(temporary, std::ios::binary | std::ios::trunc); file << row.dump();
      file.close(); if (!file) PluginError("Cannot persist recording-session progress"); }
    if (!MoveFileExA(temporary.c_str(), progressPath.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        PluginError("Cannot publish recording-session progress");
    if (force) ChickenCapture::Write({{"event", "session_transition"}, {"progress", row}});
}

void Fail(const string& reason) {
    if (failed) return;
    failed = true; stopping = true; error = reason;
    ChickenCapture::Write({{"event", "session_fault"}, {"reason", reason}, {"run_index", runIndex}});
}

void Command(ISource2EngineToClient* engine, const string& command) {
    ChickenCapture::Write({{"event", "session_command"}, {"command", command},
        {"run_index", runIndex}, {"replay_demo_tick", ChickenCapture::Tick()}});
    engine->ExecuteClientCmd(0, command.c_str(), true);
}

void Load() {
    loaded = true;
    const char* path = "";
    for (int i = 0; i + 1 < CommandLine()->ParmCount(); ++i)
        if (!strcmp(CommandLine()->GetParm(i), "-chicken-session-plan")) path = CommandLine()->GetParm(i + 1);
    enabled = path && *path;
    if (!enabled) return;
    std::ifstream file(path);
    if (!file) PluginError("Missing recording-session plan");
    plan = json::parse(file);
    if (plan.value("profile", "") != Profile || plan["runs"].empty() || plan["runs"].size() > 4096)
        PluginError("Invalid recording-session profile or run count");
    progressPath = (std::filesystem::path(path).parent_path() / "session-progress.json").string();
    stopPath = (std::filesystem::path(path).parent_path() / "stop.request").string();
    int prior = -1;
    for (const auto& item : plan["runs"]) {
        int start = item["start_tick"], end = item["end_tick"];
        string prefix = item["prefix"];
        if (start < 199 || end <= start || start <= prior || prefix.empty() ||
            prefix.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-") != string::npos)
            PluginError("Invalid recording-session interval or prefix");
        prior = end;
    }
    phaseTime = GetTickCount64();
    Publish(-1, true);
}

// Called after the actual movie callback, never from a Python timer.
void Frame(const json& row) {
    if (!enabled || quitting || row.is_null()) return;
    if (run.is_null() || row.value("movie_name", "") != run["prefix"].get<string>() + "_") {
        Fail("Unexpected movie identity in recording session"); return;
    }
    if (row["capture_index"] != frames || row["counter_after"] != frames + 1) Fail("Movie counter discontinuity");
    int tick = row["replay_demo_tick"];
    if (frameTick >= 0 && tick - frameTick != 2) Fail("Captured frame ticks are not a continuous 32 FPS grid");
    frameTick = tick; ++frames;
    if (frames > run["max_frames"].get<uint64_t>()) Fail("Recording exceeded its planned frame budget");
    for (const auto& interval : run["eligible"]) {
        if (tick > interval["start"].get<int>() && tick < interval["end"].get<int>()) {
            const auto pov = row.value("native_observation", json::object()).value("observed_pov", json::object());
            if (pov.value("status", "") != "observed" || pov.value("steam_id", "") != plan["steam_id"].get<string>() ||
                pov.value("observer_mode", -1) != 2) Fail("Selected player POV is unavailable during eligible recording");
        }
    }
}

bool Step(ISource2EngineToClient* engine, int tick) {
    if (!loaded) Load();
    if (!enabled) return false;
    auto now = GetTickCount64();
    if (quitting) return true;
    if (now - lastStopPoll >= 250) {
        lastStopPoll = now;
        if (GetFileAttributesA(stopPath.c_str()) != INVALID_FILE_ATTRIBUTES) stopping = true;
    }
    if (phase == "startup") {
        if (stopping) { phase = "stopped"; quitting = true; Publish(tick, true); Command(engine, "quit"); return true; }
        if (tick < 256) return true;
        for (const auto& command : plan["setup_commands"]) Command(engine, command.get<string>());
        run = plan["runs"][0];
        Command(engine, "demo_gototick " + std::to_string(run["start_tick"].get<int>() - 134));
        phase = "seeking"; phaseTime = now; Publish(tick, true); return true;
    }
    if (phase == "seeking") {
        int target = run["start_tick"].get<int>() - 134;
        if (tick >= target && tick < run["start_tick"].get<int>() - 96) {
            Command(engine, "spec_mode 1");
            modeTick = tick; modeIndex = 0;
            phase = "initial_select"; phaseTime = now; Publish(tick, true);
        } else if (now - phaseTime > 60000) Fail("Initial seek did not reach pre-roll");
        if (!failed) return true;
    }
    if (phase == "initial_select") {
        if (tick >= modeTick + 2) {
            Command(engine, "spec_player " + std::to_string(run["spectator_user_id"].get<int>() + 1));
            phase = "initial_pause";
        }
        return true;
    }
    if (phase == "initial_pause") {
        if (tick >= modeTick + 4) {
            Command(engine, "demo_pause"); phase = "initial_settle"; phaseTime = now; Publish(tick, true);
        }
        return true;
    }
    if (phase == "initial_settle") {
        if (now - phaseTime < 6000 && !stopping) return true;
        Command(engine, "demo_resume");
        phase = "approaching"; selectedIndex = 0; lastTick = -1; Publish(tick, true);
        return true;
    }
    if (lastTick >= 0 && tick < lastTick) Fail("Replay tick regressed after initial setup");
    lastTick = tick;
    // User stop finishes the current physical recording. Faults stop it promptly.
    if ((stopping && (phase == "approaching" || phase == "seeking")) || (failed && phase != "ending")) {
        if (ChickenCapture::movieWasRecording || phase == "starting") {
            Command(engine, "endmovie"); phase = "ending"; phaseTime = now;
        } else {
            phase = failed ? "failed" : "stopped"; quitting = true; Publish(tick, true); Command(engine, "quit"); return true;
        }
    }
    if (phase == "approaching") {
        run = plan["runs"][runIndex];
        int start = run["start_tick"];
        if (modeIndex != static_cast<int>(runIndex) && tick >= start - 128) {
            Command(engine, "spec_mode 1");
            modeIndex = static_cast<int>(runIndex); modeTick = tick;
        }
        if (selectedIndex != static_cast<int>(runIndex) && modeIndex == static_cast<int>(runIndex) && tick >= modeTick + 2) {
            Command(engine, "spec_player " + std::to_string(run["spectator_user_id"].get<int>() + 1));
            selectedIndex = static_cast<int>(runIndex);
        }
        if (tick >= start) {
            if (tick > start + 1) { Fail("Missed recording start tick"); return true; }
            if (ChickenCapture::movieWasRecording) { Fail("Previous recording has no native endpoint"); return true; }
            frames = 0; frameTick = -1;
            Command(engine, "startmovie " + run["prefix"].get<string>() + "_");
            phase = "starting"; phaseTime = now; Publish(tick, true);
        }
    } else if (phase == "starting") {
        if (ChickenCapture::movieWasRecording && ChickenCapture::lastMovieBasename == run["prefix"].get<string>() + "_") {
            phase = "recording"; Publish(tick, true);
        } else if (tick > run["start_tick"].get<int>() + 8 || now - phaseTime > 10000) Fail("Movie start was not acknowledged");
    } else if (phase == "recording") {
        if (tick >= run["end_tick"].get<int>()) {
            if (tick > run["end_tick"].get<int>() + 1) Fail("Missed recording stop tick");
            Command(engine, "endmovie"); phase = "ending"; phaseTime = now; Publish(tick, true);
        }
    } else if (phase == "ending") {
        if (!ChickenCapture::movieWasRecording && !ChickenCapture::lastEndpoint.is_null() &&
            ChickenCapture::lastEndpoint.value("movie_name", "") == run["prefix"].get<string>() + "_") {
            if (!failed && (frames < run["min_frames"].get<uint64_t>() || frames > run["max_frames"].get<uint64_t>()))
                Fail("Closed recording frame count differs from the planned interval");
            ChickenCapture::Write({{"event", "session_run_closed"}, {"run_index", runIndex},
                {"run_id", run["id"]}, {"prefix", run["prefix"]}, {"frames", frames},
                {"complete", !failed}, {"endpoint", ChickenCapture::lastEndpoint}});
            ++runIndex;
            if (stopping || runIndex == plan["runs"].size()) {
                phase = failed ? "failed" : stopping ? "stopped" : "complete";
                quitting = true; Publish(tick, true); Command(engine, "quit"); return true;
            }
            phase = "approaching"; Publish(tick, true);
        } else if (now - phaseTime > 10000) { Fail("Native movie endpoint was not observed");
            phase = "failed"; quitting = true; Publish(tick, true); Command(engine, "quit"); return true; }
    }
    Publish(tick);
    return true;
}
} // namespace ChickenSession
