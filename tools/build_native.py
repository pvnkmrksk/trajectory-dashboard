"""Build a self-contained native dashboard; run on each target operating system."""

from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "daari-deepa.spec"),
    ],
    cwd=ROOT,
    check=True,
)
print("Standalone package: " + str(ROOT / "dist" / "DaariDeepa"))

if sys.platform != "win32":
    launcher = (
        ROOT
        / "dist"
        / "DaariDeepa"
        / ("OpenDashboard.command" if sys.platform == "darwin" else "OpenDashboard.sh")
    )
    launcher.write_text(
        '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec ./DaariDeepa --open-browser "$@"\n'
    )
    launcher.chmod(0o755)

platform_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
architecture = {"aarch64": "arm64", "AMD64": "x64", "x86_64": "x64"}.get(
    platform.machine(), platform.machine()
)
archive = shutil.make_archive(
    str(ROOT / "dist" / f"DaariDeepa-{platform_name}-{architecture}"),
    "zip",
    ROOT / "dist",
    "DaariDeepa",
)
print("Download archive: " + archive)
