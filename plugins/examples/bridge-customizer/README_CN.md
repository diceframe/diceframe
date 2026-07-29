# Bot Bridge 扩展示例

此示例演示 `bot-extension` 的三种能力：

- `before_message`：新增“插件测试”命令；
- `after_result`：给普通文字回复增加可配置后缀；
- `render`：把 QQ 结构化卡片替换为插件生成的 PNG。

开发时将此目录复制到 `plugins/bridge-customizer/`，再在设置页启用。示例默认不修改回复，也不替换图片。

动态文件必须写入 `DICEFRAME_PLUGIN_DATA_DIR`。插件只能返回该目录下的 PNG、JPEG、WebP 或 GIF；DiceFrame 会检查路径、格式、大小和运行状态。扩展失败或返回 `handled: false` 时，Bot Bridge 会继续使用内置展示。
