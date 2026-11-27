import { i18n } from '@/i18n'

/**
 * Check verdict codes produced by the server (src/engine/check_verdict.py).
 *
 * Display text must never be used to infer success: an older save or a custom
 * ruleset runtime may carry no code, in which case the caller falls back to the
 * stored label instead of guessing from its wording.
 */
export type CheckVerdictCode =
  | 'critical_failure'
  | 'failure'
  | 'success'
  | 'regular_success'
  | 'hard_success'
  | 'extreme_success'
  | 'critical_success'

const SUCCESS_CODES = new Set<CheckVerdictCode>([
  'success',
  'regular_success',
  'hard_success',
  'extreme_success',
  'critical_success',
])

const CODES = new Set<string>([
  'critical_failure',
  'failure',
  'success',
  'regular_success',
  'hard_success',
  'extreme_success',
  'critical_success',
])

export function verdictCodeOf(check: { verdict_code?: unknown } | null | undefined): CheckVerdictCode | undefined {
  const raw = typeof check?.verdict_code === 'string' ? check.verdict_code.toLowerCase() : ''
  return CODES.has(raw) ? (raw as CheckVerdictCode) : undefined
}

export function isSuccessVerdict(code: CheckVerdictCode | undefined): boolean {
  return code ? SUCCESS_CODES.has(code) : false
}

/** Localized label for a code; '' when the code is missing or unknown. */
export function verdictLabel(code: CheckVerdictCode | string | undefined): string {
  if (!code || !CODES.has(code)) return ''
  const key = `verdict.${code}`
  const text = i18n.global.t(key)
  return text === key ? '' : text
}

/**
 * Best-effort label for a saved check: the localized code, else the label the
 * server froze at resolve time (custom runtimes, legacy saves).
 */
export function checkVerdictLabel(check: {
  verdict_code?: unknown
  verdict?: unknown
} | null | undefined): string {
  return verdictLabel(verdictCodeOf(check)) || String(check?.verdict ?? '')
}