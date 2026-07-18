# Echo Tool

This is a minimal DiceFrame `tool` plugin demonstrating:

- Structured tool registration with `src.plugin_sdk.ToolRuntime`.
- JSON arguments described by `input_schema`.
- Normal settings injected through a declared environment variable.
- Text and structured-data results.

Copy this directory to `plugins/echo-tool/`, choose **Settings → Plugins → Rescan local folder**, enable it, then test it from the **Tools** tab with:

```json
{"text": "Hello, DiceFrame"}
```

Standard output is reserved for JSON-RPC protocol messages; write diagnostics to standard error instead of using ordinary `print()` logging. Calls time out after 30 seconds and each protocol message is limited to 256 KB. Tool plugins still execute as third-party processes with the current operating-system user's permissions, so install only trusted sources.
