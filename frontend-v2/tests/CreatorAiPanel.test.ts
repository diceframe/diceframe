import { mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import CreatorAiPanel from '../src/features/creator/CreatorAiPanel.vue'
import type { AssistantMessage } from '../src/composables/useAssistant'

const messages = ref<AssistantMessage[]>([])
const streaming = ref(false)
const status = ref('')
const task = ref(null)
const actionError = ref('')
const pendingApproval = ref(null)
const actionBusy = ref(false)

vi.mock('@/composables/useCreatorAssistant', () => ({
  useCreatorAssistant: () => ({
    messages,
    streaming,
    status,
    task,
    actionError,
    pendingApproval,
    actionBusy,
    send: vi.fn(),
    stop: vi.fn(),
    confirmApproval: vi.fn(),
  }),
}))

describe('CreatorAiPanel', () => {
  beforeEach(() => {
    messages.value = []
    streaming.value = false
    status.value = ''
    task.value = null
    actionError.value = ''
    pendingApproval.value = null
    actionBusy.value = false
    i18n.global.locale.value = 'zh-CN'
  })

  it('keeps a request error visible instead of rendering an apparently empty assistant turn', () => {
    messages.value = [{ role: 'assistant', content: '', error: '模型服务连接失败' }]
    const wrapper = mount(CreatorAiPanel, { global: { plugins: [i18n] }, props: { project: null } })

    expect(wrapper.get('.creator-ai-text').text()).toContain('模型服务连接失败')
    wrapper.unmount()
  })
})
