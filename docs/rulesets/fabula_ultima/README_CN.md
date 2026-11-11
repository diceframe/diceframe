# FU 兼容规则（`fabula_ultima`）

兼容 Fabula Ultima 的第一方规则包，由社区玩家的私有分支整理并入。本文说明它覆盖什么、
怎么接入通用层、哪些是房规，以及许可边界。

## 接入方式

| 能力 | 位置 | 通用接缝 |
|---|---|---|
| 规则运行时（冲突步骤制、物语点、休息、成长、GM 工具） | `src/rulesets/fabula_ultima/` | `RulesetRuntime`，在 `src/rulesets/builtin.py` 注册 |
| 叙事检定（两枚属性骰相加 vs DL、机会、高值） | `check_mechanic.py` | `src/engine/check_mechanics.py` 骰制注册表，`dice_system = "fu"` |
| 每轮提示（冒险节点、叙事纪律） | `runtime.py` | `RoundPromptRuntime`（`src/rulesets/contracts.py`） |
| 规则模板与提示词 | `templates/rules/fabula_ultima.json`、`locales/en/` | 规则模板 + 内容语言 overlay |
| 怪物 / 物品 / 技能默认表 | `templates/rulesets/fabula_ultima/` | 首次编辑后写到 `<data>/templates/fabula_ultima/`，默认表不覆盖用户数据 |
| 局内面板、战斗记录、数据页 | `frontend-v2/src/features/rulesets/fabula_ultima/` | `features/rulesets/registry.ts` 的侧栏 / 时间线面板 |

通用引擎、通用路由与通用组件里没有 FU 分支：商人、战利品、旅行、机会、命刻、GM 手动叙事
都作为规则意图走 `/api/games/{game_key}/intents`，身份（仅 GM / 仅本席位）由运行时校验。

所有面向玩家、GM 与模型的文字按对局语言渲染，提供 zh-CN 与 en。

## 与官方规则的差异（房规）

以下是贡献者的玩法设计，不是官方规则，也不以「忠实还原」为验收目标：

- **冲突步骤制**：不使用全局轮次，由 GM 逐步点选行动者，同一角色可以连续行动。
- **战利品**：每名角色掷 2d10，取难度不超过点数和的战利品里难度最高的一件。
- **商人**：只有真人玩家参与的回合制购物，全员跳过或售罄即结束。
- **旅行**：GM 选定骰面，1 为意外发现、2–5 平安、6 以上遭遇危险，只写叙事不自动开战。
- **休息**：HP / MP 回满并清除状态，不恢复物资点与物语点。

已知与官方数值不一致、尚未校正的地方：

- 建卡固定 5 级，HP 取 `力量骰面 × 5 + 20`、MP 取 `意志骰面 × 5`，没有计入等级与职业加值。
- 物语点在规则模板里声明了上限 10，官方规则没有上限。
- 冲突内重投是恢复快照后整次行动重跑，官方规则是重掷一枚或两枚骰子。
- 敌人普通攻击固定用两枚 d8，没有按怪物条目的属性骰取骰。
- 只覆盖起手职业的技能结算；仪式、项目、NPC 同伴、反派多行动没有实现。

## 已知限制

- 技能、职业与物品以中文名作为标识（数据表的 `id`、`class_levels` 的键、技能结算表的键）。
  英文对局里界面、提示与日志是英文，但这些名字仍是中文；要翻译需先把标识与显示名分开。
- 叙事检定的「物语点重掷并重算本轮」没有并入：原实现整局深拷贝后回写，绕过状态归属，
  需要挂到既有的回合回滚事务上重做。冲突内的重投不受影响。
- 贡献分支带的快速开始冒险没有并入（见下）。

## 许可

本规则包依据 Fabula Ultima Third-Party Tabletop License 1.0 发布，声明全文见
`templates/rulesets/fabula_ultima/legal/ATTRIBUTION.md`。据此：

- 规则显示名不含 “Fabula” / “Fabula Ultima”，只在说明里注明兼容；
- 只引用许可第 3 条允许的内容（基础武器 / 防具 / 物品、职业与技能的规则和术语）；
- 不包含官方美术、标志、冒险与设定文本——官方快速开始里的角色、敌人和剧情不在本仓库；
- 需要配合官方核心规则书使用；包含 AI 生成内容。

新增内容时请保持这几条边界。
