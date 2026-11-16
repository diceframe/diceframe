import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { i18n } from '../src/i18n'
import { useInstalledPlugins } from '../src/features/plugins/useInstalledPlugins'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  updateConfig: vi.fn(),
  setRunning: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('../src/api/plugins', () => ({
  pluginApi: {
    list: mocks.list,
    updateConfig: mocks.updateConfig,
    setRunning: mocks.setRunning,
    restart: vi.fn(),
    clearCardCache: vi.fn(),
    install: vi.fn(),
    rescan: vi.fn(),
  },
}))

vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: mocks.confirm }),
}))

vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

// 依赖契约的两条边：运行时插件 fu-core（提供 fu:core）与内容包 fu-adventures。
function pluginPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'fu-core',
    name: '最终物语 Fabula Ultima',
    version: '1.2.0',
    plugin_type: 'ruleset-runtime',
    enabled: false,
    running: false,
    status: 'disabled',
    has_entrypoint: true,
    dependencies: [],
    ...overrides,
  }
}

function adventurePack(dependencies: unknown[], overrides: Record<string, unknown> = {}) {
  return pluginPayload({
    id: 'fu-adventures',
    name: '最终物语：起始冒险',
    version: '1.0.0',
    plugin_type: 'content-pack',
    has_entrypoint: false,
    // 内容包的开关就是 config.enabled（save 只提交 schema 里声明的字段）。
    schema: { properties: { enabled: { type: 'boolean', title: '启用' } } },
    dependencies,
    ...overrides,
  })
}

function mountHarness() {
  const busy = ref('')
  const typeFilter = ref('')
  const harness = defineComponent({
    setup() {
      const api = useInstalledPlugins(
        busy,
        async () => undefined,
        async () => undefined,
        typeFilter,
      )
      return { api, busy }
    },
    template: '<div />',
  })
  return mount(harness, { global: { plugins: [i18n] } })
}

type Harness = ReturnType<typeof mountHarness>['vm'] & {
  api: ReturnType<typeof useInstalledPlugins>
}

describe('plugin dependency prompt on enable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'zh-CN'
    mocks.updateConfig.mockResolvedValue({ ok: true })
    mocks.setRunning.mockResolvedValue({ ok: true, enabled: true, status: 'running' })
  })

  it('asks before enabling a pack whose runtime is not installed, and skips when declined', async () => {
    mocks.list.mockResolvedValue({
      plugins: [adventurePack([{ id: 'fu-core', minimum_version: '1.2.0', available: false, satisfied: false }])],
    })
    mocks.confirm.mockResolvedValue(false)
    const wrapper = mountHarness()
    const vm = wrapper.vm as unknown as Harness
    await vm.api.load()
    await flushPromises()

    const pack = vm.api.plugins.value[0]
    await vm.api.toggleEnabled(pack, true)
    await flushPromises()

    expect(mocks.confirm).toHaveBeenCalledTimes(1)
    const content = String(mocks.confirm.mock.calls[0][0]?.content || '')
    // 本地装的包也能看懂缺谁、缺哪个版本。
    expect(content).toContain('fu-core')
    expect(content).toContain('1.2.0')
    // 用户选择先不启用 → 不写 config。
    expect(mocks.updateConfig).not.toHaveBeenCalled()
  })

  it('enables anyway once the user confirms', async () => {
    mocks.list.mockResolvedValue({
      plugins: [adventurePack([{ id: 'fu-core', minimum_version: '1.2.0', available: false, satisfied: false }])],
    })
    mocks.confirm.mockResolvedValue(true)
    const wrapper = mountHarness()
    const vm = wrapper.vm as unknown as Harness
    await vm.api.load()
    await flushPromises()

    const pack = vm.api.plugins.value[0]
    await vm.api.toggleEnabled(pack, true)
    await flushPromises()

    expect(mocks.updateConfig).toHaveBeenCalledWith('fu-adventures', expect.objectContaining({ enabled: true }))
  })

  it('warns when the dependency is installed but not enabled', async () => {
    mocks.list.mockResolvedValue({
      plugins: [
        pluginPayload(),
        adventurePack([{ id: 'fu-core', minimum_version: '1.2.0', available: true, satisfied: true, version: '1.2.0' }]),
      ],
    })
    mocks.confirm.mockResolvedValue(false)
    const wrapper = mountHarness()
    const vm = wrapper.vm as unknown as Harness
    await vm.api.load()
    await flushPromises()

    const pack = vm.api.plugins.value.find(item => item.id === 'fu-adventures')!
    await vm.api.toggleEnabled(pack, true)
    await flushPromises()

    const content = String(mocks.confirm.mock.calls[0][0]?.content || '')
    expect(content).toContain('fu-core')
    expect(mocks.updateConfig).not.toHaveBeenCalled()
  })

  it('stays out of the way when every dependency is live', async () => {
    mocks.list.mockResolvedValue({
      plugins: [
        pluginPayload({ enabled: true, running: true, status: 'running' }),
        adventurePack([{ id: 'fu-core', minimum_version: '1.2.0', available: true, satisfied: true, version: '1.2.0' }]),
      ],
    })
    const wrapper = mountHarness()
    const vm = wrapper.vm as unknown as Harness
    await vm.api.load()
    await flushPromises()

    const pack = vm.api.plugins.value.find(item => item.id === 'fu-adventures')!
    await vm.api.toggleEnabled(pack, true)
    await flushPromises()

    expect(mocks.confirm).not.toHaveBeenCalled()
    expect(mocks.updateConfig).toHaveBeenCalled()
  })
})
