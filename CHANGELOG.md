# Fit8E-ColorCalib 更新记录

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
