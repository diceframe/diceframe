# 官方 SRD 来源与可用性核对

> 日期：2026-11-06。范围：SRD 5.2.1（D&D 2024 规则）之官方发布、许可条款、可获取渠道与机器可读性；并给出项目当前覆盖率基线。
> 结论先行：官方 PDF 与 open5e `srd-2024` 两条链路均可用且许可清晰；`dnd5eapi.co` 不含 2024 数据，必须从来源清单中移除；官方没有中文版，项目 zh-CN 属自行改编，署名需补改编声明。

## 1. 权威基准（冻结）

| 项 | 值 |
|---|---|
| 文档 | System Reference Document 5.2.1（"SRD 5.2.1"） |
| 官方入口 | https://www.dndbeyond.com/srd |
| 文件 | https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf |
| 发布 | 2025-05-01（HTTP `Last-Modified: Thu, 01 May 2025 15:59:02 GMT`） |
| 体积 | 6,031,375 字节 |
| SHA-256 | `8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87` |
| 页数 / 书签 | 364 页 / 520 条书签 |
| 许可 | CC-BY-4.0（无 OGL 成分；官方声明后续 SRD 一律仅 CC-BY-4.0） |

**基准冻结规则**：后续所有 SRD 内容批次以本文件的 SHA-256 为唯一判据。官方若发布 5.2.2 或页内修订，先重跑本文第 5 节核对流程，再决定是否迁移；迁移前不得混用两版清单。

## 2. 许可条款（逐字核验）

官方 PDF「Legal Information」给出的唯一允许署名为：

> This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

附加约束：

- 「Please do not include any other attribution to Wizards or its parent or affiliates other than that provided above.」——不得额外署名 Wizards 或其关联方。
- 允许声明 "compatible with fifth edition" / "5E compatible"。
- CC-BY-4.0 含免责条款（Section 5），不得暗示官方背书。

**与项目现状对照**：`templates/rulesets/dnd2024_srd/legal/ATTRIBUTION.md` 的英文段落与上述模板**逐字一致**，未附加任何多余 Wizards 署名，合规。第二段对 DiceFrame 自有内容的边界声明也不构成额外署名。

**缺口（需修）**：CC-BY-4.0 第 3(a)(1)(B) 要求「indicate if changes were made」。项目 zh-CN 目录是对英文 SRD 的**自行翻译与结构化改编**，官方从未发布中文版（见第 3 节），现行 ATTRIBUTION 未声明「已翻译/已改编」。建议在该文件追加一句，例如：

```text
The Simplified Chinese text under locales/zh-CN is an unofficial translation and
restructuring of the English SRD 5.2.1 prepared by DiceFrame; changes were made.
No Chinese-language SRD 5.2.1 is published by Wizards of the Coast.
```

另：`manifest.json` 的 `content_version` 为 `srd-5.2.1+r5`，官方不存在 `r5` 修订号。**但不要直接改这个字段**——它会被写入游戏绑定（`binding.py:15`、`builder.py:324`）并在 `advancement.py:329` 做一致性校验，改动会让既有存档判定版本不匹配。若确需规范化为 `srd-5.2.1`，必须同时提供绑定迁移；否则保持原值，仅在文档侧说明 `+r5` 为项目自有修订号。

## 3. 官方语言版本

官方页面的本地化下载仅包含 **Deutsch / Español / Français / Italiano**（2025-12-08 发布），英文为 5.2.1 原始版。

- **没有官方简体中文版**。项目 `supported_locales: ["zh-CN", "en"]` 中的 zh-CN 全部是自译内容，任何「以官方文本为准」的核对都只能对照英文 PDF。
- 官方 FAQ 明确的**范围外**条目：Artificer 职业、Aasimar 等物种、Beholder 等怪物、被商标替换前的原名（Deck of Many Things → Mysterious Deck、Orb of Dragonkind → Dragon Orb）、2014 规则（SRD 5.1）、被移除的 Half-Elf / Half-Orc、附录 Fantasy-Historical Pantheons / The Planes of Existence。

## 4. 可获取渠道与可用性实测

实测时间 2026-11-06，网络环境为当前开发机（`github.com:443` 与 `ssh.github.com:443` 均被重置，`raw.githubusercontent.com` 连接被关闭，仅 `api.github.com` 与 `media.dndbeyond.com` 可用）。

| 渠道 | 可达 | 许可 | 覆盖版本 | 机器可读 | 结论 |
|---|---|---|---|---|---|
| 官方 PDF（media.dndbeyond.com） | ✅ 200 | CC-BY-4.0 | 5.2.1 | 可解析（需分栏处理） | **权威基准** |
| 官方 SRD 页面（dndbeyond.com/srd） | ✅ | — | 5.2.1 | 否 | 版本与发布信息核对 |
| open5e v2 API（`srd-2024`） | ✅ | CC-BY-4.0 | 5.2.1 | ✅ JSON | **结构化首选** |
| open5e v1 API | ❌ 502 | — | — | — | 停用，勿用 |
| dnd5eapi.co | ✅ | CC-BY-4.0 | **仅 2014（319 法术）** | ✅ | **不含 2024，移出来源清单** |
| 5e-bits/5e-database | ✅ | MIT（代码） | 仅 `main`（2014） | ✅ | 无 2024 分支 |
| downfallx/dnd-5e-srd-markdown | ✅（经 api.github.com） | CC-BY-4.0 | 5.2.1 | 半结构化（章节级单文件 md） | 交叉校验用 |
| foundryvtt/dnd5e | ✅ | 代码 MIT / 内容 CC-BY-4.0 | 2024 | ✅ 紧凑 JSON | **参考实现，不作内容源** |
| 5etools 数据 | — | 边界不清 | — | — | 维持禁用 |

### 4.1 官方 PDF 的解析可行性（已验证）

- `pypdf` 可直接读取 364 页，无加密；书签树 520 条，可定位到章节与子职级条目。
- **坑**：正文为双栏，默认 `extract_text()` 会把左右栏按行交错，直接按行解析会产出大量假条目。必须用 `visitor_text` 取每个文本块的 `(x, y)`，以 `x < 260` 分左右栏、按 `y` 降序拼行，才能还原正确阅读顺序。
- 条目级清单（魔法物品 253 条、专长 17 条）已用上述方法提取，见 `SRD_CONTENT_INVENTORY_2026-11-06.md`。
- 下载：单连接约 8 KB/s（280 s 仅 2.3 MB），**必须分段并行**；8 段 Range 并发可在 88 s 内完成。

### 4.2 open5e v2 的实操要点

按 `document` 过滤时各端点参数名不一致，实际测得的可用写法：

| 端点 | 可用参数 | `srd-2024` 条数 |
|---|---|---|
| `/v2/spells/` | `document__key=srd-2024` | 339 |
| `/v2/feats/` | `document__key=srd-2024` | 17 |
| `/v2/magicitems/` | `document=srd-2024`（`document__key` 会返回未过滤的 2322 计数） | 760 |
| `/v2/species/` | `document__key=srd-2024` | 9 |
| `/v2/backgrounds/` | `document__key=srd-2024` | 4 |
| `/v2/classes/` | `document__key=srd-2024` | 24（12 职业 + 12 子职混编） |
| `/v2/creatures/` | `document__key=srd-2024` | 331 |
| `/v2/weapons/` `/v2/armor/` | `document__key=srd-2024` | 75 / 13 |

**关键口径差**：open5e 的 `magicitems` 把 SRD 的「变体条目」展开了。例如 SRD 只有一条 `Ammunition of Slaying`，open5e 按生物类型拆成 13 条；`Adamantine Armor` 按护甲类型拆成 8 条。因此 760 条 ≠ 253 条，**不能把 760 当作 I0 清单**；导入时必须按 SRD 原始条目归组回 253。

## 5. 官方条目计数（PDF 实测）

| 域 | 官方数量 | 依据 |
|---|---|---|
| 职业 | 12 | 书签逐条，p28–p82 |
| 子职 | 12（每职业 1 个） | 书签 `X Subclass: Y` 逐条 |
| 物种 | 9 | p83–p86 |
| 背景 | 4（Acolyte / Criminal / Sage / Soldier） | p82 |
| 专长 | 17（Origin 4 / General 2 / Fighting Style 4 / Epic Boon 7） | p86–p89，逐条提取 |
| 法术 | 339 | p106 起，与 open5e 一致 |
| 魔法物品 | **253** | p209–p253 逐条提取；需同调 119 条 |
| 非魔法武器 / 护甲 | 75 / 13（open5e 口径） | `/v2/weapons/` `/v2/armor/` |
| 怪物 | 331（open5e 口径） | `/v2/creatures/` |
| 规则术语 | Rules Glossary 占 p176–p191（16 页） | 书签 |

魔法物品类别分布：Wondrous Item 123、Weapon 33、Potion 23、Ring 22、Armor 19、Wand 13、Staff 12、Rod 7、Scroll 1。稀有度分布：Rare 84、Uncommon 75、Very Rare 53、Legendary 31、Varies 7、Common 2、Artifact 1。

> 与第三方站点常见的「258 件」存在 5 条差异，源于「表格内变体是否单独计条」。本项目以 PDF 条目（即条目名加类别/稀有度行的独立段落）为口径，253 为基线；如有争议按 inventory 文档逐条复核。

## 6. 项目现状对照

| 域 | SRD 5.2.1 | 项目当前 | 缺口 |
|---|---|---|---|
| 职业 | 12 | 12（`classes/*.json` 结构完整，含 `features_by_level`） | — |
| 子职 | 12 | 12（`srd_subclasses.json`，含 `feature_ids_by_level`） | — |
| 职业本体特性 | 131 个唯一 id | **11 个已定义** | 120 |
| 子职特性 | 58 个唯一 id | **0 个已定义**（全部幽灵 id） | 58 |
| 专长 | 17 | 6 个文件 / 覆盖 4 条（Alert、Magic Initiate×3、Savage Attacker、Skilled） | 13 |
| 法术 | 339 | 339 条元数据 + 31 条战斗效果 | 308 条无机械效果 |
| 魔法物品 | 253 | **0** | 253 |
| 物种 | 9 | 9 | — |
| 背景 | 4 | 4 | — |
| 规则术语 | 16 页 | 0 | 全部（展示层） |

职业本体特性按职业的缺口：barbarian 16、bard 11、cleric 10、druid 12、fighter 12、monk 19、paladin 15、ranger 16、rogue 15、sorcerer 9、warlock 11、wizard 9。

**已定义 11 个**：`rage`、`action_surge`、`second_wind`、`martial_arts`、`monks_focus`、`flurry_of_blows`、`patient_defense`、`step_of_the_wind`、`paladins_smite`、`sneak_attack`、`uncanny_dodge`。

职业 + 子职特性合计 189 个唯一 id，已定义 11 个，覆盖率 **5.8%**。

另：官方内容中**没有任何带 `charges` 字段的魔法物品**。此前「D3b 后端已就绪但无官方载体」的判断成立——D3b 的充能/销毁能力目前只能由测试 fixture 验证，首个真实载体必须等 I0 导入魔法物品后才能落地。

## 7. 对主计划的修订项

1. **来源表述纠错**（`DND2024_FULL_EXPERIENCE_PLAN.md` §上游与许可）：现写「open5e / dnd5eapi.co：SRD 5.2 结构化数据」——`dnd5eapi.co` 实测只有 2014 数据（`/api/spells` 返回 319 条），必须删除；改为「open5e v2 `srd-2024`（CC-BY-4.0）」。
2. **新增基准冻结**：以本文第 1 节的 SHA-256 作为唯一版本判据，清单见 `SRD_CONTENT_INVENTORY_2026-11-06.md`。
3. **I0 条目基线修正**：原「（DMG2024 约 348 条）」相关表述改为 SRD **253** 条；open5e 的 760 条为变体展开口径，不作清单用。
4. **中文合规**：`legal/ATTRIBUTION.md` 追加翻译/改编声明（见第 2 节）。`manifest.json` 的 `content_version: srd-5.2.1+r5` **不要直接改**——该字段写入游戏绑定并参与一致性校验（`advancement.py:329`），改动会令既有存档判定版本不匹配；如需规范化必须配套绑定迁移，否则保留原值。
5. **F0 / S0 可直接开工**：专长 17 条与 12 子职特性清单已逐条冻结并带页码，无需再做清单调研。

## 8. 复核流程（可重跑）

仓库内已提供可执行脚本 `scripts/srd_extract_baseline.py`（仅依赖 `pypdf`，已是项目依赖）：

```bash
python scripts/srd_extract_baseline.py --pdf <SRD_CC_v5.2.1.pdf> --out <输出目录>
```

脚本会做三件事，任何一步不符即中止：

1. 校验 SHA-256 与 364 页数，不符则提示先更新本文档再迁移；
2. 输出 `toc.json`（520 条书签，含印刷页码）；
3. 按双栏还原提取 `items.json`（魔法物品）与 `feats.json`（专长）。

当前基准下的预期输出：

```
pages      364
bookmarks  520
items      253  (需同调 119)
feats      17
```

另外可抽查 open5e `srd-2024` 的计数（spells 339 / feats 17 / species 9 / backgrounds 4）；任何一项不一致即停止导入，先更新本文件。

**书签页码的使用注意**：书签页码指向章节标题行所在页，章节正文常从该页右栏开始并延伸到下一章节标题所在页。按书签做页码范围时结束页要包含下一个章节的起始页，否则会截断末尾条目（魔法物品章节末尾的 Wand of Wonder / Wings of Flying、专长章节末尾的 Epic Boon 都踩过这个坑）。
