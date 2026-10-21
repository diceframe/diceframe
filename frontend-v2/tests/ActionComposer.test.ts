import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ActionComposer from '../src/components/ActionComposer.vue'
import { api } from '../src/api/client'
import type { GameDetail } from '../src/api/types'

vi.mock('../src/api/client', () => {
  class ApiError extends Error {
    constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
  }
  return { api: vi.fn(), apiBlob: vi.fn(), ApiError }
})

const mockedApi = vi.mocked(api)

function detail(submitted = true, roundNumber = 3, runId = 'run-1'): GameDetail {
  return {
    game_key: 'web|room|bot',
    run_id: runId,
    round_number: roundNumber,
    solo_mode: false,
    multiplayer: {
      submitted_actions: submitted
        ? [{ user_id: 'player-1', text: '检查门锁', revision_count: 1 }]
        : [],
    },
  }
}

describe('ActionComposer rollback refresh', () => {
  it('stacks the optional skill above the action in one column inside the input frame', () => {
    mockedApi.mockResolvedValue({})
    const wrapper = mount(ActionComposer, { global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false), player: {
        user_id: 'player-1', character_name: 'Scout', character_sheet: { skills: [{ name: 'Acrobatics', value: 80 }] },
      } } })
    const input = wrapper.get('[data-testid="composer-input"]')
    expect(input.find('textarea').exists()).toBe(true)
    expect(input.find('.composer-input-actions > .primary').exists()).toBe(true)
    const controls = input.get('.composer-input-actions').element.children
    expect(controls[0]!.classList.contains('action-check-options')).toBe(true)
    expect(controls[1]!.classList.contains('primary')).toBe(true)
    expect(wrapper.find('.composer-row > .primary').exists()).toBe(false)
    wrapper.unmount()
  })
  it('submits an explicitly chosen existing skill without trusting a client-side bonus', async () => {
    mockedApi.mockResolvedValue({})
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false), player: {
        user_id: 'player-1', character_name: 'Scout', character_sheet: { skills: [{ name: 'Acrobatics', value: 80 }] },
      } },
    })
    await wrapper.get('.action-check-options select').setValue('Acrobatics')
    await wrapper.get('textarea').setValue('I cross the gap')
    await wrapper.get('.composer-input-actions > .primary').trigger('click')
    await flushPromises()
    const submission = mockedApi.mock.calls.find(([path]) => String(path).endsWith('/action'))
    expect(JSON.parse(submission![1]!.body as string)).toEqual({ text: 'I cross the gap', selected_skill: 'Acrobatics' })
    expect((wrapper.get('.action-check-options select').element as HTMLSelectElement).value).toBe('')
    await wrapper.get('textarea').setValue('I ask the guard a question')
    await wrapper.get('.composer-input-actions > .primary').trigger('click')
    await flushPromises()
    const nextSubmission = mockedApi.mock.calls.filter(([path]) => String(path).endsWith('/action')).at(-1)
    expect(JSON.parse(nextSubmission![1]!.body as string)).toEqual({ text: 'I ask the guard a question' })
    wrapper.unmount()
  })

  it('keeps the selected skill with a failed draft and drops it for a different run or removed skill', async () => {
    mockedApi.mockRejectedValue(new Error('offline'))
    const props = { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false), player: {
      user_id: 'player-1', character_name: 'Scout', character_sheet: { skills: [{ name: 'Acrobatics', value: 80 }] },
    } }
    const wrapper = mount(ActionComposer, { global: { plugins: [i18n] }, props })
    await wrapper.get('.action-check-options select').setValue('Acrobatics')
    await wrapper.get('textarea').setValue('I cross the gap')
    await wrapper.get('.composer-input-actions > .primary').trigger('click')
    await flushPromises()
    wrapper.unmount()
    const restored = mount(ActionComposer, { global: { plugins: [i18n] }, props })
    expect((restored.get('.action-check-options select').element as HTMLSelectElement).value).toBe('Acrobatics')
    await restored.setProps({ player: { ...props.player, character_sheet: { skills: [{ name: 'Survival', value: 20 }] } } })
    expect((restored.get('.action-check-options select').element as HTMLSelectElement).value).toBe('')
    await restored.get('.action-check-options select').setValue('Survival')
    await restored.setProps({ detail: detail(false, 3, 'new-run') })
    expect((restored.get('.action-check-options select').element as HTMLSelectElement).value).toBe('')
    restored.unmount()
  })

  it('inherits the table visibility until the player chooses and keeps the input available', async () => {
    const wrapper = mount(ActionComposer, { global: { plugins: [i18n] }, props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false) } })
    expect(wrapper.findAll('.quick-actions button')).toHaveLength(4)
    await wrapper.setProps({ detail: { ...detail(false), quick_actions_enabled: false } })
    expect(wrapper.find('.quick-actions').exists()).toBe(false)
    await wrapper.setProps({ detail: { ...detail(false), quick_actions_enabled: false, quick_actions: ['Inspect the lock'] } })
    expect(wrapper.find('.quick-actions').exists()).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(true)
    await wrapper.get('textarea').setValue('My own action')
    const toggle = wrapper.get('[data-testid="quick-actions-toggle"]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(wrapper.get('.quick-actions').text()).toContain('Inspect the lock')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('My own action')
    await toggle.trigger('click')
    await wrapper.setProps({ detail: { ...detail(false), quick_actions_enabled: true, quick_actions: ['Inspect the lock'] } })
    expect(wrapper.find('.quick-actions').exists()).toBe(false)
    wrapper.unmount()
  })

  it('remembers personal visibility across reloads and rounds without changing another game or actor', async () => {
    const props = { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false) }
    const wrapper = mount(ActionComposer, { global: { plugins: [i18n] }, props })
    await wrapper.get('[data-testid="quick-actions-toggle"]').trigger('click')
    await wrapper.setProps({ detail: detail(false, 8, 'new-run') })
    expect(wrapper.find('.quick-actions').exists()).toBe(false)
    wrapper.unmount()
    const restored = mount(ActionComposer, { global: { plugins: [i18n] }, props })
    expect(restored.find('.quick-actions').exists()).toBe(false)
    await restored.setProps({ userId: 'player-2' })
    expect(restored.find('.quick-actions').exists()).toBe(true)
    await restored.setProps({ userId: 'player-1', gameKey: 'web|other-room|bot' })
    expect(restored.find('.quick-actions').exists()).toBe(true)
    await restored.setProps({ gameKey: props.gameKey })
    expect(restored.find('.quick-actions').exists()).toBe(false)
    expect(mockedApi.mock.calls.filter(([, options]) => options?.method === 'POST')).toEqual([])
    restored.unmount()
  })

  it('can show suggestions when the table default is hidden even if browser storage is unavailable', async () => {
    const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    try {
      const wrapper = mount(ActionComposer, { global: { plugins: [i18n] }, props: {
        gameKey: 'web|room|bot', userId: 'player-1', detail: { ...detail(false), quick_actions_enabled: false }, disabled: true,
      } })
      await wrapper.get('[data-testid="quick-actions-toggle"]').trigger('click')
      expect(wrapper.findAll('.quick-actions button')).toHaveLength(4)
      expect(wrapper.get('[data-testid="quick-actions-toggle"]').attributes('disabled')).toBeUndefined()
      wrapper.unmount()
    } finally { read.mockRestore(); write.mockRestore() }
  })

  it('resets the skill after refresh confirms a committed action without erasing an uncertain draft', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED' })
    const wrapper = mount(ActionComposer, { global: { plugins: [i18n] }, props: {
      gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false), player: {
        user_id: 'player-1', character_name: 'Scout', character_sheet: { skills: [{ name: 'Acrobatics', value: 80 }] },
      },
    } })
    await wrapper.get('.action-check-options select').setValue('Acrobatics')
    await wrapper.get('textarea').setValue('I cross the gap')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect((wrapper.get('.action-check-options select').element as HTMLSelectElement).value).toBe('Acrobatics')
    await wrapper.get('[data-testid="action-recovery-refresh"]').trigger('click')
    await wrapper.setProps({ recoveryRefreshToken: wrapper.emitted<[string]>('refresh')!.at(-1)![0] })
    expect((wrapper.get('.action-check-options select').element as HTMLSelectElement).value).toBe('')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
    mockedApi.mockReset()
    sessionStorage.clear()
  })

  function actionButton(wrapper: ReturnType<typeof mount>) {
    const button = wrapper.findAll('button').find(candidate => candidate.text() === '行动')
    if (!button) throw new Error('未找到行动按钮')
    return button
  }

  it('never restores the removed player-side dice gate, including old phase responses', async () => {
    mockedApi.mockResolvedValue({ phase: 'dice', message: '需要掷骰' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false),
      },
    })

    await wrapper.get('textarea').setValue('检查门锁')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('需要掷骰')
    expect(wrapper.text()).toContain('行动已记录')

    await wrapper.setProps({ detail: detail(true) })
    await wrapper.setProps({ detail: detail(false) })

    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('行动已记录')
  })

  it('clears stale submission feedback when the round moves backward', async () => {
    mockedApi.mockResolvedValue({})
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false, 4),
      },
    })

    await wrapper.get('textarea').setValue('观察走廊')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('行动已记录')

    await wrapper.setProps({ detail: detail(false, 3) })

    expect(wrapper.text()).not.toContain('行动已记录')
    expect(wrapper.find('textarea').exists()).toBe(true)
  })

  it('submits only the natural-language action and leaves checks to the server', async () => {
    mockedApi.mockResolvedValueOnce({ phase: 'done', advanced: true })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: {
        gameKey: 'web|room|bot',
        userId: 'player-1',
        detail: detail(false),
      },
    })

    await wrapper.get('textarea').setValue('悄悄上楼')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    const request = mockedApi.mock.calls[0]?.[1] as { body?: string }
    expect(JSON.parse(request.body || '{}')).toEqual({ text: '悄悄上楼' })
    expect(wrapper.emitted('refresh')?.at(-1)).toEqual([])
  })

  it('keeps an editable draft after a rolled-back round failure and exposes recovery attention', async () => {
    const onRecoveryStatus = vi.fn()
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_PROCESSING_FAILED', message: 'round failed' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false), onRecoveryStatus },
    })

    await wrapper.get('textarea').setValue('改写行动')
    await actionButton(wrapper).trigger('click')
    await flushPromises()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('改写行动')
    expect(wrapper.get('[data-testid="action-recovery"]').text()).toContain('退回行动阶段')
    expect(actionButton(wrapper).attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('recovery-status')?.at(-1)?.[0]).toMatchObject({ count: 1 })
    wrapper.unmount()
    expect(onRecoveryStatus).toHaveBeenLastCalledWith({ count: 0, message: '' })
  })

  it('requires refresh for post-commit and unknown results without resubmitting', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED', message: 'post commit failed' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false) },
    })

    await wrapper.get('textarea').setValue('不确定结果')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="action-recovery-refresh"]').exists()).toBe(true)
    expect(mockedApi).toHaveBeenCalledTimes(1)
    await wrapper.get('[data-testid="action-recovery-refresh"]').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    expect(typeof wrapper.emitted('refresh')?.at(-1)?.[0]).toBe('string')
    expect(mockedApi).toHaveBeenCalledTimes(1)
    wrapper.unmount()

    mockedApi.mockRejectedValueOnce(new Error('network lost'))
    const unknown = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 4) },
    })
    await unknown.get('textarea').setValue('网络未知')
    await actionButton(unknown).trigger('click')
    await flushPromises()
    expect(unknown.find('[data-testid="action-recovery-refresh"]').exists()).toBe(true)
    expect(mockedApi).toHaveBeenCalledTimes(2)
    unknown.unmount()
  })

  it('requires refresh for dedicated-model and rollback-recovery failures', async () => {
    for (const [index, [code, message]] of [
      ['CHECK_MODEL_NOT_CONFIGURED', '独立检定模型未配置'],
      ['ROUND_ROLLBACK_FAILED', '回合恢复失败'],
    ].entries()) {
      mockedApi.mockRejectedValueOnce({ code })
      const wrapper = mount(ActionComposer, {
        global: { plugins: [i18n] },
        props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 4 + index) },
      })
      await wrapper.get('textarea').setValue('需要恢复的行动')
      await actionButton(wrapper).trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-testid="action-recovery-refresh"]').exists()).toBe(true)
      expect(wrapper.get('[data-testid="action-recovery"]').text()).toContain(message)
      expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
      wrapper.unmount()
    }
  })

  it('restores a recovery bar and draft only for the same game, run, actor, and round', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_PROCESSING_FAILED', message: 'round failed' })
    const first = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 8) },
    })
    await first.get('textarea').setValue('保留这条草稿')
    await actionButton(first).trigger('click')
    await flushPromises()
    first.unmount()

    const same = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 8) },
    })
    expect(same.find('[data-testid="action-recovery"]').exists()).toBe(true)
    expect((same.get('textarea').element as HTMLTextAreaElement).value).toBe('保留这条草稿')
    same.unmount()

    const otherActor = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-2', detail: detail(false, 8) },
    })
    expect(otherActor.find('[data-testid="action-recovery"]').exists()).toBe(false)
    expect((otherActor.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    otherActor.unmount()
  })

  it('ignores a stale success after the composer identity changes', async () => {
    let finishOld!: (value: { phase: string }) => void
    let finishNew!: (value: { phase: string }) => void
    mockedApi
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve }))
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 1, 'run-a') },
    })

    await wrapper.get('textarea').setValue('旧身份行动')
    await actionButton(wrapper).trigger('click')
    await wrapper.setProps({ gameKey: 'web|other|bot', userId: 'player-2', detail: detail(false, 2, 'run-b') })
    await wrapper.get('textarea').setValue('新身份草稿')
    await actionButton(wrapper).trigger('click')

    finishOld({ phase: 'done' })
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新身份草稿')
    expect(wrapper.get('.primary').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="action-recovery"]').exists()).toBe(false)

    finishNew({ phase: 'done' })
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('ignores a stale failure after the composer identity changes', async () => {
    let rejectOld!: (error: unknown) => void
    mockedApi.mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 1, 'run-a') },
    })

    await wrapper.get('textarea').setValue('旧身份行动')
    await actionButton(wrapper).trigger('click')
    await wrapper.setProps({ gameKey: 'web|other|bot', userId: 'player-2', detail: detail(false, 2, 'run-b') })
    await wrapper.get('textarea').setValue('新身份草稿')
    rejectOld({ code: 'ROUND_PROCESSING_FAILED' })
    await flushPromises()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新身份草稿')
    expect(wrapper.find('[data-testid="action-recovery"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not persist a late failure after the composer unmounts', async () => {
    let rejectOld!: (error: unknown) => void
    mockedApi.mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 3, 'run-a') },
    })

    await wrapper.get('textarea').setValue('卸载前行动')
    await actionButton(wrapper).trigger('click')
    wrapper.unmount()
    rejectOld({ code: 'ROUND_PROCESSING_FAILED' })
    await flushPromises()

    expect(sessionStorage.length).toBe(0)
  })

  it('clears a post-commit recovery only after an acknowledged refresh', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 6) },
    })
    await wrapper.get('textarea').setValue('已提交行动')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ recoveryRefreshToken: '' })
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="action-recovery-refresh"]').trigger('click')
    const token = wrapper.emitted<[string]>('refresh')?.at(-1)?.[0]
    await wrapper.setProps({ recoveryRefreshToken: token })
    expect(wrapper.find('[data-testid="action-recovery"]').exists()).toBe(false)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(mockedApi).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('unlocks unknown recovery after acknowledged refresh while preserving the draft', async () => {
    mockedApi.mockRejectedValueOnce(new Error('network lost'))
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 7) },
    })
    await wrapper.get('textarea').setValue('需要核对的行动')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="action-recovery-refresh"]').trigger('click')
    const token = wrapper.emitted<[string]>('refresh')?.at(-1)?.[0]
    await wrapper.setProps({ recoveryRefreshToken: token })

    expect(wrapper.find('[data-testid="action-recovery"]').exists()).toBe(true)
    expect(wrapper.get('textarea').attributes('disabled')).toBeUndefined()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('需要核对的行动')
    expect(mockedApi).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not acknowledge a newer recovery with an older refresh token', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 6) },
    })
    await wrapper.get('textarea').setValue('上一轮行动')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="action-recovery-refresh"]').trigger('click')
    const oldToken = wrapper.emitted<[string]>('refresh')?.at(-1)?.[0]
    expect(typeof oldToken).toBe('string')

    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED' })
    await wrapper.setProps({ detail: detail(false, 7) })
    await wrapper.get('textarea').setValue('新一轮行动')
    await actionButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.setProps({ recoveryRefreshToken: oldToken })

    expect(wrapper.find('[data-testid="action-recovery-refresh"]').exists()).toBe(true)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新一轮行动')
    expect(mockedApi).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('exposes focusRecovery for editable input and refresh actions', async () => {
    mockedApi.mockRejectedValueOnce({ code: 'ROUND_PROCESSING_FAILED' })
    const editable = mount(ActionComposer, {
      global: { plugins: [i18n] },
      attachTo: document.body,
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 9) },
    })
    await editable.get('textarea').setValue('待修改行动')
    await actionButton(editable).trigger('click')
    await flushPromises()
    const inputScroll = vi.fn()
    Object.defineProperty(editable.get('textarea').element, 'scrollIntoView', { configurable: true, value: inputScroll })
    ;(editable.vm as unknown as { focusRecovery: () => void }).focusRecovery()
    expect(document.activeElement).toBe(editable.get('textarea').element)
    expect(inputScroll).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
    editable.unmount()

    mockedApi.mockRejectedValueOnce({ code: 'ROUND_POST_COMMIT_FAILED' })
    const refresh = mount(ActionComposer, {
      global: { plugins: [i18n] },
      attachTo: document.body,
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail(false, 10) },
    })
    await refresh.get('textarea').setValue('待核对行动')
    await actionButton(refresh).trigger('click')
    await flushPromises()
    const refreshButton = refresh.get('[data-testid="action-recovery-refresh"]')
    const buttonScroll = vi.fn()
    Object.defineProperty(refreshButton.element, 'scrollIntoView', { configurable: true, value: buttonScroll })
    ;(refresh.vm as unknown as { focusRecovery: () => void }).focusRecovery()
    expect(document.activeElement).toBe(refreshButton.element)
    expect(buttonScroll).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
    refresh.unmount()
  })

})
