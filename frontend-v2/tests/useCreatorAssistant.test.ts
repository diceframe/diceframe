import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'
import { useCreatorAssistant } from '../src/composables/useCreatorAssistant'

describe('useCreatorAssistant', () => {
  beforeEach(() => localStorage.clear())

  it('keeps project conversations separate and restores each project transcript', async () => {
    const scope = ref<string | undefined>('project-a')
    const assistant = useCreatorAssistant(scope)
    await nextTick()
    assistant.messages.value.push({ role: 'user', content: '给 A 加一个 NPC' })

    scope.value = 'project-b'
    await nextTick()
    await Promise.resolve()
    expect(assistant.messages.value).toEqual([])
    assistant.messages.value.push({ role: 'user', content: '给 B 加一条规则' })

    scope.value = 'project-a'
    await nextTick()
    await Promise.resolve()
    expect(assistant.messages.value.map(message => message.content)).toEqual(['给 A 加一个 NPC'])
  })
})
