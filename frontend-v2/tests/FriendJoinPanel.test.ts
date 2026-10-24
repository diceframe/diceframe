import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import FriendJoinPanel from '../src/features/peer/FriendJoinPanel.vue'
import { encodePeerInvites } from '../src/features/peer/inviteCode'

function render(peerAvailable = false) {
  return mount(FriendJoinPanel, { props: { peerAvailable }, global: { plugins: [i18n] } })
}

function peerCode() {
  return encodePeerInvites({
    ok: true, protocol_version: 2, topology: 'host-star', room_code: 'FRIENDS1',
    host_peer_id: 'h_abcdefghijk', host_token: 'host-only',
    invitations: [{ peer_id: 'p_abcdefghijk', token: 'guest-invitation-with-at-least-thirty-two-characters' }],
    expires_at: '2099-01-01T00:00:00Z', websocket_url: 'wss://hub.example.invalid/rooms/FRIENDS1/ws',
  }, [], 'web|friends|gm')[0]!.inviteCode
}

describe('FriendJoinPanel', () => {
  beforeEach(() => { i18n.global.locale.value = 'zh-CN' })

  it('opens with a neutral invitation input and no direct-connect setup', () => {
    const wrapper = render()
    expect(wrapper.get('h2').text()).toBe('加入好友')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="friend-join-host"]').exists()).toBe(false)
  })

  it('offers the original invitation even without direct connect and hides its credentials from the preview', async () => {
    const wrapper = render()
    const link = 'https://friends.example/subpath/#/join?game=room&share=1&seat=seat-credential&server=https%3A%2F%2Fbackend.example'
    await wrapper.get('textarea').setValue(link)
    const open = wrapper.get('[data-testid="friend-join-open-link"]')
    expect(open.attributes('href')).toBe(link)
    expect(open.attributes('target')).toBe('_blank')
    expect(open.attributes('rel')).toContain('noopener')
    expect(open.attributes('rel')).toContain('noreferrer')
    expect(wrapper.get('[role="status"]').text()).toContain('https://friends.example')
    expect(wrapper.get('[role="status"]').text()).not.toContain('seat-credential')
    expect(wrapper.emitted('peer')).toBeUndefined()
  })

  it('only advances a valid direct code after the player chooses to continue', async () => {
    const wrapper = render(true)
    const code = peerCode()
    await wrapper.get('textarea').setValue(` ${code} `)
    expect(wrapper.emitted('peer')).toBeUndefined()
    expect(wrapper.get('[role="status"]').text()).toContain('已识别直连码')
    await wrapper.get('[data-testid="friend-join-peer"]').trigger('click')
    expect(wrapper.emitted('peer')).toEqual([[code]])
    expect(wrapper.find('[data-testid="friend-join-open-link"]').exists()).toBe(false)
  })

  it('explains when direct connect is unavailable while allowing invitation URLs', async () => {
    const wrapper = render()
    await wrapper.get('textarea').setValue(peerCode())
    expect(wrapper.text()).toContain('当前服务未开放直连')
    expect(wrapper.find('[data-testid="friend-join-peer"]').exists()).toBe(false)
    await wrapper.get('textarea').setValue('https://friends.example/#/join?game=room&share=1')
    expect(wrapper.find('[data-testid="friend-join-open-link"]').exists()).toBe(true)
  })

  it('explains a missing game and clears the error when a complete invitation is pasted', async () => {
    const wrapper = render()
    await wrapper.get('textarea').setValue('https://friends.example/#/join?share=1')
    expect(wrapper.get('[role="alert"]').text()).toContain('缺少游戏信息')
    await wrapper.get('textarea').setValue('https://friends.example/#/join?game=room&share=1')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('keeps hosting behind the explicit alternate-connection action', async () => {
    const wrapper = render(true)
    const more = wrapper.get('details')
    expect(more.attributes('open')).toBeUndefined()
    expect(more.get('summary').text()).toBe('更多连接方式')
    await wrapper.get('[data-testid="friend-join-host"]').trigger('click')
    expect(wrapper.emitted('host')).toHaveLength(1)
  })
})
