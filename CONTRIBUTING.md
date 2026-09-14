# Contributing to DiceFrame

感谢你为 DiceFrame 提交问题、文档、测试、代码或内容贡献。

## 从哪里开始

- 使用前先查阅 [README](README.md) 和 [用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md)。
- Bug、需求和架构讨论优先提交到 [GitHub Issues](https://github.com/diceframe/diceframe/issues)。
- 代码或文档改动请发 Pull Request，并在描述中说明动机、行为变化和验证方式。
- 插件和内容包请先看本仓库的 [Plugin 扩展路径](docs/plugins/EXTENDING_CN.md)，并遵循 [插件开发指南](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md) 及 [插件审核规则](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md)。
- Android 客户端在独立仓库 [diceframe-mobile](https://github.com/diceframe/diceframe-mobile)，其 Bug、需求与代码贡献请提交到该仓库；APK 等发布物见 [Releases](https://github.com/diceframe/diceframe-mobile/releases)。

## 架构与工程规范

当前实现的架构事实来源：

- [docs/ARCHITECTURE_CN.md](docs/ARCHITECTURE_CN.md)
- [docs/ARCHITECTURE_EN.md](docs/ARCHITECTURE_EN.md)

修改代码时遵循：

- [docs/ENGINEERING_RULES.md](docs/ENGINEERING_RULES.md)

测试与高风险契约原则：

- 见 [docs/ENGINEERING_RULES.md](docs/ENGINEERING_RULES.md) 的 Testing 章节（§15）

这些规范不会冻结 DiceFrame 的当前架构。大型功能、模块拆分、迁移、breaking change 和架构重做都允许；要求是明确处理受影响的用户数据、兼容性、权限、持久化 identity、测试和文档。

## 修改原则

- 保持 Web API 字段向后兼容；如确需 breaking change，应明确版本、迁移、兼容或拒绝策略。
- 新功能同时补齐必要的测试、用户手册和迁移说明。
- 默认复用现有分层、服务和共享 helper；如现有架构不再合适，可以通过明确的架构改动重新设计。
- 新增行为应放在明确的 owning module；页面壳、route、WebAPI/facade 等编排层以组合和委托为主，不应因为方便访问全局状态而持续吸收独立业务职责。
- 存量大型/高耦合模块按计划渐进拆分，不要求贡献者在无关 PR 中顺手重构；但已有技术债也不应被当作新增同类耦合的先例。
- 新的 provider、ruleset、plugin、transport 等实现优先复用 capability / adapter / registry / generic connector，而不是把具体实现分支追加到通用路径。
- 不提交运行时存档、个人数据、API Key、构建产物或本机配置。

## Pull Request

DiceFrame 不设置 PR 最大行数或最大文件数。

优先：

> 最小完整改动，而不是最小代码行数。

如果 migration、backend、frontend、tests 和 docs 构成一个不可合理拆分的完整变化，可以放在同一个 PR。

如果两个改动能够独立产生价值、独立验证、独立回滚，则应尽量分开。

PR 模板中的风险勾选用于提醒 reviewer 关注 API、存档、权限、多人、规则和架构等风险面，不是额外审批流程。

## 本地验证

后端测试完全离线运行，测试使用临时目录和 fake 外部服务，不需要 API key：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=src --cov-report=term-missing
```

只运行一组测试时可以指定文件或关键字：

```bash
python -m pytest -q tests/integration/test_permissions.py
python -m pytest -q -k economy
```

新增 skip 前应说明外部条件，并优先使用明确的 pytest marker，不能用 skip 掩盖产品回归。

前端改动至少运行：

```bash
cd frontend-v2
npm run typecheck
npm run lint
npm test -- --maxWorkers=1
npm run build
```

后端改动请按仓库现有测试配置运行对应的 Python 测试；涉及真实浏览器交互时，再补充 Playwright 验证。

Pull Request 上的 Browser smoke 会保留完整日志，但浏览器或运行环境波动不会单独阻塞贡献；类型检查、单元测试、构建和后端检查仍是合并门槛。合并到 `main` 前会再次严格执行浏览器冒烟。

## AI-assisted development

允许使用 Codex、Claude、ChatGPT 等工具辅助开发。

AI 生成代码与人工代码遵守同样的架构、数据、安全和测试要求。提交者仍需要理解实际改动，并验证 AI 没有凭空创造字段、API、兼容行为或迁移语义。

AI 辅助修改在提交前应检查最终 diff / changed-file list，移除与当前任务无关的测试、注释、日志、格式化或其它工作区残留；AI review 的建议应先验证是否适用于当前仓库，而不是机械执行。

## 贡献者名单

GitHub 的 [Contributors](https://github.com/diceframe/diceframe/graphs/contributors) 页面自动按提交统计贡献，因此可能包含 CI、发布和代码助手等机器人账号。这是提交归属的准确记录，不等同于人工开发者名单，也不需要手动维护。

如果未来需要一份只列人工贡献者的鸣谢名单，应单独维护 `CONTRIBUTORS.md`，并在获得本人同意后添加；不要修改 Git 历史来处理机器人记录。
