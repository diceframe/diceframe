import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({
  networkAddresses: vi.fn(),
  copyToClipboard: vi.fn(),
  publicBaseUrl: '',
  backendUrl: '',
  origin: 'https://table.example',
}))

vi.mock('../src/api/pairing', () => ({
  pairingApi: { networkAddresses: mocks.networkAddresses },
}))
vi.mock('../src/utils/clipboard', () => ({
  copyToClipboard: mocks.copyToClipboard,
}))
vi.mock('../src/api/connection', async (original) => ({
  ...(await original<typeof import('../src/api/connection')>()),
  currentBackendUrl: () => mocks.backendUrl,
}))
// jsdom 的 window.location 不可重定义；前端 origin 从 shareLink 统一读，这里换掉它。
vi.mock('../src/utils/shareLink', async (original) => ({
  ...(await original<typeof import('../src/utils/shareLink')>()),
  currentOrigin: () => mocks.origin,
}))
vi.mock('../src/stores/useSettingsStore', () => ({
  useSettingsStore: () => ({
    config: { public_base_url: mocks.publicBaseUrl },
    loading: false,
    load: vi.fn(),
  }),
}))
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}))
// 弹窗本体不依赖 NaiveUI 的 MessageProvider；这里只要一个不抛错的 toast。
vi.mock('@/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

import InviteQrModal from '../src/features/play/InviteQrModal.vue'

function mountModal(seatToken?: string) {
  return mount(InviteQrModal, {
    // 二维码本身与本文件无关（qrcode-generator 在 jsdom 下不可用），stub 掉。
    global: { plugins: [i18n], stubs: { Teleport: true, QrCode: true } },
    props: { gameKey: 'web|room|bot', seatToken, title: '邀请链接' },
  })
}

async function render(seatToken?: string) {
  const wrapper = mountModal(seatToken)
  await flushPromises()
  return wrapper
}

/** 手动控制 /api/system/network 何时返回，用来卡住「还在解析地址」那一瞬间。 */
function deferredAddresses() {
  let settle!: (addresses: { host: string; url: string }[]) => void
  let fail!: (error: Error) => void
  const promise = new Promise<{ addresses: { host: string; url: string }[] }>(
    (resolve, reject) => {
      settle = (addresses) => resolve({ addresses })
      fail = reject
    },
  )
  mocks.networkAddresses.mockReturnValue(promise)
  return { settle, fail }
}

function setOrigin(origin: string) {
  mocks.origin = origin
}

describe('invite address guidance', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    push.mockClear()
    mocks.publicBaseUrl = ''
    mocks.backendUrl = ''
    mocks.networkAddresses.mockReset().mockResolvedValue({ addresses: [] })
    mocks.copyToClipboard.mockReset().mockResolvedValue(undefined)
    setOrigin('https://table.example')
  })

  it('explains that friends must be able to reach the address', async () => {
    const wrapper = await render()

    const hint = wrapper.get('.invite-address-hint')
    // 局域网 / 异地公网或内网穿透 / 分享地址 三个语义都要在。
    expect(hint.text()).toContain('局域网')
    expect(hint.text()).toContain('内网穿透')
    expect(hint.text()).toContain('访问')
  })

  it('links to the existing sharing-address settings', async () => {
    const wrapper = await render()

    await wrapper.get('.invite-address-settings').trigger('click')

    expect(push).toHaveBeenCalledWith({ name: 'settings' })
    // 跳转前先关掉弹窗，避免设置页被盖住。
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('provides a locale-aware multiplayer help link without exposing invitation data', async () => {
    const wrapper = await render('seat-token-abc')
    const help = wrapper.get('.invite-multiplayer-help')

    expect(help.element.tagName).toBe('A')
    expect(help.attributes('href')).toBe(
      'https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md#外网联机',
    )
    expect(help.attributes('target')).toBe('_blank')
    expect(help.attributes('rel')).toBe('noopener noreferrer')
    expect(help.attributes('href')).not.toContain('seat-token-abc')
    await help.trigger('click')
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(push).not.toHaveBeenCalled()

    i18n.global.locale.value = 'en'
    const english = await render('seat-token-abc')
    const englishHelp = english.get('.invite-multiplayer-help')
    expect(englishHelp.attributes('href')).toBe(
      'https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md#multiplayer',
    )
    expect(englishHelp.attributes('href')).not.toContain('seat-token-abc')
  })

  it.each(['en', 'ja', 'de', 'ru'] as const)(
    'uses the English guide for the %s locale without credentials',
    async (locale) => {
      i18n.global.locale.value = locale
      const wrapper = await render('seat-token-abc')
      const help = wrapper.get('[data-testid="invite-multiplayer-help"]')

      expect(help.attributes('href')).toBe(
        'https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md#multiplayer',
      )
      expect(help.attributes('href')).not.toMatch(/seat-token-abc|game=|token|key/i)
    },
  )

  it('keeps the multiplayer help link available while the address is loading', async () => {
    const pending = deferredAddresses()
    const wrapper = mountModal('seat-token-abc')
    await nextTick()

    const help = wrapper.get('.invite-multiplayer-help')
    expect(help.attributes('target')).toBe('_blank')
    expect(help.attributes('href')).not.toContain('seat-token-abc')
    expect(wrapper.emitted('close')).toBeUndefined()

    pending.settle([])
    await flushPromises()
  })

  it('warns only when the selected address is reachable from this machine alone', async () => {
    // 探测不到别的地址时，本机 origin 就是唯一候选。
    setOrigin('http://localhost:8080')
    expect((await render()).find('.invite-address-local').exists()).toBe(true)

    setOrigin('http://127.0.0.1:8080')
    expect((await render()).find('.invite-address-local').exists()).toBe(true)

    // 局域网地址是合法用法，不该被警告。
    setOrigin('http://192.168.1.20:8080')
    expect((await render()).find('.invite-address-local').exists()).toBe(false)

    setOrigin('https://table.example')
    expect((await render()).find('.invite-address-local').exists()).toBe(false)
  })

  it('prefers a reachable LAN address over the local-only origin', async () => {
    // 服务端按「局域网优先、回环垫底」给出 host。
    mocks.networkAddresses.mockResolvedValue({
      addresses: [
        { host: '192.168.1.20', url: 'http://192.168.1.20:8000' },
        { host: '127.0.0.1', url: 'http://127.0.0.1:8000' },
      ],
    })
    setOrigin('http://localhost:5173')

    const wrapper = await render()

    // 端口沿用浏览器打开前端用的那个，只换 host——独立前端与后端不同端口。
    expect(wrapper.get('.invite-link').text()).toContain('http://192.168.1.20:5173/')
    expect(wrapper.find('.invite-address-local').exists()).toBe(false)
  })

  it('keeps an explicitly configured sharing address first', async () => {
    mocks.publicBaseUrl = 'https://trpg.example.com'
    mocks.networkAddresses.mockResolvedValue({
      addresses: [{ host: '192.168.1.20', url: 'http://192.168.1.20:8000' }],
    })
    setOrigin('http://localhost:5173')

    const wrapper = await render()

    expect(wrapper.get('.invite-link').text()).toContain('https://trpg.example.com/')
  })

  it('moves the backend server param onto the same host as the join address', async () => {
    // 独立前端部署：玩家的设备既要打得开前端，也要连得上后端。
    mocks.backendUrl = 'http://localhost:10022'
    mocks.networkAddresses.mockResolvedValue({
      addresses: [{ host: '192.168.1.20', url: 'http://192.168.1.20:10022' }],
    })
    setOrigin('http://localhost:5173')

    const link = (await render()).get('.invite-link').text()

    expect(link).toContain('http://192.168.1.20:5173/')
    expect(decodeURIComponent(link)).toContain('server=http://192.168.1.20:10022')
  })

  it('carries the seat token, never the player id, for a takeover link', async () => {
    const link = (await render('seat-token-abc')).get('.invite-link').text()

    expect(decodeURIComponent(link)).toContain('#/join?')
    expect(decodeURIComponent(link)).toContain('seat=seat-token-abc')
    expect(decodeURIComponent(link)).not.toContain('user=')
  })

  it('never blocks copying the link', async () => {
    setOrigin('http://localhost:8080')
    const wrapper = await render()

    // 即使出现"仅本机可访问"的提示，复制按钮仍在，且没有任何强制确认。
    const copyButton = wrapper.get('.actions .primary')
    expect(copyButton.text()).toBe('复制链接')
    expect(copyButton.attributes('disabled')).toBeUndefined()
  })

  it('falls back to this machine when the candidate list is refused', async () => {
    // /api/system/network 仅对 owner 会话开放；403 不该让二维码出不来。
    mocks.networkAddresses.mockRejectedValue(new Error('需要管理员会话'))
    setOrigin('http://localhost:5173')

    const wrapper = await render()

    expect(wrapper.get('.invite-link').text()).toContain('http://localhost:5173/')
  })
})

/**
 * 地址解析是异步的：`/api/system/network` 回来之前 selectedBase 还是空串，
 * buildJoinLink 只能退回当前浏览器 origin。GM 在本机开发时那就是 localhost——
 * 二维码已经等到解析完才出，复制按钮必须跟着等，否则那一两秒里点一下就把
 * localhost 链接发给了朋友，而界面稍后才悄悄换成 192.168.x.x。
 */
describe('invite link copy waits for the address to resolve', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    push.mockClear()
    mocks.publicBaseUrl = ''
    mocks.backendUrl = ''
    mocks.networkAddresses.mockReset().mockResolvedValue({ addresses: [] })
    mocks.copyToClipboard.mockReset().mockResolvedValue(undefined)
    setOrigin('https://table.example')
  })

  it('Case A — cannot copy while the candidate list is still in flight', async () => {
    const pending = deferredAddresses()
    setOrigin('http://localhost:5173')

    const wrapper = mountModal()
    await nextTick()

    // 还在解析：占位文案在，二维码和链接原文都还没出。
    expect(wrapper.get('.qr-stage-placeholder').text()).toBe('正在获取可用地址…')
    expect(wrapper.find('.invite-link').exists()).toBe(false)

    const copyButton = wrapper.get('.actions .primary')
    expect(copyButton.attributes('disabled')).toBeDefined()

    // 即使绕过 disabled 直接触发，也不该把半成品链接写进剪贴板。
    await copyButton.trigger('click')
    expect(mocks.copyToClipboard).not.toHaveBeenCalled()

    pending.settle([])
    await flushPromises()
  })

  it('Case B — copies the LAN link, not the localhost one, once resolved', async () => {
    const pending = deferredAddresses()
    setOrigin('http://localhost:5173')

    const wrapper = mountModal()
    await nextTick()
    expect(wrapper.get('.actions .primary').attributes('disabled')).toBeDefined()

    pending.settle([{ host: '192.168.1.20', url: 'http://192.168.1.20:8000' }])
    await flushPromises()

    const copyButton = wrapper.get('.actions .primary')
    expect(copyButton.attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.invite-link').text()).toContain('http://192.168.1.20:5173/')

    await copyButton.trigger('click')

    const copied = mocks.copyToClipboard.mock.calls[0][0] as string
    expect(copied).toContain('http://192.168.1.20:5173/')
    expect(copied).not.toContain('localhost')
    // 复制的就是屏幕上那条：显示内容、复制内容、选中地址三者一致。
    expect(copied).toBe(wrapper.get('.invite-link').text())
  })

  it('Case C — a refused candidate list still ends up copyable via the fallback', async () => {
    // 候选接口不可用 ≠ 二维码功能不可用：退回当前 origin 后照样要能复制。
    const pending = deferredAddresses()
    setOrigin('http://localhost:5173')

    const wrapper = mountModal()
    await nextTick()
    expect(wrapper.get('.actions .primary').attributes('disabled')).toBeDefined()

    pending.fail(new Error('需要管理员会话'))
    await flushPromises()

    const copyButton = wrapper.get('.actions .primary')
    expect(copyButton.attributes('disabled')).toBeUndefined()

    await copyButton.trigger('click')

    expect(mocks.copyToClipboard).toHaveBeenCalledTimes(1)
    expect(mocks.copyToClipboard.mock.calls[0][0]).toContain('http://localhost:5173/')
  })
})
