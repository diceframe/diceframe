# DiceFrame v3.4.0

3.3.3 之后的特性版本：D&D 2024 的武器精通、职业与子职特性、怪物与 DM 工具大面积落地，同时带来本地向量模型开箱可用、规则集专属 AI 工具、多语言检定与错误码、叙事质量闸门，以及一批经济与角色修复。可从 3.3.3 直接升级，无需新增存档迁移。

## 中文

### D&D 2024：战斗与武器精通

- **武器精通八条全部生效**：新增 Cleave（命中后的免费第二击）、Nick（额外攻击不占附赠动作）、Push（推开目标）与 Topple（豁免失败倒地）；连同既有的 Graze、Sap、Vex、Slow，八条词条全部实装。推挤方向由服务端按双方位置统一计算。（#895）
- **诡诈打击六种效果**：淬毒、摔绊、撤步、恍惚、击昏、眩目都按偷袭骰数量扣费；攻击面板可以多选效果，并显示骰池与花费。（#871）
- **隐匿需要遮蔽或掩护**：Hide 要求重度遮蔽或至少 3/4 掩护；不可用时行动面板直接标出原因，且拒绝不消耗动作。同时新增五项附赠动作特性：Instinctive Pounce、Tactical Shift、Steady Aim、Fleet Step 与 Heightened Focus。（#828）
- **武僧拨挡攻击 / 拨挡能量**：在反应窗口内减伤；当这次攻击被减到 0（且没有后续伤害段）时，可把伤害掷回攻击者。拨挡能量把减伤范围扩到所有伤害类型。（#881）
- **猎人防御战术**：Escape the Horde 让借机攻击带劣势，Multiattack Defense 让同一敌人后续攻击带劣势；两者都会在一整批多重攻击中逐击生效。（#876、#891）
- **修复**：Light 追加攻击按触发它的武器判定；角色自带的感官（如 18 级游侠的盲视）现在参与遮蔽判定。（#875、#870）

### D&D 2024：职业与子职

- **野性形态（Wild Shape）**：附赠动作变形，临时生命等于德鲁伊等级，可直接使用形态自带的攻击（命中加值取 stat block），失能、死亡或到期时自动复原；升级时选形态，长休换一个，旧存档可继续读取。（#836、#838）
- **大地结社**：Land's Aid（区域伤害并治疗）、Nature's Ward（Poisoned 免疫与地形抗性）、Nature's Sanctuary（15 尺立方，给盟友半掩护与抗性）。（#839）
- **五个跨战斗长时特性**：Intimidating Presence、Dragon Wings、Holy Nimbus、Quivering Palm、Hurl Through Hell，按世界时钟到期；没有生效效果时「结束」不再出现。（#841、#852）
- **邪术祈唤**：完整的数量、前置、逐级替换与对象绑定，建卡和升级面板可选可换；八项中六项有实际效果（Agonizing Blast、Eldritch Mind、Eldritch Spear、Fiendish Vigor、Repelling Blast、Witch's Sight）。（#892）
- **专长一致性**：Alert 的先攻加值在建卡与升级重算两条路径上一致；Fleet Step 也能放宽花 Focus 的 Step of the Wind。（#889）
- **语言与简单授予**：Druidic、Thieves' Cant 与九条新语言进入目录；Divine Order、Primal Order、Deft Explorer 可选；Primal Knowledge 的属性替换、技能加值与熟练授予落到角色卡，保存往返不再丢失这些选择。（#893）

### D&D 2024：法术与施法

- **术士**：十个超魔选项（名额、点数与资格按原文）、Innate Sorcery 的开启与适用范围、升级选择与逐级替换；Extended Spell 对 Hex 这类专注法术不再失效。（#847）
- **仪式与法术书**：Ritual Adept 可无槽仪式施法，法术位耗尽时仪式法术仍在列表；Memorize Spell 支持短休换法术；Evocation Savant 的额外法术进入法术书；盗贼的 Use Magic Device 多 4 个同调位，充能豁免掷 1d6 并记录结果。（#869）
- **自然恢复**：德鲁伊长休免费施放一次（不耗法术位）、短休换回法术位；短休面板显示每环阶预算与仪式入口。（#868）

### D&D 2024：怪物与 DM 工具

- **怪物豁免**：按 stat block 六维属性计算，未印出的豁免取裸调整值；CR 0–1 的 135 条生物进入目录，可按类型查找。（#829）
- **怪物攻击**：多段伤害分别过抗性，命中附带倒地、擒抱、推挤、中毒、魅惑等效果，支持 Multiattack、豁免型动作与充能，以及八种被动特质；被擒抱或倒地可用挣脱与起身（起身花半速移动）。（#835）
- **GM 状态**：战斗面板可施加或移除 14 条规则书公共状态；来源绑定效应不能手工施加，移除不设限。（#832）
- **GM 检定**：发起检定时可指定属性或技能，加值取自角色卡真实的技能数值（属性、熟练、专精等均已计入）。（#833）

### 平台与体验

- **本地向量模型开箱可用**：Windows x64 便携版/安装版与 Docker linux/amd64 随版本提供锁定哈希的本地推理运行时，设置里的模型卡可直接「下载并启用」，无需自行安装依赖；已下载模型继续放在 `data/models`。发布包体积会相应增大。（#831）
- **规则集专属 AI 工具**：规则集可以为 AI GM 声明只在自身对局可见、可调用的工具；首个是 D&D 的掩护裁定，会主动列出可选目标，失败时返回可用清单。（#834、#890）
- **检定与错误码多语言化**：检定成功等级改为语言无关的 code，切换语言不再影响幸运、推骰、SAN 等机制，检定文本按对局语言显示；接口错误在抛错处声明 code，日语/德语/俄语不再拿到无错误码的英文兜底，同时修复「改写进行中」的 409 从未触发的问题。（#882、#896）
- **叙事质量闸门**：叙事回合落库前校验结构化输出与多人操作权限，允许一次有界修复，第二次不合格即拒绝并保持原有回滚。（#840）
- **对话不再中途截断**：对话模式下超过 500 字的回复不再被硬裁到 400 字；只在失控长度（中日 1500、英德俄 3600 字符）时按整段封顶。（#842）
- **角色卡修复**：人物资料在入局后不再退回初始状态，旁白 GM 能看到你写的称谓、外貌、性格、经历、理想、牵绊与弱点；卡库自动存回不再抹掉卡的插件归属与酒馆原始数据。（#873）
- **经济与商店**：每个对局在创建时锁定自己的货币体系，不再受共享规则模板默认值影响；目标含糊的购买（如「报纸」对「今天的报纸」）改为 fail closed，除非存在明确的免费发放；不可解析的既有经济快照会保留并在读取时报错，而不是静默清空。（#837）
- **修复**：角色卡生命条在归零或稳定后不再显示满血；休息准入统一读权威生命值，已错位的存档在读取时修正显示字段。（#880）

### 范围与已知限制

- 怪物目录目前覆盖 CR 0–1（135 条）；CR ≥ 2 的生物尚未导入。
- 野性形态可以使用形态攻击，但形态自身的 Multiattack 与部分特质尚未消费；变形（Polymorph）作为法术效果仍不可用。
- GM 检定请求目前支持属性/技能，尚不支持豁免检定；AI GM 也还不能主动施加状态或发起检定。
- 玩家的种族暗视与光照/魔法黑暗模型仍未接线。

### 升级说明

- 从 3.3.3 可直接升级，无需新增存档迁移；实例存档 schema 仍为 48。
- 新增可选存档字段：对局配置的 `currency_label` / `currency_system`，检定记录的 `verdict_code` / `success`；旧存档按既有行为读取，检定 code 由骰值事实重算。
- 接口错误响应新增 `error_code`；角色、战斗扩展与规则集 Agent 工具从 `code` 改为 `error_code`，路由同时兼容旧拼写。
- 升级前请备份完整的 `data/`。此前已被旧 bug 截断的回复或被清空的角色资料不会追溯恢复。

## English

Feature release after 3.3.3: a large D&D 2024 landing (weapon masteries, class and subclass features, monsters and GM tools), plus out-of-the-box local embedding models, ruleset-scoped AI tools, language-independent checks and error codes, a narrative quality gate, and a batch of economy and character fixes. Direct upgrade from 3.3.3; no new save migration.

### D&D 2024: Combat and weapon mastery

- **All eight weapon masteries are live**: Cleave (a free second attack on a hit), Nick (the extra attack no longer costs a Bonus Action), Push (move the target away) and Topple (a failed save knocks it Prone) join Graze, Sap, Vex and Slow. Push direction is computed server-side from both positions. (#895)
- **All six Cunning Strike effects**: Poison, Trip, Withdraw, Daze, Knock Out and Obscure cost Sneak Attack dice; the attack panel lets you pick several effects and shows the dice pool and cost. (#871)
- **Hide needs obscurement or cover**: Hide requires Heavy Obscurement or at least three-quarters cover; when unavailable the action list says why and a rejection costs no action. Five Bonus Action features land: Instinctive Pounce, Tactical Shift, Steady Aim, Fleet Step and Heightened Focus. (#828)
- **Monk Deflect Attacks / Deflect Energy**: reduce damage in a reaction window; if the attack is reduced to 0 (and has no further damage parts), you can throw the damage back at the attacker. Deflect Energy widens the reduction to every damage type. (#881)
- **Hunter Defensive Tactics**: Escape the Horde imposes Disadvantage on Opportunity Attacks, and Multiattack Defense imposes it on the same creature's later attacks; both apply swing by swing across a full Multiattack batch. (#876, #891)
- **Fixes**: the Light extra attack is judged by the weapon that enabled it; senses a character actually carries (such as a level-18 Ranger's Blindsight) now reach obscurement resolution. (#875, #870)

### D&D 2024: Classes and subclasses

- **Wild Shape**: transform as a Bonus Action with Temporary HP equal to your Druid level, use the form's own attacks (attack bonus from its stat block), and revert automatically on Incapacitation, death or expiry; choose forms on level-up and swap one on a Long Rest, while legacy saves still load. (#836, #838)
- **Circle of the Land**: Land's Aid (area damage plus healing), Nature's Ward (Poisoned immunity and terrain resistance) and Nature's Sanctuary (a 15-ft Cube granting allies Half Cover and that resistance). (#839)
- **Five long-lived features**: Intimidating Presence, Dragon Wings, Holy Nimbus, Quivering Palm and Hurl Through Hell expire on the world clock; "end" no longer appears when nothing is active. (#841, #852)
- **Eldritch Invocations**: full count, prerequisites, per-level replacement and object binding, selectable and swappable in the character builder and level-up panels; 6 of the 8 options have real effects (Agonizing Blast, Eldritch Mind, Eldritch Spear, Fiendish Vigor, Repelling Blast, Witch's Sight). (#892)
- **Feat consistency**: Alert's initiative bonus is identical on character creation and level-up recompute, and Fleet Step also relaxes the Focus-priced Step of the Wind. (#889)
- **Languages and simple grants**: Druidic, Thieves' Cant and nine new languages enter the catalog; Divine Order, Primal Order and Deft Explorer are selectable; Primal Knowledge's ability swap, skill bonus and proficiency grants reach the character sheet, and save round-trips no longer drop these choices. (#893)

### D&D 2024: Spells and casting

- **Sorcerer**: all ten Metamagic options (slot limits, costs and eligibility per the rules), Innate Sorcery activation and scope, and level-up selection and replacement; Extended Spell no longer fails on concentration spells such as Hex. (#847)
- **Rituals and spellbooks**: Ritual Adept casts rituals without a slot (rituals stay listed when slots run out), Memorize Spell swaps spells on a Short Rest, Evocation Savant adds its extra spells to the spellbook, and the Thief's Use Magic Device grants 4 extra attunement slots and rolls d6 charges with the result recorded. (#869)
- **Natural Recovery**: the Druid casts once for free per Long Rest without a slot and recovers slots on a Short Rest; the Short Rest panel shows the per-level budget and the ritual entry point. (#868)

### D&D 2024: Monsters and GM tools

- **Monster saving throws**: computed from the stat block's six abilities, with unprinted saves falling back to the bare modifier; 135 CR 0–1 creatures enter the catalog and can be browsed by type. (#829)
- **Monster attacks**: multiple damage parts each check resistances; hits carry riders such as Prone, Grapple, Push, Poisoned and Charmed; Multiattack, save-based actions with recharge and eight passive traits are supported; Grappled or Prone targets can escape and stand up (standing costs half speed). (#835)
- **GM conditions**: apply or remove 14 public rulebook conditions from the combat panel; source-bound effects cannot be applied by hand, while removal is unrestricted. (#832)
- **GM checks**: check requests can name an ability or skill, and modifiers come from the character sheet's real skill values (ability, proficiency and expertise already included). (#833)

### Platform and experience

- **Local embeddings work out of the box**: Windows x64 portable/installer and Docker linux/amd64 now ship a pinned-hash local inference runtime, so the Settings model card can download and enable a model without installing dependencies; downloaded models stay in `data/models`. Release packages grow accordingly. (#831)
- **Ruleset-scoped AI tools**: a ruleset can declare AI GM tools visible and callable only in its own games; the first is D&D cover adjudication, which lists available targets and returns a usable list on failure. (#834, #890)
- **Language-independent checks and error codes**: check success levels are now codes, so switching languages no longer changes luck, push or SAN mechanics, and verdict text renders in the game's language; interface errors declare a code at the raise site, so ja/de/ru clients no longer get an English fallback without a code, and the "rewrite in progress" 409 now actually fires. (#882, #896)
- **Narrative quality gate**: narration is validated for structured output and multiplayer authority before state is applied; one bounded repair is allowed, and a second rejection fails closed while preserving rollback. (#840)
- **Dialogue no longer cut mid-sentence**: replies over 500 characters in dialogue mode are no longer hard-trimmed to 400; trimming is paragraph-level and only triggers on runaway length (1500 CJK / 3600 Latin characters). (#842)
- **Character card fixes**: profile fields no longer reset after a card enters a game, the narrator now receives the title, appearance, personality, history, ideals, bonds and flaws you wrote, and library auto-save no longer wipes a card's plugin provenance or SillyTavern source data. (#873)
- **Economy and shops**: each game locks its currency system at creation instead of inheriting the shared rule template default; ambiguous purchase targets (such as "newspaper" versus "today's newspaper") fail closed unless an explicit free grant exists; unreadable stored economy snapshots are preserved and rejected on read instead of silently cleared. (#837)
- **Fixes**: the character sheet no longer shows full HP after a downing blow or a stabilized death save; rest admission reads canonical HP, and already-diverged saves have their display field repaired on load. (#880)

### Scope and known limits

- The monster catalog currently covers CR 0–1 (135 creatures); CR ≥ 2 creatures are not imported yet.
- Wild Shape can use its form attacks, but the form's own Multiattack and some traits are not consumed yet; Polymorph as a spell effect remains unavailable.
- GM check requests support abilities and skills, not saving throws yet; the AI GM also cannot apply conditions or initiate checks.
- The player's racial Darkvision and the light/magical-darkness model are still not wired.

### Upgrade notes

- Direct upgrade from 3.3.3 requires no new save migration; the instance save schema remains 48.
- New optional persisted fields: `currency_label` / `currency_system` in game configuration, and `verdict_code` / `success` on check records; old saves keep existing behavior, and check codes are recomputed from stored dice facts.
- Error responses gain `error_code`; characters, combat extensions and ruleset agent tools move from `code` to `error_code`, and routes accept both spellings.
- Back up the entire `data/` directory before upgrading. Replies already truncated or profiles already wiped by the old bugs are not retroactively restored.

## Downloads

- Windows 安装包 / installer · `DiceFrame-v3.4.0-windows-setup.exe`
- Windows 便携版 / portable · `DiceFrame-v3.4.0-windows-portable.zip`
- 源码包 / source package · `DiceFrame-v3.4.0-windows.zip`
- 托管 Docker 更新包 / managed Docker update · `DiceFrame-v3.4.0-docker-update-linux-amd64.zip`
- 校验和 / checksums · `SHA256SUMS`