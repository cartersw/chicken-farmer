"""Read-only renderer prerequisite report. Never starts Steam or changes CS2."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steam-library", type=Path, help="Library containing steamapps/")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    lock = json.loads((root / "upstream.lock.json").read_text(encoding="utf-8"))
    library = args.steam_library or (Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Steam" if sys.platform == "win32" else Path.home() / ".steam/steam")
    game = library / "steamapps/common/Counter-Strike Global Offensive/game"
    steam_info: dict[str, str] = {}
    info_path = game / "csgo/steam.inf"
    if info_path.is_file():
        steam_info = dict(line.split("=", 1) for line in info_path.read_text(encoding="utf-8").splitlines() if "=" in line)
    blockers = []
    if not sys.platform.startswith("linux"):
        blockers.append("Pinned upstream capture implementation requires a Linux render worker")
    if steam_info.get("PatchVersion") != lock["cs2_patch_version"]:
        blockers.append(f"Installed CS2 patch {steam_info.get('PatchVersion', 'missing')} differs from plugin target {lock['cs2_patch_version']}")
    binaries = {name: shutil.which(name) for name in ("git", "go", "cmake", "ffmpeg", "ffprobe")}
    plugin = game / "csgo/dem-render/bin/linuxsteamrt64/libserver.so"
    if not plugin.exists():
        blockers.append("Pinned Linux render plugin is not installed")
    for command in ("ffmpeg", "ffprobe"):
        if not binaries[command]:
            blockers.append(f"{command} is not on PATH")
    if sys.platform.startswith("linux") and not Path("/dev/dri/renderD128").exists():
        blockers.append("Upstream VAAPI device /dev/dri/renderD128 is missing")
    print(json.dumps({"platform":sys.platform,"steam_library":str(library),"cs2_installed":info_path.is_file(),"cs2_version":steam_info,"upstream":lock,"tools_on_path":binaries,"blockers":blockers,"ready":not blockers,"note":"Read-only static check; GPU encoding, plugin ABI, replay POV, and frame timing still require a real render validation."}, indent=2))


if __name__ == "__main__":
    main()
