# 外部冒险转换与资源编辑

本功能在本地整合分支实现，不表示已合并或已发布。R7 还需互斥选项的运行时验收。

## 用户流程

冒险库的「导入剧本 / 外部模组」接受 Markdown/TXT、Foundry Adventure 文档 JSON、Fantasy Grounds XML/未加密 `.mod` 和有文字层的 PDF。先生成纯 Draft，再编辑、检查全部项目与引用、重新审核、确认保存。已安装模组的 Adventure 副本和既有 v2 冒险编辑器也使用相同资源/条件表单。

资源表单可添加 NPC、物品、场景、地点/地图资料和遭遇目录，编辑名称、正文以及保留身份的完整原生 JSON。图表单关联场景、NPC、遭遇目录，逐条编辑现有有限 gate 类型。未应用的 JSON 或错误的条件值阻断提交。引用映射可明确关联原生资源和节点，或将未解析引用保留为文字。

外部文档默认仅 GM 可见。多篇资料之间没有可证明的因果关系，因此转换器不自动建立剧情连接；作者可点击「按资料顺序连接空分支」，仅为普通节点的空分支连接后一个节点，保留已有分支。公开给玩家前需编辑可见性并重新审核。

## 格式边界

- Foundry：支持 Adventure 文档导出中的 journal、actors、items、scenes。Journal HTML 转纯文字；明确的 Actor UUID 可映射到本包 NPC。原始文档保存在 GM 资料的 `source_record` 字符串中。不是 LevelDB 合集、整个系统或所有版本的模组安装器。[官方 AdventureData](https://foundryvtt.com/api/interfaces/foundry.documents.types.AdventureData.html)、[Adventure 文档说明](https://foundryvtt.com/article/adventure/)。
- Fantasy Grounds：支持 `definition.xml` + `db.xml`/`client.xml` 的未加密 `.mod`，或单独数据 XML；读取标准 story/encounter、NPC、item、image、battle 资料路径。Battle 原文作为 GM 遭遇参考。禁止 XML DTD/ENTITY、ZIP 路径逃逸、重复成员和超限展开。[官方模组资料格式](https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996644362/)。
- PDF：逐页提取文字层，作者检查分栏、顺序和秘密。无文字层的扫描件明确拒绝，需要先 OCR；不解析附件或执行动作。输入最多 5 MiB、128 页、提取文字最多 512 KiB，独立进程超时 8 秒终止；页面解码后检查流大小。这是受限提取进程，未宣称 OS 内存沙箱。[pypdf 提取说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)。
- 外部数值保留为参考原文，不推测转换 HP/AC/攻击或奖励；规则权威仍由原生 loader 与 ruleset adapter 校验。外部图片路径不下载或自动复制，审核面板明确提示通过本机图片库设置。宏、脚本和音乐列表跳过。

## Contract 与权限

外部 adapter 属于 `src/adventures/importers`，不会反向污染 generic engine。HTTP 的耗时解析在线程执行，native owner 审核仍由 application 边界完成。PDF 依赖 `pypdf>=6.19,<7`，两份部署锁固定 6.19.0 及 wheel SHA256。

新外部包 manifest 明确采用 `reference_policy: adventure-local`。本包图引用的 scene/NPC/encounter 必须真实存在；更新不能删掉仍被引用的资源或撤销该政策。既有无政策包保留原来的来源解析行为，不静默改变语义。

所有冒险内容库接口在读取正文前检查 owner。实际负例复现了玩家分享 query 能读取原始 GM 文件的旧缺口；详情、列表、导入、导出和修改接口已补门禁。玩家仍经授权的游戏投影读取公开剧情，原始 `source_record` 不经内容库暴露。

审核 token 覆盖编辑后的所有原生文件、来源与引用。修改后旧 token 拒绝提交；保存经既有原生 Adventure owner create-only 验证与持久化，不另造外部格式运行时。

## 验证

后端目标集 54 项通过，覆盖三个格式的实际提取/编辑/原生提交、原始数值仅参考、非法文档、PDF 超时、扫描件拒绝、本地引用删除与政策撤销拒绝、玩家原文权限负例，以及部署依赖锁。表单用先失败的测试复现并修复条件错误被其他编辑清掉的问题；显式按顺序连线保留原分支。

全前端 733 项通过，TypeScript build、目标 ESLint 和 Ruff 通过。桌面/手机真实 Foundry 上传→编辑资源与关联 NPC→重新审核→保存→读取原生库→刷新 2 项通过；既有文本启动推进与 Module 副本链路另 6 项通过。

真实浏览器还复现了另一缺口：由图生成的裸引用与审核返回的显式引用参与去重时形态不同，再次审核会改变 token。目标负例已补入，生成引用先归一到 portable 形态后去重，审核结果再次审核保持相同 digest。互斥玩家选项单独施工，当前文档不将并行 transition 当作互斥选择。
