import type { CharacterCard } from '@/api/types'

export function characterCardNeedsConversion(card: CharacterCard, targetRuleId?: string): boolean {
  const source = String(card.rule_id || '').trim()
  const target = String(targetRuleId || '').trim()
  return Boolean(source && target && source !== target)
}

export function characterCardRuleName(card: CharacterCard, unboundLabel: string): string {
  return String(card.rule_name || card.rule_id || unboundLabel)
}
