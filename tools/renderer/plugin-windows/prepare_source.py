"""Create a Windows adaptation without modifying the pinned upstream checkout.

The upstream sources retain their original license. Every replacement is guarded
so an upstream change requires an explicit review instead of a partial patch.
This does not establish compatibility with a newer CS2 interface layout.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def adapt_source(source: str) -> str:
    def replace(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise ValueError(f"Expected one upstream source anchor: {old!r}")
        source = source.replace(old, new, 1)

    replace('#include <atomic>', '#include <atomic>\n#include <windows.h>\n#include <process.h>\n#include <filesystem>')
    replace('#define CON_COMMAND_ENABLED 1',
            '// No plugin-owned console commands: the optional dem_render_info static\n'
            '// destructor would unregister after engine teardown in our pinned DLL.\n'
            '// Existing engine commands/CVars and their unhide path remain available.')
    replace('return GetProcAddress((HMODULE)lib, name);',
            'return reinterpret_cast<void*>(GetProcAddress((HMODULE)lib, name));')
    replace('return LoadLibrary(path);', 'return LoadLibraryA(path);')
    replace('typedef void (*AppSystemShutdownFn)();',
            'typedef void (*AppSystemShutdownFn)(IAppSystem* appSystem);')
    replace('const char* demoPath = NULL;',
            'const char* demoPath = NULL;\n'
            'string pluginLogPath;\n'
            'bool demoConfigurationLoaded = false;')
    replace('FILE* pFile = fopen("dem-render.log", "a");',
            'FILE* pFile = fopen(pluginLogPath.c_str(), "a");')
    replace('remove("dem-render.log");', 'remove(pluginLogPath.c_str());')
    replace('void DeleteLogFile()',
            'void Trace(const char* msg, ...)\n'
            '{\n'
            '    va_list args;\n'
            '    va_start(args, msg);\n'
            '    char buf[1024] = {};\n'
            '    vsnprintf(buf, sizeof(buf), msg, args);\n'
            '    va_end(args);\n'
            '    LogToFile(buf);\n'
            '}\n\n'
            'void DeleteLogFile()')
    replace('        Plat_MessageBox("Error", buf);\n        Plat_ExitProcess(1);',
            '        // Log and terminate rather than blocking the worker on a modal dialog.\n'
            '        LogToFile(buf);\n'
            '        OutputDebugStringA(buf);\n'
            '        ExitProcess(1);')
    replace('    RestoreGameinfoFile();',
            '    // The Windows runner owns byte-exact restoration and its own backup.\n'
            '    // Never consume an unrelated gameinfo.gi.backup.\n'
            '    #ifndef _WIN32\n'
            '        RestoreGameinfoFile();\n'
            '    #endif')
    replace('bool Connect(IAppSystem* appSystem, CreateInterfaceFn factoryFn)',
            '#include "hook_fallback.inc"\n\n'
            'bool Connect(IAppSystem* appSystem, CreateInterfaceFn factoryFn)')
    replace('void NewFrameStageNotify(void* thisptr, ClientFrameStage_t stage)',
            'namespace ChickenSession { void Frame(const json& row); }\n'
            '#include "capture_trace.inc"\n'
            '#include "session_capture.inc"\n'
            '#include "settings_isolation.inc"\n'
            '#include "calibration_trace.inc"\n\n'
            'void NewFrameStageNotify(void* thisptr, ClientFrameStage_t stage)')
    replace('    // Drain commands queued from other contexts (e.g. setup commands from ClientFullyConnect).',
            '    ChickenSettings::BeforeCommands();\n'
            '    ChickenCapture::Initialize();\n\n'
            '    ChickenSettings::ObserveCompetitiveResourcePaths();\n\n'
            '    ChickenCapture::ClockTrace::BeforeCommands();\n\n'
            '    ChickenCalibration::BeforeCommands();\n\n'
            '    // Drain commands queued from other contexts (e.g. setup commands from ClientFullyConnect).')
    replace('                    engine->ExecuteClientCmd(0, action.cmd.c_str(), true);',
            '                    ChickenCapture::ObserveCommand(action.cmd, "before");\n'
            '                    engine->ExecuteClientCmd(0, action.cmd.c_str(), true);\n'
            '                    ChickenCapture::ObserveCommand(action.cmd, "after");')
    replace('    {\n        std::lock_guard<std::mutex> lock(sequencesMutex);\n        if (newTick != currentTick',
            '    if (ChickenSession::Step(engine, newTick)) {\n'
            '        originalFrameStageNotify(thisptr, stage);\n        return;\n    }\n\n'
            '    {\n        std::lock_guard<std::mutex> lock(sequencesMutex);\n        if (newTick != currentTick')
    replace('    return result;\n}\n\n\nvoid Shutdown()',
            '    StartClientHookFallback();\n'
            '    return result;\n}\n\n\nvoid Shutdown()')
    replace('    isQuitting = true;\n\n    if (serverConfigShutdown != NULL)',
            '    isQuitting = true;\n'
            '    ChickenSettings::ObserveShutdown();\n'
            '    StopClientHookFallback();\n\n'
            '    if (serverConfigShutdown != NULL)')
    replace('void Shutdown()\n{', 'void Shutdown(IAppSystem* appSystem)\n{')
    replace('        serverConfigShutdown();', '        serverConfigShutdown(appSystem);')
    replace('    factory = factoryFn;\n    bool result = serverConfigConnect(appSystem, factory);',
            '    Trace("Connect enter appSystem=%p factory=%p original=%p", appSystem, factoryFn, serverConfigConnect);\n'
            '    factory = factoryFn;\n'
            '    bool result = serverConfigConnect(appSystem, factory);\n'
            '    Trace("Connect original returned %d", result);')
    replace('    g_pCVar = (ICvar*)factory("VEngineCvar007", NULL);',
            '    if (!result || factory == NULL) {\n'
            '        return false;\n'
            '    }\n'
            '    Trace("Resolving VEngineCvar007");\n'
            '    g_pCVar = (ICvar*)factory("VEngineCvar007", NULL);\n'
            '    Trace("Resolved VEngineCvar007=%p", g_pCVar);\n'
            '    if (g_pCVar == NULL) {\n'
            '        PluginError("Required VEngineCvar007 interface is unavailable");\n'
            '        return false;\n'
            '    }')
    replace('    UnhideCommandsAndCvars();',
            '    Trace("UnhideCommandsAndCvars begin");\n'
            '    UnhideCommandsAndCvars();\n'
            '    Trace("UnhideCommandsAndCvars completed");')
    replace('        ConVar_Register();',
            '        Trace("ConVar_Register begin");\n'
            '        ConVar_Register();\n'
            '        Trace("ConVar_Register completed");')
    replace('EXPORT void* CreateInterface(const char* pName, int* pReturnCode)',
            'extern "C" __declspec(dllexport) void* CreateInterface(const char* pName, int* pReturnCode)')
    replace('        DeleteLogFile();\n        AssertInsecureParameterIsPresent();',
            '        // The runner passes an absolute path inside its own output directory.\n'
            '        for (int i = 0; i + 1 < CommandLine()->ParmCount(); ++i) {\n'
            '            if (strcmp(CommandLine()->GetParm(i), "-chicken-render-log") == 0) {\n'
            '                const char* candidate = CommandLine()->GetParm(i + 1);\n'
            '                if (!std::filesystem::path(candidate).is_absolute()) {\n'
            '                    PluginError("-chicken-render-log requires an absolute path");\n'
            '                }\n'
            '                pluginLogPath = candidate;\n'
            '                break;\n'
            '            }\n'
            '        }\n'
            '        if (pluginLogPath.empty()) {\n'
            '            PluginError("Windows replay plugin requires -chicken-render-log with an absolute run-owned path");\n'
            '        }\n'
            '        DeleteLogFile();\n'
            '        AssertInsecureParameterIsPresent();\n'
            '        ChickenSettings::InstallEarly();\n'
            '        LogToFile("Experimental Windows replay plugin loaded; game ABI is unverified");')
    replace('    void* original = serverCreateInterface(pName, pReturnCode);\n    auto vtable = *(void***)original;',
            '    if (pName == NULL) {\n'
            '        if (pReturnCode != NULL) *pReturnCode = 1;\n'
            '        return NULL;\n'
            '    }\n'
            '    Trace("CreateInterface enter %s", pName);\n'
            '    void* original = serverCreateInterface(pName, pReturnCode);\n'
            '    Trace("CreateInterface original %s returned %p", pName, original);\n'
            '    if (original == NULL) {\n'
            '        // Factories are routinely probed for interfaces they do not expose.\n'
            '        return NULL;\n'
            '    }\n'
            '    auto vtable = *(void***)original;')
    replace('        void* serverModule = LoadLib(libPath.c_str());',
            '        Trace("Loading real server from %s", libPath.c_str());\n'
            '        void* serverModule = LoadLib(libPath.c_str());\n'
            '        Trace("Loaded real server module=%p", serverModule);')
    replace('        serverConfigConnect = (AppSystemConnectFn)vtable[0];',
            '        Trace("Hooking server config vtable=%p Connect=%p Shutdown=%p", vtable, vtable[0], vtable[4]);\n'
            '        serverConfigConnect = (AppSystemConnectFn)vtable[0];')
    replace('    return original;\n}',
            '    Trace("CreateInterface return %s = %p", pName, original);\n'
            '    return original;\n}')
    replace('if (strcmp(pName, "Source2ServerConfig001") == 0)',
            'if (strcmp(pName, "Source2ServerConfig001") == 0 && serverConfigConnect == NULL)')
    replace('} else if (strcmp(pName, "Source2GameClients001") == 0)',
            '} else if (strcmp(pName, "Source2GameClients001") == 0 && originalClientFullyConnect == NULL)')
    replace('    if (demoPath == NULL) {\n        int paramCount',
            '    if (!demoConfigurationLoaded) {\n'
            '        demoConfigurationLoaded = true;\n'
            '        int paramCount')
    callback_start = source.index('void NewClientFullyConnect(void* thisptr, int playerSlot)')
    callback_end = source.index('\nextern "C" __declspec(dllexport) void* CreateInterface', callback_start)
    old_callback = source[callback_start:callback_end]
    replace(old_callback,
            'void NewClientFullyConnect(void* thisptr, int playerSlot)\n'
            '{\n'
            '    Log("ClientFullyConnect: playerSlot=%d", playerSlot);\n'
            '    TryInstallClientHook();\n'
            '    originalClientFullyConnect(thisptr, playerSlot);\n'
            '}\n')
    return source


def adapt_sdk_icvar(source: str) -> str:
    """Backport the verified July 2026 ICvar layout to the pinned SDK.

    Mirrors AlliedModders/hl2sdk commit
    11089e8737dd20a830d301fe81cf3bb5cb23bd88 (2026-07-09).
    The first two local trial dumps demonstrate the old GetConCommandData
    slot 46 dispatching into non-executable tier0 data. Removing these two
    obsolete virtual entries makes its dispatch slot 44, as upstream defines.
    """
    replacements = [
        ('virtual int\t\t\t\tGetMaxSplitScreenSlots() const = 0;',
         'int GetMaxSplitScreenSlots() const { return m_MaxSplitScreenSlots; }'),
        ('\tvirtual void\t\t\tunk001() = 0;\n\n', ''),
        ('virtual void\t\t\t\tQueueThreadSetValue( ConVarRefAbstract* ref, CSplitScreenSlot nSlot, void* __unk01, CVValue_t* value ) = 0;\n};',
         'virtual void\t\t\t\tQueueThreadSetValue( ConVarRefAbstract* ref, CSplitScreenSlot nSlot, void* __unk01, CVValue_t* value ) = 0;\n\n'
         'private:\n\tint m_MaxSplitScreenSlots;\n};'),
        ('\tint m_SplitScreenSlots;\n\n', ''),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError(f"Expected one pinned SDK ICvar anchor: {old!r}")
        source = source.replace(old, new, 1)
    return source


def adapt_client_interfaces(source: str) -> str:
    """Use slots from advancedfx-prop 2e1366353c50d40150276c5d580b0eecb183881b.

    Its cs2/sdk_src/public/cdll_int.h describes ExecuteClientCmd at 51,
    GetDemoFile at 69, and the returned demo object's tick accessor at 3.
    Preserve our method names while correcting their Windows dispatch layout.
    """
    replacements = [
        ('    virtual int _Unknown_002(void) = 0;\n'
         '    virtual int _GetDemoTick(void) = 0; // :003 see :004',
         '    virtual int GetDemoStartTick(void) = 0; //:002\n'
         '    virtual int GetDemoTick(void) = 0; //:003'),
        ('#endif\n    virtual int GetDemoTick(void) = 0; //:004 points to the same function as :003 on Windows',
         '    virtual int GetDemoTick(void) = 0; //:004 Linux layout from pinned upstream\n'
         '#endif\n'
         '#if defined _WIN32\n'
         '    virtual void _Unknown_004(void) = 0;\n'
         '#endif'),
        ('    virtual void ExecuteClientCmd(int iUnk0MaybeSplitScreenSlotSetTo0, const char* pszCommands, bool bUnk2SetToTrue) = 0; //:050',
         '    virtual void _Unknown_050(void) = 0;\n'
         '    virtual void ExecuteClientCmd(int iUnk0MaybeSplitScreenSlotSetTo0, const char* pszCommands, bool bUnk2SetToTrue) = 0; //:051'),
        ('virtual void GetScreenSize(int& width, int& height) = 0; //:060',
         'virtual void GetScreenSize(int& width, int& height) = 0; //:061'),
        ('virtual char const* GetLevelName(void) = 0; //:063',
         'virtual char const* GetLevelName(void) = 0; //:064'),
        ('virtual char const* GetLevelNameShort(void) = 0; //:064',
         'virtual char const* GetLevelNameShort(void) = 0; //:065'),
        ('virtual IDemoPlayer* GetDemoPlayer(void) = 0; //:068',
         'virtual IDemoPlayer* GetDemoPlayer(void) = 0; //:069 (GetDemoFile in current HLAE SDK)'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError(f"Expected one pinned client-interface anchor: {old!r}")
        source = source.replace(old, new, 1)
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = (args.upstream / "main.cpp").read_text(encoding="utf-8")
    transformed = adapt_source(original)
    header = adapt_client_interfaces((args.upstream / "cdll_interfaces.h").read_text(encoding="utf-8"))
    fallback = Path(__file__).with_name("hook_fallback.inc").read_bytes()
    icvar = adapt_sdk_icvar((args.sdk / "public" / "icvar.h").read_text(encoding="utf-8"))
    convar_source = (args.sdk / "tier1" / "convar.cpp").read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "main.cpp").write_text(transformed, encoding="utf-8", newline="\n")
    (args.output / "cdll_interfaces.h").write_text(header, encoding="utf-8", newline="\n")
    (args.output / "hook_fallback.inc").write_bytes(fallback)
    (args.output / "capture_trace.inc").write_bytes(Path(__file__).with_name("capture_trace.inc").read_bytes())
    (args.output / "session_capture.inc").write_bytes(Path(__file__).with_name("session_capture.inc").read_bytes())
    (args.output / "observation_trace.inc").write_bytes(Path(__file__).with_name("observation_trace.inc").read_bytes())
    (args.output / "clock_trace.inc").write_bytes(Path(__file__).with_name("clock_trace.inc").read_bytes())
    (args.output / "packet_trace.inc").write_bytes(Path(__file__).with_name("packet_trace.inc").read_bytes())
    (args.output / "settings_isolation.inc").write_bytes(Path(__file__).with_name("settings_isolation.inc").read_bytes())
    (args.output / "calibration_trace.inc").write_bytes(Path(__file__).with_name("calibration_trace.inc").read_bytes())
    (args.output / "icvar.h").write_text(icvar, encoding="utf-8", newline="\n")
    (args.output / "convar.cpp").write_bytes(convar_source)


if __name__ == "__main__":
    main()
