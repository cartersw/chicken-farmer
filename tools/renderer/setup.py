"""Prepare a pinned renderer checkout; optionally compile without installing into CS2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def local_tool(name: str) -> str:
    """Find a PATH tool or an executable installed beside this Python."""
    executable = name + (".exe" if sys.platform == "win32" else "")
    adjacent = Path(sys.executable).parent / executable
    return shutil.which(name) or (str(adjacent) if adjacent.is_file() else name)


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Compile Go renderer (any OS)")
    parser.add_argument("--build-plugin", action="store_true", help="Compile native C++ plugin; never install it or launch CS2")
    parser.add_argument("--go", default="go", help="Go executable")
    parser.add_argument("--cmake", default=local_tool("cmake"), help="CMake executable")
    parser.add_argument("--generator", help="Optional CMake generator, e.g. Visual Studio 18 2026")
    args = parser.parse_args()
    lock = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
    checkout = ROOT / "upstream"
    if not checkout.exists():
        run("git", "clone", "--no-checkout", lock["repository"], str(checkout))
        run("git", "checkout", "--detach", lock["commit"], cwd=checkout)
    head = run("git", "rev-parse", "HEAD", cwd=checkout)
    if head != lock["commit"]:
        raise RuntimeError(f"Checkout is {head}, expected {lock['commit']}; refusing to reset existing files")
    patch = ROOT / "patches" / "0001-runtime-profile-and-clean-worker.patch"
    # A repeat setup is harmless. Refuse conflicts instead of resetting local changes.
    check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=checkout, capture_output=True)
    if check.returncode == 0:
        run("git", "apply", str(patch), cwd=checkout)
    else:
        run("git", "apply", "--reverse", "--check", str(patch), cwd=checkout)
    for source in (ROOT / "overlay").glob("*.go"):
        destination = checkout / "dem-render" / source.name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Modified overlay {destination}; review differences before replacing it")
        shutil.copyfile(source, destination)
    if args.build:
        (ROOT / "build").mkdir(exist_ok=True)
        binary = ROOT / "build" / ("dem-render.exe" if sys.platform == "win32" else "dem-render")
        print(run(args.go, "build", "-o", str(binary), ".", cwd=checkout / "dem-render"))
    if args.build_plugin:
        if sys.platform == "win32":
            plugin_source = ROOT / "plugin-windows"
            plugin_build = ROOT / "build" / "plugin-windows"
            configure = [args.cmake, "-S", str(plugin_source), "-B", str(plugin_build),
                         f"-DPython3_EXECUTABLE={sys.executable}"]
            if not args.generator or args.generator.startswith("Visual Studio"):
                configure.extend(["-A", "x64"])
        elif sys.platform.startswith("linux"):
            plugin_source = checkout / "cs2-server-plugin"
            plugin_build = ROOT / "build" / "plugin"
            configure = [args.cmake, "-S", str(plugin_source), "-B", str(plugin_build), "-DCMAKE_BUILD_TYPE=Release"]
        else:
            raise RuntimeError("Plugin build supports Windows and Linux only")
        if args.generator:
            configure.extend(["-G", args.generator])
        print(run(*configure))
        print(run(args.cmake, "--build", str(plugin_build), "--config", "Release"))
    print(f"Prepared {lock['repository']} at {head}. No Steam launch or game installation changes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"renderer setup failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            if exc.stdout:
                print(exc.stdout, file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        raise SystemExit(1)
