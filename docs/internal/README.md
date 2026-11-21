# Internal maintainer documents

简体中文说明：本目录存放维护者内部工作文档，不面向玩家或外部贡献者发布。

`docs/internal/` holds maintainer-only working documents:

- `GAL_TRACK_PLAN.md` — GAL 轨道施工顺序与完成标准；
- `COC_TRACK_PLAN.md` — CoC 7e 轨道：已完成的改动、契约变化、理想状态与待办；
- `DND2024_FULL_EXPERIENCE_PLAN.md` — D&D 2024 完整网团体验施工计划（内容 / 效果原语 / 战术地图三层）；
- `plans/` — 游戏化 / HD-2D 体验的施工路线图；
- `CONTENT_ARCHITECTURE_VNEXT_MASTER_PLAN.md` — 内容架构 vNext 母方案；
- `design/` — 带日期的设计草案与核查记录。

**D&D 2024 S0 切片的入口是两张表**：

- `design/SLICE_STATUS.md` — 每个切片做到哪儿了（状态 + 证据）。
- `design/GAP_REGISTER.md` — 发现但本片不做的缺口（`GAP-n`）。

规格本体（32 份 `S0*.md`，含权威索引 `S0_TASK_INDEX_2026-11-07.md`）不在 `main`，在
`docs/dnd2024-s0-specs` 分支上。要读规格：

```bash
git fetch origin docs/dnd2024-s0-specs
git show FETCH_HEAD:docs/internal/design/S0_TASK_INDEX_2026-11-07.md
```

命名约定：切片写 `S0-G2`，缺口写 `GAP-19`——**不要只写 `G2`**，两套编号会撞。


Boundary rules:

- These documents describe **local, possibly unreleased** work. Branch names, PR ordering, token/step budgets and unfinished boundaries are internal coordination details, not product announcements or release commitments.
- Nothing here is a public contract. The [architecture documents](../ARCHITECTURE_EN.md), [engineering rules](../ENGINEERING_RULES.md), [accepted ADRs](../adr/README.md) and the content/plugin contracts under `docs/` remain the only authoritative references.
- Do not link these documents from player-facing guides, the website or the public documentation repository (`diceframe-content`).
- New local-only design notes that must never be committed stay untracked via `.git/info/exclude` (currently `docs/internal/design/`); already-tracked files remain tracked while this directory is quarantined.
