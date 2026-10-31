# D&D 2024 法术缺口矩阵（308 条逐条分类）

> 维护者内部文档。来源：`srd_spells.json` 339 条元数据 − `srd_combat_core.json` 的 `spell_effects` 31 条 = 308 条无机械效果。
> 分类方法：逐条按规则书语义归入"所需效果原语"桶，脚本校验无重复无遗漏（2026-10-29）。
> 用途：阶段 1B / 阶段二排期依据。分类是工程判断，不是规则裁定；个别法术跨桶时按主要结算需求归桶。

## 总览

| 桶 | 条数 | 归宿 |
|---|---|---|
| 直填（单体攻防/增益，照 31 条模板改数字） | ~40 | 阶段二数据填充（其中 8 条例外需小原语，见 P1/P3） |
| 几何依赖（aeo 22 + zone 34） | 56 | **阶段三之后**（2D 地图语义） |
| 战斗外工具/侦查/社交 | 68 | 叙事层，非硬缺口 |
| 其余（需 P1–P13 原语） | ~144 | 见下表 |

两个已验证的修正性事实（推翻"多目标=需要地图"的直觉）：

- **点名制多目标不需要 2D**：`validation.py` L435-442 已支持 `target_ids` 列表（去重、`target_count` 上限、敌我侧校验），bless 的 3 目标即此机制。几何 AoE（按位置圈人）才需要地图。
- **condition 目前不阻止行动**：睡眠术的 `incapacitated` 只是标记，回合跳过只认 `dead`/`stable`（`resolution.py` L624-629）。hold 系缺的是"门控"，不是"回合接管"。

## P1–P13：1D theater 下可做的原语管线

| # | 原语 | 点亮 | 代表法术 | 复用/债务关系 | 规模 |
|---|---|---|---|---|---|
| P1 | attack_sources 数据驱动（感官修正：来袭攻击优劣势） | ~10 | invisibility, greater_invisibility, blur, mirror_image, mislead, seeming, sanctuary | `check_modes.py` 硬编码 5 来源之债（阶段一 B 层已记录）；同时服务职业隐形/宿敌系 | 中 |
| P2 | condition 行动门控 + 每回合豁免 tick | ~8 | hold_person, hold_monster, power_word_stun, irresistible_dance；补全 sleep/color_spray | 切片 2 condition 基建 + 既有回合跳过逻辑 | 中 |
| P3 | 多目标结算循环（含多 beam 攻击） | ~8 | mass_healing_word, prayer_of_healing, mass_cure_wounds, mass_heal, scorching_ray, eldritch_blast 升环 beam | `target_ids` 校验已有 → resolution 循环；切片 1 `count` 循环先例 | 小 |
| P4 | temp HP / max HP 修改 | ~4 | false_life, aid, death_ward | 切片 1 heal 原语扩展（伤害先扣 temp） | 小 |
| P5 | 伤害类型抗性/免疫 | ~5 | protection_from_energy, stoneskin, protection_from_poison, fire_shield | 切片 3 伤害修正窗口同构（×0.5 → 按类型）；**同时偿还 Rage 裁剪的抗性债** | 中 |
| P6 | 1D 位移 + 速度修改 | ~12 | misty_step, dimension_door, haste, slow, longstrider, expeditious_retreat, levitate, spider_climb | position 标量重设 + economy.speed；theater 下语义完整 | 中 |
| P7 | 施法反应（触发源 hit → cast） | 1 | counterspell | 切片 3 反应管线同构扩展；法术对战核心 | 中 |
| P8 | dispel（魔法 condition 移除） | ~5 | dispel_magic, remove_curse, lesser/greater_restoration | condition 移除事件已有；需"按来源类型筛选" | 小 |
| P9 | 复活（dead → 存活） | ~4 | revivify, raise_dead, resurrection | dead condition 反向 + 缓死术先例 | 中 |
| P10 | 属性检定意图 | ~6 | guidance, resistance, enhance_ability, pass_without_trace, glibness | 新 intent 类型；战斗外技能检定同管线；阶段一裁掉 ability_check 的还债 | 中 |
| P11 | 召唤（combatant 生成 + 托管回合） | ~15 | conjure 系, animate_dead, find_steed/familiar, faithful_hound | hosted AI 回合管线；阶段三 token 层的前置 | 大 |
| P12 | 属性覆盖/变形 | ~8 | polymorph, shapechange, alter_self, gaseous_form, enlarge_reduce | character 字段 override；druid 变形线同管线 | 大 |
| P13 | 回合接管 | ~8 | command, dominate 系, suggestion, geas | AI 意图注入；最难 | 大 |

**排期结论**：

- **阶段 1B = P1–P9**（+ 直填 ~40）：全部 1D 语义完整，合计点亮约 100 条法术；做完后 1–5 级团法术体验基本完整。每个 P 一个切片，复用阶段一工作流（任务书 / 白名单 / 停止条件 / 执行报告），各选一个代表法术做垂直验证（P1→invisibility，P2→hold_person，P5→stoneskin，P7→counterspell）。
- **P10–P13 放阶段二后段**：与职业 catalog 填充交错（它们同样是职业特性的宿主：P5 还 Rage 抗性债，P10 还检定债，P12 是 druid 变形线，P11 是阶段三 token 层前置）。
- **P1–P5 是法术与职业特性共用底座**，投资双倍回收，优先做。

## 附：308 条逐条清单（按桶，id 为 canonical ref）

### 几何 AoE（22，阶段三）

entangle, grease, ice_knife, shatter, fireball, lightning_bolt, fear, hypnotic_pattern, major_image, sleet_storm, cone_of_cold, circle_of_death, chain_lightning, freezing_sphere, fire_storm, prismatic_spray, divine_word, sunburst, meteor_swarm, earthquake, incendiary_cloud, sunbeam

### 持续区域/墙（34，阶段三）

fog_cloud, darkness, flaming_sphere, moonbeam, silence, spike_growth, spiritual_weapon, web, call_lightning, gust_of_wind, spirit_guardians, stinking_cloud, wall_of_fire, black_tentacles, ice_storm, resilient_sphere, cloudkill, insect_plague, wall_of_force, wall_of_stone, blade_barrier, wall_of_ice, wall_of_thorns, antilife_shell, forcecage, storm_of_vengeance, tsunami, prismatic_wall, antimagic_field, holy_aura, aura_of_life, daylight, hallucinatory_terrain, wind_wall

注：1D 位置区间是 2D zone 的退化形式——阶段三的 zone 管线若按"位置区间"抽象设计，1D/2D 可共用。

### 战斗外工具（68，叙事层）

dancing_lights, druidcraft, elementalism, light, mage_hand, mending, message, minor_illusion, prestidigitation, thaumaturgy, comprehend_languages, create_or_destroy_water, detect_evil_and_good, detect_magic, detect_poison_and_disease, disguise_self, identify, illusory_script, purify_food_and_drink, speak_with_animals, floating_disk, silent_image, arcane_lock, augury, continual_flame, darkvision, find_traps, knock, locate_animals_or_plants, locate_object, rope_trick, sending, tongues, water_breathing, water_walk, clairvoyance, create_food_and_water, meld_into_stone, speak_with_dead, speak_with_plants, plant_growth, arcane_eye, fabricate, private_sanctum, secret_chest, stone_shape, awaken, commune, commune_with_nature, contact_other_plane, creation, dream, legend_lore, telepathic_bond, heroes_feast, magnificent_mansion, mirage_arcane, project_image, sequester, demiplane, scrying, instant_summons, arcanist_s_magic_aura, detect_thoughts, divination, find_the_path, locate_creature, wish

### 直填桶 single（46；~40 直填 + 例外见 P1/P3/P12）

shillelagh, true_strike, dissonant_whispers, divine_favor, ensnaring_strike, hunter_s_mark, hellish_rebuke, mage_armor, ray_of_sickness, sanctuary, searing_smite, acid_arrow, barkskin, blindness_deafness, blur, dragon_s_breath, flame_blade, heat_metal, invisibility, magic_weapon, mind_spike, mirror_image, ray_of_enfeeblement, scorching_ray, shining_smite, warding_bond, beacon_of_hope, bestow_curse, vampiric_touch, blight, fire_shield, guardian_of_faith, greater_invisibility, phantasmal_killer, vitriolic_sphere, contagion, flame_strike, mislead, seeming, eyebite, harm, disintegrate, finger_of_death, power_word_kill, foresight, phantasmal_force

### 位移/变形 move（37）

expeditious_retreat, feather_fall, jump, longstrider, alter_self, enlarge_reduce, levitate, misty_step, spider_climb, blink, fly, gaseous_form, haste, slow, passwall, dimension_door, freedom_of_movement, polymorph, reverse_gravity, tree_stride, telekinesis, wind_walk, move_earth, etherealness, plane_shift, teleport, teleportation_circle, word_of_recall, transport_via_plants, shapechange, true_polymorph, animal_shapes, control_water, control_weather, maze, banishment, astral_projection

注：跨平面旅行（plane_shift / etherealness / astral_projection / gate）在 1D 下无机械语义，归叙事层。

### 心灵控制 control（25）

animal_friendship, command, hideous_laughter, calm_emotions, enthrall, hold_person, suggestion, zone_of_truth, charm_monster, compulsion, confusion, dominate_beast, geas, dominate_person, modify_memory, hold_monster, flesh_to_stone, irresistible_dance, mass_suggestion, power_word_stun, antipathy_sympathy, befuddlement, dominate_monster, weird, imprisonment

### 召唤 summon（23）

find_familiar, unseen_servant, find_steed, phantom_steed, faithful_hound, giant_insect, animate_dead, conjure_animals, conjure_woodland_beings, conjure_minor_elementals, animate_objects, conjure_elemental, summon_dragon, conjure_fey, create_undead, planar_ally, conjure_celestial, arcane_sword, arcane_hand, simulacrum, animal_messenger, tiny_hut, gate

### 治疗/复活 life（19）

false_life, goodberry, aid, prayer_of_healing, gentle_repose, revivify, mass_healing_word, raise_dead, reincarnate, death_ward, heal, mass_cure_wounds, regenerate, resurrection, mass_heal, power_word_heal, true_resurrection, clone, magic_jar

### 反制/防护 counter（16）

protection_from_evil_and_good, protection_from_poison, protection_from_energy, lesser_restoration, remove_curse, greater_restoration, dispel_magic, dispel_evil_and_good, counterspell, magic_circle, nondetection, see_invisibility, true_seeing, stoneskin, globe_of_invulnerability, mind_blank

### 延时触发 trigger（12）

alarm, magic_mouth, glyph_of_warding, contingency, delayed_blast_fireball, programmed_illusion, symbol, planar_binding, hallow, guards_and_wards, forbiddance, time_stop

注：glyph/symbol 有几何成分归阶段三；time_stop 是回合结构问题（切片 4 的 economy 只给"多一个动作"，时间停止要"多整个回合"）。

### 属性检定 check（6）

guidance, resistance, enhance_ability, pass_without_trace, glibness, bane

注：guidance 是 SRD 使用率最高的戏法之一，因无 ability_check 管线而不可用（阶段一切片 2 的刻意裁剪）。
