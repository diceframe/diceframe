import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import type { Mock } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h, nextTick, onMounted } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import type { GameDetail, Player } from '../src/api/types'
import { gameState } from './helpers/playViewGame'

const GAME_KEY = 'web|combat|bot'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  fetchRulesetAvailableActions: vi.fn(),
  resolveGameSceneImageUrl: vi.fn(),
}))

vi.mock('../src/composables/useGame', async () => {
  const helper = await import('./helpers/playViewGame')
  return { useGame: helper.useGame }
})
vi.mock('../src/api/client', () => ({
  api: mocks.api,
  apiBlob: vi.fn(),
  hasAccessToken: () => false,
  isNotFoundError: (error: unknown) => Boolean((error as { status?: number })?.status === 404),
  retryOnRateLimit: <T>(request: () => Promise<T>) => request(),
}))
vi.mock('../src/api/rulesets', () => ({
  fetchRulesetAvailableActions: mocks.fetchRulesetAvailableActions,
}))
vi.mock('../src/api/sceneImages', () => ({
  fileToBase64: vi.fn(),
  resolveGameSceneImageUrl: mocks.resolveGameSceneImageUrl,
  revokeSceneImageUrl: vi.fn(),
  sceneImageStyle: () => ({}),
}))
vi.mock('../src/api/generatedImages', () => ({ generateCurrentRoundImage: vi.fn() }))
vi.mock('../src/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}))
vi.mock('../src/stores/useSettingsStore', () => ({
  useSettingsStore: () => ({
    config: { public_base_url: '', imagegen_manual_scene: false, imagegen_auto_storyboard: false },
    loading: false,
    load: vi.fn(async () => undefined),
  }),
}))

import PlayView from '../src/features/play/PlayView.vue'

const STUBS = {
  Teleport: true,
  GameSidebar: true,
  KpQuestionDialog: true,
  MapBackgroundSettingsModal: true,
  RulesetPlayHost: true,
  DirectorProposalCard: true,
  GameTimeline: true,
  EconomyProposalCard: true,
  TableTalkFeed: true,
  GmToolbar: true,
  MultiplayerPanel: true,
  HealthPanel: true,
  ManualRollsPanel: true,
  MapWorkspace: true,
  SceneGalleryModal: true,
  CurrentRoundImageModal: true,
  PlayHelpCenter: true,
  InviteQrModal: true,
  GmChargeComposer: true,
  PortraitPicker: true,
  AdventureSceneImagePicker: true,
  RulesetCharacterCenterHost: true,
}

function combatExtensionDetail(overrides: Record<string, unknown> = {}): GameDetail {
  return {
    game_key: GAME_KEY,
    state: 'active_action',
    solo_mode: true,
    world_name: '测试世界',
    combat_extension: {
      scheduler: {
        kind: 'threshold',
        ready: ['player:ally'],
        gauges: { 'player:ally': 80, 'npc:boss': 100 },
      },
      entities: ['player:ally', 'npc:boss'],
      entity_names: { 'player:ally': '阿刃', 'npc:boss': '黑骑士' },
      pools: { 'player:ally': { hp: { current: 12, maximum: 12 } } },
      actions: [{ id: 'ability:qi_palm', kind: 'ability', name: '内力掌', costs: [] }],
    },
    ...overrides,
  } as unknown as GameDetail
}

async function mountPlayView(query: Record<string, string> = { game: GAME_KEY, user: 'ally' }, extraStubs: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' } },
      { path: '/join', name: 'join', component: { template: '<div />' } },
      { path: '/characters', name: 'characters', component: { template: '<div />' } },
      { path: '/play', name: 'play', component: PlayView },
    ],
  })
  await router.push({ name: 'play', query })
  await router.isReady()

  const wrapper = mount(PlayView, {
    global: { plugins: [i18n, router], stubs: { ...STUBS, ...extraStubs } as typeof STUBS },
  })
  await flushPromises()
  return wrapper
}

describe('combat actions entry near the action composer', () => {
  const mockedApi = mocks.api as unknown as Mock
  const mockedAvailableActions = mocks.fetchRulesetAvailableActions as unknown as Mock

  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    gameState.detail.value = combatExtensionDetail()
    gameState.players.value = [{ user_id: 'ally', character_name: '阿刃' } as Player]
    gameState.isGm.value = false
    gameState.userId.value = 'ally'
    mockedApi.mockReset().mockResolvedValue({})
    mockedAvailableActions.mockReset().mockResolvedValue({})
    mocks.resolveGameSceneImageUrl.mockReset().mockResolvedValue('')
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('does not reopen GM tools on entry because another visit left them open', async () => {
    localStorage.setItem('play_rail_collapsed', '0')
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const first = await mountPlayView({ game: GAME_KEY })
    expect(first.get('[data-testid="play-gm-toggle"]').attributes('aria-expanded')).toBe('false')
    await first.get('[data-testid="play-gm-toggle"]').trigger('click')
    expect(first.get('[data-testid="play-gm-toggle"]').attributes('aria-expanded')).toBe('true')
    first.unmount()
    const next = await mountPlayView({ game: GAME_KEY })
    expect(next.get('[data-testid="play-gm-toggle"]').attributes('aria-expanded')).toBe('false')
    next.unmount()
    localStorage.removeItem('play_rail_collapsed')
  })

  it('keeps character information ahead of the on-demand GM tools without remounting them', async () => {
    localStorage.removeItem('play_rail_collapsed')
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const wrapper = await mountPlayView()
    expect(wrapper.get('[data-testid="play-character-dock"]').attributes('aria-hidden')).toBe('false')
    expect(wrapper.get('[data-testid="play-gm-drawer"]').classes()).toContain('collapsed')
    const toolbar = wrapper.findComponent({ name: 'GmToolbar' }).element
    await wrapper.get('[data-testid="play-gm-toggle"]').trigger('click')
    expect(wrapper.get('[data-testid="play-gm-drawer"]').classes()).not.toContain('collapsed')
    await wrapper.get('[data-testid="play-gm-toggle"]').trigger('click')
    expect(wrapper.findComponent({ name: 'GmToolbar' }).element).toBe(toolbar)
    expect(wrapper.get('[data-testid="play-gm-drawer"]').classes()).toContain('collapsed')
    wrapper.unmount()
  })

  it('puts the GM invite action directly below the party member list', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const InviteModal = defineComponent({
      name: 'InviteQrModal',
      props: { title: { type: String, default: '' }, hint: { type: String, default: '' } },
      template: '<div data-testid="invite-qr-modal"><span data-testid="invite-title">{{ title }}</span><span data-testid="invite-hint">{{ hint }}</span></div>',
    })
    const wrapper = await mountPlayView({ game: GAME_KEY }, { InviteQrModal: InviteModal })
    const members = wrapper.get('.party-rail__members').element
    const actions = wrapper.get('.party-rail__members-actions').element
    expect(actions.parentElement).toBe(members.parentElement)
    expect(actions.previousElementSibling).toBe(members)
    const invite = wrapper.get('[data-testid="play-invite-player"]')
    expect(invite.text()).toContain('邀请好友')
    await invite.trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="invite-title"]').text()).toBe('邀请链接')
    expect(wrapper.get('[data-testid="invite-hint"]').text()).toBe(String(i18n.global.t('inviteNewCharacterQrHint')))
    wrapper.unmount()
  })

  it('keeps the GM workbench open when interacting outside it and closes explicitly', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const wrapper = await mountPlayView({ game: GAME_KEY })
    document.body.appendChild(wrapper.element)
    const toggle = wrapper.get('[data-testid="play-gm-toggle"]')
    const drawer = wrapper.get('[data-testid="play-gm-drawer"]')
    await toggle.trigger('click')
    await wrapper.get('.play-main').trigger('pointerdown')
    expect(drawer.attributes('aria-hidden')).toBe('false')
    await drawer.get('.rail-toggle').trigger('click')
    expect(drawer.attributes('aria-hidden')).toBe('true')
    await toggle.trigger('click')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(drawer.attributes('aria-hidden')).toBe('true')
    wrapper.unmount()
  })

  it('opens a separate GM-only room password dialog beside the invite action', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false, has_room_password: true })
    const PasswordDialog = defineComponent({
      name: 'RoomPasswordDialog', props: ['gameKey', 'hasPassword'], emits: ['close', 'changed'],
      template: '<div data-testid="room-password-dialog-stub" />',
    })
    const wrapper = await mountPlayView({ game: GAME_KEY }, { RoomPasswordDialog: PasswordDialog })
    expect(wrapper.findComponent(PasswordDialog).exists()).toBe(false)
    const entry = wrapper.get('.party-rail__members-actions [data-testid="play-room-password"]')
    expect(entry.text()).toContain('房间密码')
    await entry.trigger('click')
    const dialog = wrapper.getComponent(PasswordDialog)
    expect(dialog.props()).toMatchObject({ gameKey: GAME_KEY, hasPassword: true })
    dialog.vm.$emit('close')
    await nextTick()
    expect(wrapper.findComponent(PasswordDialog).exists()).toBe(false)
    gameState.isGm.value = false
    await nextTick()
    expect(wrapper.find('[data-testid="play-room-password"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps password management out of the general game settings form', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false, has_room_password: true })
    const wrapper = await mountPlayView({ game: GAME_KEY }, { GmToolbar: false })
    const settingsButton = wrapper.findAll('button').find(button => button.text() === '对局设置')!
    expect(settingsButton).toBeDefined()
    await settingsButton.trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.find('input[type="password"]').exists()).toBe(false)
    expect(dialog.text()).toContain('奖励')
    expect(dialog.text()).toContain('暂离')
    await dialog.findAll('select')[1]!.setValue('ai_takeover')
    await dialog.findAll('button').find(button => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(mockedApi.mock.calls.some(([path]) => String(path).endsWith('/settings/away-control-policy'))).toBe(true)
    expect(mockedApi.mock.calls.some(([path]) => String(path).endsWith('/room-password'))).toBe(false)
    wrapper.unmount()
  })

  it('places one roll controller in the party rail without a hidden player collaboration drawer', async () => {
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const wrapper = await mountPlayView()
    expect(wrapper.find('[data-testid="play-gm-toggle"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="play-gm-drawer"]').exists()).toBe(false)
    await wrapper.get('[data-testid="play-tools-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="play-collaboration-toggle"]').exists()).toBe(false)
    const rolls = wrapper.findAllComponents({ name: 'ManualRollsPanel' })
    expect(rolls).toHaveLength(1)
    expect(rolls[0]!.element.closest('.party-rail')).not.toBeNull()
    wrapper.unmount()
  })

  it('keeps one check controller in the scene and opens it from the grouped GM task list', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const opened = vi.fn()
    const CheckTool = defineComponent({
      name: 'CheckModePanel', emits: ['status'],
      setup(_, { emit, expose }) {
        expose({ open: opened })
        onMounted(() => emit('status', { pendingCount: 2, error: '' }))
        return {}
      },
      template: '<button data-testid="check-controller">check</button>',
    })
    const wrapper = await mountPlayView({ game: GAME_KEY }, {
      GmToolbar: false, AISeatsPanel: true, CheckModePanel: CheckTool,
    })
    expect(wrapper.findAll('[data-testid="check-controller"]')).toHaveLength(1)
    expect(wrapper.find('.scene-strip [data-testid="check-controller"]').exists()).toBe(true)
    expect(wrapper.get('[role="tab"]').text()).toContain('2')
    await wrapper.get('[data-testid="gm-open-check-requests"]').trigger('click')
    expect(opened).toHaveBeenCalledTimes(1)
    const tabs = wrapper.findAll('[role="tab"]')
    await tabs[1]!.trigger('click')
    expect(wrapper.find('[data-section="party"] multiplayer-panel-stub').exists()).toBe(true)
    expect(wrapper.find('[data-section="party"] a-i-seats-panel-stub').exists()).toBe(true)
    await tabs[3]!.trigger('click')
    expect(wrapper.find('[data-section="rules"] manual-rolls-panel-stub').exists()).toBe(false)
    expect(wrapper.findAll('.party-rail manual-rolls-panel-stub')).toHaveLength(1)
    expect(wrapper.findAll('[data-testid="check-controller"]')).toHaveLength(1)
    wrapper.unmount()
  })

  it('collects AI seat and action recovery attention in the GM task list', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false, round_number: 7 })
    const focusRecovery = vi.fn()
    const SeatTool = defineComponent({
      name: 'AISeatsPanel', props: ['round'], emits: ['attention'],
      setup(_, { emit }) { onMounted(() => emit('attention', 2)); return {} },
      template: '<span data-testid="seat-tool">{{ round }}</span>',
    })
    const Composer = defineComponent({
      name: 'ActionComposer', emits: ['recovery-status'],
      setup(_, { emit, expose }) {
        expose({ focusRecovery })
        onMounted(() => emit('recovery-status', { count: 1, message: 'Failed action' }))
        return {}
      }, template: '<div />',
    })
    const wrapper = await mountPlayView({ game: GAME_KEY }, {
      GmToolbar: false, AISeatsPanel: SeatTool, ActionComposer: Composer,
    })
    expect(wrapper.get('[role="tab"]').text()).toContain('3')
    expect(wrapper.get('[data-testid="seat-tool"]').text()).toBe('7')
    await wrapper.get('[data-testid="gm-open-ai-attention"]').trigger('click')
    expect(wrapper.findAll('[role="tab"]')[1]!.attributes('aria-selected')).toBe('true')
    await wrapper.findAll('[role="tab"]')[0]!.trigger('click')
    await wrapper.get('[data-testid="gm-open-action-recovery"]').trigger('click')
    expect(focusRecovery).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-testid="play-gm-drawer"]').attributes('aria-hidden')).toBe('true')
    wrapper.unmount()
  })

  it('opens workbench todo only for the current game after a game switch', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })
    const openSection = vi.fn()
    const Toolbar = defineComponent({
      setup(_, { expose }) { expose({ openSection }); return () => h('div', { 'data-testid': 'toolbar-stub' }) },
    })
    const wrapper = await mountPlayView({ game: GAME_KEY, workbench: '1' }, { GmToolbar: Toolbar })
    expect(openSection).toHaveBeenCalledWith('todo')
    openSection.mockClear()
    gameState.currentGame.value = 'web|new|bot'
    gameState.detail.value = combatExtensionDetail({ game_key: 'web|new|bot', solo_mode: false })
    await flushPromises()
    expect(openSection).toHaveBeenCalledWith('todo')
    wrapper.unmount()
  })

  it('focuses the mounted economy proposal when its GM todo is reopened', async () => {
    gameState.isGm.value = true
    gameState.userId.value = 'ally'
    gameState.detail.value = combatExtensionDetail({
      solo_mode: false,
      gm_uid: 'ally',
      economy_proposals: [{ id: 'pay-1', status: 'pending', kind: 'purchase', approval_policy: 'gm', payer_uid: 'ally', amount: 25 }],
    })
    const focus = vi.fn()
    const EconomyCard = defineComponent({
      setup(_, { expose }) {
        expose({ focus })
        return () => h('article', { 'data-testid': 'economy-card' })
      },
    })
    const wrapper = await mountPlayView({}, { GmToolbar: false, EconomyProposalCard: EconomyCard })
    const task = wrapper.findAll('.gm-task-link').find(button => button.text().includes('经济'))
    expect(task).toBeTruthy()
    await task!.trigger('click')
    await flushPromises()
    expect(focus).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not auto-collapse the player sidebar when the play container is narrow', async () => {
    let resize: ((entries: Array<{ contentRect: { width: number } }>) => void) | undefined
    class ResizeObserverMock {
      constructor(callback: typeof resize) { resize = callback }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    localStorage.removeItem('play_sidebar_collapsed')
    const wrapper = await mountPlayView()
    resize?.([{ contentRect: { width: 900 } }])
    await nextTick()
    expect(wrapper.get('.play-layout').classes()).not.toContain('collapsed')
    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it('Case A: the ActionComposer tools bar shows 战斗动作 even without ruleset tools', async () => {
    // 自由规则只开启 combat_extension：没有专业工具、也不是 KP（不在名册里），
    // tools 栏仍必须因为 hasCombatExtension 出现。
    gameState.userId.value = ''
    gameState.players.value = []

    const wrapper = await mountPlayView()

    const tools = wrapper.get('.ruleset-context-tools')
    const trigger = tools.get('[data-testid="combat-extension-tool"]')
    expect(trigger.text()).toContain('战斗动作')
    // 名册为空 → 没有 KP 提问按钮；这条 tools 栏是 combat extension 单独撑起来的。
    expect(tools.find('.kp-question-tool-trigger').exists()).toBe(false)
    expect(wrapper.find('.composer').exists()).toBe(true)
    wrapper.unmount()
  })

  it('Case B: no combat_extension means no tools bar and no entry', async () => {
    gameState.userId.value = ''
    gameState.players.value = []
    gameState.detail.value = { game_key: GAME_KEY, state: 'active_action', solo_mode: true }

    const wrapper = await mountPlayView()

    expect(wrapper.find('[data-testid="combat-extension-tool"]').exists()).toBe(false)
    expect(wrapper.find('.ruleset-context-tools').exists()).toBe(false)
    wrapper.unmount()
  })

  it('Case C+D: a player opens the panel through the Modal and sees no GM controls', async () => {
    const wrapper = await mountPlayView()

    expect(wrapper.find('.combat-ext').exists()).toBe(false)
    await wrapper.get('[data-testid="combat-extension-tool"]').trigger('click')
    await flushPromises()

    // 真正挂载了 CombatExtensionPanel：行动按钮与池展示来自该组件。
    expect(wrapper.find('.combat-ext').exists()).toBe(true)
    expect(wrapper.get('.combat-ext').text()).toContain('内力掌')

    // 普通玩家：只看到自己的池，没有 actor selector，也没有推进调度器。
    expect(wrapper.get('.combat-ext').text()).toContain('12 / 12')
    expect(wrapper.find('.combat-ext-scheduler-head button').exists()).toBe(false)
    expect(wrapper.get('.combat-ext').text()).not.toContain('行动实体')
    // 目标选择器有且只有行动本身那一个（GM 会多出 actor selector）。
    expect(wrapper.findAll('.combat-ext-action select')).toHaveLength(1)
    wrapper.unmount()
  })

  it('Case E: a GM additionally sees the actor selector and the scheduler advance', async () => {
    gameState.isGm.value = true
    const wrapper = await mountPlayView()

    await wrapper.get('[data-testid="combat-extension-tool"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.combat-ext').text()).toContain('行动实体')
    const advance = wrapper.get('.combat-ext-scheduler-head button')
    expect(advance.text()).toBe('推进时间')
    // actor selector + 行动目标 selector
    expect(wrapper.findAll('.combat-ext-action select')).toHaveLength(2)

    await advance.trigger('click')
    await flushPromises()
    expect(String(mockedApi.mock.calls.at(-1)?.[0])).toContain('/combat/scheduler/advance')
    wrapper.unmount()
  })

  it('Case F: the controls rail no longer renders CombatExtensionPanel persistently', async () => {
    gameState.isGm.value = true
    gameState.detail.value = combatExtensionDetail({ solo_mode: false })

    const wrapper = await mountPlayView()

    // GM controls rail 存在，但里面没有常驻的战斗扩展面板；面板只在入口打开后出现。
    expect(wrapper.find('.play-control-rail').exists()).toBe(true)
    expect(wrapper.find('.play-control-rail .combat-ext').exists()).toBe(false)
    expect(wrapper.findAll('.combat-ext')).toHaveLength(0)

    // 源码契约：右侧 controls rail 里不再直接渲染 CombatExtensionPanel。
    const source = readFileSync(join(process.cwd(), 'src/features/play/PlayView.vue'), 'utf8')
    const railStart = source.indexOf('class="play-control-rail"')
    const railSource = source.slice(railStart, source.indexOf('</aside>', railStart))
    expect(railSource).not.toContain('<CombatExtensionPanel')
    expect(railSource).toContain('<GmToolbar')
    wrapper.unmount()
  })

  it('Case G: the CombatMessageComposer tools slot has the same entry', async () => {
    gameState.detail.value = combatExtensionDetail({
      solo_mode: false,
      ruleset_runtime: {
        id: 'dnd2024',
        capabilities: { authoritative_intents: true, deterministic_combat: true },
      },
    })
    mockedAvailableActions.mockResolvedValue({
      gameplay: { state_version: 3, combat: { status: 'active' } },
    })

    const wrapper = await mountPlayView()

    expect(wrapper.find('.combat-message-composer').exists()).toBe(true)
    expect(wrapper.find('.composer').exists()).toBe(false)
    const trigger = wrapper.get('.combat-message-composer [data-testid="combat-extension-tool"]')
    expect(trigger.text()).toContain('战斗动作')

    // 该入口在战斗发言模式下同样真的能打开面板。
    await trigger.trigger('click')
    await flushPromises()
    expect(wrapper.get('.combat-ext').text()).toContain('内力掌')
    wrapper.unmount()
  })
})
