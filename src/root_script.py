#!/usr/bin/env python3
"""
Self-contained AYASettings Root Script (.sh) generator for KONKR Pocket FIT Elite (Fit 8E).

AYASettings' built-in "Root 脚本" runner (`com.ayaneo.settings.ui.optimization.RootScriptFragment`):
  - Enforces a 1,048,576-byte (1 MiB) file size limit
  - Executes via `xsu sh <filePath>` when tapping "整体运行" (Run File)
  - Executes non-comment lines one-by-one when tapping "逐行运行" (Run Line-by-Line)
  - Captures stdout only (not stderr) for its result popup
  - Splits command strings on whitespace in `Runtime.exec(String)`, so filenames must have no spaces

Each generated script embeds the gzip+base64 payload inside `#FIT8E_*_B64:` comment lines and
places its execution logic on a single guarded line with `exec 2>&1`:
  - Under "整体运行", `$0` is the script path and the full transactional `fit8e_calib_shizuku.sh`
    engine runs with full device/panel verification, SHA-256 verification, atomic backup/record,
    SELinux labeling, and a manual-reboot reminder.
  - Under accidental "逐行运行", all `#` payload lines are skipped instantly and the single
    guarded line detects `[ ! -f "$0" ]`, printing a clear reminder to tap "整体运行".

SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import shlex
import tempfile
from pathlib import Path

import color_engine
from fit8e_version import APP_VERSION

MAX_AYA_ROOT_SCRIPT_BYTES = 1_048_576
_B64_WRAP = 200

MODE_LABELS: dict[int, tuple[str, str]] = {
    0: ("自然标准", "Natural"),
    1: ("增强模式", "Boosted"),
    2: ("鲜艳模式", "Vivid"),
    3: ("自适应鲜艳", "Adaptive Vibrant"),
}

PRESET_SPECS: tuple[tuple[str, int, int, str, str], ...] = (
    ("01_Fit8E_安装校色_6500K_D65标准.sh", 6500, 0, "6500K D65 标准（推荐）", "6500K D65 Reference (Recommended)"),
    ("02_Fit8E_安装校色_6000K_暖色.sh",   6000, 0, "6000K 暖色日光",        "6000K Warm Daylight"),
    ("03_Fit8E_安装校色_6800K_微冷.sh",   6800, 0, "6800K 微冷清透",        "6800K Crisp White"),
    ("04_Fit8E_安装校色_7100K_冷白.sh",   7100, 0, "7100K 冷白通透",        "7100K Cool White"),
)


def _gzip_b64_comment_lines(data: bytes, prefix: str) -> str:
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    return "\n".join(f"{prefix}{encoded[i:i + _B64_WRAP]}" for i in range(0, len(encoded), _B64_WRAP))


def _safe_label(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _guard_destination(destination: Path, *protected: Path) -> None:
    dest_res = destination.resolve()
    for p in protected:
        if not p:
            continue
        if dest_res == p.resolve() or (destination.exists() and p.exists() and destination.samefile(p)):
            raise ValueError("Choose an export path separate from built-in project files.")


def _atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
        temp_path = Path(staged.name)
    try:
        temp_path.write_bytes(data)
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)


def default_install_script_name(cct: int = 6500, tint: int = 0) -> str:
    """Return a whitespace-free default filename safe for AYASettings Runtime.exec."""
    if cct == 6500 and tint == 0:
        return f"Fit8E_安装校色_6500K_D65_v{APP_VERSION}.sh"
    if tint == 0:
        return f"Fit8E_安装校色_{cct}K_v{APP_VERSION}.sh"
    return f"Fit8E_安装校色_{cct}K_tint{tint:+d}_v{APP_VERSION}.sh"


# Single-line bootstrap (< 340 bytes, well under xsud's 1024-byte IPC command buffer):
#   - Under AYASettings "整体运行" (xsu sh <path>): $0 is the script file -> extracts and runs #F: runner with "$0".
#   - Under accidental "逐行运行" (xsu <line>): all # comments (#F:/#P:) are skipped and [ ! -f "$0" ] triggers a clear hint.
_BOOTSTRAP_LINE = (
    'exec 2>&1; '
    'if [ ! -f "$0" ]; then '
    'printf "%s\\n" "[提示] 请点击「整体运行」，不要点「逐行运行」！" '
    '"[Hint] Tap Run File (整体运行), NOT Run Line-by-Line."; '
    'exit 1; fi; '
    'w=$(mktemp -d /data/local/tmp/fit8e-root.XXXXXXXX) || { echo "ERROR: cannot create temp dir"; exit 1; }; '
    'trap \'rm -rf "$w"\' 0; '
    'sed -n "s/^#F://p" "$0" | base64 -d | gunzip > "$w/run.sh" || { echo "ERROR: runner decode failed"; exit 1; }; '
    'sh "$w/run.sh" "$0" "$w"'
)


def build_install_root_script(
    helper_bytes: bytes,
    profile_bytes: bytes,
    *,
    cct: int = 6500,
    tint: int = 0,
    label_zh: str = "",
    label_en: str = "",
) -> bytes:
    """Build a self-contained (< 1 MiB) AYASettings Root Script that installs a validated QDCM V3 profile."""
    color_engine.validate_qdcm(profile_bytes)
    helper_lf = helper_bytes.replace(b"\r\n", b"\n")
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    tint_str = f"{tint:+d}" if tint else "0"
    desc_zh = _safe_label(label_zh or (f"{cct}K D65 标准" if (cct == 6500 and tint == 0) else f"{cct}K / 色调 {tint_str}"))
    desc_en = _safe_label(label_en or (f"{cct}K D65 Reference" if (cct == 6500 and tint == 0) else f"{cct}K / Tint {tint_str}"))
    banner_arg = shlex.quote(f"=== Fit 8E 硬件校色 v{APP_VERSION} · {desc_zh} (CIE 2015 2°) ===")

    inner_runner = (
        "#!/system/bin/sh\n"
        "exec 2>&1\n"
        'self=$1; work=$2\n'
        'sed -n "s/^#H://p" "$self" | base64 -d | gunzip > "$work/helper.sh" || { echo "ERROR: helper decode failed"; exit 1; }\n'
        'sed -n "s/^#P://p" "$self" | base64 -d | gunzip > "$work/profile.json" || { echo "ERROR: profile decode failed"; exit 1; }\n'
        f'printf "%s\\n" {banner_arg}\n'
        f'sh "$work/helper.sh" install "$work/profile.json" --sha256 {profile_sha}\n'
        'rc=$?\n'
        'if [ "$rc" = 0 ]; then\n'
        '    printf "%s\\n" "" "【安装成功】SHA-256 与 ar14 面板校验全部通过！" \\\n'
        '        "请【手动重启掌机】以使硬件校色生效（无需连接电脑）。" \\\n'
        '        "重启后默认自然标准 (Mode 0)，可运行切模式脚本随时切到 Mode 3 自适应鲜艳。"\n'
        'else\n'
        '    printf "%s\\n" "" "【安装未完成】退出码: $rc（原有配置已保护，未损坏）。"\n'
        'fi\n'
        'exit "$rc"\n'
    ).encode("utf-8")

    runner_block = _gzip_b64_comment_lines(inner_runner, "#F:")
    helper_block = _gzip_b64_comment_lines(helper_lf, "#H:")
    profile_block = _gzip_b64_comment_lines(profile_bytes, "#P:")

    script = (
        "#!/system/bin/sh\n"
        f"# ======================================================================\n"
        f"# KONKR Pocket FIT Elite (Fit 8E) 硬件校色 v{APP_VERSION} - AYASettings Root 脚本\n"
        f"# 预设档案: {desc_zh} ({desc_en}) · CIE 2015 2° · Mode 0/1/2/3\n"
        f"# 档案摘要: SHA-256 {profile_sha[:16]}...\n"
        f"# 使用方法: 在 AYASettings「Root 脚本」中选中本文件 -> 点击【整体运行】\n"
        f"# 注意事项: 1) 请点【整体运行】勿点「逐行运行」 2) 成功后请手动重启掌机\n"
        f"# ======================================================================\n"
        f"{_BOOTSTRAP_LINE}\n"
        f"{runner_block}\n"
        f"{helper_block}\n"
        f"{profile_block}\n"
    ).encode("utf-8")

    if len(script) >= MAX_AYA_ROOT_SCRIPT_BYTES:
        raise ValueError(
            f"Generated root script ({len(script)} bytes) exceeds AYASettings 1 MiB limit ({MAX_AYA_ROOT_SCRIPT_BYTES})"
        )
    return script


def build_action_root_script(
    helper_bytes: bytes,
    action: str,
    *,
    mode: int | None = None,
) -> bytes:
    """Build a lightweight (~8 KB) self-contained AYASettings Root Script for mode switch, status, or uninstall."""
    if action not in ("mode", "uninstall", "status"):
        raise ValueError(f"Unsupported root script action: {action}")
    if action == "mode":
        if mode not in MODE_LABELS:
            raise ValueError(f"Invalid display mode: {mode}")
        m_zh, m_en = MODE_LABELS[mode]
        title_zh = f"切换色彩模式 Mode {mode}（{m_zh}）"
        title_en = f"Switch Color Mode {mode} ({m_en})"
        helper_cmd = f'sh "$work/helper.sh" mode {mode}'
        ok_block = (
            f'    printf "%s\\n" "" "【切换成功】已实时切换到 Mode {mode} · {m_zh}（无需重启）。"\n'
            '    dumpsys SurfaceFlinger 2>/dev/null | grep -E "DisplayColorSetting:|Current color mode:" | head -n 4\n'
        )
        fail_block = '    printf "%s\\n" "" "【切换失败】退出码: $rc"\n'
        usage_note = "无需重启，点击【整体运行】后立即生效"
    elif action == "uninstall":
        title_zh = "卸载校色并还原原厂"
        title_en = "Uninstall Calibration & Restore Factory"
        helper_cmd = 'sh "$work/helper.sh" uninstall'
        ok_block = (
            '    printf "%s\\n" "" "【还原成功】已恢复首次安装前的原厂档案与显示模式。" \\\n'
            '        "请【手动重启掌机】以恢复原厂显示状态。"\n'
        )
        fail_block = '    printf "%s\\n" "" "【还原未完成】退出码: $rc（现有档案已保留）。"\n'
        usage_note = "还原成功后请手动重启掌机"
    else:
        title_zh = "查看当前校色与色彩模式状态"
        title_en = "Inspect Calibration & Display Mode Status"
        helper_cmd = 'sh "$work/helper.sh" status'
        ok_block = '    printf "%s\\n" "" "【查询完成】"\n'
        fail_block = '    printf "%s\\n" "" "【查询失败】退出码: $rc"\n'
        usage_note = "只读查询，不修改任何系统配置"

    helper_lf = helper_bytes.replace(b"\r\n", b"\n")
    inner_runner = (
        "#!/system/bin/sh\n"
        "exec 2>&1\n"
        'self=$1; work=$2\n'
        'sed -n "s/^#H://p" "$self" | base64 -d | gunzip > "$work/helper.sh" || { echo "ERROR: helper decode failed"; exit 1; }\n'
        f'printf "%s\\n" "=== Fit 8E 硬件校色 v{APP_VERSION} · {title_zh} ==="\n'
        f'{helper_cmd}\n'
        'rc=$?\n'
        'if [ "$rc" = 0 ]; then\n'
        f'{ok_block}'
        'else\n'
        f'{fail_block}'
        'fi\n'
        'exit "$rc"\n'
    ).encode("utf-8")

    runner_block = _gzip_b64_comment_lines(inner_runner, "#F:")
    helper_block = _gzip_b64_comment_lines(helper_lf, "#H:")

    script = (
        "#!/system/bin/sh\n"
        f"# ======================================================================\n"
        f"# KONKR Pocket FIT Elite (Fit 8E) 硬件校色 v{APP_VERSION} - {title_zh}\n"
        f"# Action: {title_en}\n"
        f"# 使用方法: 在 AYASettings「Root 脚本」中选中本文件 -> 点击【整体运行】\n"
        f"# 说明: {usage_note}；请勿点击「逐行运行」\n"
        f"# ======================================================================\n"
        f"{_BOOTSTRAP_LINE}\n"
        f"{runner_block}\n"
        f"{helper_block}\n"
    ).encode("utf-8")

    if len(script) >= MAX_AYA_ROOT_SCRIPT_BYTES:
        raise ValueError(f"Generated root script exceeds AYASettings 1 MiB limit ({len(script)} bytes)")
    return script


def export_install_root_script(
    base_json: Path,
    helper_path: Path,
    destination: Path,
    cct: int = 6500,
    tint: int = 0,
    *,
    label_zh: str = "",
    label_en: str = "",
) -> tuple[int, dict]:
    """Synthesize (cct, tint) QDCM profile and atomically write a self-contained AYASettings Root Script."""
    _guard_destination(destination, base_json, helper_path)
    raw_profile, preview = color_engine.synthesize_qdcm_bytes(base_json, cct, tint)
    helper_bytes = helper_path.read_bytes()
    script_bytes = build_install_root_script(
        helper_bytes, raw_profile, cct=cct, tint=tint, label_zh=label_zh, label_en=label_en
    )
    _atomic_write(destination, script_bytes)
    return len(script_bytes), preview


def build_preset_bundle(base_json: Path, helper_path: Path, out_dir: Path) -> list[Path]:
    """Generate the complete preset AYASettings Root Script pack (4 CCT presets + 4 modes + status + uninstall + guide)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    helper_bytes = helper_path.read_bytes()
    created: list[Path] = []

    for filename, cct, tint, zh, en in PRESET_SPECS:
        dest = out_dir / filename
        export_install_root_script(base_json, helper_path, dest, cct, tint, label_zh=zh, label_en=en)
        created.append(dest)

    mode_files = (
        ("10_Fit8E_切换模式0_自然标准.sh", 0),
        ("11_Fit8E_切换模式3_自适应鲜艳.sh", 3),
        ("12_Fit8E_切换模式1_增强模式.sh", 1),
        ("13_Fit8E_切换模式2_鲜艳模式.sh", 2),
    )
    for filename, m in mode_files:
        dest = out_dir / filename
        _atomic_write(dest, build_action_root_script(helper_bytes, "mode", mode=m))
        created.append(dest)

    status_path = out_dir / "20_Fit8E_查看当前状态.sh"
    _atomic_write(status_path, build_action_root_script(helper_bytes, "status"))
    created.append(status_path)

    uninstall_path = out_dir / "99_Fit8E_卸载还原原厂.sh"
    _atomic_write(uninstall_path, build_action_root_script(helper_bytes, "uninstall"))
    created.append(uninstall_path)

    guide_path = out_dir / "使用说明_README.txt"
    guide_text = (
        f"KONKR Pocket FIT Elite (Fit 8E) 掌机端 Root 脚本使用说明 (v{APP_VERSION})\n"
        "=====================================================================\n"
        "适用机型：仅限骁龙 8 Elite (SM8750P) + ar14 144Hz 内屏的 Fit 8E，其他机型请勿运行。\n"
        "特点：无需电脑、无需开 USB 调试、无需 Shizuku，每个 .sh 均为单文件自包含脚本，与电脑版工具完全互通。\n\n"
        "一、安装校色步骤\n"
        "1. 将本目录中的脚本解压到掌机内部存储（推荐 /sdcard/Download/Fit8E/，注意路径和文件名不要包含空格）。\n"
        "2. 打开掌机内置「AYASettings」->「设备」->「Root 脚本」-> 点击中间「+」打开文件选择器。\n"
        "3. 【关键】点击左上角菜单展开左侧边栏，务必选择左侧的「Pocket FIT Elite」（不要选「最近」，否则系统文件管理器会返回空路径），再进入解压目录挑选想要的色温预设脚本：\n"
        "   - 01_Fit8E_安装校色_6500K_D65标准.sh  （CIE 2015 2° 实测 D65 标准，推荐）\n"
        "   - 02_Fit8E_安装校色_6000K_暖色.sh     （护眼微暖）\n"
        "   - 03_Fit8E_安装校色_6800K_微冷.sh     （略微清冷通透）\n"
        "   - 04_Fit8E_安装校色_7100K_冷白.sh     （更偏向传统冷白）\n"
        "4. 返回「Root 脚本」页面后，先确认输入框中已显示脚本路径/文件名称且不为空（底部按钮亮红），然后务必点击【整体运行】（不要点「逐行运行」）。\n"
        "5. 等待结果弹窗提示「安装成功」后，【手动重启掌机】即自动生效。\n\n"
        "二、切换色彩模式（重启激活后实时生效，无需再重启）\n"
        "按同样步骤在「Root 脚本」中选中以下脚本（确认路径非空）并点【整体运行】：\n"
        "   - 10_Fit8E_切换模式0_自然标准.sh      （sRGB/P3 参考色准 + HDR10 PQ）\n"
        "   - 11_Fit8E_切换模式3_自适应鲜艳.sh    （sRGB 鲜艳+肤色保护，P3/HDR10 自动切换）\n"
        "   - 12_Fit8E_切换模式1_增强模式.sh      （sRGB 全局微提饱和）\n"
        "   - 13_Fit8E_切换模式2_鲜艳模式.sh      （面板原生广色域 + 所选白点）\n\n"
        "三、查看状态与还原原厂\n"
        "   - 20_Fit8E_查看当前状态.sh            （查询当前安装摘要、激活状态与生效模式）\n"
        "   - 99_Fit8E_卸载还原原厂.sh            （完整恢复首次安装前的原厂配置，完成后请手动重启）\n\n"
        "---------------------------------------------------------------------\n"
        f"English Quick Guide (v{APP_VERSION})\n"
        "1. Extract these .sh files to internal storage (no spaces in path/filename, e.g. /sdcard/Download/Fit8E/).\n"
        "2. Open AYASettings -> Device -> Root Script -> tap '+' to open the file picker.\n"
        "3. IMPORTANT: Open the left sidebar and pick 'Pocket FIT Elite' (do NOT pick 'Recent', which returns an empty path), then browse to your preset script (01..04).\n"
        "4. Back on the Root Script dialog, confirm the filename/path field is NOT empty (buttons turn red), then tap Run File (整体运行), NOT Run Line-by-Line (逐行运行).\n"
        "5. Reboot manually after the success popup. Use scripts 10..13 to switch modes live without rebooting,\n"
        "   script 20 to inspect status, and script 99 to restore factory state (then reboot manually).\n"
    )
    # Use UTF-8 with BOM so Android Chrome / Windows Notepad decode local text/plain as UTF-8 instead of GBK.
    _atomic_write(guide_path, guide_text.encode("utf-8-sig"))
    created.append(guide_path)
    return created
