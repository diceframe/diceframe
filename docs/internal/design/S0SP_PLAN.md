# S0 法术线铺设计划

| 项 | 值 |
|---|---|
| 适用范围 | `srd_spells.json` 的 339 条法术在战斗与探索里的可结算性 |
| 分类事实来源 | `SPELL_GAP_MATRIX.md`（308 条缺口按"所需原语"分桶，含阶段归属） |
| 进度事实来源 | `scripts/spell_coverage.py`（**唯一**数字来源；本文不抄数字） |
| 状态 | 生效中；切片开工时在 `SLICE_STATUS.md` 增行 |

## 1. 这份文档解决什么

`SPELL_GAP_MATRIX.md` 回答了「缺什么、属于哪个阶段」，没有回答「做到哪了」。

而手写的进度一定会腐烂：`SLICE_STATUS.md` 里 MM3 标 🔵 时 `#835` 已经合并两个月，F25 标 ⬜ 时 `#830`/`#836` 也已经合了。两处都是因为进度写在文档里，而文档没人跟着改。

所以这里定两件事：

1. **进度只从脚本读**：`python scripts/spell_coverage.py`。它按环阶 × 桶列出全部缺口，每条带环阶；`--check` 是棘轮（实现条数只许涨）。
2. **切片按「法术条数」验收，不按「原语个数」验收**。

## 2. 姿态：垂直切片，不按桶横切

阶段 1B 的教训是 P1–P9 九个原语一次合掉（`#619`），之后内容没人填——因为"原语"没有可验收的交付物，它只是一条管线，管线本身不产出玩家能用的法术。

**每个切片必须交付可施放的法术条数**，验收标准是端到端：

- 施放路径走得通（战斗内 / 战斗外各一条）；
- 结算落在事件流上（`spell.cast` + 后续的 `damage.applied` / `condition.applied` / `healed`）；
- 存档往返不丢（`ruleset_character` 与 `spellcasting.class` 都过一遍 `normalize`）；
- 至少一条用例断言**数值**，而不是"事件里有个字段"。

不满足这四条的不算点亮，脚本也就不该把它算进 `spell_effects`。

## 3. 排期：按环阶三段

环阶是玩家实际的体验单位——一个 5 级角色的法术池是 0–3 环，所以 6 环以上覆盖率再低，也轮不到它先修。当前分布（以脚本输出为准）显示覆盖是从 5 环起**直接归零**的，这就是第一优先级。

### S1 · 1–2 环直填（纯数据，不碰原语）

目标：把 1–2 环从「零头」推到**过半**。这一段的缺口绝大多数集中在两个桶：

- **直填桶**：照已有 14 个 `mode`（`spell_attack` / `saving_throw` / `buff` / `healing` / `temp_hp` / `teleport` / `dispel` / `revive` / `stabilize` / `reaction_defense` / `triggered_damage` / `automatic_damage` / `debuff` / `obscuring_zone`）改数字即可。
- **治疗/复活**：`healing` / `revive` / `stabilize` 三个 mode 已有，缺的是把法术接上去（`aid` 的 temp HP 走 P4 已有的 `temp_hp`）。

验收：新增的每条法术都有端到端用例；`--check` 的基线跟着上调。

### S2 · counterspell（补 P7 的洞）

P1–P9 里唯一「有管线、没法术」的桶。它 1 条，接的是既有反应管线（`reaction_spells.py`）。放独立切片是因为它要动反应窗口，和纯数据的 S1 不同类。

### S3 · 1–3 环控制与位移

`hold` 系、`charm` 系、`command`、`misty_step` 系、`haste`/`slow`。原语（P2 行动门控、P6 位移）已经在，缺的是：

- `condition` 目录的扩充（现有 23 条 `condition`，控制类法术需要更多带"每回合豁免"的状态）；
- 内容本身。

这一片的隐藏成本是 condition 目录，不是法术条数——开工前先数一遍 S3 需要哪些 condition、其中多少已有。

### 不做（明确记录，避免反复讨论）

- **几何 AoE（22）与持续区域（34）**：需要 2D 地图语义，属阶段三。脚本会把它们列出来，那是对的——它们是缺口，只是排在地图之后。
- **6–9 环**：等 1–3 环的形态稳定。届时按「每环至少 N 条能打」定门槛，N 由那时的脚本输出决定。
- **战斗外工具 68 条**：叙事层，不是机械缺口。它们出现在脚本输出里是提醒，不是待办。

## 4. 每片开工前要做的三件事

1. `python scripts/spell_coverage.py` 拿当前基线，记下这一片要吃掉哪几行。
2. 读 `SPELL_GAP_MATRIX.md` 里对应桶的排期理由与"复用/债务关系"那一列——它写明了这个桶会顺带还哪笔债（P5 还 Rage 抗性、P10 还检定意图、P12 是德鲁伊变形线）。
3. 在 `SLICE_STATUS.md` 增行，证据列写 PR 号，**不要**写"已实现"（那正是会腐烂的写法）。

## 5. 脚本用法

```
python scripts/spell_coverage.py            # 人读：环阶表 + 每个桶的逐条清单
python scripts/spell_coverage.py --json     # 机器读：CI 或后续脚本消费
python scripts/spell_coverage.py --check    # 棘轮：实现条数低于基线则退出 1
```

要点：

- 桶分类**从 `SPELL_GAP_MATRIX.md` 解析**，不复制一份。矩阵改名或换格式会让脚本报错退出，而不是静默给出"没有缺口"。
- 矩阵里点名但目录里没有的法术 id，同样报错退出——那是矩阵与目录已经漂移的信号。
- 实现条数取自 `srd_combat_core.json` 的 `spell_effects`，与运行时读取的是同一份数据，不存在"文档说实现了、引擎没有"的错位。

CI 接入的最省事方式是在后端测试里加一条 `subprocess` 调用 `--check`；不急的话先按片手跑，等 S1 落地后再接。
