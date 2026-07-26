import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'

import NapcatGuide from '@/components/plugins/NapcatGuide.vue'
import { messages } from '@/i18n'

describe('NapcatGuide', () => {
  it('renders the complete Chinese guide with literal @bot commands', () => {
    const i18n = createI18n({
      legacy: false,
      locale: 'zh-CN',
      fallbackLocale: 'zh-CN',
      messages,
    })
    const wrapper = mount(NapcatGuide, {
      global: { plugins: [i18n] },
    })

    expect(wrapper.text()).toContain('使用说明')
    expect(wrapper.text()).toContain('到群聊里粘贴发送这条绑定指令，并 @bot')
    expect(wrapper.text()).toContain('让玩家先在群聊里 @bot 发送“加入 角色名”')
    expect(wrapper.text()).toContain('6. 卡片缓存')
  })
})
