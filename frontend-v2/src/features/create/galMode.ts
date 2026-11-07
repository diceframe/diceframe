/**
 * GAL 玩法模式的创建契约（服务端 `src/gameplays/gal` 的镜像）。
 *
 * 服务端 `_validate_create` 要求同时满足：`play_mode=gal`、`rule_id=gal_narrative`、
 * `solo=true`；任一条不满足直接拒绝（GAL_REQUIRES_SOLO / GAL_REQUIRES_NARRATIVE_RULE）。
 * 前端过去靠一个「GAL 预览」复选框顺带设置这三项，复选框删掉后规则下拉只剩 rule_id，
 * 于是会出现 `rule_id=gal_narrative` + `play_mode=free` 这种必被拒的组合。
 */

export const GAL_RULE_ID = 'gal_narrative'

export type CreatePlayMode = 'free' | 'adventure' | 'gal'

export interface GalRuleState {
  playMode: CreatePlayMode
  solo: boolean
}

/** 切换规则时的联动；返回 null 表示状态无需变化。 */
export function galLinkageForRule(
  previousRule: string,
  nextRule: string,
  state: GalRuleState,
): GalRuleState | null {
  if (nextRule === GAL_RULE_ID) {
    if (state.playMode === 'gal' && state.solo) return null
    return { playMode: 'gal', solo: true }
  }
  if (previousRule === GAL_RULE_ID && state.playMode === 'gal') {
    return { playMode: 'free', solo: state.solo }
  }
  return null
}

/** 用户从 GAL 切换到自由/模组时，回退到哪条非 GAL 规则。 */
export function nonGalFallbackRule(candidates: Array<string | undefined | null>): string {
  const hit = candidates
    .map(value => String(value || '').trim())
    .find(id => id && id !== GAL_RULE_ID)
  return hit || 'freeform'
}

/** 创建 payload 是否满足 GAL 契约；不符合时前端就该拦下，而不是让服务端拒绝。 */
export function galCreateIsConsistent(ruleId: string, playMode: string, solo: boolean): boolean {
  if (ruleId === GAL_RULE_ID) return playMode === 'gal' && solo
  return playMode !== 'gal'
}
