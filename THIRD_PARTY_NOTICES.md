# Third-party notices

Fit8E-ColorCalib is distributed under the GNU General Public License, version 3 only. The following notices apply to the identified components.

## Original Fit8E-ColorCalib code

The application incorporates code from KONKR Pocket FIT Elite Community Contributors. Its original notice follows.

```text
MIT License

Copyright (c) 2026 KONKR Pocket FIT Elite Community Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Qualcomm QDCM profiles

The bundled calibration profile and the two QDCM profiles in the source archive's `reference/` directory retain their original notices in the `Copyright` field. That field contains The Linux Foundation's 2021 redistribution conditions and disclaimer. Qualcomm Innovation Center, Inc.'s 2022–2024 changes carry the identifier BSD-3-Clause-Clear. The complete notices accompany each profile.

## Android platform-tools

The Windows package includes Google's ADB executable and its two DLLs. Their notices are reproduced in `platform-tools/NOTICE.txt` alongside those files.

## Windows executable

The Windows package includes the CPython runtime, Tcl/Tk, and the PyInstaller bootloader. The accompanying `licenses/` directory contains Python.txt, Tcl.txt, Tk.txt and PyInstaller.txt, including the bootloader exception.

## CIE colorimetry data

The daylight projection in `src/color_engine.py` is derived from the following International Commission on Illumination (CIE) datasets. It linearly interpolates S0, S1 and S2 to 1 nm and sums each against the CIE 2015 2° matching functions. The D65 coordinates use the tabulated illuminant. The derived numerical data is provided under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

- CIE 2018, [Components of the relative spectral distribution of daylight](https://doi.org/10.25039/CIE.DS.w7zunnny).
- CIE 2015, [Cone-fundamental-based spectral tristimulus values for 2° field size](https://doi.org/10.25039/CIE.DS.548rw69q).
- CIE 2019, [CIE standard illuminant D65](https://doi.org/10.25039/CIE.DS.hjfjmt59).
