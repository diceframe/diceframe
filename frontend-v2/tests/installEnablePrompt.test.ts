import { beforeEach, describe, expect, it, vi } from 'vitest'
import { registerNaiveApis } from '@/composables/useNaiveBridge'

vi.mock('@/api/plugins', () => ({
  pluginApi: { setRunning: vi.fn(async () => ({ ok: true })) },
}))

import { pluginApi } from '@/api/plugins'
import { promptEnableAfterInstall } from '@/features/plugins/installEnable'

type DialogConfig = {
  positiveText?: string
  negativeText?: string
  onPositiveClick?: () => void
  onNegativeClick?: () => void
  onClose?: () => void
}

const dialogs: DialogConfig[] = []
const messages: string[] = []

registerNaiveApis(
  {
    success: (content: string) => messages.push(content),
    error: (content: string) => messages.push(content),
    info: (content: string) => messages.push(content),
    warning: (content: string) => messages.push(content),
  } as never,
  {
    info: (config: DialogConfig) => dialogs.push(config),
    warning: (config: DialogConfig) => dialogs.push(config),
    error: (config: DialogConfig) => dialogs.push(config),
    success: (config: DialogConfig) => dialogs.push(config),
  } as never,
)

beforeEach(() => {
  dialogs.length = 0
  messages.length = 0
  vi.mocked(pluginApi.setRunning).mockClear()
})

describe('promptEnableAfterInstall', () => {
  it('asks to enable a freshly installed artifact and starts it on confirm', async () => {
    const pending = promptEnableAfterInstall({ id: 'coc-pack', name: 'CoC Pack', enabled: false })
    expect(dialogs).toHaveLength(1)
    expect(dialogs[0]!.positiveText).toBeTruthy()
    dialogs[0]!.onPositiveClick?.()
    await expect(pending).resolves.toBe(true)
    expect(pluginApi.setRunning).toHaveBeenCalledWith('coc-pack', true)
    expect(messages.length).toBe(1)
  })

  it('leaves the artifact disabled when the user declines', async () => {
    const pending = promptEnableAfterInstall({ id: 'coc-pack', name: 'CoC Pack' })
    dialogs[0]!.onNegativeClick?.()
    await expect(pending).resolves.toBe(false)
    expect(pluginApi.setRunning).not.toHaveBeenCalled()
  })

  it('does not ask when the host already reports the artifact as enabled', async () => {
    await expect(promptEnableAfterInstall({ id: 'coc-pack', enabled: true })).resolves.toBe(false)
    expect(dialogs).toHaveLength(0)
    expect(pluginApi.setRunning).not.toHaveBeenCalled()
  })

  it('does not ask without an id (install response was incomplete)', async () => {
    await expect(promptEnableAfterInstall({ id: '', name: 'x' })).resolves.toBe(false)
    expect(dialogs).toHaveLength(0)
  })
})
