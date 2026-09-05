# Codex / AI 协作入口

修改代码前，按任务范围阅读：

1. 架构事实来源：
   - [docs/ARCHITECTURE_CN.md](docs/ARCHITECTURE_CN.md)
   - [docs/ARCHITECTURE_EN.md](docs/ARCHITECTURE_EN.md)
2. 工程修改规则：
   - [docs/ENGINEERING_RULES.md](docs/ENGINEERING_RULES.md)
3. 涉及权限、存档、迁移、多人、规则等高风险行为时：
   - [docs/ENGINEERING_RULES.md](docs/ENGINEERING_RULES.md) 的 Testing 章节（§15）
4. 涉及已有重大架构决策时：
   - [docs/adr/](docs/adr/)

关键原则：

- 当前架构是事实来源，不是不可改变的路线图。
- 可以做大型重构、拆分、迁移和 breaking change；必须显式处理受影响的 contract。
- 不要把翻译后的 display name 当作 canonical identity。
- Locale 不得无意改变 mechanics。
- compatibility 应留在明确边界，不要散回正常业务逻辑。
- **应用 AI 配置只支持新格式**：共享服务商目录 `ai_providers`、独立凭据 `ai_provider_key_<id>` 与能力级 `*_provider_ref`。禁止新增或恢复旧直填地址、密钥、API 格式及旧 AI 环境配置回退；旧字段更新必须明确拒绝，不自动迁移、不猜测创建服务商。
- 缺失或失效的服务商引用应使对应能力保持未配置或停用，不能启用残留旧值；删除服务商时同步清理引用与凭据。TTS `browser`、`edge-tts` 和 ASR `disabled` 无需引用，本地服务商允许空密钥；需要远端地址的 TTS（含 `gpt-sovits`）与 ASR 必须解析有效引用。
- 引用解析后传给服务的内部连接参数仍是合法运行时契约。此新配置规则不取消当前仍有效的存档、规则、插件和业务数据契约；移除其它兼容前须核实上游生产与投影路径，并覆盖拒绝或转换行为。
- specific ruleset 不应反向污染 generic engine。
- migration correctness > migration completeness；无法证明安全时 fail closed。
- 系统模板不得无条件覆盖用户数据。
- AI 可以改架构，但不能靠猜测创造字段、API 或迁移语义。
- 新增行为前先识别 owning module；不要仅因为 central View / route / facade 能拿到所有状态，就把独立职责继续堆进去。
- Orchestrator 负责组合与委托，feature/domain module 负责实现；存量肥大文件是渐进治理对象，不是新代码继续堆积的先例。
- 新 provider / ruleset / plugin / transport 实现优先通过 capability / adapter / registry / public contract 扩展，避免在 generic path 增加具体实现分支。
- 新 route 不得新增 `api._*` / `registry._*` 等私有成员穿透；新 service 优先使用显式依赖，不把完整 `WebAPI` 当 service locator。
- 提交前检查最终 changed-file list，确认没有其它任务、其它测试或 AI 工作区残留串入当前 PR。
