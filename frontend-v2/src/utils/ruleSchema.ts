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

// zh 界面用中文；en/ja 界面均回退英文（ja 缺失文案回退英文，而非中文）。
function localeIsZh(): boolean {
  const locale = i18n.global.locale.value
  return typeof locale === 'string' && locale.startsWith('zh')
}

const LOCALE_FIELD_SUFFIX: Record<string, string> = { en: 'en' }

export function localeFieldSuffix(): string {
  const locale = i18n.global.locale.value
  if (typeof locale === 'string' && locale.startsWith('zh')) return ''
  const main = typeof locale === 'string' ? locale.split('-')[0] : ''
  // ja 等非 zh 界面缺失时回退 en 后缀字段，而非原字段（zh）。
  return LOCALE_FIELD_SUFFIX[main] || 'en'
}

export function localizedField<T = unknown>(obj: Record<string, unknown> | null | undefined, key: string): T | undefined {
  // Content V2 responses are already materialized by the backend. Only V1
  // payloads without active_locale may use the historical suffix fallback.
  if (obj?.active_locale) return obj[key] as T | undefined
  const suffix = localeFieldSuffix()
  if (suffix) {
    const v = obj?.[`${key}_${suffix}`]
    if (v !== undefined && v !== null) return v as T
  }
  return obj?.[key] as T | undefined
}

export function localizedLabel(value: LabelValue, fallback: LabelValue = ''): string {
  // zh 界面优先中文标签；en/ja 界面均回退英文标签（ja 缺失时回退英文）。
  const localeFirst = localeIsZh() ? 'zh' : 'en'
  const localeSecond = localeIsZh() ? 'en' : 'zh'
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
  // 中文界面只显示中文名(力量),不拼括号英文,避免列表/侧栏过宽;
  // en/ja 界面显示英文缩写(STR)（ja 缺失时回退英文）。
  const name = localeIsZh() ? (attr.name || ATTR_NAME_ZH[key] || key) : (attr.name_en || ATTR_NAME_EN[key] || attr.name || key)
  return name
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

type AttrValues = Record<string, number | string | undefined>

/**
 * 计算规则里的简单属性公式（与服务端 _safe_eval 的子集一致）：整数、属性名、
 * + - * / //、括号。无法解析时返回 null，调用方回退到静态数值。
 */
export function evalRuleFormula(formula: string | undefined, attrs: AttrValues): number | null {
  if (!formula) return null
  const tokens = formula.match(/\d+|[A-Za-z_][A-Za-z0-9_]*|\/\/|[-+*/()]/g)
  if (!tokens || tokens.join('') !== formula.replace(/\s+/g, '')) return null
  let pos = 0
  const peek = () => tokens[pos]
  const next = () => tokens[pos++]
  function primary(): number {
    const token = next()
    if (token === undefined) throw new Error('eof')
    if (token === '(') {
      const value = expression()
      if (next() !== ')') throw new Error('paren')
      return value
    }
    if (token === '-') return -primary()
    if (/^\d+$/.test(token)) return Number(token)
    if (/^[A-Za-z_]/.test(token)) {
      const raw = Number.parseInt(String(attrs[token] ?? ''), 10)
      if (Number.isNaN(raw)) throw new Error('attr')
      return raw
    }
    throw new Error('token')
  }
  function term(): number {
    let value = primary()
    while (peek() === '*' || peek() === '/' || peek() === '//') {
      const op = next()
      const right = primary()
      if (op === '*') value *= right
      else if (right === 0) throw new Error('div')
      else value = op === '//' ? Math.floor(value / right) : value / right
    }
    return value
  }
  function expression(): number {
    let value = term()
    while (peek() === '+' || peek() === '-') {
      const op = next()
      const right = term()
      value = op === '+' ? value + right : value - right
    }
    return value
  }
  try {
    const value = expression()
    return pos === tokens.length && Number.isFinite(value) ? Math.trunc(value) : null
  } catch {
    return null
  }
}

/** 技能基础值：优先当前语言的查找表（含属性派生），再回退旧的规范名表与自定义技能缺省。 */
export function skillBaseValue(name: string | undefined, ruleMeta?: RuleMeta | null, attrs?: AttrValues): number {
  const key = String(name || '').trim()
  const entry = ruleMeta?.skill_base_lookup?.[key]
    ?? Object.entries(ruleMeta?.skill_base_lookup || {}).find(([known]) => known.toLowerCase() === key.toLowerCase())?.[1]
  if (entry) {
    const derived = attrs ? evalRuleFormula(entry.formula, attrs) : null
    return derived !== null ? Math.max(0, Math.min(99, derived)) : Number(entry.base) || 0
  }
  const legacy = ruleMeta?.skill_base_values?.[key]
  if (legacy !== undefined) return Number(legacy) || 0
  return Number(ruleMeta?.custom_skill_base ?? 0) || 0
}

/** 技能点预算：规则声明公式时随属性变化（CoC 职业点 + 兴趣点），否则是固定总额。 */
export function skillPointBudget(ruleMeta?: RuleMeta | null, attrs?: AttrValues): number {
  const derived = attrs ? evalRuleFormula(ruleMeta?.skill_point_formula, attrs) : null
  if (derived !== null) return Math.max(0, derived)
  return Number(ruleMeta?.skill_point_total || 0) || 0
}

export function skillPointCost(skill: { name?: string; value?: string | number }, ruleMeta?: RuleMeta | null, attrs?: AttrValues): number {
  const value = Number(skill.value ?? 0) || 0
  if (ruleMeta?.skill_point_spend_mode === 'above_base') {
    return Math.max(0, value - skillBaseValue(skill.name, ruleMeta, attrs))
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
