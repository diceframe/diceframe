import { describe, expect, it } from 'vitest'
import type { CharacterCard } from '../src/api/types'
import { characterCardNeedsConversion, characterCardRuleName } from '../src/utils/characterCards'

function card(ruleId = '', ruleName = ''): CharacterCard {
  return {
    id: 'card-1',
    character_name: 'Test Character',
    rule_id: ruleId,
    rule_name: ruleName,
  }
}

describe('character card rule binding', () => {
  it('does not force legacy unbound cards through conversion', () => {
    expect(characterCardNeedsConversion(card(), 'freeform_fantasy')).toBe(false)
  })

  it('only requests conversion when both rules exist and differ', () => {
    expect(characterCardNeedsConversion(card('freeform_fantasy'), 'freeform_fantasy')).toBe(false)
    expect(characterCardNeedsConversion(card('freeform_coc'), 'freeform_fantasy')).toBe(true)
    expect(characterCardNeedsConversion(card('freeform_coc'))).toBe(false)
  })

  it('uses the friendly rule name and falls back for legacy cards', () => {
    expect(characterCardRuleName(card('freeform_coc', 'Call of Cthulhu'), 'Unbound')).toBe('Call of Cthulhu')
    expect(characterCardRuleName(card(), 'Unbound')).toBe('Unbound')
  })
})
