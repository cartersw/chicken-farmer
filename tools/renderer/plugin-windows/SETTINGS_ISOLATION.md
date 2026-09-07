# Native settings isolation

`CHICKEN_SETTINGS_ISOLATION_V1` passed the live `windows-settings-012` trial:
64 frames and an MP4 were produced, CS2 exited with code zero, and the startup
Cloud and local-path guards remained valid through the server shutdown check.
The recovery journal verified all 39 selected settings files with zero restore
operations; gameinfo was restored and the staged plugin was moved out of CS2.
This verifies one bounded trial on the exact binaries below, not isolation of
every Steam process or game file.
The user subsequently reported Steam Offline Mode for this session; the trial
did not independently record that mode. Online Steam Cloud synchronization and
reconnection remain unverified. The observed native interface block is separate
evidence and is not inferred from Steam being offline.

The tested Release build at `../build/plugin-windows/Release/server.dll` has SHA-256
`0f9c1f0c4c4a6275960d48d2df8a9b1b5684040a490f9cd9ad484989d1c3823c`.
MSVC compilation and linking passed. The policy marker remains present and the
faulting optional console-command object/destructor is absent from DLL/MAP.
The runner must verify the native report and compare backed-up personal files
after the owned process exits. It must retain recovery information on failures.

The plugin requires `-insecure`, its existing absolute run-owned log argument,
and these additional arguments:

```text
-chicken-render-settings <run-output>/replay-settings
-chicken-render-isolation <run-output>/settings-isolation.json
```

The runner creates `replay-settings/cfg` from backed-up preferences before
launch and sets **only the child process's** `USRLOCALCSGO` environment variable
to `replay-settings`. The plugin requires that these paths belong to the log's
parent directory and that the report does not already exist. It never copies,
overwrites, or deletes the user's settings. Its only settings-related disk
write is the report and its temporary file in the run output directory.

Local files and the engine's Steam Cloud interface are separate controls.
The exact installed engine already reads `USRLOCALCSGO` before choosing its
normal Steam userdata directory. The plugin verifies the actual filesystem
search and write paths before any of its queued capture commands execute.
It does not set `host_readconfig_ignore_userconfig`, which would also disable
loading preferences. Preferences remain available from the cloned profile.

The Cloud hook changes one import pointer **inside this CS2 process**. It
returns null for the engine's `STEAMREMOTESTORAGE_INTERFACE_VERSION016`
requests and forwards all other interface requests unchanged. It does not
change the user's Steam Cloud preference, Steam launch options, registry,
or files in the Steam client. This mechanism follows the
[HLAE implementation at commit 03f972b4710ab9a8b1601fc0c34ce33c9c0268cc](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/main.cpp#L1804).

HLAE installs that import hook when `engine2.dll` loads. Our server plugin
loads later. Consequently, the plugin checks the engine's configuration
RemoteStorage context **before** loading the real server DLL: both its cached
counter and interface pointer must still be zero. A previous use causes an
immediate abort, requiring an earlier-loader implementation rather than a
claim that a late hook protects startup. After installation, the cached
interface must remain null and the import must still point to our wrapper.
These conditions are checked each engine frame before our commands.

The native DLL is pinned in its owning CS2 process because engine shutdown
can save configuration after the server shutdown callback. The hook remains
installed until Windows destroys that process. This does not create a
service, background process, scheduled task, or persistent injection path.

## First runtime trial and bounded shutdown fix

`windows-settings-011` used DLL SHA-256
`746e391b7aee6b8d37794c877a78ad7f6884bed8fdb5b48b51506d0d40bffc69`.
The earliest factory observed context counter zero and a null cached interface;
the first subsequent engine RemoteStorage016 acquisition was blocked. Native
USRLOCAL search and write-path queries both resolved into the clone before
any queued capture commands. The shutdown guard snapshot also completed.
Its report SHA-256 is
`88c9c8509f075d753d64e9254155ebe6427937c717f98ffc6005405326fb1533`.

The Windows crash dump `cs2.exe.55292.dmp` records read access violation
`0xc0000005` in `tier0.dll+0x6a58e` at address `0x66a0`, during DLL process
detach. Matching plugin MAP entries identify this call chain:

```text
CRT process detach/onexit
  dem_render_info_command static destructor  [plugin return RVA 0x1f8d80]
  ConCommand::~ConCommand                     [0x7b273]
  ConCommand::Destroy                         [0xa8c53]
  UnRegisterConCommand                        [0xab261]
  tier0 command registry                     [fault RVA 0x6a58e]
```

The tier0 instruction dereferences a command-entry array after that array has
been cleared during engine teardown. Pinning the plugin postponed its static
destructor until this later shutdown phase. The fix removes the guarded
`CON_COMMAND_ENABLED` definition from the generated Windows adaptation.
Upstream's only plugin-owned console command is the optional
`dem_render_info`; `ConVar_Register` and `ConVar_Unregister` in that conditional
manage that registration. Engine command dispatch, existing engine CVars,
their unhide operation, capture instrumentation, and the Cloud hook remain
enabled. Disabling an unused diagnostic command avoids invoking engine-owned
registration state from a late static destructor.

The trial-011 DLL, PDB, MAP, and a hash-bound diagnosis are preserved under
`../build/plugin-windows/settings-011-746e391b/`. Trial 011 remains a failed
render, despite its settings guards passing and its selected settings staying
unchanged. Its failed process exit must not be reclassified as success.

## Successful repeat trial

`data/rendered/windows-settings-012/` used the tested DLL above, with identical
source and staged DLL hashes. It captured Dust2 ticks 6000 through 6128 into
64 frames at 32 fps and encoded the MP4. Owned process 55776 exited normally.
The earliest server factory observed a zero Cloud context counter and null
interface, then installed the guard before loading the real server. One
RemoteStorage016 acquisition was observed and denied before the first capture
commands. The native filesystem returned exactly this run's `replay-settings`
root for USRLOCAL search and the synthetic filename beneath that root for the
write query. The probe file was not created.

`settings-isolation.json` ended with status `ready` and observation
`server_shutdown_guard_still_installed`. Its SHA-256 is
`1f89aeaacf08ffe44a841362da7dfb0d8a14685c6212b79b292916428efaedfe`.
The MP4 SHA-256 is
`2bcd882030034a8d245cb7225224f38fd7b7a5f9d5a26af41d11a72c8ae8eaca`.
The settings journal records 39 original and 39 post-run selected files,
state `restored`, and zero restoration operations: the selected personal files
did not require rewriting. The render manifest reports restored gameinfo and
removal of the staged plugin from the game directory.

The result remains `video_ready_timing_unverified`. Settings isolation does not
promote POV, frame/action timing, HUD correctness, or training acceptance.
Treat its Steam connection context as user-reported offline, not an online
Cloud or reconnection test.
The native report's Cloud count is the count observed at its server shutdown
snapshot, not a measurement of all subsequent engine or Steam client activity.

## Installed binary evidence

All addresses below are RVAs, not raw file offsets. Unknown hashes abort.

| Binary | Required SHA-256 |
|---|---|
| `engine2.dll` | `26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac` |
| `filesystem_stdio.dll` | `a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef` |

- Engine function `0x20a7f0` formats `USRLOCAL%s` at `0x20a819`, calls
  `getenv` at `0x20a851`, and uses the result in filesystem `AddSearchPath`
  slot 31 at `0x20a91b`, with path ID `USRLOCAL`. Its fallback requests the
  Steam user data directory only when the environment variable is absent.
- The engine import at `0x482698` is
  `SteamInternal_FindOrCreateUserInterface`. The loaded pointer must match
  the loaded Steam DLL's export before replacement.
- The config-specific Steam context at `0x60f1f0` contains initializer
  `0x1f2430`, its counter at `+8`, and cached interface at `+16`.
  The initializer requests RemoteStorage016 and stores the returned pointer.
- Config reads at `0x1f3cd3` and writes at `0x1f400a` obtain that context.
  Both check the cached interface for null before calling storage methods;
  the write path returns failure without calling Steam when it is null.
- Filesystem RTTI identifies `CFileSystem_Stdio`, vtable `0x1afc78`.
  Slot 42 (`0x4f3a0`) implements `GetWritePath`; slot 43 (`0x54480`)
  implements `GetSearchPath`. Those slots match the pinned SDK's
  `IFileSystem` layout. `GetWritePath` returns the full write filename,
  verified by its implementation at `0x4f210`. The plugin only queries the
  synthetic filename `cfg/chicken-isolation-probe.vcfg`; it creates no probe.
- The actual search-path list must contain exactly the cloned profile root.
  The actual write destination must equal that root plus the probe filename.
  Paths are checked again every 64 engine-frame checks.

## Report and limits

The schema-1 `settings-isolation.json` initially has status
`installed_pending_path_verification`. It becomes `ready` only after both
Cloud and native filesystem checks pass, before our first capture command.
It records the policy marker, process ID, requested and observed paths,
binary identities, startup-context check, hook scope, and count of denied
interface acquisitions observed so far. The server shutdown snapshot says
`server_shutdown_guard_still_installed`; it is not a final count of all
subsequent engine shutdown activity.

The report explicitly limits its scope to
`engine2_user_config_remote_storage`. The separate Steam client can update
metadata or synchronize local files independently; this hook cannot observe
or control that process. Other game subsystems may write installation-level
configuration or caches through other filesystem path IDs. The runner's
settings backups, process-exit checks, conflict-aware restoration, and real
before/after comparisons remain required. Do not report that every game file
or every Steam Cloud operation is isolated on the strength of this handshake.

The original working capture DLL was preserved as
`../build/plugin-windows/server-known-working-ed246f2a.dll`, SHA-256
`ed246f2a8096eb856b1b7b741244ae8eced2bcfe667965ee8987e8abfbb7abfd`.
It lacks these settings guards and must not satisfy the runner's isolation
preflight.
