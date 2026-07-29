# Bot Bridge 共享核心

中文 | [English](BOT_BRIDGE_CORE_EN.md)

Bot Bridge 用来把 DiceFrame 接到聊天平台。NapCat/QQ、Discord、Telegram 等适配器只处理各平台的消息收发和展示，跑团逻辑统一交给 DiceFrame。

代码位于 `src/bots/bridge_core/`。

## 它负责什么

平台无关的跑团业务，已内置：

- **HTTP API client**（`client.py`）：调用 DiceFrame WebUI 的 REST 接口（建/绑游戏、提交行动、掷骰、支付、推进等）。
- **会话与玩家映射 store**（`store.py`）：把平台群/频道 + 用户映射到 DiceFrame game_key 与 player uid，持久化为 JSON。
- **命令前缀与触发策略**（`triggers.py`）：识别命令、过滤触发。
- **通用命令匹配**（`commands.py`）：解析并路由命令到业务。
- **语言解析**（`language.py`）：规范化对局语言，在首次绑定前识别明确的中英文命令。
- **通用文本 presenter**（`presenters.py`）：把业务结果渲染成平台可发的中英文纯文本，文案支持 `command_prefix` 参数。
- **通用业务调度**（`service.py` 的 `DiceFrameBridgeService`）：串起 client / store / commands / presenters，提供给适配器调用。

## 适配器只负责什么

- 读取平台消息、提取文本、识别平台用户与聊天流
- 处理平台配置、插件生命周期、发送回复
- 平台专属能力，例如 NapCat 的图片卡片、私聊投递、群事件同步

平台消息进来后，适配器转成 `BridgeInput` 交给 `DiceFrameBridgeService`，拿到响应文本再发回平台。

HTTP 客户端通过 `X-Bot-Token` 请求头鉴权。DiceFrame 托管的插件由宿主生成并注入各自独立的内部 Token；只有完全独立运行的外部桥接才从“设置 → Bot API”复制全局 Token。连接测试调用 `/api/bot/ping`，会同时验证地址和 Token，不依赖 QQ / NapCat 是否启用。

## 语言

Bot 绑定成功时，DiceFrame 会把当前对局语言一并返回并保存在聊天流绑定中。之后的帮助、状态、前情、地图、支付、建卡提示和错误信息都按该语言显示；旧绑定没有语言字段时继续使用中文。

目前共享核心和 QQ / NapCat 支持中文与英文命令。英文对局可使用 `help`、`join`、`status`、`recap`、`map`、`roll`、`pay`、`advance`、`away`、`back`、`create character` 等命令，行动正文仍可直接使用自然语言。语言以对局设置为准，不会根据每条剧情行动反复猜测。

## 触发策略

默认优先支持显式前缀：

- `跑团 ...`
- `/df ...`
- `/diceframe ...`

是否支持 `@机器人` 后的裸命令由适配器决定。若平台的 `@机器人` 会触发额外主回复或干扰正常聊天，必须默认 `prefix_only`；兼容旧用法时只能通过配置显式打开（例如 `mention_bare`），帮助文案仍优先引导用户使用前缀。

## 新平台接入

Discord、Telegram 等新适配器不需要重写跑团流程。通常只需完成：

1. 把平台事件转成 `BridgeInput`，填写频道、用户、文本、是否提及 Bot 等字段。
2. 调用 `DiceFrameBridgeService`。
3. 把 `BridgeResult` 的回复发送回平台。
4. 按需增加平台专属展示，例如 Discord slash commands、embeds、私信或权限映射。

若平台需要富卡、按钮或主动同步，可以像 QQ 一样在适配层复用 `commands` 和 `presenters`；支付归属、角色认领、推进权限、语言和 API 调用仍由共享层处理。

## 插件扩展

DiceFrame 的 `bot-extension` 进程插件可以在不修改 `bridge_core` 源码的情况下扩展聊天体验：

```text
平台消息
→ before_message（新增命令、修改或拦截消息）
→ 内置 Bridge / 游戏核心
→ after_result（修改展示结果）
→ render（替换文字、图片或卡片）
→ 平台发送
```

NapCat 通过宿主注入的内部 Bot Token 调用扩展接口；MaiBot 插件会先从 `/api/bot/ping` 检测协议，支持时调用相同接口，不支持时保持旧逻辑。扩展失败、超时或没有处理时均回退内置展示。

插件输出可使用 `text`、`card` 和 `image`。动态图片必须写入插件专属运行目录，由 DiceFrame 验证路径、格式和 10 MB 大小限制后提供鉴权读取。详细协议和示例见 [插件开发指南](PLUGIN_DEVELOPMENT_CN.md#711-bot-bridge-扩展插件)。

扩展只控制聊天命令和展示。角色状态、骰点、余额、支付确认与存档修改仍由 DiceFrame 游戏核心决定。

## 当前进度

- NapCat/QQ：已用 `bridge_core` 的 client / store / 命令匹配 / presenters；富卡、私聊、轮询同步等平台能力仍保留在 QQ 适配层。
- 共享核心和 QQ / NapCat 的主要玩家文案、帮助与命令均支持中文和英文。
- `presenters` 的命令文案支持 `command_prefix`；QQ 按平台提及方式展示，通用服务中文默认 `跑团`、英文默认示例使用 `/df`。
- 插件管理：Web 设置页支持安装 zip、卸载插件；插件包标准见 [PLUGIN_DEVELOPMENT_CN.md](PLUGIN_DEVELOPMENT_CN.md)。
- Bot Bridge 扩展：支持 `before_message`、`after_result`、`render`，并已接入 QQ / NapCat 与 MaiBot 外部桥接。
