import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  preview: vi.fn(), apply: vi.fn(), livePreview: vi.fn(), liveApply: vi.fn(),
}))

vi.mock('../src/features/rulesets/dnd2024/api', () => ({
  previewRulesetAdvancement: mocks.preview,
  applyRulesetAdvancement: mocks.apply,
  previewCharacterCardAdvancement: vi.fn(),
  applyCharacterCardAdvancement: vi.fn(),
  previewLiveCharacterAdvancement: mocks.livePreview,
  applyLiveCharacterAdvancement: mocks.liveApply,
}))

import Dnd2024AdvancementPanel from '../src/features/rulesets/dnd2024/progression/Dnd2024AdvancementPanel.vue'

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find(candidate => candidate.text() === text)
  if (!button) throw new Error(`Missing button: ${text}`)
  return button
}

const character = {
  character_name: 'Arden',
  ruleset_character: {
    rule_binding: { runtime_id: 'core:dnd2024' },
    build: { level: 1 },
  },
}

describe('D&D 2024 advancement panel', () => {
  beforeEach(() => {
    mocks.preview.mockReset().mockResolvedValue({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: true, errors: [], requirements: [], from_level: 1, to_level: 2,
        class_ref: 'class:fighter', source_ref: 'srd-5.2.1:p42:fighter-features',
        content_version: 'srd-5.2.1+r2',
        diff: {
          proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 8 },
          gained_feature_ids: ['action_surge'], spell_slot_changes: {}, abilities: {},
        },
        snapshot: {},
      },
    })
    mocks.apply.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd',
      character: { ...character, level: 2 },
    })
    mocks.livePreview.mockReset().mockImplementation(mocks.preview)
    mocks.liveApply.mockReset().mockResolvedValue({
      ok: true, rule_id: 'dnd2024_srd', revision: 4,
      character: { ...character, level: 2, ruleset_revision: 4 },
    })
  })

  it('shows an exact before/after preview and applies only a valid plan', async () => {
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character, language: 'en' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Lv. 1 → Lv. 2')
    expect(wrapper.text()).toContain('action surge')
    expect(wrapper.get('section').attributes('aria-labelledby')).toBe('dnd-advancement-title')
    await buttonByText(wrapper, 'Apply level').trigger('click')
    await flushPromises()

    expect(mocks.apply).toHaveBeenCalledWith(
      'dnd2024_srd', character, { hp_method: 'fixed' }, 'en',
    )
    expect(wrapper.emitted('applied')?.[0]?.[0]).toMatchObject({ level: 2 })
  })

  it('keeps apply disabled while a required subclass is missing', async () => {
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false, errors: ['subclass_ref is required at this level'],
        requirements: [{
          id: 'subclass_ref', kind: 'single', required: true,
          options: [{ value: 'subclass:champion', name: 'Champion' }],
        }],
        from_level: 2, to_level: 3, class_ref: 'class:fighter',
        source_ref: 'srd-5.2.1:p42:fighter-features', content_version: 'srd-5.2.1+r2',
        diff: { proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 8 }, gained_feature_ids: [], spell_slot_changes: {}, abilities: {} }, snapshot: {},
      },
    })
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character, language: 'en' },
    })
    await flushPromises()

    expect((buttonByText(wrapper, 'Apply level').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('Champion')
  })

  it('renders feature choices and submits the picked option', async () => {
    // Without this block the panel showed "feature choice ... requires a
    // selection at this level" and offered nothing to click, so a Champion
    // could never reach level 7.
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false,
        errors: ['feature choice additional_fighting_style requires a selection at this level'],
        requirements: [{
          id: 'feature_choice:subclass:champion:additional_fighting_style:fighting_style_feat',
          kind: 'feature_choice',
          required: true,
          owner: 'subclass:champion',
          feature_id: 'additional_fighting_style',
          choice_id: 'fighting_style_feat',
          count: 1,
          options: [
            {
              value: 'feat:defense', name: 'Defense',
              source_ref: 'srd-5.2.1:p47:defense',
              available: true, unavailable_reasons: [],
            },
            {
              value: 'feat:great_weapon_fighting', name: 'Great Weapon Fighting',
              source_ref: 'srd-5.2.1:p47:great-weapon-fighting',
              available: false, unavailable_reasons: ['effect_not_implemented'],
            },
          ],
        }],
        from_level: 6, to_level: 7, class_ref: 'class:fighter',
        source_ref: 'srd-5.2.1:p42:fighter-features',
        content_version: 'srd-5.2.1+r2',
        diff: {
          proficiency_bonus: { before: 3, after: 3 }, hp: { gain: 6 },
          gained_feature_ids: ['additional_fighting_style'],
          spell_slot_changes: {}, abilities: {},
        },
        snapshot: {},
      },
    })
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character, language: 'en' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Additional Fighting Style')
    // "This ruleset does not implement it" must not read like a prerequisite
    // the player could satisfy by changing ability scores.
    expect(wrapper.text()).toContain('Not implemented in this ruleset yet')

    const defense = wrapper.findAll('label').find(label => label.text().includes('Defense'))
    await defense!.get('input').trigger('change')
    await flushPromises()

    expect(mocks.preview).toHaveBeenLastCalledWith(
      'dnd2024_srd',
      character,
      {
        hp_method: 'fixed',
        feature_choices: [{
          owner: 'subclass:champion',
          feature_id: 'additional_fighting_style',
          choice_id: 'fighting_style_feat',
          selected_refs: ['feat:defense'],
        }],
      },
      'en',
    )
  })

  it('uses the live entity endpoint when game and player IDs are present', async () => {
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: {
        ruleId: 'dnd2024_srd', character, language: 'zh-CN',
        gameKey: 'web|room|bot', userId: 'gm', revision: 3,
      },
    })
    await flushPromises()
    await buttonByText(wrapper, '确认升级').trigger('click')
    await flushPromises()

    expect(mocks.livePreview).toHaveBeenCalledWith(
      'web|room|bot', 'gm', { hp_method: 'fixed' },
    )
    expect(mocks.liveApply).toHaveBeenCalledWith(
      'web|room|bot', 'gm', { hp_method: 'fixed' }, 3, expect.any(String),
    )
    expect(mocks.apply).not.toHaveBeenCalled()
  })

  it('explains exact spell counts and locks extra selections', async () => {
    mocks.preview.mockResolvedValueOnce({
      ok: true,
      rule_id: 'dnd2024_srd',
      advancement: {
        ok: false,
        errors: [
          'prepared_spell_refs must contain exactly 5 spells',
          'spellbook_refs must contain exactly 8 spells',
        ],
        requirements: [{
          id: 'class_spell_choices', kind: 'spell_selection', required: true,
          cantrip_count: 0, prepared_spell_count: 5, spellbook_minimum: 8,
          cantrips: [],
          leveled_spells: Array.from({ length: 9 }, (_, index) => ({
            ref: `spell:spell-${index}`, name: `Spell ${index}`, level: 1,
          })),
        }],
        from_level: 1, to_level: 2, class_ref: 'class:wizard',
        source_ref: 'srd-5.2.1:p42:wizard-features', content_version: 'srd-5.2.1+r2',
        diff: { proficiency_bonus: { before: 2, after: 2 }, hp: { gain: 6 }, gained_feature_ids: [], spell_slot_changes: {}, abilities: {} }, snapshot: {},
      },
    })
    const wizard = {
      ...character,
      ruleset_character: {
        ...character.ruleset_character,
        build: { level: 1, class_levels: [{ class_ref: 'class:wizard', level: 1 }] },
        spellcasting: { class: {
          prepared_spell_refs: Array.from({ length: 5 }, (_, index) => `spell:spell-${index}`),
          spellbook_refs: Array.from({ length: 8 }, (_, index) => `spell:spell-${index}`),
        } },
      },
    }
    const wrapper = mount(Dnd2024AdvancementPanel, {
      props: { ruleId: 'dnd2024_srd', character: wizard, language: 'zh-CN' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('准备法术需要正好 5 个')
    expect(wrapper.text()).toContain('法术书需要正好 8 个法术')
    expect(wrapper.text()).toContain('法师请先选法术书')
    const extraSpellChoices = wrapper.findAll('label').filter(label => label.text().includes('Spell 8'))
    expect(extraSpellChoices).toHaveLength(2)
    expect(extraSpellChoices.every(label => label.get('input').attributes('disabled') !== undefined)).toBe(true)
  })
})
