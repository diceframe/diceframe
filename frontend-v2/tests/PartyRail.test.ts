import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import type { GameDetail, Player } from '../src/api/types'
import PartyRail from '../src/features/play/surface/PartyRail.vue'
import PartyMemberMenu from '../src/features/play/surface/PartyMemberMenu.vue'
import { NDropdown } from 'naive-ui'

describe('party rail', () => {
  it('offers member operations only to the GM and forwards their canonical seat id', async () => {
    i18n.global.locale.value = 'zh-CN'
    const players = [{ user_id: 'a', character_name: 'Ari' }, { user_id: 'b', character_name: 'Bea' }] as Player[]
    const wrapper = mount(PartyRail, { props: { detail: { gm_uid: 'a' } as GameDetail, players, isGm: true, currentUserId: 'a' }, global: { plugins: [i18n], stubs: { PortraitImage: true } } })
    const links = wrapper.findAll('[data-testid="party-member-menu"]')
    expect(links).toHaveLength(2)
    expect(links[1]!.attributes('aria-label')).toContain('Bea')
    const dropdown = wrapper.findAllComponents(PartyMemberMenu)[1]!.getComponent(NDropdown)
    for (const key of ['copy-link', 'edit', 'away', 'control-ai', 'kick']) dropdown.vm.$emit('select', key)
    expect(wrapper.emitted('copy-link')).toEqual([['b']])
    expect(wrapper.emitted('edit')).toEqual([['b']])
    expect(wrapper.emitted('set-away')).toEqual([['b', true]])
    expect(wrapper.emitted('set-control')).toEqual([['b', 'ai']])
    expect(wrapper.emitted('kick')).toEqual([['b']])
    await wrapper.setProps({ linkBusyUid: 'b' })
    for (const link of wrapper.findAll('[data-testid="party-member-menu"]')) expect(link.attributes('disabled')).toBeDefined()
    await wrapper.setProps({ isGm: false })
    expect(wrapper.findAll('[data-testid="party-member-menu"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('places direct operations between members and rules and shows each member action state', () => {
    i18n.global.locale.value = 'zh-CN'
    const players = [{ user_id: 'a', character_name: 'Ari' }, { user_id: 'b', character_name: 'Bea' }, { user_id: 'c', character_name: 'Cai' }] as Player[]
    const detail = { solo_mode: false, multiplayer: { submitted_actions: [{ user_id: 'a' }, { user_id: 'b', dice_pending: true }], away_players: [players[2]] } } as unknown as GameDetail
    const wrapper = mount(PartyRail, { props: { detail, players }, slots: { actions: '<div data-testid="party-actions">Operations</div>' }, global: { plugins: [i18n], stubs: { PortraitImage: true } } })
    const children = [...wrapper.element.children]
    const actions = wrapper.get('[data-testid="party-actions"]').element
    expect(children.indexOf(actions)).toBeGreaterThan(children.indexOf(wrapper.get('.party-rail__members').element.parentElement!))
    expect(children.indexOf(actions)).toBeLessThan(children.indexOf(wrapper.get('.party-rail__recap').element))
    const members = wrapper.findAll('.party-rail__member')
    expect(members[0]!.text()).toContain('已行动')
    expect(members[1]!.text()).toContain('待掷骰')
    expect(members[2]!.text()).toContain('暂离')
    wrapper.unmount()
  })

  it('keeps rule identity and every rule hint reachable in a default-closed disclosure', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(PartyRail, {
      props: { detail: {} as GameDetail, players: [], ruleMeta: { rule_name: 'Test rules', rule_version: '1.2', mechanics: 'Resolved on the server', attr_hint: 'Attributes remain authoritative', skill_hint: 'Skills use the selected rules' } },
      global: { plugins: [i18n], stubs: { PortraitImage: true } },
    })
    const rules = wrapper.get('details.party-rail__recap')
    expect(rules.attributes('open')).toBeUndefined()
    expect(rules.get('summary').text()).toBe('规则提示')
    for (const text of ['Test rules', '1.2', 'Resolved on the server', 'Attributes remain authoritative', 'Skills use the selected rules']) expect(rules.text()).toContain(text)
    wrapper.unmount()
  })

  it('explains that a general invite creates a bound character while existing roles use a claim link', () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(PartyRail, {
      props: { detail: {} as GameDetail, players: [] },
      slots: { membersActions: '<button>邀请玩家</button>' },
      global: { plugins: [i18n], stubs: { PortraitImage: true } },
    })
    const invite = wrapper.get('.party-rail__members-actions details')
    expect(invite.attributes('open')).toBeUndefined()
    expect(invite.get('summary').text()).toBe('邀请说明')
    expect(wrapper.get('.party-rail__invite-hint').text()).toContain('自动绑定')
    expect(wrapper.get('.party-rail__invite-hint').text()).toContain('认领链接')
    wrapper.unmount()
  })
})
