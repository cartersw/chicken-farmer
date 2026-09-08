// Experimental, opt-in capture evidence for the exact engine2.dll identified below.
// These are native movie submission and pixel-readback hooks, not filesystem clocks.
// See CAPTURE_HOOK.md for the binary evidence and the deliberately unvalidated clocks.
#include <bcrypt.h>
#include <intrin.h>
#include <iomanip>
#include <cmath>
#include <type_traits>

namespace ChickenCapture {
constexpr const char* EngineSha = "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac";
using MovieFrameFn = void (*)(void*, const void*);
using MovieEndFn = void (*)(void*);
// The final argument is a DWORD in the native callee, not a bool: its full value
// is copied into the queued renderer command. A byte would leave stack garbage.
using ReadPixelsFn = bool (*)(void*, void*, const int*, int, int, const int*, void*, int, int);
MovieFrameFn originalMovieFrame = nullptr;
MovieEndFn originalMovieEnd = nullptr;
ReadPixelsFn originalReadPixels = nullptr;
BYTE* engineBase = nullptr;
DWORD engineThread = 0;
void** movieVtable = nullptr;
void** renderVtable = nullptr;
std::mutex ledgerMutex;
FILE* ledger = nullptr;
bool attempted = false;
uint64_t readbackCounter = 0;
json lastMovie;
bool movieWasRecording = false;
uint64_t lastCounterAfter = 0;
string lastMovieBasename;
json lastEndpoint;
void* currentMovieObject = nullptr;

int64_t Qpc() { LARGE_INTEGER value{}; QueryPerformanceCounter(&value); return value.QuadPart; }
bool HasArgument(const char* name) {
    for (int i = 0; i < CommandLine()->ParmCount(); ++i)
        if (!strcmp(CommandLine()->GetParm(i), name)) return true;
    return false;
}
// Current-build offsets are reviewed separately in NATIVE_COMPATIBILITY_14180.md.
// Historical replay keeps its original profile. Current competitive replay
// selects a separate evidence profile; source-command acceptance is external.
bool CalibrationProfile() {
    return HasArgument("-chicken-calibration-plan") || HasArgument("-chicken-calibration-replay");
}
bool CompetitiveProfile() { return HasArgument("-chicken-competitive-replay"); }
bool CurrentBuildProfile() { return CalibrationProfile() || CompetitiveProfile(); }
const char* ExpectedEngineSha() {
    return CurrentBuildProfile() ? "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07" : EngineSha;
}
size_t ProfileRva(size_t legacy, size_t current) { return CurrentBuildProfile() ? current : legacy; }
const char* ProfileName() {
    return CompetitiveProfile() ? "cs2-14180-competitive-replay-v1" :
        CalibrationProfile() ? "cs2-14180-calibration-v1" : "cs2-14178-replay-v1";
}

template<class T> T Read(const void* base, size_t offset) {
    T value{};
    memcpy(&value, static_cast<const BYTE*>(base) + offset, sizeof(value));
    return value;
}

string Sha256(const BYTE* data, size_t length) {
    if (length > ULONG_MAX) return "";
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BYTE digest[32]{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return "";
    auto status = BCryptHash(algorithm, nullptr, 0, const_cast<BYTE*>(data),
                             static_cast<ULONG>(length), digest, sizeof(digest));
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status < 0) return "";
    char result[65]{};
    for (size_t i = 0; i < sizeof(digest); ++i) sprintf(result + 2 * i, "%02x", digest[i]);
    return result;
}

void Write(json row) {
    std::lock_guard<std::mutex> lock(ledgerMutex);
    if (!ledger) return;
    row["thread_id"] = GetCurrentThreadId();
    const string line = row.dump();
    if (fwrite(line.data(), 1, line.size(), ledger) != line.size() ||
        fputc('\n', ledger) == EOF || fflush(ledger) != 0) {
        PluginError("Could not persist capture evidence ledger");
    }
}

json Tick() {
    // Never invoke engine interfaces from the render/worker thread.
    if (isQuitting || GetCurrentThreadId() != engineThread || !engineToClient) return nullptr;
    auto demo = engineToClient->GetDemoPlayer();
    return demo && engineToClient->IsPlayingDemo() ? json(demo->GetDemoTick()) : json(nullptr);
}

json DemoStartTick() {
    if (isQuitting || GetCurrentThreadId() != engineThread || !engineToClient) return nullptr;
    auto demo = engineToClient->GetDemoPlayer();
    if (!demo || !engineToClient->IsPlayingDemo()) return nullptr;
    auto table = *reinterpret_cast<void***>(demo);
    if (table[2] != engineBase + 0x35a80 || table[3] != engineBase + 0x35a90)
        PluginError("Capture demo epoch accessor layout mismatch");
    // Native GetDemoTick subtracts exactly the member returned by this getter
    // from CNetworkClientService's current tick. Observe it; never infer it.
    return demo->GetDemoStartTick();
}

#include "observation_trace.inc"
#include "packet_trace.inc"
#include "clock_trace.inc"

string MovieName(void* self) {
    auto name = Read<const char*>(self, 0x130);
    return name ? string(name, strnlen(name, 1024)) : string();
}

void TraceCvars() {
    for (const char* name : {"cl_radar_show_all_players_when_spectating", "cl_spec_show_bindings",
                             "cl_spec_stats", "cl_drawhud", "cl_draw_only_deathnotices",
                             "spec_show_xray", "cl_drawhud_force_radar", "cl_drawhud_force_teamid_overhead"}) {
        ConVarRefAbstract ref(name);
        if (ref.IsValidRef()) {
            const auto value = ref.GetString();
            Trace("Capture CVar %s=%s", name, value.String());
            Write({{"event", "cvar"}, {"name", name}, {"value", value.String()}});
        } else {
            Trace("Capture CVar %s unavailable", name);
            Write({{"event", "cvar"}, {"name", name}, {"value", nullptr}});
        }
    }
}

void NewMovieFrame(void* self, const void* event) {
    const auto qpcBefore = Qpc();
    // CRenderService::OnClientOutput passes its EventClientOutput_t argument
    // unchanged to this call. The exact return RVA is independently verified.
    const auto callerRva = reinterpret_cast<BYTE*>(_ReturnAddress()) - engineBase;
    if (ledger && callerRva != ProfileRva(0x1e5b58, 0x1e5b48))
        PluginError("Unexpected native movie-frame caller; capture clock cannot be trusted");
    currentMovieObject = self;
    const auto before = Read<uint64_t>(self, 0x138);
    const auto state = Read<int>(self, 0x168);
    const auto flags = Read<uint16_t>(self, 0x14c);
    json row;
    if (ledger && movieWasRecording && (state == 0 || state == 2)) {
        // A real subsequent render event supplies the final boundary; the
        // endmovie command itself can share the final captured frame's cursor.
        lastEndpoint = {{"event", "movie_end"}, {"movie_name", lastMovieBasename},
               {"next_capture_index", lastCounterAfter}, {"replay_demo_tick", Tick()},
               {"demo_start_tick", DemoStartTick()},
               {"render_time_seconds", Read<float>(event, 0x28)},
               {"real_time_seconds", Read<float>(event, 0x2c)},
               {"native_observation", Observation::Snapshot("movie_endpoint")},
               {"native_clock", ClockTrace::Snapshot("movie_endpoint")},
               {"native_state_before", state},
               {"clock_phase", "first_movie_callback_after_recording_stopped"}};
        Write(lastEndpoint);
        movieWasRecording = false;
    }
    if (ledger && state > 2 && (flags & 1)) {
        char filename[1200]{};
        const auto nativeName = MovieName(self);
        const auto name = std::filesystem::path(nativeName).filename().string();
        snprintf(filename, sizeof(filename), "%s%08llu.tga", name.c_str(), before);
        row = {{"event", "movie_frame"}, {"movie_name", name}, {"capture_index", before},
               {"qpc_before", qpcBefore},
               {"native_movie_name", nativeName},
               {"tga_filename", filename}, {"replay_demo_tick", Tick()},
               {"demo_start_tick", DemoStartTick()},
               {"submission_event_tick_raw", Read<int>(event, 0x28)},
               {"submission_event_0x28_float_raw", Read<float>(event, 0x28)},
               {"submission_event_time_raw", Read<float>(event, 0x2c)},
               {"render_time_seconds", Read<float>(event, 0x28)},
               {"real_time_seconds", Read<float>(event, 0x2c)},
               {"native_observation", Observation::Snapshot("movie_submission")},
               {"native_clock", ClockTrace::Snapshot("movie_submission")},
               {"state_before", state}, {"movie_flags", flags}};
        // The submission identity is published before native code queues the TGA.
        { std::lock_guard<std::mutex> lock(ledgerMutex); lastMovie = row; }
        if (before == 0) TraceCvars();
        movieWasRecording = true;
        lastMovieBasename = name;
    }
    originalMovieFrame(self, event);
    if (!row.is_null()) {
        row["qpc_after"] = Qpc();
        row["counter_after"] = Read<uint64_t>(self, 0x138);
        lastCounterAfter = Read<uint64_t>(self, 0x138);
        row["replay_demo_tick_after"] = Tick();
        row["native_clock_after"] = ClockTrace::Snapshot("movie_submission_after");
        ChickenSession::Frame(row);
        Write(std::move(row));
    }
}

void NewMovieEnd(void* self) {
    const auto state = Read<int>(self, 0x168);
    if (ledger && (state == 1 || state == 3)) {
        Write({{"event", "movie_stop_request"}, {"movie_name", std::filesystem::path(MovieName(self)).filename().string()},
               {"next_capture_index", Read<uint64_t>(self, 0x138)},
               {"replay_demo_tick", Tick()}, {"clock_phase", "endmovie_request"}});
    }
    originalMovieEnd(self);
}

void ObserveCommand(const string& command, const char* phase) {
    if (!ledger || command != "endmovie" || !currentMovieObject) return;
    auto cursor = Tick();
    Trace("Native endmovie %s cursor=%s counter=%llu state=%d", phase, cursor.dump().c_str(),
          Read<uint64_t>(currentMovieObject, 0x138), Read<int>(currentMovieObject, 0x168));
}

bool NewReadPixels(void* self, void* texture, const int* sourceRect, int unknown3,
                   int unknown4, const int* destRect, void* pixels, int format, int unknown8) {
    const auto returnRva = reinterpret_cast<BYTE*>(_ReturnAddress()) - engineBase;
    const bool nativeScreenshot = returnRva == ProfileRva(0x1e88c6, 0x1e88b6);
    int width = 0, height = 0;
    json row;
    if (ledger && nativeScreenshot) {
        {
            std::lock_guard<std::mutex> lock(ledgerMutex);
            row = {{"event", "pixel_readback"}, {"readback_index", readbackCounter++},
                   {"qpc_before", Qpc()},
                   {"submission_candidate", lastMovie}};
        }
        row["replay_demo_tick"] = Tick();
        row["image_format"] = format;
        row["native_argument9"] = unknown8;
        row["native_caller_rva"] = CurrentBuildProfile() ? "0x1e88b6" : "0x1e88c6";
        row["clock_on_engine_thread"] = GetCurrentThreadId() == engineThread;
        row["native_observation"] = Observation::Snapshot("pixel_readback_before");
        row["native_clock"] = ClockTrace::Snapshot("pixel_readback_before");
    }
    const bool result = originalReadPixels(self, texture, sourceRect, unknown3, unknown4,
                                           destRect, pixels, format, unknown8);
    if (!row.is_null()) {
        row["qpc_after"] = Qpc();
        row["success"] = result;
        row["replay_demo_tick_after"] = Tick();
        row["native_observation_after"] = Observation::Snapshot("pixel_readback_after");
        row["native_clock_after"] = ClockTrace::Snapshot("pixel_readback_after");
        // Query only after the actual native readback has finished. The exact
        // screenshot caller obtains this texture description via the same slot.
        using TextureInfoFn = const BYTE* (*)(void*, void*);
        const auto info = reinterpret_cast<TextureInfoFn>(renderVtable[90])(self, texture);
        if (info) { width = Read<int16_t>(info, 0); height = Read<int16_t>(info, 2); }
        if (sourceRect && sourceRect[2] > 0 && sourceRect[3] > 0) {
            width = sourceRect[2]; height = sourceRect[3];
        }
        if (destRect && destRect[2] > 0 && destRect[3] > 0) {
            width = destRect[2]; height = destRect[3];
        }
        row["width"] = width; row["height"] = height;
        // Normalize byte-channel formats to RGB in native row order. A retained TGA
        // must match this hash (or its row-flipped counterpart) before association.
        int bytes = 0, red = 0, green = 1, blue = 2;
        switch (format) {
            case 0: bytes = 4; break;                         // RGBA8888
            case 1: bytes = 4; red = 3; green = 2; blue = 1; break; // ABGR8888
            case 2: bytes = 3; break;                         // RGB888
            case 3: bytes = 3; red = 2; blue = 0; break;       // BGR888
            case 11: bytes = 4; red = 1; green = 2; blue = 3; break; // ARGB8888
            case 12: case 16: bytes = 4; red = 2; blue = 0; break; // BGRA/BGRX
        }
        if (result && pixels && bytes && unknown8 == 0 && width > 0 && height > 0 && width <= 8192 && height <= 8192) {
            const size_t count = static_cast<size_t>(width) * height;
            std::vector<BYTE> raw(count * bytes);
            SIZE_T copied = 0;
            if (ReadProcessMemory(GetCurrentProcess(), pixels, raw.data(), raw.size(), &copied) && copied == raw.size()) {
                std::vector<BYTE> rgb(count * 3);
                for (size_t i = 0; i < count; ++i) {
                    rgb[i * 3] = raw[i * bytes + red];
                    rgb[i * 3 + 1] = raw[i * bytes + green];
                    rgb[i * 3 + 2] = raw[i * bytes + blue];
                }
                row["rgb_sha256"] = Sha256(rgb.data(), rgb.size());
                for (int y = 0; y < height / 2; ++y)
                    for (size_t x = 0; x < static_cast<size_t>(width) * 3; ++x)
                        std::swap(rgb[static_cast<size_t>(y) * width * 3 + x],
                                  rgb[static_cast<size_t>(height - 1 - y) * width * 3 + x]);
                row["rgb_sha256_flipped"] = Sha256(rgb.data(), rgb.size());
            } else row["pixel_hash_error"] = "buffer_unreadable";
        } else row["pixel_hash_error"] = "unsupported_format_or_extent_or_failed_readback";
        Write(std::move(row));
    }
    return result;
}

void Initialize() {
    if (attempted) return;
    if (CalibrationProfile() && CompetitiveProfile())
        PluginError("Capture profiles must not mix calibration and competitive replay");
    attempted = true;
    const char* path = nullptr;
    for (int i = 0; i + 1 < CommandLine()->ParmCount(); ++i)
        if (!strcmp(CommandLine()->GetParm(i), "-chicken-capture-log")) path = CommandLine()->GetParm(i + 1);
    if (!path) return;
    if (!std::filesystem::path(path).is_absolute()) PluginError("-chicken-capture-log requires an absolute path");
    engineThread = GetCurrentThreadId();
    engineBase = reinterpret_cast<BYTE*>(GetModuleHandleA("engine2.dll"));
    wchar_t modulePath[32768]{};
    if (!engineBase || !GetModuleFileNameW(reinterpret_cast<HMODULE>(engineBase), modulePath, 32768))
        PluginError("Capture hook could not identify loaded engine2.dll");
    std::ifstream file(std::filesystem::path(modulePath), std::ios::binary);
    std::vector<BYTE> image((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (image.empty() || Sha256(image.data(), image.size()) != ExpectedEngineSha())
        PluginError("Capture hook engine2.dll SHA256 mismatch; refusing version-specific hooks");
    movieVtable = reinterpret_cast<void**>(engineBase + 0x54c460);
    auto render = Read<void*>(engineBase, 0x688c68);
    if (!render || movieVtable[9] != engineBase + ProfileRva(0x100af0, 0x100ae0) ||
        movieVtable[2] != engineBase + ProfileRva(0x100280, 0x100270))
        PluginError("Capture hook layout verification failed");
    renderVtable = *reinterpret_cast<void***>(render);
    originalMovieFrame = reinterpret_cast<MovieFrameFn>(movieVtable[9]);
    originalMovieEnd = reinterpret_cast<MovieEndFn>(movieVtable[2]);
    originalReadPixels = reinterpret_cast<ReadPixelsFn>(renderVtable[74]);
    if (!originalReadPixels || !renderVtable[90]) PluginError("Capture hook render interface is incomplete");
    if (CurrentBuildProfile()) {
        auto renderModule = GetModuleHandleA("rendersystemdx11.dll");
        auto renderBase = reinterpret_cast<BYTE*>(renderModule);
        if (Observation::ModuleSha(renderModule) != "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500" ||
            renderVtable[74] != renderBase + 0x3f190 || renderVtable[90] != renderBase + 0x30190)
            PluginError("Calibration requires the inspected DX11 readback interface");
    }
    ledger = fopen(path, "wx");
    if (!ledger) PluginError("Capture evidence path must be new and writable: %s", path);
    const auto observation = Observation::Initialize();
    const auto packetTrace = PacketTrace::Initialize();
    const auto networkClock = ClockTrace::Initialize();
    LARGE_INTEGER qpcFrequency{}; QueryPerformanceFrequency(&qpcFrequency);
    Write({{"event", "header"}, {"schema_version", 1}, {"hook", "engine2.CMovieRecorder.movie_frame_submit+ReadTexturePixels"},
           {"qpc_frequency", qpcFrequency.QuadPart},
           {"engine_sha256", ExpectedEngineSha()}, {"native_profile", ProfileName()}, {"pixel_correspondence_validated", false},
           {"native_observation", observation},
           {"native_clock", networkClock},
           {"packet_trace", packetTrace},
           {"clock", "IDemoFile.GetDemoTick"}, {"fraction_available", false},
           {"training_ready", false}, {"readback_hash", "SHA256_RGB_native_rows_and_flipped_rows"},
           {"render_clock", {{"field", "EventClientOutput_t.m_flRenderTime"}, {"units", "seconds"},
                             {"event_caller_rva", CurrentBuildProfile() ? "0x1e5b48" : "0x1e5b58"}, {"field_offset", 40},
                             {"execution_clock_calibrated", false},
                             {"sdk_commit", "bd17582be4bc18970e4fe4c518359872fda8276d"}}},
           {"native_epoch", {{"field", "IDemoFile.GetDemoStartTick"}, {"getter_rva", "0x35a80"},
                             {"identity", "GetDemoTick = native_client_tick - demo_start_tick"},
                             {"execution_clock_equivalence_verified", false}}}});
    MemoryBarrier();
    PatchVTableEntry(movieVtable, 9, reinterpret_cast<void*>(&NewMovieFrame));
    PatchVTableEntry(movieVtable, 2, reinterpret_cast<void*>(&NewMovieEnd));
    PatchVTableEntry(renderVtable, 74, reinterpret_cast<void*>(&NewReadPixels));
    PacketTrace::Install();
    ClockTrace::Install();
    Trace("Native capture evidence hooks installed for verified engine2.dll; raw ledger=%s", path);
}
} // namespace ChickenCapture
