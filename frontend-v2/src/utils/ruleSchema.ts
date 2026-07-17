import type { CharacterSheet, IdentityFieldSpec, ResourceSpec, RuleAttribute, RuleMeta } from '@/api/types'
import { i18n } from '@/i18n'

const ATTR_NAME_EN: Record<string, string> = { str: 'STR', con: 'CON', dex: 'DEX', int: 'INT', edu: 'EDU', app: 'APP', pow: 'POW', siz: 'SIZ', wis: 'WIS', cha: 'CHA' }
const ATTR_NAME_ZH: Record<string, string> = { str: '力量', con: '体质', dex: '敏捷', int: '智力', edu: '教育', app: '外貌', pow: '意志', siz: '体型', wis: '感知', cha: '魅力' }
const LABELS: Record<string, { zh: string; en: string }> = {
  name: { zh: '角色名', en: 'Character Name' },
  origin: { zh: '种族', en: 'Origin' },
  archetype: { zh: '职业', en: 'Archetype' },
  background: { zh: '背景故事', en: 'Background' },
  level: { zh: '等级', en: 'Level' },
  xp: { zh: '经验', en: 'XP' },
  hp: { zh: '生命', en: 'HP' },
  max_hp: { zh: '最大生命', en: 'Max HP' },
  currency: { zh: '金币', en: 'Gold' },
  attributes: { zh: '属性', en: 'Attributes' },
  skills: { zh: '技能', en: 'Skills' },
  equipment: { zh: '装备', en: 'Equipment' },
  inventory: { zh: '背包', en: 'Inventory' },
  key_items: { zh: '关键物品', en: 'Key Items' },
}

export type IdentityField = IdentityFieldSpec
export type RuleAttr = RuleAttribute

type LabelValue = string | { zh?: string; en?: string } | undefined

type SheetUpdate = Partial<CharacterSheet> & { identity?: Record<string, string> }

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function isEnglish(): boolean {
  return i18n.global.locale.value === 'en'
}

const LOCALE_FIELD_SUFFIX: Record<string, string> = { en: 'en' }

export function localeFieldSuffix(): string {
  const locale = i18n.global.locale.value
  if (typeof locale === 'string' && locale.startsWith('zh')) return ''
  const main = typeof locale === 'string' ? locale.split('-')[0] : ''
  return LOCALE_FIELD_SUFFIX[main] || ''
}

export function localizedField<T = unknown>(obj: Record<string, unknown> | null | undefined, key: string): T | undefined {
  const suffix = localeFieldSuffix()
  if (suffix) {
    const v = obj?.[`${key}_${suffix}`]
    if (v !== undefined && v !== null) return v as T
  }
  return obj?.[key] as T | undefined
}

export function localizedLabel(value: LabelValue, fallback: LabelValue = ''): string {
  const localeFirst = isEnglish() ? 'en' : 'zh'
  const localeSecond = isEnglish() ? 'zh' : 'en'
  if (value && typeof value === 'object') {
    return value[localeFirst] || value[localeSecond] || localizedLabel(undefined, fallback)
  }
  if (value) return value
  if (fallback && typeof fallback === 'object') return fallback[localeFirst] || fallback[localeSecond] || ''
  return fallback || ''
}

export function tr(key: string): string { return localizedLabel(undefined, LABELS[key] || key) }

export function attrDisplayName(attr: RuleAttr): string {
  const key = attr.key || ''
  if (attr.display_name) return attr.display_name
  const name = isEnglish() ? (attr.name_en || ATTR_NAME_EN[key] || attr.name || key) : (attr.name || ATTR_NAME_ZH[key] || key)
  const nameEn = attr.name_en || ATTR_NAME_EN[key] || (key ? key.toUpperCase() : '')
  if (isEnglish()) return name
  return nameEn ? `${name} (${nameEn})` : name
}

export function suggestedAttributes(attrs: RuleAttr[], totalPoints?: number): Record<string, number> {
  const result: Record<string, number> = {}
  if (!attrs.length) return result
  for (const attr of attrs) result[attr.key] = Number(attr.min ?? 0) || 0
  const minTotal = attrs.reduce((sum, attr) => sum + (Number(attr.min ?? 0) || 0), 0)
  const maxTotal = attrs.reduce((sum, attr) => sum + (Number(attr.max ?? attr.min ?? 0) || 0), 0)
  const midpointTotal = attrs.reduce((sum, attr) => sum + Math.floor(((Number(attr.min ?? 0) || 0) + (Number(attr.max ?? attr.min ?? 0) || 0)) / 2), 0)
  const target = Math.max(minTotal, Math.min(Number(totalPoints || midpointTotal) || midpointTotal, maxTotal))
  let remaining = target - minTotal
  let guard = attrs.length * 200
  while (remaining > 0 && guard-- > 0) {
    let changed = false
    for (const attr of attrs) {
      const max = Number(attr.max ?? result[attr.key]) || result[attr.key]
      if (remaining <= 0) break
      if (result[attr.key] < max) {
        result[attr.key] += 1
        remaining -= 1
        changed = true
      }
    }
    if (!changed) break
  }
  return result
}

export function skillPointCost(skill: { name?: string; value?: string | number }, ruleMeta?: RuleMeta | null): number {
  const value = Number(skill.value ?? 0) || 0
  if (ruleMeta?.skill_point_spend_mode === 'above_base') {
    const base = Number(ruleMeta.skill_base_values?.[String(skill.name || '')] ?? 0) || 0
    return Math.max(0, value - base)
  }
  return value
}

export function identitySchema(ruleMeta?: RuleMeta | null): IdentityField[] {
  return ruleMeta?.identity_schema?.length ? ruleMeta.identity_schema : [
    { key: 'origin', label: LABELS.origin, legacy_field: 'race' },
    { key: 'archetype', label: LABELS.archetype, legacy_field: 'class' },
    { key: 'background', label: LABELS.background, legacy_field: 'background' },
  ]
}

export function identityLabel(field: IdentityField): string {
  return localizedLabel(field.label, LABELS[field.key] || '')
}

export function getIdentityValue(cs: CharacterSheet | undefined, field: IdentityField): string {
  const sheet = record(cs)
  const identity = record(cs?.identity)
  const value = identity[field.key]
  if (value !== undefined && value !== '') return String(value)
  const fallback = field.legacy_field ? sheet[field.legacy_field] : undefined
  return fallback !== undefined ? String(fallback) : ''
}

export function setIdentityUpdate(updates: SheetUpdate, field: IdentityField, value: string): void {
  updates.identity = updates.identity || {}
  updates.identity[field.key] = value
  if (field.legacy_field) (updates as Record<string, unknown>)[field.legacy_field] = value
}

export function currencyLabel(ruleMeta?: RuleMeta | null): string {
  return localizedLabel(ruleMeta?.ui_schema?.currency_label, ruleMeta?.currency || LABELS.currency)
}

export function getCurrencyAmount(cs?: CharacterSheet): number {
  if (cs?.currency?.amount !== undefined) return Number.parseInt(String(cs.currency.amount), 10) || 0
  return Number.parseInt(String(cs?.gold ?? 0), 10) || 0
}

export function getResourceValue(cs: CharacterSheet | undefined, key: string): { current: number; max: number } {
  const sheet = record(cs)
  const res = record(cs?.resources?.[key])
  if (key === 'hp') {
    return {
      current: Number(res.current ?? cs?.hp ?? 0) || 0,
      max: Number(res.max ?? cs?.max_hp ?? cs?.hp ?? 0) || 0,
    }
  }
  return {
    current: Number(res.current ?? sheet[key] ?? 0) || 0,
    max: Number(res.max ?? sheet[`max_${key}`] ?? 0) || 0,
  }
}

export function isAutoHpRule(ruleMeta?: RuleMeta | null): boolean {
  return Boolean(ruleMeta?.auto_hp || ruleMeta?.mechanics === 'coc7e_core')
}

export function calcAutoHp(attrs: Record<string, number | string | undefined>, ruleMeta?: RuleMeta | null): number | null {
  if (ruleMeta?.mechanics === 'coc7e_core' || ruleMeta?.rule_id === 'freeform_coc') {
    return Math.max(Math.floor(((Number.parseInt(String(attrs.con ?? 0), 10) || 0) + (Number.parseInt(String(attrs.siz ?? 0), 10) || 0)) / 10), 1)
  }
  return null
}

export function resourceLabel(spec: ResourceSpec): string {
  return localizedLabel(spec.label, spec.key === 'hp' ? LABELS.hp : spec.key)
}
