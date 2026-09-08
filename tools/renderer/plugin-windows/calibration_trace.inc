// Opt-in local control calibration. Console dispatch is observed; the engine's
// input consumption time and physical-device timing are deliberately not claimed.
#include <set>
#include <regex>

namespace ChickenCalibration {
constexpr const char* Marker = "CHICKEN_CONTROLLED_CALIBRATION_V1";
constexpr const char* Producer = "cs2-controlled-calibration-plan-v1";
constexpr const char* ClockBasis = "local_controller_tick_base_64hz";
FILE* ledger = nullptr;
json plan;
bool attempted = false, enabled = false;
bool localMapRequested = false;
json commandSupport = json::array();
int stage = 0;
int32_t startTick = 0, previousTick = 0;
uint32_t pawnHandle = UINT32_MAX;
BYTE* networkBase = nullptr;
size_t nextAction = 0;
int64_t frequency = 0, initializeQpc = 0, stageQpc = 0, lastNotice = 0;
std::set<string> held;

int64_t Qpc() { return ChickenCapture::Qpc(); }
double SecondsSince(int64_t timestamp) { return double(Qpc() - timestamp) / double(frequency); }
void Write(json row) {
    if (!ledger) return;
    row["schema_version"] = 1;
    row["thread_id"] = GetCurrentThreadId();
    const auto line = row.dump();
    if (fwrite(line.data(), 1, line.size(), ledger) != line.size() ||
        fputc('\n', ledger) == EOF || fflush(ledger) != 0)
        PluginError("Cannot persist controlled calibration evidence");
}
void Fail(const string& reason) {
    Write({{"event", "calibration_failed"}, {"reason", reason}, {"qpc", Qpc()}});
    PluginError("Controlled calibration failed: %s", reason.c_str());
}
bool ExactKeys(const json& value, std::initializer_list<const char*> names) {
    if (!value.is_object() || value.size() != names.size()) return false;
    for (const auto name : names) if (!value.contains(name)) return false;
    return true;
}
bool Integer(const json& value, int minimum, int maximum) {
    return value.is_number_integer() && value >= minimum && value <= maximum;
}
const std::set<string> Controls = {"forward", "back", "left", "right", "attack", "attack2",
    "duck", "jump", "sprint", "reload", "turnleft", "turnright"};

void ValidatePlan() {
    if (!ExactKeys(plan, {"schema_version", "producer", "map", "fps", "width", "height",
                          "duration_seconds", "actions", "movie_name", "demo_path"}) ||
        !Integer(plan["schema_version"], 1, 1) || plan["producer"] != Producer ||
        plan["map"] != "de_dust2" || !Integer(plan["fps"], 32, 32) ||
        !Integer(plan["width"], 1280, 1280) || !Integer(plan["height"], 720, 720) ||
        !Integer(plan["duration_seconds"], 2, 30) || !plan["actions"].is_array() ||
        plan["actions"].empty() || plan["actions"].size() > 128 ||
        !plan["movie_name"].is_string() || !plan["demo_path"].is_string())
        Fail("Unsupported bounded calibration plan");
    if (!std::regex_match(plan["movie_name"].get<string>(), std::regex("calibration-[0-9a-f]{32}")))
        Fail("Invalid owned capture prefix");
    auto demoText = plan["demo_path"].get<string>();
    auto demo = std::filesystem::u8path(demoText);
    auto planPath = ChickenSettings::RequiredPath("-chicken-calibration-plan");
    if (demoText.find_first_of("\";\r\n") != string::npos || !demo.is_absolute() ||
        demo.filename() != "controlled.dem" || !ChickenSettings::SamePath(demo.parent_path(), planPath.parent_path()) ||
        std::filesystem::exists(demo)) Fail("Demo path must be fresh and owned by this calibration run");
    std::set<string> ids, down;
    int previous = -1;
    const int end = plan["duration_seconds"].get<int>() * 1000 - 500;
    for (const auto& action : plan["actions"]) {
        if (!ExactKeys(action, {"id", "at_ms", "command"}) || !action["id"].is_string() ||
            !action["command"].is_string() || !Integer(action["at_ms"], 0, end)) Fail("Invalid control action");
        auto id = action["id"].get<string>(), command = action["command"].get<string>();
        int at = action["at_ms"].get<int>();
        if (!std::regex_match(id, std::regex("[a-z][a-z0-9_-]{0,63}")) ||
            !ids.insert(id).second || at < previous) Fail("Invalid action identity/order");
        previous = at;
        if (command.size() > 1 && (command[0] == '+' || command[0] == '-') && Controls.count(command.substr(1))) {
            auto control = command.substr(1);
            if (command[0] == '+' ? !down.insert(control).second : !down.erase(control))
                Fail("Control transitions must alternate press and release");
        } else {
            std::smatch match;
            if (!std::regex_match(command, match, std::regex("setang 0 (-?(0|[1-9][0-9]{0,2})) 0")) ||
                std::stoi(match[1].str()) < -180 || std::stoi(match[1].str()) > 180)
                Fail("Command is outside the calibration whitelist");
        }
    }
    if (!down.empty()) Fail("Calibration must end with all controls released");
}

json Local() {
    const auto observation = ChickenCapture::Observation::Snapshot("calibration_frame_start");
    return observation.value("local_player", json{{"status", "unavailable"}});
}
bool Alive(const json& local) {
    return local.value("status", "") == "observed" &&
        local.contains("controller_tick_base") && local["controller_tick_base"].is_number_integer() &&
        local.value("observer_services_pointer_observed", false) &&
        (local.value("observer_services_present", json(nullptr)) == false || local.value("observer_mode", json(nullptr)) == 0) &&
        local.contains("life_state") && local["life_state"] == 0 &&
        (local.value("team", json(nullptr)) == 2 || local.value("team", json(nullptr)) == 3) &&
        local.contains("pawn_state") && local["pawn_state"]["health"].is_number_integer() &&
        local["pawn_state"]["health"].get<int>() > 0;
}

json LocalConnection() {
    // The pinned SDK GetGameServer declaration after slot29 is stale. Use only
    // the independently inspected slot23 getter (mov rax,[rcx+150h];ret).
    namespace O = ChickenCapture::Observation;
    auto engine = ChickenCapture::engineBase;
    auto service = factory ? factory("NetworkServerService_001", nullptr) : nullptr;
    auto table = O::Value<const BYTE*>(service, 0);
    if (!service || table != engine + 0x576058 ||
        O::Value<const BYTE*>(table, 23 * 8) != engine + 0x1dea60)
        return {{"verified", false}, {"reason", "local_server_interface_unavailable"}};
    const BYTE getter[] = {0x48, 0x8b, 0x81, 0x50, 0x01, 0x00, 0x00, 0xc3};
    BYTE actual[sizeof(getter)]{};
    if (!O::Copy(engine + 0x1dea60, actual, sizeof(actual)) || memcmp(getter, actual, sizeof(actual)))
        Fail("Local server getter binary layout mismatch");
    auto server = O::Value<const BYTE*>(service, 0x150);
    const bool active = server && O::Value<const BYTE*>(server, 0) == engine + 0x53e0d8 &&
        O::Value<int32_t>(server, 0x38, -1) >= 3;
    // Engine CEngineClient::GetNetChannel(slot0), inspected at RVA75c40,
    // resolves the current client via global90d4b0 and returns client+f0.
    // Validate the returned object's RTTI table and exact IsLoopback body in
    // networksystem.dll before reading its address kind; never call a guessed
    // interface on an arbitrary pointer.
    auto engineTable = O::Value<const BYTE*>(engineToClient, 0);
    if (engineTable != engine + 0x538290 || O::Value<const BYTE*>(engineTable, 41 * 8) != engine + 0x75c40)
        Fail("Local client channel getter interface mismatch");
    const BYTE channelGetter[] = {0x4c,0x8b,0x05,0x69,0x78,0x89,0x00,0x4d,0x85,0xc0,0x74,0x10,
        0x48,0x63,0xca,0x48,0x8d,0x04,0x49,0x49,0x8b,0x84,0xc0,0xf0,0x00,0x00,0x00,0xc3,0x33,0xc0,0xc3};
    BYTE channelActual[sizeof(channelGetter)]{};
    if (!O::Copy(engine + 0x75c40, channelActual, sizeof(channelActual)) ||
        memcmp(channelGetter, channelActual, sizeof(channelActual))) Fail("Local channel getter body mismatch");
    auto gameClient = O::Value<const BYTE*>(engine, 0x90d4b0);
    auto channel = O::Value<const BYTE*>(gameClient, 0xf0);
    auto channelTable = O::Value<const BYTE*>(channel, 0);
    const BYTE loopBody[] = {0x83, 0xb9, 0xa8, 0x72, 0x00, 0x00, 0x04, 0x0f, 0x94, 0xc0, 0xc3};
    BYTE loopActual[sizeof(loopBody)]{};
    const bool channelVerified = networkBase && channelTable == networkBase + 0x22ae08 &&
        O::Value<const BYTE*>(channelTable, 7 * 8) == networkBase + 0xb6600 &&
        O::Copy(networkBase + 0xb6600, loopActual, sizeof(loopActual)) &&
        !memcmp(loopActual, loopBody, sizeof(loopActual));
    const bool loopback = channelVerified && O::Value<int32_t>(channel, 0x72a8, -1) == 4;
    ConVarRefAbstract lan("sv_lan");
    const auto lanText = lan.IsValidRef() ? string(lan.GetString().String()) : string();
    return {{"verified", active && loopback && (lanText == "true" || lanText == "1")},
        {"local_server_active", active}, {"loopback", loopback}, {"server_interface", "NetworkServerService_001"},
        {"channel_layout_verified", channelVerified}, {"channel_address_kind", channelVerified ?
            json(O::Value<int32_t>(channel, 0x72a8, -1)) : json(nullptr)},
        {"reason", loopback ? "" : "local_loopback_channel_unavailable"}};
}

json InputRegistration(const string& command) {
    namespace O = ChickenCapture::Observation;
    if (command.size() < 2 || (command[0] != '+' && command[0] != '-') || !Controls.count(command.substr(1)))
        return {{"available", false}};
    // Input actions are intentionally separate from ICvar's command registry.
    // The inspected normal console dispatcher calls CInputService::1b91b0
    // first; that parser strips +/- and dispatches only descriptor type zero.
    auto service = O::Value<const BYTE*>(O::clientBase, 0x25eaa48);
    auto table = O::Value<const BYTE*>(service, 0);
    if (table != ChickenCapture::engineBase + 0x570208 ||
        O::Value<const BYTE*>(table, 37 * 8) != ChickenCapture::engineBase + 0x1c1440)
        return {{"available", false}, {"reason", "input_service_interface_unavailable"}};
    const auto count = O::Value<int32_t>(service, 0x32fb8, -1);
    auto entries = O::Value<const BYTE*>(service, 0x32f88);
    uint8_t active = 0;
    const bool activeKnown = O::Get(service, 0x3ec4c, active);
    if (!entries || count <= 0 || count > 4096 || !activeKnown || active != 1)
        return {{"available", false}, {"reason", "input_registry_or_active_input_unavailable"},
            {"registry_count", count}, {"input_active", activeKnown ? json(active) : json(nullptr)}};
    int matches = 0, type = -1;
    for (int i = 0; i < count; ++i) {
        auto descriptor = O::Value<const BYTE*>(entries, size_t(i) * 16);
        auto name = O::Value<const char*>(descriptor, 0);
        if (O::String(name, 128) == command.substr(1)) {
            ++matches; type = O::Value<int32_t>(descriptor, 8, -1);
        }
    }
    return {{"available", matches == 1 && type == 0}, {"action_name", command.substr(1)},
        {"command_support_basis", "runtime_input_value_registry_type0"}, {"registry_count", count},
        {"matching_descriptors", matches}, {"descriptor_type", type}, {"input_active", active},
        {"input_consumption_verified", false}};
}
void Dispatch(const string& command) {
    const auto name = command.substr(0, command.find(' '));
    // jointeam is interpreted by the local server's client-command handler,
    // rather than registered as a client-side ConCommand. Only this fixed setup
    // message is allowed through that path; alive/team state is checked later.
    const bool fixedServerSetup = command == "jointeam 2";
    const bool inputAction = !command.empty() && (command[0] == '+' || command[0] == '-');
    if (inputAction) {
        const auto support = InputRegistration(command);
        if (!support.value("available", false)) {
            Write({{"event", "input_support_unavailable"}, {"command", command}, {"support", support}});
            Fail("Input action unavailable: " + command);
        }
    }
    if (!inputAction && !fixedServerSetup && (!g_pCVar || (!g_pCVar->FindConCommand(name.c_str(), true).IsValidRef() &&
        !g_pCVar->FindConVar(name.c_str(), true).IsValidRef()))) Fail("Required engine command unavailable: " + name);
    engineToClient->ExecuteClientCmd(0, command.c_str(), true);
}
void Setup() {
    for (const char* command : {"sv_cheats 1", "bot_kick", "mp_autoteambalance 0", "mp_limitteams 0",
        "mp_freezetime 0", "mp_ignore_round_win_conditions 1", "mp_respawn_on_death_t 1",
        "mp_respawn_on_death_ct 1", "mp_warmup_end", "jointeam 2", "firstperson", "mp_restartgame 1"}) Dispatch(command);
    Write({{"event", "local_setup_dispatched"}, {"qpc", Qpc()}});
}
void ConfigureFullUserCommands() {
    namespace O = ChickenCapture::Observation;
    // The current tier0 name lookup excludes this DEVELOPMENTONLY variable.
    // Resolve the real server's original registered handle, verify its cached
    // data/name/type, then use the normal ConVar setter. Do not register a new
    // similarly named variable or edit the binary/flags.
    HMODULE serverModule = nullptr;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCSTR>(serverCreateInterface), &serverModule) ||
        O::ModuleSha(serverModule) != "cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c" ||
        O::ModuleSha(GetModuleHandleA("tier0.dll")) != "b9bf7c956bb31e11be649c31b096ea8813601748b9d984d39f10a9eb4458fb75")
        Fail("Complete-command recorder requires inspected server and tier0 binaries");
    auto base = reinterpret_cast<const BYTE*>(serverModule);
    uint16_t access = UINT16_MAX;
    int registered = 0;
    ConVarData* cached = nullptr;
    if (!O::Get(base, 0x202b8c8, access) || !O::Get(base, 0x202b8cc, registered) ||
        !O::Get(base, 0x202b8d0, cached) || access == UINT16_MAX || !cached)
        Fail("Original full-command recording ConVar handle is unavailable");
    ConVarRefAbstract value(ConVarRef(access, registered), EConVarType_Bool);
    if (!value.IsValidRef() || value.GetConVarData() != cached || !value.IsConVarDataAvailable() ||
        value.GetType() != EConVarType_Bool || O::String(value.GetName()) != "sv_cq_delta_encode_svc_usercmds")
        Fail("Original full-command recording ConVar identity mismatch");
    const string before = value.GetString().String();
    value.SetBool(false);
    const string after = value.GetString().String();
    if (after != "false" && after != "0") Fail("Complete-command recording option did not change");
    Write({{"event", "recording_options"}, {"qpc", Qpc()},
        {"sv_cq_delta_encode_svc_usercmds", after}, {"previous_value", before},
        {"setter", "original_registered_ConVarRef_SetBool"}, {"native_convar_access_index", access},
        {"native_convar_registered_index", registered}, {"requested_usercmd_encoding", "full_payloads"},
        {"actual_recorded_payloads_verified", false}});
}
void Initialize() {
    if (attempted) return;
    attempted = true;
    if (!ChickenCapture::HasArgument("-chicken-calibration-plan")) return;
    if (!ChickenCapture::HasArgument("-insecure") || ChickenCapture::HasArgument("+connect") ||
        ChickenCapture::HasArgument("-playdemo") || !ChickenSettings::ready || !ChickenCapture::ledger)
        Fail("Calibration requires the protected insecure local worker");
    auto path = ChickenSettings::RequiredPath("-chicken-calibration-plan");
    auto evidence = ChickenSettings::RequiredPath("-chicken-calibration-ledger");
    if (!ChickenSettings::SamePath(evidence.parent_path(), path.parent_path()) ||
        evidence.filename() != "calibration_ledger.jsonl" || std::filesystem::file_size(path) > 65536)
        Fail("Calibration input/evidence path is outside its bounded run");
    std::ifstream input(path);
    plan = json::parse(input);
    ValidatePlan();
    networkBase = reinterpret_cast<BYTE*>(GetModuleHandleA("networksystem.dll"));
    if (ChickenSettings::ModuleHash(reinterpret_cast<HMODULE>(networkBase)) !=
        "fc33c097eca4ab0049590985a772b5b2f556523933d0482f86217889e1283127")
        Fail("Local connection networksystem.dll SHA256 mismatch");
    ledger = _wfopen(evidence.c_str(), L"wx");
    if (!ledger) Fail("Calibration ledger must be fresh and writable");
    LARGE_INTEGER ticks{}; QueryPerformanceFrequency(&ticks); frequency = ticks.QuadPart;
    initializeQpc = stageQpc = Qpc();
    Write({{"event", "header"}, {"producer", Producer}, {"plugin_marker", Marker},
        {"qpc_frequency", frequency}, {"qpc", initializeQpc}, {"clock_basis", ClockBasis},
        {"control_source", "dispatched_engine_controls_not_physical_device_latency"},
        {"physical_input_timestamps", false}, {"training_ready", false}, {"plan", plan},
        {"engine_sha256", ChickenCapture::ExpectedEngineSha()}, {"native_profile", ChickenCapture::ProfileName()}});
    enabled = true;
}

void BeforeCommands() {
    try {
        Initialize();
        if (!enabled || isQuitting) return;
        if (SecondsSince(initializeQpc) > 180) Fail("Calibration exceeded native wall timeout");
        if (!engineToClient || engineToClient->IsPlayingDemo()) Fail("Calibration cannot run in demo playback");
        if (stage == 0 && !localMapRequested && SecondsSince(initializeQpc) >= 8) {
            // Offline startup can consume +map before the client is ready, as
            // previously observed for +playdemo. Reissue one fixed local map
            // request from the live engine thread, with no external address.
            Dispatch("sv_lan 1"); Dispatch("tv_enable 1");
            Dispatch("map de_dust2 loopback=true");
            localMapRequested = true;
            Write({{"event", "local_map_dispatched"}, {"qpc", Qpc()}, {"map", "de_dust2"}});
            return;
        }
        if (!engineToClient->IsInGame()) return;
        const string map = engineToClient->GetLevelNameShort() ? engineToClient->GetLevelNameShort() : "";
        if (map != "de_dust2") {
            if (stage != 0) Fail("Calibration map changed to: " + map);
            // The menu/background world can become active before +map finishes.
            // Dispatch no controls there; wait for the requested local world.
            if (SecondsSince(lastNotice) > 1) {
                Write({{"event", "waiting_for_map"}, {"qpc", Qpc()}, {"observed_map", map}});
                lastNotice = Qpc();
            }
            return;
        }
        const auto connection = LocalConnection();
        if (!connection.value("verified", false)) {
            if (stage != 0) Fail("Verified local connection was lost");
            if (SecondsSince(lastNotice) > 1) {
                Write({{"event", "waiting_for_local_connection"}, {"qpc", Qpc()}, {"connection", connection}});
                lastNotice = Qpc();
            }
            return;
        }
        if (stage == 0) { Setup(); stage = 1; stageQpc = Qpc(); return; }
        auto local = Local();
        if (stage == 1) {
            if (SecondsSince(stageQpc) < 6 || !Alive(local) || !local.value("first_person_camera_verified", false)) {
                if (SecondsSince(lastNotice) > 1) {
                    Write({{"event", "waiting_for_local_player"}, {"qpc", Qpc()}, {"local_player", local}});
                    lastNotice = Qpc();
                }
                return;
            }
            for (const auto& action : plan["actions"]) {
                const auto command = action["command"].get<string>();
                const auto name = command.substr(0, command.find(' '));
                auto support = (command[0] == '+' || command[0] == '-') ? InputRegistration(command) :
                    json{{"available", g_pCVar->FindConCommand(name.c_str(), true).IsValidRef()}, {"command_support_basis", "ICvar_concommand"}};
                support["command"] = command;
                if (!support.value("available", false)) {
                    Write({{"event", "input_support_unavailable"}, {"support", support}});
                    Fail("Probe command unavailable: " + name);
                }
                commandSupport.push_back(support);
            }
            Dispatch("firstperson");
            Dispatch("slot2");
            ConfigureFullUserCommands();
            Dispatch("tv_record_immediate 1");
            Dispatch("tv_delay 0");
            // This command rejects colon-bearing absolute paths. The worker
            // archives only this unique run-owned basename after process exit.
            Dispatch("tv_record " + plan["movie_name"].get<string>());
            stage = 2; stageQpc = Qpc(); return;
        }
        if (stage == 2) {
            if (SecondsSince(stageQpc) < 1) return;
            if (!Alive(local)) Fail("Player not alive when starting original capture");
            Dispatch("host_framerate 32");
            Dispatch("startmovie " + plan["movie_name"].get<string>() + "_ tga");
            stage = 3; return;
        }
        if (stage == 3) {
            if (!Alive(local)) Fail("Player not alive at calibration clock origin");
            startTick = previousTick = local["controller_tick_base"].get<int32_t>();
            pawnHandle = local["pawn_handle"].get<uint32_t>();
            Write({{"event", "calibration_ready"}, {"clock_basis", ClockBasis}, {"start_tick_base", startTick},
                {"qpc", Qpc()}, {"local_player", local}, {"local_player_alive", true}, {"map", map},
                {"local_connection_verified", true}, {"local_connection", "loopback"},
                {"connection_evidence", connection}, {"commands_verified", true}, {"registered_controls", commandSupport}});
            stage = 4;
        }
        if (stage == 4) {
            if (!Alive(local) || local["pawn_handle"] != pawnHandle) Fail("Local pawn continuity was lost");
            const auto tick = local["controller_tick_base"].get<int32_t>();
            if (tick < previousTick) Fail("Local scheduling clock moved backwards");
            previousTick = tick;
            const double elapsed = double(tick - startTick) * 15.625;
            Write({{"event", "frame_sample"}, {"elapsed_ms", elapsed}, {"qpc", Qpc()}, {"local_player", local}});
            while (nextAction < plan["actions"].size() && plan["actions"][nextAction]["at_ms"].get<int>() <= elapsed) {
                const auto& action = plan["actions"][nextAction++];
                const auto command = action["command"].get<string>();
                const auto before = Local();
                const auto qpcBefore = Qpc();
                Dispatch(command);
                const auto qpcAfter = Qpc();
                if (command[0] == '+') held.insert(command.substr(1));
                if (command[0] == '-') held.erase(command.substr(1));
                Write({{"event", "action_dispatch"}, {"id", action["id"]}, {"command", command},
                    {"at_ms", action["at_ms"]}, {"actual_elapsed_ms", double(before["controller_tick_base"].get<int32_t>() - startTick) * 15.625},
                    {"qpc_before", qpcBefore}, {"qpc_after", qpcAfter},
                    {"local_player_before", before}, {"local_player_after", Local()}});
            }
            if (elapsed >= plan["duration_seconds"].get<int>() * 1000) {
                if (!held.empty() || nextAction != plan["actions"].size()) Fail("Incomplete control sequence at end");
                Dispatch("endmovie"); Dispatch("tv_stoprecord"); Dispatch("host_framerate 0");
                Write({{"event", "recording_stop_dispatched"}, {"elapsed_ms", elapsed}, {"qpc", Qpc()}, {"local_player", local}});
                stage = 5; stageQpc = Qpc();
            }
        } else if (stage == 5 && SecondsSince(stageQpc) >= 0.5) {
            Write({{"event", "calibration_complete"}, {"qpc", Qpc()}, {"actions_dispatched", nextAction},
                {"training_ready", false}, {"timing_status", "unverified"}});
            stage = 6;
            Dispatch("quit");
        }
    } catch (const std::exception& error) { Fail(error.what()); }
}
} // namespace ChickenCalibration
