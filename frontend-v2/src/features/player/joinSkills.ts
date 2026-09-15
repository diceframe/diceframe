import type { CharacterSkill } from '@/api/types'

/** 现场建卡表单里的技能草稿（数值可能还是输入框字符串）。 */
export interface JoinSkillDraft {
  name: string
  value: string | number
  effect?: string
}

/**
 * 现场建卡的技能提交投影。
 *
 * 单一投影：数值字符串转 number、空值不写入，可选 `effect` 保留并截断到
 * 500 字符（效果说明是实现侧描述文本，不是规则权威）。提交路径只允许调用
 * 这一个函数，避免再次 `.map()` 把 effect 丢掉。
 */
export function joinSkillsPayload(skills: readonly JoinSkillDraft[]): CharacterSkill[] {
  return (skills || [])
    .filter(s => String(s?.name || '').trim())
    .map(s => {
      const row: CharacterSkill = {
        name: String(s.name).trim(),
        value: s.value === '' ? undefined : Number(s.value),
      }
      const effect = String(s.effect || '').trim().slice(0, 500)
      if (effect) row.effect = effect
      return row
    })
}
