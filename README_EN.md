**This project was built by GPT6.2 Astra and Gemini4 Neon.**

# Fit 8E Display Calibration

Install a color profile on the KONKR Pocket FIT Elite (Fit 8E) to correct oversaturation and white-point tint on its internal display. Current version **0.1.1**. [简体中文](README.md) · [Changelog](CHANGELOG.md) · [Project](https://github.com/lx081081/Fit8E-ColorCalib)

The application code is licensed under the GNU General Public License, version 3 only. [License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## Before you start

- **Use only on the Snapdragon 8 Elite Fit 8E with its ar14 internal panel.** For a “Panel unconfirmed” result, check the panel model before selecting manual confirmation. Other handhelds, including the Fit 8G3, are unsupported.
- **Save your game before installing or uninstalling.** The Windows tool reboots the handheld by default. Keep any custom JSON used with a legacy installer so the tool can verify it during removal.
- This community tool changes the display configuration at your own risk. The built-in calibration was measured on one unit. Individual panels can vary.

## Features

- The 5500–7500 K slider and the 6000, 6500, 6800 and 7100 K presets use the **CIE 2015 2° observer**. Selecting a preset resets tint to zero. Moving the temperature slider keeps the selected tint.
- **6500 K with tint 0** uses the exact measured D65 profile. Other settings compile new hardware tables for export or installation. When a custom JSON is selected, the tool uses its white point and locks both sliders.
- **Natural** selects sRGB, Display P3 or HDR10 according to content. **Boosted** increases saturation. **Vivid** uses the native panel gamut.

## Getting started

1. Extract the complete `Fit8E-ColorCalib-v0.1.1-win64.zip` and launch `Fit8E-ColorCalib.exe`. ADB and the default profile are included. Public builds are available on the [releases page](https://github.com/lx081081/Fit8E-ColorCalib/releases).
2. Enable **USB debugging** under Developer options. Connect a USB data cable, allow debugging, click **Refresh** and confirm `Pocket FIT Elite`.
3. For the measured default, leave **6500 K and tint 0** selected. Choose **Install Calibration & Reboot** or **Install Only** to reboot later.
4. Unlock the handheld after reboot and refresh. If Windows loses the USB connection, reconnect the data cable and select File transfer. Older installations without a boot record are shown as **Activation Unconfirmed**.
5. Choose **Uninstall & Restore** to restore the recorded configuration and display mode. The tool recognizes the old built-in 6500 K profile. After a busy or timeout message, wait for the operation to finish and refresh before retrying.

<details>
<summary>macOS, Linux, CLI and Shizuku</summary>

- macOS / Linux requires Python 3.9 or newer and ADB. Run `python3 src/fit8e_color_tool.py status` in the source directory. Once `Target Match` is `True`, run `python3 src/fit8e_color_tool.py install` to install the default profile.
- To export a profile, run `python3 src/fit8e_color_tool.py export --cct 6800 --tint 0 --out fit8e-6800.json`. For your own conventional 1931 RGB/XYZ ArgyllCMS `.ti3` measurements, use `src/build_qdcm_v3.py` to generate a custom profile.
- For Shizuku, copy `scripts/fit8e_calib_shizuku.sh` and the default JSON from `assets` to `/sdcard/Download/`. In a Shizuku-authorized shell run `sh /sdcard/Download/fit8e_calib_shizuku.sh install`, then reboot manually.

</details>

## How it works and what was measured

The temperature is the nominal CIE D-series daylight temperature. The tool integrates the daylight basis spectra against the 2015 2° matching functions at 1 nm intervals and anchors 6500 K to the tabulated D65 spectrum. Tint shifts the white point toward green or magenta in the observer's uniform chromaticity coordinates. Two 105-patch factory captures relate the original sRGB and Display P3 primaries to the measured panel. Measured gray ramps determine the hardware tone curves. [CIE daylight basis](https://doi.org/10.25039/CIE.DS.w7zunnny) · [CIE 2015 2° observer](https://doi.org/10.25039/CIE.DS.548rw69q)

Natural mode was verified with 51 SDR patches at system brightness 102/255 using an X-Rite i1Display3 with Panasonic PFS spectral correction.

| Metric | Factory | 0.1.1 default |
|---|---:|---:|
| White-point distance to 2015 D65, Δu′v′ | 0.00955 | 0.00022 |
| Mean gray distance at 20–100%, Δu′v′ | 0.00685 | 0.00055 |
| White luminance at the same setting, cd/m² | 211.96 | 191.20 |

Five repeat readings averaged **(0.31345, 0.33072)** against the D65 target **(0.31345, 0.33080)**. A 1931 2° cross-check against the white-adapted sRGB reference gave mean **ΔE00 0.45** and maximum **1.12**. Other temperatures are model predictions. The HDR10 PQ curve uses the project settings of 280 nits at the knee, 447 nits target peak and 0.37 nits black. Optical verification for this profile covers Natural-mode SDR sRGB. [Measurement results](assets/measurements/verification.json)
