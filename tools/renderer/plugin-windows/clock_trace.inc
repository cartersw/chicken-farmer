// Included inside ChickenCapture after observation_trace.inc. See CLOCK_HOOK.md.
// These are delivered-message clocks, not an asserted pixel or execution clock.
namespace ClockTrace {
using MessageFn = bool (*)(void*, const void*);
MessageFn originalTick = nullptr;
MessageFn originalEntitiesDispatch = nullptr;
MessageFn originalEntitiesApply = nullptr;
MessageFn originalUserCommands = nullptr;
void** clientTable = nullptr;
const BYTE* service = nullptr;
const BYTE* trackedClient = nullptr;
std::mutex stateMutex;
bool ready = false;
bool healthy = true;
string error;
uint64_t generation = 0, invocation = 0, completed = 0;
uint64_t tickReturns = 0, entityDispatchReturns = 0, entityApplyReturns = 0;
uint64_t userCommandReturns = 0;
uint32_t inFlight = 0;
int32_t lastTick = -1, maxTick = -1, maxEntityTick = -1;
int32_t maxUserCommandTick = -1;
json maxUserCommandTicksBySlot = json::object();
int32_t lastCommandCursor = INT32_MIN;
uint64_t lastCommandGeneration = UINT64_MAX;

void Fault(const char* reason) {
    healthy = false;
    if (error.empty()) error = reason;
}

const BYTE* CurrentClient() {
    // No virtual calls here: validate the installed getter and read its member.
    if (!service || Observation::Value<const BYTE*>(service, 0) != engineBase + 0x573890)
        return nullptr;
    const auto client = Observation::Value<const BYTE*>(service, 0xa0);
    return client && Observation::Value<void**>(client, 0) == clientTable ? client : nullptr;
}

void Reset(const BYTE* client, const char* reason) {
    trackedClient = client;
    ++generation;
    lastTick = maxTick = maxEntityTick = -1;
    maxUserCommandTick = -1;
    maxUserCommandTicksBySlot = json::object();
    tickReturns = entityDispatchReturns = entityApplyReturns = 0;
    userCommandReturns = 0;
    // Do not clear faults or in-flight handlers across a seek/client reset.
    Write({{"event", "network_clock_epoch"}, {"schema_version", 1},
           {"client_generation", generation}, {"reason", reason},
           {"client_address", reinterpret_cast<uintptr_t>(client)},
           {"last_completed_invocation", completed}, {"handlers_in_flight", inFlight}});
}

json NullableTick(int32_t value) { return value >= 0 ? json(value) : json(nullptr); }

bool UserCommandsTargetIsNoOp() {
    // The engine forwards this message to Source2Client002 slot181. Verify the
    // actual pointer and exact implementation, not just the static RTTI table.
    const auto target = Observation::Value<const BYTE*>(engineBase, 0x688d80);
    const auto table = Observation::Value<const BYTE*>(target, 0);
    const auto function = Observation::Value<const BYTE*>(table, 181 * 8);
    BYTE body[3]{};
    return target && target == reinterpret_cast<const BYTE*>(::client) &&
        table == Observation::clientBase + 0x1b1a2f8 &&
        function == Observation::clientBase + 0xb344a0 &&
        Observation::Get(function, 0, body) && body[0] == 0xc2 && body[1] == 0 && body[2] == 0;
}

bool ReadUserCommands(const void* message, json& commands) {
    int32_t count = -1;
    const BYTE* storage = nullptr;
    if (!Observation::Get(message, 0x48, count) || count < 0 || count > 4096 ||
        !Observation::Get(message, 0x50, storage) || (count && !storage)) return false;
    commands = json::array();
    for (int32_t i = 0; i < count; ++i) {
        const auto command = Observation::Value<const BYTE*>(storage, 8 + size_t(i) * 8);
        uint32_t present = 0;
        int32_t number = -1, slot = -1, executed = -1, clientTick = -1;
        if (Observation::Value<const BYTE*>(command, 0) != engineBase + 0x560f50 ||
            !Observation::Get(command, 0x10, present) ||
            !Observation::Get(command, 0x28, number) ||
            !Observation::Get(command, 0x34, slot) ||
            !Observation::Get(command, 0x2c, executed) ||
            !Observation::Get(command, 0x30, clientTick)) return false;
        commands.push_back({{"envelope_index", i},
            {"command_number", present & 0x04 ? json(number) : json(nullptr)},
            {"player_slot", present & 0x20 ? json(slot) : json(nullptr)},
            {"server_tick_executed", present & 0x08 ? json(executed) : json(nullptr)},
            {"client_tick", present & 0x10 ? json(clientTick) : json(nullptr)}});
    }
    return true;
}

json Snapshot(const char* phase) {
    json out = {{"schema_version", 1}, {"phase", phase},
        {"status", "unavailable"}, {"execution_clock_verified", false},
        {"causality_bound_verified", false}, {"user_commands_observed", true},
        {"packet_trace", PacketTrace::Snapshot()}};
    if (!ready || isQuitting || GetCurrentThreadId() != engineThread) {
        out["reason"] = !ready ? "hooks_unavailable" : "not_active_engine_thread";
        return out;
    }
    const auto demoTick = Tick();
    std::lock_guard<std::mutex> lock(stateMutex);
    const auto client = CurrentClient();
    if (client != trackedClient) Reset(client, "active_client_changed_at_observation");
    int32_t serverTick = -1, clientTick = -1;
    const bool readable = client && Observation::Get(client, 0x37c, serverTick) &&
        Observation::Get(client, 0x378, clientTick);
    out["client_generation"] = generation;
    out["client_address"] = reinterpret_cast<uintptr_t>(client);
    out["replay_demo_tick"] = demoTick;
    out["client_tick"] = readable ? json(clientTick) : json(nullptr);
    out["server_tick"] = readable ? json(serverTick) : json(nullptr);
    out["last_delivered_net_tick"] = NullableTick(lastTick);
    out["max_delivered_net_tick"] = NullableTick(maxTick);
    out["max_observed_entity_tick"] = NullableTick(maxEntityTick);
    out["max_observed_user_command_execution_tick"] = NullableTick(maxUserCommandTick);
    out["max_observed_user_command_execution_ticks_by_slot"] = maxUserCommandTicksBySlot;
    out["user_commands_target_is_noop"] = UserCommandsTargetIsNoOp();
    out["last_completed_invocation"] = completed;
    out["handlers_in_flight"] = inFlight;
    out["net_tick_returns"] = tickReturns;
    out["entity_dispatch_returns"] = entityDispatchReturns;
    out["entity_apply_returns"] = entityApplyReturns;
    out["user_command_returns"] = userCommandReturns;
    out["healthy"] = healthy;
    out["reason"] = !healthy ? error : !readable ? "active_client_unavailable" :
        demoTick.is_null() ? "demo_not_playing" : inFlight ? "message_handler_in_flight" :
        maxTick < 0 ? "no_committed_nonnegative_net_tick_in_generation" : "";
    if (healthy && readable && !demoTick.is_null() && !inFlight && maxTick >= 0)
        out["status"] = "observed_message_clock";
    return out;
}

bool Dispatch(void* self, const void* message, MessageFn original, const char* kind,
              uintptr_t expectedMessageTable, bool isTick) {
    if (!ready || isQuitting) return original(self, message);
    const bool onEngine = GetCurrentThreadId() == engineThread;
    const bool isUserCommands = !strcmp(kind, "user_commands");
    int32_t rawTick = -1, serverBefore = -1, serverAfter = -1;
    uint32_t hasBits = 0;
    const auto messageTable = Observation::Value<const BYTE*>(message, 0);
    bool layout = messageTable == engineBase + expectedMessageTable &&
        Observation::Get(self, 0x37c, serverBefore);
    json commands;
    const bool userTargetNoop = isUserCommands && UserCommandsTargetIsNoOp();
    bool hasTick = false;
    if (layout && isUserCommands) {
        layout = ReadUserCommands(message, commands);
    } else if (layout) {
        layout = Observation::Get(message, 0x40, hasBits);
        hasTick = bool(hasBits & (isTick ? 2u : (1u << 15)));
        layout = layout && Observation::Get(message, isTick ? 0x50 : 0xc0, rawTick);
    }
    const int32_t effectiveTick = isTick || hasTick ? rawTick : serverBefore;
    const auto demoBefore = onEngine ? Tick() : json(nullptr);
    uint64_t id = 0, entryGeneration = 0;
    bool active = false;
    json before;
    {
        std::lock_guard<std::mutex> lock(stateMutex);
        id = ++invocation;
        const auto client = CurrentClient();
        if (client != trackedClient) Reset(client, "active_client_changed_at_dispatch");
        active = client == self && client != nullptr;
        if (!onEngine) Fault("message_handler_off_engine_thread");
        if (!layout) Fault("message_layout_or_client_read_failed");
        if (active && isTick && layout && rawTick >= 0 && lastTick >= 0 && rawTick < lastTick)
            Reset(client, "delivered_net_tick_regressed");
        entryGeneration = generation;
        ++inFlight;
        before = {{"event", "network_message"}, {"schema_version", 1},
            {"phase", "entry"}, {"kind", kind}, {"invocation_index", id},
            {"client_generation", entryGeneration}, {"active_client", active},
            {"client_address", reinterpret_cast<uintptr_t>(self)},
            {"message_vtable_rva", messageTable ? json(reinterpret_cast<uintptr_t>(messageTable) - reinterpret_cast<uintptr_t>(engineBase)) : json(nullptr)},
            {"message_layout_verified", layout}, {"clock_on_engine_thread", onEngine},
            {"replay_demo_tick", demoBefore}, {"has_explicit_tick", hasTick},
            {"raw_message_tick", layout && !isUserCommands ? json(rawTick) : json(nullptr)},
            {"effective_server_tick", layout && !isUserCommands ? json(effectiveTick) : json(nullptr)},
            {"server_tick_before", layout ? json(serverBefore) : json(nullptr)},
            {"prior_delivered_net_tick", NullableTick(lastTick)},
            {"handlers_in_flight", inFlight}, {"packet_trace", PacketTrace::Snapshot()}};
        // Record offered entity data, even if the original later rejects it.
        // This maximum is conservative evidence; it is not an accepted bound.
        if (active && layout && !isTick && !isUserCommands && effectiveTick >= 0)
            maxEntityTick = (std::max)(maxEntityTick, effectiveTick);
        if (isUserCommands) {
            before["commands"] = commands;
            before["user_commands_target_is_noop"] = userTargetNoop;
            if (!userTargetNoop) Fault("user_commands_forward_target_not_verified_noop");
            if (active && layout) for (const auto& command : commands) {
                const auto& executed = command["server_tick_executed"];
                const auto& slot = command["player_slot"];
                if (executed.is_number_integer() && executed.get<int32_t>() >= 0) {
                    maxUserCommandTick = (std::max)(maxUserCommandTick, executed.get<int32_t>());
                    if (slot.is_number_integer() && slot.get<int32_t>() >= 0 && slot.get<int32_t>() < 64) {
                        const string key = std::to_string(slot.get<int32_t>());
                        maxUserCommandTicksBySlot[key] = (std::max)(
                            maxUserCommandTicksBySlot.value(key, -1), executed.get<int32_t>());
                    }
                }
            }
        }
    }
    Write(before);
    const bool result = original(self, message);
    const bool afterReadable = Observation::Get(self, 0x37c, serverAfter);
    const auto demoAfter = onEngine ? Tick() : json(nullptr);
    {
        std::lock_guard<std::mutex> lock(stateMutex);
        --inFlight;
        completed = (std::max)(completed, id);
        if (!afterReadable) Fault("client_read_failed_after_handler");
        if (active && generation == entryGeneration && layout) {
            if (isTick) {
                ++tickReturns;
                // The native demo handler intentionally ignores negative ticks.
                if (rawTick >= 0) {
                    lastTick = rawTick;
                    maxTick = (std::max)(maxTick, rawTick);
                    if (!result || serverAfter != rawTick) Fault("net_tick_return_did_not_commit_input_tick");
                }
            } else if (isUserCommands) ++userCommandReturns;
            else if (!strcmp(kind, "packet_entities_dispatch")) ++entityDispatchReturns;
            else ++entityApplyReturns;
        }
        before["phase"] = "return";
        before["original_result"] = result;
        before["server_tick_after"] = afterReadable ? json(serverAfter) : json(nullptr);
        before["replay_demo_tick_after"] = demoAfter;
        before["generation_after"] = generation;
        before["handlers_in_flight"] = inFlight;
        before["packet_trace_after"] = PacketTrace::Snapshot();
        if (isUserCommands) {
            const bool targetAfter = UserCommandsTargetIsNoOp();
            before["user_commands_target_is_noop_after"] = targetAfter;
            if (!targetAfter) Fault("user_commands_forward_target_changed_after_handler");
        }
        before["healthy"] = healthy;
        before["error"] = error;
    }
    Write(std::move(before));
    return result;
}

bool NewTick(void* self, const void* message) {
    // Deserialized network input is CNetMessagePB, not the derived stack-local
    // CNETMsg_Tick_t helper. Both have PB+0x30, but only this table was observed.
    return Dispatch(self, message, originalTick, "net_tick", 0x533e48, true);
}
bool NewEntitiesDispatch(void* self, const void* message) {
    return Dispatch(self, message, originalEntitiesDispatch, "packet_entities_dispatch", 0x52cf60, false);
}
bool NewEntitiesApply(void* self, const void* message) {
    return Dispatch(self, message, originalEntitiesApply, "packet_entities_apply", 0x52cf60, false);
}
bool NewUserCommands(void* self, const void* message) {
    return Dispatch(self, message, originalUserCommands, "user_commands", 0x539f70, false);
}

json Initialize() {
    // Parent has already hashed engine2.dll. These additional exact vtable guards
    // reject another hook or an unexpected interface before touching any slot.
    auto factory = reinterpret_cast<CreateInterfaceFn>(GetProcAddress(
        reinterpret_cast<HMODULE>(engineBase), "CreateInterface"));
    service = factory ? static_cast<const BYTE*>(factory("NetworkClientService_001", nullptr)) : nullptr;
    clientTable = reinterpret_cast<void**>(engineBase + 0x5335d8);
    auto serviceTable = Observation::Value<const BYTE*>(service, 0);
    if (serviceTable != engineBase + 0x573890 ||
        Observation::Value<const BYTE*>(serviceTable, 23 * 8) != engineBase + ProfileRva(0x1d2bc0, 0x1d2bb0) ||
        clientTable[88] != engineBase + 0x6a2d0 ||
        clientTable[111] != engineBase + 0x6afe0 ||
        clientTable[128] != engineBase + 0x89340 ||
        clientTable[129] != engineBase + 0x47a00)
        PluginError("Network clock hook exact interface/vtable guard failed");
    originalTick = reinterpret_cast<MessageFn>(clientTable[88]);
    originalEntitiesDispatch = reinterpret_cast<MessageFn>(clientTable[111]);
    originalEntitiesApply = reinterpret_cast<MessageFn>(clientTable[129]);
    originalUserCommands = reinterpret_cast<MessageFn>(clientTable[128]);
    return {{"schema_version", 1}, {"status", "prepared"},
        {"engine_sha256", ExpectedEngineSha()}, {"client_vtable_rva", "0x5335d8"},
        {"net_tick_slot", 88}, {"packet_entities_dispatch_slot", 111},
        {"packet_entities_apply_slot", 129}, {"net_tick_field_offset", 80},
        {"user_commands_slot", 128},
        {"packet_entities_tick_field_offset", 192}, {"client_server_tick_offset", 892},
        {"user_commands_observed", true}, {"causality_bound_verified", false},
        {"execution_clock_verified", false},
        {"scope", "delivered_net_tick_packet_entities_and_user_command_envelopes"}};
}

void Install() {
    MemoryBarrier();
    PatchVTableEntry(clientTable, 88, reinterpret_cast<void*>(&NewTick));
    PatchVTableEntry(clientTable, 111, reinterpret_cast<void*>(&NewEntitiesDispatch));
    PatchVTableEntry(clientTable, 129, reinterpret_cast<void*>(&NewEntitiesApply));
    PatchVTableEntry(clientTable, 128, reinterpret_cast<void*>(&NewUserCommands));
    ready = true;
    Write({{"event", "network_clock_installed"}, {"native_clock", Snapshot("hook_install")}});
    Trace("Native network clock hooks installed; execution epoch and causality remain unverified");
}

void BeforeCommands() {
    if (!ready) return;
    const auto state = Snapshot("before_frame_commands");
    const auto cursor = Tick();
    const int32_t value = cursor.is_number_integer() ? cursor.get<int32_t>() : INT32_MIN;
    const auto observedGeneration = state.value("client_generation", uint64_t(0));
    if (value == lastCommandCursor && observedGeneration == lastCommandGeneration) return;
    lastCommandCursor = value;
    lastCommandGeneration = observedGeneration;
    Write({{"event", "network_clock_before_commands"}, {"native_clock", state}});
}
} // namespace ClockTrace
