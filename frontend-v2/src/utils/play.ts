import type { CharacterSheet, RuleMeta, SpecialStatSpec } from '@/api/types'
import { i18n } from '@/i18n'
import { getResourceValue, resourceLabel } from './ruleSchema'

export interface DiceTag { system: string; value: string }

export const SYSTEM_DICE_MARKER_PREFIX = '(\u7cfb\u7edf\u63b7\u9ab0:'
const SYSTEM_DICE_RE = new RegExp(`\\n\\${SYSTEM_DICE_MARKER_PREFIX}\\s*([^=\\s]+)=([^\\)]+)\\)\\s*$`)

export function parseAction(text: string): { text: string; dice: DiceTag | null } {
  const raw = String(text || '')
  const m = raw.match(SYSTEM_DICE_RE)
  if (!m) return { text: raw, dice: null }
  return { text: raw.slice(0, m.index).trim(), dice: { system: m[1].toUpperCase(), value: m[2].trim() } }
}

const STATE_LABELS: Record<string, { zh: string; en: string }> = {
  active_action: { zh: '行动阶段', en: 'Action Phase' },
  active_judgment: { zh: 'GM 判定中', en: 'GM Resolving' },
  paused: { zh: '暂停', en: 'Paused' },
  waiting: { zh: '等待玩家', en: 'Waiting for Players' },
  created: { zh: '已创建', en: 'Created' },
  ended: { zh: '已结束', en: 'Ended' },
}
export function gameStateLabel(state?: string): string {
  const label = state ? STATE_LABELS[state] : undefined
  if (label) return i18n.global.locale.value === 'en' ? label.en : label.zh
  return state || (i18n.global.locale.value === 'en' ? 'Unknown' : '未知')
}

export function playerColor(userId: string): string {
  let hash = 0
  String(userId || 'player').split('').forEach(ch => { hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0 })
  return `hsl(${Math.abs(hash) % 360} 68% 66%)`
}

const SPECIAL_STAT_COLORS: Record<string, string> = {
  sanity: 'stat-sanity',
  qi: 'stat-qi',
  luck: 'stat-luck',
  cyberware: 'stat-cyber',
  cyberware_load: 'stat-cyber',
  humanity: 'stat-humanity',
  heat: 'stat-heat',
  '\u4e49\u4f53': 'stat-cyber',
}
export function specialStatColor(key: string): string {
  return SPECIAL_STAT_COLORS[key] || ''
}

export interface SpecialStat { key: string; name: string; current: number; max: number; color: string }

function sheetRecord(cs: CharacterSheet): Record<string, unknown> {
  return cs as Record<string, unknown>
}

export function buildSpecialStats(cs: CharacterSheet, ruleSpecialStats: SpecialStatSpec[] | undefined): SpecialStat[] {
  const stats: SpecialStat[] = []
  const values = sheetRecord(cs)
  for (const ss of ruleSpecialStats || []) {
    const key = ss.key
    if (!key || values[key] === undefined) continue
    const max = values[`max_${key}`] ?? ss.max ?? 0
    stats.push({
      key,
      name: ss.name || key,
      current: Number(values[key]) || 0,
      max: Number(max) || 0,
      color: specialStatColor(key),
    })
  }
  return stats
}

export function primaryResourceList(cs: CharacterSheet, ruleMeta?: RuleMeta | null): { key: string; label: string; current: number; max: number }[] {
  const schema = ruleMeta?.resource_schema || []
  const specialKeys = new Set((ruleMeta?.rule_special_stats || []).map(s => s.key))
  const out: { key: string; label: string; current: number; max: number }[] = []
  for (const spec of schema) {
    const key = spec.key
    if (key === 'hp' || specialKeys.has(key)) continue
    const val = getResourceValue(cs, key)
    if (val.max <= 0 && val.current <= 0) continue
    out.push({ key, label: resourceLabel(spec), current: val.current, max: val.max })
  }
  return out
}
