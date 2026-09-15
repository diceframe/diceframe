import { describe, expect, it } from 'vitest'
import {
  currencyAmountToInputText,
  currencyInputStep,
  formatCurrencyAmount,
  parseCurrencyInput,
  resolveEditableUnit,
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


// ===== rate=3：无法有限十进制表示，展示必须落在 base unit =====

const silverCopper: CurrencySystem = {
  schema_version: 2,
  base_unit: 'copper',
  display_unit: 'silver',
  units: [
    { id: 'silver', name: '银币', rate: 3 },
    { id: 'copper', name: '铜币', rate: 1 },
  ],
}

const thousandth: CurrencySystem = {
  schema_version: 2,
  base_unit: 'mill',
  display_unit: 'unit',
  units: [
    { id: 'unit', name: '元', rate: 1000 },
    { id: 'mill', name: '毫', rate: 1 },
  ],
}

const tenth: CurrencySystem = {
  schema_version: 2,
  base_unit: 'dime',
  display_unit: 'unit',
  units: [
    { id: 'unit', name: '单位', rate: 10 },
    { id: 'dime', name: '角', rate: 1 },
  ],
}

describe('rate=3 display parity with backend', () => {
  it('decomposes into mixed units instead of pretending decimals', () => {
    expect(formatCurrencyAmount(1, silverCopper)).toBe('1 铜币')
    expect(formatCurrencyAmount(3, silverCopper)).toBe('1 银币')
    expect(formatCurrencyAmount(4, silverCopper)).toBe('1 银币 1 铜币')
    expect(formatCurrencyAmount(6, silverCopper)).toBe('2 银币')
    expect(formatCurrencyAmount(2, silverCopper)).toBe('2 铜币')
  })

  it('falls back to base-unit inputs for non-decimal rates', () => {
    expect(resolveEditableUnit(silverCopper).id).toBe('copper')
    expect(currencyAmountToInputText(4, silverCopper)).toBe('4')
    expect(parseCurrencyInput('4', silverCopper)).toBe(4)
    expect(currencyInputStep(silverCopper)).toBe('1')
  })
})

describe('display/input separation (editor round-trip)', () => {
  it('CoC: canonical 25 -> input "0.25" -> parse -> 25', () => {
    const input = currencyAmountToInputText(25, dollarCent)
    expect(input).toBe('0.25')
    expect(input).toMatch(/^\d+(\.\d+)?$/)
    expect(parseCurrencyInput(input, dollarCent)).toBe(25)
  })

  it('CNY: canonical 1250 -> input "12.50" -> parse -> 1250', () => {
    const input = currencyAmountToInputText(1250, yuanFen)
    expect(input).toBe('12.50')
    expect(parseCurrencyInput(input, yuanFen)).toBe(1250)
  })

  it('灵石: canonical 25 -> input "25" -> parse -> 25', () => {
    const input = currencyAmountToInputText(25, spirit)
    expect(input).toBe('25')
    expect(parseCurrencyInput(input, spirit)).toBe(25)
  })

  it('rate=3: canonical 4 -> input "4" -> parse -> 4 (base-unit fallback)', () => {
    const input = currencyAmountToInputText(4, silverCopper)
    expect(input).toBe('4')
    expect(parseCurrencyInput(input, silverCopper)).toBe(4)
  })
})

describe('currencyInputStep derives from editable unit rate', () => {
  it('maps decimal-friendly rates to their smallest step', () => {
    expect(currencyInputStep(dollarCent)).toBe('0.01')
    expect(currencyInputStep(yuanFen)).toBe('0.01')
    expect(currencyInputStep(tenth)).toBe('0.1')
    expect(currencyInputStep(thousandth)).toBe('0.001')
  })

  it('uses 1 for integer currencies, base-unit fallbacks and missing systems', () => {
    expect(currencyInputStep(spirit)).toBe('1')
    expect(currencyInputStep(silverCopper)).toBe('1')
    expect(currencyInputStep(null)).toBe('1')
  })
})