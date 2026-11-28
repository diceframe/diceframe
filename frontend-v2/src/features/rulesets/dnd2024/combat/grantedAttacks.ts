import type { JsonObject } from '../../../../api/types'
import type { RulesetCombatAction } from '../../../../api/types'

/**
 * 两种"被授予的第二击"的意图构造：Light 追加攻击与 Cleave。
 *
 * 它们都是**普通 attack 意图**加一个声明字段，服务端据此改走对应的校验与
 * 经济；面板不判断"能不能打"，只把服务端给的事实搬进意图。放在这里而不是
 * 面板里，是因为面板已接近体积棘轮上限，而这两段与 UI 无关。
 */
export function lightExtraIntent(
  row: RulesetCombatAction, targetId: string,
): JsonObject | null {
  const weapon = (row.weapons || [])[0]
  if (!weapon || !targetId) return null
  return {
    type: 'attack',
    actor_id: String(row.actor_id || ''),
    target_id: targetId,
    ...(weapon.weapon_ref
      ? { weapon_ref: String(weapon.weapon_ref) }
      : { attack_id: String(weapon.id) }),
    // 服务端按这一次声明改走"追加攻击"的校验与经济。
    light_extra_attack: true,
  }
}

export function cleaveIntent(
  action: RulesetCombatAction, targetId: string,
): JsonObject | null {
  const weapon = (action.weapons || [])[0]
  if (!weapon || !targetId) return null
  return {
    type: 'attack',
    actor_id: String(action.actor_id || ''),
    target_id: targetId,
    ...(weapon.weapon_ref
      ? { weapon_ref: String(weapon.weapon_ref) }
      : { attack_id: String(weapon.id) }),
    cleave_from: String(action.cleave_from || ''),
  }
}

/** 哪一组 Light 追加攻击该出现在当前分页（免费那组在"动作"页）。 */
export function lightRowsForTab(
  rows: RulesetCombatAction[], tab: string,
): RulesetCombatAction[] {
  return rows.filter(row => (tab === 'bonus' ? row.cost === 'bonus_action' : row.cost !== 'bonus_action'))
}

/** 本动作还剩几次攻击；面板只用它显示提示，不参与判定。 */
export function remainingAttacks(combat: JsonObject | undefined): number {
  const economy = (combat?.economy ?? {}) as JsonObject
  return Number(economy.attacks_remaining ?? 0)
}
