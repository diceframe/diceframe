/** Reasoning effort levels, mirrored from src/llm/reasoning.py; the backend validates saves. */
import type { MessageKey } from '@/i18n'

export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
export type ReasoningEffortConfigKey =
  | 'model_reasoning_effort' | 'fallback1_reasoning_effort'
  | 'fallback2_reasoning_effort' | 'check_model_reasoning_effort'

export const REASONING_EFFORT_LEVELS: readonly ReasoningEffort[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
export const REASONING_EFFORT_LABEL_KEYS: Record<ReasoningEffort, MessageKey> = {
  none: 'reasoningEffortNone', minimal: 'reasoningEffortMinimal', low: 'reasoningEffortLow',
  medium: 'reasoningEffortMedium', high: 'reasoningEffortHigh', xhigh: 'reasoningEffortXhigh', max: 'reasoningEffortMax',
}
// Anthropic Messages carries effort in output_config.effort and has no "off" level.
const ANTHROPIC_LEVELS: ReadonlySet<ReasoningEffort> = new Set(['low', 'medium', 'high', 'xhigh', 'max'])

/** Levels the provider's API format can express. The backend rejects the rest on save. */
export function reasoningEffortLevels(apiFormat?: string): readonly ReasoningEffort[] {
  return apiFormat === 'anthropic'
    ? REASONING_EFFORT_LEVELS.filter(level => ANTHROPIC_LEVELS.has(level))
    : REASONING_EFFORT_LEVELS
}

export function isReasoningEffortPreset(value: string): value is ReasoningEffort {
  return (REASONING_EFFORT_LEVELS as readonly string[]).includes(value)
}

/** The reasoning-effort setting that belongs to a chat route's provider reference, if it has one. */
export function reasoningEffortKeyFor(providerRefKey: string): ReasoningEffortConfigKey | undefined {
  switch (providerRefKey) {
    case 'llm_provider_ref': return 'model_reasoning_effort'
    case 'fallback1_provider_ref': return 'fallback1_reasoning_effort'
    case 'fallback2_provider_ref': return 'fallback2_reasoning_effort'
    case 'check_provider_ref': return 'check_model_reasoning_effort'
    default: return undefined
  }
}
