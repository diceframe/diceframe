import { mount } from '@vue/test-utils'
import { describe, expect, it, beforeEach } from 'vitest'
import { i18n } from '../src/i18n'
import GmToolbar from '../src/components/play/GmToolbar.vue'

const baseDetail = {
  game_key: 'web|style|bot',
  round_number: 1,
  state: 'active_action',
  solo_mode: true,
  narrative_perspective: 'auto',
  gm_style_override: null,
  player_access_open: true,
  players: [],
}

function mountToolbar(gmStyleOverride: unknown) {
  return mount(GmToolbar, {
    global: { plugins: [i18n] },
    props: {
      detail: { ...baseDetail, gm_style_override: gmStyleOverride },
      players: [],
      isGm: true,
    },
  })
}

function lastGmStylePayload(wrapper: ReturnType<typeof mountToolbar>) {
  const emitted = wrapper.emitted('gm-style') as Array<[{ gm_style: unknown }]> | undefined
  return emitted?.at(-1)?.[0]?.gm_style
}

describe('GmToolbar GM narration style', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
  })

  it('follows world (checked, controls hidden) when override is null', () => {
    const wrapper = mountToolbar(null)
    expect((wrapper.find('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('unchecking emits an explicit neutral override and enables the controls', async () => {
    const wrapper = mountToolbar(null)
    await wrapper.find('input[type="checkbox"]').setValue(false)
    expect(lastGmStylePayload(wrapper)).toEqual({
      tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '',
    })
    // 父组件保存并重新拉取 detail 后，gm_style_override 变为显式 dict。
    await wrapper.setProps({
      detail: {
        ...baseDetail,
        gm_style_override: { tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' },
      },
    })
    expect((wrapper.find('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(true)
  })

  it('selecting literary + detailed + slow emits the right body', async () => {
    const wrapper = mountToolbar({})
    const buttons = wrapper.findAll('.gm-style-options button')
    await buttons[1].trigger('click') // 文学
    await buttons[7].trigger('click') // 详细
    await buttons[8].trigger('click') // 慢
    expect(lastGmStylePayload(wrapper)).toMatchObject({
      tone: 'literary', verbosity: 'detailed', pace: 'slow',
    })
  })

  it('restores the draft from game detail on reopen', () => {
    const wrapper = mountToolbar({ tone: 'literary', verbosity: 'detailed', pace: 'slow' })
    expect((wrapper.find('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
    const active = wrapper.findAll('.gm-style-options button.active').map(b => b.text())
    expect(active).toEqual(['文学', '详细', '慢'])
  })

  it('checking follow world again emits null', async () => {
    const wrapper = mountToolbar({ tone: 'dark' })
    await wrapper.find('input[type="checkbox"]').setValue(true)
    expect(lastGmStylePayload(wrapper)).toBeNull()
  })
})
