# 已安装模组资源与用户副本（本地整合分支）

## 产品行为

模组详情提供资源搜索、类型筛选、完整只读原始数据及图片预览。已安装但禁用的包也可浏览；浏览不会启用包，不修改全局贡献注册表，也不导入世界书或角色库。

世界、Book、角色模板、原生 Adventure、声明的头像/场景图片可创建用户副本。NPC、物品、法术、职业的叙事贡献复制为 Book 条目，完整源资料保存在扩展中；不会据此生成数值规则。规则目录的怪物/遭遇及地图贡献只读，仍归所属 runtime 使用。

副本窗口可改名称、世界描述、角色背景、Book 条目，v2 Adventure 使用图表单；所有原生字段另有高级 JSON 编辑。未应用的 JSON 编辑、修改后的旧审核、引用缺口及目标冲突均阻止保存。必须明确确认审核；保存结果不确定时锁定请求并要求先查看内容库。单资源 create-only，没有批次 receipt 或自动重放。

## 边界

- `ModuleResources` 以临时注册表读取显式 module + selector，声明之外的路径不能访问。原始内容、图片及 Fork 路由在读取 body 前要求 owner（沿用未配置密码的本地模式）。
- `ModuleForks` 只编排预审与委托；World/Book/Card/Adventure/Image 的校验和写入归各 owner。来源内容及整个声明依赖集合的摘要参与审核；源或依赖变化要求重新建立副本。
- 世界 v3 的独立主 Book 显式解析为同包资源，不把 display name 当 identity，不猜外部 Book。native Book 的 keywords/flags 经明确 legacy→v3 adapter 转换，原始 entry 保留在扩展中。世界副本由 DB 主 Book 负责条目，用户模板的旧读取投影在事务内派生；文件失败回滚 DB。
- 同包声明图片先经原上传 owner 做无写入预校验，保存时物化为内容哈希的用户图片。世界、角色及图片副本在卸载原模组后仍可用。图片成功而内容 owner 随后失败，允许留下一张去重图片；不会伪称跨 owner 全局事务。
- 原生 Adventure Fork 保留 v1/v2 格式、所有 JSON 与来源说明，名称更新相应 locale 投影。现有 Adventure ZIP contract 为 JSON-only；发现非 JSON 文件时在预审阻断，不能假装已复制媒体。声明的 module 图片可单独复制。
- 新代码未穿透 route 的 `api._*`，未把具体 D&D loader 加入 generic path。架构文档仍记录已合并 main，本说明记录本地待审实现。

## 验证

- backend 25 项：禁用包读取、同包资源和规则目录、路径拒绝、四 owner 副本、独立 v3 主 Book/关键词、编辑旧 token、来源/图片依赖变更、World DB/文件回滚、头像无写入预审与卸载后副本、owner HTTP 拒绝、原生媒体预审阻断及现有内容审核/图片回归。
- frontend 全量 729 项、120 文件通过；副本审核、JSON 未应用门禁、保存不确定状态、切换模组丢弃旧响应均覆盖。TypeScript build 与目标 ESLint/Ruff 通过。
- desktop/mobile 两条真实链路：浏览禁用模组→编辑 World/Card/Adventure→重新审核→确认保存→读真实库→Adventure 列表展示；几何检查无横向溢出，手机截图已查看。
- 最终 backend 全量 5646 项通过、1 项跳过；最终 desktop/mobile 两项再次通过。日志：`vnext-module-resources-backend-20261007.log`、`vnext-module-resources-browser-final-20261007.log`。
