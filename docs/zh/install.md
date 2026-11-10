# 安装 DiceFrame

[English](../en/install.md) | 简体中文 · [文档目录](../README.md)

选择一种安装方式，然后开始[第一局](content-and-play.md#第一局怎么玩)。升级已有安装前，请阅读[应用更新](updates.md)及 [AI 配置契约变更](updates.md#ai-配置契约变更)。

## Windows 便携版

普通 Windows 用户建议直接下载便携版：

[前往 Releases 下载最新版](https://github.com/diceframe/diceframe/releases/latest)

下载最新的 `DiceFrame-vX.Y.Z-windows-portable.zip`，解压后运行 `DiceFrame.exe`。首次打开后，先进入“管理 → 设置 → 模型接口”，在“AI 服务商”中添加服务商名称、API 格式、Base URL、API Key 和可用模型，再进入“管理 → 设置 → 模型配置”为主模型、备用模型、向量记忆、TTS、ASR 和生图选择实际使用的服务商与模型。向量记忆的开关、输入上限和连接测试也集中在该页的向量模型卡片中。

Windows 便携版可以在“管理 → 设置 → 关于”的版本更新区检查并应用更新。

`DiceFrame.exe` 会把 DiceFrame 开在自己的窗口里（使用系统自带的 Microsoft Edge WebView2；Windows 11 和绝大多数 Windows 10 已经装好）。窗口打开后控制台会自动关闭，启动器日志写入 `logs/launcher.log`；第一次启动会自动带上首次访问密码，不用手动输入。关闭这个窗口就会停止 DiceFrame。系统缺少 WebView2 时会退回到默认浏览器并保留控制台；想一直用浏览器，可以用 `DiceFrame.exe --browser` 启动。其他玩家照常用浏览器或移动端通过链接加入。

## Docker 运行

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

托管应用更新与回滚方式见[应用更新](updates.md#docker-直接升级与数据保留)。

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

## 从源码运行

源码运行适合开发、调试或自己改代码。需要：

- Python 3.10 或更高版本
- Node.js 20.19+，或 22.12+
- 一个兼容 OpenAI Chat Completions API 的模型服务

可以使用 DeepSeek、硅基流动、OpenAI、Ollama 等服务。只要它提供 OpenAI 兼容接口，就可以在“管理 → 设置 → 模型接口”里配置。

先克隆仓库，或解压源码发布包：

```bash
git clone https://github.com/diceframe/diceframe.git
cd diceframe
```

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

Windows 下也可以双击 `web_ui.bat` 启动；它会检查依赖，并在缺少前端构建产物时自动构建。

## 独立部署 Web 前端

如果希望让浏览器前端固定使用 HTTPS、而后端继续运行在 NAS、家用电脑或服务器上，可以把 WebUI 单独构建并部署到 Cloudflare Pages 等静态托管：

```bash
cd frontend-v2
npm ci
npm run build:standalone
```

完整的 Cloudflare Pages 参数、后端 HTTPS、跨域白名单、安全建议与排错步骤见官网的[独立部署 WebUI 指南](https://diceframe.com/docs?doc=standalone)。Windows 便携版、Docker 和服务器自带 WebUI 继续使用默认同源模式，不需要单独配置。

新手玩法、多人流程、群聊命令和状态变动说明见 [DiceFrame 用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md)。

## 移动端 App

DiceFrame 提供独立的 Android 客户端，源码在 [diceframe-mobile](https://github.com/diceframe/diceframe-mobile) 仓库，安装包在 [Releases](https://github.com/diceframe/diceframe-mobile/releases) 页下载；使用问题、需求与贡献也请前往该仓库。

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
