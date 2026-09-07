"""Read-only PE/RTTI inspection for diagnosing version-specific capture hooks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct


class PE:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("Not a PE file")
        sections, optional_size = struct.unpack_from("<H12xH", self.data, pe + 6)
        optional = pe + 24
        if struct.unpack_from("<H", self.data, optional)[0] != 0x20B:
            raise ValueError("Requires PE32+")
        self.base = struct.unpack_from("<Q", self.data, optional + 24)[0]
        self.sections = []
        for index in range(sections):
            entry = optional + optional_size + index * 40
            name = self.data[entry:entry + 8].split(b"\0")[0].decode("ascii")
            size, rva, raw_size, raw = struct.unpack_from("<IIII", self.data, entry + 8)
            flags = struct.unpack_from("<I", self.data, entry + 36)[0]
            self.sections.append(dict(name=name, rva=rva, size=size, raw=raw, raw_size=raw_size, executable=bool(flags & 0x20000000)))

    def rva(self, offset: int) -> int:
        for section in self.sections:
            if section["raw"] <= offset < section["raw"] + section["raw_size"]:
                return section["rva"] + offset - section["raw"]
        raise ValueError(f"No section for file offset {offset:#x}")

    def offset(self, rva: int) -> int:
        for section in self.sections:
            if section["rva"] <= rva < section["rva"] + section["raw_size"]:
                return section["raw"] + rva - section["rva"]
        raise ValueError(f"No section for RVA {rva:#x}")

    def executable(self, va: int) -> bool:
        return any(section["executable"] and section["rva"] <= va - self.base < section["rva"] + section["size"] for section in self.sections)

    def vtables(self, name: str) -> list[dict]:
        target = name.encode() + b"\0"
        name_offset = self.data.find(target)
        if name_offset < 0:
            return []
        type_rva = self.rva(name_offset - 16)
        matches = []
        needle = struct.pack("<I", type_rva)
        cursor = 0
        while (cursor := self.data.find(needle, cursor)) >= 0:
            col = cursor - 12
            cursor += 4
            if col < 0 or col + 24 > len(self.data):
                continue
            signature, adjustment, cd_offset, _, hierarchy, self_rva = struct.unpack_from("<IIIIII", self.data, col)
            if signature != 1 or self_rva != self.rva(col):
                continue
            pointer = struct.pack("<Q", self.base + self_rva)
            table_cursor = 0
            while (table_cursor := self.data.find(pointer, table_cursor)) >= 0:
                table = table_cursor + 8
                table_cursor += 8
                functions = []
                for index in range(100):
                    address = struct.unpack_from("<Q", self.data, table + 8 * index)[0]
                    if not self.executable(address):
                        break
                    functions.append(hex(address - self.base))
                if functions:
                    matches.append(dict(vtable_rva=hex(self.rva(table)), this_adjustment=adjustment, functions=functions))
        return matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("class_names", nargs="+")
    args = parser.parse_args()
    binary = PE(args.binary)
    print(json.dumps({"image_base": hex(binary.base), "classes": {name: binary.vtables(name) for name in args.class_names}}, indent=2))


if __name__ == "__main__":
    main()
