import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import RulesetPlayHost from '../src/features/rulesets/RulesetPlayHost.vue'

afterEach(() => { document.body.innerHTML = '' })

function mountHost(runtimeId = 'unregistered:ruleset') {
  return mount(RulesetPlayHost, {
    attachTo: document.body,
    props: { runtimeId, activeTool: 'combat' as const, gameKey: 'web|play|bot', actorId: 'gm', isGm: true },
    global: { plugins: [i18n] },
  })
}

describe('ruleset tool window chrome', () => {
  it('keeps the close control inside the tool tab row instead of its own header row', async () => {
    const wrapper = mountHost()
    const bar = document.querySelector<HTMLElement>('.dnd-toolbox-bar')!
    expect(bar).not.toBeNull()
    const close = bar.querySelector<HTMLElement>('[data-testid="dnd-toolbox-close"]')!
    expect(close.parentElement).toBe(bar)
    // 关闭按钮不再挂在 Modal 的 header 上（那条 header 已经收成 sr-only）
    expect(close.closest('header')).toBeNull()
    close.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps the modal title for assistive tech while the visual header is folded away', () => {
    const wrapper = mountHost()
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    const labelledBy = dialog.getAttribute('aria-labelledby')
    expect(labelledBy).toBeTruthy()
    expect(document.getElementById(labelledBy!)?.textContent?.trim().length).toBeGreaterThan(0)
    wrapper.unmount()
  })
})
