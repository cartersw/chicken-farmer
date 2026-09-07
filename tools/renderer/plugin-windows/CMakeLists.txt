cmake_minimum_required(VERSION 3.24)
project(chicken_renderer_windows LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "This experimental plugin requires an x64 MSVC Windows build.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(CHICKEN_RENDERER_UPSTREAM "${CMAKE_CURRENT_LIST_DIR}/../upstream" CACHE PATH "Pinned Reka renderer checkout")

include(FetchContent)
FetchContent_Declare(json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.12.0
    GIT_SHALLOW TRUE
)
FetchContent_Declare(hl2sdk
    GIT_REPOSITORY https://github.com/alliedmodders/hl2sdk.git
    GIT_TAG 9ab16fa9fcdeeb30565dfdbf6fbb312356978a0b
)
FetchContent_MakeAvailable(json hl2sdk)

find_package(Python3 COMPONENTS Interpreter REQUIRED)
set(plugin_sources "${CMAKE_CURRENT_BINARY_DIR}/source")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_LIST_DIR}/prepare_source.py"
    "${CMAKE_CURRENT_LIST_DIR}/hook_fallback.inc"
    "${CMAKE_CURRENT_LIST_DIR}/capture_trace.inc"
    "${CHICKEN_RENDERER_UPSTREAM}/cs2-server-plugin/main.cpp"
    "${CHICKEN_RENDERER_UPSTREAM}/cs2-server-plugin/cdll_interfaces.h"
    "${hl2sdk_SOURCE_DIR}/public/icvar.h"
    "${hl2sdk_SOURCE_DIR}/tier1/convar.cpp"
)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/prepare_source.py"
        --upstream "${CHICKEN_RENDERER_UPSTREAM}/cs2-server-plugin"
        --sdk "${hl2sdk_SOURCE_DIR}"
        --output "${plugin_sources}"
    RESULT_VARIABLE prepare_result
    COMMAND_ERROR_IS_FATAL ANY
)

add_library(server SHARED
    "${plugin_sources}/main.cpp"
    "${plugin_sources}/convar.cpp"
)
target_include_directories(server PRIVATE
    "${plugin_sources}"
    "${hl2sdk_SOURCE_DIR}/public"
    "${hl2sdk_SOURCE_DIR}/public/tier0"
    "${hl2sdk_SOURCE_DIR}/public/tier1"
)
target_compile_definitions(server PRIVATE
    WIN32=1 _WINDOWS=1 COMPILER_MSVC=1 COMPILER_MSVC64=1
    X64BITS=1 PLATFORM_64BITS=1 NO_MALLOC_OVERRIDE=1
    _CRT_SECURE_NO_WARNINGS=1 NOMINMAX=1
)
target_compile_options(server PRIVATE /W3 /EHsc /utf-8 /Zi)
target_link_options(server PRIVATE /DEBUG:FULL /MAP)
target_link_libraries(server PRIVATE
    nlohmann_json::nlohmann_json
    "${hl2sdk_SOURCE_DIR}/lib/public/win64/tier0.lib"
    bcrypt
)
set_target_properties(server PROPERTIES PREFIX "" OUTPUT_NAME "server")
add_custom_command(TARGET server POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CHICKEN_RENDERER_UPSTREAM}/LICENSE" "$<TARGET_FILE_DIR:server>/REKA-LICENSE.txt"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CHICKEN_RENDERER_UPSTREAM}/THIRD_PARTY_LICENSES" "$<TARGET_FILE_DIR:server>/THIRD_PARTY_LICENSES.txt"
)
