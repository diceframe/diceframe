# DiceFrame 插件开发指南

本指南定义 DiceFrame 插件的通用包结构、manifest 标准、配置规则和各类插件的扩展边界。当前已经完整落地的是进程型聊天桥接插件；内容包已支持通过 `contributes` 注册规则、世界模板和内容目录，并支持把角色模板导入角色卡库、把 NPC/道具/法术/职业导入世界书；主题插件已支持选择并加载 CSS 变量；地图包已支持地点和素材资产进入地图接口；导入导出、Provider 和工具类插件的类型标准已经确定，但完整运行时能力会按类型逐步实现。

**重要：只有“当前支持状态”标为“已支持”或“部分支持”的能力，照本文开发后才会真实生效。标为“预留”的类型目前可以被安装、配置、展示和查询贡献清单，但不会自动接入导入导出、Provider 或工具流程。插件 README 必须如实说明当前作用。**

## 1. 插件系统目标

DiceFrame 插件不只面向机器人接入。长期目标是让社区可以通过插件安装和分发这些能力：

- 聊天桥接：QQ/NapCat、Discord、Telegram 等。
- 内容包：规则、世界模板、角色模板、NPC、道具、法术、职业。
- 主题：CSS 变量、色板、背景、图标和界面风格包。
- 地图包：地点模板、图标、场景素材、战斗网格素材。
- 导入导出：SillyTavern/酒馆角色卡、世界书、Lorebook 等格式转换。
- Provider：LLM、Embedding、TTS、图片生成等外部服务接入。
- 工具：批量校验、备份、转换、生成器等辅助功能。

## 2. 当前支持状态

| 类型 | plugin_type | 当前状态 |
|------|-------------|----------|
| 聊天桥接 | `channel-adapter` | 已支持进程托管、配置、启停、HTTP API 调用 |
| 内容包 | `content-pack` | 部分支持：可安装、配置、展示；启用后可注册规则、世界模板和内容目录，支持把内容导入用户角色卡库/世界书 |
| 主题 | `theme` | 部分支持：可安装、配置、展示；可在插件设置页选择并加载 CSS 变量主题 |
| 地图包 | `map-pack` | 部分支持：可安装、配置、展示；地点会并入地图接口，图标/场景/网格素材会作为资产清单返回 |
| 导入导出 | `import-export` | 可作为进程型插件接入；正式格式 API 待补 |
| Provider | `provider` | 可作为进程型插件接入；统一 Provider 注册待补 |
| 工具 | `tool` | 可作为进程型插件接入；工具 API 待补 |

`content-pack`、`theme`、`map-pack` 是声明型插件，可以没有后台进程。其他类型当前需要 `entrypoint`，由宿主作为独立进程托管。

## 3. 插件边界

- 插件以 `plugins/<plugin-id>/` 为安装单位。
- 插件安装包必须只包含一个 `plugin.json`。
- 插件不得直接读取或修改 DiceFrame 的 `data/` 存档；需要能力时应补正式 HTTP API 或类型专用注册流程。
- 插件禁止 `import src.webui`；缺少能力时按 `routes -> api -> services -> core` 补正式接口。
- 普通配置保存到 `data/plugins/<id>/config.json`。
- 敏感配置保存到 `data/plugins/<id>/secrets.json`，公开 API 只返回掩码。
- 插件运行数据应写入宿主传入的数据目录或 `data/plugins/<id>/`，不得写入插件源码目录。
- 卸载插件默认保留 `data/plugins/<id>/`，避免误删配置、令牌和用户数据。

## 4. 插件包结构

最小结构：

```text
<plugin-id>/
  plugin.json
  config.schema.json
  README_CN.md
```

zip 包允许两种结构：

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

安装器会拒绝绝对路径、`..` 路径穿越、符号链接和包含多个 `plugin.json` 的 zip 包。覆盖同 ID 插件必须在 WebUI 显式勾选“覆盖同 ID 插件”。

## 4.1 从示例开始

仓库内置两个可复制的示例插件：

| 示例 | 路径 | 类型 | 用途 |
|------|------|------|------|
| Starter Content | `plugins/examples/starter-content` | `content-pack` | 规则、世界模板、角色模板、NPC、道具、法术、职业 |
| Paper Theme | `plugins/examples/paper-theme` | `theme` | 安全 CSS 变量主题 |

开发新插件的推荐流程：

1. 复制一个示例目录到新的插件目录，例如 `plugins/my-content-pack`。
2. 修改 `plugin.json` 的 `id`、`name`、`version`、`description`、`capabilities`、`permissions`。
3. 修改 `config.schema.json`，只保留实际需要的配置项。
4. 把内容、主题或入口代码放到插件目录内。
5. 运行打包命令：

```powershell
python scripts\package_plugin.py plugins\my-content-pack --overwrite
```

生成的 zip 位于 `dist/plugins/`，可在 WebUI “设置 -> 插件 -> 安装插件”中安装测试。

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
- `entrypoint`：进程型插件启动命令，字符串数组。`"{python}"` 会替换为当前 Python 解释器。声明型插件可以省略。
- `config_schema`：配置 schema 文件路径，必须位于插件目录内；默认 `config.schema.json`。
- `contributes`：声明型插件提供的资源清单。路径必须位于插件目录内，可使用 glob；启用插件后才会注册。
- `capabilities`：声明插件提供的业务能力，例如群聊、私聊、提交行动。只声明实际提供的能力。
- `permissions`：声明插件需要的宿主能力。宿主会校验未知权限，并在插件设置页展示。未填写时宿主会按插件类型和配置字段推导基础权限，但对外发布插件建议显式填写。
- `docs`：插件目录内说明文档路径。

允许的 `plugin_type`：

```text
channel-adapter
content-pack
theme
map-pack
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
| `map.assets` | 注册地图地点和素材资源 |

## 6.1 安全边界

当前宿主已做的安全约束：

- 安装 zip 时拒绝绝对路径、`..` 路径穿越、符号链接和多插件包。
- 插件 ID、目录名、`plugin_type`、`config_schema`、`entrypoint`、`contributes` 和 `permissions` 会在加载时校验。
- 普通配置和敏感配置分开保存；公开 API 只返回 secret 的配置状态和掩码。
- 声明型插件资源只能注册插件目录内的安全路径；插件资产 URL 只能访问已声明贡献文件。
- 内容包导入是复制到用户数据，不会让用户数据继续引用插件文件。
- 主题插件只允许 CSS 变量，不允许脚本、组件或任意 CSS 注入。

当前还没有完整代码沙箱。带 `entrypoint` 的进程型插件本质上会在本机启动独立进程，所以只应安装可信来源的插件；权限声明用于安装前后审阅和后续更严格的权限控制，不等于操作系统级隔离。

## 6.2 config.schema.json

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

### 7.1 聊天桥接插件

适用于 QQ/NapCat、MaiBot、Discord、Telegram 等聊天流接入。聊天桥接插件是进程型插件，必须提供 `entrypoint`。

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

### 7.2 内容包插件

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

当前宿主已支持 `rules`、`world_templates`、`character_templates`、`npcs`、`items`、`spells`、`classes` 贡献注册。启用内容包后，插件规则会出现在规则列表中，插件世界模板会出现在创建游戏的世界模板列表中；角色模板、NPC、道具、法术和职业会出现在插件设置页的“内容包”目录中，也可通过 `/api/plugins/content` 查询。目录中的插件内容保持只读，卸载或停用插件后不再出现在列表里；用户主动导入时会复制一份到自己的角色卡库或世界书，之后不再依赖原插件文件。

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
    "classes": ["content/classes/*.json"]
  },
  "docs": "README_CN.md"
}
```

约定：

- `content/rules/*.json` 必须是 DiceFrame 规则模板，建议显式填写 `rule_id` 和 `rule_name`。
- `content/worlds/*.json` 必须是 DiceFrame 世界模板，建议显式填写 `world_id`、`world_name`、`default_rule` 和 `language`。
- 内置规则/世界模板优先于插件资源；插件不要使用与内置资源相同的 ID。
- 角色模板可从插件设置页导入角色卡库；NPC、道具、法术、职业可导入指定世界书。
- 内容包不会自动写入用户角色卡库、世界书或运行中游戏，必须由用户主动导入。
- 内容包不得写运行时数据。

内容语言约定：

- 世界模板、世界书和内容目录用 `language` 标识内容语言，常用值为 `zh-CN` 和 `en`。创建游戏时同语言内容会优先显示，其他语言内容仍可选择。
- 世界模板正文按内容语言书写：`world_name`、`description`、`world_setting`、`starter_scene`、`starter_lorebook[].content` 不会被宿主自动翻译。
- 规则模板按语言拆分文件：`<rule_id>.json`（中文版，纯中文）+ `<rule_id>_en.json`（英文版，纯英文全文）。`rule_id` 保持不变（是引用键，`world.default_rule` 指向它，不随语言变，区别于世界模板的 `world_id`）。`RuleSystem.path_for(rules_dir, rule_id, language)` 按游戏语言选文件（`_en.json` 不存在则回退中文版）。规则列表与详情会配对合并各语言文件，前端按界面语言显示对应字段。自定义规则可不拆，保持单文件并在字段后加 `_en` 后缀（如 `attr_hint_en`）做英文显示。新增语言：在 `engine/language.py` 的 `_LANG_FIELD_SUFFIXES` 登记后缀，加 `<rule_id>_<suffix>.json`，加载/展示自动生效。
- 规则字段、枚举、协议标签和内部难度键保持稳定，例如 `rule_id`、`dice_system`、`combat_model`、`mechanics`、`difficulty_instructions` 的键、GM 标签 `HP/GOLD/QUICK_ACTIONS` 等不随内容语言改名。

### 7.3 主题插件

适用于 CSS 变量、色板、背景、图标和界面风格。主题插件通常是声明型插件。

建议目录：

```text
theme/
  theme.json
  tokens.css
  assets/
```

第一阶段建议只支持 CSS 变量和静态资源，不支持任意前端组件注入。主题必须同时考虑亮色/暗色可读性。

当前宿主可以通过 `contributes.theme` 或 `contributes.themes` 注册主题描述文件。启用主题插件后，可在 WebUI “设置 -> 插件 -> 主题”选择主题；选择结果保存在当前浏览器，实际生效方式是覆盖 CSS 变量。

`theme/theme.json` 示例：

```json
{
  "id": "paper-soft",
  "name": "Paper Soft",
  "description": "柔和纸面配色。",
  "tokens": {
    "base": {
      "--gold": "#c79a45"
    },
    "dark": {
      "--panel": "#231f19",
      "--text": "#f0e6d2"
    },
    "light": {
      "--panel": "#fff7df",
      "--text": "#312719"
    }
  }
}
```

约定：

- 只支持 CSS 变量覆盖，不支持注入任意前端组件或脚本。
- 变量名必须以 `--` 开头；包含 `url(`、`;`、`{}` 等高风险内容的值会被忽略。
- 主题必须同时考虑亮色/暗色可读性。

### 7.4 地图包插件

适用于地点模板、地图图标、场景素材和战斗网格素材。地图包通常是声明型插件，也可以后续扩展为带生成进程的插件。

建议目录：

```text
maps/
  locations/
  icons/
  scenes/
  grids/
```

第一阶段推荐做“地图素材/地点模板包”，避免一开始就做实时协同战棋。

当前宿主可以通过 `contributes.locations`、`contributes.icons`、`contributes.scenes`、`contributes.grids` 注册地图包资源。启用地图包后：

- `locations` 中的 JSON 地点会并入 `/api/games/{game_key}/map` 的 `locations`。
- `icons`、`scenes`、`grids` 会出现在 `/api/games/{game_key}/map` 的 `assets` 中，并带有受限访问 URL。
- 地点 JSON 可使用 `world_id` 或 `worlds` 限定适用世界；未限定时对所有世界可用。

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

地图包当前不会提供实时战棋、拖拽编辑、碰撞体或网格规则；这些属于后续地图系统能力。

## 7.5 仍未达成的插件能力

- 内容包资源已经支持导入用户角色卡库/世界书，但还没有直接进入运行中游戏流程的自动消费机制。
- 主题插件还不能注入 Vue 组件、布局或任意 CSS 文件。
- 地图包还没有地图编辑器、战棋网格规则、图层管理和实时协同。
- `import-export` 还没有统一导入导出任务 API。
- `provider` 还没有统一 Provider 注册和选择 UI。
- `tool` 还没有统一工具运行、进度、取消和结果下载接口。

### 7.6 导入导出插件

适用于 SillyTavern/酒馆角色卡、世界书、Lorebook 等格式转换。当前可作为进程型插件接入。

要求：

- 必须声明输入/输出格式和版本。
- 导入前必须校验 JSON/PNG 元数据，不得直接覆盖用户现有数据。
- 出错时返回可读错误，不泄露本地路径和完整私密内容。

### 7.7 Provider 插件

适用于 LLM、Embedding、TTS、图片生成等外部服务。当前可作为进程型插件接入，统一 Provider 注册接口后续实现。

要求：

- API key 等敏感信息必须走 secret 字段。
- 网络请求必须有超时和错误处理。
- 不得记录完整 prompt、响应或令牌。

### 7.8 工具插件

适用于批量校验、备份、转换、生成器等辅助能力。当前可作为进程型插件接入。

要求：

- 明确输入、输出和副作用。
- 涉及文件写入时必须限制在插件数据目录或用户明确选择的位置。
- 长任务应可取消或可恢复。

## 8. 商店收录

官方社区索引仓库为 `https://github.com/diceframe/diceframe-plugins`。WebUI “插件 -> 插件商店”默认读取该仓库 `main` 分支的 `plugin_details.json`，并通过镜像源自动重试。

最小商店条目：

```json
{
  "id": "example-plugin",
  "repository_url": "https://github.com/username/example-plugin",
  "branch": "main",
  "tags": ["content-pack"],
  "manifest": {
    "schema_version": 1,
    "id": "example-plugin",
    "name": "示例插件",
    "version": "0.1.0",
    "description": "一句话说明插件用途",
    "plugin_type": "content-pack",
    "capabilities": [],
    "docs": "README_CN.md"
  }
}
```

商店安装默认会下载 `repository_url` 对应 GitHub 仓库的分支 zip。若插件仓库不是“仓库根目录即插件目录”的结构，应提供 `package_url`，指向已经打包好的 DiceFrame 插件 zip。镜像源只用于提高 GitHub raw/下载可用性，不作为可信来源；安装前宿主仍会做路径、manifest、schema 和插件 ID 校验。

## 9. 安装、更新与卸载语义

- 安装：宿主解压到临时目录，校验后移动到 `plugins/<id>/`。
- 覆盖安装：必须显式覆盖；宿主会先停止旧插件，再替换目录。
- 更新：从商店重新下载并覆盖安装。
- 卸载：先停止插件，再删除 `plugins/<id>/`。默认保留 `data/plugins/<id>/`。
- 重装同 ID 插件会自动复用保留的配置数据。

## 10. 发布检查

- README 必须说明插件用途、安装方式、配置项、能力、外部依赖、使用示例和限制。
- zip 包只包含一个插件，不包含 `__pycache__`、日志、临时文件、私有账号或本机绝对路径。
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
| 插件包包含多个 plugin.json | zip 里混入多个插件目录 | 一个 zip 只打一个插件 |
| 声明型插件启用后没显示内容 | `enabled` 仍是 false，或 `contributes` glob 没匹配到文件 | 在插件设置页启用，并检查路径大小写 |
| 进程型插件启动失败 | `entrypoint` 命令错误或依赖缺失 | 先在本地用同一命令运行，确认退出码和日志 |
