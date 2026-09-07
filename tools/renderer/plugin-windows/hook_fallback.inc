// Experimental lifecycle adaptation for post-August-2026 demo playback.
// ClientFullyConnect may not fire for demos. Resolve the loaded client DLL's
// own exported factory rather than polling the engine's mutable app registry.
// Engine commands are only queued here; FrameStageNotify executes them.
std::mutex clientHookMutex;
HANDLE clientHookWorker = NULL;

bool TryInstallClientHook()
{
    std::lock_guard<std::mutex> lock(clientHookMutex);
    if (isQuitting) return false;
    if (client != NULL) return true;

    HMODULE module = GetModuleHandleA("client.dll");
    if (module == NULL) return false;
    auto clientFactory = reinterpret_cast<CreateInterfaceFn>(GetProcAddress(module, "CreateInterface"));
    if (clientFactory == NULL) return false;
    auto candidate = static_cast<ISource2Client*>(clientFactory("Source2Client002", NULL));
    if (candidate == NULL) return false;

    auto vtable = *reinterpret_cast<void***>(candidate);
    if (vtable == NULL || vtable[36] == NULL) {
        PluginError("Source2Client002 has no expected frame-stage entry; aborting");
        return false;
    }
    // Publish the original function before the engine can see the patched entry.
    originalFrameStageNotify = reinterpret_cast<FrameStageNotifyFn>(vtable[36]);
    QueueEngineCommand("demo_ui_mode 0");
    QueueEngineCommand("sv_cheats 1");
    MemoryBarrier();
    PatchVTableEntry(vtable, 36, reinterpret_cast<void*>(&NewFrameStageNotify));
    client = candidate;
    LogToFile("Hooked FrameStageNotify (client.dll factory, experimental lifecycle fallback)");
    return true;
}

unsigned __stdcall ClientHookWorker(void*)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
    while (!isQuitting && std::chrono::steady_clock::now() < deadline) {
        if (TryInstallClientHook()) return 0;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    if (!isQuitting) {
        LogToFile("ERROR: Source2Client002 frame hook unavailable after 60 seconds; replay commands cannot execute");
    }
    return 0;
}

void StartClientHookFallback()
{
    if (clientHookWorker != NULL || isQuitting) return;
    if (TryInstallClientHook()) return;
    LogToFile("Waiting up to 60 seconds for client.dll Source2Client002");
    clientHookWorker = reinterpret_cast<HANDLE>(_beginthreadex(NULL, 0, &ClientHookWorker, NULL, 0, NULL));
    if (clientHookWorker == NULL) {
        PluginError("Could not start the bounded client-hook worker");
    }
}

void StopClientHookFallback()
{
    // Shutdown sets isQuitting before joining. Never hold clientHookMutex here.
    if (clientHookWorker == NULL) return;
    const DWORD result = WaitForSingleObject(clientHookWorker, 3000);
    if (result != WAIT_OBJECT_0) {
        // Do not permit DLL teardown while a worker might still execute its code.
        PluginError("Client-hook worker did not stop before shutdown; aborting process");
    }
    CloseHandle(clientHookWorker);
    clientHookWorker = NULL;
}
