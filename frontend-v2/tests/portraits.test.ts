import { describe, expect, it } from 'vitest'
import { builtinPortraits, defaultBuiltinPortrait, resolveBuiltinPortrait } from '../src/utils/portraits'

describe('character portraits', () => {
  it('provides eight portraits across two atlases for every built-in ruleset', () => {
    for (const ruleId of ['dnd5e', 'freeform_coc', 'freeform_cyberpunk', 'freeform_fantasy', 'freeform_wuxia', 'tavern_free']) {
      const options = builtinPortraits(ruleId)
      expect(options).toHaveLength(8)
      expect(options.map(option => option.id)).toEqual([0, 1, 2, 3, 4, 5, 6, 7].map(index => `${ruleId}:${index}`))
      expect(options.slice(0, 4).every(option => option.image.endsWith(`/avatars/${ruleId}.png`))).toBe(true)
      expect(options.slice(4).every(option => option.image.endsWith(`/avatars/${ruleId}_2.png`))).toBe(true)
    }
  })

  it('selects stable defaults from all eight portraits', () => {
    expect(defaultBuiltinPortrait('freeform_coc', 'player_1')).toEqual(defaultBuiltinPortrait('freeform_coc', 'player_1'))
    const selected = new Set(Array.from({ length: 128 }, (_, index) => defaultBuiltinPortrait('freeform_coc', `player_${index}`).index))
    expect(selected).toEqual(new Set([0, 1, 2, 3, 4, 5, 6, 7]))
    expect(resolveBuiltinPortrait(undefined, 'freeform_coc_en', 'player_1').ruleId).toBe('freeform_coc')
  })

  it('resolves portraits from the added atlas without changing their public ids', () => {
    const portrait = resolveBuiltinPortrait({ kind: 'builtin', id: 'freeform_wuxia:6' })
    expect(portrait.index).toBe(6)
    expect(portrait.image).toMatch(/\/avatars\/freeform_wuxia_2\.png$/)
    expect(portrait.position).toBe('0% 100%')
  })

  it('falls custom rules back to the generic fantasy pack', () => {
    expect(builtinPortraits('my_custom_rule')[0].ruleId).toBe('freeform_fantasy')
  })
})
