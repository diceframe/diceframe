import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import PushDecisionPanel from '../src/features/play/check-decisions/PushDecisionPanel.vue'

const mocked = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { api: mocked.api, errorMessage: actual.errorMessage }
})
const push = { schema_version: 1, revision: 2, status: 'pending' as const, run_id: 'run', round: 3, reason: '', consequence: '' }
const check = { check_id: 'check', actor_uid: 'a', roll: 60, push }
const render = (isGm = false, actorId = 'a') => mount(PushDecisionPanel, {
  global: { plugins: [i18n] }, props: { gameKey: 'web|room|bot', check, isGm, actorId, readonly: false },
})
afterEach(() => vi.clearAllMocks())

it('owner requests Push with a reason and saved scope, never client dice', async () => {
  i18n.global.locale.value = 'zh-CN'; mocked.api.mockResolvedValue({ ok: true })
  const wrapper = render()
  await wrapper.get('textarea').setValue('用灯检查门缝')
  await wrapper.get('[data-testid="push-request"]').trigger('click'); await flushPromises()
  expect(mocked.api).toHaveBeenCalledWith('/games/web%7Croom%7Cbot/checks/check/push/request', {
    method: 'POST', body: JSON.stringify({ run_id: 'run', round_number: 3, expected_revision: 2, reason: '用灯检查门缝' }),
  })
  expect(wrapper.emitted('refresh')).toHaveLength(1)
})

it('Keeper offers risk and only the owner can confirm the offered re-roll', async () => {
  const keeper = render(true, 'gm')
  await keeper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: '用灯检查' } } })
  expect(keeper.find('[data-testid="push-confirm"]').exists()).toBe(false)
  expect(keeper.text()).toContain('用灯检查')
  await keeper.get('textarea').setValue('惊动守卫')
  mocked.api.mockResolvedValue({ ok: true })
  await keeper.get('[data-testid="push-offer"]').trigger('click'); await flushPromises()
  expect(JSON.parse(mocked.api.mock.calls[0]?.[1].body).consequence).toBe('惊动守卫')
  const owner = render()
  await owner.setProps({ check: { ...check, push: { ...push, status: 'offered', consequence: '惊动守卫' } } })
  expect(owner.text()).toContain('惊动守卫')
  expect(owner.find('[data-testid="push-confirm"]').exists()).toBe(true)
  const other = render(false, 'b')
  expect(other.find('button').exists()).toBe(false)
  await owner.setProps({ readonly: true })
  expect(owner.find('button').exists()).toBe(false)
})

it('owner with GM authority sees confirmation first and can expand risk editing', async () => {
  const ownerGm = render(true, 'a')
  expect(ownerGm.text()).toContain('你同时是主持人和行动者')
  expect(ownerGm.text()).toContain('申请后不能再消耗幸运改判')
  await ownerGm.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: '新方法' } } })
  expect(ownerGm.text()).toContain('请先说明这次尝试失败的风险')
  await ownerGm.setProps({ check: { ...check, push: { ...push, status: 'offered', consequence: '惊动守卫' } } })
  expect(ownerGm.find('[data-testid="push-confirm"]').exists()).toBe(true)
  expect(ownerGm.find('[data-testid="push-adjust-risk"]').exists()).toBe(true)
  expect(ownerGm.find('textarea').exists()).toBe(false)
  await ownerGm.get('[data-testid="push-adjust-risk"]').trigger('click')
  expect(ownerGm.find('textarea').exists()).toBe(true)
  expect(ownerGm.find('[data-testid="push-offer"]').exists()).toBe(true)
  expect(ownerGm.find('[data-testid="push-confirm"]').exists()).toBe(false)
  mocked.api.mockResolvedValue({ ok: true })
  await ownerGm.get('textarea').setValue('惊动楼上的守卫')
  await ownerGm.get('[data-testid="push-offer"]').trigger('click'); await flushPromises()
  expect(ownerGm.find('textarea').exists()).toBe(false)
  expect(ownerGm.find('[data-testid="push-confirm"]').exists()).toBe(true)
  const otherKeeper = render(true, 'gm')
  await otherKeeper.setProps({ check: { ...check, push: { ...push, status: 'offered', consequence: '风险' } } })
  expect(otherKeeper.find('textarea').exists()).toBe(true)
  expect(otherKeeper.find('[data-testid="push-confirm"]').exists()).toBe(false)
})

it('preserves a failed-save draft and discards completions from a previous game', async () => {
  mocked.api.mockRejectedValue(new Error('save failed'))
  const wrapper = render()
  await wrapper.get('textarea').setValue('新方法')
  await wrapper.get('[data-testid="push-request"]').trigger('click'); await flushPromises()
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新方法')
  expect(wrapper.get('[role="alert"]').text()).toContain('save failed')
  let finish!: (value: unknown) => void
  mocked.api.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  await wrapper.get('[data-testid="push-request"]').trigger('click')
  await wrapper.setProps({ gameKey: 'new' })
  finish({ ok: true }); await flushPromises()
  expect(wrapper.emitted('refresh')).toBeUndefined()
})

it('shows original and pushed rolls plus the accepted consequence after refresh', async () => {
  const wrapper = render()
  await wrapper.setProps({ check: { ...check, roll: 87, push: { ...push, status: 'resolved', failed: true, consequence: '失去线索', original: { roll: 60 } } } })
  expect(wrapper.text()).toContain('60 → 87')
  expect(wrapper.text()).toContain('失去线索')
  expect(wrapper.find('button').exists()).toBe(false)
})

it('keeps a typed risk across polling that replaces the check object for the same game, check and viewer', async () => {
  const wrapper = render(true, 'gm')
  await wrapper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: 'New approach' } } })
  await wrapper.get('textarea').setValue('Alert the upstairs guard')
  await wrapper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: 'New approach' } } })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('Alert the upstairs guard')
  expect(wrapper.get('[data-testid=push-offer]').attributes('disabled')).toBeUndefined()
  await wrapper.setProps({ actorId: 'another-viewer' })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
})

it('keeps a typed risk while a refresh briefly omits push details', async () => {
  const wrapper = render(true, 'gm')
  await wrapper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: 'New approach' } } })
  await wrapper.get('textarea').setValue('Alert the upstairs guard')
  await wrapper.setProps({ check: { ...check, push: undefined } })
  await wrapper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: 'New approach' } } })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('Alert the upstairs guard')
})

it('discards a draft when a missing push projection is replaced by a new run', async () => {
  const wrapper = render(true, 'gm')
  await wrapper.setProps({ check: { ...check, push: { ...push, status: 'requested', reason: 'Old approach' } } })
  await wrapper.get('textarea').setValue('Old risk')
  await wrapper.setProps({ check: { ...check, push: undefined } })
  await wrapper.setProps({ check: { ...check, push: { ...push, run_id: 'run-2', status: 'requested', reason: 'New approach' } } })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
})

it('shows a localized refusal instead of a bare status line', async () => {
  const { ApiError } = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  i18n.global.locale.value = 'zh-CN'
  mocked.api.mockRejectedValue(new ApiError('HTTP 403', 403, 'push_forbidden'))
  const wrapper = render()
  await wrapper.get('textarea').setValue('换个办法')
  await wrapper.get('[data-testid="push-request"]').trigger('click'); await flushPromises()
  const alert = wrapper.get('[role="alert"]').text()
  expect(alert).toBe(i18n.global.t('apiErrors.push_forbidden'))
  expect(alert).not.toContain('HTTP 403')
})
