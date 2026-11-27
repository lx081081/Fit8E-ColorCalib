"""Offline regression tests. Device writes are confined to a temporary directory."""
import contextlib
from dataclasses import replace
import hashlib
import io
import json
import math
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
import root_script

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


    def test_cie2015_daylight_against_spectral_integration(self):
        # Independent 1 nm integrations of the CIE daylight basis spectra with
        # CIE 170-2 Table 10.7a. The tabulated-D65 anchor moves xy by < 3e-7.
        reference = {
            5500: (.334442810, .349582251), 6000: (.322946301, .339580073),
            6500: (.313452449, .330801828), 6800: (.308523039, .326065786),
            7000: (.305527369, .323122512), 7100: (.304090675, .321696657),
            7500: (.298837184, .316394367),
        }
        for cct, expected in reference.items():
            xyz, actual = color_engine.target_whitepoint_xyz(cct)
            self.assertEqual(xyz[1], 100)
            self.assertLess(max(abs(x-y) for x,y in zip(expected, actual)), 3e-7)
            center = color_engine._xy_to_uv60(*actual)
            for tint in (-10, 1, 10):
                _, colored = color_engine.target_whitepoint_xyz(cct, tint)
                u, v = color_engine._xy_to_uv60(*colored)
                self.assertAlmostEqual(math.hypot(u-center[0], v-center[1]), abs(tint)*.0005)
                self.assertGreater(tint*(v-center[1]), 0)

    def test_measured_d65_matches_dynamic_model_and_modes(self):
        measured = ASSET.read_bytes()
        self.assertEqual(hashlib.sha256(measured).hexdigest(), color_engine.D65_PROFILE_SHA256)
        self.assertEqual(color_engine.synthesize_qdcm_bytes(ASSET, 6500, 0)[0], measured)
        doc = json.loads(measured)
        tables = color_engine._dec_raw(doc["Common"]["PostBlendGamut"])["Custom"]
        white = color_engine.D65_XYZ
        equivalent = color_engine._equivalent_1931(white)
        rgb = color_engine._mat_vec(color_engine.M_PANEL_INV, white)
        native = [[v/max(rgb) if i==j else 0. for j in range(3)] for i,v in enumerate(rgb)]
        mappings = [native, color_engine._content_map(compiler.M_P3, equivalent)[0],
                    color_engine._content_map(compiler.M_SRGB, equivalent)[0]]
        for slot, mapping in enumerate(mappings):
            self.assertEqual(color_engine._build_gamut_csv(mapping), tables[str(slot)])
        # Mode 3 Adaptive Vibrant slots ("3"=P3_Enhance, "4"=sRGB_Enhance) must match dynamic model
        self.assertEqual(
            color_engine._build_adaptive_gamut_csv(mappings[1], color_engine._transform_p3_enhance_to_p3),
            tables["3"],
        )
        self.assertEqual(
            color_engine._build_adaptive_gamut_csv(mappings[1], color_engine._transform_srgb_enhance_to_p3),
            tables["4"],
        )
        # Neutral axis (R=G=B) must be bit-identical between Mode 0 P3 and Mode 3 Enhance LUTs
        p3_nums = tables["1"].rstrip(",").split(",")
        for enh_slot in ("3", "4"):
            enh_nums = tables[enh_slot].rstrip(",").split(",")
            for i in range(17):
                idx = (i * 289 + i * 17 + i) * 3
                self.assertEqual(enh_nums[idx:idx + 3], p3_nums[idx:idx + 3])
        for cct, tint in ((6450,0),(6500,1),(6000,0),(6800,0),(7100,0),(7500,-10)):
            raw, preview = color_engine.synthesize_qdcm_bytes(ASSET,cct,tint)
            self.assertNotEqual(raw, measured)
            color_engine.validate_qdcm(raw)
            panel = json.loads(raw)[color_engine.PANEL_KEY]
            self.assertEqual(panel["HDR"]["PostBlendGamut"], panel["P3"]["PostBlendGamut"])
            self.assertEqual(panel["HDR_Enhance"]["PostBlendGamut"], panel["P3_Enhance"]["PostBlendGamut"])
            self.assertEqual(panel["HDR_Enhance"]["PostBlendHdrBlob"], panel["HDR"]["PostBlendHdrBlob"])
            for em in color_engine.ENHANCE_MODES:
                self.assertEqual(panel[em]["Applicability"]["RenderIntent"], 2)
            self.assertEqual(preview, color_engine.estimate_whitepoint_preview(cct,tint))
            self.assertEqual(preview["xy"], color_engine.target_whitepoint_xyz(cct,tint)[1])

    def test_cct_and_tint_consistency_across_all_modes(self):
        panel = color_engine._PANEL
        m_rel = color_engine._relative_matrix(panel)
        k_rel = [v * 100.0 / panel.white[1] for v in panel.black]
        _, base65_xy = color_engine.target_whitepoint_xyz(6500, 0)
        _, base65_v60 = color_engine._xy_to_uv60(*base65_xy)

        def white_2015_xy(igc, gc, nums):
            # Evaluate pure white node (16,16,16) through HPM GC -> panel CIE 2015 2° XYZ
            idx = (16 * 289 + 16 * 17 + 16) * 3
            u = [nums[idx + c] / 4095.0 for c in range(3)]
            v_enc = []
            for uc in u:
                p = uc * 1023.0
                i0 = min(1022, int(p))
                f = p - i0
                v_enc.append((gc[i0] * (1.0 - f) + gc[i0 + 1] * f) / 1023.0)
            lin = [compiler.pchip_interp(panel.levels, panel.channel_curves[c], [v_enc[c]])[0] for c in range(3)]
            xyz = [color_engine._mat_vec(m_rel, lin)[i] + k_rel[i] for i in range(3)]
            s = sum(xyz)
            return xyz[0] / s, xyz[1] / s

        def duv76(xy1, xy2):
            def uv(x, y):
                d = -2.0 * x + 12.0 * y + 3.0
                return 4.0 * x / d, 9.0 * y / d
            u1, v1 = uv(*xy1)
            u2, v2 = uv(*xy2)
            return math.hypot(u1 - u2, v1 - v2)

        for cct, tint in ((5500, -10), (5500, 0), (6500, -10), (6500, 10), (7500, 0), (7500, 10)):
            raw, _ = color_engine.synthesize_qdcm_bytes(ASSET, cct, tint)
            doc = json.loads(raw)
            igc = [int(v) for v in color_engine._dec_raw(doc["Common"]["PostBlendIGC"])["Predefined"]["Rec601"].rstrip(",").split(",")]
            gc = [int(v) for v in color_engine._dec_raw(doc["Common"]["PostBlendGC"])["Predefined"]["Rec601noDither"].rstrip(",").split(",")]
            custom = color_engine._dec_raw(doc["Common"]["PostBlendGamut"])["Custom"]
            _, tgt_xy = color_engine.target_whitepoint_xyz(cct, tint)
            _, base_xy = color_engine.target_whitepoint_xyz(cct, 0)
            _, base_v60 = color_engine._xy_to_uv60(*base_xy)

            # Mode 0 P3 and Mode 3 P3_Enhance / sRGB_Enhance share an identical neutral diagonal
            p3_nums = [int(v) for v in custom["1"].rstrip(",").split(",")]
            for slot in ("0", "1", "2", "3", "4"):
                nums = [int(v) for v in custom[slot].rstrip(",").split(",")]
                w_xy = white_2015_xy(igc, gc, nums)
                self.assertLess(duv76(w_xy, tgt_xy), 0.00025, f"{cct}K/{tint:+d} slot {slot}")
                _, w_v60 = color_engine._xy_to_uv60(*w_xy)
                if tint > 0:
                    self.assertGreater(w_v60 - base_v60, 0.0020)
                elif tint < 0:
                    self.assertLess(w_v60 - base_v60, -0.0020)
                if slot in ("3", "4"):
                    for i in range(17):
                        di = (i * 289 + i * 17 + i) * 3
                        self.assertEqual(nums[di:di + 3], p3_nums[di:di + 3])
            if cct == 5500 and tint == 0:
                self.assertGreater(tgt_xy[0], base65_xy[0])
            elif cct == 7500 and tint == 0:
                self.assertLess(tgt_xy[0], base65_xy[0])
            self.assertGreater(base65_v60, 0.0)

    def test_old_external_default_cannot_shadow_measured_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / app.PANEL_FILENAME
            shutil.copyfile(ROOT / "reference" / app.PANEL_FILENAME, old)
            with patch.object(app, "get_resource_roots", return_value=[Path(tmp),ROOT]):
                self.assertEqual(app.find_default_json(), ASSET)
            with patch.object(app, "get_resource_roots", return_value=[Path(tmp)]):
                self.assertIsNone(app.find_default_json())

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

    def test_release_notes_extractor_matches_changelog(self):
        script = ROOT / "scripts" / "extract_release_notes.py"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "notes.md"
            ok = subprocess.run(
                [sys.executable, "-B", str(script), "--version", app.APP_VERSION, "--out", str(out)],
                capture_output=True,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            body = out.read_text(encoding="utf-8")
            tag = f"v{app.APP_VERSION}"
            for required in (
                f"Fit8E-ColorCalib-{tag}-win64.zip",
                f"Fit8E-ColorCalib-{tag}-root-scripts.zip",
                f"Fit8E-ColorCalib-{tag}-macos-linux-shizuku.zip",
                "### English summary",
                f"/blob/{tag}/CHANGELOG.md",
            ):
                self.assertIn(required, body)
            self.assertNotIn("## 0.1.1", body)
        missing = subprocess.run(
            [sys.executable, "-B", str(script), "--version", "9.9.9-missing"],
            capture_output=True,
        )
        self.assertEqual(missing.returncode, 2)

    def test_profile_timeout_reports_pending_work_without_retrying(self):
        client = app.AdbClient(language="zh")
        st = app.DeviceStatus(serial="TEST", has_root_tool="su")
        with patch.object(client, "run", return_value=(124, "", "timeout")) as run:
            ok, detail = client._apply(st, ["install", "profile.json"], False, reboot=True)
            self.assertFalse(ok)
            self.assertIn("掌机可能仍在处理", detail)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[1], 120)
            client.language = "en"
            self.assertIn("before retrying", client._helper("TEST", ["uninstall"])[2])
            self.assertEqual(client._helper("TEST", ["probe"]), (124, "", "timeout"))
            self.assertEqual(run.call_args.args[1], 40)

    def test_invalid_cli_arguments_do_not_produce_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            for args in (["export", "--out", str(out), "--cct", "9000"],
                         ["install", "--tint", "11"]):
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                    app.run_cli(args)
                self.assertEqual(caught.exception.code, 2)
            self.assertFalse(out.exists())

    def test_export_preserves_default_and_existing_destination_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "default.json"
            base.write_bytes(ASSET.read_bytes())
            with self.assertRaisesRegex(ValueError, "built-in"):
                app.export_profile(base, base, 7100, 0)
            destination = Path(temp) / "saved.json"
            destination.write_bytes(b"earlier export")
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    app.export_profile(base, destination, 6000, 1)
            self.assertEqual(destination.read_bytes(), b"earlier export")
            self.assertEqual(base.read_bytes(), ASSET.read_bytes())
            self.assertEqual(set(Path(temp).iterdir()), {base, destination})

    def test_invalid_measurement_inputs_leave_existing_output(self):
        with self.assertRaisesRegex(ValueError, "invertible"):
            compiler.mat_inv([[1,1,1]] * 3)
        with self.assertRaisesRegex(ValueError, "gains"):
            compiler.characterize_panel([], (float("nan"), 1, 1))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "saved.json"
            out.write_bytes(b"old profile")
            with self.assertRaisesRegex(ValueError, "black < knee < peak"):
                compiler.generate_qdcm_v3(NATIVE, ASSET, out, hdr_knee=448, hdr_peak=447)
            self.assertEqual(out.read_bytes(), b"old profile")
            tpl = Path(tmp) / "tpl.json"
            tpl.write_bytes(ASSET.read_bytes())
            with self.assertRaisesRegex(ValueError, "separate from the template"):
                compiler.generate_qdcm_v3(NATIVE, tpl, tpl)
            self.assertEqual(tpl.read_bytes(), ASSET.read_bytes())
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    compiler.generate_qdcm_v3(NATIVE, ASSET, out)
            self.assertEqual(out.read_bytes(), b"old profile")

    def test_utf8_bom_rejected_consistently(self):
        bommed = b"\xef\xbb\xbf" + ASSET.read_bytes()
        with self.assertRaisesRegex(ValueError, "UTF-8 BOM"):
            color_engine.validate_qdcm(bommed)

    def test_empty_helper_output_cannot_succeed(self):
        client = app.AdbClient()
        st = app.DeviceStatus(serial="TEST")
        with patch.object(client, "_helper", return_value=(0, "", "")):
            ok, detail = client._apply(st, ["install", "profile.json"], False, reboot=True)
            self.assertFalse(ok)
            self.assertIn("without reporting", detail)

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

    def test_root_script_generation_and_preset_bundle(self):
        helper_bytes = HELPER.read_bytes()
        d65_bytes = ASSET.read_bytes()
        sh_a = root_script.build_install_root_script(helper_bytes, d65_bytes, cct=6500, tint=0)
        sh_b = root_script.build_install_root_script(helper_bytes, d65_bytes, cct=6500, tint=0)
        self.assertEqual(sh_a, sh_b)
        self.assertLess(len(sh_a), root_script.MAX_AYA_ROOT_SCRIPT_BYTES)
        # Single non-comment bootstrap line well under xsud's 1024-byte IPC command limit
        exec_lines = [ln for ln in sh_a.decode("utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        self.assertEqual(len(exec_lines), 1)
        self.assertLess(len(exec_lines[0].encode("utf-8")), 512)
        # Arbitrary custom label characters must not inject shell syntax or break single-line comments
        tricky = root_script.build_install_root_script(
            helper_bytes, d65_bytes, label_zh='bad"$(rm -rf /)"\nname.json', label_en="bad\nname.json"
        )
        tricky_exec = [ln for ln in tricky.decode("utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        self.assertEqual(len(tricky_exec), 1)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "presets"
            created = root_script.build_preset_bundle(ASSET, HELPER, out_dir)
            self.assertEqual(len(created), 11)
            sh_files = [p for p in created if p.suffix == ".sh"]
            self.assertEqual(len(sh_files), 10)
            for p in created:
                self.assertNotIn(" ", p.name)
                self.assertLess(p.stat().st_size, root_script.MAX_AYA_ROOT_SCRIPT_BYTES)
                if p.suffix == ".sh":
                    self.assertFalse(p.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertTrue((out_dir / "使用说明_README.txt").read_bytes().startswith(b"\xef\xbb\xbf"))
            # Export must never overwrite protected project files
            base_copy = Path(tmp) / "base.json"
            base_copy.write_bytes(d65_bytes)
            helper_copy = Path(tmp) / "helper.sh"
            helper_copy.write_bytes(helper_bytes)
            with self.assertRaisesRegex(ValueError, "separate"):
                app.export_root_script_file(base_copy, base_copy, 6500, 0, helper_path=helper_copy)
            with self.assertRaisesRegex(ValueError, "separate"):
                app.export_root_script_file(base_copy, helper_copy, 6500, 0, helper_path=helper_copy)
            with self.assertRaisesRegex(ValueError, "separate"):
                app.export_root_script_file(base_copy, base_copy, None, 0, helper_path=helper_copy)
            # Release root-scripts archive helper packs all preset files + license notices
            import importlib.util
            spec = importlib.util.spec_from_file_location("build_win_rel", ROOT / "scripts/build_windows_release.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            stage = Path(tmp) / "stage"
            stage.mkdir()
            bundle_dir, zip_path = mod.build_root_scripts_archive(ASSET, HELPER, stage, app.APP_VERSION)
            self.assertTrue(bundle_dir.is_dir())
            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
            prefix = f"Fit8E-ColorCalib-v{app.APP_VERSION}-root-scripts/"
            self.assertIn(prefix + "01_Fit8E_安装校色_6500K_D65标准.sh", names)
            self.assertIn(prefix + "11_Fit8E_切换模式3_自适应鲜艳.sh", names)
            self.assertIn(prefix + "99_Fit8E_卸载还原原厂.sh", names)
            self.assertIn(prefix + "使用说明_README.txt", names)
            self.assertIn(prefix + "LICENSE", names)

    def test_cli_export_script_and_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            inst_sh = tdir / "inst.sh"
            custom_json = tdir / "custom.json"
            custom_json.write_bytes(color_engine.synthesize_qdcm_bytes(ASSET, 6800, -1)[0])
            custom_sh = tdir / "custom.sh"
            mode_sh = tdir / "mode3.sh"
            st_sh = tdir / "status.sh"
            un_sh = tdir / "uninstall.sh"
            preset_dir = tdir / "pack"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.run_cli(["export-script", "--cct", "6000", "--tint", "-2", "--out", str(inst_sh)]), 0)
                self.assertEqual(app.run_cli(["export-script", "--json", str(custom_json), "--out", str(custom_sh)]), 0)
                self.assertEqual(app.run_cli(["export-script", "--action", "mode", "--mode", "3", "--out", str(mode_sh)]), 0)
                self.assertEqual(app.run_cli(["export-script", "--action", "status", "--out", str(st_sh)]), 0)
                self.assertEqual(app.run_cli(["export-script", "--action", "uninstall", "--out", str(un_sh)]), 0)
                self.assertEqual(app.run_cli(["export-presets", "--out-dir", str(preset_dir)]), 0)
            for p in (inst_sh, custom_sh, mode_sh, st_sh, un_sh):
                self.assertTrue(p.is_file())
                self.assertLess(p.stat().st_size, root_script.MAX_AYA_ROOT_SCRIPT_BYTES)
            self.assertEqual(len(list(preset_dir.iterdir())), 11)
            bad_out = tdir / "bad.sh"
            for bad_args in (
                ["export-script", "--action", "mode", "--out", str(bad_out)],
                ["export-script", "--action", "install", "--mode", "3", "--out", str(bad_out)],
                ["export-script", "--action", "status", "--cct", "6000", "--out", str(bad_out)],
                ["export-script", "--json", str(custom_json), "--tint", "1", "--out", str(bad_out)],
                ["export-script", "--cct", "9000", "--out", str(bad_out)],
            ):
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                    app.run_cli(bad_args)
                self.assertEqual(caught.exception.code, 2)
            self.assertFalse(bad_out.exists())


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
        self.isolated = self.isolated.replace(b"/proc/sys/kernel/random/boot_id", b'"$FIT_TEST_DIR/boot-id"')
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
            call = shlex.split(args[2])
            as_root = call[0] in ("su","xsu")
            if as_root:
                # Elevated OEM wrappers (like xsu) do not forward stdin; helper must be staged.
                self.assertIsNone(stdin)
                self.root_calls += 1
                call = shlex.split(call[2])
                self.assertEqual(call[0], "sh")
                self.assertTrue(call[1].startswith("/data/local/tmp/fit8e-helper."))
                staged = self.tmp / Path(call[1]).name
                self.assertEqual(staged.read_bytes(), self.script)
                op, rest = call[2], call[3:]
            else:
                self.assertEqual(stdin, self.script)
                self.assertEqual(call[:3], ["sh", "-s", "--"])
                op, rest = call[3], call[4:]
            if op == "probe" and self.bad_probe:
                return reply(1, err="probe failed")
            if op in ("install", "uninstall") and self.deny_shell and not as_root:
                return reply(73, err="cannot stage")
            rest = [(self.tmp / Path(v).name).as_posix() if v.startswith("/data/local/tmp/") else v for v in rest]
            env = self.env.copy()
            if as_root:
                env["TEST_ROOT"] = "1"
            if serial == "OTHER":
                env["TEST_DEVICE"] = "other"
            p = self.device(op, *rest, env=env)
            return reply(p.returncode, p.stdout.decode(), p.stderr.decode())
        elif args[0] == "shell" and args[1].startswith("mktemp /data/local/tmp/fit8e-qdcm."):
            return reply(out="/data/local/tmp/fit8e-qdcm.abcdef12\n")
        elif args[0] == "shell" and args[1].startswith("mktemp /data/local/tmp/fit8e-helper."):
            return reply(out="/data/local/tmp/fit8e-helper.abcdef12\n")
        elif args[0] == "shell" and (
            args[1].startswith("rm -f /data/local/tmp/fit8e-qdcm.")
            or args[1].startswith("rm -f /data/local/tmp/fit8e-helper.")
        ):
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
        for change in ("truncated","out_of_range","unbound","wrong_panel","bad_enhance_intent","partial_enhance","missing_hdr_enhance"):
            data=json.loads(ASSET.read_bytes())
            if change=="wrong_panel":
                data["other"]=data.pop(color_engine.PANEL_KEY)
            elif change=="unbound":
                mode=data[color_engine.PANEL_KEY]["P3"]
                block=color_engine._dec_raw(mode["PostBlendGamut"])
                block["Custom"]=9
                mode["PostBlendGamut"]=color_engine._enc_obj(block)
            elif change=="bad_enhance_intent":
                data[color_engine.PANEL_KEY]["sRGB_Enhance"]["Applicability"]["RenderIntent"]=1
            elif change=="partial_enhance":
                del data[color_engine.PANEL_KEY]["HDR_Enhance"]
            elif change=="missing_hdr_enhance":
                data[color_engine.PANEL_KEY]["HDR_Enhance"]["PostBlendHdrBlob"]=color_engine._enc_obj({"blob":""})
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
        # Mode 3 (Adaptive Vibrant) live switch works via both helper and AdbClient
        self.assertTrue(self.client.set_mode(3)[0])
        self.assertEqual(self.mode.read_text().strip(), "3")
        self.assertFalse(self.client.set_mode(4)[0])

    def test_device_validator_decodes_chunk_boundaries_and_escaped_keys(self):
        doc = json.loads(self.adjusted)
        hdr = doc[color_engine.PANEL_KEY]["HDR"]
        for count in (511, 512):
            # Consecutive lengths exercise both possible byte orders for the first word.
            hdr["PostBlendHdrBlob"] = color_engine._enc_obj({"blob": "x" * count + '"\\'})
            data = json.dumps(doc).encode().replace(b'"Version"', br'"\u0056ersion"', 1)
            self.custom.write_bytes(data)
            color_engine.validate_qdcm(data)
            self.assertEqual(self.device("validate", self.custom.as_posix()).returncode, 0)
        self.custom.write_bytes(data.replace(br'"\u0056ersion"', br'"\u00ZZersion"'))
        self.assertNotEqual(self.device("validate", self.custom.as_posix()).returncode, 0)

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
        boot = self.tmp / "boot-id"
        boot.write_text("boot-root-1")
        self.mode.write_text("null")
        self.deny_shell=True
        self.assertTrue(self.client.install(ASSET,reboot=False)[0])
        self.assertEqual(self.root_calls,1)
        self.assertFalse((self.tmp / "fit8e-helper.abcdef12").exists())
        self.assertEqual((self.tmp / "root-attributes").read_text().splitlines(), ["chown", "chcon"])
        # Activation state must remain readable by non-root probe after elevated install.
        self.assertEqual(self.client.probe().activation, "pending")
        if os.name != "nt":
            self.assertEqual((self.display / ".fit8e-colorcalib").stat().st_mode & 0o777, 0o755)
            self.assertEqual((self.display / ".fit8e-colorcalib/activation").stat().st_mode & 0o777, 0o644)
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertEqual(self.root_calls, 2)
        self.assertEqual(self.mode.read_text().strip(),"null")
        # Root-created 0600 record/original must signal rc=73 so non-root desktop ADB retries elevated.
        self.assertIn(b'[ -r "$record" ] || die 73', self.script)
        self.assertIn(b'[ -r "$original" ] || die 73', self.script)

    def test_shizuku_flags_without_positional_path(self):
        p = self.device("install", "--sha256", "deadbeef")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"/sdcard/Download/", p.stderr)
        self.assertNotIn(b"Unknown argument", p.stderr)

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


    def test_one_uninstall_restores_factory_across_legacy_upgrade(self):
        legacy = (ROOT / "reference" / app.PANEL_FILENAME).read_bytes()
        self.target.write_bytes(legacy)
        self.assertTrue(self.client.install(ASSET, reboot=False)[0])
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.mode.read_text().strip(), "2")

        # A 0.1.0 journal may already have archived the old built-in as its base.
        state = self.display / ".fit8e-colorcalib"
        state.mkdir()
        (state / "original").write_bytes(legacy)
        self.target.write_bytes(ASSET.read_bytes())
        current = hashlib.sha256(self.target.read_bytes()).hexdigest()
        (state / "record").write_text(f"{hashlib.sha256(legacy).hexdigest()} 0 {current} {current}\n")
        self.assertTrue(self.client.uninstall(reboot=False)[0])
        self.assertFalse(self.target.exists())
        self.assertFalse(state.exists())
        self.assertEqual(self.mode.read_text().strip(), "0")

    def test_activation_requires_matching_profile_and_new_boot(self):
        boot = self.tmp / "boot-id"
        boot.write_text("boot-one")
        self.assertTrue(self.client.install(ASSET, reboot=False)[0])
        self.assertEqual(self.client.probe().activation, "pending")
        boot.write_text("boot-two")
        self.assertEqual(self.client.probe().activation, "rebooted")
        self.target.write_bytes(self.adjusted)
        self.assertEqual(self.client.probe().activation, "")

    def test_busy_lock_prevents_writes(self):
        script=self.tmp/"helper.sh"
        script.write_bytes(self.isolated)
        p=self.bash('(flock -nx 9 || exit 90; sh "$1" install "$2") 9<"$FIT_TEST_DIR/display"',
                    script.as_posix(), ASSET.as_posix())
        self.assertEqual(p.returncode,5,p.stderr)
        self.assertFalse(self.target.exists())

    def test_root_script_execution_and_line_by_line_guard(self):
        # 1. Accidental "逐行运行" runs the single bootstrap line via `sh -c <line>` where $0 is not a file.
        guard = self.bash(root_script._BOOTSTRAP_LINE)
        self.assertEqual(guard.returncode, 1)
        self.assertIn("整体运行", guard.stdout.decode("utf-8", "replace"))
        self.assertFalse(self.target.exists())

        # 2. Full isolated "整体运行" (sh <filePath>) lifecycle: install -> mode 3 -> status -> uninstall.
        self.target.write_bytes(b"oem-stock-profile")
        (self.tmp / "boot-id").write_text("boot-root-sh-1")
        inst_raw = root_script.build_install_root_script(
            self.isolated, self.adjusted, cct=6800, tint=-1, label_zh='6800K "safe" $(test)'
        ).replace(b"/data/local/tmp/fit8e-root.XXXXXXXX", b"$FIT_TEST_DIR/fit8e-root.XXXXXXXX")
        mode3_raw = root_script.build_action_root_script(
            self.isolated, "mode", mode=3
        ).replace(b"/data/local/tmp/fit8e-root.XXXXXXXX", b"$FIT_TEST_DIR/fit8e-root.XXXXXXXX")
        status_raw = root_script.build_action_root_script(
            self.isolated, "status"
        ).replace(b"/data/local/tmp/fit8e-root.XXXXXXXX", b"$FIT_TEST_DIR/fit8e-root.XXXXXXXX")
        uninst_raw = root_script.build_action_root_script(
            self.isolated, "uninstall"
        ).replace(b"/data/local/tmp/fit8e-root.XXXXXXXX", b"$FIT_TEST_DIR/fit8e-root.XXXXXXXX")

        root_env = dict(self.env, TEST_ROOT="1")
        for name, data in (
            ("install.sh", inst_raw), ("mode3.sh", mode3_raw),
            ("status.sh", status_raw), ("uninstall.sh", uninst_raw),
        ):
            (self.tmp / name).write_bytes(data)

        p_inst = self.bash('sh "$1"', (self.tmp / "install.sh").as_posix(), env=root_env)
        self.assertEqual(p_inst.returncode, 0, p_inst.stdout.decode("utf-8", "replace"))
        inst_out = p_inst.stdout.decode("utf-8", "replace")
        self.assertIn("安装成功", inst_out)
        self.assertIn("手动重启掌机", inst_out)
        self.assertIn('6800K "safe" $(test)', inst_out)
        self.assertEqual(self.target.read_bytes(), self.adjusted)
        self.assertEqual(self.mode.read_text().strip(), "0")
        self.assertFalse(self.reboots)
        self.assertEqual((self.tmp / "root-attributes").read_text().splitlines(), ["chown", "chcon"])

        p_mode = self.bash('sh "$1"', (self.tmp / "mode3.sh").as_posix(), env=root_env)
        self.assertEqual(p_mode.returncode, 0, p_mode.stdout.decode("utf-8", "replace"))
        self.assertIn("切换成功", p_mode.stdout.decode("utf-8", "replace"))
        self.assertEqual(self.mode.read_text().strip(), "3")

        p_st = self.bash('sh "$1"', (self.tmp / "status.sh").as_posix(), env=root_env)
        self.assertEqual(p_st.returncode, 0, p_st.stdout.decode("utf-8", "replace"))
        st_out = p_st.stdout.decode("utf-8", "replace")
        self.assertIn("Target match: yes", st_out)
        self.assertIn("查询完成", st_out)

        p_un = self.bash('sh "$1"', (self.tmp / "uninstall.sh").as_posix(), env=root_env)
        self.assertEqual(p_un.returncode, 0, p_un.stdout.decode("utf-8", "replace"))
        un_out = p_un.stdout.decode("utf-8", "replace")
        self.assertIn("还原成功", un_out)
        self.assertIn("手动重启掌机", un_out)
        self.assertEqual(self.target.read_bytes(), b"oem-stock-profile")
        self.assertEqual(self.mode.read_text().strip(), "2")
        self.assertFalse(self.reboots)



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
                window._render_status(app.DeviceStatus(adb_path=None))
                self.assertEqual(window.status_pill.cget("text"), "ADB Missing")
                window.selected_json = None
                window._update_profile_label()
                self.assertEqual(window.lbl_prof_v.cget("text"), "(Calibration JSON missing)")
                window.selected_json = ASSET
                window._render_status(ready)
                with patch.object(window.client, "probe", side_effect=AssertionError("should not probe rebooting device")), \
                     patch.object(window.client, "install", return_value=(True, "Profile installed. Reboot requested.")), \
                     patch.object(window.client, "uninstall", return_value=(True, "Restored. Reboot requested.")), \
                     patch.object(messagebox, "askyesno", return_value=True), \
                     patch.object(messagebox, "showinfo") as info, patch.object(messagebox, "showerror") as error:
                    window._do_install(reboot=True)
                    self.assertEqual(window.status.state, "disconnected")
                    window._render_status(ready)
                    window._do_uninstall()
                    self.assertEqual(window.status.state, "disconnected")
                    self.assertEqual(info.call_count, 2)
                    error.assert_not_called()
                window.tint_var.set(6)
                window._select_preset(6500)
                self.assertEqual(window.tint_var.get(), 0)
                self.assertEqual(window.cct_var.get(), 6500)
                self.assertIn("2015", window._current_profile_desc())
                window._select_preset(6800)
                self.assertEqual(window.cct_var.get(), 6800)
                # All 4 color mode cards (including Mode 3 Adaptive Vibrant) render & highlight cleanly
                self.assertEqual(set(window.mode_cards.keys()), {0, 1, 2, 3})
                window._highlight_mode(3)
                self.assertEqual(window.active_mode_id, 3)
                self.assertEqual(window.mode_cards[3]["title"].cget("text"), "Adaptive")
                self.assertEqual(window.btn_export_sh.cget("text"), "Export Root .sh")
                window.update_idletasks()
                self.assertLessEqual(window.btn_export_sh.master.winfo_reqwidth(), window.winfo_reqwidth())
                # Export Root Script flow works for both tuned built-in and custom JSON
                from tkinter import filedialog
                with tempfile.TemporaryDirectory() as tmp:
                    out_tuned = Path(tmp) / "Fit8E_6800K.sh"
                    with patch.object(filedialog, "asksaveasfilename", return_value=str(out_tuned)), \
                         patch.object(messagebox, "showinfo") as info, \
                         patch.object(messagebox, "showerror") as err:
                        window._export_root_script()
                        self.assertEqual(info.call_count, 1)
                        err.assert_not_called()
                    self.assertTrue(out_tuned.is_file())
                    self.assertLess(out_tuned.stat().st_size, root_script.MAX_AYA_ROOT_SCRIPT_BYTES)
                    custom_src = Path(tmp) / "custom_calib.json"
                    custom_src.write_bytes(ASSET.read_bytes())
                    out_custom = Path(tmp) / "Fit8E_custom.sh"
                    window.selected_json = custom_src
                    window.is_custom_json = True
                    window._set_wp_controls_enabled(False)
                    self.assertEqual(str(window.btn_export_sh["state"]), "normal")
                    with patch.object(filedialog, "asksaveasfilename", return_value=str(out_custom)), \
                         patch.object(messagebox, "showinfo") as info, \
                         patch.object(messagebox, "showerror") as err:
                        window._export_root_script()
                        self.assertEqual(info.call_count, 1)
                        err.assert_not_called()
                    self.assertTrue(out_custom.is_file())
                    window._reset_profile()
                window._toggle_lang()
                self.assertEqual(window.mode_cards[3]["title"].cget("text"), "自适应鲜艳")
                self.assertEqual(window.btn_export_sh.cget("text"), "导出 Root 脚本")
            finally:
                window.destroy()
        with patch.object(tk.Tk, "__init__", hidden), patch.object(tk.Tk, "mainloop", exercise), \
             patch.object(app, "find_adb", return_value=None), \
             patch.object(app, "_run_subproc", side_effect=AssertionError("Unexpected real device command")):
            self.assertEqual(app.run_cli([]), 0)


if __name__ == "__main__":
    unittest.main()
