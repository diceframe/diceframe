# DiceFrame 插件索引与审核规则

中文 | [English](../en/plugin-registry.md)

DiceFrame 采用“作者维护源码仓库、官方只维护公开索引”的模式。插件源码、Issue、版本和 Release 均由作者管理；[`diceframe/diceframe-plugins`](https://github.com/diceframe/diceframe-plugins) 只保存仓库地址、审核基线和商店展示缓存。

## 第一次投稿

作者不需要 Fork 索引仓库、上传 ZIP 或计算 SHA-256。

1. 插件使用独立、公开的 GitHub 仓库，仓库根目录就是插件目录。
2. 根目录包含 `plugin.json`、配置 Schema、README 和 LICENSE。
3. 创建一个非草稿、非预发布的 GitHub Release。
4. 在索引仓库选择“添加插件”Issue 模板，只填写插件 ID 和仓库地址。
5. 自动检查失败时按评论修改仓库，然后回复 `/recheck`。
6. 检查通过后等待投稿 Issue 中的收录结果。

机器人会把验证结果绑定到 Release 对应的完整 Git commit。如果最新 Release 在收录前发生变化，系统会要求按新版本重新验证。

## 自动检查

自动检查至少覆盖：

- 仓库公开、未归档，且地址是标准 GitHub HTTPS URL。
- 最新正式 Release、tag、固定 commit 和根目录 `plugin.json` 可读取。
- 插件 ID、三段式版本、类型、权限和必填字段合法。
- 配置 Schema、README、LICENSE 存在。
- 文件数量、单文件和总体积不超过安装限制。
- 没有 `.env`、私钥、凭据 JSON 等明显秘密文件。
- 插件 ID 和仓库没有重复收录。
- 根据实际入口和配置计算有效权限，而不是只相信作者写下的空权限数组。

自动检查不会执行插件入口，也不能证明代码没有恶意行为。

## 内容审核

自动检查只覆盖结构与代码安全，不预审内容。所有插件内容文本及素材（世界书、角色、规则、NPC、道具、头像、头图等）须符合《内容规范》（见插件开发指南 8.1 节）。维护者人工审核时会检查法律红线、社区准则、权利来源与许可；仅标注来源、版权归属或“非官方同人”不等于获得授权。收到带初步证据且能定位具体版本的权利通知时，可先临时隐藏并转送作者；根据补充证据和申诉结果恢复或下架。

社区索引允许投稿者自己创作的非官方同人世界书、角色、NPC、规则改编与图片；不得直接打包从原作提取的立绘、规则书正文/扫描件、音乐、视频、字体等素材。无授权证明的同人包保持 `community` 标记；明确授权或开放许可经核验后才可申请更高审核标记。

## 受欢迎度

商店展示的星数取自插件 GitHub 仓库的 star 计数，由索引仓库的同步任务每日刷新。星数反映仓库受欢迎度，供玩家参考，不等于 DiceFrame 的质量背书。

## 风险与更新策略

| 等级 | 含义 | 更新方式 |
|---|---|---|
| `declarative` | 无进程入口的内容包（可含地图）、可选音色预设或主题插件 | 权限和运行方式不变时，打开商店时自动检查并提示，由用户确认后安装或更新 |
| `unrestricted-process` | 启动 Python、Node、EXE 或其他进程 | 只提示更新，用户确认后安装 |
| `bundled` | DiceFrame 组织维护并随主程序发布 | 跟随 DiceFrame 更新 |
| `approval-required` | 新版本扩大权限或改变运行方式 | 暂停安装和更新，重新审核 |

`declarative` 与 `unrestricted-process` 由插件类型 descriptor（`src/plugin_host/support.py` 的 `process_mode`）决定：无进程入口的声明型类型为 `declarative`，带进程的为 `unrestricted-process`；`bundled` 和 `approval-required` 是发布策略层叠加。新增类型自动按其 descriptor 归类，无需逐类型手改审核规则。

第三方进程插件以当前操作系统用户权限运行。环境变量过滤和权限声明不是操作系统沙箱，因此商店必须明确显示高权限警告。

## 后续发布

正常更新不再向索引仓库投稿。作者只需：

1. 更新 `plugin.json.version`。
2. 提交代码、创建 Git tag。
3. 创建新的正式 GitHub Release。

DiceFrame 安装或检查更新时直接解析最新 Release，并下载该 Release 对应的固定 commit 源码快照。索引仓库的每日同步只用于刷新商店展示缓存；即使 GitHub 停止长期闲置仓库的定时任务，客户端更新仍可工作。

以下变化必须重新投稿：

- 更换仓库或插件 ID。
- 转移作者或所有权。
- 增加权限。
- 从声明型改为进程型。
- 改变敏感数据、网络或文件访问方式。

## 安装格式

- 官方商店中的开源插件：安装 GitHub Release 指向的仓库源码快照，最终落到 `data/plugin-packages/<id>/`。
- 私下、网盘或离线分享：作者生成一个 `.dfplugin` 文件。它是受限 ZIP 容器，但扩展名固定为 `.dfplugin`。
- 本地开发：把插件目录放到 `plugins/<id>/`，在设置页点击“重新扫描本地目录”。

本地 `.dfplugin` 不等于官方收录。安装器仍会检查路径穿越、符号链接、重复路径、文件数量、大小、manifest 和配置 Schema。

## 收录声明

收录表示插件满足索引格式和审核规则，不构成 DiceFrame 对安全性、功能质量、持续维护或适用性的保证。插件页面应同时展示仓库、风险等级、权限和更新策略。违反内容规范的插件将被下架。

