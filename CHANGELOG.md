# Fit8E-ColorCalib 更新记录

## 0.1.2

以下变更相对于 0.1.1（`ed6927c`）。

### 新增与校色

- 新增 **Mode 3 自适应鲜艳（`display_color_mode = 3` / `DisplayColorSetting: Enhanced`）**，在 SM8750P QDCM V3 档案中原生打通 `RenderIntent = 2 (Enhance)` 硬件管线（`sRGB_Enhance`、`P3_Enhance`、`HDR_Enhance` 及 Custom 3D LUT 槽位 `3` / `4`）。
- Mode 3 严格遵循 **CIE 2015 2°** 标准：中性灰轴与 Mode 0 逐点一致，对 sRGB 内容在 OKLab 空间执行肤色保护与平滑广色域扩展（实测 sRGB 容积约 125% / DCI-P3 92%），并在 Display P3 与 HDR10 内容下自动保持完整宽色域与 PQ 映射。
- 默认 6500 K / 色调 0 档案（含 Mode 3）SHA-256 为 `18987308cfb4602159ee0a2f46bed55079663a3062ef6ef35a30c7f8db5beb9f`；5500–7500 K 色温滑块与 -10..+10 红绿色调微调会同步重编译全部 5 张 3D LUT 及 `HDR` / `HDR_Enhance` 映射表，各模式白点与红绿偏移方向一致。
- GUI 新增第 4 张「自适应鲜艳 / Adaptive」实时切换卡片，CLI 与 `fit8e_calib_shizuku.sh` 均支持 `mode 0|1|2|3` 及 7 模式完整结构校验；兼容从 0.1.1 与 0.1.0 历史内置档一键升级和还原。
- 新增 **AYASettings「Root 脚本」免电脑掌机端方案**（`src/root_script.py`）：单文件自包含 `.sh`（gzip+base64 压缩后约 240 KB，远低于 1 MiB 限制），内置完整机型 / `ar14` 面板校验、SHA-256 核对、原子备份与 SELinux 标签、安装/卸载后手动重启提示，以及误点「逐行运行」安全防呆。
- 桌面 GUI 新增 **「导出 Root 脚本 / Export Root .sh」**，CLI 新增 `export-script` 与 `export-presets`；Release 构建与 GitHub Actions 发布流水线同步产出并上传独立的 `Fit8E-ColorCalib-v0.1.2-root-scripts.zip` 预设包（含 6500K D65 / 6000K / 6800K / 7100K 安装脚本、Mode 0/1/2/3 实时切模式脚本、状态查询与卸载还原脚本），Windows 便携包内亦附带同款 `root-scripts/`。
- 中英文 README 补充 CIE 2015 2° 实测效果简图以及电脑 USB / 掌机 Root 脚本操作示意图（`assets/docs/`），移除 `assets/` 中与 `reference/` 重复的旧版 0.1.0 对比图，并在发布工作流中加入自动化回归测试门禁。

### 修复

- 桌面端在 OEM `xsu` 提权回退时改为推送临时脚本执行，避免 `xsu` 丢弃标准输入导致静默空操作，同时拒绝无输出的空成功返回。
- 修复 root 回退创建 `.fit8e-colorcalib` 目录权限过严导致普通 shell `probe` 无法读取激活状态的问题（显式设置 `0755` / `0644`），并在 `read_record` 中对不可读的 root 属主 `record` / `original` 返回退出码 `73`，使掌机端 Root 脚本安装后与电脑端非 root ADB 升级/还原无缝互通。
- 修复 `fit8e_calib_shizuku.sh` 在 `install --sha256` / `uninstall --force` 不带位置参数时误把标志当作路径的问题；统一拒绝 UTF-8 BOM。
- 修复安装/卸载触发重启后立刻 `probe()` 的竞态、禁用按钮悬停底色残留、英文界面局部中文文案，以及 `build_qdcm_v3.py` 模板自覆盖与非原子写入、`build_windows_release.py` 构建失败返回值问题。

### English summary

Version 0.1.2 adds hardware **Mode 3 Adaptive Vibrant** (`display_color_mode = 3`, `RenderIntent = 2` with `sRGB_Enhance`, `P3_Enhance` and `HDR_Enhance`) calibrated under CIE 2015 2° with OKLab skin-tone protection and full 5500–7500 K CCT / -10..+10 green-magenta tint synthesis across all five 3D LUTs and both HDR10 curves. It also adds self-contained **AYASettings Root Scripts (`.sh`)** for PC-free handheld installation (with manual reboot prompts, line-by-line guard, GUI/CLI custom `.sh` export, and a standalone preset script release ZIP for 6500/6000/6800/7100 K plus live mode switchers), refreshes both READMEs with CIE 2015 2° summary and step-by-step visuals, adds CI regression test and Root Script release publishing, and hardens `xsu` elevated execution, root/non-root state interoperability, helper flag parsing, BOM validation, atomic compiler writes, and GUI/CLI mode 3 support while keeping single-step upgrades and rollback from 0.1.1/0.1.0 profiles.

## 0.1.1

本版对照 GitHub 发布标签 [v0.1.0（05b09cb）](https://github.com/lx081081/Fit8E-ColorCalib/commit/05b09cbf965ac8b648b9d6e3e7ddfb17c27d5674) 和本次核对的 [main（4ed3518）](https://github.com/lx081081/Fit8E-ColorCalib/commit/4ed3518853cf24f962caa0bea66174bad60e9459)。main 在发布标签之后移除了旧审查报告。以下变更均相对于该 main。

### 校色

- 5500–7500 K 滑块、四个预设、色调、预览、导出和安装统一采用 CIE 2015 2° 日光白点。预设色调归零，手动调色温会保留当前色调。
- 6500 K / 色调 0 直接输出样机已安装并复测的 D65 文件。其 SHA-256 为 `afb9547872edc21799b17d6e90f5df704cf4c3be069c1c893354210e4c28e8b6`。
- 其他白点使用本轮原厂 105 色测量、屏幕灰阶和双观察者转换矩阵重新编译 sRGB、P3 和原生色域表。HDR 使用同一 P3 表，亮度参数以这份 D65 档为基准调整。
- 新增本轮 SDR 测量记录。原厂与默认档的白点 Δu′v′ 分别为 0.00955 与 0.00022。其他预设及独立 P3、HDR10 信号的真机精度仍需各自的光学测量。[数据](assets/measurements/verification.json)

### 修复

- 改进掌机对大校色表的解析，安装和卸载的 ADB 等待由 40 秒延长为 120 秒。超时会提示刷新核对，避免重复提交。
- 短暂锁竞争会重试，持续占用、权限问题和缺少锁工具分别报告。并发操作继续受同一把锁保护。
- 跨旧版卸载会识别备份中已知的历史内置 6500 K 档，一次恢复到固件默认。其他原有配置仍按安装记录和校验和还原。
- 安装时记录开机标识和档案摘要，区分待重启、已重启和旧安装待确认。修正显示服务公布色彩模式后，界面提前显示「已生效」的问题。
- 默认档通过 SHA-256 识别，避免把旧目录中的同名文件当作 0.1.1 的实测 D65。自定义 JSON 的界面显示文件白点来源，并清除内置档的预览读数。
- 导出会保护内置档，写入失败时保留已有的导出文件。自定义测量生成器会报告无效的 RGB 增益、亮度范围和不可逆的原色矩阵。
- Windows 构建使用独立临时目录，成功后才替换同版本交付物。便携包和源码包收录更新记录、新模型和测量证据。

### English summary

Version 0.1.1 moves the nominal daylight slider, tint and presets to CIE 2015 2°. The default reproduces the verified D65 bytes. It also fixes slow device validation, lock and timeout diagnostics, layered legacy rollback, premature activation reporting and stale external defaults. The source and portable packages include the new measurement evidence.
