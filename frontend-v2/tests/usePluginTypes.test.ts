import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  listTypes: vi.fn(),
  t: vi.fn(),
}))

vi.mock('../src/api/plugins', () => ({
  pluginApi: { listTypes: mocks.listTypes },
}))

vi.mock('../src/composables/useLocale', () => ({
  useLocale: () => ({ t: mocks.t }),
}))

import { usePluginTypes } from '../src/features/plugins/usePluginTypes'

describe('usePluginTypes', () => {
  beforeEach(() => {
    mocks.listTypes.mockReset()
    mocks.t.mockReset()
    // t 直接走全局 i18n，绕过 useI18n 的 setup 上下文要求
    mocks.t.mockImplementation((key: string) => i18n.global.t(key as never))
    i18n.global.locale.value = 'zh-CN'
  })

  it('exposes filterable types sorted by filter_order with convention label keys', async () => {
    mocks.listTypes.mockResolvedValue({
      ok: true,
      types: [
        { id: 'tool', level: 'supported', filterable: true, filter_order: 4 },
        { id: 'content-pack', level: 'supported', filterable: true, filter_order: 1 },
        { id: 'bot-extension', level: 'supported', filterable: false, filter_order: 0 },
        { id: 'theme', level: 'supported', filterable: true, filter_order: 2 },
        { id: 'voice-pack', level: 'supported', filterable: true, filter_order: 3 },
      ],
    })
    const { pluginTypeFilters, loadTypes } = usePluginTypes()
    await loadTypes()
    expect(pluginTypeFilters.value).toEqual([
      { value: 'content-pack', labelKey: 'pluginTypeContentPack' },
      { value: 'theme', labelKey: 'pluginTypeTheme' },
      { value: 'voice-pack', labelKey: 'pluginTypeVoicePack' },
      { value: 'tool', labelKey: 'pluginTypeTool' },
    ])
  })

  it('resolves type labels by i18n convention, falling back to raw id', () => {
    const { pluginTypeLabel } = usePluginTypes()
    expect(pluginTypeLabel('content-pack')).toBe('内容包')
    expect(pluginTypeLabel('theme')).toBe('主题')
    expect(pluginTypeLabel('voice-pack')).toBe('音色预设')
    expect(pluginTypeLabel('bot-extension')).toBe('Bot Bridge 扩展')
    // 未知类型：i18n 无对应键，回退原始 id
    expect(pluginTypeLabel('unknown-type')).toBe('unknown-type')
    expect(pluginTypeLabel(undefined)).toBe(i18n.global.t('uncategorized' as never))
  })
})
