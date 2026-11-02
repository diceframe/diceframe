# 插件与群聊适配器

[English](../en/plugins.md) | 简体中文 · [文档目录](../README.md)

## QQ / NapCat

推荐的群聊方式是内置 QQ / NapCat 插件。

基本流程：

1. 进入“管理 → 插件”，打开 `QQ / NapCat` 的插件配置。
2. 配置 NapCat 的 WebSocket 地址、端口和 token。
3. 启用 `QQ / NapCat` 插件。
4. 在游戏页复制 Bot 绑定命令。
5. 到群里发送绑定命令，把网页游戏和群聊关联起来。

DiceFrame Bot API Token 由宿主自动生成并注入，内置 QQ / NapCat 无需填写。外部 MaiBot Bridge 等适配器可在“管理 → 设置 → Bot API”复制服务地址和 Token。

Bot 图片卡片需要系统提供中文字体：Windows 与 Docker 镜像（已内置 `fonts-noto-cjk`）开箱可用；直接在 Linux 主机上运行 `python web_server.py` 时请自行安装 CJK 字体（如 Debian/Ubuntu 的 `fonts-noto-cjk`），否则卡片渲染会失败并自动降级为纯文本消息。

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

## 插件商店与隐私

DiceFrame 插件商店可以浏览和安装社区插件。插件由作者通过 GitHub Release 发布；安装和更新都需要用户确认，插件申请运行外部进程或扩大权限时会额外提示风险。本地或私下分享的插件也可以通过 `.dfplugin` 文件安装。

DiceFrame Hub 为插件商店提供审核信息、版本状态和详情。Hub 暂时不可用时，已安装插件和本地游戏不受影响。匿名使用统计默认关闭，可以在“管理 → 设置 → 安全 → DiceFrame Hub 与隐私”中管理。

如果你想开发或发布插件，请阅读[插件开发指南](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md)和[插件索引与审核规则](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md)。
