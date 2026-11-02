# Documentation map

Start here when developing or using DiceFrame. The source in the checked-out revision and its architecture documents describe current behavior. A plan or archived handoff does not override them.

## Current contracts

- Architecture: [English](ARCHITECTURE_EN.md) · [简体中文](ARCHITECTURE_CN.md)
- [Engineering rules](ENGINEERING_RULES.md): ownership, authority, data, extensions and contract testing
- [Accepted architecture decisions](adr/README.md): why durable boundaries were chosen
- Content: [Adventure bundle](adventures/ADVENTURE_BUNDLE_EN.md), [lorebook adapter](LOREBOOK_ADAPTER_GUIDE.md), [content synchronization](content-sync/CONTRACT.md)
- Plugin development: [English](plugins/EXTENDING_EN.md) · [简体中文](plugins/EXTENDING_CN.md)

## User guides

| Topic | English | 简体中文 |
| --- | --- | --- |
| Installation and privacy | [Install](en/install.md) | [安装](zh/install.md) |
| Plugins and chat adapters | [Plugins](en/plugins.md) | [插件与群聊](zh/plugins.md) |
| AI and interface settings | [Guide](en/ai-settings.md) | [指南](zh/ai-settings.md) |
| Content and play, including DF Agent | [Guide](en/content-and-play.md) | [指南](zh/content-and-play.md) |
| Story plans, skill development and finales | [Guide](en/run-story-and-finale.md) | [指南](zh/run-story-and-finale.md) |
| Updates and recovery | [Guide](en/updates.md) | [指南](zh/updates.md) |
| Rules and dice | [Guide](en/rules-and-dice.md) | [指南](zh/rules-and-dice.md) |
| Player Direct Connect (experimental) | [Guide](en/direct-connect.md) | [指南](zh/direct-connect.md) |
| Translation maintenance | [Policy](en/translations.md) | [政策](zh/translations.md) |
| Lorebook management | [Guide](LOREBOOK_USER_GUIDE_EN.md) | [指南](LOREBOOK_USER_GUIDE_CN.md) |
| Adventure creator starter | [Lantern Harbor](../plugins/examples/starter-adventure/README.md) | [灯火港湾](../plugins/examples/starter-adventure/README_zh-CN.md) |
| User guide | [User guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md) | [用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md) |
| Docker deployment | [Docker deployment](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) | [Docker 部署](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/deploy.md) |
| Standalone WebUI | [Standalone WebUI](https://diceframe.com/en/docs?doc=standalone) | [独立部署 WebUI](https://diceframe.com/docs?doc=standalone) |
| Plugin development | [Plugin development](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) | [插件开发](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md) |
| Plugin index and review | [Plugin registry](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-registry.md) | [插件索引与审核](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md) |
| Bot Bridge core | [Bot Bridge Core](https://github.com/diceframe/diceframe-content/blob/main/docs/en/bot-bridge-core.md) | [Bot Bridge 核心](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/bot-bridge-core.md) |

## Contributing / 交流与反馈

Bug reports and feature requests belong in [GitHub Issues](https://github.com/diceframe/diceframe/issues). Code and documentation contributions should use a Pull Request after reading [CONTRIBUTING.md](../CONTRIBUTING.md).

GitHub maintains the [Contributors](https://github.com/diceframe/diceframe/graphs/contributors) page from commit history. It may include CI, release, and coding-assistant bot accounts; those records are kept accurate and are not intended to be a manually curated list of human developers.

问题反馈和改进建议请优先通过 [GitHub Issues](https://github.com/diceframe/diceframe/issues) 提交，代码贡献欢迎发起 PR。提交前请阅读 [贡献指南](../CONTRIBUTING.md)。

QQ 交流群：1060613588

感谢所有提交代码、文档、测试、问题反馈和内容改进的朋友。贡献者由 GitHub 根据提交记录自动统计，详见 [Contributors](https://github.com/diceframe/diceframe/graphs/contributors)。其中可能包含 CI、发布流程和代码助手等机器人账号；它们保留在自动统计中，但不代表人工开发者名单。

贡献规则和本地验证方式见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## Project structure / 项目结构

```text
.
├── web_server.py          # WebUI 服务入口
├── scripts/               # 维护检查和开发调试脚本
├── frontend-v2/           # Vue 3 + TypeScript 前端源码
├── static-v2/             # 前端构建输出入口
├── src/
│   ├── engine/            # 游戏状态、骰子、战斗、剧情追踪
│   ├── commands/          # 回合处理、标签解析、状态应用
│   ├── generation/        # 世界、规则、角色生成
│   ├── lorebook/          # 世界书存储与匹配
│   ├── memory/            # 长期记忆、摘要、embedding
│   ├── rules/             # JSON 规则系统
│   ├── webui/             # HTTP API、routes、services
│   └── bots/bridge_core/  # 通用聊天底座（各平台适配器复用）
├── plugins/               # 内置/示例插件（随版本分发；用户安装的插件在 data/plugin-packages/）
├── prompts/               # GM 系统提示词
├── templates/             # 内置规则和世界模板
└── docs/                  # 双语文档：更新、规则与骰子、玩家直连；插件开发等见 diceframe-content
```

## License

This project is licensed under the [GNU Affero General Public License v3.0](../LICENSE).

You may use, modify, and distribute DiceFrame under the terms of the AGPL-3.0. If you distribute a modified version, or make a modified version available as a network service, you must provide the corresponding source code as required by the license.

本项目采用 [GNU Affero General Public License v3.0](../LICENSE) 授权。

你可以在 AGPL-3.0 的条款下使用、修改和分发本项目。若你分发修改后的版本，或将修改后的版本作为网络服务提供给他人使用，应按 AGPL-3.0 的要求公开相应源码。

## Documentation ownership and UI changes

This repository owns version-bound configuration, feature behavior and extension contracts. The public [documentation repository](https://github.com/diceframe/diceframe-content/tree/main/docs) owns the everyday user manual, deployment and voice instructions. The website displays that content; it does not maintain another copy of the manual.

When changing navigation, labels, defaults or an operation's prerequisites:

1. Check the relevant English and Simplified Chinese guides in both repositories against the implemented routes, translated labels and service behavior. Keep the two languages equivalent; community translations follow the [translation policy](en/translations.md).
2. Update entry paths and examples together. Link to the owning contract rather than copying configuration schemas into several manuals.
3. Check relative links and any affected screenshots. Capture screenshots only with demonstration data, without credentials, private saves or personal information.
4. Base current instructions on merged implementation. Mark planned behavior as a proposal; coordinate related documentation PRs without presenting an unmerged feature as available. Before merging a docs-only follow-up, recheck the target branch's UI for changes.

## Internal notes and history

[Internal maintainer notes](internal/README.md) (`internal/`) hold construction plans, the content-architecture vNext master plan and dated design drafts. They describe local, possibly unreleased work: branch names, PR sequencing and unfinished boundaries there are internal coordination details, not product commitments. Do not link them from user-facing guides, the website or the public documentation repository, and do not present an unmerged feature they describe as available.

[Archived plans and handoffs](archive/README.md) preserve historical decisions and unfinished inventories. Their branch names, PR ordering and test counts are historical. Temporary review reports, local paths, approval drafts and execution logs belong outside the published documentation; report their outcomes in the working conversation.

When a change lands, update its user guide and affected contract. Keep a durable decision in an ADR, keep a proposal explicitly marked as a proposal, and archive superseded execution plans. Do not maintain a second manual catalog of test names or copy session transcripts into `docs/`.
