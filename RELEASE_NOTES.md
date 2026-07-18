# DiceFrame v1.3.0

## 中文

本版本完成了插件系统的第一阶段底座，并加强了插件分发、Bot 接入和项目验证流程。

### 新增

- 新增 Tool Plugin Runtime v1：插件可通过独立子进程注册并执行结构化短任务。
- 新增版本握手、JSON Schema 参数校验、30 秒调用超时、消息大小限制和错误隔离。
- 插件设置页新增“工具”页面，可查看工具说明、填写参数并手动确认执行。
- 新增公开插件 SDK 和 `echo-tool` 中英文示例。
- 支持社区插件独立维护源码仓库，商店从正式 GitHub Release 固定到精确提交安装。
- 支持本地 `.dfplugin` 安装包、安装安全限制和风险分级更新策略。

### Bot 与安全

- 内置 QQ / NapCat 不再需要用户填写 DiceFrame Bot Token；宿主会为托管插件生成独立内部 Token。
- 外部 MaiBot Bridge 等程序仍可从“设置 → Bot API”获取服务地址和全局 Token。
- 加强插件权限、密钥隔离、安装路径检查、SSE 鉴权和发布包隐私边界。
- 增加后端、前端和真实浏览器自动检查。

### 当前限制

- AI 尚不能自动选择并调用工具插件；当前由用户在插件设置页手动调用。
- 长任务进度、取消、导入导出插件和模型 Provider 插件仍在后续计划中。
- 进程型第三方插件仍具有当前系统用户权限，只应安装可信来源。

## English

This release delivers the first usable plugin-runtime foundation and strengthens plugin distribution, bot integration, and automated validation.

### Added

- Tool Plugin Runtime v1 for registering and executing structured short-running tasks in isolated child processes.
- Protocol handshakes, JSON Schema input validation, 30-second timeouts, message-size limits, and error isolation.
- A Tools page in plugin settings for inspecting schemas and manually confirming tool calls.
- A public plugin SDK and bilingual `echo-tool` example.
- Community-owned plugin repositories installed from exact commits referenced by formal GitHub Releases.
- Local `.dfplugin` packages, archive safety limits, and risk-based update behavior.

### Bot and security

- Built-in QQ / NapCat no longer requires a manually entered DiceFrame Bot Token; managed plugins receive separate internal tokens.
- External programs such as MaiBot Bridge can still obtain the service URL and global token from Settings → Bot API.
- Stronger plugin permissions, secret isolation, package-path validation, SSE authentication, and release privacy boundaries.
- Expanded backend, frontend, and real-browser CI coverage.

### Current limitations

- AI does not yet select or invoke tool plugins automatically; tools are currently called manually from plugin settings.
- Long-task progress and cancellation, import/export plugins, and model-provider plugins remain planned work.
- Third-party process plugins still run with the current operating-system user privileges and should only be installed from trusted sources.
