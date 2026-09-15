/**
 * 统一货币显示/解析工具：canonical base-unit 整数 ↔ 显示金额 / 输入文本。
 *
 * 后端 economy 只处理 canonical 整数；所有页面禁止自行 `amount / 100` 或
 * `amount * 100`，一律通过本模块换算，与后端 CurrencyCodec 行为保持一致。
 *
 * 边界（展示与输入分离）：
 * - `formatCurrencyAmount` → UI 展示（可含 $/¥/单位名称）；
 * - `currencyAmountToInputText` → 编辑框初值（纯数字字符串）；
 * - `parseCurrencyInput` → 编辑框字符串 → canonical int。
 * input formatter 与 input parser 永远使用同一个「可编辑单位」
 * （`resolveEditableUnit`）：display.rate 可有限十进制表示时用 display
 * 单位，否则回退 base unit。
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
 * 编辑输入使用的单位：display.rate 可有限十进制表示（且 >1）时用 display
 * 单位（输入 0.25 表示 $0.25）；否则回退 base unit（输入 4 表示 4 个基础
 * 单位），避免编辑框出现无法精确解析的小数。
 */
export function resolveEditableUnit(cs?: CurrencySystem | null): CurrencyUnit {
  const display = resolveDisplayUnit(cs)
  const rate = Math.round(Number(display.rate) || 1)
  if (rate > 1 && decimalsForRate(rate) !== null) return display
  const units = (cs?.units || []).filter(u => u && Number(u.rate) > 0)
  return units.find(u => u.id === cs?.base_unit)
    || units.find(u => Math.round(Number(u.rate) || 0) === 1)
    || display
}

/**
 * Format one canonical base-unit integer for display.
 * 例如：25 cent → "$0.25"；1250 fen → "¥12.50"；25 灵石 → "25 灵石"。
 * 没有 currency_system 时退回「amount」纯数字。
 * display.rate 无法有限十进制表示（如 rate=3）时，不做小数近似，按单位
 * 面额贪心分解，剩余部分以 base unit 显示（1 → "1 铜币"）。
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
  const displayRate = Math.round(Number(display.rate) || 1)
  if (displayRate > 1) {
    const decimals = decimalsForRate(displayRate)
    if (decimals !== null) {
      const text = (abs / displayRate).toFixed(decimals)
      return sign + unitText(display, text)
    }
  }
  // display 即 base（或 rate 无法精确十进制表示）：按单位面额贪心分解。
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
  if (displayRate === 1) return sign + unitText(display, String(abs))
  // 金额不足以兑任何高面额单位：以 base unit 显示，绝不冒充高面额单位。
  const baseUnit = (cs.units || []).find(u => u.id === cs.base_unit)
    || (cs.units || []).find(u => Number(u.rate) === 1)
    || display
  return sign + unitText(baseUnit, String(abs))
}

/**
 * 当前金额输入框实际使用的单位名（与 parser 同单位）。
 * display.rate 无法有限十进制表示时是 base unit 名（如「铜币」），
 * 让标签不会与 parse 行为不一致；拿不到名称时回退 fallbackLabel。
 */
export function currencyEditableUnitLabel(
  cs?: CurrencySystem | null,
  fallbackLabel?: string,
): string {
  const unit = resolveEditableUnit(cs)
  if (unit?.name) return unit.name
  return fallbackLabel || ''
}

/**
 * canonical integer → 仅供编辑框使用的纯数字字符串（不含 $/¥/单位名）。
 * 与 `parseCurrencyInput` 使用同一个可编辑单位，保证 round-trip。
 */
export function currencyAmountToInputText(
  amount: number,
  cs?: CurrencySystem | null,
): string {
  const value = Math.round(Number(amount) || 0)
  const unit = resolveEditableUnit(cs)
  const rate = Math.round(Number(unit.rate) || 1)
  if (rate <= 1) return String(value)
  const decimals = decimalsForRate(rate)
  if (decimals === null) return String(value)
  return (value / rate).toFixed(decimals)
}

/**
 * Parse one editable-unit decimal input ("0.25") into a canonical integer,
 * or null when the input is not a positive amount expressible in base units.
 * `allowZero` 供角色卡余额编辑使用（0 是合法余额）；扣款/上限等路径保持 >0。
 */
export function parseCurrencyInput(
  text: string | number,
  cs?: CurrencySystem | null,
  options?: { allowZero?: boolean },
): number | null {
  const raw = String(text ?? '').trim()
  if (!DECIMAL_RE.test(raw)) return null
  const unit = resolveEditableUnit(cs)
  const rate = Math.round(Number(unit.rate) || 1)
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

/** 编辑框最小步长：跟随可编辑单位 rate（100 → "0.01"，1000 → "0.001"，
 * 10 → "0.1"）；rate=1 或无法有限十进制表示（回退 base unit）时为 "1"。 */
export function currencyInputStep(cs?: CurrencySystem | null): string {
  const unit = resolveEditableUnit(cs)
  const rate = Math.round(Number(unit.rate) || 1)
  const decimals = rate > 1 ? decimalsForRate(rate) : null
  if (decimals === null) return '1'
  return `0.${'0'.repeat(decimals - 1)}1`
}
