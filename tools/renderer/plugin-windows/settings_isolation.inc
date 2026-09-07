// Settings isolation for one experimentally verified CS2 binary pair.
// See SETTINGS_ISOLATION.md. This does not control the separate Steam process.
#include "filesystem.h"
#include "tier1/bufferstring.h"

namespace ChickenSettings {
constexpr const char* Policy = "CHICKEN_SETTINGS_ISOLATION_V1";
constexpr const char* FileSystemSha = "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef";
constexpr const char* StorageVersion = "STEAMREMOTESTORAGE_INTERFACE_VERSION016";
constexpr size_t StorageContextRva = 0x60f1f0;
constexpr size_t StorageImportRva = 0x482698;
using FindInterfaceFn = void* (*)(int, const char*);
FindInterfaceFn originalFindInterface = nullptr;
void** importSlot = nullptr;
BYTE* guardedEngine = nullptr;
std::atomic<uint64_t> blockedRequests{0};
std::filesystem::path settingsRoot;
std::filesystem::path reportPath;
IFileSystem* guardedFileSystem = nullptr;
json isolationReport;
bool installed = false;
bool ready = false;
uint64_t frameChecks = 0;

template<class T> T SafeRead(const void* address) {
    T value{};
    SIZE_T copied = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), address, &value, sizeof(value), &copied) || copied != sizeof(value))
        PluginError("Settings isolation: unreadable guarded address");
    return value;
}

std::filesystem::path NormalPath(const std::filesystem::path& path) {
    if (!path.is_absolute()) PluginError("Settings isolation requires absolute paths");
    return std::filesystem::weakly_canonical(path).lexically_normal();
}

bool SamePath(const std::filesystem::path& first, const std::filesystem::path& second) {
    return _wcsicmp(NormalPath(first).c_str(), NormalPath(second).c_str()) == 0;
}

string ModuleHash(HMODULE module) {
    wchar_t filename[32768]{};
    auto count = GetModuleFileNameW(module, filename, static_cast<DWORD>(std::size(filename)));
    if (count == 0 || count >= std::size(filename)) PluginError("Settings isolation: module path unavailable");
    std::ifstream file(std::filesystem::path(filename), std::ios::binary);
    if (!file) PluginError("Settings isolation: cannot read module for identity check");
    std::vector<BYTE> bytes((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (file.bad() || bytes.empty()) PluginError("Settings isolation: incomplete module identity read");
    return ChickenCapture::Sha256(bytes.data(), bytes.size());
}

std::filesystem::path RequiredPath(const char* flag) {
    const char* value = nullptr;
    for (int i = 0; i < CommandLine()->ParmCount(); ++i) {
        if (strcmp(CommandLine()->GetParm(i), flag) != 0) continue;
        if (value || i + 1 >= CommandLine()->ParmCount())
            PluginError("Settings isolation: missing or repeated %s", flag);
        value = CommandLine()->GetParm(i + 1);
    }
    if (!value || !*value) PluginError("Settings isolation: required argument %s is absent", flag);
    return NormalPath(std::filesystem::u8path(value));
}

void Persist(const char* observation) {
    isolationReport["last_observation"] = observation;
    isolationReport["cloud_blocked_requests"] = blockedRequests.load();
    const string body = isolationReport.dump(2) + "\n";
    const auto temporary = std::filesystem::path(reportPath.wstring() + L".native-tmp");
    HANDLE output = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (output == INVALID_HANDLE_VALUE) PluginError("Settings isolation: cannot create owned report temporary file");
    DWORD written = 0;
    const bool ok = WriteFile(output, body.data(), static_cast<DWORD>(body.size()), &written, nullptr) &&
                    written == body.size() && FlushFileBuffers(output);
    CloseHandle(output);
    if (!ok || !MoveFileExW(temporary.c_str(), reportPath.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        PluginError("Settings isolation: cannot persist guard report");
}

void* FindInterface(int user, const char* name) {
    if (name && strcmp(name, StorageVersion) == 0) {
        const auto count = blockedRequests.fetch_add(1) + 1;
        if (count == 1) Trace("Settings isolation blocked engine RemoteStorage016 acquisition");
        return nullptr;
    }
    return originalFindInterface(user, name);
}

void VerifyCloudGuard() {
    if (!installed || SafeRead<void*>(importSlot) != reinterpret_cast<void*>(&FindInterface))
        PluginError("Settings isolation: engine Cloud import guard is not installed");
    // Steam's context initializer caches its interface pointer at context+16.
    // Its call sites in this exact engine check null before every config access.
    if (SafeRead<void*>(guardedEngine + StorageContextRva + 16) != nullptr)
        PluginError("Settings isolation: engine obtained a config Cloud interface");
}

void InstallEarly() {
    if (installed) return;
    settingsRoot = RequiredPath("-chicken-render-settings");
    reportPath = RequiredPath("-chicken-render-isolation");
    const auto runRoot = NormalPath(std::filesystem::u8path(pluginLogPath).parent_path());
    if (!SamePath(settingsRoot, runRoot / "replay-settings") ||
        !SamePath(reportPath, runRoot / "settings-isolation.json") ||
        !std::filesystem::is_directory(settingsRoot / "cfg") || std::filesystem::exists(reportPath))
        PluginError("Settings isolation paths must name the runner's new cloned profile and report");
    wchar_t envPath[32768]{};
    const auto envLength = GetEnvironmentVariableW(L"USRLOCALCSGO", envPath, static_cast<DWORD>(std::size(envPath)));
    if (envLength == 0 || envLength >= std::size(envPath) || !SamePath(settingsRoot, std::filesystem::path(envPath)))
        PluginError("Settings isolation requires matching child-process USRLOCALCSGO before startup");

    const auto engineModule = GetModuleHandleW(L"engine2.dll");
    if (!engineModule || ModuleHash(engineModule) != ChickenCapture::EngineSha)
        PluginError("Settings isolation: unsupported engine2.dll identity");
    guardedEngine = reinterpret_cast<BYTE*>(engineModule);
    if (SafeRead<void*>(guardedEngine + StorageContextRva) != guardedEngine + 0x1f2430)
        PluginError("Settings isolation: unsupported engine RemoteStorage context initializer");
    const auto previousCounter = SafeRead<uint64_t>(guardedEngine + StorageContextRva + 8);
    const auto previousInterface = SafeRead<void*>(guardedEngine + StorageContextRva + 16);
    Trace("Settings isolation earliest factory Cloud context counter=%llu interface=%p", previousCounter, previousInterface);
    if (previousCounter != 0 || previousInterface != nullptr)
        PluginError("Settings isolation loaded after engine config Cloud context use; earlier injection is required");

    const auto steamModule = GetModuleHandleW(L"steam_api64.dll");
    const auto expected = steamModule ? GetProcAddress(steamModule, "SteamInternal_FindOrCreateUserInterface") : nullptr;
    importSlot = reinterpret_cast<void**>(guardedEngine + StorageImportRva);
    if (!expected || SafeRead<void*>(importSlot) != reinterpret_cast<void*>(expected))
        PluginError("Settings isolation: engine Steam import differs from the verified original");
    // Keep this DLL resident through engine configuration shutdown. No new
    // process/service is created; Windows releases it when this CS2 process ends.
    HMODULE pinnedSelf = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           reinterpret_cast<LPCWSTR>(&FindInterface), &pinnedSelf))
        PluginError("Settings isolation: cannot pin hook until process exit");
    originalFindInterface = reinterpret_cast<FindInterfaceFn>(expected);
    DWORD previousProtection = 0;
    if (!VirtualProtect(importSlot, sizeof(void*), PAGE_READWRITE, &previousProtection))
        PluginError("Settings isolation: cannot protect Steam import slot");
    auto replaced = InterlockedCompareExchangePointer(importSlot, reinterpret_cast<void*>(&FindInterface), reinterpret_cast<void*>(expected));
    DWORD ignored = 0;
    const bool protectedAgain = VirtualProtect(importSlot, sizeof(void*), previousProtection, &ignored) != 0;
    if (replaced != reinterpret_cast<void*>(expected) || !protectedAgain)
        PluginError("Settings isolation: cannot atomically install Steam import guard");
    installed = true;
    VerifyCloudGuard();
    isolationReport = {{"schema_version", 1}, {"policy", Policy}, {"status", "installed_pending_path_verification"},
        {"pid", GetCurrentProcessId()}, {"settings_root", settingsRoot.u8string()},
        {"engine_sha256", ChickenCapture::EngineSha}, {"filesystem_sha256", FileSystemSha},
        {"cloud_hook_installed", true}, {"cloud_interface", StorageVersion},
        {"cloud_cache_uninitialized_at_install", true}, {"startup_guard_passed", true},
        {"local_path_verified", false}, {"scope", "engine2_user_config_remote_storage"},
        {"steam_client_process_isolated", false}, {"hook_lifetime", "owned_cs2_process"},
        {"primary_source_commit", "03f972b4710ab9a8b1601fc0c34ce33c9c0268cc"}};
    Persist("server_factory_before_real_server_load");
    Trace("Settings isolation %s installed before real server load", Policy);
}

void VerifyPaths() {
    if (!guardedFileSystem) {
        const auto fsModule = GetModuleHandleW(L"filesystem_stdio.dll");
        if (!fsModule || ModuleHash(fsModule) != FileSystemSha)
            PluginError("Settings isolation: unsupported filesystem_stdio.dll identity");
        const auto create = reinterpret_cast<CreateInterfaceFn>(GetProcAddress(fsModule, "CreateInterface"));
        guardedFileSystem = create ? static_cast<IFileSystem*>(create("VFileSystem017", nullptr)) : nullptr;
        const auto fsBase = reinterpret_cast<BYTE*>(fsModule);
        if (!guardedFileSystem || SafeRead<void*>(guardedFileSystem) != fsBase + 0x1afc78)
            PluginError("Settings isolation: unexpected filesystem interface/vtable");
        const auto table = SafeRead<void**>(guardedFileSystem);
        if (SafeRead<void*>(table + 42) != fsBase + 0x4f3a0 || SafeRead<void*>(table + 43) != fsBase + 0x54480)
            PluginError("Settings isolation: unsupported filesystem path-query methods");
    }
    CBufferStringN<32768> searchPaths;
    if (!guardedFileSystem->GetSearchPath("USRLOCAL", GET_SEARCH_PATH_ALL, searchPaths, 64))
        PluginError("Settings isolation: USRLOCAL search path is missing");
    std::istringstream paths(searchPaths.Get());
    string path;
    size_t count = 0;
    while (std::getline(paths, path, ';')) {
        if (path.empty()) continue;
        if (!SamePath(settingsRoot, std::filesystem::u8path(path)))
            PluginError("Settings isolation: USRLOCAL searches outside the cloned settings directory");
        ++count;
    }
    if (count != 1) PluginError("Settings isolation: ambiguous USRLOCAL search path");
    CBufferStringN<32768> writePath;
    // Query only: this probe filename is never created by the plugin.
    const char* probe = "cfg/chicken-isolation-probe.vcfg";
    if (!guardedFileSystem->GetWritePath(probe, "USRLOCAL", writePath) ||
        !SamePath(std::filesystem::u8path(writePath.Get()), settingsRoot / probe))
        PluginError("Settings isolation: USRLOCAL writes outside the cloned settings directory");
    isolationReport["usrlocal_search_path"] = searchPaths.Get();
    isolationReport["usrlocal_write_path"] = writePath.Get();
    isolationReport["local_path_verified"] = true;
}

void BeforeCommands() {
    VerifyCloudGuard();
    if (!ready || (++frameChecks % 64) == 0) VerifyPaths();
    if (!ready) {
        isolationReport["status"] = "ready";
        Persist("before_first_capture_command");
        ready = true;
        Trace("Settings isolation ready; config reads/writes use %s", settingsRoot.u8string().c_str());
    }
}

void ObserveShutdown() {
    if (!installed) return;
    VerifyCloudGuard();
    // The IAT hook remains installed: subsequent engine shutdown can still save
    // settings. This snapshot is not a claim that all shutdown I/O has finished.
    Persist("server_shutdown_guard_still_installed");
}
} // namespace ChickenSettings
