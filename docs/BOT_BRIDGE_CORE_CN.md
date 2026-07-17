# Bot Bridge 共享核心

DiceFrame 聊天平台接入的共享核心层，供 NapCat/QQ 及后续 Discord/Telegram 适配器复用同一套跑团业务。

## 目标边界

`src/bots/bridge_core/` 是平台无关共享层，负责：

- DiceFrame HTTP API client
- 会话/玩家映射 JSON store
- 命令前缀与触发策略
- 通用命令匹配
- 通用文本 presenter
- 通用文本业务调度 `DiceFrameBridgeService`

平台适配器只负责：

- 读取平台消息、提取文本、识别平台用户和聊天流
- 处理平台配置、生命周期、发送回复
- 平台专属能力，例如 NapCat 图片卡片、私聊投递、群事件同步

## 触发策略

平台默认应优先支持显式前缀：

- `跑团 ...`
- `/df ...`
- `/diceframe ...`

是否支持 `@机器人` 后的裸命令，由平台适配器自行决定。若某个平台的 `@机器人` 会触发额外主回复或干扰正常聊天，必须默认使用 `prefix_only`；兼容旧用法时只能通过配置显式打开，例如 `mention_bare`，并且帮助文案仍应优先引导用户使用前缀。

## 当前接入状态

- NapCat/QQ：已改用 `bridge_core` 的 client、store、command matchers、presenters；富卡、私聊、轮询同步等平台能力仍保留在 QQ 适配层。
- `bridge_core.presenters` 的命令文案已支持 `command_prefix` 参数；QQ 默认 `@我`，通用服务默认 `跑团`。
- 插件管理：Web 设置页支持安装 zip、卸载插件；插件包标准见 `docs/PLUGIN_DEVELOPMENT_CN.md`。

## 后续清单

1. 将 QQ/NapCat 仍在适配层内的纯文本业务逐步迁移到 `DiceFrameBridgeService`，只留下卡片、私聊、事件同步等平台特性。
2. 若未来平台适配器需要独立分发，做一个可发布的 `diceframe_bridge_core` 包或发布时自动同步 `src/bots/bridge_core`，避免长期维护两份核心。
3. 为 `DiceFrameBridgeService` 增加仓库内单元测试，覆盖触发策略、掷骰确认、支付确认、绑定、加入、行动和推进。
4. 后续新平台必须先复用 `bridge_core`，只有平台消息收发和平台专属展示可以新写。
