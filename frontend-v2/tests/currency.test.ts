import { describe, expect, it } from 'vitest'
import {
  currencyInputStep,
  formatCurrencyAmount,
  parseCurrencyInput,
} from '@/utils/currency'
import type { CurrencySystem } from '@/utils/currency'

const dollarCent: CurrencySystem = {
  schema_version: 2,
  base_unit: 'cent',
  display_unit: 'dollar',
  units: [
    { id: 'dollar', name: '美元', symbol: '$', rate: 100 },
    { id: 'cent', name: '美分', rate: 1 },
  ],
}

const yuanFen: CurrencySystem = {
  schema_version: 2,
  base_unit: 'fen',
  display_unit: 'yuan',
  units: [
    { id: 'yuan', name: '人民币', symbol: '¥', rate: 100 },
    { id: 'fen', name: '分', rate: 1 },
  ],
}

const spirit: CurrencySystem = {
  base_unit: 'unit',
  display_unit: 'unit',
  units: [{ id: 'unit', name: '灵石', rate: 1 }],
}

describe('formatCurrencyAmount', () => {
  it('formats minor units with symbol and fixed decimals', () => {
    expect(formatCurrencyAmount(25, dollarCent)).toBe('$0.25')
    expect(formatCurrencyAmount(100, dollarCent)).toBe('$1.00')
    expect(formatCurrencyAmount(1250, yuanFen)).toBe('¥12.50')
    expect(formatCurrencyAmount(0, dollarCent)).toBe('$0.00')
    expect(formatCurrencyAmount(-250, dollarCent)).toBe('-$2.50')
  })

  it('falls back to unit name without symbol', () => {
    const noSymbol: CurrencySystem = {
      base_unit: 'unit',
      display_unit: 'unit',
      units: [{ id: 'unit', name: '灵石', rate: 1 }],
    }
    expect(formatCurrencyAmount(25, noSymbol)).toBe('25 灵石')
  })

  it('returns plain number when no system exists', () => {
    expect(formatCurrencyAmount(25, null)).toBe('25')
    expect(formatCurrencyAmount(25, null, '金币')).toBe('25 金币')
  })
})

describe('parseCurrencyInput', () => {
  it('parses display-unit decimals into canonical integers', () => {
    expect(parseCurrencyInput('0.25', dollarCent)).toBe(25)
    expect(parseCurrencyInput('1', dollarCent)).toBe(100)
    expect(parseCurrencyInput('12.50', yuanFen)).toBe(1250)
    expect(parseCurrencyInput('3', spirit)).toBe(3)
  })

  it('rejects amounts that cannot become whole base units', () => {
    expect(parseCurrencyInput('0.001', dollarCent)).toBeNull()
    expect(parseCurrencyInput('0.5', spirit)).toBeNull()
    expect(parseCurrencyInput('abc', dollarCent)).toBeNull()
    expect(parseCurrencyInput('-5', dollarCent)).toBeNull()
    expect(parseCurrencyInput('0', dollarCent)).toBeNull()
    expect(parseCurrencyInput('', dollarCent)).toBeNull()
  })

  it('allows zero only for character-sheet editing', () => {
    expect(parseCurrencyInput('0', spirit, { allowZero: true })).toBe(0)
    expect(parseCurrencyInput('0.00', dollarCent, { allowZero: true })).toBe(0)
  })

  it('treats missing system as rate 1', () => {
    expect(parseCurrencyInput('25', null)).toBe(25)
    expect(parseCurrencyInput('0.5', null)).toBeNull()
  })
})

describe('currencyInputStep', () => {
  it('uses 0.01 when a decimal minor unit exists', () => {
    expect(currencyInputStep(dollarCent)).toBe('0.01')
    expect(currencyInputStep(yuanFen)).toBe('0.01')
  })

  it('uses 1 for integer currencies and missing systems', () => {
    expect(currencyInputStep(spirit)).toBe('1')
    expect(currencyInputStep(null)).toBe('1')
  })
})
