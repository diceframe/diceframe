# Internal maintainer documents

简体中文说明：本目录存放维护者内部工作文档，不面向玩家或外部贡献者发布。

`docs/internal/` holds maintainer-only working documents:

- `GAL_TRACK_PLAN.md` — GAL 轨道施工顺序与完成标准；
- `plans/` — 游戏化 / HD-2D 体验的施工路线图；
- `CONTENT_ARCHITECTURE_VNEXT_MASTER_PLAN.md` — 内容架构 vNext 母方案；
- `design/` — 带日期的设计草案与核查记录。

Boundary rules:

- These documents describe **local, possibly unreleased** work. Branch names, PR ordering, token/step budgets and unfinished boundaries are internal coordination details, not product announcements or release commitments.
- Nothing here is a public contract. The [architecture documents](../ARCHITECTURE_EN.md), [engineering rules](../ENGINEERING_RULES.md), [accepted ADRs](../adr/README.md) and the content/plugin contracts under `docs/` remain the only authoritative references.
- Do not link these documents from player-facing guides, the website or the public documentation repository (`diceframe-content`).
- New local-only design notes that must never be committed stay untracked via `.git/info/exclude` (currently `docs/internal/design/`); already-tracked files remain tracked while this directory is quarantined.
