import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'

const mocks = vi.hoisted(() => ({ api: vi.fn(), config: vi.fn(), confirm: vi.fn() }))
vi.mock('../src/api/client', () => ({
  api: mocks.api,
  ApiError: class extends Error {},
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('../src/api/peer', () => ({ getRendezvousConfig: mocks.config, createRendezvousRoom: vi.fn() }))
vi.mock('../src/composables/useConfirm', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
vi.mock('../src/composables/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }))

import PeerConnectView from '../src/features/peer/PeerConnectView.vue'
import { usePeerSessionStore } from '../src/peer/store/peerSession'
import { encodePeerInvites } from '../src/features/peer/inviteCode'

function peerCode() {
  return encodePeerInvites({
    ok: true, protocol_version: 2, topology: 'host-star', room_code: 'FRIENDS1',
    host_peer_id: 'h_abcdefghijk', host_token: 'host-only',
    invitations: [{ peer_id: 'p_abcdefghijk', token: 'guest-invitation-with-at-least-thirty-two-characters' }],
    expires_at: '2099-01-01T00:00:00Z', websocket_url: 'wss://hub.example.invalid/rooms/FRIENDS1/ws',
  }, [], 'web|friends|gm', [{ actorId: 'nightingale', actorName: '夜莺' }])[0]!.inviteCode
}

async function render(code = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = usePeerSessionStore()
  const start = vi.spyOn(session, 'startMulti').mockImplementation(() => {})
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { name: 'overview', path: '/', component: { template: '<div />' } },
    { name: 'legal-privacy', path: '/privacy', component: { template: '<div />' } },
  ] })
  const wrapper = mount(PeerConnectView, {
    props: { embedded: true, initialMode: 'guest', initialInvite: code },
    global: { plugins: [pinia, i18n, router], stubs: { NIcon: true } },
  })
  await flushPromises()
  return { wrapper, start }
}

describe('direct-connect invitation entry', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
    mocks.api.mockReset().mockResolvedValue({ games: [] })
    mocks.config.mockReset().mockResolvedValue({ max_peers_per_room: 6 })
  })

  it('opens ordinary invitations pasted into the legacy direct-connect form', async () => {
    const { wrapper, start } = await render()
    const link = 'https://friends.example/sub/#/join?game=room&share=1&seat=original-seat'
    await wrapper.get('textarea').setValue(link)
    expect(wrapper.get('[data-testid="peer-open-invitation"]').attributes('href')).toBe(link)
    expect(wrapper.find('.peer-direct-consent').exists()).toBe(false)
    expect(wrapper.find('.peer-primary').exists()).toBe(false)
    expect(start).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('prefills a forwarded code and preserves consent and assigned-actor requirements', async () => {
    const code = peerCode()
    const { wrapper, start } = await render(code)
    expect(wrapper.get('textarea').element.value).toBe(code)
    expect(wrapper.get('.peer-invite-preview').text()).toContain('夜莺')
    expect(start).not.toHaveBeenCalled()
    expect(wrapper.get('.peer-primary').attributes('disabled')).toBeDefined()
    await wrapper.get('.peer-direct-consent input').setValue(true)
    await wrapper.get('.peer-primary').trigger('click')
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ isHost: false, assignedActorId: 'nightingale' }))
    wrapper.unmount()
  })

  it('reports a damaged code and disables connection before any session is started', async () => {
    const { wrapper, start } = await render('DFP2-broken')
    expect(wrapper.get('[role="alert"]').text()).toContain('损坏')
    await wrapper.get('.peer-direct-consent input').setValue(true)
    expect(wrapper.get('.peer-primary').attributes('disabled')).toBeDefined()
    expect(start).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
