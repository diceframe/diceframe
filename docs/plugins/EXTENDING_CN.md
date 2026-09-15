# Plugin 扩展路径

本文用于快速判断“一个新能力应该接在哪里”。完整的发布、商店和审核规则仍以 [DiceFrame Content 插件开发指南](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md) 为准。

所有插件都从 `plugin.json` 开始。`schema_version`、`id`、`name`、`version` 和 `plugin_type` 是核心身份；由宿主先校验 manifest、权限和运行时 descriptor，再将数据交给应用。不要把 TypedDict 当作运行时校验。

## A. Content Pack

1. 使用 `plugin_type: "content-pack"`，新包优先使用 `content_schema_version: 2` 和 typed locale。
2. 在 `contributes` 中声明 rules、world templates、characters、NPC、items、spells、classes 或 maps 的相对路径。
3. canonical ID 与显示名分离；Locale 只翻译展示字段，不改 identity 和 mechanics。
4. 规则货币结构用 `currency_system`（`schema_version: 2`）声明：`base_unit` 是 canonical 整数的计数单位（rate 必须为 1），`display_unit` 是默认展示单位，units 的 rate 为正整数。加载/安装时经 `RuleBundleLoader` → `validate_currency_system` 校验，非法声明直接失败；只有 `currency` 名称的旧规则按 legacy rate=1 兼容，不按名称猜单位。示例见 `plugins/examples/starter-content-v2/content/rules/moonlight.json`。
5. 参考 `plugins/examples/starter-content-v2/`，并为内容校验、locale fallback 和重复 ID 行为添加测试。
6. 打包前运行：

```bash
python scripts/package_plugin.py path/to/plugin
```

这条路径不需要修改 `PluginHost`。

## B. Tool Plugin

1. 使用 `plugin_type: "tool"` 和安全的 `entrypoint` 数组；宿主要求 `tool.execute` 权限。
2. 使用 `src.plugin_sdk.ToolRuntime`，通过 `runtime.tool(...)` 注册 name、title、description 和 object `input_schema`。
3. handler 接收 `arguments` 和 `context` JSON object，并返回 JSON object。不自建另一套 stdio 协议。
4. 参考 `plugins/examples/echo-tool/`，测试 initialize descriptor、`tool.call` 和无效输入拒绝。

## C. Provider Capability

1. 使用 `plugin_type: "provider"`；需网络访问时由 manifest/配置声明并接受宿主权限处理。
2. 使用 `src.plugin_sdk.ProviderRuntime`，通过 `runtime.capability(kind=..., version=...)` 注册能力。当前 SDK 对外方法别名为 `generate`。
3. `kind` 是稳定能力命名空间，handler 接收 `arguments` / `context` 并返回 JSON object。
4. 新增一个合法 capability kind 不修改 `PluginHost`；增加 SDK/插件实现和行为测试即可。

## D. Bot Extension

1. 使用 `plugin_type: "bot-extension"` 和 `src.plugin_sdk.BridgeExtensionRuntime`。
2. `runtime.extension(...)` 可声明 `before_message`、`after_result`、`render` 阶段，以及 priority、timeout、platforms 和 kinds。
3. handler 返回 JSON object；可以返回 `handled`、替换后 `payload` 或 `outputs`。输出只使用宿主校验的 text/card/image 结构，不绕过路径、数量和长度限制。
4. 参考 `plugins/examples/bridge-customizer/`，测试阶段筛选、优先级、平台/类型过滤和 output validation。

## E. 真正新增 Plugin Type

先确认它不只是现有 content-pack、tool、provider 或 bot-extension 的新 capability。只有当 process mode、lifecycle 或安全语义真的不同时，才新增 plugin type。

需要逐项检查：

- `src/plugin_host/support.py` 的 support/process/permission/contribution/cleanup descriptor；
- 如果是 RPC type，`src/plugin_host/capabilities.py` 的 initializer；
- permission policy、package/lifecycle cleanup 和对外 support metadata；
- manifest、security、lifecycle、SDK/descriptor 行为测试；
- 如果用户会看到该类型，再更新前端 metadata 和文档。

## 提交前

至少运行所改插件域的 pytest、`python -m ruff check src/`、`python -m mypy` 和 `python scripts/package_plugin.py ...`。测试保护 descriptor、RPC、权限、路径安全和兼容行为，不锁死 helper 名称或文件形状。
