#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
Build a portable Windows release ZIP for Fit8E-ColorCalib:
  - Compiles src/fit8e_color_tool.py into a single windowed Fit8E-ColorCalib.exe via PyInstaller
  - Bundles minimal Google platform-tools (adb.exe, AdbWinApi.dll, AdbWinUsbApi.dll, NOTICE.txt)
  - Includes the calibration profile, both READMEs, and project and runtime license notices
  - Packages everything cleanly into release/Fit8E-ColorCalib-v<ver>-win64.zip
"""

from __future__ import annotations

import argparse
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = runpy.run_path(str(REPO_ROOT / "src" / "fit8e_version.py"))["APP_VERSION"]
PANEL_JSON = "qdcm_calib_data_ar14_1080p_144hz_video_mode_dsi_panel.json"
ADB_FILES = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt"]


def locate_platform_tools(explicit: Path | None) -> Path | None:
    if explicit and (explicit / "adb.exe").is_file():
        return explicit
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        REPO_ROOT / "platform-tools",
        Path(local_app) / "Android" / "Sdk" / "platform-tools" if local_app else None,
    ]
    if local_app:
        wg = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        if wg.is_dir():
            for hit in wg.glob("Google.PlatformTools*/platform-tools"):
                candidates.append(hit)
    w = shutil.which("adb.exe") or shutil.which("adb")
    if w:
        candidates.append(Path(w).resolve().parent)
    for c in candidates:
        if c and (c / "adb.exe").is_file():
            return c
    return None


def copy_runtime_licenses(python: str, destination: Path) -> None:
    # The build interpreter can differ from the interpreter running this script.
    roots = subprocess.check_output(
        [python, "-c", "import sys; from importlib.metadata import distribution; "
         "print(sys.base_prefix); print(distribution('pyinstaller').locate_file(''))"],
        text=True,
    ).splitlines()
    base, packages = map(Path, roots)
    notices = {
        "Python.txt": base / "LICENSE.txt",
        "Tcl.txt": REPO_ROOT / "LICENSES" / "Tcl.txt",
    }
    for name, parent, pattern in (
        ("Tk.txt", base, "tcl/tk*/license.terms"),
        ("PyInstaller.txt", packages, "pyinstaller-*.dist-info/**/COPYING.txt"),
    ):
        notice = next(parent.glob(pattern), None)
        if notice is None:
            raise FileNotFoundError(f"Missing {name} notice under {parent}")
        notices[name] = notice
    destination.mkdir()
    for name, notice in notices.items():
        shutil.copy2(notice, destination / name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable, help="Python interpreter with PyInstaller installed")
    parser.add_argument("--platform-tools", type=Path, default=None, help="Directory containing adb.exe & DLLs")
    parser.add_argument("--version", default=APP_VERSION, choices=[APP_VERSION],
                        help="The package must use the application version")
    args = parser.parse_args()

    pt_dir = locate_platform_tools(args.platform_tools)
    if not pt_dir:
        print("ERROR: Could not locate adb.exe / platform-tools. Pass --platform-tools <dir>.", file=sys.stderr)
        return 1
    print(f"Using platform-tools: {pt_dir}")

    json_src = REPO_ROOT / "assets" / PANEL_JSON
    if not json_src.is_file():
        print(f"ERROR: Missing calibration JSON: {json_src}", file=sys.stderr)
        return 1

    rel_root = REPO_ROOT / "release"
    rel_root.mkdir(exist_ok=True)
    pkg_name = f"Fit8E-ColorCalib-v{args.version}-win64"
    with tempfile.TemporaryDirectory(prefix=".win-build-", dir=rel_root) as directory:
        stage = Path(directory)
        build_package(args.python, pt_dir, json_src, pkg_name, stage)
        final = rel_root / pkg_name
        previous = stage / "previous-package"
        if final.exists():
            if final.is_symlink() or final.resolve().parent != rel_root.resolve():
                raise ValueError(f"Unexpected package directory: {final}")
            final.replace(previous)
        try:
            (stage / pkg_name).replace(final)
            (stage / f"{pkg_name}.zip").replace(rel_root / f"{pkg_name}.zip")
        except OSError:
            if final.exists():
                final.replace(stage / pkg_name)
            if previous.exists():
                previous.replace(final)
            raise
    print(f"Built portable package: {final}")
    print(f"Built ZIP archive: {rel_root / (pkg_name + '.zip')}")
    return 0


def build_package(python: str, pt_dir: Path, json_src: Path, pkg_name: str, stage: Path) -> None:
    work_build = stage / "build"
    work_dist = stage / "dist"
    pkg_dir = stage / pkg_name

    # Windows Explorer reads the PE version resource independently of the GUI title.
    work_build.mkdir(exist_ok=True)
    version_file = work_build / "windows-version.txt"
    version_tuple = (*map(int, APP_VERSION.split(".")), 0)
    version_file.write_text(f"""VSVersionInfo(
    ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple},
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'Fit8E-ColorCalib'),
        StringStruct('ProductName', 'Fit8E-ColorCalib'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('ProductVersion', '{APP_VERSION}')
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")

    # Run PyInstaller
    add_data = f"{json_src};assets"
    helper = REPO_ROOT / "scripts" / "fit8e_calib_shizuku.sh"
    ico_src = REPO_ROOT / "assets" / "app_icon.ico"
    cmd = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--version-file", str(version_file),
        "--name", "Fit8E-ColorCalib",
        "--paths", str(REPO_ROOT / "src"),
        "--add-data", add_data,
        "--add-data", f"{helper};scripts",
        "--distpath", str(work_dist),
        "--workpath", str(work_build),
        "--specpath", str(work_build),
    ]
    measurements = REPO_ROOT / "assets" / "measurements"
    for name in ("factory2015-normalized.ti3", "factory1931.ti3"):
        cmd += ["--add-data", f"{measurements / name};assets/measurements"]
    if ico_src.is_file():
        cmd += ["--icon", str(ico_src), "--add-data", f"{ico_src};assets"]
    png_src = REPO_ROOT / "assets" / "app_icon_48.png"
    if png_src.is_file():
        cmd += ["--add-data", f"{png_src};assets"]
    cmd.append(str(REPO_ROOT / "src" / "fit8e_color_tool.py"))
    print("Running PyInstaller:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    exe_path = work_dist / "Fit8E-ColorCalib.exe"
    if not exe_path.is_file():
        print("ERROR: PyInstaller did not produce Fit8E-ColorCalib.exe", file=sys.stderr)
        return 1

    # Assemble portable directory
    pkg_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe_path, pkg_dir / "Fit8E-ColorCalib.exe")

    (pkg_dir / "assets").mkdir(exist_ok=True)
    shutil.copy2(json_src, pkg_dir / "assets" / PANEL_JSON)
    shutil.copytree(measurements, pkg_dir / "assets" / "measurements")

    (pkg_dir / "scripts").mkdir(exist_ok=True)
    shutil.copy2(helper, pkg_dir / "scripts")

    (pkg_dir / "platform-tools").mkdir(exist_ok=True)
    for fname in ADB_FILES:
        shutil.copy2(pt_dir / fname, pkg_dir / "platform-tools" / fname)

    for doc in ("README.md", "README_EN.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(REPO_ROOT / doc, pkg_dir / doc)
    copy_runtime_licenses(python, pkg_dir / "licenses")

    # Create ZIP
    zip_path = stage / f"{pkg_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(pkg_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(stage))


if __name__ == "__main__":
    sys.exit(main())
