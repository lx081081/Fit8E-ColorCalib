**本项目由 GPT6.2 Astra 和 Gemini4 Neon 构建。**

# Fit 8E 屏幕校色工具

通过 USB 调试给 KONKR Pocket FIT Elite（Fit 8E）的内屏安装校色档，改善颜色过饱和和白点偏色。当前版本为 `0.1.0`。[English](README_EN.md) · [完整源码](https://github.com/lx081081/Fit8E-ColorCalib/archive/refs/tags/v0.1.0.zip)

本项目的应用代码采用 GNU 通用公共许可证第 3 版（仅此版本，`GPL-3.0-only`）。[许可证全文](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md)

## 使用前请注意

- **仅适用于骁龙 8 Elite、配备 ar14 内屏的 Fit 8E。** Fit 8G3 和其他掌机请勿安装。若工具提示「Fit 8E · 面板待确认」，请先核实内屏型号。只有确认是 ar14 后，才勾选「已手动核实 ar14 面板」。
- **安装或卸载前请保存游戏进度。** Windows 工具默认会重启掌机。用过旧版自定义校色档的用户，请保留当时安装的 JSON，卸载时可能需要用它核对。
- 这是社区工具，修改显示配置的风险由使用者承担。内置校色档来自实测样机，同型号屏幕的白点和色差也可能不同。

## 功能

- 三种模式可即时切换。**自然标准**按内容使用 sRGB、Display P3 或 HDR10，**增强模式**提高饱和度，**鲜艳模式**使用面板原生广色域。
- 白点色温可调至 **5500–7500 K**，也可微调绿色或品红色偏。调整后的校色档可以导出为 JSON，也可以直接安装。
- 有校色仪时，可以用源码中的 `src/build_qdcm_v3.py` 根据自己的 ArgyllCMS `.ti3` 测量文件生成校色档，再通过「自定义 JSON...」安装。

## 使用步骤

### Windows

1. [下载 Windows 64 位便携包](https://github.com/lx081081/Fit8E-ColorCalib/releases/download/v0.1.0/Fit8E-ColorCalib-v0.1.0-win64.zip)，完整解压后双击 `Fit8E-ColorCalib.exe`。包内已包含 ADB 和默认校色档。
2. 在掌机的开发者选项中开启 **USB 调试**，用 USB 数据线连接电脑，在掌机上点「允许 USB 调试」。回到工具点「刷新」，确认显示 `Pocket FIT Elite`。
3. 首次使用可保留默认 **6500 K、色调 0**，点「一键安装校色并重启」。想自己选择重启时间，可点「仅安装（稍后重启）」。
4. 掌机重启后先解锁，再点「刷新」。看到 **「已安装并生效」** 即可切换显示模式。
5. 需要撤销时，点「卸载并还原原厂」并确认重启。工具会按首次安装记录恢复当时的校色档和显示模式。

<details>
<summary>macOS、Linux 或直接在掌机上使用</summary>

- **macOS / Linux** 安装 Python 3.9 或更高版本和 ADB 后，在源码目录运行 `python3 src/fit8e_color_tool.py status`。确认 `Target Match` 一项为 `True` 后，用 `python3 src/fit8e_color_tool.py install` 安装并重启。
- **掌机端 Shizuku** 将 `scripts/fit8e_calib_shizuku.sh` 和 `assets` 中的校色 JSON 复制到 `/sdcard/Download/`。在 Shizuku 授权的终端运行 `sh /sdcard/Download/fit8e_calib_shizuku.sh install`，成功后手动重启。

</details>

## 工作原理

工具把这块面板的高通 QDCM 校色档写入 `/data/vendor/display/`。掌机重启后，显示服务加载校色档。显示芯片通过三维查找表把颜色映射到目标色域，用亮度曲线校正灰阶，并把 HDR 高光映射到内屏能显示的亮度范围。

在项目样机上，系统亮度设为 40% 时，51 个 sRGB 色块的平均色差（ΔE00）从出厂的 **3.77** 降至自然标准模式的 **0.46**。数值越小，实测颜色越接近目标颜色。[查看校色前后实测对比图](assets/Fit8E_Compare_Report.png)
