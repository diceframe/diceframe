<p align="center">
  <img src="docs/assets/diceframe-logo.svg" width="144" height="144" alt="DiceFrame Logo">
</p>

<h1 align="center">DiceFrame</h1>

<p align="center"><a href="README_EN.md">English</a> | 中文</p>

<p align="center"><a href="https://github.com/diceframe/diceframe/stargazers"><img src="https://img.shields.io/github/stars/diceframe/diceframe?style=flat-square&logo=github&label=Stars" alt="GitHub Stars"></a> <a href="https://github.com/diceframe/diceframe/releases"><img src="https://img.shields.io/github/v/release/diceframe/diceframe?style=flat-square&logo=github&label=Release" alt="GitHub Release"></a> <a href="https://github.com/diceframe/diceframe/blob/main/LICENSE"><img src="https://img.shields.io/github/license/diceframe/diceframe?style=flat-square&logo=github&label=License" alt="License"></a></p>

![DiceFrame WebUI preview](docs/assets/diceframe-readme-hero.jpg)

<p align="center"><a href="https://diceframe.com">官方网站</a></p>

DiceFrame 是一个可以自己部署的 **AI 跑团引擎**，支持 **D&D / CoC / 自定义规则**与**多人 WebUI**。

它把 Web 桌面、角色卡、世界书、骰子、状态变动、剧情日志和群聊 Bot 接到同一个游戏状态里。玩家用自然语言说“我想做什么”，系统负责把这句话交给 GM 模型、处理骰子与状态变化，并把结果同步给网页或群聊里的其他玩家。

这个项目适合几种场景：

- 一个人试跑世界观，看看一个设定能不能玩起来。
- 小团在浏览器里联机，由一个人当 GM 管理入口和节奏。
- 群聊里跑团，玩家用 `@bot` 提交行动、查状态；需要检定时系统自动判断并掷骰。
- 自己改规则、世界书和角色模板，做一套私人跑团工具。

当前版本仍处于早期发布阶段。功能已经能跑，但接口、存档结构和文档还会继续整理。

## 交流与反馈

问题反馈和改进建议请优先通过 [GitHub Issues](https://github.com/diceframe/diceframe/issues) 提交，代码贡献欢迎发起 PR。提交前请阅读 [贡献指南](CONTRIBUTING.md)。

QQ 交流群：1060613588

## 功能概览

- WebUI：顶部以“总览 / 游玩 / 角色 / 内容 / 管理”组织主要工作区；内容区包含世界书、世界、冒险包和规则，管理区包含记忆、日志、插件和设置。
- 多人桌：邀请链接、玩家等待、暂离/回来、GM 强制推进、SSE 实时同步；实验性玩家直连可通过一次性链接码建立 WebRTC 对局。
- 骰子与状态：D&D 5e 轻量规则、自定义 d20、CoC 7e 轻量 d100 与无骰叙事分层处理；支持规则声明的优势/劣势、CoC 奖惩骰，以及 HP、理智、金币、物品、经验、死亡/复活等状态标签。
- 世界书：NPC、地点、物品、事件、谜题、势力等条目，按关键词注入上下文。
- 记忆与摘要：长团会压缩历史，也可以启用 embedding 做语义召回。
- AI 生成：世界、规则、角色、世界书条目都可以由模型辅助生成。
- 语音朗读：系统音色零配置回退，也可连接在线或本地 OpenAI 兼容 TTS、GPT-SoVITS；服务已有的 voice ID 和个人参考 WAV 可直接使用，商店音色预设完全可选。
- 系统生图：在模型路由中配置 OpenAI 兼容图像模型，为对话场景、角色头像、道具和地图背景生成图片；重大场景切换可在后台自动生成场景图。
- QQ / [NapCat](https://github.com/NapNeko/NapCat) 插件：群聊绑定网页对局，支持行动、桌外询问、状态、前情、地图、感知、支付；检定由系统自动判断并掷骰。
- 应用更新：便携版和托管 Docker 支持旁路安装、健康检查和失败回滚；源码、Git 与基础镜像按安装方式给出安全更新流程。
- Docker：提供 Linux/Docker 部署入口，运行数据挂载到 `data/`。

## 快速开始

### Windows 便携版

普通 Windows 用户建议直接下载便携版：

[前往 Releases 下载最新版](https://github.com/diceframe/diceframe/releases/latest)

下载最新的 `DiceFrame-vX.Y.Z-windows-portable.zip`，解压后运行 `DiceFrame.exe`。首次打开后，先进入“管理 → 设置 → 模型接口”，在“AI 服务商”中添加服务商名称、API 格式、Base URL、API Key 和可用模型，再进入“管理 → 设置 → 模型配置”为主模型、备用模型、向量记忆、TTS、ASR 和生图选择实际使用的服务商与模型。向量记忆的开关、输入上限和连接测试也集中在该页的向量模型卡片中。

Windows 便携版可以在“管理 → 设置 → 关于”的版本更新区检查并应用更新。

### Docker 运行

已安装 Docker 的用户可以直接拉取镜像：

```bash
docker pull ghcr.io/diceframe/diceframe:latest
docker run -d --name diceframe -p 9876:9876 -v ./data:/app/data ghcr.io/diceframe/diceframe:latest
```

打开：

```text
http://localhost:9876
```

Docker 会把运行数据挂载到当前目录的 `data/`。详细说明见 [DiceFrame 部署说明](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/deploy.md)。

升级到支持托管更新的基线镜像后，普通应用版本可以直接在“管理 → 设置 → 关于”的版本更新区完成；Python 或系统运行时变化仍需拉取新镜像。程序不会挂载或控制 Docker socket。

NAS 用户可以在 Docker 管理界面搜索 `diceframe` 拉取镜像，或在 [Docker Hub](https://hub.docker.com/r/falconku/diceframe) 查找。

`latest` 只跟随正式版。预览版也会发布 Docker 镜像，但不会覆盖 `latest`；需要体验时请从 GitHub Releases 复制完整预览版本号并显式拉取，例如：

```bash
docker pull ghcr.io/diceframe/diceframe:2.3.0-beta.2
# Docker Hub：docker pull falconku/diceframe:2.3.0-beta.2
```

如果原先使用 Compose 部署，更新命令必须在存放 `docker-compose.yml` 的部署目录执行：

```bash
cd /path/to/diceframe
docker compose pull
docker compose up -d
```

如果原先使用的是上面的 `docker run` 命令，则不能在任意目录改用 Compose 更新；请按部署说明拉取新镜像并用原端口、卷和环境变量重新创建容器。出现 `no configuration file provided: not found`，表示当前目录没有 Compose 配置文件。

### 从源码运行

源码运行适合开发、调试或自己改代码。需要：

- Python 3.10 或更高版本
- Node.js 20.19+，或 22.12+
- 一个兼容 OpenAI Chat Completions API 的模型服务

可以使用 DeepSeek、硅基流动、OpenAI、Ollama 等服务。只要它提供 OpenAI 兼容接口，就可以在“管理 → 设置 → 模型接口”里配置。

第一次从 GitHub 克隆后，需要先构建前端：

```bash
cd frontend-v2
npm ci
npm run build
cd ..

pip install -r requirements.txt
python web_server.py
```

直接运行时会自动读取项目根目录的 `.env`；已经在 PowerShell、服务管理器或 Docker 中设置的环境变量优先级更高。

启动后打开终端里显示的地址，默认是：

```text
http://localhost:18000
```

第一次进入“管理 → 设置”，先在“模型接口”的“AI 服务商”中添加连接信息与模型目录，再到“模型配置”分配主模型、备用模型和各项 AI 能力；向量记忆的开关、输入上限和测试与向量模型配置放在一起。

**AI 配置契约变更：**应用仅支持 `ai_providers` 与各能力的 `*_provider_ref`，API Key 使用 `ai_provider_key_<id>` 保存到敏感配置。旧能力级直填地址、密钥、API 格式及 `TRPG_LLM_*`、`TRPG_EMBEDDING_*`、`TRPG_TTS_*`、`TRPG_ASR_*`、`TRPG_IMAGEGEN_*` 环境入口不再启用，也不会自动迁移或创建服务商；旧配置更新请求返回 HTTP 400 `unsupported AI config fields`。请在设置页手动添加服务商并分配用途。

主备模型、向量、生图及 OpenAI-compatible/GPT-SoVITS 语音只使用对应服务商引用；缺失或未知引用不会恢复旧地址和密钥，相关远程能力保持未配置。浏览器语音、edge-tts 和已禁用 ASR 无需服务商引用，本地服务商允许空 API Key。服务商连接测试可使用临时地址与密钥，但不会回退到旧能力级配置。

Windows 下也可以双击 `web_ui.bat` 启动；它会检查依赖，并在缺少前端构建产物时自动构建。

### 独立部署 Web 前端

如果希望让浏览器前端固定使用 HTTPS、而后端继续运行在 NAS、家用电脑或服务器上，可以把 WebUI 单独构建并部署到 Cloudflare Pages 等静态托管：

```bash
cd frontend-v2
npm ci
npm run build:standalone
```

完整的 Cloudflare Pages 参数、后端 HTTPS、跨域白名单、安全建议与排错步骤见官网的[独立部署 WebUI 指南](https://diceframe.com/docs?doc=standalone)。Windows 便携版、Docker 和服务器自带 WebUI 继续使用默认同源模式，不需要单独配置。

新手玩法、多人流程、群聊命令和状态变动说明见 [DiceFrame 用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md)。

### 移动端 App

DiceFrame 提供独立的 Android 客户端，源码在 [diceframe-mobile](https://github.com/diceframe/diceframe-mobile) 仓库，安装包在 [Releases](https://github.com/diceframe/diceframe-mobile/releases) 页下载；使用问题、需求与贡献也请前往该仓库。

## 第一局怎么玩

1. 打开 WebUI。
2. 进入“管理 → 设置 → 模型接口”添加连接与模型目录，再到“模型配置”选择主模型。
3. 回到“总览”，点击“创建新冒险”并选择游戏语言。
4. 选择模板世界、AI 生成世界，或自己填写世界设定。
5. 选择规则和难度。
6. 创建角色，或用 AI 生成一个角色草稿后再手动改。
7. 进入“游玩”，输入角色行动。
8. 行动需要检定时，系统会自动判断并只掷一次骰，随后 GM 继续叙事。

多人模式下，GM 创建游戏后复制邀请链接给其他玩家。玩家加入并认领角色后，每轮提交自己的行动；所有活跃玩家都提交后，或 GM 强制推进后，进入下一段叙事。

## QQ / NapCat

推荐的群聊方式是内置 QQ / NapCat 插件。

基本流程：

1. 进入“管理 → 插件”，打开 `QQ / NapCat` 的插件配置。
2. 配置 NapCat 的 WebSocket 地址、端口和 token。
3. 启用 `QQ / NapCat` 插件。
4. 在游戏页复制 Bot 绑定命令。
5. 到群里发送绑定命令，把网页游戏和群聊关联起来。

DiceFrame Bot API Token 由宿主自动生成并注入，内置 QQ / NapCat 无需填写。外部 MaiBot Bridge 等适配器可在“管理 → 设置 → Bot API”复制服务地址和 Token。

Bot 会跟随绑定对局的语言显示帮助和主要操作提示；中文与英文对局都可直接使用对应语言的命令。

群聊里常用命令：

- `@bot 帮助`：查看当前群可用指令。
- `@bot 绑定 <game_key> <凭证>`：GM 将网页游戏绑定到群聊。
- `@bot 邀请`：发送网页加入链接和新玩家教程卡。
- `@bot 新建角色` / `@bot 车卡`：在群里发送建卡入口。
- `@bot AI车卡`：AI 辅助生成角色草稿，私聊确认后发到群里公示。
- `@bot 加入 角色名`：认领已有角色。
- `@bot 询问 <问题>`：向 KP 进行桌外询问；只使用公开剧情、规则和该角色已知信息，不提交行动、不推进剧情、不触发检定，也不消耗本轮行动。英文对局使用 `@bot ask kp <question>` 或 `@bot ask: <question>`；普通 `ask the guard ...` 仍是角色行动。
- `@bot 前情`：查看前情提要和最近回合。
- `@bot 地图`：查看当前场景和地点连接。
- `@bot 状态`：查看 HP、金币、背包等摘要。
- `@bot 感知`：私聊查看角色专属感知。
- `@bot 支付`：查看待确认支付列表。
- `@bot 确认支付` / `@bot 拒绝支付`：处理付款。
- `@bot 推进`：GM 强制推进当前回合。
- `@bot 暂离` / `@bot 回来`：临时下线不阻塞回合。
- `@bot <自然语言行动>`：提交角色行动。

DiceFrame 插件商店可以浏览和安装社区插件。插件由作者通过 GitHub Release 发布；安装和更新都需要用户确认，插件申请运行外部进程或扩大权限时会额外提示风险。本地或私下分享的插件也可以通过 `.dfplugin` 文件安装。

DiceFrame Hub 为插件商店提供审核信息、版本状态和详情。Hub 暂时不可用时，已安装插件和本地游戏不受影响。匿名使用统计默认关闭，可以在“管理 → 设置 → 高级参数 → DiceFrame Hub 与隐私”中管理。

如果你想开发或发布插件，请阅读[插件开发指南](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md)和[插件索引与审核规则](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md)。

## 文档

| 内容 | 中文 | English |
|------|------|---------|
| 用户手册 | [用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md) | [User guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md) |
| Docker 部署 | [Docker 部署](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/deploy.md) | [Docker deployment](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) |
| 独立 WebUI | [独立部署 WebUI](https://diceframe.com/docs?doc=standalone) | [Standalone WebUI](https://diceframe.com/en/docs?doc=standalone) |
| 应用更新 | [应用更新](docs/zh/updates.md) | [Application updates](docs/en/updates.md) |
| 规则与骰子 | [规则与骰子](docs/zh/rules-and-dice.md) | [Rules and dice](docs/en/rules-and-dice.md) |
| 玩家直连（实验性） | [玩家直连](docs/zh/direct-connect.md) | [Player Direct Connect](docs/en/direct-connect.md) |
| 插件开发 | [插件开发](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md) | [Plugin development](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) |
| 插件索引与审核 | [插件索引与审核](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md) | [Plugin registry](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-registry.md) |
| Bot Bridge 核心 | [Bot Bridge 核心](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/bot-bridge-core.md) | [Bot Bridge Core](https://github.com/diceframe/diceframe-content/blob/main/docs/en/bot-bridge-core.md) |


## 数据与隐私

运行数据默认放在：

```text
data/
```

这里会包含配置、访问口令、插件运行数据、存档、世界书数据库和记忆数据库。简单说，这里放的是“你的桌子”和“你的记录”。

常见数据位置：

- `data/config.json`：普通配置
- `data/secrets.json`：API key、token 等敏感配置
- `data/access_token.txt`：首次启动时生成的 WebUI 初始口令；忘记密码时可新建 `data/reset_access_password.txt` 写入新密码并重启
- `data/saves/`：游戏存档
- `data/templates/`：运行时规则和世界模板；用户自定义内容会保存在这里，升级时不会被内置模板覆盖
- `data/plugins/`：插件运行数据
- `data/plugin-packages/`：用户安装的插件源码（跨版本保留）
- `data/bot/cards/`：群聊图片卡片缓存

请妥善保管这些文件。备份、迁移服务器或发给别人排查问题时，先确认里面没有 API Key、访问口令、真实群号、私人聊天记录或不想公开的存档。

## 项目结构

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

## 贡献者

感谢所有提交代码、文档、测试、问题反馈和内容改进的朋友。贡献者由 GitHub 根据提交记录自动统计，详见 [Contributors](https://github.com/diceframe/diceframe/graphs/contributors)。其中可能包含 CI、发布流程和代码助手等机器人账号；它们保留在自动统计中，但不代表人工开发者名单。

贡献规则和本地验证方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

本项目采用 [GNU Affero General Public License v3.0](LICENSE) 授权。

你可以在 AGPL-3.0 的条款下使用、修改和分发本项目。若你分发修改后的版本，或将修改后的版本作为网络服务提供给他人使用，应按 AGPL-3.0 的要求公开相应源码。
