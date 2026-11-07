import { describe, expect, it } from 'vitest'

import {
  GAL_RULE_ID,
  galCreateIsConsistent,
  galLinkageForActiveRule,
  galLinkageForRule,
  nonGalFallbackRule,
} from '../src/features/create/galMode'

describe('GAL rule ⇄ play mode linkage', () => {
  it('switches to gal play and forces solo when the GAL rule is picked', () => {
    expect(galLinkageForRule('dnd2024_srd', GAL_RULE_ID, { playMode: 'free', solo: false })).toEqual({
      playMode: 'gal', solo: true,
    })
    // 已经是 GAL 单人局：不需要任何改动，避免把用户设置覆盖回去
    expect(galLinkageForRule('', GAL_RULE_ID, { playMode: 'gal', solo: true })).toBeNull()
  })

  it('restores free play when leaving the GAL rule', () => {
    expect(galLinkageForRule(GAL_RULE_ID, 'dnd2024_srd', { playMode: 'gal', solo: true })).toEqual({
      playMode: 'free', solo: true,
    })
    // 普通规则之间切换不动玩法模式
    expect(galLinkageForRule('dnd2024_srd', 'coc7e', { playMode: 'adventure', solo: false })).toBeNull()
  })

  it('falls back to a non-GAL rule when the player leaves GAL through the play-mode buttons', () => {
    expect(nonGalFallbackRule([GAL_RULE_ID, 'coc7e', 'freeform'])).toBe('coc7e')
    expect(nonGalFallbackRule(['', undefined, null])).toBe('freeform')
  })

  it('rejects payloads the server would refuse', () => {
    expect(galCreateIsConsistent(GAL_RULE_ID, 'gal', true)).toBe(true)
    expect(galCreateIsConsistent(GAL_RULE_ID, 'free', true)).toBe(false)
    expect(galCreateIsConsistent(GAL_RULE_ID, 'gal', false)).toBe(false)
    expect(galCreateIsConsistent('dnd2024_srd', 'gal', true)).toBe(false)
    expect(galCreateIsConsistent('dnd2024_srd', 'adventure', false)).toBe(true)
  })
})

describe('GAL active-rule correction (source-agnostic)', () => {
  it('forces gal+solo whenever the effective rule is the GAL rule', () => {
    expect(galLinkageForActiveRule(GAL_RULE_ID, { playMode: 'free', solo: false })).toEqual({
      playMode: 'gal', solo: true,
    })
    expect(galLinkageForActiveRule(GAL_RULE_ID, { playMode: 'adventure', solo: true })).toEqual({
      playMode: 'gal', solo: true,
    })
    expect(galLinkageForActiveRule(GAL_RULE_ID, { playMode: 'gal', solo: true })).toBeNull()
  })

  it('demotes an illegal gal play mode when the effective rule is not GAL', () => {
    // 模板来源选了 GAL 后切到 AI/自定义来源：生效规则已不是 GAL，gal 必须降级
    expect(galLinkageForActiveRule('dnd2024_srd', { playMode: 'gal', solo: true })).toEqual({
      playMode: 'free', solo: true,
    })
    expect(galLinkageForActiveRule('', { playMode: 'gal', solo: false })).toEqual({
      playMode: 'free', solo: false,
    })
  })

  it('keeps adventure mode untouched for non-GAL rules', () => {
    // 冒险模式的规则切换不应间接取消冒险
    expect(galLinkageForActiveRule('coc7e', { playMode: 'adventure', solo: false })).toBeNull()
    expect(galLinkageForActiveRule('dnd2024_srd', { playMode: 'free', solo: true })).toBeNull()
  })
})
