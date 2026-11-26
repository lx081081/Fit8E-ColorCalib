"""Offline regression tests. Device writes are confined to a temporary directory."""
import contextlib
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import build_qdcm_v3 as compiler
import color_engine
import fit8e_color_tool as app

ASSET = ROOT / "assets" / app.PANEL_FILENAME
HELPER = ROOT / "scripts" / "fit8e_calib_shizuku.sh"
MEAS = ROOT / "reference" / "measurements"
NATIVE = MEAS / "fit8e_native_pfs_profile.ti3"
VERIFY = MEAS / "fit8e_verify51_pfs.ti3"
OLD_TEST = ROOT / "reference" / "qdcm_ar14_passthrough_test.json"


class ProfileTests(unittest.TestCase):
    def test_whitepoint_exports(self):
        for cct, tint in ((5500, -10), (6500, 0), (6800, -1), (7500, 10)):
            raw, _ = color_engine.synthesize_qdcm_bytes(ASSET, cct, tint)
            color_engine.validate_qdcm(raw)
            if (cct, tint) == (6500, 0):
                self.assertEqual(raw, ASSET.read_bytes())

    def test_application_version_and_builder_must_agree(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as done, contextlib.redirect_stdout(output):
            app.run_cli(["--version"])
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"fit8e_color_tool {app.APP_VERSION}")
        for strings in app.STRINGS.values():
            self.assertIn(f"v{app.APP_VERSION}", strings["win_title"])
        wrong = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_windows_release.py"),
                                "--version", "0.0.0-test"], capture_output=True)
        self.assertEqual(wrong.returncode, 2)
        self.assertIn(app.APP_VERSION.encode(), wrong.stderr)

    def test_invalid_cli_arguments_do_not_produce_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            for args in (["export", "--out", str(out), "--cct", "9000"],
                         ["install", "--tint", "11"]):
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                    app.run_cli(args)
                self.assertEqual(caught.exception.code, 2)
            self.assertFalse(out.exists())

    def test_ti3_names_extra_table_and_missing_verification(self):
        lines = NATIVE.read_text().splitlines()
        col = lines.index("BEGIN_DATA_FORMAT") + 1
        fields = lines[col].split()
        ix, iz = fields.index("XYZ_X"), fields.index("XYZ_Z")
        fields[ix], fields[iz] = fields[iz], fields[ix]
        lines[col] = " ".join(fields)
        for i in range(lines.index("BEGIN_DATA") + 1, lines.index("END_DATA")):
            cells = lines[i].split()
            cells[ix], cells[iz] = cells[iz], cells[ix]
            cells[0] = f'"patch {i}"'
            lines[i] = " ".join(cells)
        lines += ["CAL", "BEGIN_DATA_FORMAT", "RGB_I RGB_R RGB_G RGB_B", "END_DATA_FORMAT",
                  "NUMBER_OF_SETS 2", "BEGIN_DATA", "0 0 0 0", "1 1 1 1", "END_DATA"]
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as log:
            alt, first, second = (Path(tmp) / n for n in ("alt.ti3", "first.json", "second.json"))
            alt.write_text("\n".join(lines), encoding="utf-8")
            self.assertEqual(compiler.load_ti3(alt), compiler.load_ti3(NATIVE))
            compiler.generate_qdcm_v3(NATIVE, ASSET, first, verify_ti3=VERIFY)
            compiler.generate_qdcm_v3(alt, ASSET, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertIn("Simulated sRGB", log.getvalue())
            color_engine.validate_qdcm(first.read_bytes())
            original = first.read_bytes()
            cli = subprocess.run([sys.executable, "-B", str(ROOT / "src/build_qdcm_v3.py"), "--ti3", str(NATIVE),
                                  "--out", str(first), "--verify-ti3", str(Path(tmp) / "missing.ti3")],
                                 capture_output=True)
            self.assertNotEqual(cli.returncode, 0)
            self.assertIn(b"missing.ti3", cli.stderr)
            self.assertEqual(first.read_bytes(), original)

    def test_documented_measurement_baseline(self):
        for name, avg, maximum, count in (
                (VERIFY, 3.7680, 8.3919, 37), (MEAS / "fit8e_cal_final_mode0_srgb51.ti3", .4627, 1.1119, 0)):
            errors = []
            for row in compiler.load_ti3(name):
                target = compiler.mat_vec(compiler.M_SRGB, [compiler.srgb_eotf(x/100) for x in row[1:4]])
                errors.append(compiler.ciede2000(compiler.xyz_to_lab(target, compiler.D65),
                                                 compiler.xyz_to_lab(row[4:7], compiler.D65)))
            self.assertEqual(len(errors), 51)
            self.assertAlmostEqual(sum(errors)/51, avg, places=4)
            self.assertAlmostEqual(max(errors), maximum, places=4)
            self.assertEqual(sum(v >= 2 for v in errors), count)


class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("sh") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe")
        if not Path(cls.shell).is_file():
            raise unittest.SkipTest("POSIX shell required (Git Bash on Windows)")
        cls.adjusted = color_engine.synthesize_qdcm_bytes(ASSET, 6800, -1)[0]

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="fit8e-review-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.display = self.tmp / "display"
        self.display.mkdir()
        self.target = self.display / app.PANEL_FILENAME
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.mode = self.tmp / "mode"
        self.mode.write_text("2")
        self.custom = self.tmp / "custom.json"
        self.custom.write_bytes(self.adjusted)
        self.env = dict(os.environ, FIT_TEST_DIR=self.tmp.as_posix(), TEST_SOC="SM8750P", TEST_DEVICE="PocketFITElite",
                        TEST_PANEL=color_engine.PANEL_KEY)
        self.script = HELPER.read_bytes().replace(b"\r\n", b"\n")
        self.isolated = self.script.replace(b"display_dir=/data/vendor/display", b'display_dir="$FIT_TEST_DIR/display"')
        self.isolated = self.isolated.replace(b"/sys/class/", b'"$FIT_TEST_DIR/sys"/class/')
        self.assertNotIn(b"display_dir=/data/", self.isolated)
        self.stub("getprop", '''case "$1" in
ro.product.device|ro.product.name) echo "$TEST_DEVICE";;
ro.product.model) echo "Test handheld";;
ro.soc.model) echo "$TEST_SOC";;
ro.board.platform) echo sun;;
vendor.display.primary_panel) echo "$TEST_PANEL";;
esac''')
        self.stub("settings", '''case "$1" in
get) cat "$FIT_TEST_DIR/mode";;
put) [ -z "$FAIL_SETTING" ] || exit 1; echo "$4" > "$FIT_TEST_DIR/mode";;
delete) [ -z "$FAIL_SETTING" ] || exit 1; echo null > "$FIT_TEST_DIR/mode";;
esac''')
        self.stub("dumpsys", "echo 'DisplayColorSetting: Managed'")
        self.stub("id", 'if [ -n "$TEST_ROOT" ]; then echo 0; else echo 2000; fi')
        self.stub("chown", 'echo chown >> "$FIT_TEST_DIR/root-attributes"')
        self.stub("chcon", 'echo chcon >> "$FIT_TEST_DIR/root-attributes"')
        self.stub("su", "exit 1")  # Allows the host to discover the fallback. No sudo/su is executed.
        if os.name == "nt":
            # Git Bash's Perl binds the MSYS flock implementation. Use the inherited
            # descriptor just as Android toybox does, including non-blocking contention.
            self.stub("flock", 'use Fcntl qw(LOCK_EX LOCK_NB); open(my $fd,"<&=9") or die $!;\n'
                      'flock($fd, LOCK_EX|LOCK_NB) or exit 1;', shebang="#!/usr/bin/perl")
        self.serials = ["HANDHELD"]
        self.bad_probe = self.corrupt = self.fail_reboot = self.deny_shell = False
        self.reboots = []
        self.commands = []
        self.root_calls = 0
        for mock in (patch.object(app, "find_adb", return_value="test-adb"),
                     patch.object(app, "_run_subproc", side_effect=self.transport),
                     patch.dict(os.environ, {"ANDROID_SERIAL": ""})):
            mock.start()
            self.addCleanup(mock.stop)
        self.client = app.AdbClient()

    def stub(self, name, body, shebang="#!/bin/sh"):
        p = self.bindir / name
        p.write_bytes((shebang + "\n" + body + "\n").encode())
        p.chmod(0o755)

    def bash(self, body, *args, stdin=None, env=None):
        prepare = 'FIT_TEST_DIR=$(cygpath -u "$FIT_TEST_DIR"); export FIT_TEST_DIR; ' if os.name == "nt" else ""
        prepare += 'export PATH="$FIT_TEST_DIR/bin:/usr/bin:/bin:$PATH"; '
        return subprocess.run([self.shell, "-c", prepare + body,
                               "review", *args], input=stdin, capture_output=True, env=env or self.env, timeout=45)

    def device(self, *args, env=None):
        return self.bash('exec sh -s -- "$@"', *map(str,args), stdin=self.isolated, env=env)

    def transport(self, cmd, timeout, stdin=None):
        def reply(rc=0, out="", err=""):
            return subprocess.CompletedProcess(cmd, rc, out, err)
        if cmd[1:] == ["devices"]:
            return reply(out="List of devices attached\n"+"".join(s+"\tdevice\n" for s in self.serials))
        self.assertEqual(cmd[1], "-s")
        serial, args = cmd[2], cmd[3:]
        self.commands.append((serial, args))
        if serial not in self.serials:
            return reply(1, err="disconnected")
        if args[0] == "push":
            local = self.tmp / Path(args[-1]).name
            local.write_bytes(b"truncated" if self.corrupt else Path(args[1]).read_bytes())
        elif args == ["reboot"]:
            self.reboots.append(serial)
            if self.fail_reboot:
                return reply(1, err="transport unavailable")
        elif args[:2] == ["shell", "-T"]:
            self.assertEqual(stdin, self.script)
            call = shlex.split(args[2])
            as_root = call[0] in ("su","xsu")
            if as_root:
                self.root_calls += 1
                call = shlex.split(call[2])
            self.assertEqual(call[:3], ["sh", "-s", "--"])
            op, rest = call[3], call[4:]
            if op == "probe" and self.bad_probe:
                return reply(1, err="probe failed")
            if op == "install" and self.deny_shell and not as_root:
                return reply(73, err="cannot stage")
            rest = [(self.tmp / Path(v).name).as_posix() if v.startswith("/data/local/tmp/") else v for v in rest]
            env = self.env.copy()
            if as_root:
                env["TEST_ROOT"] = "1"
            if serial == "OTHER":
                env["TEST_DEVICE"] = "other"
            p = self.device(op, *rest, env=env)
            return reply(p.returncode, p.stdout.decode(), p.stderr.decode())
        elif args[0] == "shell" and args[1].startswith("mktemp "):
            return reply(out="/data/local/tmp/fit8e-qdcm.abcdef12\n")
        elif args[0] == "shell" and args[1].startswith("rm -f /data/local/tmp/fit8e-qdcm."):
            (self.tmp / Path(args[1]).name).unlink(missing_ok=True)
        else:
            self.fail(f"Unexpected ADB command {args}")
        return reply()

    def test_round_trip_preserves_first_file_and_mode_across_updates(self):
        self.target.write_bytes(b"pre-existing OEM configuration")
        self.assertTrue(self.client.install(ASSET, reboot=False)[0])
        self.assertEqual(self.target.read_bytes(), ASSET.read_bytes())
        self.assertTrue(self.client.install(self.custom, reboot=False)[0])
        self.assertEqual(self.target.read_bytes(), self.adjusted)
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertEqual(self.target.read_bytes(), b"pre-existing OEM configuration")
        self.assertEqual(self.mode.read_text().strip(), "2")
        self.assertEqual(list(self.display.iterdir()), [self.target])

    def test_untracked_and_persist_files_are_preserved(self):
        persistent = self.tmp / "persist" / app.PANEL_FILENAME
        persistent.parent.mkdir()
        persistent.write_bytes(b"factory")
        self.assertNotIn(b"/mnt/vendor/persist", self.script)
        self.assertTrue(self.client.uninstall()[0])
        self.assertFalse(self.reboots)
        self.target.write_bytes(b"unknown")
        self.assertFalse(self.client.uninstall()[0])
        self.assertEqual(self.target.read_bytes(), b"unknown")
        self.assertEqual(persistent.read_bytes(), b"factory")
        self.assertEqual(self.mode.read_text().strip(), "2")

    def test_device_identity_and_scoped_force(self):
        for changes in ({"TEST_DEVICE":"other"}, {"TEST_SOC":"other"}, {"TEST_PANEL":"ar140"}):
            with patch.dict(self.env,changes):
                for op in (["install",self.custom.as_posix()],["mode","0"],["uninstall"]):
                    p=self.device(*op,"--force")
                    self.assertEqual(p.returncode,2,p.stderr)
                self.assertFalse(self.client.uninstall(force=True)[0])
        with patch.dict(self.env,{"TEST_PANEL":""}):
            self.assertFalse(self.client.set_mode(1)[0])
            self.assertTrue(self.client.set_mode(1,force=True)[0])
        self.assertFalse(self.reboots)

    @staticmethod
    def panel_edid(text=b"ar14 1080p 14", tag=0xfe, offset=108, serial=1):
        block = bytearray(128)
        block[:8] = bytes.fromhex("00ffffffffffff00")
        block[12:16] = serial.to_bytes(4, "little")
        block[18:20] = bytes((1, 3))
        block[offset + 3] = tag
        block[offset + 5:offset + 18] = text.ljust(13, b" ")
        block[127] = -sum(block) % 256
        return block

    def connector(self, name="card0-DSI-1"):
        node = self.tmp / "sys/class/drm" / name
        node.mkdir(parents=True, exist_ok=True)
        (node / "status").write_bytes(b"connected\n")
        (node / "modes").write_bytes(b"1080x1920x144vid\n1080x1920x60vid\n")
        return node

    def test_edid_identifies_the_internal_ar14_and_144hz(self):
        self.env["TEST_PANEL"] = ""
        node = self.connector()
        for tag, offset, serial in ((0xfe, 108, 5), (0xfc, 54, 123456)):
            with self.subTest(tag=tag):
                (node / "edid").write_bytes(self.panel_edid(tag=tag, offset=offset, serial=serial))
                status = self.client.probe()
                self.assertEqual((status.panel_mode, status.panel_source, status.panel_timing),
                                 ("ar14 1080p 14", "DSI-1/edid", "1080x1920x144vid"))
                self.assertTrue(status.is_target_device)
                self.assertEqual(self.client.target_error(status, False), "")
        self.assertIn(b"Target match: yes", self.device("status").stdout)
        self.assertTrue(self.client.set_mode(1)[0])

    def test_incomplete_edid_evidence_requires_confirmation(self):
        self.env["TEST_PANEL"] = ""
        node = self.connector()
        valid = self.panel_edid()
        bad_checksum = valid.copy()
        bad_checksum[10] ^= 1
        bad_header = valid.copy()
        bad_header[0] = 1
        bad_header[127] = (bad_header[127] - 1) % 256
        for detail, data, timing in (
            ("bad checksum", bad_checksum, b"1080x1920x144vid"),
            ("bad header", bad_header, b"1080x1920x144vid"),
            ("short EDID", valid[:-1], b"1080x1920x144vid"),
            ("serial descriptor", self.panel_edid(tag=0xff), b"1080x1920x144vid"),
            ("generic EDID text", self.panel_edid(text=b"Vendor text"), b"1080x1920x144vid"),
            ("no EDID", b"", b"1080x1920x144vid"),
            ("144 Hz missing", valid, b"1080x1920x60vid"),
        ):
            with self.subTest(detail=detail):
                (node / "modes").write_bytes(timing + b"\n")
                (node / "edid").write_bytes(data)
                status = self.client.probe()
                self.assertTrue(status.hardware_ok)
                self.assertEqual(status.target_issue, "st_panel_unknown")
                self.assertFalse(status.is_target_device)
                self.assertTrue(self.client.target_error(status, False))
        self.assertEqual(self.device("mode", "0").returncode, 2)
        self.assertEqual(self.mode.read_text().strip(), "2")

    def test_edid_cannot_override_wrong_hardware_panel_or_connector(self):
        self.env["TEST_PANEL"] = ""
        internal = self.connector()
        external = self.connector("card0-HDMI-A-1")
        (external / "edid").write_bytes(self.panel_edid())
        (internal / "edid").write_bytes(self.panel_edid())
        (internal / "status").write_bytes(b"disconnected\n")
        self.assertEqual(self.client.probe().target_issue, "st_panel_unknown")
        (internal / "status").write_bytes(b"connected\n")
        for change, issue in (({"TEST_PANEL": "ar15"}, "st_panel_mismatch"),
                              ({"TEST_SOC": "other"}, "st_wrong_soc"),
                              ({"TEST_DEVICE": "other"}, "st_wrong_model")):
            with self.subTest(change=change), patch.dict(self.env, change):
                status = self.client.probe()
                self.assertEqual(status.target_issue, issue)
                self.assertTrue(self.client.target_error(status, True))
                self.assertEqual(self.device("mode", "0", "--force").returncode, 2)
        for wrong in (b"ar140", b"ar15 1080p 14"):
            with self.subTest(wrong=wrong):
                (internal / "edid").write_bytes(self.panel_edid(text=wrong))
                self.assertEqual(self.client.probe().target_issue, "st_panel_mismatch")
                self.assertEqual(self.device("mode", "0", "--force").returncode, 2)
        self.assertEqual(self.mode.read_text().strip(), "2")

    def test_target_serial_stays_pinned_and_probe_failure_is_not_ready(self):
        self.serials=["OTHER","HANDHELD"]
        self.assertEqual(self.client.probe().serial,"HANDHELD")
        self.assertTrue(self.client.install(ASSET,serial="HANDHELD")[0])
        self.assertEqual(self.reboots,["HANDHELD"])
        self.serials=["OTHER"]
        calls=len(self.commands)
        self.assertFalse(self.client.uninstall(serial="HANDHELD")[0])
        self.assertEqual(len(self.commands),calls)
        self.serials=["HANDHELD","SECOND"]
        self.assertEqual(self.client.probe().state,"ambiguous")
        self.bad_probe=True
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(app.run_cli(["--serial","HANDHELD","status"]),0)

    def test_transfer_failure_preserves_live_file(self):
        self.target.write_bytes(b"original")
        self.corrupt=True
        ok,msg=self.client.install(ASSET)
        self.assertFalse(ok)
        self.assertIn("sha-256 mismatch", msg.lower())
        self.assertEqual(self.target.read_bytes(),b"original")
        self.assertFalse(self.reboots)

    def test_content_checks_apply_to_both_entry_points(self):
        for p in (OLD_TEST,):
            self.assertFalse(self.client.install(p)[0])
            self.assertFalse(self.commands)
            self.assertNotEqual(self.device("install",p.as_posix()).returncode,0)
        for change in ("truncated","out_of_range","unbound","wrong_panel"):
            data=json.loads(ASSET.read_bytes())
            if change=="wrong_panel":
                data["other"]=data.pop(color_engine.PANEL_KEY)
            elif change=="unbound":
                mode=data[color_engine.PANEL_KEY]["P3"]
                block=color_engine._dec_raw(mode["PostBlendGamut"])
                block["Custom"]=9
                mode["PostBlendGamut"]=color_engine._enc_obj(block)
            else:
                block=color_engine._dec_raw(data["Common"]["PostBlendGC"])
                values=block["Predefined"]["Rec601noDither"].rstrip(",").split(",")
                if change=="truncated": values.pop()
                else: values[1]="1024"
                block["Predefined"]["Rec601noDither"]=",".join(values)+","
                data["Common"]["PostBlendGC"]=color_engine._enc_obj(block)
            raw=json.dumps(data).encode()
            with self.assertRaises(ValueError): color_engine.validate_qdcm(raw)
            self.custom.write_bytes(raw)
            self.assertNotEqual(self.device("validate",self.custom.as_posix()).returncode,0)
        self.assertFalse(self.target.exists())

    def test_mode_and_reboot_failure_and_uninstall_retry(self):
        self.env["FAIL_SETTING"]="1"
        ok,detail=self.client.install(ASSET)
        self.assertFalse(ok)
        self.assertIn("Profile installed",detail)
        self.assertTrue(self.target.exists())
        self.assertFalse(self.reboots)
        self.assertFalse(self.client.set_mode(0)[0])
        self.assertFalse(self.client.uninstall()[0])
        self.assertFalse(self.target.exists())
        del self.env["FAIL_SETTING"]
        self.fail_reboot=True
        ok,detail=self.client.uninstall()
        self.assertFalse(ok)
        self.assertIn("Reboot request failed",detail)
        self.assertEqual(self.mode.read_text().strip(),"2")

    def test_legacy_and_external_changes(self):
        self.target.write_bytes(self.adjusted)
        self.assertFalse(self.client.uninstall(reboot=False,legacy_json=ASSET)[0])
        self.assertTrue(self.client.uninstall(reboot=False,legacy_json=self.custom)[0])
        self.assertFalse(self.target.exists())
        self.target.write_bytes(ASSET.read_bytes())
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertTrue(self.client.install(ASSET,reboot=False)[0])
        self.target.write_bytes(b"edited later")
        self.assertFalse(self.client.uninstall()[0])
        self.assertEqual(self.target.read_bytes(),b"edited later")

    def test_controlled_privilege_retry_and_absent_initial_setting(self):
        self.mode.write_text("null")
        self.deny_shell=True
        self.assertTrue(self.client.install(ASSET,reboot=False)[0])
        self.assertEqual(self.root_calls,1)
        self.assertEqual((self.tmp / "root-attributes").read_text().splitlines(), ["chown", "chcon"])
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertEqual(self.mode.read_text().strip(),"null")

    def test_shizuku_staging_failure_and_interrupted_commit_are_recoverable(self):
        self.target.write_bytes(b"original profile")
        self.stub("cp", '''for dest do :; done
case "$dest" in
    */.fit8e-stage.*) if [ -n "$FAIL_COPY" ]; then echo truncated > "$dest"; exit 0; fi ;;
esac
exec /usr/bin/cp "$@"''')
        with patch.dict(self.env, {"FAIL_COPY":"1"}):
            failed = self.device("install", self.custom.as_posix())
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(b"checksum", failed.stderr)
        self.assertEqual(self.target.read_bytes(), b"original profile")
        self.stub("mv", '''for dest do :; done
case "$dest" in
    */qdcm_calib_data_*.json) if [ -n "$FAIL_RENAME" ]; then exit 1; fi ;;
esac
exec /usr/bin/mv "$@"''')
        with patch.dict(self.env, {"FAIL_RENAME":"1"}):
            failed = self.device("install", self.custom.as_posix())
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"original profile")
        self.assertEqual(self.device("install", self.custom.as_posix()).returncode, 0)
        self.assertEqual(self.target.read_bytes(), self.adjusted)
        self.assertEqual(self.device("uninstall").returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"original profile")

    def test_original_backup_damage_prevents_uninstall(self):
        self.target.write_bytes(b"original")
        self.assertTrue(self.client.install(ASSET, reboot=False)[0])
        (self.display / ".fit8e-colorcalib/original").write_bytes(b"incomplete")
        self.assertFalse(self.client.uninstall()[0])
        self.assertEqual(self.target.read_bytes(), ASSET.read_bytes())
        self.assertFalse(self.reboots)

    def test_busy_lock_prevents_writes(self):
        script=self.tmp/"helper.sh"
        script.write_bytes(self.isolated)
        p=self.bash('(flock -nx 9 || exit 90; sh "$1" install "$2") 9<"$FIT_TEST_DIR/display"',
                    script.as_posix(), ASSET.as_posix())
        self.assertEqual(p.returncode,5,p.stderr)
        self.assertFalse(self.target.exists())



class GuiTests(unittest.TestCase):
    def test_failed_actions_display_the_probed_device(self):
        try:
            import tkinter as tk
            from tkinter import messagebox
        except ImportError:
            self.skipTest("Tk unavailable")
        if os.name != "nt" and not os.environ.get("DISPLAY"):
            self.skipTest("No desktop display")
        native_init = tk.Tk.__init__
        def hidden(window, *args, **kwargs):
            native_init(window, *args, **kwargs)
            window.withdraw()
        ready = app.DeviceStatus(adb_path="simulated", state="device", serial="VERIFIED", model="Fit 8E",
                                 model_ok=True, hardware_ok=True, panel_state="match")
        def exercise(window):
            try:
                window.update_idletasks()
                for changes, label in (({"panel_state": "unknown"}, "Fit 8E · 面板待确认"),
                                       ({"panel_state": "mismatch"}, "Fit 8E · 面板不匹配"),
                                       ({"hardware_ok": False}, "芯片型号不匹配"),
                                       ({"model_ok": False, "hardware_ok": False}, "机型不匹配")):
                    window._render_status(replace(ready, **changes))
                    self.assertEqual(window.status_pill.cget("text"), label)
                    with patch.object(messagebox, "showerror") as blocked:
                        self.assertFalse(window._target_ready())
                        self.assertIn(label, blocked.call_args.args[1])
                window._render_status(ready)
                window._run_async = lambda task, done: done(task())
                with patch.object(window.client, "probe", return_value=ready) as probe, \
                     patch.object(window.client, "install", return_value=(False, "Reboot request failed")) as install, \
                     patch.object(window.client, "uninstall", return_value=(False, "Mode restore failed")) as uninstall, \
                     patch.object(messagebox, "askyesno", return_value=True), \
                     patch.object(messagebox, "showerror") as error, patch.object(messagebox, "showinfo") as info:
                    window._do_install(reboot=True)
                    window._do_uninstall()
                    self.assertEqual(install.call_args.kwargs["serial"], "VERIFIED")
                    self.assertEqual(uninstall.call_args.kwargs["serial"], "VERIFIED")
                    self.assertEqual([c.args for c in probe.call_args_list], [("VERIFIED",), ("VERIFIED",)])
                    self.assertIs(window.status, ready)
                    self.assertEqual(error.call_count, 2)
                    info.assert_not_called()
                window._toggle_lang()
                window._select_preset(6800)
                self.assertEqual(window.cct_var.get(), 6800)
            finally:
                window.destroy()
        with patch.object(tk.Tk, "__init__", hidden), patch.object(tk.Tk, "mainloop", exercise), \
             patch.object(app, "find_adb", return_value=None), \
             patch.object(app, "_run_subproc", side_effect=AssertionError("Unexpected real device command")):
            self.assertEqual(app.run_cli([]), 0)


if __name__ == "__main__":
    unittest.main()
