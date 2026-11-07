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

/**
 * 以「生效规则」为准的联动：不管规则是从哪条路径来的（模板下拉、AI 基础规则、
 * AI 生成结果、世界来源切换、冒险兼容规则、深链启动），只要 effective rule 与
 * play_mode/solo 的组合不满足 GAL 契约，就在这里统一拉回合法状态；返回 null
 * 表示已经是合法组合。
 *
 * 与 galLinkageForRule 的差异：那里以「上一次规则」为参照，只在 GAL⇄非 GAL
 * *切换* 时生效；这里只看当前生效规则——非 GAL 规则 + play_mode=gal 任何
 * 时候都是非法组合（例如模板选了 GAL 后切到 AI 来源），必须降级为 free。
 * play_mode=adventure 不受影响：冒险模式的规则切换不应间接取消冒险。
 */
export function galLinkageForActiveRule(
  activeRule: string,
  state: GalRuleState,
): GalRuleState | null {
  if (activeRule === GAL_RULE_ID) {
    if (state.playMode === 'gal' && state.solo) return null
    return { playMode: 'gal', solo: true }
  }
  if (state.playMode === 'gal') {
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
