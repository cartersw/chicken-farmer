// Included after Observation and before ClockTrace; see CLOCK_HOOK.md.
// This observes complete returned network packet bytes, not a proven pixel bound.
namespace PacketTrace {
using ReadPacketFn = void* (*)(void*);
ReadPacketFn originalReadPacket = nullptr;
void** demoTable = nullptr;
std::mutex mutex;
bool ready = false, healthy = true;
string error;
uint64_t invocation = 0, completed = 0, returns = 0, nullReturns = 0;
uint32_t inFlight = 0;
uint64_t activeInvocation = 0;
int32_t maxSourceTick = -1;
json latest = nullptr;

void Fault(const char* reason) {
    healthy = false;
    if (error.empty()) error = reason;
}

json SnapshotLocked() {
    return {{"schema_version", 1}, {"status", !ready || !healthy || inFlight || !returns ?
                "unavailable" : "observed_returned_packet_bytes"},
        {"healthy", healthy}, {"reason", !ready ? "hook_unavailable" : !healthy ? error :
            inFlight ? "read_packet_in_flight" : !returns ? "no_packet_returned" : ""},
        {"read_invocations", invocation}, {"last_completed_read_invocation", completed},
        {"reads_in_flight", inFlight},
        {"active_read_invocation", inFlight == 1 ? json(activeInvocation) : json(nullptr)},
        {"returned_packets", returns}, {"null_returns", nullReturns},
        {"latest_returned_packet", latest},
        {"process_lifetime_max_returned_source_demo_tick", maxSourceTick >= 0 ? json(maxSourceTick) : json(nullptr)},
        {"source_packet_association_verified", false}, {"causality_bound_verified", false}};
}

json Snapshot() {
    std::lock_guard<std::mutex> lock(mutex);
    return SnapshotLocked();
}

json PlayerState(const void* self) {
    // These are raw native scheduling members, not names inferred from values.
    // +0x22c is the selected source tick only on an ordinary non-null return.
    // A future-entry/null path replaces it with the current playback tick.
    int32_t selected = 0, epoch = 0, seek = 0, pending = 0;
    uint8_t seeking = 0, filter = 0, alternate = 0;
    const bool valid = Observation::Value<void**>(self, 0) == demoTable &&
        Observation::Get(self, 0x22c, selected) && Observation::Get(self, 0x204, epoch) &&
        Observation::Get(self, 0x208, seek) && Observation::Get(self, 0x18c0, pending) &&
        Observation::Get(self, 0x1230, seeking) && Observation::Get(self, 0x18a1, filter) &&
        Observation::Get(self, 0x18a2, alternate);
    if (!valid) return {{"layout_verified", false}};
    return {{"layout_verified", true}, {"raw_selected_tick_0x22c", selected},
        {"playback_epoch_0x204", epoch}, {"seek_target_0x208", seek},
        {"pending_seek_0x18c0", pending}, {"seeking_flag_0x1230", seeking},
        {"packet_filter_flag_0x18a1", filter}, {"alternate_gate_flag_0x18a2", alternate}};
}

bool Payload(const void* packet, std::vector<BYTE>& bytes) {
    const BYTE* source = nullptr;
    int32_t length = -1;
    if (!Observation::Get(packet, 0x50, source) || !Observation::Get(packet, 0x74, length) ||
        length < 0 || length > 16 * 1024 * 1024 || (length && !source)) return false;
    bytes.resize(static_cast<size_t>(length));
    for (size_t offset = 0; offset < bytes.size(); offset += 1024 * 1024) {
        const size_t size = (std::min)(bytes.size() - offset, size_t(1024 * 1024));
        if (!Observation::Copy(source + offset, bytes.data() + offset, size)) return false;
    }
    return true;
}

void* NewReadPacket(void* self) {
    if (!ready || isQuitting) return originalReadPacket(self);
    const bool onEngine = GetCurrentThreadId() == engineThread;
    const auto beforeTick = onEngine ? Tick() : json(nullptr);
    const auto beforeState = PlayerState(self);
    uint64_t id = 0;
    json row;
    {
        std::lock_guard<std::mutex> lock(mutex);
        id = ++invocation;
        if (!onEngine) Fault("read_packet_off_engine_thread");
        if (!beforeState.value("layout_verified", false)) Fault("demo_player_layout_unreadable");
        if (inFlight) Fault("overlapping_read_packet_transactions");
        ++inFlight;
        activeInvocation = id;
        row = {{"event", "demo_packet_read"}, {"schema_version", 1}, {"phase", "entry"},
            {"read_invocation", id}, {"clock_on_engine_thread", onEngine},
            {"demo_player_address", reinterpret_cast<uintptr_t>(self)},
            {"replay_demo_tick", beforeTick}, {"player_state", beforeState},
            {"packet_trace", SnapshotLocked()}};
    }
    Write(row);
    // No instrumentation lock is held across engine code. Direct demo commands
    // and message handlers can run here; their rows retain this transaction ID.
    void* result = originalReadPacket(self);
    const auto afterTick = onEngine ? Tick() : json(nullptr);
    const auto afterState = PlayerState(self);
    std::vector<BYTE> bytes;
    const bool readable = !result || (afterState.value("layout_verified", false) &&
        Observation::Value<void*>(self, 0x1e0) == result && Payload(result, bytes));
    const string digest = result && readable ? Sha256(bytes.data(), bytes.size()) : "";
    {
        std::lock_guard<std::mutex> lock(mutex);
        --inFlight;
        completed = (std::max)(completed, id);
        if (!inFlight) activeInvocation = 0;
        if (!afterState.value("layout_verified", false)) Fault("demo_player_layout_unreadable_after_return");
        if (!readable) Fault("returned_packet_layout_or_payload_unreadable");
        if (result && readable && digest.empty()) Fault("returned_packet_hash_failed");
        row["phase"] = "return";
        row["returned_packet"] = result != nullptr;
        row["returned_packet_address"] = reinterpret_cast<uintptr_t>(result);
        row["replay_demo_tick_after"] = afterTick;
        row["player_state_after"] = afterState;
        row["payload_readable"] = readable;
        if (result) {
            const uint64_t index = returns++;
            const auto sourceTick = afterState.contains("raw_selected_tick_0x22c") ?
                afterState["raw_selected_tick_0x22c"] : json(nullptr);
            if (readable && sourceTick.is_number_integer())
                maxSourceTick = (std::max)(maxSourceTick, sourceTick.get<int32_t>());
            latest = {{"returned_packet_index", index}, {"read_invocation", id},
                {"source_demo_tick", sourceTick},
                {"byte_count", readable ? json(bytes.size()) : json(nullptr)},
                {"payload_sha256", digest.empty() ? json(nullptr) : json(digest)},
                {"payload_readable", readable}};
            row["packet"] = latest;
        } else {
            ++nullReturns;
            row["packet"] = nullptr;
        }
        row["packet_trace"] = SnapshotLocked();
    }
    Write(std::move(row));
    return result;
}

json Initialize() {
    // The enclosing capture initialization already verifies the entire engine.
    demoTable = reinterpret_cast<void**>(engineBase + 0x52db68);
    if (demoTable[22] != engineBase + 0x2b820 || demoTable[2] != engineBase + 0x35a80 ||
        demoTable[3] != engineBase + 0x35a90)
        PluginError("Demo packet hook exact interface/vtable guard failed");
    originalReadPacket = reinterpret_cast<ReadPacketFn>(demoTable[22]);
    return {{"schema_version", 1}, {"status", "prepared"}, {"engine_sha256", EngineSha},
        {"demo_player_vtable_rva", "0x52db68"}, {"read_packet_slot", 22},
        {"read_packet_function_rva", "0x2b820"}, {"packet_data_offset", 80},
        {"packet_byte_count_offset", 116}, {"selected_source_tick_offset", 556},
        {"payload_hash", "SHA256_exact_returned_network_packet_bytes"},
        {"maximum_payload_bytes", 16 * 1024 * 1024},
        {"source_packet_association_verified", false}, {"causality_bound_verified", false}};
}

void Install() {
    MemoryBarrier();
    PatchVTableEntry(demoTable, 22, reinterpret_cast<void*>(&NewReadPacket));
    ready = true;
    Write({{"event", "demo_packet_hook_installed"}, {"packet_trace", Snapshot()}});
    Trace("Native ReadPacket transaction hook installed; source-byte association remains unverified");
}
} // namespace PacketTrace
