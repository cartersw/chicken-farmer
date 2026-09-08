// Read-only observations for the exact CS2 1.41.7.8 client/schema binaries.
// Included inside ChickenCapture, after Read/Sha256/Write/Tick are defined.
// All entity/schema reads use bounded ReadProcessMemory and fail to null.
namespace Observation {
constexpr const char* ClientSha = "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4";
const char* ExpectedClientSha() {
    return CalibrationProfile() ? "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae" : ClientSha;
}
constexpr const char* SchemaSha = "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512";
BYTE* clientBase = nullptr;
BYTE* schemaBase = nullptr;
bool schemaReady = false;
json fields = json::object();

bool Copy(const void* address, void* target, size_t size) {
    if (!address || !target || size > 1024 * 1024) return false;
    SIZE_T copied = 0;
    return ReadProcessMemory(GetCurrentProcess(), address, target, size, &copied) && copied == size;
}
template<class T> bool Get(const void* address, size_t offset, T& result) {
    if (!address) return false;
    return Copy(static_cast<const BYTE*>(address) + offset, &result, sizeof(result));
}
template<class T> T Value(const void* address, size_t offset, T fallback = T{}) {
    T result{};
    return Get(address, offset, result) ? result : fallback;
}
string String(const void* address, size_t maximum = 256) {
    string result;
    for (size_t i = 0; address && i < maximum; ++i) {
        char c = 0;
        if (!Get(address, i, c)) return "";
        if (!c) return result;
        if (static_cast<unsigned char>(c) < 32 || static_cast<unsigned char>(c) > 126) return "";
        result.push_back(c);
    }
    return "";
}
string ModuleSha(HMODULE module) {
    wchar_t path[32768]{};
    if (!module || !GetModuleFileNameW(module, path, 32768)) return "";
    std::ifstream file(std::filesystem::path(path), std::ios::binary);
    std::vector<BYTE> data((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    return data.empty() ? "" : Sha256(data.data(), data.size());
}
bool WantedClass(const string& name) {
    for (const char* wanted : {"CEntityInstance", "C_BaseEntity", "CGameSceneNode",
        "CBasePlayerController", "CCSPlayerController", "C_BasePlayerPawn", "C_CSPlayerPawnBase",
        "C_CSPlayerPawn", "CPlayer_ObserverServices", "CPlayer_CameraServices",
        "CPlayer_MovementServices", "CCSPlayer_MovementServices", "CPlayer_WeaponServices",
        "C_BasePlayerWeapon", "C_CSWeaponBase"})
        if (name == wanted) return true;
    return false;
}
bool LoadSchema() {
    // HLAE's pinned SchemaSystem definitions, independently guarded by DLL hash.
    auto system = schemaBase + 0x75730;
    const auto count = Value<uint64_t>(system, 0x190);
    auto scopes = Value<const BYTE*>(system, 0x198);
    if (count == 0 || count > 256 || !scopes) return false;
    for (uint64_t i = 0; i < count; ++i) {
        auto scope = Value<const BYTE*>(scopes, i * 8);
        if (!scope || String(scope + 8) != "client.dll") continue;
        const auto classes = Value<uint16_t>(scope, 0x470);
        auto entries = Value<const BYTE*>(scope, 0x478);
        if (!entries || classes == 0 || classes > 20000) return false;
        for (uint16_t j = 0; j < classes; ++j) {
            auto declared = Value<const BYTE*>(entries, j * 24 + 16);
            auto info = Value<const BYTE*>(declared, 0x20);
            auto name = String(Value<const char*>(info, 8));
            if (!WantedClass(name)) continue;
            const auto size = Value<uint32_t>(info, 0x20);
            const auto fieldCount = Value<uint16_t>(info, 0x24);
            auto fieldArray = Value<const BYTE*>(info, 0x30);
            if (size == 0 || size > 1024 * 1024 || fieldCount > 4096 || !fieldArray) continue;
            for (uint16_t k = 0; k < fieldCount; ++k) {
                auto field = fieldArray + k * 32;
                auto fieldName = String(Value<const char*>(field, 0));
                uint32_t offset = UINT32_MAX;
                if (!fieldName.empty() && Get(field, 16, offset) && offset < size)
                    fields[name + "." + fieldName] = {{"offset", offset}, {"class_size", size}};
            }
        }
        return fields.contains("CEntityInstance.m_pEntity") &&
            fields.contains("C_BasePlayerPawn.m_pObserverServices") &&
            fields.contains("CPlayer_ObserverServices.m_hObserverTarget") &&
            fields.contains("CBasePlayerController.m_steamID");
    }
    return false;
}
template<class T> bool Field(const void* object, const char* name, T& result) {
    auto field = fields.find(name);
    if (field == fields.end()) return false;
    const auto offset = (*field)["offset"].get<size_t>();
    const auto size = (*field)["class_size"].get<size_t>();
    return sizeof(T) <= size - offset && Get(object, offset, result);
}
template<class T> T FieldValue(const void* object, const char* name, T fallback = T{}) {
    T value{};
    return Field(object, name, value) ? value : fallback;
}
json Vector(const void* address, size_t offset) {
    float v[3]{};
    if (!Get(address, offset, v)) return nullptr;
    for (float x : v) if (!std::isfinite(x) || std::abs(x) > 10000000.0f) return nullptr;
    return {v[0], v[1], v[2]};
}
json FieldVector(const void* object, const char* name) {
    auto field = fields.find(name);
    if (field == fields.end()) return nullptr;
    const auto offset = (*field)["offset"].get<size_t>();
    if ((*field)["class_size"].get<size_t>() - offset < 3 * sizeof(float)) return nullptr;
    return Vector(object, offset);
}
template<class T> json Scalar(const void* object, const char* name) {
    T value{};
    if (!Field(object, name, value)) return nullptr;
    if constexpr (std::is_floating_point<T>::value)
        if (!std::isfinite(value)) return nullptr;
    return value;
}
uint32_t Handle(const void* entity) {
    auto identity = FieldValue<const BYTE*>(entity, "CEntityInstance.m_pEntity");
    return Value<uint32_t>(identity, 0x10, UINT32_MAX);
}
const BYTE* Entity(uint32_t handle) {
    if (handle == UINT32_MAX) return nullptr;
    const uint32_t index = handle & 0x7fff;
    if (index >= 0x7fff) return nullptr;
    auto system = Value<const BYTE*>(clientBase, 0x23ad958);
    auto page = Value<const BYTE*>(system, 0x10 + (index >> 9) * 8);
    auto identity = page ? page + (index & 0x1ff) * 0x70 : nullptr;
    // Native GetEntityFromIndex checks the low 15 bits. We additionally require
    // the complete serial-bearing handle so a recycled index cannot pass.
    if (Value<uint32_t>(identity, 0x10, UINT32_MAX) != handle) return nullptr;
    auto entity = Value<const BYTE*>(identity, 0);
    return entity && Handle(entity) == handle ? entity : nullptr;
}
string EntityClass(const void* entity) {
    auto table = Value<const BYTE*>(entity, 0);
    if (!table || table < clientBase || table >= clientBase + 0x2800000) return "";
    auto locator = Value<const BYTE*>(table - 8, 0);
    if (!locator || Value<uint32_t>(locator, 0) != 1) return "";
    const auto type = Value<uint32_t>(locator, 12);
    return type < 0x2800000 ? String(clientBase + type + 16) : "";
}
json PawnState(const BYTE* pawn) {
    auto scene = FieldValue<const BYTE*>(pawn, "C_BaseEntity.m_pGameSceneNode");
    json angles = FieldVector(pawn, "C_CSPlayerPawn.m_angEyeAngles");
    if (angles.is_null()) angles = FieldVector(pawn, "C_CSPlayerPawnBase.m_angEyeAngles");
    auto movement = FieldValue<const BYTE*>(pawn, "C_BasePlayerPawn.m_pMovementServices");
    auto weaponServices = FieldValue<const BYTE*>(pawn, "C_BasePlayerPawn.m_pWeaponServices");
    const auto weaponHandle = FieldValue<uint32_t>(weaponServices, "CPlayer_WeaponServices.m_hActiveWeapon", UINT32_MAX);
    auto weapon = Entity(weaponHandle);
    uint32_t flags = 0;
    const bool flagsKnown = Field(pawn, "C_BaseEntity.m_fFlags", flags);
    return {{"source", "schema_state_at_observation_boundary"},
        {"origin", FieldVector(scene, "CGameSceneNode.m_vecAbsOrigin")},
        {"eye_angles", angles}, {"velocity", FieldVector(pawn, "C_BaseEntity.m_vecAbsVelocity")},
        {"flags", Scalar<uint32_t>(pawn, "C_BaseEntity.m_fFlags")},
        {"health", Scalar<int32_t>(pawn, "C_BaseEntity.m_iHealth")},
        {"simulation_time", Scalar<float>(pawn, "C_BaseEntity.m_flSimulationTime")},
        {"simulation_tick", Scalar<int32_t>(pawn, "C_BaseEntity.m_nSimulationTick")},
        {"last_executed_command_number", Scalar<int32_t>(pawn, "C_BasePlayerPawn.m_nLastExecutedCommandNumber")},
        {"last_executed_command_tick", Scalar<int32_t>(pawn, "C_BasePlayerPawn.m_nLastExecutedCommandTick")},
        {"movement_last_command_number_processed", Scalar<int32_t>(movement, "CPlayer_MovementServices.m_nLastCommandNumberProcessed")},
        {"movement_has_ever_processed_command", Scalar<bool>(movement, "CCSPlayer_MovementServices.m_bHasEverProcessedCommand")},
        // public/const.h in the pinned SDK: FL_ONGROUND = (1u << 0).
        {"on_ground", flagsKnown ? json(bool(flags & (1u << 0))) : json(nullptr)},
        {"ground_entity_handle", Scalar<uint32_t>(pawn, "C_BaseEntity.m_hGroundEntity")},
        {"duck_amount", Scalar<float>(movement, "CCSPlayer_MovementServices.m_flDuckAmount")},
        {"ducked", Scalar<bool>(movement, "CCSPlayer_MovementServices.m_bDucked")},
        {"active_weapon_handle", weaponHandle == UINT32_MAX ? json(nullptr) : json(weaponHandle)},
        {"weapon_class", EntityClass(weapon)},
        {"ammo_clip", Scalar<int32_t>(weapon, "C_BasePlayerWeapon.m_iClip1")},
        {"last_shot_time", Scalar<float>(weapon, "C_CSWeaponBase.m_fLastShotTime")}};
}
// Own-player path for controlled local recordings. Keep the spectator POV
// resolver separate: a live pawn is not an observed spectator target.
json LocalPlayer() {
    json result = {{"status", "unavailable"}};
    if (!schemaReady) { result["reason"] = "schema_unavailable"; return result; }
    auto controller = Value<const BYTE*>(clientBase, 0x23a0f30);
    if (EntityClass(controller) != ".?AVCCSPlayerController@@") {
        result["reason"] = "local_controller_unavailable"; return result;
    }
    const auto pawnHandle = FieldValue<uint32_t>(controller, "CBasePlayerController.m_hPawn", UINT32_MAX);
    auto pawn = Entity(pawnHandle);
    if (!pawn || EntityClass(pawn).find("PlayerPawn") == string::npos) {
        result["reason"] = "local_player_pawn_unavailable"; return result;
    }
    const auto controllerHandle = Handle(controller);
    if (FieldValue<uint32_t>(pawn, "C_BasePlayerPawn.m_hController", UINT32_MAX) != controllerHandle) {
        result["reason"] = "local_identity_not_reciprocal"; return result;
    }
    const BYTE* observer = nullptr;
    const bool observerPointerKnown = Field(pawn, "C_BasePlayerPawn.m_pObserverServices", observer);
    result["observer_services_pointer_observed"] = observerPointerKnown;
    result["observer_services_present"] = observerPointerKnown ? json(observer != nullptr) : json(nullptr);
    result["status"] = "observed";
    result["steam_id"] = std::to_string(FieldValue<uint64_t>(controller, "CBasePlayerController.m_steamID"));
    result["controller_handle"] = controllerHandle;
    result["pawn_handle"] = pawnHandle;
    result["controller_tick_base"] = Scalar<int32_t>(controller, "CBasePlayerController.m_nTickBase");
    result["observer_mode"] = Scalar<uint8_t>(observer, "CPlayer_ObserverServices.m_iObserverMode");
    result["life_state"] = Scalar<uint8_t>(pawn, "C_BaseEntity.m_lifeState");
    result["team"] = Scalar<uint8_t>(pawn, "C_BaseEntity.m_iTeamNum");
    result["pawn_state"] = PawnState(pawn);
    auto camera = FieldValue<const BYTE*>(pawn, "C_BasePlayerPawn.m_pCameraServices");
    result["camera_view_entity_handle"] = Scalar<uint32_t>(camera, "CPlayer_CameraServices.m_hViewEntity");
    auto view = clientBase + 0x23d9c90;
    const bool cameraKnown = Value<const BYTE*>(view, 0) == clientBase + 0x1b331c0;
    result["camera_origin"] = cameraKnown ? Vector(view, 0x4b0) : json(nullptr);
    const auto& origin = result["pawn_state"]["origin"];
    const auto& eye = result["camera_origin"];
    const auto& overrideHandle = result["camera_view_entity_handle"];
    bool firstPerson = false;
    if (origin.is_array() && eye.is_array() && origin.size() == 3 && eye.size() == 3 &&
        overrideHandle == UINT32_MAX) {
        const double dx = eye[0].get<double>() - origin[0].get<double>();
        const double dy = eye[1].get<double>() - origin[1].get<double>();
        const double dz = eye[2].get<double>() - origin[2].get<double>();
        firstPerson = std::isfinite(dx) && std::isfinite(dy) && std::isfinite(dz) &&
            dx * dx + dy * dy <= 4.0 && dz >= 24 && dz <= 76;
    }
    result["first_person_camera_verified"] = firstPerson;
    result["first_person_camera_check"] = "stationary_local_eye_origin_no_view_override";
    return result;
}
json Pov() {
    json result = {{"status", "unavailable"}};
    if (!schemaReady) { result["reason"] = "schema_unavailable"; return result; }
    // First unique-order HLAE GetSplitScreenPlayer pattern returns this slot-0
    // native player pointer. Runtime RTTI distinguishes pawn from controller.
    auto local = Value<const BYTE*>(clientBase, 0x23a0f30);
    result["local_class"] = EntityClass(local);
    result["local_handle"] = Handle(local);
    // Trial 007 independently identified this object as CCSPlayerController.
    // Follow the schema-owned pawn handle rather than treating its memory as a pawn.
    if (result["local_class"] == ".?AVCCSPlayerController@@") {
        result["local_controller_handle"] = Handle(local);
        const auto localPawnHandle = FieldValue<uint32_t>(local, "CBasePlayerController.m_hPawn", UINT32_MAX);
        local = Entity(localPawnHandle);
        result["local_pawn_handle"] = localPawnHandle;
    }
    result["local_pawn_class"] = EntityClass(local);
    if (!local || result["local_pawn_class"].get<string>().find("Pawn") == string::npos) {
        result["reason"] = "local_pawn_unavailable"; return result;
    }
    auto observer = FieldValue<const BYTE*>(local, "C_BasePlayerPawn.m_pObserverServices");
    uint8_t mode = 0;
    uint32_t targetHandle = UINT32_MAX;
    if (!Field(observer, "CPlayer_ObserverServices.m_iObserverMode", mode) ||
        !Field(observer, "CPlayer_ObserverServices.m_hObserverTarget", targetHandle)) {
        result["reason"] = "observer_services_unavailable"; return result;
    }
    result["observer_mode"] = mode;
    result["observer_target_handle"] = targetHandle;
    auto camera = FieldValue<const BYTE*>(local, "C_BasePlayerPawn.m_pCameraServices");
    result["camera_view_entity_handle"] = Scalar<uint32_t>(camera, "CPlayer_CameraServices.m_hViewEntity");
    auto target = Entity(targetHandle);
    result["target_class"] = EntityClass(target);
    if (!target || result["target_class"].get<string>().find("PlayerPawn") == string::npos) {
        result["reason"] = "observer_target_not_resolved_player_pawn"; return result;
    }
    const auto controllerHandle = FieldValue<uint32_t>(target, "C_BasePlayerPawn.m_hController", UINT32_MAX);
    auto controller = Entity(controllerHandle);
    result["controller_class"] = EntityClass(controller);
    if (!controller || result["controller_class"] != ".?AVCCSPlayerController@@") {
        result["reason"] = "target_controller_unavailable"; return result;
    }
    uint64_t steam = 0;
    if (!Field(controller, "CBasePlayerController.m_steamID", steam) || !steam) {
        result["reason"] = "steam_identity_unavailable"; return result;
    }
    result["status"] = "observed";
    result["resolution_path"] = "local_controller.pawn_handle.observer_services.target_handle.pawn.controller_handle.steam_id";
    result["steam_id"] = std::to_string(steam);
    result["pawn_handle"] = targetHandle;
    result["controller_handle"] = controllerHandle;
    result["controller_entity_index"] = controllerHandle & 0x7fff;
    // This entity index is deliberately not named player_slot: that mapping is
    // independently compared with the parsed demo by the validator.
    result["controller_pawn_handle"] = Scalar<uint32_t>(controller, "CBasePlayerController.m_hPawn");
    result["controller_tick_base"] = Scalar<int32_t>(controller, "CBasePlayerController.m_nTickBase");
    result["pawn_state"] = PawnState(target);
    return result;
}
json Camera() {
    auto view = clientBase + 0x23d9c90;
    if (Value<const BYTE*>(view, 0) != clientBase + 0x1b331c0) return nullptr;
    const auto fov = Value<float>(view, 0x4a8);
    auto camera = json{{"source", "CViewRender.current_view_at_observation_boundary"},
        {"origin", Vector(view, 0x4b0)}, {"angles", Vector(view, 0x4c8)},
        {"matrix_input_origin", Vector(view, 0x10)}, {"matrix_input_angles", Vector(view, 0x1c)},
        {"matrix_built_framecount", Value<int32_t>(clientBase, 0x23cb500, -1)},
        {"fov", std::isfinite(fov) && fov > 0 && fov < 180 ? json(fov) : json(nullptr)},
        {"pixel_camera_equivalence_verified", false}};
    return camera;
}
json Snapshot(const char* phase) {
    json result = {{"schema_version", 1}, {"source_phase", phase},
        {"clock_on_engine_thread", GetCurrentThreadId() == engineThread},
        {"replay_demo_tick", Tick()}};
    if (!clientBase || isQuitting || GetCurrentThreadId() != engineThread) {
        result["status"] = "unavailable_on_this_thread"; return result;
    }
    if (!schemaReady) {
        schemaReady = LoadSchema();
        if (schemaReady) Write({{"event", "native_schema"}, {"fields", fields}});
    }
    result["observed_pov"] = Pov();
    if (HasArgument("-chicken-calibration-plan")) result["local_player"] = LocalPlayer();
    result["rendered_camera"] = Camera();
    auto demo = engineToClient ? engineToClient->GetDemoPlayer() : nullptr;
    if (demo) {
        auto table = Value<void**>(demo, 0);
        if (table && Value<void*>(table, 12 * 8) == engineBase + 0x35a50)
            result["demo_paused"] = reinterpret_cast<bool (*)(void*)>(table[12])(demo);
    }
    return result;
}
json Initialize() {
    clientBase = reinterpret_cast<BYTE*>(GetModuleHandleA("client.dll"));
    schemaBase = reinterpret_cast<BYTE*>(GetModuleHandleA("schemasystem.dll"));
    if (ModuleSha(reinterpret_cast<HMODULE>(clientBase)) != ExpectedClientSha() ||
        ModuleSha(reinterpret_cast<HMODULE>(schemaBase)) != SchemaSha)
        PluginError("Native observation client/schema DLL SHA256 mismatch; refusing version-specific reads");
    schemaReady = LoadSchema();
    Trace("Native observation initialized: schema_ready=%d fields=%zu", schemaReady, fields.size());
    return {{"schema_version", 1}, {"client_sha256", ExpectedClientSha()}, {"schemasystem_sha256", SchemaSha},
        {"source_commit", "03f972b4710ab9a8b1601fc0c34ce33c9c0268cc"},
        {"schema_ready", schemaReady}, {"schema_fields", fields},
        {"entity_reads", "bounded_and_complete_handle_checked"},
        {"camera_source", "CViewRender_current_view_and_matrix_input_vectors"},
        {"camera_object_rva", "0x23d9c90"}, {"local_player_array_rva", "0x23a0f30"},
        {"entity_system_global_rva", "0x23ad958"},
        {"pixel_camera_equivalence_verified", false}, {"execution_clock_equivalence_verified", false}};
}
} // namespace Observation
