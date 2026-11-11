import { api } from '@/api/client'

export interface ClassLevelResult {
  ok?: boolean
  class?: string
  class_levels?: Record<string, number>
  pending_class_levels?: number
  mastered?: string[]
  error?: string
  error_code?: string
}

export interface SkillPointResult {
  ok?: boolean
  skill?: string
  value?: number
  skill_points?: number
  error?: string
  error_code?: string
}

/** 把一个待分配的职业等级投到指定职业（本人或 GM）。 */
export function assignClassLevel(
  gameKey: string,
  uid: string,
  className: string,
  runId?: string,
): Promise<ClassLevelResult> {
  return api<ClassLevelResult>(
    `/games/${encodeURIComponent(gameKey)}/characters/${encodeURIComponent(uid)}/class-level`,
    { method: 'POST', body: JSON.stringify({ class: className, run_id: runId }) },
  )
}

/** 消耗 1 个技能点，把已有技能 +1（本人或 GM）。 */
export function spendSkillPoint(
  gameKey: string,
  uid: string,
  skill: string,
  runId?: string,
): Promise<SkillPointResult> {
  return api<SkillPointResult>(
    `/games/${encodeURIComponent(gameKey)}/characters/${encodeURIComponent(uid)}/skill-point`,
    { method: 'POST', body: JSON.stringify({ skill, run_id: runId }) },
  )
}

export interface AttributeStepResult {
  ok?: boolean
  attribute?: string
  value?: number
  pending_attribute_steps?: number
  max_hp?: number
  error?: string
  error_code?: string
}

/** 消耗一次里程碑提升（20/40 级），把某项属性骰提高一档。 */
export function applyAttributeStep(
  gameKey: string,
  uid: string,
  attribute: string,
  runId?: string,
): Promise<AttributeStepResult> {
  return api<AttributeStepResult>(
    `/games/${encodeURIComponent(gameKey)}/characters/${encodeURIComponent(uid)}/attribute-step`,
    { method: 'POST', body: JSON.stringify({ attribute, run_id: runId }) },
  )
}
