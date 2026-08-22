import { describe, expect, it } from 'vitest'
import { builtinPortraits, defaultBuiltinPortrait, resolveBuiltinPortrait } from '../src/utils/portraits'

describe('character portraits', () => {
  it('provides a distinct mixed-style portrait set for every built-in ruleset', () => {
    const allImages = new Set<string>()
    let optionCount = 0
    for (const ruleId of ['dnd5e', 'freeform_coc', 'freeform_cyberpunk', 'freeform_fantasy', 'freeform_wuxia', 'tavern_free']) {
      const options = builtinPortraits(ruleId)
      expect(options.length).toBeGreaterThanOrEqual(2)
      expect(options.map(option => option.id)).toEqual(options.map((_, index) => `${ruleId}:${index}`))
      expect(new Set(options.map(option => option.image)).size).toBe(options.length)
      expect(options.every(option => option.image.includes(`/avatars/v3/${ruleId}/`))).toBe(true)
      expect(options.some(option => option.style === 'realistic')).toBe(true)
      expect(options.some(option => option.style === 'anime')).toBe(true)
      expect(options.every(option => option.position === '50% 26%')).toBe(true)
      options.forEach(option => allImages.add(option.image))
      optionCount += options.length
    }
    expect(allImages.size).toBe(optionCount)
  })

  it('selects stable defaults that remain inside the available portrait set', () => {
    expect(defaultBuiltinPortrait('freeform_coc', 'player_1')).toEqual(defaultBuiltinPortrait('freeform_coc', 'player_1'))
    const available = builtinPortraits('freeform_coc')
    const selected = new Set(Array.from({ length: 128 }, (_, index) => defaultBuiltinPortrait('freeform_coc', `player_${index}`).index))
    expect(selected.size).toBeGreaterThan(1)
    expect([...selected].every(index => index >= 0 && index < available.length)).toBe(true)
    expect(resolveBuiltinPortrait(undefined, 'freeform_coc_en', 'player_1').ruleId).toBe('freeform_coc')
  })

  it('resolves the new images without changing stored portrait ids', () => {
    const portrait = resolveBuiltinPortrait({ kind: 'builtin', id: 'freeform_wuxia:6' })
    expect(portrait.index).toBe(6)
    expect(portrait.style).toBe('anime')
    expect(portrait.image).toMatch(/\/avatars\/v3\/freeform_wuxia\/anime-3\.jpg$/)
    expect(portrait.position).toBe('50% 26%')
  })

  it('falls custom rules back to the generic fantasy pack', () => {
    expect(builtinPortraits('my_custom_rule')[0].ruleId).toBe('freeform_fantasy')
  })
})
