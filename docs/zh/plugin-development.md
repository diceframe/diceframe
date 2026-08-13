# DiceFrame 插件开发指南

中文 | [English](../en/plugin-development.md)

本指南定义 DiceFrame 插件的通用包结构、manifest 标准、配置规则和各类插件的扩展边界。当前真正可用的是聊天桥接、Bot Bridge 命令/Hook/渲染扩展、内容包（包括只读地图定义、地点、图标和底图）、可选音色预设、安全主题变量与结构化工具。导入导出和通用 Provider 插件目前只有类型占位；TTS 已由核心提供浏览器、OpenAI 兼容和 GPT-SoVITS 适配器。

**重要：只有“当前支持状态”标为“已支持”或“部分支持”的能力，照本文开发后才会真实生效。标为“预留”的类型在开发目录中只能被识别和展示，不会接入对应业务流程，插件商店也不允许安装。插件 README 必须如实说明当前作用。**

## 1. 插件系统目标

DiceFrame 插件不只面向机器人接入。长期目标是让社区可以通过插件安装和分发这些能力：

- 聊天桥接：QQ/NapCat、Discord、Telegram 等。
- Bot Bridge 扩展：新增命令、修改回复、替换文字/图片/卡片展示。
- 内容包：规则、世界模板、角色模板、NPC、道具、法术、职业，以及可选的地图定义和素材。
- 主题：基于 v2 语义 token 的色板、字体、圆角和阴影风格包。
- 导入导出：SillyTavern/酒馆角色卡、世界书、Lorebook 等格式转换。
- Provider：LLM、Embedding、TTS、图片生成等外部服务接入。
- 工具：批量校验、备份、转换、生成器等辅助功能。

## 2. 当前支持状态

| 类型 | plugin_type | 当前状态 |
|------|-------------|----------|
| 聊天桥接 | `channel-adapter` | 已支持进程托管、配置、启停、HTTP API 调用 |
| Bot Bridge 扩展 | `bot-extension` | 已支持命令拦截、消息/结果 Hook、文字/图片/卡片渲染和失败回退 |
| 内容包 | `content-pack` | 已支持规则、世界模板、内容目录和只读地图贡献，支持把部分内容导入用户角色卡库/世界书 |
| 主题 | `theme` | 已支持选择和加载 v2 语义 token 主题 |
| 音色预设 | `voice-pack` | 可选：OpenAI 兼容 voice ID，以及 GPT-SoVITS 参考 WAV/提示文本 |
| 导入导出 | `import-export` | 预留：尚无统一导入导出任务 API，商店不允许安装 |
| Provider | `provider` | 预留：尚无 Provider 注册运行时，商店不允许安装 |
| 工具 | `tool` | 已支持进程握手、工具注册、结构化调用、超时和设置页手动测试 |

`content-pack`、`theme` 和 `voice-pack` 是声明型插件，可以没有后台进程。`channel-adapter`、`bot-extension` 和 `tool` 需要 `entrypoint`，由宿主作为独立进程托管。

## 3. 插件边界

- 插件以 `data/plugin-packages/<plugin-id>/` 为安装单位（用户安装）；内置/示例插件位于 `plugins/<plugin-id>/`。
- 插件安装包必须只包含一个 `plugin.json`。
- 插件不得直接读取或修改 DiceFrame 的 `data/` 存档；需要能力时应补正式 HTTP API 或类型专用注册流程。
- 插件禁止 `import src.webui`；缺少能力时按 `routes -> api -> services -> core` 补正式接口。
- 普通配置保存到 `data/plugins/<id>/config.json`。
- 敏感配置保存到 `data/plugins/<id>/secrets.json`，公开 API 只返回掩码。
- 插件运行数据应写入宿主传入的数据目录或 `data/plugins/<id>/`，不得写入插件源码目录。
- 卸载插件默认保留 `data/plugins/<id>/`，避免误删配置、令牌和用户数据。

## 4. 插件目录与分发格式

最小结构：

```text
<plugin-id>/
  plugin.json
  config.schema.json
  README_CN.md
```

安装后的统一结构是 `data/plugin-packages/<plugin-id>/`（用户安装）；本地开发可直接放仓库 `plugins/<plugin-id>/`。开源插件把上述文件直接放在独立 GitHub 仓库根目录，通过正式 GitHub Release 分发；作者不需要上传 ZIP，也不需要计算 SHA-256。

私下、网盘或离线分享使用 `.dfplugin` 文件。它是受限 ZIP 容器，内部只允许以下两种结构：

```text
plugin.json
config.schema.json
README_CN.md
```

或：

```text
<plugin-id>/
  plugin.json
  config.schema.json
  README_CN.md
```

本地安装只接受 `.dfplugin` 扩展名。安装器会拒绝绝对路径、`..` 路径穿越、符号链接、加密文件、重复路径和包含多个 `plugin.json` 的包。压缩包上限为 20 MB，解压后总大小上限为 100 MB，单文件上限为 25 MB，文件条目上限为 2048。覆盖同 ID 插件必须在 WebUI 显式勾选“覆盖同 ID 插件”。

## 4.1 从示例开始

仓库内置四个可复制的示例插件：

| 示例 | 路径 | 类型 | 用途 |
|------|------|------|------|
| Starter Content | `plugins/examples/starter-content` | `content-pack` | 规则、世界模板、角色模板、NPC、道具、法术、职业 |
| Paper Theme | `plugins/examples/paper-theme` | `theme` | 安全的 v2 语义 token 主题 |
| Echo Tool | `plugins/examples/echo-tool` | `tool` | 进程握手、工具注册、JSON 参数与结构化结果 |
| Bridge Customizer | `plugins/examples/bridge-customizer` | `bot-extension` | 自定义命令、结果 Hook、QQ 图片渲染 |

开发新插件的推荐流程：

1. 复制一个示例目录到新的插件目录，例如 `plugins/my-content-pack`。
2. 修改 `plugin.json` 的 `id`、`name`、`version`、`description`、`capabilities`、`permissions`。
3. 修改 `config.schema.json`，只保留实际需要的配置项。
4. 把内容、主题或入口代码放到插件目录内。
5. 运行打包命令：

```powershell
python scripts\package_plugin.py plugins\my-content-pack --overwrite
```

生成的 `.dfplugin` 位于 `dist/plugins/`，可在 WebUI “设置 -> 插件 -> 安装插件”中安装测试。开发时也可以把目录放到仓库的 `plugins/<id>/`，然后点击“重新扫描本地目录”。

打包脚本会复用宿主的 manifest、schema、权限和贡献资源校验，并拒绝 `__pycache__`、日志、数据库、符号链接和不安全路径。

## 5. plugin.json

`plugin.json` 必须是 UTF-8 JSON。示例：

```json
{
  "schema_version": 1,
  "id": "qq-napcat",
  "name": "QQ / NapCat",
  "version": "1.0.0",
  "description": "通过 NapCat WebSocket 服务器将群聊连接到 DiceFrame。",
  "plugin_type": "channel-adapter",
  "entrypoint": ["{python}", "-m", "src.bots.qq.main"],
  "config_schema": "config.schema.json",
  "capabilities": ["channel.group", "channel.private", "game.action"],
  "permissions": ["process.spawn", "network.client", "diceframe.http", "plugin.config", "plugin.secrets", "plugin.data"],
  "docs": "README_CN.md"
}
```

字段约定：

- `schema_version`：当前固定为 `1`。
- `id`：稳定插件 ID，必须匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`，且安装后目录名必须等于该 ID。
- `name` / `version` / `description`：展示信息。
- `plugin_type`：插件类型，必填。未填写或填写未知类型时，插件会被拒绝加载。
- `entrypoint`：进程型插件启动命令，字符串数组。`"{python}"` 会替换为当前 Python 解释器，`"{plugin_dir}"` 和 `"{data_dir}"` 分别替换为插件源码目录和专属运行数据目录。声明型插件可以省略。
- `config_schema`：配置 schema 文件路径，必须位于插件目录内；默认 `config.schema.json`。
- `contributes`：声明型插件提供的资源清单。路径必须位于插件目录内，可使用 glob；启用插件后才会注册。
- `capabilities`：声明插件提供的业务能力，例如群聊、私聊、提交行动。只声明实际提供的能力。
- `permissions`：声明插件需要的宿主能力。宿主会校验未知权限，并在插件设置页展示。未填写时宿主会按插件类型和配置字段推导基础权限，但对外发布插件建议显式填写。
- `docs`：插件目录内说明文档路径（如 `README_CN.md`）。该文档会在插件设置页的「说明」页签中展示（轻量 Markdown：标题、列表、加粗、行内代码）。建议用中文写清楚：插件是什么、怎么启用、怎么使用；没有该字段时前端不显示「说明」页签。

允许的 `plugin_type`：

```text
channel-adapter
bot-extension
content-pack
theme
voice-pack
import-export
provider
tool
```

允许的 `permissions`：

| 权限 | 含义 |
|------|------|
| `process.spawn` | 启动独立插件进程 |
| `network.client` | 插件进程访问外部网络 |
| `diceframe.http` | 调用 DiceFrame HTTP API |
| `plugin.config` | 读取插件普通配置 |
| `plugin.secrets` | 读取插件敏感配置 |
| `plugin.data` | 读写插件专属数据目录 |
| `content.read` | 注册和读取内容包资源 |
| `content.import` | 由用户主动导入内容到角色卡库或世界书 |
| `theme.tokens` | 注册主题 CSS 变量 |
| `voice.assets` | 注册语音音色描述、试听和参考音频 |
| `map.assets` | 注册地图地点和素材资源 |
| `tool.execute` | 注册并执行结构化工具调用 |
| `bot.extend` | 扩展 Bot Bridge 命令、消息处理和展示 |

## 6.1 安全边界

当前宿主已做的安全约束：

- 安装 `.dfplugin` 或 GitHub 源码快照时拒绝绝对路径、`..` 路径穿越、符号链接、加密文件、重复路径和多插件包，并执行压缩包、解压总量、单文件和文件数量限制。
- 插件 ID、目录名、`plugin_type`、`config_schema`、`entrypoint`、`contributes` 和 `permissions` 会在加载时校验。
- 普通配置和敏感配置分开保存；公开 API 只返回 secret 的配置状态和掩码。
- 进程型插件只继承启动所需的安全系统变量，不继承 DiceFrame 主进程中的其他密钥；需要 `diceframe.http` 时，宿主自动生成并注入该插件独有的内部 Token，而不是共享全局 Bot Token。
- 声明型插件资源只能注册插件目录内的安全路径；插件资产 URL 只能访问已声明贡献文件。
- 内容包导入是复制到用户数据，不会让用户数据继续引用插件文件。
- 主题插件只允许 CSS 变量，不允许脚本、组件或任意 CSS 注入。

当前还没有完整代码沙箱。带 `entrypoint` 的进程型插件本质上会在本机启动独立进程，所以只应安装可信来源的插件；环境变量隔离和权限声明能减少误暴露，但不等于操作系统级沙箱。

## 6.2 进程型插件生命周期要求

宿主负责创建、重启和管理**插件进程**（指数退避重启、稳定后复位）。插件自身必须负责两件事，避免成为僵尸或残留子进程：

1. **监控父进程存活与宿主世代**：宿主为插件注入 `TRPG_PARENT_PID`（主进程 PID），并在每次启动插件进程前，把本宿主进程的世代标识（token）原子写入 `DICEFRAME_PLUGIN_DATA_DIR/.host-generation`。插件须双检，任一触发立即退出（自杀）：
   - **父进程 PID 消失**：宿主进程退出，插件应立即结束，否则残留为孤儿进程。
   - **宿主世代变化/缺失**：DiceFrame 主程序重启是 `os.execv` 自替换（PID 与 starttime 都不变），纯 PID 检测会误判父进程存活，宿主换代后旧插件进程残留为孤儿、继续占用单实例锁等资源。世代文件值变化或缺失 = 宿主已换代，插件应立即退出。

   可参照独立分发的 `cloudflare-tunnel` 插件（仓库 [`diceframe/cloudflare-tunnel`](https://github.com/diceframe/cloudflare-tunnel)，v0.2.0 起内置世代检测）的 `parent_watch.py` 参考实现（跨平台 `pid_exists` + `read_generation_file` + `start_parent_watch`），进程型插件可复制，并传入世代文件路径与当前值：

   ```python
   # 插件内 self-contained 实现，不依赖 SDK 公共导出
   from pathlib import Path
   from parent_watch import read_generation_file, start_parent_watch

   generation_file = Path(os.environ["DICEFRAME_PLUGIN_DATA_DIR"]) / ".host-generation"
   start_parent_watch(
       on_exit=lambda: cleanup_your_subprocess(),
       generation_file=generation_file,
       initial_generation=read_generation_file(generation_file),
   )
   ```

   `on_exit` 是宿主退出或换代时的清理回调（如 kill 插件 spawn 的子进程）。带单实例锁的插件（如聊天桥接）还应在 `on_exit` 中释放锁，并可按 `qq-napcat` 插件 v1.4.0 实现锁接管（孤儿检测 + SIGTERM→SIGKILL 后重建锁），避免新实例被残留锁拒绝。

   旧版宿主（< 2.0.2）不写世代文件：`read_generation_file` 读到空串，世代检查自动跳过，退化为纯 PID 检测，插件在新旧宿主上均可用。

2. **退出时清理子进程**：插件 spawn 的任何子进程（如外部二进制），必须在插件退出时终止，防止残留。建议在插件主循环结束后统一 kill，并配合 `start_parent_watch` 的 `on_exit` 兜底。

宿主的插件进程重启（`_monitor_process`）与插件的子进程自愈是两个层面：插件进程崩溃由宿主拉起；插件内部自己 spawn 的子进程崩溃，由插件自己负责（可自行实现重试，指数退避上限建议 60s）。

规范边界：重连策略、业务退避等因插件而异，不强制统一；只有"监控父进程 + 宿主世代 + 退出清理"是每个进程型插件都应遵守的底线。

## 6.3 config.schema.json

配置 schema 使用受限 JSON Schema 子集：

- 顶层必须是 `{"type": "object", "properties": {...}}`。
- 支持字段类型：`boolean`、`string`、`number`、`integer`、`array`。
- 支持 UI 控件：`switch`、`text`、`secret`、`number`、`select`、`string-list`。
- 敏感字段使用 `ui.sensitive: true` 或 `ui.control: "secret"`。
- `ui.env` 可把配置注入进程型插件的环境变量。
- `ui.generate: true` 只用于敏感字段；启用插件时如果为空，宿主会自动生成令牌。

示例：

```json
{
  "type": "object",
  "required": ["enabled"],
  "properties": {
    "enabled": {
      "type": "boolean",
      "title": "启用插件",
      "default": false,
      "ui": {"control": "switch", "order": 10}
    },
    "base_url": {
      "type": "string",
      "title": "服务地址",
      "default": "http://127.0.0.1:18000",
      "ui": {"control": "text", "env": "DICEFRAME_BASE_URL", "order": 20}
    },
    "token": {
      "type": "string",
      "title": "访问令牌",
      "ui": {"control": "secret", "sensitive": true, "generate": true, "env": "PLUGIN_TOKEN", "order": 30}
    }
  }
}
```

## 7. 插件类型分册

> 插件类型的权威清单（支持级别、运行方式、推断权限、必需权限、内容贡献映射）由 `src/plugin_host/support.py` 的 `PLUGIN_TYPE_SUPPORT` descriptor 表单一来源驱动；新增类型只需改该表，宿主、策略、注册表与前端自动跟随。本节按类型给出作者使用指南，部分预留类型尚未接入运行时。

### 7.1 聊天桥接插件

适用于 QQ/NapCat、MaiBot、Discord、Telegram 等聊天流接入。聊天桥接插件是进程型插件，必须提供 `entrypoint`。

聊天桥接调用 DiceFrame HTTP API 时使用请求头 `X-Bot-Token`。由 DiceFrame 托管的插件会得到独立、自动生成的内部 Token，插件作者和用户都不需要填写；QQ / NapCat 因此不依赖设置页的全局 Token。只有完全运行在 DiceFrame 之外的程序，才使用管理员从“设置 → Bot API”复制的全局 Token。

推荐结构：

```text
src/bots/<platform>/
  config.py
  transport.py
  api_client.py
  store.py
  adapter.py
  command_matchers.py
  message_utils.py
  presenters.py
  delivery.py
  main.py
```

要求：

- 只通过 DiceFrame HTTP API 工作，不直接 import `src.webui`。
- 平台用户与游戏角色映射保存在插件数据目录。
- 消息事件必须用平台 `message_id` 去重，并持久化有限窗口。
- 平台断线重连、限速、消息格式转换由插件负责。
- 骰点、状态变化、剧情推进由 DiceFrame 服务端完成。
- HTTP 字段保持向后兼容；新增平台不得要求 Web 前端改用平台专属字段。

### 7.1.1 Bot Bridge 扩展插件

`bot-extension` 用来扩展聊天处理本身，而不是连接新的平台。它可以新增命令、修改进入 Bridge 的消息、修改业务结果，并替换文字、图片或结构化卡片展示。QQ / NapCat 直接调用扩展协议；MaiBot 等外部桥接在 `/api/bot/ping` 检测到 `bridge_extensions.protocol_version` 后，通过 `POST /api/bot/bridge/extensions` 使用同一套扩展。旧版 DiceFrame 没有该能力时，外部桥接继续使用原有逻辑。

作者推荐使用 `src.plugin_sdk.BridgeExtensionRuntime`，可复制 `plugins/examples/bridge-customizer` 开始开发。一个插件可以注册多个扩展，每个扩展声明以下阶段：

- `before_message`：在内置命令处理前运行；可修改 `payload.text`，也可返回 `handled: true` 和 `outputs` 直接处理命令。
- `after_result`：在内置逻辑产生展示结果后运行；适合增加提示、过滤内容或修改结构化字段。
- `render`：选择最终展示；返回 `handled: true` 时替换内置文字或卡片渲染。

扩展按 `priority` 从高到低执行。`before_message` / `after_result` 可依次传递修改后的 `payload`；`render` 使用第一个返回 `handled: true` 的结果。调用异常会被记录并跳过，协议错误或超时会停止该插件，Bot Bridge 随后使用内置逻辑。

输出支持：

```json
{"type": "text", "text": "自定义回复"}
{"type": "card", "title": "角色状态", "subtitle": "", "lines": ["HP 8/10"], "fallback_text": "HP 8/10"}
{"type": "image", "path": "status.png", "caption": "", "alt": "角色状态", "fallback_text": "HP 8/10"}
```

图片必须由插件写入 `DICEFRAME_PLUGIN_DATA_DIR`，只接受 PNG、JPEG、WebP 或 GIF，单个文件最大 10 MB。宿主验证路径后提供带 Bot 鉴权的读取地址；NapCat 会下载到自己的卡片缓存再发送，MaiBot 会转换为 base64 后调用图片发送能力。图片发送失败时使用 `fallback_text`。

清单至少包含：

```json
{
  "plugin_type": "bot-extension",
  "entrypoint": ["{python}", "{plugin_dir}/main.py"],
  "permissions": ["process.spawn", "plugin.config", "plugin.data", "bot.extend"]
}
```

标准输出只用于 JSON-RPC，不得打印日志。单条 RPC 请求或响应仍受 256 KB 限制，因此图片必须返回文件路径，不能放入 base64。插件只能改变聊天命令与展示，骰点、余额、支付和存档状态仍由 DiceFrame 游戏核心决定。

### 7.2 内容包插件

冒险头图的数据层级、创建/GM/打包/存档迁移全链路见 [`scene-images.md`](scene-images.md)。

适用于规则、世界模板、角色模板、NPC、道具、法术、职业等。内容包插件通常是声明型插件，可以没有 `entrypoint`。

建议目录：

```text
content/
  rules/
  worlds/
  characters/
  npc/
  items/
  spells/
```

当前宿主已支持 `rules`、`world_templates`、`character_templates`、`npcs`、`items`、`spells`、`classes`、`map_definitions`、`map_locations`、`map_icons`、`map_backgrounds` 贡献注册。启用内容包后，插件规则会出现在规则列表中，插件世界模板会出现在创建游戏的世界模板列表中；角色模板、NPC、道具、法术和职业会出现在插件设置页的“内容包”目录中，也可通过 `/api/plugins/content` 查询；地图贡献由游玩页地图直接消费。目录中的插件内容保持只读，卸载或停用插件后不再出现在列表里；用户主动导入普通内容时会复制一份到自己的角色卡库或世界书，之后不再依赖原插件文件。

冒险头图资产通过 `scene_images` 注册，与 `portraits` 一样属于内容包的声明型图片贡献。

`plugin.json` 示例：

```json
{
  "schema_version": 1,
  "id": "starter-content",
  "name": "Starter Content",
  "version": "0.1.0",
  "description": "提供一组规则和世界模板。",
  "plugin_type": "content-pack",
  "config_schema": "config.schema.json",
  "contributes": {
    "rules": ["content/rules/*.json"],
    "world_templates": ["content/worlds/*.json"],
    "character_templates": ["content/characters/*.json"],
    "npcs": ["content/npc/*.json"],
    "items": ["content/items/*.json"],
    "spells": ["content/spells/*.json"],
    "classes": ["content/classes/*.json"],
    "portraits": ["assets/portraits/*"],
    "scene_images": ["assets/scenes/*"],
    "map_definitions": ["maps/definitions/*.json"],
    "map_locations": ["maps/locations/*.json"],
    "map_icons": ["maps/icons/*"],
    "map_backgrounds": ["maps/backgrounds/*"]
  },
  "docs": "README_CN.md"
}
```

角色模板与 NPC 条目可以携带内置头像或内容包图片资源：

```json
{ "portrait": { "kind": "builtin", "id": "freeform_fantasy:0" } }
{ "portrait": { "kind": "asset", "path": "assets/portraits/mira.webp" } }
```

`kind: "asset"` 的路径必须位于插件目录内，并被 `contributes.portraits` 声明；支持 PNG、JPEG、WebP，单张不超过 3 MB。内容包启用期间会直接预览该资源；用户把角色或 NPC 导入自己的卡库或世界书时，宿主会把图片复制并规范化到本地头像库，导入副本不再依赖插件。WebUI 的“制作内容包”勾选“打包角色与 NPC 头像”后会自动完成这些步骤。

规则模板和世界模板都可以声明冒险头图。世界头图用于表达具体世界书的视觉，规则头图是没有世界专属图时的回退：

```json
{ "scene_image": { "kind": "builtin", "id": "freeform_fantasy" } }
{ "scene_image": { "kind": "asset", "path": "assets/scenes/valley.webp" } }
```

`kind: "asset"` 必须匹配 `contributes.scene_images`；支持 PNG、JPEG、WebP，源文件不超过 8 MB、尺寸不小于 320×180。DiceFrame 保存上传图或把包内图片固化到冒险时，会按 16:9 居中裁切并规范化为 1600×900 WebP。内容作者只应在包内写 `builtin` 或 `asset`；`plugin` 和 `upload` 是宿主生成的运行时引用，不属于可分发源格式。

冒险头图完整生命周期与优先级固定如下：

1. 创建冒险时按“世界模板 `scene_image` > 规则模板 `scene_image` > 对应内置规则图”计算内容默认；用户可以直接采用默认，也可以上传该冒险的专属图。
2. 选定结果写入游戏存档。来自内容包的图片会复制进本地冒险图片库，因此停用、更新或卸载内容包不会让已创建存档失图。
3. 首页存档卡片、创建流程确认页和游玩页读取同一份存档引用，不各自猜测规则图。
4. GM 可在游玩页随时上传新图，或“恢复内容默认”。恢复时会重新按当前世界优先、当前规则其次计算。
5. 存档导出会把非内置头图放入存档 zip，导入到另一台 DiceFrame 后重新落入本地图片库。
6. WebUI“制作内容包”可分别上传世界默认图和规则默认图；导出器会写入模板、生成 `assets/scenes/` 文件并补全 manifest，打包结果可直接分发。

同一世界有独立美术时把图写在世界模板；多个世界共享同一视觉时只写规则模板。旧包不声明 `scene_image` 时无需迁移，会自动使用内置回退。

约定：

- `content/rules/*.json` 必须是 DiceFrame 规则模板，建议显式填写 `rule_id` 和 `rule_name`。
- `content/worlds/*.json` 必须是 DiceFrame 世界模板，建议显式填写 `world_id`、`world_name`、`default_rule` 和 `language`。
- 内置规则/世界模板优先于插件资源；插件不要使用与内置资源相同的 ID。
- 角色模板可从插件设置页导入角色卡库；NPC、道具、法术、职业可导入指定世界书。
- 内容目录不会自动写入用户角色卡库或世界书，必须由用户主动导入；但用户用包内世界/规则创建冒险时，模板及其默认头图会按上述契约进入新存档。
- 内容包不得写运行时数据。

内容语言约定：

- 世界模板、世界书和内容目录用 `language` 标识内容语言，常用值为 `zh-CN` 和 `en`。创建游戏时同语言内容会优先显示，其他语言内容仍可选择。
- 世界模板正文按内容语言书写：`world_name`、`description`、`world_setting`、`starter_scene`、`starter_lorebook[].content` 不会被宿主自动翻译。
- 规则模板按语言拆分文件：`<rule_id>.json`（中文版，纯中文）+ `<rule_id>_en.json`（英文版，纯英文全文）。`rule_id` 保持不变（是引用键，`world.default_rule` 指向它，不随语言变，区别于世界模板的 `world_id`）。`RuleSystem.path_for(rules_dir, rule_id, language)` 按游戏语言选文件（`_en.json` 不存在则回退中文版）。规则列表与详情会配对合并各语言文件，前端按界面语言显示对应字段。自定义规则可不拆，保持单文件并在字段后加 `_en` 后缀（如 `attr_hint_en`）做英文显示。新增语言：在 `engine/language.py` 的 `_LANG_FIELD_SUFFIXES` 登记后缀，加 `<rule_id>_<suffix>.json`，加载/展示自动生效。
- 规则字段、枚举、协议标签和内部难度键保持稳定，例如 `rule_id`、`dice_system`、`combat_model`、`mechanics`、`difficulty_instructions` 的键、GM 标签 `HP/GOLD/QUICK_ACTIONS` 等不随内容语言改名。

#### 7.2.1 AI 检定元数据与离线兼容词表

正常主链不会再根据玩家消息里的关键词直接触发检定。全员行动收齐或 GM 手动推进后，阶段 1 GM 通过 `dice_checks` 工具统一判断是否需要检定；服务端会校验玩家、角色卡属性/技能和目标值，再生成一次不可重掷的骰面。规则包必须提供稳定的 `attributes[].key`、`dice_system` 和 `mechanics`；可选 `check_mechanic` 显式声明 `dice`、`comparison` 和暴击规则，未声明时由 `dice_system` 推导。

`intents` 只保留为模型调用完全不可用时的离线兼容路径，也可为旧客户端/旧存档提供默认属性与候选技能。它不再是正常联网主链的判定权来源。

**离线兼容优先级**（从规则词表到全局兜底）：

1. 规则模板自带 `intents`：用规则自己的词表，完全自控。
2. 规则模板 `extends: "intents_base"`（或插件目录内的词表基类）：继承主程序/基类的词库，子规则可覆盖个别意图。
3. 两者都没有：回退到全局通用词表（`templates/rules/fallback_intents.json`），只提供有限的通用触发。

**`intents` 字段结构**：

```json
{
  "intents": {
    "defaults": {
      "applies_to_dice_systems": ["d20", "d100"],
      "zh_match": "substring",
      "en_match": "word",
      "case_sensitive": false,
      "prefer_longest": true
    },
    "stealth": {
      "aliases": {
        "zh-CN": ["潜行", "潜入", "隐匿", "悄悄"],
        "en": ["sneak", "stealth", "hide", "creep"]
      },
      "skill_candidates": {
        "zh-CN": ["潜行", "隐匿"],
        "en": ["stealth", "dexterity"]
      },
      "default_attribute": "dex",
      "priority": 10
    }
  }
}
```

- `aliases`：离线兼容触发词，按语言分键（`zh-CN` / `en` / 未来语言）。
- `skill_candidates`：命中后候选技能，按角色卡技能名匹配。
- `default_attribute`：命中后默认用哪个属性检定（`str` / `dex` / `int` / `wis` / `cha` 等）。
- `priority`：多个意图同时命中时，数值小者优先（先匹配者胜）。
- `applies_to_dice_systems`：该意图适用于哪些骰制（`d20` / `d100`）。不匹配当前局骰制的意图会被跳过，防止 COC 触发只属于 DND 的意图（反之亦然）。

**`dice_system` 决定骰子本身**：AI 只提出检定参数，骰子算法（d20 还是 d100、成功线、大成功/大失败）由规则模板顶层的 `dice_system`、`mechanics` 和可选 `check_mechanic` 决定。

**继承主程序词库**：插件规则的 `extends` 支持继承主程序 `templates/rules/` 下的词表（如 `intents_base`），只需写 `"extends": "intents_base"`。继承是"按需"的——不需要词库的规则可以不继承，用全局兜底即可。

**多语言扩展**：词表是数据驱动的，加语言只需给 `aliases` / `skill_candidates` 增加对应语言键（如 `ja`），并保证 `engine/language.py` 登记了该语言后缀。新增语言不会污染其他语言场景。

### 7.3 主题插件

主题插件是声明型插件，适用于色板、字体、圆角和阴影。当前只支持主题契约 v2；未声明 `"schema_version": 2` 的旧主题不会被加载，也不会进行旧变量映射。

建议目录：

```text
theme/
  theme.json
```

当前宿主可以通过 `contributes.theme` 或 `contributes.themes` 注册主题描述文件。启用主题插件后，可在 WebUI “设置 -> 插件 -> 主题”选择主题；选择结果保存在当前浏览器。

`theme/theme.json` 示例：

```json
{
  "schema_version": 2,
  "id": "paper-soft",
  "name": "Paper Soft",
  "description": "柔和纸面配色。",
  "tokens": {
    "base": {
      "--df-accent": "#c79a45",
      "--df-interactive": "#347d78"
    },
    "dark": {
      "--df-canvas": "#17130f",
      "--df-surface-1": "#231f19",
      "--df-text": "#f0e6d2"
    },
    "light": {
      "--df-canvas": "#eadbb9",
      "--df-surface-1": "#fff7df",
      "--df-text": "#312719"
    }
  }
}
```

约定：

- `tokens` 只接受 `base`、`dark`、`light` 三组；`base` 总是生效，模式组覆盖同名值。
- 只接受宿主白名单中的 `--df-*` 语义 token。旧的 `--page`、`--panel`、`--gold` 等变量不会映射。
- 色彩：`--df-canvas`、`--df-canvas-glow`、`--df-surface-1/2/3`、`--df-surface-raised`、`--df-control-bg`、`--df-border`、`--df-border-soft`、`--df-focus`、`--df-accent`、`--df-accent-strong`、`--df-interactive`、`--df-interactive-strong`、`--df-success`、`--df-success-strong`、`--df-warning`、`--df-danger`、`--df-danger-strong`、`--df-info`、`--df-text`、`--df-text-secondary`、`--df-text-muted`、`--df-on-accent`、`--df-hover`。
- 其他：`--df-font-title/body/mono`、`--df-shadow`、`--df-shadow-strong`、`--df-radius-sm/md/lg`。
- 值会按 token 类型校验；未知变量、非法颜色、危险声明和 `url()` 都会被忽略。主题不能注入脚本、组件、布局或任意 CSS。
- 当前不允许主题提供背景图。如果未来开放，只会通过宿主校验的插件资源引用实现，不会允许任意 `url()` CSS 注入。
- 主题应同时验证亮色/暗色的文本对比度、焦点态和禁用态。

### 7.4 内容包中的地图贡献

`content-pack` 可以同时提供世界、角色等常规内容和地图，也可以只填写地图字段，作为纯地图内容包发布。启用后，游玩页会按世界自动选择合适的地图；除 GM 的背景来源选择外，不会把地图布局状态写入存档。

建议目录：

```text
maps/
  definitions/
  locations/
  icons/
  scenes/
```

声明贡献：

```json
{
  "contributes": {
    "map_definitions": ["maps/definitions/*.json"],
    "map_locations": ["maps/locations/*.json"],
    "map_icons": ["maps/icons/*"],
    "map_backgrounds": ["maps/scenes/*"]
  }
}
```

启用内容包后：

- `map_locations` 中的 JSON 地点会并入 `/api/games/{game_key}/map` 的 `locations`。
- `map_icons` 和 `map_backgrounds` 会经内容、格式与尺寸校验，然后获得受限访问 URL。
- 地点 JSON 可使用 `world_id` 或 `worlds` 限定适用世界；未限定时对所有世界可用。
- 地图定义使用插件 ID 作命名空间，不同内容包可以重用相同的地图素材 ID。

地点示例：

```json
{
  "id": "old-town",
  "name": "旧城区",
  "world_id": "city-noir",
  "connected_to": ["station"],
  "content": "煤气灯、雨水和狭窄巷道构成的旧城区。",
  "keywords": ["旧城区", "煤气灯"]
}
```

地图定义示例：

```json
{
  "schema_version": 1,
  "id": "city-noir-overview",
  "name": "雾都概览",
  "worlds": ["city-noir"],
  "mode": "graph",
  "background": "city-noir",
  "nodes": [
    { "location_ref": "old-town", "x": -18, "y": 8, "icon": "old-town" },
    { "location_ref": "station", "x": 16, "y": 12, "icon": "station" }
  ],
  "default_view": { "x": 0, "y": 0, "zoom": 1.2 }
}
```

约定：

- `schema_version` 必须为 `1`，`mode` 当前只支持 `graph`。
- `background` 引用同一内容包 `map_backgrounds` 中的文件 ID；`icon` 引用同一内容包 `map_icons` 中的图标 ID。
- `location_ref` 可指向内容包地图地点 ID，也可匹配当前世界 Lorebook 地点的 ID 或名称。
- `x/y` 均位于 `-50..50`，两者必须同时提供；未提供的地点仍使用稳定的自动布局。
- 世界模板可用 `default_map: "plugin:<plugin-id>:map:<map-id>"` 明确选择地图。否则宿主按 `worlds`、`default` 和排序自动匹配；无匹配时回退为 Lorebook 地点关系图。

### 7.5 音色预设插件

`voice-pack` 是无进程的声明型插件，通过现有插件商店安装。用户界面称其为“音色预设”，它不是使用本地 TTS 的必要条件。用户可以直接填写 OpenAI 兼容服务已有的 `voice_id`，或在“设置 → 我的音色”保存个人 GPT-SoVITS 参考 WAV/文本。预设只提供可选的一键配置、说明和小型试听/参考音频，不重复打包 GPT-SoVITS、Kokoro 等基础模型，也不负责安装 Python/CUDA 环境。

```json
{
  "schema_version": 1,
  "id": "example-narrator-voice",
  "name": "示例旁白音色",
  "version": "1.0.0",
  "plugin_type": "voice-pack",
  "config_schema": "config.schema.json",
  "contributes": {
    "voices": ["voices/*.json"],
    "voice_assets": ["voices/*.wav", "voices/*.mp3"]
  }
}
```

`config.schema.json` 至少提供与其他声明型插件相同的 `enabled` 布尔开关；音色预设不需要 `entrypoint`。

GPT-SoVITS 音色描述示例：

```json
{
  "schema_version": 1,
  "id": "example-narrator-zh",
  "name": "示例中文旁白",
  "engine": "gpt-sovits",
  "language": "zh-CN",
  "reference_audio": "voices/example-narrator.wav",
  "preview_audio": "voices/example-narrator.mp3",
  "prompt_text": "篝火已经点亮，新的冒险即将开始。",
  "prompt_language": "zh-CN",
  "license": "CC-BY-4.0",
  "consent": true
}
```

OpenAI 兼容服务的音色预设不需要参考音频，改为声明 `"engine": "openai-compatible"` 和服务实际接受的 `"voice_id"`。所有音色必须声明 `license` 和 `consent: true`；这表示作者确认有权分发音色描述、试听及参考音频。GPT-SoVITS 的 `reference_audio` 必须是 WAV，所有音频路径都必须同时出现在 `contributes.voice_assets` 中。

基础模型与训练权重不得塞进音色预设；当前插件包仍受 20 MB 上限保护。个人 GPT-SoVITS 音色支持两种参考来源：上传到 DiceFrame 的 WAV 适合同机、共享文件系统服务；“TTS 服务端可见路径”适合容器或远程主机。也可以使用 OpenAI 兼容 HTTP 接口，把原生模型和音色完全交给 AllTalk 等上游服务管理。

从开源音色选择、授权记录、本地打包到插件商店投稿的完整步骤，见 [音色预设发布指南](voice-pack-publishing.md)。仓库内的 `plugins/examples/kokoro-zh-voice-presets` 是一个不携带权重和音频的轻量示例。

## 7.6 仍未达成的插件能力

- 内容包资源已经支持导入用户角色卡库/世界书，但还没有直接进入运行中游戏流程的自动消费机制。
- 主题插件还不能注入 Vue 组件、布局或任意 CSS 文件。
- 内容包地图还没有编辑器、图层管理和实时协同；游玩页地图保持只读。
- `import-export` 还没有统一导入导出任务 API。
- `provider` 还没有统一 Provider 注册和选择 UI。
- `tool` 已支持短任务调用，但还没有长任务进度、取消和结果文件下载接口，也尚未自动接入 AI 工具选择。

### 7.7 导入导出插件

适用于 SillyTavern/酒馆角色卡、世界书、Lorebook 等格式转换。当前仅保留类型定义，尚未提供可调用运行时，商店不允许安装此类型。

要求：

- 必须声明输入/输出格式和版本。
- 导入前必须校验 JSON/PNG 元数据，不得直接覆盖用户现有数据。
- 出错时返回可读错误，不泄露本地路径和完整私密内容。

### 7.8 Provider 插件

适用于 LLM、Embedding、TTS、图片生成等外部服务。当前仅保留类型定义，统一 Provider 注册接口后续实现，商店不允许安装此类型。

要求：

- API key 等敏感信息必须走 secret 字段。
- 网络请求必须有超时和错误处理。
- 不得记录完整 prompt、响应或令牌。

### 7.9 工具插件

适用于校验、查询、转换和生成器等短任务。工具插件使用宿主管理的 JSON-RPC 标准输入/输出协议，启动后必须完成版本握手并注册至少一个工具。作者推荐使用 `src.plugin_sdk.ToolRuntime`，可复制 `plugins/examples/echo-tool` 开始开发。

```python
from src.plugin_sdk import ToolRuntime

runtime = ToolRuntime()

@runtime.tool(
    name="echo",
    title="Echo",
    description="Return text.",
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
)
def echo(arguments, context):
    return {"content": [{"type": "text", "text": str(arguments.get("text") or "")}]}

runtime.run()
```

宿主会校验工具名、输入 Schema、协议版本和返回对象。单次调用默认超时 30 秒，单条请求或响应上限 256 KB；标准输出只能发送协议消息，日志必须写入标准错误。用户可以在“设置 → 插件 → 工具”查看正在运行的工具并手动测试。HTTP 调用入口为 `GET /api/plugins/tools` 和 `POST /api/plugins/tools/{plugin_id}/{tool_name}`，写调用需要确认头。

要求：

- 明确输入、输出和副作用。
- 涉及文件写入时必须限制在插件数据目录或用户明确选择的位置。
- 当前运行时只适合 30 秒内可完成的小型任务；长任务应等待后续任务/进度协议。
- 不得向标准输出写普通日志，否则握手或调用会失败关闭。

## 8. 商店收录

官方社区索引仓库为 `https://github.com/diceframe/diceframe-plugins`。作者通过 Issue 模板提交插件 ID 和公开仓库地址；机器人读取最新正式 GitHub Release，自动检查后在投稿 Issue 中给出收录结果。

DiceFrame 安装时重新解析最新 Release，并下载它所指向的完整 Git commit 源码快照，而不是会变化的 `main` 分支。声明型插件在权限不扩大时，打开插件商店时自动检查并提示，由用户确认后更新；带入口的进程型插件只提示更新；权限或运行方式变化会暂停更新并要求重新审核。

`trust_level` 只表示来源：`official` 是 DiceFrame 官方维护，`community` 是社区投稿。任何等级都不等于安全担保，进程型插件仍能以当前用户权限运行代码。完整流程见 [插件索引与审核规则](plugin-registry.md)。

### 8.1 内容规范

内容包面向公开社区分发，作者须确保内容合法、健康、尊重他人权益。以下红线适用于世界书、角色、规则、NPC、道具等所有插件内容文本。

**法律红线（违反将拒绝收录或下架）**

- 不制作政治内容。
- 不含色情、性化未成年、嫖娼赌博；不渲染过度暴力、血腥、恐怖主义或教唆犯罪。
- 不侵犯他人著作权、商标权、肖像权或隐私权：使用他人文本、规则、头像、头图、字体、音频等资源时，必须具有授权、开放许可、公有领域依据或其他可证明的合法依据，并按许可要求标注来源和许可；仅署名或声明“非官方”不能代替授权。
- 不诽谤、侮辱真实人物，不泄露他人隐私或个人信息。

**社区准则**

- 不含仇恨、歧视（种族、性别、宗教、地域等）或极端主义内容。
- 描述与实际功能一致，不把「预留」或「部分支持」写成完整支持。
- 不夹带与插件用途无关的推广、广告或外部引流。

**同人 / 衍生作品**

- 社区索引允许投稿者上传自己创作的同人世界书、角色、NPC、规则改编和同人图片。必须在 README 与 description 标明原作、权利人和“非官方同人”，并使用 `community` 来源等级。
- 未取得原权利人授权的社区同人包，不得直接打包从原作提取的立绘、头像、地图、规则书正文/扫描件、音乐、视频、字体或其他现成素材；包内实际分发的文字和图片应由投稿者创作，或另有可证明的许可。
- 有原权利人授权、适用开放许可或其他明确合法依据的同人包，可以在核验后申请更高审核标记；没有授权证明不影响其按“社区非官方同人”投稿，但 DiceFrame 不为其权利状态背书。
- “版权归原作”“非官方同人”只是身份说明，不会自动取得复制、改编或网络传播权，也不能掩盖直接搬运原作素材。
- 收到包含初步证据且能定位到具体版本的权利通知时，DiceFrame 可先临时隐藏相关条目，转送作者并根据补充证据、申诉和最终核查决定恢复或下架。

DiceFrame 不对社区内容逐一预审；维护者发现或收到举报查实后将下架违规插件。

## 9. 安装、更新与卸载语义

- 安装：宿主解压到临时目录，校验后移动到 `data/plugin-packages/<id>/`。
- 覆盖安装：必须显式覆盖；宿主会先停止旧插件，再替换目录。
- 更新：商店解析作者仓库最新正式 Release；检测到新版本后提示用户，声明型与进程型插件都必须由用户确认后更新。
- 卸载：先停止插件，再删除 `data/plugin-packages/<id>/`。默认保留 `data/plugins/<id>/`。
- 重装同 ID 插件会自动复用保留的配置数据。

## 10. 发布检查

- README 必须说明插件用途、安装方式、配置项、能力、外部依赖、使用示例和限制。
- GitHub 仓库根目录或 `.dfplugin` 只包含一个插件，不包含 `__pycache__`、日志、临时文件、私有账号或本机绝对路径。
- `plugin.json` 的 `id`、目录名和安装包顶层目录保持一致。
- 敏感字段必须标记为 secret，不把 token 放进普通配置。
- 不记录完整令牌、玩家私密消息、角色感知内容或 API 响应。
- 进程型插件必须能在关闭时清理后台任务、连接和文件句柄。
- 声明型插件不得假装已经接入尚未实现的运行时能力；README 要写清当前作用。
- 运行打包命令，并用 WebUI 本地安装测试：

```powershell
python scripts\package_plugin.py plugins\my-plugin --overwrite
```

- 进程型插件还应运行 `py_compile`；改动宿主或前端时再运行插件宿主测试和相关前端检查。

## 11. 常见错误

| 报错/现象 | 通常原因 | 处理 |
|-----------|----------|------|
| 插件加载失败：插件 ID 非法 | `id` 不符合 `^[a-z0-9]+(?:-[a-z0-9]+)*$` | 使用小写字母、数字和短横线 |
| 插件 ID 与目录名不一致 | 目录名和 `plugin.json.id` 不相同 | 让目录名、安装包顶层目录、`id` 三者一致 |
| 不支持的 plugin_type | `plugin_type` 写错或尚未支持 | 使用本文列出的类型 |
| 未知插件权限 | `permissions` 中写了宿主不认识的值 | 使用本文列出的权限 |
| contributes 路径越界 | `contributes` 使用绝对路径或 `..` | 只引用插件目录内文件 |
| 插件包包含多个 plugin.json | `.dfplugin` 混入多个插件目录 | 一个文件只打一个插件 |
| 声明型插件启用后没显示内容 | `enabled` 仍是 false，或 `contributes` glob 没匹配到文件 | 在插件设置页启用，并检查路径大小写 |
| 进程型插件启动失败 | `entrypoint` 命令错误或依赖缺失 | 先在本地用同一命令运行，确认退出码和日志 |
