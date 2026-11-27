#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Extract the matching version section from CHANGELOG.md and format GitHub Release notes."""

from __future__ import annotations

import argparse
import re
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = runpy.run_path(str(REPO_ROOT / "src" / "fit8e_version.py"))["APP_VERSION"]
REPO_URL = "https://github.com/lx081081/Fit8E-ColorCalib"


def extract_changelog_section(changelog_text: str, version: str) -> str:
    """Return the markdown body under `## <version>` up to the next `## ` heading."""
    lines = changelog_text.splitlines()
    header = f"## {version}"
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start = idx + 1
            break
    if start is None:
        raise ValueError(f"CHANGELOG.md does not contain a '{header}' section.")
    end = len(lines)
    for idx in range(start, len(lines)):
        if re.match(r"^##\s+\S", lines[idx]):
            end = idx
            break
    section = "\n".join(lines[start:end]).strip()
    if not section:
        raise ValueError(f"CHANGELOG.md section '{header}' is empty.")
    return section


def build_release_notes(changelog_text: str, version: str) -> str:
    """Assemble full GitHub Release notes from the standard download guide and CHANGELOG.md section."""
    section = extract_changelog_section(changelog_text, version)
    tag = f"v{version}"
    return (
        "**本项目由 GPT6.2 Astra 和 Gemini4 Neon 构建。** 应用代码按 GPL-3.0-only 开源。"
        "本版适用于配备骁龙 8 Elite 和 `ar14` 内屏的 KONKR Pocket FIT Elite（Fit 8E）。\n\n"
        "### 下载 / Downloads\n\n"
        f"- **Windows 64 位便携包（`Fit8E-ColorCalib-{tag}-win64.zip`）**：完整解压后运行 `Fit8E-ColorCalib.exe`，"
        "已内置 ADB、默认校色档与掌机端预设 Root 脚本。\n"
        f"- **掌机免电脑 Root 脚本预设包（`Fit8E-ColorCalib-{tag}-root-scripts.zip`）**：无需电脑与 USB 调试，"
        "解压到掌机内部存储（路径和文件名不含空格，如 `/sdcard/Download/Fit8E/`），在 **AYASettings → Root 脚本** 中点击 **「整体运行」** 即可使用。\n"
        f"- **macOS / Linux / Shizuku 源码包（`Fit8E-ColorCalib-{tag}-macos-linux-shizuku.zip`）**："
        "包含命令行工具、掌机端脚本、校色档与测量数据。\n\n"
        f"{section}\n\n"
        "---\n\n"
        "安装或卸载前请保存游戏进度：Windows 工具默认自动重启，掌机端 Root 脚本安装/卸载后会提示手动重启。\n\n"
        f"[中文说明]({REPO_URL}/blob/{tag}/README.md) · "
        f"[English]({REPO_URL}/blob/{tag}/README_EN.md) · "
        f"[完整更新记录]({REPO_URL}/blob/{tag}/CHANGELOG.md)\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GitHub Release notes from CHANGELOG.md")
    parser.add_argument("--version", default=APP_VERSION, help="Release version without leading 'v'")
    parser.add_argument("--changelog", type=Path, default=REPO_ROOT / "CHANGELOG.md")
    parser.add_argument("--out", type=Path, default=None, help="Write markdown output to file instead of stdout")
    args = parser.parse_args(argv)

    try:
        notes = build_release_notes(args.changelog.read_text(encoding="utf-8"), args.version)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(notes, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
