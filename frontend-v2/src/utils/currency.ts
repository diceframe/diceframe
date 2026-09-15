/**
 * 统一货币显示/解析工具：canonical base-unit 整数 ↔ 显示金额。
 *
 * 后端 economy 只处理 canonical 整数；所有页面禁止自行 `amount / 100` 或
 * `amount * 100`，一律通过本模块换算，与后端 CurrencyCodec 行为保持一致。
 */

export interface CurrencyUnit {
  id: string
  name: string
  symbol?: string
  rate: number
}

export interface CurrencySystem {
  schema_version?: number
  base_unit: string
  display_unit?: string
  units: CurrencyUnit[]
}

const MAX_FORMAT_DECIMALS = 8
const DECIMAL_RE = /^\d+(\.\d+)?$/

export function resolveDisplayUnit(cs?: CurrencySystem | null): CurrencyUnit {
  const units = (cs?.units || []).filter(u => u && Number(u.rate) > 0)
  if (!units.length) return { id: 'unit', name: '', rate: 1 }
  const want = String(cs?.display_unit || cs?.base_unit || '')
  return units.find(u => u.id === want) || units[0]
}

function decimalsForRate(rate: number): number | null {
  const r = Math.round(Number(rate) || 0)
  if (r <= 1) return null
  for (let candidate = 1; candidate <= MAX_FORMAT_DECIMALS; candidate += 1) {
    if (10 ** candidate % r === 0) return candidate
  }
  return null
}

function unitText(unit: CurrencyUnit, value: string): string {
  return unit.symbol ? `${unit.symbol}${value}` : `${value} ${unit.name}`
}

/**
 * Format one canonical base-unit integer for display.
 * 例如：25 cent → "$0.25"；1250 fen → "¥12.50"；25 灵石 → "25 灵石"。
 * 没有 currency_system 时退回「amount」纯数字。
 */
export function formatCurrencyAmount(
  amount: number,
  cs?: CurrencySystem | null,
  fallbackLabel?: string,
): string {
  const value = Math.round(Number(amount) || 0)
  if (!cs || !(cs.units || []).length) return fallbackLabel ? `${value} ${fallbackLabel}` : String(value)
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  const display = resolveDisplayUnit(cs)
  const rate = Math.round(Number(display.rate) || 1)
  if (rate > 1) {
    const decimals = decimalsForRate(rate)
    if (decimals !== null) {
      const text = (abs / rate).toFixed(decimals)
      return sign + unitText(display, text)
    }
    // rate 无法精确十进制表示：按单位面额贪心分解（与后端一致）。
    const larger = (cs.units || [])
      .filter(u => Number(u.rate) > 1 && Number(u.rate) <= abs)
      .sort((a, b) => Number(b.rate) - Number(a.rate))
    if (larger.length) {
      const parts: string[] = []
      let remaining = abs
      for (const unit of larger) {
        const count = Math.floor(remaining / Number(unit.rate))
        remaining %= Number(unit.rate)
        if (count) parts.push(`${count} ${unit.name}`)
      }
      const baseUnit = (cs.units || []).find(u => u.id === cs.base_unit) || display
      if (remaining || !parts.length) parts.push(`${remaining} ${baseUnit.name || baseUnit.id}`)
      return sign + parts.join(' ')
    }
  }
  return sign + unitText(display, String(abs))
}

/**
 * Parse one display-unit decimal input ("0.25") into a canonical integer,
 * or null when the input is not a positive amount expressible in base units.
 */
export function parseCurrencyInput(
  text: string | number,
  cs?: CurrencySystem | null,
  options?: { allowZero?: boolean },
): number | null {
  const raw = String(text ?? '').trim()
  if (!DECIMAL_RE.test(raw)) return null
  const display = resolveDisplayUnit(cs)
  const rate = Math.round(Number(display.rate) || 1)
  const [intPart, fracPart = ''] = raw.split('.')
  let canonical = Number(intPart) * rate
  if (fracPart) {
    const denominator = 10 ** fracPart.length
    const numerator = Number(fracPart)
    const scaled = (numerator * rate) / denominator
    if (!Number.isInteger(scaled)) return null
    canonical += scaled
  }
  const allowZero = options?.allowZero === true
  return canonical > 0 || (allowZero && canonical === 0) ? canonical : null
}

/** Input step for display-unit amounts: "0.01" when a minor unit exists, else "1". */
export function currencyInputStep(cs?: CurrencySystem | null): string {
  const display = resolveDisplayUnit(cs)
  const rate = Math.round(Number(display.rate) || 1)
  return rate > 1 && decimalsForRate(rate) !== null ? '0.01' : '1'
}
