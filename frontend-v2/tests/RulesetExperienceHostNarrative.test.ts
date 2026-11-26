import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CharacterSheet } from '../src/api/types'

const mocks = vi.hoisted(() => ({ fetchRulesetExperience: vi.fn() }))

vi.mock('../src/api/rulesets', () => ({
  fetchRulesetExperience: mocks.fetchRulesetExperience,
}))
vi.mock('../src/features/rulesets/registry', () => ({
  resolveRulesetExperience: () => BuilderStub,
  resolveRulesetAdvancementExtension: () => null,
}))
vi.mock('../src/features/rulesets/GenericCharacterBuilder.vue', () => ({
  default: { template: '<div />' },
}))

const BuilderStub = {
  name: 'BuilderStub',
  props: ['initial', 'ruleId', 'language', 'embedded', 'experience'],
  emits: ['submit', 'cancel'],
  template: '<div data-testid="builder" />',
}

import RulesetExperienceHost from '../src/features/rulesets/RulesetExperienceHost.vue'

const PROFILE = {
  appearance: 'Red hair, dented pauldron.',
  personality: 'Clipped and practical.',
}
const PORTRAIT = { kind: 'builtin', id: 'wizard-1' }

function authoredCard(): CharacterSheet {
  return {
    character_name: 'Gill Hella',
    portrait: { ...PORTRAIT } as CharacterSheet['portrait'],
    ruleset_character: {
      identity: { name: 'Gill Hella' },
      build: { level: 1, class_levels: [{ class_ref: 'class:paladin', level: 1 }] },
      profile: { ...PROFILE },
    },
    rule_binding: { rule_id: 'dnd2024_srd', runtime_id: 'core:dnd2024' },
  } as CharacterSheet
}

/** What the current ruleset builder returns: mechanics only, no narrative data. */
function rebuiltCard(): CharacterSheet {
  return {
    character_name: 'Gill Hella',
    ruleset_character: {
      identity: { name: 'Gill Hella' },
      build: { level: 1, class_levels: [{ class_ref: 'class:paladin', level: 1 }] },
    },
    rule_binding: { rule_id: 'dnd2024_srd', runtime_id: 'core:dnd2024' },
  } as CharacterSheet
}

async function renderHost(initial?: CharacterSheet) {
  const wrapper = mount(RulesetExperienceHost, {
    props: { ruleId: 'dnd2024_srd', language: 'en', initial, chain: false },
  })
  await flushPromises()
  return wrapper
}

describe('RulesetExperienceHost narrative carry-over', () => {
  beforeEach(() => {
    mocks.fetchRulesetExperience.mockReset()
    mocks.fetchRulesetExperience.mockResolvedValue({
      experience: { profile: 'dnd2024' },
      ruleset_runtime: { id: 'core:dnd2024', capabilities: { character_builder: 'professional' } },
    })
  })

  it('keeps the profile and portrait of the card the builder was opened with', async () => {
    const wrapper = await renderHost(authoredCard())

    wrapper.findComponent(BuilderStub).vm.$emit('submit', rebuiltCard())
    await flushPromises()

    const [submitted] = wrapper.emitted('submit')?.[0] as [CharacterSheet]
    expect((submitted.ruleset_character as Record<string, unknown>).profile).toEqual(PROFILE)
    expect(submitted.portrait).toEqual(PORTRAIT)
    // 机制仍然由建卡器决定。
    expect((submitted.ruleset_character as Record<string, unknown>).build).toEqual(
      (rebuiltCard().ruleset_character as Record<string, unknown>).build,
    )
  })

  it('never overwrites a profile the rebuild already produced', async () => {
    const wrapper = await renderHost(authoredCard())
    const rebuilt = rebuiltCard()
    const rebuiltProfile = { notes: 'typed inside the builder' }
    ;(rebuilt.ruleset_character as Record<string, unknown>).profile = rebuiltProfile

    wrapper.findComponent(BuilderStub).vm.$emit('submit', rebuilt)
    await flushPromises()

    const [submitted] = wrapper.emitted('submit')?.[0] as [CharacterSheet]
    expect((submitted.ruleset_character as Record<string, unknown>).profile).toEqual(rebuiltProfile)
  })

  it('leaves a brand-new character untouched', async () => {
    const wrapper = await renderHost(undefined)

    wrapper.findComponent(BuilderStub).vm.$emit('submit', rebuiltCard())
    await flushPromises()

    const [submitted] = wrapper.emitted('submit')?.[0] as [CharacterSheet]
    expect(submitted.portrait).toBeUndefined()
    expect((submitted.ruleset_character as Record<string, unknown>).profile).toBeUndefined()
  })
})