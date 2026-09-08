"""Read-only PE contract audit and archived native disassembly; never loads DLLs.

This does not assert byte identity with an unavailable older DLL. It checks
documented guard locations and preserves current function bytes for comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from inspect_pe import PE

HASHES = {
    "legacy14178": {
        "engine": "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac",
        "client": "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4",
    },
    "calibration14180": {
        "engine": "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07",
        "client": "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae",
        "renderer": "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500",
    },
}
UNCHANGED_HASHES = {
    "filesystem": "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef",
    "schema": "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512",
}
TABLES = {
    "engine": {
        0x54C460: {2: 0x100280, 9: 0x100AF0},
        0x52DB68: {2: 0x35A80, 3: 0x35A90, 12: 0x35A50, 22: 0x2B820},
        0x5335D8: {88: 0x6A2D0, 111: 0x6AFE0, 128: 0x89340, 129: 0x47A00},
        0x573890: {23: 0x1D2BC0},
        0x538290: {64: 0x75B50, 65: 0x75BB0},
    },
    "client": {0x1B1A2F8: {181: 0xB344A0}, 0x1B331C0: {4: 0xBC1BA0}},
    "filesystem": {0x1AFC78: {42: 0x4F3A0, 43: 0x54480}},
    "renderer": {0x3EC3F8: {74: 0x3F190, 90: 0x30190}},
}
TYPES = {0x533E48: "CNETMsg_Tick", 0x52CF60: "CSVCMsg_PacketEntities", 0x539F70: "CSVCMsg_UserCommands",
         0x560F50: "CMsgServerUserCmd"}
FUNCTIONS = {
    "engine": [0x100280, 0x100AF0, 0x1E5850, 0x1E88C6, 0x35A50, 0x35A80, 0x35A90,
               0x2B820, 0x2B8B0, 0x2D100, 0x29BD0, 0x3FC940, 0x6A2D0, 0x6AFE0, 0x47A00,
               0x89340, 0x174C80, 0x1607C0, 0x14C200, 0x16D170, 0x1D2BC0,
               0x1F2430, 0x1F3CD3, 0x1F400A, 0x20A7F0],
    "client": [0x93AAE0, 0xAD6C20, 0x9BE990, 0xBB0830, 0xEAAF0, 0xBC1BA0, 0x829490,
               0xB344A0, 0xB39530, 0xA715A0, 0x6CE310],
    "renderer": [0x30190, 0x3F190],
}
# Contiguous audit ranges include split unwind entries that function_range alone
# cannot identify as belonging to the same source function. Ends are exclusive.
RANGES = {"engine": [(0x100280, 0x100370), (0x100AF0, 0x100F90),
    (0x1E8510, 0x1E8A30), (0x1E6FA0, 0x1E70D0),
    (0x2B820, 0x2CD94), (0x2D100, 0x2D740), (0x29BD0, 0x2A150),
    (0x6A2D0, 0x6AFE0), (0x47A00, 0x47C90), (0x1D6620, 0x1D6650),
    (0x75B50, 0x75C10)],
    "client": [(0x93AAE0, 0x93AB30), (0xAD6C20, 0xAD6C80),
               (0x9BE990, 0x9BEA50), (0xBC1BA0, 0xBC1DC0)]}


def profile_rva(rva, profile):
    # Only the individually checked late-engine routines moved by 16 bytes.
    return rva - 16 if profile == "calibration14180" and rva in {
        0x100280, 0x100370, 0x100AF0, 0x100F90, 0x1E5850, 0x1E88C6,
        0x1E8510, 0x1E8A30, 0x1E6FA0, 0x1E70D0,
        0x174C80, 0x1607C0, 0x14C200, 0x16D170,
        0x1D2BC0, 0x1F2430, 0x1F3CD3, 0x1F400A, 0x20A7F0} else rva


def cstring(pe, rva):
    offset = pe.offset(rva)
    return pe.data[offset:offset + 1024].split(b"\0")[0].decode("ascii", errors="replace")


def type_name(pe, table):
    col = struct.unpack_from("<Q", pe.data, pe.offset(table) - 8)[0] - pe.base
    descriptor = struct.unpack_from("<I", pe.data, pe.offset(col) + 12)[0]
    return cstring(pe, descriptor + 16)


def import_at(pe, wanted):
    pe_header = struct.unpack_from("<I", pe.data, 0x3C)[0]
    directory = struct.unpack_from("<I", pe.data, pe_header + 24 + 112 + 8)[0]
    cursor = pe.offset(directory)
    for _ in range(1024):
        lookup, _, _, name, address = struct.unpack_from("<IIIII", pe.data, cursor)
        cursor += 20
        if not any((lookup, name, address)):
            break
        for index in range(16384):
            value = struct.unpack_from("<Q", pe.data, pe.offset((lookup or address) + index * 8))[0]
            if not value:
                break
            if address + index * 8 == wanted:
                return cstring(pe, name), (f"ordinal:{value & 65535}" if value >> 63 else cstring(pe, value + 2))
    return None


def function_range(pe, address):
    section = next(s for s in pe.sections if s["name"] == ".pdata")
    for offset in range(section["raw"], section["raw"] + section["size"], 12):
        start, end, _ = struct.unpack_from("<III", pe.data, offset)
        if start <= address < end:
            return start, end
    return address, address + 128  # Leaf functions need not have unwind entries.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dumpbin", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(HASHES), default="legacy14178")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    paths = {"engine": args.game / "bin/win64/engine2.dll", "client": args.game / "csgo/bin/win64/client.dll",
             "filesystem": args.game / "bin/win64/filesystem_stdio.dll", "schema": args.game / "bin/win64/schemasystem.dll",
             "renderer": args.game / "bin/win64/rendersystemdx11.dll"}
    images = {name: PE(path) for name, path in paths.items()}
    result = {"schema_version": 2, "profile": args.profile,
              "scope": "documented_native_contract_locations_and_current_code_archive",
              "older_complete_binary_comparison_available": False, "runtime_compatibility_verified": False,
              "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "modules": {}, "checks": [], "functions": []}

    def check(name, actual, expected):
        result["checks"].append({"name": name, "actual": actual, "expected": expected, "matched": actual == expected})

    def bytes_at(module, label, rva, expected):
        pe = images[module]
        offset = pe.offset(rva)
        check(f"{module}.{label}.{rva:x}", pe.data[offset:offset + len(expected) // 2].hex(), expected)

    def archive_range(name, pe, start, end, label):
        body = pe.data[pe.offset(start):pe.offset(end - 1) + 1]
        filename = f"{name}-{label}.txt"
        output = subprocess.run([str(args.dumpbin), "/nologo", "/disasm", f"/range:{pe.base + start:#x},{pe.base + end - 1:#x}",
                                 str(args.output / paths[name].name)], check=True, capture_output=True).stdout
        (args.output / filename).write_bytes(output)
        result["functions"].append({"module": name, "start_rva": hex(start),
            "end_rva": hex(end), "sha256": hashlib.sha256(body).hexdigest(), "disassembly": filename})

    for name, pe in images.items():
        archived = args.output / paths[name].name
        archived.write_bytes(pe.data)
        result["modules"][name] = {"path": str(paths[name]), "sha256": hashlib.sha256(pe.data).hexdigest(),
            "size_bytes": len(pe.data), "archived_path": str(archived),
            "sections": [{**s, "sha256": hashlib.sha256(pe.data[s["raw"]:s["raw"] + s["raw_size"]]).hexdigest()} for s in pe.sections]}
        expected_hash = {**HASHES[args.profile], **UNCHANGED_HASHES}.get(name)
        if expected_hash:
            check(f"{name}.whole_file_sha256", result["modules"][name]["sha256"], expected_hash)
        for table, entries in TABLES.get(name, {}).items():
            for slot, target in entries.items():
                actual = struct.unpack_from("<Q", pe.data, pe.offset(table) + slot * 8)[0] - pe.base
                expected = profile_rva(target, args.profile) if name == "engine" else target
                check(f"{name}.vtable.{table:x}.slot{slot}", hex(actual), hex(expected))
        for rva in FUNCTIONS.get(name, []):
            rva = profile_rva(rva, args.profile) if name == "engine" else rva
            start, end = function_range(pe, rva)
            archive_range(name, pe, start, end, f"{rva:x}")
        for start, end in RANGES.get(name, []):
            if name == "engine":
                start, end = profile_rva(start, args.profile), profile_rva(end, args.profile)
            archive_range(name, pe, start, end, f"range-{start:x}-{end:x}")
    engine, client = images["engine"], images["client"]
    for table, expected in TYPES.items():
        name = type_name(engine, table)
        check(f"engine.message_type.{table:x}", expected in name, True)
        result["checks"][-1]["rtti"] = name
    check("engine.storage_context_initializer", hex(struct.unpack_from("<Q", engine.data, engine.offset(0x60F1F0))[0] - engine.base),
          hex(profile_rva(0x1F2430, args.profile)))
    check("engine.storage_import", import_at(engine, 0x482698), ("steam_api64.dll", "SteamInternal_FindOrCreateUserInterface"))
    check("client.user_commands_noop", client.data[client.offset(0xB344A0):client.offset(0xB344A0) + 3].hex(), "c20000")
    check("engine.demo_start_tick_getter", engine.data[engine.offset(0x35A80):engine.offset(0x35A80) + 7].hex(), "8b8104020000c3")
    shift = 16 if args.profile == "calibration14180" else 0
    for label, site, expected in (
        ("native_movie_call", 0x1E5B55, "ff5048"),
        ("movie_event_forward", 0x1E5B52, "498bd6"),
        ("movie_render_seconds", 0x1E5AFB, "f3410f105628"),
        ("native_readback_call", 0x1E88C3, "41ffd2"),
        ("native_readback_slot74", 0x1E88A6, "4c8b9050020000"),
        ("native_readback_ninth_dword", 0x1E888E, "44897c2440"),
        ("client_service_getter", 0x1D2BC0, "488b81a0000000c3"),
    ):
        bytes_at("engine", label, site - shift, expected)
    bytes_at("renderer", "readback_ninth_dword_load_and_store", 0x3F21D,
             "8b8424a0000000894748")
    if args.profile == "calibration14180":
        for label, rva, expected in (
            ("movie_counter_increment", 0x100E74, "48ff8638010000"),
            ("movie_prefix_member", 0x100BDB, "488b8630010000"),
            ("movie_state_member", 0x100AEB, "8b8168010000"),
            ("movie_flags_word", 0x100DC4, "0fb7864c010000"),
            ("cloud_initializer_call", 0x1F2438, "ff155a022900"),
            ("cloud_initializer_store", 0x1F243E, "488903"),
            ("cloud_write_null_test", 0x1F4007, "48833800753e"),
            ("usrlocal_add_search_path", 0x20A90B, "ff90f8000000"),
            ("packet_selected_tick", 0x2BFF4, "89872c020000"),
            ("packet_data_size", 0x2C740, "44896b74"),
            ("packet_data_pointer", 0x2C747, "4c896b50"),
            ("net_tick_committed_field", 0x6A76C, "89bb7c030000"),
            ("level_short_member", 0x75BDE, "488b8918020000"),
        ):
            bytes_at("engine", label, rva, expected)
        check("engine.cloud_interface_name", cstring(engine, 0x5783B0),
              "STEAMREMOTESTORAGE_INTERFACE_VERSION016")
        check("engine.user_local_environment_format", cstring(engine, 0x57D580), "USRLOCAL%s")
        check("engine.user_local_path_id", cstring(engine, 0x57DA78), "USRLOCAL")
    check("client.observer_in_eye_enum", struct.unpack_from("<Q", client.data, client.offset(0x1AF2220) + 8)[0], 2)
    result["all_documented_location_checks_matched"] = all(c["matched"] for c in result["checks"])
    (args.output / "audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_matched": result["all_documented_location_checks_matched"], "checks": len(result["checks"]),
                      "mismatches": [c for c in result["checks"] if not c["matched"]], "report": str(args.output / "audit.json")}))
    return 0 if result["all_documented_location_checks_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
