// Pure helpers shared by the combat panel and its encounter-prep section.

export function localizedTerm(value: unknown, locale: string): string {
  const raw = String(value || '')
  const key = raw.trim().toLowerCase().replace(/[\s-]+/g, '_')
  const labels: Record<string, [string, string]> = {
    tutorial: ['教学', 'Tutorial'], standard: ['标准', 'Standard'], challenging: ['挑战', 'Challenging'], lethal: ['致命', 'Lethal'],
    action: ['动作', 'Action'], bonus_action: ['附赠动作', 'Bonus Action'], reaction: ['反应', 'Reaction'],
    slashing: ['挥砍', 'Slashing'], piercing: ['穿刺', 'Piercing'], bludgeoning: ['钝击', 'Bludgeoning'],
    acid: ['强酸', 'Acid'], cold: ['寒冷', 'Cold'], fire: ['火焰', 'Fire'], force: ['力场', 'Force'], lightning: ['闪电', 'Lightning'],
    necrotic: ['黯蚀', 'Necrotic'], poison: ['毒素', 'Poison'], psychic: ['心灵', 'Psychic'], radiant: ['光耀', 'Radiant'], thunder: ['雷鸣', 'Thunder'],
    opportunity_attack: ['借机攻击', 'Opportunity Attack'], attack: ['攻击检定', 'Attack'], spell_attack: ['法术攻击检定', 'Spell Attack'],
    saving_throw: ['豁免检定', 'Saving Throw'], death_save: ['死亡豁免', 'Death Save'], medicine: ['医药检定', 'Medicine Check'], concentration: ['专注检定', 'Concentration Check'],
    blessed: ['祝福', 'Blessed'], hexed: ['受诅咒', 'Hexed'], shield_of_faith: ['虔诚护盾', 'Shield of Faith'],
  dodging: ['闪避', 'Dodging'], disengaged: ['已脱离接战', 'Disengaged'], stable: ['伤势稳定', 'Stable'], unconscious: ['昏迷', 'Unconscious'],
    dead: ['死亡', 'Dead'], defeated: ['已击败', 'Defeated'], death_saves: ['死亡豁免记录', 'Death Saves'],
    slowed_10: ['速度降低 10 尺', 'Speed reduced by 10 ft'], next_attack_advantage: ['下次攻击有优势', 'Next attack has advantage'],
    next_attack_disadvantage: ['下次攻击有劣势', 'Next attack has disadvantage'], faerie_fire: ['妖火标记', 'Faerie Fire'],
    shield: ['护盾术（AC +5）', 'Shield (+5 AC)'], cannot_regain_hp: ['无法恢复生命', 'Cannot regain HP'],
    hit_reaction_spell: ['被命中反应', 'Reaction when hit'], reaction_defense: ['反应防御', 'Reaction defense'],
  }
  const pair = labels[key]
  return pair ? (locale.startsWith('zh') ? pair[0] : pair[1]) : raw.replaceAll('_', ' ')
}

export function friendlyCombatError(cause: unknown, locale: string): string {
  const message = cause instanceof Error ? cause.message : String(cause)
  if (!locale.startsWith('zh')) return message
  const outOfRange = message.match(/target is out of range \((\d+) > (\d+) feet\)/i)
  if (outOfRange) {
    return `目标距离为 ${outOfRange[1]} 尺，这个武器最远只能攻击 ${outOfRange[2]} 尺。请换远程武器，或先移动靠近。`
  }
  if (/not your turn|requested actor is not the current actor/i.test(message)) return '现在还没轮到这个角色。请等待当前行动者结束回合。'
  if (/already been spent|action is not available/i.test(message)) return '这个动作资源本回合已经用掉了。请选择仍可用的动作，或结束回合。'
  if (/state version|stale|expected_version/i.test(message)) return '战斗状态刚刚发生变化，界面已为你刷新。请按最新状态再试一次。'
  if (/resolve the melee hit spell decision first/i.test(message)) return '请先选择是否追加法术，结算这次命中后再继续行动。'
  if (/automatic combat turn exceeded the safety limit/i.test(message)) return '敌方自动回合未能正常结束，本次操作已安全回滚。请刷新战斗状态后重试。'
  return message
}

export function intentId(): string {
  return globalThis.crypto?.randomUUID?.() || `intent-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
