import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, LOCALE_STORAGE_KEY } from '../src/i18n'
import AccessPasswordHelp from '../src/features/auth/AccessPasswordHelp.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
}))

import LoginView from '../src/features/auth/LoginView.vue'

describe('AccessPasswordHelp', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    document.body.innerHTML = ''
  })

  it('opens a guide that covers every way of starting DiceFrame', async () => {
    const wrapper = mount(AccessPasswordHelp, { global: { plugins: [i18n] }, attachTo: document.body })
    expect(document.body.textContent).not.toContain('Initial access password:')

    await wrapper.get('[data-testid="access-help-open"]').trigger('click')

    const text = document.body.textContent ?? ''
    expect(text).toContain('如何找到访问密码')
    expect(text).toContain('Windows 便携版')
    expect(text).toContain('Docker')
    expect(text).toContain(String.raw`data\access_token.txt`)
    expect(text).toContain('data/reset_access_password.txt')
    expect(text).toContain('TRPG_ACCESS_TOKEN')
    expect(text).toContain('邀请链接')
    wrapper.unmount()
  })

  it('shows English copy when the UI language is English', async () => {
    i18n.global.locale.value = 'en'
    const wrapper = mount(AccessPasswordHelp, { global: { plugins: [i18n] }, attachTo: document.body })
    expect(wrapper.get('[data-testid="access-help-open"]').text()).toBe('Where is the access password?')
    wrapper.unmount()
  })

  it('keeps the access-password guide in the first-start hint instead of a separate row', () => {
    vi.stubGlobal('__DF_STANDALONE__', false)
    i18n.global.locale.value = 'zh-CN'
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN')
    const wrapper = mount(LoginView, { global: { plugins: [i18n] }, attachTo: document.body })

    const firstStartHint = wrapper.get('.login-password-hint')
    const accessHelp = firstStartHint.get('[data-testid="access-help-open"]')
    expect(getComputedStyle(accessHelp.element).display).not.toBe('block')
    expect(wrapper.find('.login-help > [data-testid="access-help-open"]').exists()).toBe(false)

    wrapper.unmount()
  })
})
