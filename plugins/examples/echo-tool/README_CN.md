# Echo Tool

这是一个最小的 DiceFrame `tool` 插件示例，用来演示：

- 通过 `src.plugin_sdk.ToolRuntime` 注册结构化工具。
- 使用 `input_schema` 描述 JSON 参数。
- 读取由配置 Schema 注入的环境变量。
- 返回文本内容和结构化数据。

将此目录复制为 `plugins/echo-tool/`，在“设置 → 插件”点击“重新扫描本地目录”，启用插件后可在“工具”页用以下参数测试：

```json
{"text": "你好，DiceFrame"}
```

工具进程的标准输出仅用于 JSON-RPC 协议。调试信息应写到标准错误，不能使用普通 `print()` 向标准输出写日志。单次调用默认超时 30 秒，单条协议消息上限 256 KB。工具插件仍是以当前系统用户权限运行的第三方进程，只应安装可信来源。
