"""Read-only renderer prerequisite report. Never starts Steam or changes CS2."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steam-library", type=Path, help="Library containing steamapps/")
    parser.add_argument("--game-dir", type=Path, help="Explicit CS2 game/ directory")
    parser.add_argument("--plugin", type=Path, help="Built Windows server.dll")
    parser.add_argument("--allow-version-mismatch", action="store_true", help="Report version difference as an experimental-run warning")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    lock = json.loads((root / "upstream.lock.json").read_text(encoding="utf-8"))
    library = args.steam_library or (Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Steam" if sys.platform == "win32" else Path.home() / ".steam/steam")
    game = args.game_dir or library / "steamapps/common/Counter-Strike Global Offensive/game"
    steam_info: dict[str, str] = {}
    info_path = game / "csgo/steam.inf"
    if info_path.is_file():
        steam_info = dict(line.split("=", 1) for line in info_path.read_text(encoding="utf-8").splitlines() if "=" in line)
    blockers = []
    warnings = []
    windows = sys.platform == "win32"
    if not windows and not sys.platform.startswith("linux"):
        blockers.append("Rendering supports Windows (experimental) and Linux")
    if steam_info.get("PatchVersion") != lock["cs2_patch_version"]:
        destination = warnings if args.allow_version_mismatch and info_path.is_file() else blockers
        destination.append(f"Installed CS2 patch {steam_info.get('PatchVersion', 'missing')} differs from plugin target {lock['cs2_patch_version']}; runtime ABI compatibility remains unverified")
    binaries = {name: shutil.which(name) for name in ("git", "go", "cmake", "ffmpeg", "ffprobe")}
    if windows:
        repo = root.parent.parent
        candidates = {
            "go": [repo / ".tools/go/bin/go.exe"],
            "cmake": [Path(sys.executable).parent / "cmake.exe"],
            "ffmpeg": sorted((repo / ".tools/ffmpeg").glob("*/bin/ffmpeg.exe")),
            "ffprobe": sorted((repo / ".tools/ffmpeg").glob("*/bin/ffprobe.exe")),
        }
        for name, paths in candidates.items():
            if not binaries[name]:
                binaries[name] = next((str(p) for p in paths if p.is_file()), None)
        plugin = args.plugin or root / "build/plugin-windows/Release/server.dll"
        if not (game / "bin/win64/cs2.exe").is_file():
            blockers.append("Native cs2.exe is missing")
        try:
            processes = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"], text=True, stderr=subprocess.PIPE)
            if '"cs2.exe"' in processes.lower():
                blockers.append("CS2 is already running; close it before rendering")
        except (OSError, subprocess.CalledProcessError):
            blockers.append("Process check unavailable; rerun with permission to inspect Windows processes")
        warnings.append("Windows captures are diagnostic until POV and capture/execution timing are verified")
    else:
        plugin = game / "csgo/dem-render/bin/linuxsteamrt64/libserver.so"
    if not plugin.exists():
        blockers.append("Windows plugin is not built; run setup.py --build-plugin" if windows else "Pinned Linux render plugin is not installed")
    for command in ("ffmpeg", "ffprobe"):
        if not binaries[command]:
            blockers.append(f"{command} was not found on PATH or in the project's local tools")
    if sys.platform.startswith("linux") and not Path("/dev/dri/renderD128").exists():
        blockers.append("Upstream VAAPI device /dev/dri/renderD128 is missing")
    print(json.dumps({"platform":sys.platform,"steam_library":str(library),"game_dir":str(game),"cs2_installed":info_path.is_file(),"cs2_version":steam_info,"upstream":lock,"tools":binaries,"plugin":str(plugin),"blockers":blockers,"warnings":warnings,"ready_for_attempt":not blockers,"compatibility_verified":False,"training_ready":False,"note":"Read-only static check. Readiness permits an attempt, not an ABI, POV, encoder or timing certification. Windows uses windows.py and temporary per-run plugin staging."}, indent=2))


if __name__ == "__main__":
    main()
