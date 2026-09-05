# DiceFrame v2.5.3

> 增量更新。v2.5.3 在 v2.5.2 的基础上收敛经济与支付的实现语义，并修复两个实测问题（奖励被误拦截、结算提示不消失）。2.5 产品的完整特性介绍见 v2.5.0–v2.5.2 的历史发布页，本页只描述相对 v2.5.2 的变化。重要战役升级前，请先备份完整的 `data/` 文件夹。

## 中文

### 本版重点：经济与支付收敛

- **每局奖励策略**：GM 控台顶层新增「对局设置」，可直接选择剧情奖励发放方式——自动发放小额现金，或所有奖励都需 GM 确认；自动发放的上限也按局设置。只有单角色、纯货币、上限内的奖励才会免点击自动到账；带物品、多人、超上限的奖励一律交给 GM。策略解析顺序为 本局设置 → 规则模板默认 → 服务器全局兜底，全局默认上限从 10000 收紧到 500。
- **购买链路更快更简单**：移除了同一回合内的第二次 AI 价格复检。GM 在叙事中口述价格后，支付窗口会在下一轮自动出现；等不及可以用 GM 控台「发起支付」立即定价。未定价商品的叙事交付拦截保持不变，仍然不可能白拿货。
- **整轮回滚**：回滚或重写第 N 轮时，该轮及之后的所有经济结算一并撤销；早于该轮创建、在其后才结算的报价会重新弹回待确认，玩家不为已被抹掉的剧情付钱。物品恢复改用整快照与绝对结算前镜像，删除了易错的库存差分逻辑；历史重写（swipe）同时会截断目标轮之后的日志分支。
- **退役无人的转账/手续费/团队分摊**：这些提案类型早已没有创建入口（旧的 PAY/TEAM_PAY 标签协议在 schema 6 就已停用），本次彻底移除其结算与展示代码。

### 修复

- **奖励被误拦截**：叙事奖励不再被"任务完成证据"启发式吞掉。此前叙述里只要出现"如果/以后"等词或缺少"完成/击败"等关键词，奖励就会被静默丢弃并提示"任务确认完成前不会发放奖励"——即使它根本不是任务（实测第 5 轮奖励未入账事故）。现在叙事奖励一律进入提案，由奖励策略与 GM 决定。
- **结算提示不消失**：钱到账后，回合里过期的「结算待确认：关联结果尚未生效」提示现在会自动清除；单人局的结算确认消息直接显示在公开时间线，不再藏进私聊记录（多人局维持原有可见性规则：公开交易公开显示，GM 私下交易只有当事人可见）。

### 破坏性变更与升级提示

- **AI 服务必须使用共享服务商引用**：对话、向量、语音识别、生图不再接受旧的独立 base_url/api_key 兼容配置。从旧版升级后，请到管理页服务商设置确认各能力的服务商引用；已在使用服务商引用的配置无需改动。
- **存档迁移到 schema 8**：待处理的转账/手续费/分摊提案会被作废（它们从未扣款），已成交的交易流水、余额与物品完全不受影响。
- 奖励自动结算的全局默认上限为 500；可在每局「对局设置」中按本局经济规模调整（D&D 金币与 COC 美元的量级各自独立）。
- 从 v2.5.2 升级无需额外操作；重要战役升级前仍建议备份完整 `data/`。

### 下载与校验

- **普通 Windows 用户**：`DiceFrame-v2.5.3-windows-portable.zip`
- **源码运行用户**：`DiceFrame-v2.5.3-windows.zip`
- **托管 Docker 更新**：`DiceFrame-v2.5.3-docker-update-linux-amd64.zip`
- 下载后请使用 Release 中的 `SHA256SUMS` 统一校验；重要战役建议保留旧版程序与数据备份，便于回退。

## English

### Highlights: economy and payment convergence

- **Per-game reward policy**: The GM console top-level "Game settings" dialog now chooses how narrative rewards are granted — auto-grant small cash rewards, or require GM confirmation for everything — with a per-game cap. Only single-recipient, pure-currency, within-cap rewards settle without a click; item rewards, multi-recipient grants, and over-cap amounts always go to the GM. Resolution order: game override → rule-template default → server global fallback, with the global default cap tightened from 10000 to 500.
- **Faster, simpler purchases**: The second same-round AI pricing pass is gone. When the GM narrates a price, the payer dialog appears next round; "Create payment" prices it immediately. Narrative delivery interception for unpriced items is unchanged — free handouts remain impossible.
- **Whole-round rollback**: Rolling back or rewriting round N withdraws every settlement from N onward; an offer created earlier but settled inside the erased era reopens as pending, so players never pay for fiction that no longer exists. Item recovery now uses whole snapshots and absolute before-images instead of fragile inventory diffs, and a historical swipe branch-cuts the log after the target round.
- **Retired unused transfer/fee/team-split proposals**: These kinds lost their creation entry points long ago (the PAY/TEAM_PAY tag protocol was retired in schema 6); their settlement and display code is now removed entirely.

### Fixes

- **Rewards wrongly swallowed**: Narrative rewards are no longer dropped by a "completion evidence" heuristic. Previously any conditional word ("if", "later") — or the absence of words like "completed/defeated" — silently discarded a reward with a misleading quest notice, even when it was not quest payment (a real round-5 incident where gold never arrived). Rewards now always become proposals decided by policy and the GM.
- **Stale settlement notice**: After the money settled, the round kept showing "结算待确认：关联结果尚未生效" forever. The notice is now cleared once every decision has resolved, and solo tables show settlement confirmations directly on the shared timeline (multiplayer visibility routing is unchanged: public deals stay public, GM's private deals stay private).

### Breaking changes and upgrade notes

- **AI services require shared provider references**: Narrative LLM, embeddings, speech recognition, and image generation no longer accept the legacy standalone base_url/api_key configuration. After upgrading, confirm each capability's provider reference in the admin provider settings; configurations already using provider references need no change.
- **Save migration to schema 8**: Pending transfer/fee/team-split proposals are superseded (they never charged anyone); committed transactions, balances, and items are untouched.
- The global default cap for auto-settled rewards is now 500; adjust it per game in "Game settings" to match each table's economy (D&D gold and CoC dollars scale independently).
- Upgrading from v2.5.2 needs no extra steps; backing up the complete `data/` directory before upgrading remains recommended for important campaigns.

### Downloads and verification

- **Regular Windows users**: `DiceFrame-v2.5.3-windows-portable.zip`
- **Source-run users**: `DiceFrame-v2.5.3-windows.zip`
- **Managed Docker update**: `DiceFrame-v2.5.3-docker-update-linux-amd64.zip`
- Verify downloads with the Release `SHA256SUMS`. For important campaigns, keep the prior program version and a data backup so rollback remains possible.
