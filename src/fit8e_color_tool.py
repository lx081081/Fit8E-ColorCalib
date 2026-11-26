#!/usr/bin/env python3
"""
KONKR Pocket FIT Elite (SM8750P) Hardware Color Calibration Tool
Zero-Root / Zero-Unlock / Zero-Flash native Qualcomm QDCM V3 HPM installer,
hardware color-temperature & tint compiler, and live mode switcher.

Supports both a minimalist light-mode Tkinter GUI (default) and a full CLI.
SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import argparse
import hashlib
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Ensure sibling color_engine.py is importable whether run from repo root, src/, or PyInstaller bundle
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import color_engine  # noqa: E402
from fit8e_version import APP_VERSION  # noqa: E402

PANEL_FILENAME = "qdcm_calib_data_ar14_1080p_144hz_video_mode_dsi_panel.json"
REMOTE_DATA_PATH = f"/data/vendor/display/{PANEL_FILENAME}"

CCT_PRESETS = [
    (6000, "preset_6000"),
    (6500, "preset_6500"),
    (6800, "preset_6800"),
    (7100, "preset_7100"),
]

# =============================================================================
# Localization (Chinese / English)
# =============================================================================
STRINGS = {
    "zh": {
        "title": "KONKR Pocket FIT Elite 色彩校准",
        "win_title": f"KONKR Pocket FIT Elite 色彩校准 v{APP_VERSION}",
        "subtitle": "免 Root 原生硬件色彩管理 · sRGB / Display P3 / HDR10 PQ · 硬件色温调节",
        "lang_btn": "EN",
        "sec_modes": "色彩模式切换（实时生效，无需重启）",
        "sec_log": "运行日志",
        "btn_refresh": "刷新",
        "btn_restart_adb": "重置 ADB",
        "btn_install_reboot": "一键安装校色并重启",
        "btn_install_only": "仅安装（稍后重启）",
        "btn_uninstall": "卸载并还原原厂",
        "btn_export": "导出 JSON",
        "btn_browse": "自定义 JSON...",
        "btn_reset_profile": "恢复默认",
        "chk_force": "已手动核实 ar14 面板",
        "lbl_profile": "校色档案：",
        "lbl_cct": "白点色温",
        "lbl_warm": "5500K 暖",
        "lbl_cool": "7500K 冷",
        "lbl_tint": "红绿微调",
        "tint_zero": "0 中性",
        "tint_mag": "{v} 偏品红",
        "tint_grn": "+{v} 偏绿",
        "bright_chip": "峰值保留 {pct}%",
        "preset_6000": "6000K 暖色",
        "preset_6500": "6500K 标准",
        "preset_6800": "6800K 微冷",
        "preset_7100": "7100K 冷白",
        "profile_builtin_d65": "内置公版（6500K D65 实测验证 · ΔE 0.46）",
        "profile_builtin_tuned": "内置公版（实时硬件编译 · {cct}K · 色调 {tint}）",
        "st_disconnected": "未检测到设备 · 请开启 USB 调试并连接数据线",
        "st_offline": "设备离线 (offline) · 请解锁屏幕后重插 USB 线或重开 USB 调试",
        "st_unauthorized": "等待授权 · 请在掌机屏幕上点击「允许 USB 调试」",
        "st_active": "已安装并生效",
        "st_reboot_needed": "已安装，重启后生效",
        "st_stock": "原厂未校色",
        "st_system_managed": "系统仍报告色彩管理模式",
        "st_wrong_model": "机型不匹配",
        "st_wrong_soc": "芯片型号不匹配",
        "st_panel_unknown": "Fit 8E · 面板待确认",
        "st_panel_mismatch": "Fit 8E · 面板不匹配",
        "badge_rec": "推荐",
        "mode_0_title": "自然标准",
        "mode_0_desc": "sRGB 精准缩限 (ΔE 0.46)\nP3 与 HDR10 PQ 自动切换",
        "mode_1_title": "增强模式",
        "mode_1_desc": "sRGB 基准 +10% 饱和度\n标准白点均衡输出",
        "mode_2_title": "鲜艳模式",
        "mode_2_desc": "原生 98% P3 广色域\n精准白点 + Gamma 2.2",
        "mode_active_tag": "当前生效",
        "confirm_install_title": "确认安装硬件校色",
        "confirm_install_msg": (
            "即将向掌机写入 SM8750P 硬件校色档案：\n"
            "  校色配置：{profile_desc}\n"
            f"  目标路径：{REMOTE_DATA_PATH}\n"
            "  特点：免 Root / 不修改系统分区 / OTA 无损 / 随时可还原。\n\n"
            "{reboot_note}"
        ),
        "reboot_note_yes": "安装完成后掌机将自动重启以使校色与色温生效。是否继续？",
        "reboot_note_no": "安装完成后请手动重启掌机以使校色与色温生效。是否继续？",
        "confirm_uninstall_title": "确认卸载并还原原厂",
        "confirm_uninstall_msg": (
            "将恢复首次安装前的档案和显示模式。选中的自定义 JSON 可用于核对旧版安装。\n"
            "完成后掌机将自动重启。是否继续？"
        ),
        "err_no_adb": "未找到 adb.exe · 请将 platform-tools 放在程序同目录或配置 PATH",
        "err_no_json": "找不到校色 JSON 文件：",
        "err_wrong_device": (
            "{reason}\n"
            "本工具专为 KONKR Pocket FIT Elite (SM8750P, ar14 144Hz) 定制。\n"
            "当前设备：{model} ({device}, SoC={soc}, 面板={panel})\n"
            "为防止显示异常已中止操作。"
        ),
        "msg_done_install_reboot": "安装完成并已发送重启指令！\n重启解锁进入桌面后，原生硬件色彩管理与新色温即自动生效。",
        "msg_done_install_only": "安装完成（SHA-256 校验一致）！\n请手动重启掌机，开机后即自动生效。",
        "msg_no_install": "设备上没有已安装的校色档。",
        "msg_done_uninstall": "已恢复原有档案及显示模式，并发送重启指令。",
        "msg_done_export": "已导出硬件校色 JSON（{cct}K / 色调 {tint}）：\n{path}",
        "info_line_fmt": "{soc}  ·  {panel}  ·  色彩管理: {cm}  ·  当前管线: {mode}",
    },
    "en": {
        "title": "KONKR Pocket FIT Elite Color Calibration",
        "win_title": f"KONKR Pocket FIT Elite Color Calibration v{APP_VERSION}",
        "subtitle": "Zero-Root Native Hardware Color Management · sRGB / P3 / HDR10 PQ · Whitepoint Tuning",
        "lang_btn": "中文",
        "sec_modes": "Color Mode Switcher (Live, No Reboot)",
        "sec_log": "Activity Log",
        "btn_refresh": "Refresh",
        "btn_restart_adb": "Reset ADB",
        "btn_install_reboot": "Install Calibration & Reboot",
        "btn_install_only": "Install Only",
        "btn_uninstall": "Uninstall & Restore",
        "btn_export": "Export JSON",
        "btn_browse": "Custom JSON...",
        "btn_reset_profile": "Reset Default",
        "chk_force": "I verified the ar14 panel",
        "lbl_profile": "Profile:",
        "lbl_cct": "Whitepoint CCT",
        "lbl_warm": "5500K Warm",
        "lbl_cool": "7500K Cool",
        "lbl_tint": "Green/Magenta Tint",
        "tint_zero": "0 Neutral",
        "tint_mag": "{v} Magenta",
        "tint_grn": "+{v} Green",
        "bright_chip": "Peak {pct}%",
        "preset_6000": "6000K Warm",
        "preset_6500": "6500K D65",
        "preset_6800": "6800K Crisp",
        "preset_7100": "7100K Cool",
        "profile_builtin_d65": "Built-in Verified D65 (6500K · ΔE 0.46)",
        "profile_builtin_tuned": "Built-in Hardware Compiled ({cct}K · Tint {tint})",
        "st_disconnected": "No device detected · Enable USB Debugging and connect via USB",
        "st_offline": "Device offline · Unlock screen & replug USB cable (or toggle USB Debugging)",
        "st_unauthorized": "Unauthorized · Tap 'Allow USB debugging' on handheld screen",
        "st_active": "Installed & Active",
        "st_reboot_needed": "Installed · Reboot Required",
        "st_stock": "Stock (Uncalibrated)",
        "st_system_managed": "System reports managed color modes",
        "st_wrong_model": "Device model mismatch",
        "st_wrong_soc": "SoC mismatch",
        "st_panel_unknown": "Fit 8E · Panel unconfirmed",
        "st_panel_mismatch": "Fit 8E · Panel mismatch",
        "badge_rec": "Rec.",
        "mode_0_title": "Natural",
        "mode_0_desc": "Accurate sRGB clamp (ΔE 0.46)\nAuto Display P3 & HDR10 PQ",
        "mode_1_title": "Boosted",
        "mode_1_desc": "sRGB base +10% saturation\nBalanced calibrated whitepoint",
        "mode_2_title": "Vivid",
        "mode_2_desc": "Full native 98% P3 gamut\nCalibrated whitepoint + Gamma 2.2",
        "mode_active_tag": "Active",
        "confirm_install_title": "Confirm Calibration Install",
        "confirm_install_msg": (
            "Ready to push SM8750P hardware calibration profile:\n"
            "  Profile: {profile_desc}\n"
            f"  Target: {REMOTE_DATA_PATH}\n"
            "  Zero-Root · No system partition edits · OTA-safe · 100% reversible.\n\n"
            "{reboot_note}"
        ),
        "reboot_note_yes": "The handheld will reboot automatically to apply the profile. Proceed?",
        "reboot_note_no": "You will need to reboot manually afterwards to activate. Proceed?",
        "confirm_uninstall_title": "Confirm Uninstall",
        "confirm_uninstall_msg": (
            "Restore the previous file and display mode. A selected custom JSON can identify a legacy install.\n"
            "The handheld will reboot automatically. Proceed?"
        ),
        "err_no_adb": "adb binary not found · Place platform-tools next to this app or in PATH",
        "err_no_json": "Calibration JSON not found: ",
        "err_wrong_device": (
            "{reason}\n"
            "This tool is built specifically for KONKR Pocket FIT Elite (SM8750P, ar14 144Hz).\n"
            "Detected: {model} ({device}, SoC={soc}, panel={panel})\n"
            "Operation aborted to protect your display."
        ),
        "msg_done_install_reboot": "Calibration installed and reboot triggered!\nUnlock after boot and hardware color management will be live.",
        "msg_done_install_only": "Calibration installed (SHA-256 verified)!\nPlease reboot your handheld to activate.",
        "msg_no_install": "No installed calibration profile.",
        "msg_done_uninstall": "Previous calibration and display mode restored. Reboot requested.",
        "msg_done_export": "Exported hardware calibration JSON ({cct}K / Tint {tint}):\n{path}",
        "info_line_fmt": "{soc}  ·  {panel}  ·  Color Mgmt: {cm}  ·  Pipeline: {mode}",
    },
}


# =============================================================================
# Core ADB & Calibration Engine
# =============================================================================
def get_resource_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    script_dir = Path(__file__).resolve().parent
    roots.append(script_dir)
    roots.append(script_dir.parent)
    roots.append(Path.cwd())
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def find_default_json() -> Optional[Path]:
    for root in get_resource_roots():
        for rel in (Path("assets") / PANEL_FILENAME, Path(PANEL_FILENAME)):
            p = root / rel
            if p.is_file():
                return p
    return None


def find_app_icon() -> Optional[Path]:
    for root in get_resource_roots():
        for rel in (Path("assets") / "app_icon.ico", Path("app_icon.ico")):
            p = root / rel
            if p.is_file():
                return p
    return None


def find_header_logo() -> Optional[Path]:
    for root in get_resource_roots():
        for rel in (Path("assets") / "app_icon_48.png", Path("app_icon_48.png")):
            p = root / rel
            if p.is_file():
                return p
    return None


def find_adb() -> Optional[str]:
    exe_name = "adb.exe" if os.name == "nt" else "adb"
    for root in get_resource_roots():
        for rel in (Path("platform-tools") / exe_name, Path(exe_name)):
            p = root / rel
            if p.is_file():
                return str(p)
    w = shutil.which("adb")
    if w:
        return w
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            sdk_adb = Path(local_app) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
            if sdk_adb.is_file():
                return str(sdk_adb)
            winget_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
            if winget_root.is_dir():
                for hit in winget_root.glob("Google.PlatformTools*/platform-tools/adb.exe"):
                    if hit.is_file():
                        return str(hit)
    return None


def _run_subproc(cmd: list[str], timeout: int = 15, stdin: Optional[bytes] = None) -> subprocess.CompletedProcess[str]:
    # Binary stdin keeps the shell helper LF-only on Windows.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    p = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, creationflags=flags)
    return subprocess.CompletedProcess(cmd, p.returncode, p.stdout.decode("utf-8", "replace"),
                                      p.stderr.decode("utf-8", "replace"))


@dataclass
class DeviceStatus:
    adb_path: Optional[str] = None
    state: str = "disconnected"
    serial: str = ""
    model: str = ""
    device: str = ""
    product: str = ""
    soc: str = ""
    platform_board: str = ""
    build_id: str = ""
    panel_mode: str = ""
    panel_state: str = "unknown"
    panel_source: str = ""
    panel_timing: str = ""
    model_ok: bool = False
    hardware_ok: bool = False
    error: str = ""
    sf_color_setting: str = ""
    sf_active_mode: str = ""
    sf_color_modes: list[str] = field(default_factory=list)
    sys_color_mode: Optional[int] = None
    installed_data: bool = False
    installed_data_md5: str = ""
    has_root_tool: str = ""

    @property
    def target_issue(self) -> str:
        if not self.model_ok:
            return "st_wrong_model"
        if not self.hardware_ok:
            return "st_wrong_soc"
        if self.panel_state == "unknown":
            return "st_panel_unknown"
        if self.panel_state != "match":
            return "st_panel_mismatch"
        return ""

    @property
    def is_target_device(self) -> bool:
        return self.state == "device" and not self.target_issue


class AdbClient:
    def __init__(self, log_fn: Optional[Callable[[str], None]] = None, serial: Optional[str] = None):
        self.adb = find_adb()
        self.log_fn = log_fn or (lambda _msg: None)
        self.serial = serial or os.environ.get("ANDROID_SERIAL")

    def log(self, msg: str) -> None:
        self.log_fn(msg)

    def refresh_adb(self) -> Optional[str]:
        self.adb = find_adb()
        return self.adb

    def run(self, args: list[str], timeout: int = 15, *, serial: Optional[str] = None,
            stdin: Optional[bytes] = None) -> tuple[int, str, str]:
        if not self.adb:
            return 127, "", "adb not found"
        cmd = [self.adb]
        if serial:
            cmd.extend(["-s", serial])
        try:
            p = _run_subproc(cmd + args, timeout, stdin)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"ADB timeout ({timeout}s)"
        except OSError as exc:
            return 1, "", str(exc)

    def _helper(self, serial: str, args: list[str], *, root: str = "") -> tuple[int, str, str]:
        # Only the project helper or PyInstaller's embedded copy may run on the device.
        base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else _THIS_DIR.parent
        try:
            script = (base / "scripts" / "fit8e_calib_shizuku.sh").read_bytes().replace(b"\r\n", b"\n")
        except OSError as exc:
            return 1, "", f"Device helper unavailable: {exc}"
        command = shlex.join(["sh", "-s", "--", *args])
        if root:
            if root not in ("su", "xsu"):
                return 1, "", "Unrecognized root command"
            command = shlex.join([root, "-c", command])
        return self.run(["shell", "-T", command], 40, serial=serial, stdin=script)

    def restart_server(self) -> None:
        self.log("[ADB] Restarting adb server...")
        self.run(["kill-server"], timeout=10)
        rc, out, err = self.run(["start-server"], timeout=15)
        self.log(f"[ADB] start-server rc={rc} {(out + err).strip()}")

    def _probe_serial(self, serial: str, state: str) -> DeviceStatus:
        st = DeviceStatus(adb_path=self.adb, serial=serial, state=state)
        if state != "device":
            return st
        rc, out, err = self._helper(serial, ["probe"])
        head, marker, sf = out.partition("---SF---")
        fields = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
        if rc or not marker or not fields.get("DEVICE") or fields.get("PANEL_STATE") not in ("match", "mismatch", "unknown"):
            st.state = "probe_error"
            st.error = (err or out or "Device identity probe returned no data").strip()
            return st
        st.model = fields.get("MODEL", "")
        st.device = fields["DEVICE"]
        st.product = fields.get("PRODUCT", "")
        st.soc = fields.get("SOC", "")
        st.platform_board = fields.get("BOARD", "")
        st.build_id = fields.get("BUILD", "")
        st.panel_mode = fields.get("PANEL", "")
        st.panel_state = fields["PANEL_STATE"]
        st.panel_source = fields.get("PANEL_SOURCE", "")
        st.panel_timing = fields.get("PANEL_TIMING", "")
        st.hardware_ok = fields.get("HARDWARE_OK") == "1"
        st.model_ok = fields.get("MODEL_OK", fields.get("HARDWARE_OK")) == "1"
        try:
            st.sys_color_mode = int(fields.get("SYSMODE", ""))
        except ValueError:
            pass
        md5 = fields.get("DATA_MD5", "").lower()
        if re.fullmatch(r"[0-9a-f]{32}", md5):
            st.installed_data = True
            st.installed_data_md5 = md5
        if fields.get("ROOTTOOL") in ("su", "xsu"):
            st.has_root_tool = fields["ROOTTOOL"]
        for line in sf.splitlines():
            line = line.strip()
            if line.startswith("DisplayColorSetting:"):
                st.sf_color_setting = line.partition(":")[2].strip()
            elif line.startswith("Current color mode:"):
                st.sf_active_mode = line.partition(":")[2].strip()
            elif line.startswith("ColorMode::"):
                st.sf_color_modes.append(line)
        return st

    def probe(self, serial: Optional[str] = None) -> DeviceStatus:
        selected = serial or self.serial
        st = DeviceStatus(adb_path=self.adb, serial=selected or "")
        if not self.adb:
            return st
        rc, out, err = self.run(["devices"], timeout=10)
        if rc:
            st.state, st.error = "probe_error", (err or out).strip()
            return st
        devices = {}
        for line in out.splitlines():
            if "\t" in line:
                name, state = line.split("\t", 1)
                devices[name] = state.strip()
        if selected:
            return self._probe_serial(selected, devices.get(selected, "disconnected"))
        if not devices:
            return st
        online = [name for name, state in devices.items() if state == "device"]
        if len(online) == 1:
            return self._probe_serial(online[0], "device")
        if len(devices) == 1:
            return self._probe_serial(*next(iter(devices.items())))
        matches = []
        for name in online:
            candidate = self._probe_serial(name, "device")
            if candidate.is_target_device:
                matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
        st.state = "ambiguous"
        st.error = "Select a device with --serial or ANDROID_SERIAL: " + ", ".join(devices)
        return st

    @staticmethod
    def target_error(st: DeviceStatus, force: bool) -> str:
        if st.state != "device" or not st.serial:
            return st.error or f"Device not ready ({st.state})"
        issue = st.target_issue
        if not issue or (force and issue == "st_panel_unknown"):
            return ""
        return f"{STRINGS['en'][issue]}: {st.device} / {st.soc} / panel {st.panel_mode or '(unavailable)'}"

    def _apply(self, st: DeviceStatus, args: list[str], force: bool, reboot: bool) -> tuple[bool, str]:
        if force:
            args = [*args, "--force"]
        rc, out, err = self._helper(st.serial, args)
        if rc == 73 and st.has_root_tool:
            self.log("[ADB] Display directory requires elevated file permissions; retrying the same transaction.")
            rc, out, err = self._helper(st.serial, args, root=st.has_root_tool)
        detail = (out + "\n" + err).strip()
        if rc:
            return False, detail or f"Device operation failed ({rc})"
        if reboot and not detail.startswith("UNCHANGED:"):
            return self._reboot(st, detail)
        if detail:
            self.log(detail)
        return True, detail

    def _with_profile(self, st: DeviceStatus, operation: str, raw: bytes, force: bool, reboot: bool) -> tuple[bool, str]:
        rc, out, err = self.run(["shell", "mktemp /data/local/tmp/fit8e-qdcm.XXXXXXXX"], serial=st.serial)
        remote = out.strip()
        if rc or not re.fullmatch(r"/data/local/tmp/fit8e-qdcm\.[a-zA-Z0-9]+", remote):
            return False, f"Cannot stage profile: {(err or out).strip()}"
        try:
            # Push precisely the bytes validated on the host, including custom inputs.
            with tempfile.TemporaryDirectory(prefix="fit8e-") as folder:
                local = Path(folder) / "profile.json"
                local.write_bytes(raw)
                rc, out, err = self.run(["push", str(local), remote], 30, serial=st.serial)
            if rc:
                return False, f"Profile transfer failed: {(err or out).strip()}"
            args = [operation, remote]
            if operation == "install":
                args.extend(["--sha256", hashlib.sha256(raw).hexdigest()])
            # Remove the upload before requesting a reboot (which drops ADB).
            ok, detail = self._apply(st, args, force, reboot=False)
        finally:
            rc, out, err = self.run(["shell", "rm -f " + remote], serial=st.serial)
            if rc:
                self.log(f"[ADB] Unable to clear upload {remote}: {(err or out).strip()}")
        if ok and reboot and not detail.startswith("UNCHANGED:"):
            return self._reboot(st, detail)
        return ok, detail

    def _reboot(self, st: DeviceStatus, detail: str) -> tuple[bool, str]:
        rc, out, err = self.run(["reboot"], 10, serial=st.serial)
        if rc:
            return False, f"{detail}\nReboot request failed: {(err or out).strip()}. Reboot manually."
        return True, detail.split(" Reboot to activate.")[0] + " Reboot requested."

    def install(self, local_json: Path, force: bool = False, reboot: bool = True,
                cct: Optional[int] = None, tint: int = 0, *, serial: Optional[str] = None) -> tuple[bool, str]:
        try:
            if cct is not None and (cct != 6500 or tint != 0):
                if not (5500 <= cct <= 7500 and -10 <= tint <= 10):
                    raise ValueError("CCT must be 5500..7500 K and tint -10..10")
                raw, _ = color_engine.synthesize_qdcm_bytes(local_json, cct, tint)
            else:
                raw = local_json.read_bytes()
            color_engine.validate_qdcm(raw)
            st = self.probe(serial)
            error = self.target_error(st, force)
            if error:
                return False, error
            return self._with_profile(st, "install", raw, force, reboot)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, str(exc)

    def set_mode(self, mode: int, *, serial: Optional[str] = None, force: bool = False) -> tuple[bool, str]:
        if mode not in (0, 1, 2):
            return False, f"Invalid mode: {mode}"
        st = self.probe(serial)
        error = self.target_error(st, force)
        if error:
            return False, error
        return self._apply(st, ["mode", str(mode)], force, reboot=False)

    def uninstall(self, reboot: bool = True, *, serial: Optional[str] = None, force: bool = False,
                  legacy_json: Optional[Path] = None) -> tuple[bool, str]:
        st = self.probe(serial)
        error = self.target_error(st, force)
        if error:
            return False, error
        if legacy_json is None:
            return self._apply(st, ["uninstall"], force, reboot)
        try:
            raw = legacy_json.read_bytes()
            color_engine.validate_qdcm(raw)
            return self._with_profile(st, "uninstall", raw, force, reboot)
        except (OSError, ValueError) as exc:
            return False, str(exc)


# =============================================================================
# Modern High-DPI Minimalist Light Tkinter GUI
# =============================================================================
def launch_gui(serial: Optional[str] = None) -> int:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    # Crisp Apple / Win11 Fluent Light Palette
    C_BG        = "#F6F8FA"
    C_WHITE     = "#FFFFFF"
    C_SUB       = "#F3F4F6"
    C_SUB_H     = "#E5E7EB"
    C_BORDER    = "#E5E7EB"
    C_BORDER_MD = "#D1D5DB"
    C_TEXT      = "#111827"
    C_TEXT2     = "#374151"
    C_MUTED     = "#6B7280"
    C_ACCENT    = "#2563EB"
    C_ACCENT_H  = "#1D4ED8"
    C_BLUE_BG   = "#EFF6FF"
    C_BLUE_BD   = "#93C5FD"
    C_BLUE_TX   = "#1D4ED8"
    C_GREEN_BG  = "#ECFDF5"
    C_GREEN_H   = "#D1FAE5"
    C_GREEN_BD  = "#10B981"
    C_GREEN_TX  = "#047857"
    C_AMBER_BG  = "#FFFBEB"
    C_AMBER_TX  = "#B45309"
    C_RED_BG    = "#FEF2F2"
    C_RED_H     = "#FEE2E2"
    C_RED_BD    = "#FECACA"
    C_RED_TX    = "#DC2626"

    FONT_UI = "Microsoft YaHei UI" if os.name == "nt" else "Helvetica"
    FONT_MONO = "Consolas" if os.name == "nt" else "Menlo"

    class _GradientSlider(tk.Canvas):
        """Minimalist horizontal slider with smooth 3-stop gradient track and crisp circular thumb."""
        def __init__(
            self, parent: tk.Widget, variable: tk.IntVar,
            from_: int, to: int, step: int,
            colors: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
            on_change: Callable[[], None], dpi_scale: float = 1.0,
        ) -> None:
            self._h = max(22, int(24 * dpi_scale))
            super().__init__(parent, height=self._h, bg=C_WHITE, highlightthickness=0, bd=0, cursor="hand2")
            self.var = variable
            self.from_ = from_
            self.to = to
            self.step = step
            self.colors = colors
            self.on_change = on_change
            self.dpi_scale = dpi_scale
            self.enabled = True
            self.bind("<Configure>", lambda _e: self.redraw())
            self.bind("<Button-1>", self._on_pointer)
            self.bind("<B1-Motion>", self._on_pointer)

        def set_enabled(self, enabled: bool) -> None:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self.redraw()

        def _interp_color(self, t: float) -> str:
            c0, c1, c2 = self.colors
            if t <= 0.5:
                u = t * 2.0
                r = int(c0[0] + (c1[0] - c0[0]) * u)
                g = int(c0[1] + (c1[1] - c0[1]) * u)
                b = int(c0[2] + (c1[2] - c0[2]) * u)
            else:
                u = (t - 0.5) * 2.0
                r = int(c1[0] + (c2[0] - c1[0]) * u)
                g = int(c1[1] + (c2[1] - c1[1]) * u)
                b = int(c1[2] + (c2[2] - c1[2]) * u)
            return f"#{r:02X}{g:02X}{b:02X}"

        def _on_pointer(self, event) -> None:
            if not self.enabled:
                return
            w = max(1, self.winfo_width())
            pad = int(10 * self.dpi_scale)
            span = max(1, w - 2 * pad)
            frac = max(0.0, min(1.0, (event.x - pad) / span))
            raw = self.from_ + frac * (self.to - self.from_)
            snapped = int(round((raw - self.from_) / self.step) * self.step + self.from_)
            snapped = max(self.from_, min(self.to, snapped))
            if self.var.get() != snapped:
                self.var.set(snapped)
                self.on_change()
            self.redraw()

        def redraw(self) -> None:
            self.delete("all")
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 20:
                return
            pad = int(10 * self.dpi_scale)
            cy = h // 2
            th = max(6, int(6 * self.dpi_scale))
            y0, y1 = cy - th // 2, cy + th // 2
            x0, x1 = pad, w - pad
            span = max(1, x1 - x0)

            # Smooth gradient track (32 segments)
            segs = 32
            for i in range(segs):
                sx0 = x0 + (span * i) / segs
                sx1 = x0 + (span * (i + 1)) / segs + 0.5
                col = "#E5E7EB" if not self.enabled else self._interp_color((i + 0.5) / segs)
                self.create_rectangle(sx0, y0, sx1, y1, fill=col, outline="")
            # Subtle hairline frame around track
            self.create_rectangle(x0, y0, x1, y1, fill="", outline="#CBD5E1", width=1)
            # Quiet center tick mark (6500K / 0 neutral)
            cx_mid = x0 + span * 0.5
            self.create_line(cx_mid, y0 - 2, cx_mid, y1 + 2, fill="#94A3B8", width=1)

            # Crisp circular thumb with brand-blue ring
            frac = (self.var.get() - self.from_) / max(1, (self.to - self.from_))
            tx = x0 + frac * span
            rad = max(6, int(7 * self.dpi_scale))
            ring = C_ACCENT if self.enabled else "#9CA3AF"
            ring_w = max(2, int(2.2 * self.dpi_scale))
            self.create_oval(tx - rad, cy - rad, tx + rad, cy + rad, fill="#FFFFFF", outline=ring, width=ring_w)

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.lang = "zh"
            self.default_json = find_default_json()
            self.selected_json: Optional[Path] = self.default_json
            self.is_custom_json = False
            self.busy = False
            self.active_mode_id: Optional[int] = None
            self.status = DeviceStatus()
            self.log_q: queue.Queue[str] = queue.Queue()
            self.client = AdbClient(log_fn=self._enqueue_log, serial=serial)

            self.cct_var = tk.IntVar(value=6500)
            self.tint_var = tk.IntVar(value=0)
            self.force_var = tk.BooleanVar(value=False)

            self.title(self.t("win_title"))
            self.configure(bg=C_BG)

            if os.name == "nt":
                ico = find_app_icon()
                if ico:
                    try:
                        self.iconbitmap(str(ico))
                    except Exception:
                        pass

            # DPI-aware compact balanced window geometry
            dpi_scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
            self.dpi_scale = dpi_scale
            scr_w, scr_h = self.winfo_screenwidth(), self.winfo_screenheight()
            win_w = min(int(860 * dpi_scale), int(scr_w * 0.90))
            win_h = min(int(585 * dpi_scale), int(scr_h * 0.88))
            min_w = min(int(780 * dpi_scale), int(scr_w * 0.85))
            min_h = min(int(545 * dpi_scale), int(scr_h * 0.84))
            pos_x = max(0, (scr_w - win_w) // 2)
            pos_y = max(0, (scr_h - win_h) // 2)
            self.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
            self.minsize(min_w, min_h)

            self._build_ui()
            self._poll_log_queue()
            self._enqueue_log(f"Ready · v{APP_VERSION} · ADB: {Path(self.client.adb).name if self.client.adb else 'not found'}")
            self.after(120, self.refresh_status)

        def t(self, key: str) -> str:
            return STRINGS[self.lang][key]

        def _enqueue_log(self, msg: str) -> None:
            ts = time.strftime("%H:%M:%S")
            self.log_q.put(f"[{ts}] {msg}\n")

        def _poll_log_queue(self) -> None:
            try:
                while True:
                    line = self.log_q.get_nowait()
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", line)
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
            except queue.Empty:
                pass
            self.after(100, self._poll_log_queue)

        def _bind_hover(self, btn: tk.Button, normal_bg: str, hover_bg: str) -> None:
            def on_enter(_e):
                if str(btn["state"]) != "disabled":
                    btn.configure(bg=hover_bg)
            def on_leave(_e):
                if str(btn["state"]) != "disabled":
                    btn.configure(bg=normal_bg)
            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)

        def _btn(
            self, parent: tk.Widget, text: str, cmd: Callable,
            bg: str = C_WHITE, fg: str = C_TEXT2, hover_bg: str = C_SUB,
            border: Optional[str] = C_BORDER_MD, bold: bool = False,
            padx: int = 14, pady: int = 7, font_size: int = 9,
        ) -> tuple[tk.Widget, tk.Button]:
            if border:
                wrap = tk.Frame(parent, bg=border, padx=1, pady=1)
                b = tk.Button(
                    wrap, text=text, command=cmd, bg=bg, fg=fg,
                    activebackground=hover_bg, activeforeground=fg,
                    disabledforeground="#9CA3AF",
                    relief="flat", bd=0, cursor="hand2",
                    font=(FONT_UI, font_size, "bold" if bold else "normal"),
                    padx=padx, pady=pady,
                )
                b.pack()
                self._bind_hover(b, bg, hover_bg)
                return wrap, b
            b = tk.Button(
                parent, text=text, command=cmd, bg=bg, fg=fg,
                activebackground=hover_bg, activeforeground=fg,
                disabledforeground="#BFDBFE" if bg == C_ACCENT else "#9CA3AF",
                relief="flat", bd=0, cursor="hand2",
                font=(FONT_UI, font_size, "bold" if bold else "normal"),
                padx=padx, pady=pady,
            )
            self._bind_hover(b, bg, hover_bg)
            return b, b

        def _draw_logo_emblem(self, parent: tk.Widget) -> tk.Widget:
            logo_png = find_header_logo()
            if logo_png:
                try:
                    self._logo_img = tk.PhotoImage(file=str(logo_png))
                    return tk.Label(parent, image=self._logo_img, bg=C_WHITE, bd=0)
                except Exception:
                    pass
            sz = int(34 * self.dpi_scale)
            cv = tk.Canvas(parent, width=sz, height=sz, bg=C_WHITE, highlightthickness=0, bd=0)
            s = sz / 34.0
            cv.create_oval(10 * s,  3 * s, 24 * s, 17 * s, fill="#10B981", outline="")
            cv.create_oval( 4 * s, 14 * s, 18 * s, 28 * s, fill="#F43F5E", outline="")
            cv.create_oval(16 * s, 14 * s, 30 * s, 28 * s, fill="#2563EB", outline="")
            cv.create_oval(12.5 * s, 12.5 * s, 21.5 * s, 21.5 * s, fill="#FFFFFF", outline="#111827", width=max(1.2, 1.5 * s))
            cv.create_oval(15.3 * s, 15.3 * s, 18.7 * s, 18.7 * s, fill="#2563EB", outline="")
            return cv

        def _build_ui(self) -> None:
            # ── Top Pure-White Header Band ───────────────────────────────────
            hdr_bar = tk.Frame(self, bg=C_WHITE)
            hdr_bar.pack(fill="x")
            hdr_inner = tk.Frame(hdr_bar, bg=C_WHITE)
            hdr_inner.pack(fill="x", padx=22, pady=(13, 12))

            w_lang, self.btn_lang = self._btn(
                hdr_inner, self.t("lang_btn"), self._toggle_lang,
                bg=C_WHITE, fg=C_TEXT2, hover_bg=C_SUB, border=C_BORDER, padx=10, pady=4, font_size=8
            )
            w_lang.pack(side="right", padx=(6, 0))
            w_adb, self.btn_restart_adb = self._btn(
                hdr_inner, self.t("btn_restart_adb"), self._restart_adb,
                bg=C_WHITE, fg=C_TEXT2, hover_bg=C_SUB, border=C_BORDER, padx=10, pady=4, font_size=8
            )
            w_adb.pack(side="right", padx=(6, 0))
            w_ref, self.btn_refresh = self._btn(
                hdr_inner, self.t("btn_refresh"), self.refresh_status,
                bg=C_WHITE, fg=C_TEXT2, hover_bg=C_SUB, border=C_BORDER, padx=10, pady=4, font_size=8
            )
            w_ref.pack(side="right")

            emblem = self._draw_logo_emblem(hdr_inner)
            emblem.pack(side="left", padx=(0, 12))
            hdr_txt = tk.Frame(hdr_inner, bg=C_WHITE)
            hdr_txt.pack(side="left", fill="x", expand=True)
            title_row = tk.Frame(hdr_txt, bg=C_WHITE)
            title_row.pack(fill="x")
            self.lbl_title = tk.Label(
                title_row, text=self.t("title"), bg=C_WHITE, fg=C_TEXT,
                font=(FONT_UI, 12, "bold"), anchor="w"
            )
            self.lbl_title.pack(side="left")
            tk.Label(
                title_row, text=f"v{APP_VERSION}", bg=C_SUB, fg=C_MUTED,
                font=(FONT_UI, 8), padx=6, pady=1
            ).pack(side="left", padx=(8, 0))
            self.lbl_sub = tk.Label(
                hdr_txt, text=self.t("subtitle"), bg=C_WHITE, fg=C_MUTED,
                font=(FONT_UI, 8), anchor="w"
            )
            self.lbl_sub.pack(fill="x", pady=(2, 0))

            tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x")

            # ── Main Content Container ───────────────────────────────────────
            content = tk.Frame(self, bg=C_BG)
            content.pack(fill="both", expand=True, padx=22, pady=(12, 14))

            # 1. Compact Device Status Card
            dev_card = tk.Frame(content, bg=C_WHITE, highlightbackground=C_BORDER, highlightthickness=1)
            dev_card.pack(fill="x", pady=(0, 10))
            dev_in = tk.Frame(dev_card, bg=C_WHITE)
            dev_in.pack(fill="x", padx=16, pady=11)

            dev_top = tk.Frame(dev_in, bg=C_WHITE)
            dev_top.pack(fill="x")
            self.lbl_md5_tag = tk.Label(dev_top, text="", bg=C_WHITE, fg=C_MUTED, font=(FONT_MONO, 8), anchor="e")
            self.lbl_md5_tag.pack(side="right")
            self.status_pill = tk.Label(
                dev_top, text="...", bg=C_SUB, fg=C_MUTED,
                font=(FONT_UI, 8, "bold"), padx=10, pady=3
            )
            self.status_pill.pack(side="left")
            self.lbl_dev_name = tk.Label(
                dev_top, text="", bg=C_WHITE, fg=C_TEXT,
                font=(FONT_UI, 10, "bold"), anchor="w"
            )
            self.lbl_dev_name.pack(side="left", padx=(10, 0), fill="x", expand=True)

            self.lbl_dev_meta = tk.Label(dev_in, text="", bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 8), anchor="w")
            self.lbl_dev_meta.pack(fill="x", pady=(6, 0))

            # 2. Calibration, Whitepoint & Actions Card
            act_card = tk.Frame(content, bg=C_WHITE, highlightbackground=C_BORDER, highlightthickness=1)
            act_card.pack(fill="x", pady=(0, 12))
            act_in = tk.Frame(act_card, bg=C_WHITE)
            act_in.pack(fill="x", padx=16, pady=13)

            # 2a. Whitepoint Presets + Live Swatch Readout
            wp_top = tk.Frame(act_in, bg=C_WHITE)
            wp_top.pack(fill="x")

            # Right readout (packed first so never clips)
            self.lbl_bright_chip = tk.Label(
                wp_top, text="", bg=C_SUB, fg=C_MUTED,
                font=(FONT_UI, 8), padx=8, pady=2
            )
            self.lbl_bright_chip.pack(side="right")
            self.lbl_cct_val = tk.Label(
                wp_top, text="6500K", bg=C_WHITE, fg=C_TEXT,
                font=(FONT_UI, 9, "bold"), padx=6
            )
            self.lbl_cct_val.pack(side="right")
            sw_sz = int(16 * self.dpi_scale)
            self.swatch_box = tk.Frame(
                wp_top, bg="#FFFFFF", width=sw_sz, height=sw_sz,
                highlightbackground="#CBD5E1", highlightthickness=1
            )
            self.swatch_box.pack_propagate(False)
            self.swatch_box.pack(side="right", padx=(6, 2))

            self.lbl_cct_k = tk.Label(wp_top, text=self.t("lbl_cct"), bg=C_WHITE, fg=C_TEXT2, font=(FONT_UI, 9, "bold"))
            self.lbl_cct_k.pack(side="left", padx=(0, 10))

            self.preset_widgets: dict[int, tuple[tk.Frame, tk.Button, str]] = {}
            for cct_v, pkey in CCT_PRESETS:
                wrap = tk.Frame(wp_top, bg=C_BORDER, padx=1, pady=1)
                wrap.pack(side="left", padx=(0, 6))
                b = tk.Button(
                    wrap, text=self.t(pkey),
                    command=lambda v=cct_v: self._select_preset(v),
                    bg=C_WHITE, fg=C_TEXT2, activebackground=C_BLUE_BG, activeforeground=C_BLUE_TX,
                    disabledforeground="#9CA3AF",
                    relief="flat", bd=0, cursor="hand2",
                    font=(FONT_UI, 8), padx=9, pady=2,
                )
                b.pack()
                self.preset_widgets[cct_v] = (wrap, b, pkey)

            # 2b. Dual Sliders Row: CCT (left, wider) + Tint (right, compact)
            sl_row = tk.Frame(act_in, bg=C_WHITE)
            sl_row.pack(fill="x", pady=(10, 2))
            sl_row.grid_columnconfigure(0, weight=5)
            sl_row.grid_columnconfigure(1, weight=3)

            # CCT slider block (subtle warm -> white -> cool gradient track)
            cct_blk = tk.Frame(sl_row, bg=C_WHITE)
            cct_blk.grid(row=0, column=0, sticky="ew", padx=(0, 18))
            self.lbl_warm = tk.Label(cct_blk, text=self.t("lbl_warm"), bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 7))
            self.lbl_warm.pack(side="left", padx=(0, 6))
            self.lbl_cool = tk.Label(cct_blk, text=self.t("lbl_cool"), bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 7))
            self.lbl_cool.pack(side="right", padx=(6, 0))
            self.scale_cct = _GradientSlider(
                cct_blk, self.cct_var, from_=5500, to=7500, step=50,
                colors=((253, 213, 148), (248, 250, 252), (165, 206, 255)),
                on_change=self._on_wp_change, dpi_scale=self.dpi_scale,
            )
            self.scale_cct.pack(side="left", fill="x", expand=True)

            # Tint slider block (subtle magenta -> white -> green gradient track)
            tint_blk = tk.Frame(sl_row, bg=C_WHITE)
            tint_blk.grid(row=0, column=1, sticky="ew")
            self.lbl_tint_k = tk.Label(tint_blk, text=self.t("lbl_tint"), bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 8))
            self.lbl_tint_k.pack(side="left", padx=(0, 6))
            self.lbl_tint_val = tk.Label(tint_blk, text=self.t("tint_zero"), bg=C_WHITE, fg=C_TEXT2, font=(FONT_UI, 8), width=9, anchor="e")
            self.lbl_tint_val.pack(side="right", padx=(6, 0))
            self.scale_tint = _GradientSlider(
                tint_blk, self.tint_var, from_=-10, to=10, step=1,
                colors=((249, 180, 214), (248, 250, 252), (146, 232, 192)),
                on_change=self._on_wp_change, dpi_scale=self.dpi_scale,
            )
            self.scale_tint.pack(side="left", fill="x", expand=True)

            # Hairline divider between Whitepoint and Action Buttons
            tk.Frame(act_in, bg="#F1F5F9", height=1).pack(fill="x", pady=(10, 12))

            # 2c. Primary Action Buttons Row
            btn_row = tk.Frame(act_in, bg=C_WHITE)
            btn_row.pack(fill="x")
            w_un, self.btn_uninstall = self._btn(
                btn_row, self.t("btn_uninstall"), self._do_uninstall,
                bg=C_RED_BG, fg=C_RED_TX, hover_bg=C_RED_H, border=C_RED_BD, padx=14, pady=8
            )
            w_un.pack(side="right")
            w_ir, self.btn_install_reboot = self._btn(
                btn_row, self.t("btn_install_reboot"), lambda: self._do_install(reboot=True),
                bg=C_ACCENT, fg="#FFFFFF", hover_bg=C_ACCENT_H, border=None, bold=True, padx=20, pady=9
            )
            w_ir.pack(side="left")
            w_io, self.btn_install_only = self._btn(
                btn_row, self.t("btn_install_only"), lambda: self._do_install(reboot=False),
                bg=C_WHITE, fg=C_TEXT2, hover_bg=C_SUB, border=C_BORDER_MD, padx=14, pady=8
            )
            w_io.pack(side="left", padx=(10, 0))

            # 2d. Quiet Profile & Export Line
            tk.Frame(act_in, bg="#F1F5F9", height=1).pack(fill="x", pady=(11, 8))
            prof_row = tk.Frame(act_in, bg=C_WHITE)
            prof_row.pack(fill="x")
            w_rst, self.btn_reset_prof = self._btn(
                prof_row, self.t("btn_reset_profile"), self._reset_profile,
                bg=C_WHITE, fg=C_MUTED, hover_bg=C_SUB, border=None, padx=8, pady=2, font_size=8
            )
            w_rst.pack(side="right", padx=(4, 0))
            w_br, self.btn_browse = self._btn(
                prof_row, self.t("btn_browse"), self._browse_json,
                bg=C_SUB, fg=C_TEXT2, hover_bg=C_SUB_H, border=None, padx=9, pady=2, font_size=8
            )
            w_br.pack(side="right", padx=(6, 0))
            w_exp, self.btn_export = self._btn(
                prof_row, self.t("btn_export"), self._export_json,
                bg=C_SUB, fg=C_TEXT2, hover_bg=C_SUB_H, border=None, padx=9, pady=2, font_size=8
            )
            w_exp.pack(side="right", padx=(6, 0))
            self.lbl_prof_k = tk.Label(prof_row, text=self.t("lbl_profile"), bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 8))
            self.lbl_prof_k.pack(side="left")
            self.lbl_prof_v = tk.Label(prof_row, text="", bg=C_WHITE, fg=C_TEXT2, font=(FONT_UI, 8, "bold"), anchor="w")
            self.lbl_prof_v.pack(side="left", fill="x", expand=True, padx=(2, 8))

            # 3. Color Mode Switcher Section (3 Crisp White Tiles)
            self.hdr_modes = tk.Label(
                content, text=self.t("sec_modes"), bg=C_BG, fg=C_MUTED,
                font=(FONT_UI, 8, "bold"), anchor="w"
            )
            self.hdr_modes.pack(fill="x", pady=(0, 6))

            mode_grid = tk.Frame(content, bg=C_BG)
            mode_grid.pack(fill="x", pady=(0, 12))
            self.mode_cards: dict[int, dict[str, tk.Widget]] = {}
            for col in range(3):
                mode_grid.grid_columnconfigure(col, weight=1, uniform="mode")

            for m in (0, 1, 2):
                cf = tk.Frame(mode_grid, bg=C_WHITE, highlightbackground=C_BORDER, highlightthickness=1, cursor="hand2")
                cf.grid(row=0, column=m, sticky="nsew", padx=(0 if m == 0 else 10, 0))

                top_r = tk.Frame(cf, bg=C_WHITE)
                top_r.pack(fill="x", padx=14, pady=(11, 4))
                tag_lbl = tk.Label(top_r, text="", bg=C_WHITE, fg=C_GREEN_TX, font=(FONT_UI, 7, "bold"), padx=6, pady=2)
                tag_lbl.pack(side="right")
                t_lbl = tk.Label(top_r, text=self.t(f"mode_{m}_title"), bg=C_WHITE, fg=C_TEXT, font=(FONT_UI, 10, "bold"), anchor="w")
                t_lbl.pack(side="left")
                rec_lbl = None
                if m == 0:
                    rec_lbl = tk.Label(top_r, text=self.t("badge_rec"), bg=C_BLUE_BG, fg=C_BLUE_TX, font=(FONT_UI, 7, "bold"), padx=6, pady=1)
                    rec_lbl.pack(side="left", padx=(6, 0))

                d_lbl = tk.Label(cf, text=self.t(f"mode_{m}_desc"), bg=C_WHITE, fg=C_MUTED, font=(FONT_UI, 8), justify="left", anchor="w")
                d_lbl.pack(fill="x", padx=14, pady=(0, 11))

                widgets = [cf, top_r, t_lbl, d_lbl, tag_lbl] + ([rec_lbl] if rec_lbl else [])
                for w in widgets:
                    w.bind("<Button-1>", lambda _e, mode=m: self._do_mode(mode))
                    w.bind("<Enter>", lambda _e, mode=m: self._hover_mode(mode, True))
                    w.bind("<Leave>", lambda _e, mode=m: self._hover_mode(mode, False))

                self.mode_cards[m] = {
                    "frame": cf, "top": top_r, "title": t_lbl,
                    "desc": d_lbl, "tag": tag_lbl, "rec": rec_lbl,
                }

            # 4. Quiet Compact Light Log Footer
            log_hdr = tk.Frame(content, bg=C_BG)
            log_hdr.pack(fill="x", pady=(0, 4))
            self.chk_force = tk.Checkbutton(
                log_hdr, text=self.t("chk_force"), variable=self.force_var,
                bg=C_BG, fg=C_MUTED, selectcolor=C_WHITE,
                activebackground=C_BG, activeforeground=C_TEXT,
                font=(FONT_UI, 8), cursor="hand2"
            )
            self.chk_force.pack(side="right")
            self.hdr_log = tk.Label(log_hdr, text=self.t("sec_log"), bg=C_BG, fg=C_MUTED, font=(FONT_UI, 8, "bold"), anchor="w")
            self.hdr_log.pack(side="left")

            log_wrap = tk.Frame(content, bg=C_WHITE, highlightbackground=C_BORDER, highlightthickness=1)
            log_wrap.pack(fill="both", expand=True)
            self.log_box = scrolledtext.ScrolledText(
                log_wrap, bg="#FAFBFC", fg=C_TEXT2, insertbackground=C_TEXT,
                font=(FONT_MONO, 8), relief="flat", bd=0, state="disabled",
                height=3, padx=10, pady=7
            )
            self.log_box.pack(fill="both", expand=True)

            self._on_wp_change()

        def _select_preset(self, cct: int) -> None:
            if self.is_custom_json:
                return
            self.cct_var.set(cct)
            self._on_wp_change()

        def _format_tint(self, tint: int) -> str:
            if tint == 0:
                return self.t("tint_zero")
            if tint < 0:
                return self.t("tint_mag").format(v=tint)
            return self.t("tint_grn").format(v=tint)

        def _on_wp_change(self) -> None:
            cct = int(self.cct_var.get())
            tint = int(self.tint_var.get())
            prev = color_engine.estimate_whitepoint_preview(cct, tint)

            self.lbl_cct_val.configure(text=f"{cct}K")
            self.lbl_tint_val.configure(text=self._format_tint(tint))
            self.lbl_bright_chip.configure(text=self.t("bright_chip").format(pct=prev["brightness_pct"]))
            self.swatch_box.configure(bg=prev["swatch"])

            for pv, (wrap, btn, _pk) in self.preset_widgets.items():
                if not self.is_custom_json and cct == pv:
                    wrap.configure(bg=C_BLUE_BD)
                    btn.configure(bg=C_BLUE_BG, fg=C_BLUE_TX, font=(FONT_UI, 8, "bold"))
                else:
                    wrap.configure(bg=C_BORDER)
                    btn.configure(bg=C_WHITE, fg=C_TEXT2 if not self.is_custom_json else "#9CA3AF", font=(FONT_UI, 8))

            self.scale_cct.redraw()
            self.scale_tint.redraw()
            self._update_profile_label()

        def _set_wp_controls_enabled(self, enabled: bool) -> None:
            st = "normal" if enabled else "disabled"
            self.scale_cct.set_enabled(enabled)
            self.scale_tint.set_enabled(enabled)
            self.btn_export.configure(state=st)
            for _pv, (_wrap, btn, _pk) in self.preset_widgets.items():
                btn.configure(state=st)
            self._on_wp_change()

        def _hover_mode(self, mode: int, entering: bool) -> None:
            if self.active_mode_id == mode:
                return
            bg = "#F8FAFC" if entering else C_WHITE
            bd = C_BORDER_MD if entering else C_BORDER
            c = self.mode_cards[mode]
            c["frame"].configure(bg=bg, highlightbackground=bd)
            c["top"].configure(bg=bg)
            c["title"].configure(bg=bg)
            c["desc"].configure(bg=bg)
            c["tag"].configure(bg=bg)

        def _current_profile_desc(self) -> str:
            if not self.selected_json:
                return "(missing JSON)"
            if self.is_custom_json:
                return self.selected_json.name
            cct = int(self.cct_var.get())
            tint = int(self.tint_var.get())
            if cct == 6500 and tint == 0:
                return self.t("profile_builtin_d65")
            return self.t("profile_builtin_tuned").format(cct=cct, tint=f"{tint:+d}" if tint else "0")

        def _update_profile_label(self) -> None:
            if not self.selected_json:
                self.lbl_prof_v.configure(text="(未找到校色 JSON)", fg=C_RED_TX)
            elif self.is_custom_json:
                self.lbl_prof_v.configure(text=self.selected_json.name, fg=C_AMBER_TX)
            else:
                cct = int(self.cct_var.get())
                tint = int(self.tint_var.get())
                fg = C_TEXT2 if (cct == 6500 and tint == 0) else C_BLUE_TX
                self.lbl_prof_v.configure(text=self._current_profile_desc(), fg=fg)

        def _toggle_lang(self) -> None:
            self.lang = "en" if self.lang == "zh" else "zh"
            self.title(self.t("win_title"))
            self.lbl_title.configure(text=self.t("title"))
            self.lbl_sub.configure(text=self.t("subtitle"))
            self.btn_lang.configure(text=self.t("lang_btn"))
            self.hdr_modes.configure(text=self.t("sec_modes"))
            self.hdr_log.configure(text=self.t("sec_log"))
            self.btn_refresh.configure(text=self.t("btn_refresh"))
            self.btn_restart_adb.configure(text=self.t("btn_restart_adb"))
            self.btn_install_reboot.configure(text=self.t("btn_install_reboot"))
            self.btn_install_only.configure(text=self.t("btn_install_only"))
            self.btn_uninstall.configure(text=self.t("btn_uninstall"))
            self.btn_export.configure(text=self.t("btn_export"))
            self.btn_browse.configure(text=self.t("btn_browse"))
            self.btn_reset_prof.configure(text=self.t("btn_reset_profile"))
            self.chk_force.configure(text=self.t("chk_force"))
            self.lbl_prof_k.configure(text=self.t("lbl_profile"))
            self.lbl_cct_k.configure(text=self.t("lbl_cct"))
            self.lbl_warm.configure(text=self.t("lbl_warm"))
            self.lbl_cool.configure(text=self.t("lbl_cool"))
            self.lbl_tint_k.configure(text=self.t("lbl_tint"))
            for _pv, (_wrap, btn, pkey) in self.preset_widgets.items():
                btn.configure(text=self.t(pkey))
            for m in (0, 1, 2):
                c = self.mode_cards[m]
                c["title"].configure(text=self.t(f"mode_{m}_title"))
                c["desc"].configure(text=self.t(f"mode_{m}_desc"))
                if c["rec"] is not None:
                    c["rec"].configure(text=self.t("badge_rec"))
            self._on_wp_change()
            self._render_status(self.status)

        def _browse_json(self) -> None:
            p = filedialog.askopenfilename(
                title="Select QDCM V3 Calibration JSON",
                filetypes=[("QDCM JSON", "*.json"), ("All files", "*.*")],
            )
            if p:
                self.selected_json = Path(p)
                self.is_custom_json = True
                self._set_wp_controls_enabled(False)
                self._enqueue_log(f"Custom profile selected: {p}")

        def _reset_profile(self) -> None:
            self.selected_json = self.default_json
            self.is_custom_json = False
            self.cct_var.set(6500)
            self.tint_var.set(0)
            self._set_wp_controls_enabled(True)
            self._enqueue_log("Reset to built-in D65 (6500K / Tint 0) profile.")

        def _export_json(self) -> None:
            if not self.default_json or not self.default_json.is_file():
                messagebox.showerror("Error", self.t("err_no_json") + str(self.default_json))
                return
            cct = int(self.cct_var.get())
            tint = int(self.tint_var.get())
            default_name = PANEL_FILENAME if (cct == 6500 and tint == 0) else f"qdcm_fit8e_{cct}K_tint{tint:+d}.json"
            out_path = filedialog.asksaveasfilename(
                title="Export Hardware Calibration JSON",
                initialfile=default_name,
                defaultextension=".json",
                filetypes=[("QDCM JSON", "*.json")],
            )
            if not out_path:
                return
            try:
                raw, _ = color_engine.synthesize_qdcm_bytes(self.default_json, cct=cct, tint=tint)
                Path(out_path).write_bytes(raw)
                self._enqueue_log(f"[Export] Saved {cct}K / Tint {tint:+d} -> {out_path}")
                messagebox.showinfo("OK", self.t("msg_done_export").format(cct=cct, tint=tint, path=out_path))
            except Exception as exc:
                messagebox.showerror("Export Failed", str(exc))

        def _set_busy(self, busy: bool) -> None:
            self.busy = busy
            st = "disabled" if busy else "normal"
            for b in (self.btn_refresh, self.btn_restart_adb, self.btn_install_reboot, self.btn_install_only, self.btn_uninstall):
                b.configure(state=st)

        def _run_async(self, fn: Callable, on_done: Optional[Callable] = None) -> None:
            if self.busy:
                return
            self._set_busy(True)

            def worker() -> None:
                res = None
                err = None
                try:
                    res = fn()
                except Exception as exc:
                    err = exc
                self.after(0, lambda: self._finish_async(res, err, on_done))

            threading.Thread(target=worker, daemon=True).start()

        def _finish_async(self, res, err, on_done) -> None:
            self._set_busy(False)
            if err is not None:
                self._enqueue_log(f"[Error] {err}")
                messagebox.showerror("Error", str(err))
            elif on_done is not None:
                on_done(res)

        def refresh_status(self) -> None:
            self.client.refresh_adb()
            self._run_async(self.client.probe, self._render_status)

        def _restart_adb(self) -> None:
            def task():
                self.client.restart_server()
                return self.client.probe()
            self._run_async(task, self._render_status)

        def _render_status(self, st: DeviceStatus) -> None:
            self.status = st
            if not st.adb_path:
                self.status_pill.configure(text="ADB 缺失", bg=C_RED_BG, fg=C_RED_TX)
                self.lbl_dev_name.configure(text=self.t("err_no_adb"), fg=C_RED_TX)
                self.lbl_dev_meta.configure(text="")
                self.lbl_md5_tag.configure(text="")
                self._highlight_mode(None)
                return

            if st.state == "disconnected":
                self.status_pill.configure(text="○ 未连接" if self.lang == "zh" else "○ Disconnected", bg=C_SUB, fg=C_MUTED)
                self.lbl_dev_name.configure(text="", fg=C_TEXT)
                self.lbl_dev_meta.configure(text=self.t("st_disconnected"))
                self.lbl_md5_tag.configure(text="")
                self._highlight_mode(None)
                return
            if st.state == "offline":
                self.status_pill.configure(text="● Offline", bg=C_AMBER_BG, fg=C_AMBER_TX)
                self.lbl_dev_name.configure(text=st.serial, fg=C_TEXT)
                self.lbl_dev_meta.configure(text=self.t("st_offline"))
                self.lbl_md5_tag.configure(text="")
                self._highlight_mode(None)
                return
            if st.state == "unauthorized":
                self.status_pill.configure(text="● 待授权" if self.lang == "zh" else "● Unauthorized", bg=C_AMBER_BG, fg=C_AMBER_TX)
                self.lbl_dev_name.configure(text=st.serial, fg=C_TEXT)
                self.lbl_dev_meta.configure(text=self.t("st_unauthorized"))
                self.lbl_md5_tag.configure(text="")
                self._highlight_mode(None)
                return

            if st.state != "device":
                self.status_pill.configure(text=st.state, bg=C_AMBER_BG, fg=C_AMBER_TX)
                self.lbl_dev_name.configure(text=st.serial, fg=C_TEXT)
                self.lbl_dev_meta.configure(text=st.error or st.state)
                self.lbl_md5_tag.configure(text="")
                self._highlight_mode(None)
                return

            has_calibrated_modes = any("SRGB" in m or "DISPLAY_P3" in m for m in st.sf_color_modes)
            if st.target_issue == "st_panel_unknown":
                self.status_pill.configure(text=self.t(st.target_issue), bg=C_AMBER_BG, fg=C_AMBER_TX)
            elif st.target_issue:
                self.status_pill.configure(text=self.t(st.target_issue), bg=C_RED_BG, fg=C_RED_TX)
            elif has_calibrated_modes and st.installed_data:
                self.status_pill.configure(text=f"● {self.t('st_active')}", bg=C_GREEN_BG, fg=C_GREEN_TX)
                self._enqueue_log(f"Connected {st.model} · {st.sf_color_setting} / {st.sf_active_mode} · MD5 {st.installed_data_md5[:8]}")
            elif st.installed_data and not has_calibrated_modes:
                self.status_pill.configure(text=f"● {self.t('st_reboot_needed')}", bg=C_AMBER_BG, fg=C_AMBER_TX)
            elif has_calibrated_modes:
                self.status_pill.configure(text=f"● {self.t('st_system_managed')}", bg=C_GREEN_BG, fg=C_GREEN_TX)
            else:
                self.status_pill.configure(text=f"○ {self.t('st_stock')}", bg=C_SUB, fg=C_MUTED)

            self.lbl_dev_name.configure(text=f"{st.model or st.device}  ({st.serial})", fg=C_TEXT)
            self.lbl_md5_tag.configure(text=f"MD5 {st.installed_data_md5[:8]}" if st.installed_data_md5 else "")
            self.lbl_dev_meta.configure(
                text=self.t("info_line_fmt").format(
                    soc=st.soc or st.platform_board or "-",
                    panel="ar14 · 144 Hz" if st.panel_timing and st.is_target_device else st.panel_mode or "-",
                    cm=st.sf_color_setting or "-",
                    mode=st.sf_active_mode or "-",
                )
            )
            self._highlight_mode(st.sys_color_mode if has_calibrated_modes else None)

        def _highlight_mode(self, active: Optional[int]) -> None:
            self.active_mode_id = active
            for m, c in self.mode_cards.items():
                if active == m:
                    bg = "#F0FDF4"
                    c["frame"].configure(bg=bg, highlightbackground=C_GREEN_BD, highlightthickness=2)
                    c["top"].configure(bg=bg)
                    c["title"].configure(bg=bg, fg="#065F46")
                    c["desc"].configure(bg=bg, fg="#047857")
                    c["tag"].configure(text=self.t("mode_active_tag"), bg=C_GREEN_H, fg=C_GREEN_TX)
                else:
                    c["frame"].configure(bg=C_WHITE, highlightbackground=C_BORDER, highlightthickness=1)
                    c["top"].configure(bg=C_WHITE)
                    c["title"].configure(bg=C_WHITE, fg=C_TEXT)
                    c["desc"].configure(bg=C_WHITE, fg=C_MUTED)
                    c["tag"].configure(text="", bg=C_WHITE)

        def _target_ready(self) -> bool:
            if self.status.state != "device":
                messagebox.showwarning("ADB", self.status.error or self.t("st_disconnected"))
                return False
            if self.client.target_error(self.status, self.force_var.get()):
                messagebox.showerror(
                    "Safety Check",
                    self.t("err_wrong_device").format(
                        reason=self.t(self.status.target_issue), model=self.status.model,
                        device=self.status.device, soc=self.status.soc, panel=self.status.panel_mode or "?"
                    ),
                )
                return False
            return True

        def _do_install(self, reboot: bool) -> None:
            if not self.selected_json or not self.selected_json.is_file():
                messagebox.showerror("Error", self.t("err_no_json") + str(self.selected_json))
                return
            if not self._target_ready():
                return

            msg = self.t("confirm_install_msg").format(
                profile_desc=self._current_profile_desc(),
                reboot_note=self.t("reboot_note_yes") if reboot else self.t("reboot_note_no"),
            )
            if not messagebox.askyesno(self.t("confirm_install_title"), msg):
                return

            selected_serial = self.status.serial
            json_path = self.selected_json
            force = self.force_var.get()
            cct = None if self.is_custom_json else int(self.cct_var.get())
            tint = 0 if self.is_custom_json else int(self.tint_var.get())

            def task():
                ok, detail = self.client.install(json_path, force=force, reboot=reboot, cct=cct, tint=tint, serial=selected_serial)
                new_st = self.client.probe(selected_serial)
                return ok, detail, new_st

            def done(res):
                ok, detail, new_st = res
                self._render_status(new_st)
                if ok:
                    messagebox.showinfo("OK", self.t("msg_done_install_reboot") if reboot else self.t("msg_done_install_only"))
                else:
                    messagebox.showerror("Install Failed", detail)

            self._run_async(task, done)

        def _do_uninstall(self) -> None:
            if not self._target_ready():
                return
            if not messagebox.askyesno(self.t("confirm_uninstall_title"), self.t("confirm_uninstall_msg")):
                return

            selected_serial = self.status.serial
            force = self.force_var.get()
            proof = self.selected_json if self.is_custom_json else None

            def task():
                ok, detail = self.client.uninstall(reboot=True, serial=selected_serial, force=force, legacy_json=proof)
                return ok, detail, self.client.probe(selected_serial)

            def done(res):
                ok, detail, new_st = res
                self._render_status(new_st)
                if ok:
                    key = "msg_no_install" if detail.startswith("UNCHANGED:") else "msg_done_uninstall"
                    messagebox.showinfo("OK", self.t(key))
                else:
                    messagebox.showerror("Uninstall Failed", detail)

            self._run_async(task, done)

        def _do_mode(self, mode: int) -> None:
            if self.busy:
                return
            if not self._target_ready():
                return

            selected_serial = self.status.serial
            force = self.force_var.get()

            def task():
                ok, detail = self.client.set_mode(mode, serial=selected_serial, force=force)
                return ok, detail, self.client.probe(selected_serial)

            def done(res):
                ok, detail, new_st = res
                self._render_status(new_st)
                if not ok:
                    messagebox.showerror("Error", detail)

            self._run_async(task, done)

    app = App()
    app.mainloop()
    return 0


# =============================================================================
# CLI Entrypoint
# =============================================================================
def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="fit8e_color_tool",
        description="KONKR Pocket FIT Elite (SM8750P) Hardware Color Calibration Tool",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("-s", "--serial", help="ADB serial (also accepts ANDROID_SERIAL)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Inspect connected handheld and calibration status")

    p_inst = sub.add_parser("install", help="Install hardware calibration JSON (Zero-Root)")
    p_inst.add_argument("--json", type=Path, default=None, help="Path to custom QDCM V3 JSON")
    p_inst.add_argument("--cct", type=int, default=6500, help="Target whitepoint CCT in Kelvin (5500..7500, default: 6500)")
    p_inst.add_argument("--tint", type=int, default=0, help="Green/Magenta tint (-10..+10, default: 0)")
    p_inst.add_argument("--no-reboot", action="store_true", help="Do not reboot automatically")
    p_inst.add_argument("--force", action="store_true", help="Confirm a verified ar14 panel if automatic identification is inconclusive")

    p_exp = sub.add_parser("export", help="Compile & export a hardware QDCM JSON for a custom CCT/tint")
    p_exp.add_argument("--cct", type=int, default=6500, help="Target CCT (5500..7500, default: 6500)")
    p_exp.add_argument("--tint", type=int, default=0, help="Tint (-10..+10, default: 0)")
    p_exp.add_argument("--out", type=Path, required=True, help="Output JSON path")

    p_un = sub.add_parser("uninstall", help="Remove calibration JSON and restore stock")
    p_un.add_argument("--no-reboot", action="store_true", help="Do not reboot automatically")

    p_mode = sub.add_parser("mode", help="Switch live color mode (0=Natural, 1=Boosted, 2=Vivid)")
    p_mode.add_argument("value", type=int, choices=[0, 1, 2])

    p_un.add_argument("--legacy-json", type=Path, help="Exact profile used for an untracked legacy installation")
    for command in (p_un, p_mode):
        command.add_argument("--force", action="store_true", help="Confirm a verified ar14 panel if automatic identification is inconclusive")
    args = parser.parse_args(argv)
    if args.cmd in ("export", "install"):
        if not 5500 <= args.cct <= 7500 or not -10 <= args.tint <= 10:
            parser.error("CCT must be 5500..7500 K and tint -10..10")
        if args.cmd == "install" and args.json and (args.cct != 6500 or args.tint != 0):
            parser.error("custom JSON already contains its whitepoint; omit --cct and --tint")
    if not args.cmd:
        return launch_gui(args.serial)

    if args.cmd == "export":
        base = find_default_json()
        if not base:
            print(f"ERROR: Could not find default {PANEL_FILENAME}", file=sys.stderr)
            return 2
        raw, prev = color_engine.synthesize_qdcm_bytes(base, cct=args.cct, tint=args.tint)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(raw)
        print(f"Exported {args.cct}K / Tint {args.tint:+d} (peak {prev['brightness_pct']}%) -> {args.out} ({len(raw)} bytes)")
        return 0

    client = AdbClient(log_fn=print, serial=args.serial)
    if not client.adb:
        print("ERROR: adb not found. Put platform-tools alongside this tool or in PATH.", file=sys.stderr)
        return 2

    if args.cmd == "status":
        st = client.probe()
        print(f"ADB:             {st.adb_path}")
        print(f"State:           {st.state} ({st.serial})")
        if st.error:
            print(st.error, file=sys.stderr)
        if st.state == "device":
            print(f"Device:          {st.model} ({st.device} / {st.product})")
            print(f"SoC / Panel:     {st.soc} ({st.platform_board}) / {st.panel_mode}")
            evidence = " ".join(value for value in (st.panel_source, st.panel_timing) if value)
            print(f"Panel check:     {st.panel_state} ({evidence or 'unknown'})")
            print(f"Build:           {st.build_id}")
            print(f"Target Match:    {st.is_target_device}")
            print(f"Installed JSON:  {st.installed_data} (MD5={st.installed_data_md5 or '-'})")
            print(f"Color Mgmt:      {st.sf_color_setting} | Active: {st.sf_active_mode}")
            print(f"Modes Exposed:   {', '.join(st.sf_color_modes)}")
            print(f"Sys Color Mode:  {st.sys_color_mode}")
        return 0 if st.state == "device" else 1

    if args.cmd == "install":
        jpath = args.json or find_default_json()
        if not jpath:
            print(f"ERROR: Could not find default {PANEL_FILENAME}", file=sys.stderr)
            return 2
        cct = None if args.json else args.cct
        tint = 0 if args.json else args.tint
        ok, msg = client.install(jpath, force=args.force, reboot=not args.no_reboot, cct=cct, tint=tint)
        print("SUCCESS:" if ok else "FAILED:", msg)
        return 0 if ok else 1

    if args.cmd == "uninstall":
        ok, msg = client.uninstall(reboot=not args.no_reboot, force=args.force, legacy_json=args.legacy_json)
        print("SUCCESS:" if ok else "FAILED:", msg)
        return 0 if ok else 1

    if args.cmd == "mode":
        ok, msg = client.set_mode(args.value, force=args.force)
        print("SUCCESS:" if ok else "FAILED:", msg)
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
