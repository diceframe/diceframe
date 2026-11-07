import { describe, expect, it } from 'vitest'
import type { RuleMeta } from '../src/api/types'
import { evalRuleFormula, skillBaseValue, skillPointBudget, skillPointCost } from '../src/utils/ruleSchema'

const cocMeta: RuleMeta = {
  skill_point_spend_mode: 'above_base',
  skill_point_total: 320,
  skill_point_formula: 'edu * 4 + int * 2',
  skill_base_values: { 侦查: 25 },
  skill_base_lookup: {
    'Spot Hidden': { base: 25 },
    Dodge: { base: 25, formula: 'dex // 2' },
    'Own Language': { base: 50, formula: 'edu' },
  },
  custom_skill_base: 5,
}

const attrs = { dex: 70, edu: 80, int: 60 }

describe('rule formulas', () => {
  it('evaluates the server formula subset', () => {
    expect(evalRuleFormula('edu * 4 + int * 2', attrs)).toBe(440)
    expect(evalRuleFormula('dex // 2', { dex: 55 })).toBe(27)
    expect(evalRuleFormula('(edu - 10) * 2', attrs)).toBe(140)
  })

  it('returns null for unknown attributes or unsupported syntax', () => {
    expect(evalRuleFormula('pow * 2', attrs)).toBeNull()
    expect(evalRuleFormula('edu ** 2', attrs)).toBeNull()
    expect(evalRuleFormula('edu; alert(1)', attrs)).toBeNull()
    expect(evalRuleFormula(undefined, attrs)).toBeNull()
  })
})

describe('CoC skill budget', () => {
  it('derives the budget from attributes and falls back to the fixed total', () => {
    expect(skillPointBudget(cocMeta, attrs)).toBe(440)
    expect(skillPointBudget(cocMeta)).toBe(320)
    expect(skillPointBudget({ skill_point_total: 12 }, attrs)).toBe(12)
  })

  it('looks up localized names and derived bases', () => {
    expect(skillBaseValue('Spot Hidden', cocMeta, attrs)).toBe(25)
    expect(skillBaseValue('spot hidden', cocMeta, attrs)).toBe(25)
    expect(skillBaseValue('Dodge', cocMeta, attrs)).toBe(35)
    expect(skillBaseValue('Dodge', cocMeta)).toBe(25)
    expect(skillBaseValue('Own Language', cocMeta, attrs)).toBe(80)
    expect(skillBaseValue('侦查', cocMeta, attrs)).toBe(25)
    expect(skillBaseValue('Basket Weaving', cocMeta, attrs)).toBe(5)
  })

  it('charges only the part above the character base', () => {
    expect(skillPointCost({ name: 'Dodge', value: 50 }, cocMeta, attrs)).toBe(15)
    expect(skillPointCost({ name: 'Spot Hidden', value: 60 }, cocMeta, attrs)).toBe(35)
    expect(skillPointCost({ name: 'Own Language', value: 70 }, cocMeta, attrs)).toBe(0)
    expect(skillPointCost({ name: 'Dodge', value: 50 }, { skill_point_spend_mode: 'total_value' }, attrs)).toBe(50)
  })
})
