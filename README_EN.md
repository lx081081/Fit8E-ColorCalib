**This project was built by GPT6.2 Astra and Gemini4 Neon.**

# Fit 8E Display Calibration

Install a color profile for the KONKR Pocket FIT Elite (Fit 8E) to correct oversaturated colors and white-point tint on its built-in display. Current version `0.1.0`. [简体中文](README.md) · [Complete source](https://github.com/lx081081/Fit8E-ColorCalib/archive/refs/tags/v0.1.0.zip)

The application code is licensed under the GNU General Public License, version 3 only (`GPL-3.0-only`). [Full license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## Before you start

- **Use this tool only with a Snapdragon 8 Elite Fit 8E and its ar14 internal panel.** Other handhelds, including the Fit 8G3, are unsupported. If the app shows “Fit 8E · Panel unconfirmed”, check the panel model before selecting “I verified the ar14 panel”.
- **Save your game before installing or uninstalling.** The Windows app reboots the handheld by default. If you installed a custom profile with an older version, keep the original JSON. You may need it for removal.
- This is a community tool. You assume the risk of changing your display configuration. The bundled profile was measured on one unit. White point and color accuracy can vary between panels.

## Features

- **Switch display modes on the fly.** Natural uses sRGB, Display P3 or HDR10 according to the content. Boosted increases saturation. Vivid uses the panel’s native gamut.
- **Adjust the white point.** Choose a color temperature from 5500 to 7500 K and fine-tune green or magenta tint. Export the adjusted profile as JSON or install it directly.
- **Use your own measurements.** If you have a colorimeter, `src/build_qdcm_v3.py` in the source tree can turn your ArgyllCMS `.ti3` measurements into a profile. Load it with **Custom JSON...**.

## How to use it

### Windows

1. [Download the Windows 64-bit portable package](https://github.com/lx081081/Fit8E-ColorCalib/releases/download/v0.1.0/Fit8E-ColorCalib-v0.1.0-win64.zip). Extract the entire archive, open `Fit8E-ColorCalib.exe`, and click **EN** for English. The package includes ADB and the default profile.
2. Enable **USB debugging** in the handheld’s Developer options. Connect it to your PC with a USB data cable and tap **Allow USB debugging** on the handheld. Click **Refresh** in the app and check that it shows `Pocket FIT Elite`.
3. For a first install, leave the defaults at **6500 K and tint 0**. Click **Install Calibration & Reboot**, or choose **Install Only** to reboot manually later.
4. Unlock the handheld after reboot and click **Refresh**. Once the app shows **Installed & Active**, you can switch display modes.
5. To undo the install, click **Uninstall & Restore** and confirm the reboot. The tool restores the profile and display mode recorded before the first installation.

<details>
<summary>Use macOS, Linux or Shizuku on the handheld</summary>

- **macOS / Linux** requires Python 3.9 or later and ADB. From the source directory, run `python3 src/fit8e_color_tool.py status`. After checking that `Target Match` is `True`, run `python3 src/fit8e_color_tool.py install` to install and reboot.
- **Shizuku on the handheld** can install the same profile. Copy `scripts/fit8e_calib_shizuku.sh` and the calibration JSON from `assets` to `/sdcard/Download/`. Run `sh /sdcard/Download/fit8e_calib_shizuku.sh install` in a Shizuku-authorized shell, then reboot manually after it succeeds.

</details>

## How it works

The tool writes a Qualcomm QDCM profile for this panel to `/data/vendor/display/`. After reboot, the display service loads it. The display processor uses a 3D lookup table to map colors into the target gamut, applies tone curves to correct grayscale, and maps HDR highlights into the panel’s brightness range.

At 40% system brightness, the test unit’s average color error (ΔE00) across 51 sRGB patches fell from **3.77** with the stock settings to **0.46** in calibrated Natural mode. A smaller value means the measured colors are closer to their targets. [View the before-and-after measurements](assets/Fit8E_Compare_Report.png)
