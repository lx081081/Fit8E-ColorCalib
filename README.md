**本项目由 GPT6.2 Astra 和 Gemini4 Neon 构建。**

# Fit 8E 屏幕校色工具

通过 USB 调试给 KONKR Pocket FIT Elite（Fit 8E）的内屏安装校色档，改善颜色过饱和和白点偏色。当前版本为 **0.1.1**。[English](README_EN.md) · [更新记录](CHANGELOG.md) · [项目主页](https://github.com/lx081081/Fit8E-ColorCalib)

应用代码采用 GNU 通用公共许可证第 3 版（仅此版本）。[许可证全文](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md)

## 使用前请注意

- **仅适用于骁龙 8 Elite、配备 ar14 内屏的 Fit 8E。** 若工具提示「面板待确认」，请先核实内屏型号，再勾选手动确认。Fit 8G3 和其他掌机请勿安装。
- **安装或卸载前请保存游戏进度。** Windows 工具默认会重启掌机。使用过旧版自定义档的用户，请保留当时安装的 JSON，以便卸载时核对。
- 这是社区工具，修改显示配置的风险由使用者承担。内置档来自一台样机，同型号面板之间也有差异。

## 功能

- **白点色温**支持 5500–7500 K。滑块和 6000 / 6500 / 6800 / 7100 K 四个预设均按 CIE 2015 2° 观察者计算。点击预设会将色调归零。手动移动色温滑块会保留色调。
- **6500 K、色调 0** 直接使用本机实测并复核的 D65 原档。其余设置会生成新的校色表，可导出 JSON 或直接安装。选中自定义 JSON 后，工具使用文件内的白点，锁定两个滑块。
- **自然标准**按内容使用 sRGB、Display P3 或 HDR10。**增强模式**提高饱和度，**鲜艳模式**使用面板原生广色域。

## 使用步骤

1. 完整解压 `Fit8E-ColorCalib-v0.1.1-win64.zip`，双击 `Fit8E-ColorCalib.exe`。便携包已包含 ADB 和默认校色档。公开发布的版本可在[发布页](https://github.com/lx081081/Fit8E-ColorCalib/releases)查看。
2. 在掌机的开发者选项开启 **USB 调试**。用数据线连接电脑，允许调试，点工具的「刷新」，确认型号为 `Pocket FIT Elite`。
3. 首次使用可保留 **6500 K、色调 0**。点「一键安装校色并重启」，或点「仅安装（稍后重启）」自行选择重启时间。
4. 重启后解锁并刷新。若 Windows 未重新识别掌机，重插掌机数据线并选择「文件传输」。旧版安装缺少重启记录时会显示「生效待确认」。
5. 需要撤销时，点「卸载并还原原厂」。工具恢复首次安装前的配置和显示模式，并识别历史内置 6500 K 档。遇到锁占用或传输超时，请先等当前操作结束，再刷新核对档案。

<details>
<summary>macOS、Linux、命令行和 Shizuku</summary>

- macOS / Linux 需要 Python 3.9 或以上版本及 ADB。在源码目录运行 `python3 src/fit8e_color_tool.py status`。确认 `Target Match` 为 `True` 后，运行 `python3 src/fit8e_color_tool.py install` 安装默认档。
- 例如运行 `python3 src/fit8e_color_tool.py export --cct 6800 --tint 0 --out fit8e-6800.json`。有自己的测量时，可用 `src/build_qdcm_v3.py` 将常规 1931 RGB/XYZ 格式的 ArgyllCMS `.ti3` 生成为自定义档。
- 掌机端使用 Shizuku 时，将 `scripts/fit8e_calib_shizuku.sh` 和 `assets` 中的默认校色 JSON 放到 `/sdcard/Download/`。在已授权的终端运行 `sh /sdcard/Download/fit8e_calib_shizuku.sh install`，成功后手动重启。

</details>

## 工作原理与实测

色温数值表示 CIE D 系列日光的标称色温。工具以 1 nm 间隔积分日光基函数和 2015 2° 观察者函数，以标准 D65 光谱为 6500 K 锚点。色调沿该观察者的日光轨迹向绿色或品红微调。程序用样机的两组 105 色测量换算 sRGB 和 Display P3 原色，再用实测灰阶生成硬件查找表。[CIE 日光基函数](https://doi.org/10.25039/CIE.DS.w7zunnny) · [CIE 2015 2° 观察者](https://doi.org/10.25039/CIE.DS.548rw69q)

在自然模式、系统亮度 102/255 下，默认档的 51 色复测得到以下结果。仪器为 X-Rite i1Display3，使用松下 PFS 校正光谱。

| 指标 | 原厂默认 | 0.1.1 默认档 |
|---|---:|---:|
| 白点到 2015 D65 的距离 Δu′v′ | 0.00955 | 0.00022 |
| 20%–100% 灰阶平均 Δu′v′ | 0.00685 | 0.00055 |
| 白场亮度（cd/m²，同一亮度设置） | 211.96 | 191.20 |

校准后五次白点测量的平均坐标为 **(0.31345, 0.33072)**，D65 目标为 **(0.31345, 0.33080)**。在 1931 2° 下按适配后的 sRGB 目标复核，51 色平均 ΔE00 为 **0.45**，最大 **1.12**。其他色温是模型计算值。HDR10 继续使用 PQ 映射，280 尼特拐点、447 尼特目标峰值和 0.37 尼特黑位均为档案参数。本轮光学复测覆盖自然模式的 SDR sRGB。[本轮测量数据](assets/measurements/verification.json)
