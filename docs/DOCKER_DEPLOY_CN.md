# Docker / Linux 部署说明

中文 | [English](DOCKER_DEPLOY_EN.md)

想用 Docker 可以直接照下面部署；习惯在电脑上运行的用户不用改用法。Windows、macOS 和 Linux 仍然可以这样启动：

```bash
pip install -r requirements.txt
python web_server.py
```

Docker 里固定把运行数据放在 `/app/data`，compose 会映射到项目根目录的 `./data`。存档、配置、访问令牌、插件运行数据都留在宿主机，不会被镜像重建清掉。

## 快速启动

使用发布镜像：

```bash
cp .env.example .env
# 编辑 .env，填 TRPG_LLM_API_KEY；如需自定义模型，再填 base_url/model
docker compose pull
docker compose up -d
```

启动后打开：

```text
http://localhost:9876
```

如果要换宿主机端口，改 `.env`：

```env
DICEFRAME_HTTP_PORT=8080
```

然后访问 `http://localhost:8080`。容器内部端口仍是 `9876`，这样 WebUI、插件托管和内部 API 地址保持稳定。

## 常用命令

```bash
docker compose pull
docker compose up -d
docker compose logs -f
docker compose down
```

如果你在本地修改了源码，需要重新构建自己的镜像：

```bash
docker compose up -d --build
docker compose build --no-cache
```

设置页只负责提示 Docker/NAS 有新版本，不会在容器内部替换程序文件。NAS 用户可以直接在设备自带的容器管理界面检查并拉取新镜像。

## 数据与密钥

- `./data` 是运行时目录，已经在 `.gitignore` 和 `.dockerignore` 中排除。
- `.env` 只放本机部署配置，已经在 `.gitignore` 和 `.dockerignore` 中排除。
- `.env.example` 可以提交，里面不应写真实 API key、token、群号或私有地址。
- 若不设置 `TRPG_ACCESS_TOKEN`，服务会自动生成首次访问口令并写入 `./data/access_token.txt`。
- 若忘记 WebUI 访问密码，在挂载出来的 `./data` 目录新建 `reset_access_password.txt`，写入新密码并重启容器；重置成功后该文件会自动删除。

## QQ / NapCat

Docker 版主服务仍然使用内置插件宿主。启用 QQ 插件时，Bot 作为主服务容器内的子进程运行，和 PC 版的插件生命周期一致。

推荐流程：

1. 先启动 WebUI：`docker compose up -d`
2. 在 WebUI 插件页启用 `QQ / NapCat`
3. 如果 NapCat 不在容器内，填写宿主机或 NAS 上可从容器访问的 `NAPCAT_HOST` / `NAPCAT_PORT`

内置 QQ 插件不需要填写 DiceFrame Bot API Token，服务会自动生成并持久化。外部 MaiBot Bridge 可在 WebUI 的“设置 → Bot API”复制 Token。

也可以在首次启动前写入 `.env`：

```env
# 可选：固定全局 Bot API Token；留空则自动生成
TRPG_BOT_TOKEN=
NAPCAT_HOST=192.168.1.10
NAPCAT_PORT=3001
NAPCAT_TOKEN=
```

注意：Linux Docker 里 `127.0.0.1` 指容器自身，不是宿主机。NapCat 如果跑在宿主机，请填宿主机局域网 IP，或使用 compose 已映射的 `host.docker.internal`。

## PC 与 Docker 共存

PC 直接运行默认读项目内 `data/`；Docker compose 也映射同一个 `./data`。这意味着你可以在本机直接跑，也可以改用 Docker 跑，存档和设置仍是同一份。

如果想分开两套环境：

```bash
TRPG_DATA_DIR=./data-docker docker compose up
```

或把 compose 的 volume 改成：

```yaml
volumes:
  - ./data-docker:/app/data
```
