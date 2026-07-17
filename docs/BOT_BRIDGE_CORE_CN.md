# Bot Bridge 共享核心

DiceFrame 聊天平台接入的共享核心层。新平台适配器（NapCat/QQ、Discord、Telegram 等）复用这一层，只实现平台自身的消息收发与展示，不重写跑团业务。

代码位于 `src/bots/bridge_core/`。

## 它负责什么

平台无关的跑团业务，已内置：

- **HTTP API client**（`client.py`）：调用 DiceFrame WebUI 的 REST 接口（建/绑游戏、提交行动、掷骰、支付、推进等）。
- **会话与玩家映射 store**（`store.py`）：把平台群/频道 + 用户映射到 DiceFrame game_key 与 player uid，持久化为 JSON。
- **命令前缀与触发策略**（`triggers.py`）：识别命令、过滤触发。
- **通用命令匹配**（`commands.py`）：解析并路由命令到业务。
- **通用文本 presenter**（`presenters.py`）：把业务结果渲染成平台可发的纯文本，文案支持 `command_prefix` 参数。
- **通用业务调度**（`service.py` 的 `DiceFrameBridgeService`）：串起 client / store / commands / presenters，提供给适配器调用。

## 适配器只负责什么

- 读取平台消息、提取文本、识别平台用户与聊天流
- 处理平台配置、插件生命周期、发送回复
- 平台专属能力，例如 NapCat 的图片卡片、私聊投递、群事件同步

平台消息进来后，适配器转成 `BridgeInput` 交给 `DiceFrameBridgeService`，拿到响应文本再发回平台。

## 触发策略

默认优先支持显式前缀：

- `跑团 ...`
- `/df ...`
- `/diceframe ...`

是否支持 `@机器人` 后的裸命令由适配器决定。若平台的 `@机器人` 会触发额外主回复或干扰正常聊天，必须默认 `prefix_only`；兼容旧用法时只能通过配置显式打开（例如 `mention_bare`），帮助文案仍优先引导用户使用前缀。

## 当前进度

- NapCat/QQ：已用 `bridge_core` 的 client / store / 命令匹配 / presenters；富卡、私聊、轮询同步等平台能力仍保留在 QQ 适配层。
- `presenters` 的命令文案支持 `command_prefix`；QQ 默认 `@我`，通用服务默认 `跑团`。
- 插件管理：Web 设置页支持安装 zip、卸载插件；插件包标准见 [PLUGIN_DEVELOPMENT_CN.md](PLUGIN_DEVELOPMENT_CN.md)。