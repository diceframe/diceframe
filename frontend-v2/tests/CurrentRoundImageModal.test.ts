import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import CurrentRoundImageModal from '../src/components/play/CurrentRoundImageModal.vue'
import type { GameDetail, LogEntry, Player } from '../src/api/types'

const storyboardApi = vi.hoisted(() => ({
  analyzeStoryboard: vi.fn(),
  fetchStoryboardDraft: vi.fn().mockResolvedValue({ ok: false }),
}))

vi.mock('../src/api/generatedImages', () => storyboardApi)


function mountModal(detail: Partial<GameDetail>, log: LogEntry[], players: Player[] = [], autoStoryboard = false) {
  i18n.global.locale.value = 'zh-CN'
  return mount(CurrentRoundImageModal, {
    global: { plugins: [i18n], stubs: { Teleport: true } },
    props: { open: true, gameKey: 'web|room|game', detail: detail as GameDetail, log, players, autoStoryboard },
  })
}

describe('CurrentRoundImageModal', () => {
  beforeEach(() => {
    storyboardApi.fetchStoryboardDraft.mockReset().mockResolvedValue({ ok: false })
    storyboardApi.analyzeStoryboard.mockReset()
  })
  it('drafts the default prompt from scene and narration content only', () => {
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: 'The party regroups inside the lighthouse.' }],
    )
    const prompt = wrapper.get('textarea').element.value
    expect(prompt).toContain('雾港码头')
    expect(prompt).toContain('The party regroups inside the lighthouse.')
    expect(prompt).not.toContain('第4轮')
  })

  it('keeps style and composition wording out of the client draft', () => {
    const wrapper = mountModal({ scene: '雾港码头', round_number: 5 }, [])
    const prompt = wrapper.get('textarea').element.value
    expect(prompt).toBe('雾港码头')
  })

  it('truncates long narration at the server-side context limit', () => {
    const longNarration = '雾'.repeat(2500)
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: longNarration }],
    )
    const prompt = wrapper.get('textarea').element.value
    expect(prompt).toBe(`雾港码头\n${'雾'.repeat(1600)}`)
  })

  it('emits the target round and manual payload on generate', async () => {
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: 'The party regroups inside the lighthouse.' }],
    )
    await wrapper.get('button.primary').trigger('click')
    const payload = wrapper.emitted('generate')?.[0]?.[0] as { prompt: string; round: number; panels: unknown[]; panel_count: number; use_avatar_references: boolean }
    expect(payload.round).toBe(4)
    expect(payload.panels).toEqual([{
      participants: [],
      location: '雾港码头',
      description: 'The party regroups inside the lighthouse.',
    }])
    expect(payload.use_avatar_references).toBe(false)
    expect(payload.panel_count).toBe(1)
    expect(payload.prompt).toContain('The party regroups inside the lighthouse.')
  })

  it('keeps the storyboard control concise', () => {
    const wrapper = mountModal({ scene: '雾港码头', round_number: 5 }, [])
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/分镜格数|按 [1-6] 格分析分镜/)
    expect(wrapper.text()).not.toContain('系统会根据公开叙事判断同时异地或独立关键镜头，自动生成最多六格分镜。')
    expect(wrapper.text()).not.toContain('分镜留空')
  })

  it('enables uploaded portrait references and reports the detected count', async () => {
    const players: Player[] = [
      {
        user_id: 'uploaded-player',
        character_name: '观者',
        character_sheet: { portrait: { kind: 'upload', asset_id: 'avatar-1' } },
      },
      {
        user_id: 'builtin-player',
        character_name: '旅人',
        character_sheet: { portrait: { kind: 'builtin', id: 'freeform_fantasy:0' } },
      },
    ]
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: 'The party regroups.' }],
      players,
    )
    const checkbox = wrapper.get<HTMLInputElement>('.avatar-reference-toggle input[type="checkbox"]')
    expect(checkbox.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('已检测到本局 1 个上传头像')

    await checkbox.setValue(true)
    await wrapper.get('button.primary').trigger('click')
    const payload = wrapper.emitted('generate')?.[0]?.[0] as { use_avatar_references: boolean }
    expect(payload.use_avatar_references).toBe(true)
  })

  it('disables portrait references with a specific reason when no upload exists', () => {
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [],
      [{
        user_id: 'builtin-player',
        character_name: '旅人',
        character_sheet: { portrait: { kind: 'builtin', id: 'freeform_fantasy:0' } },
      }],
    )
    expect(wrapper.get('.avatar-reference-toggle input[type="checkbox"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('本局没有可用的上传头像')
  })

  it('pre-fills multiple panels and submits edited panel content', async () => {
    const wrapper = mountModal(
      { scene: '寄宿屋', round_number: 5 },
      [{ round: 4, gm_response: '门口的老妇人交出簿子；与此同时，塔底的队伍钻入船肋；随后，阿尔比娜回到前厅。' }],
    )
    expect(wrapper.find('select').exists()).toBe(false)
    const locations = wrapper.findAll('input').filter(input => !input.attributes('type'))
    await locations[0].setValue('寄宿屋门链')
    const descriptions = wrapper.findAll('textarea')
    await descriptions[1].setValue('手动确认的塔底关键镜头')
    await wrapper.get('button.primary').trigger('click')
    const payload = wrapper.emitted('generate')?.[0]?.[0] as { panels: Array<{ location: string; description: string }>; panel_count: number }
    expect(payload.panels).toHaveLength(1)
    expect(payload.panel_count).toBe(1)
    expect(payload.panels[0].location).toBe('寄宿屋门链')
    expect(payload.panels[0].description).toBe('手动确认的塔底关键镜头')
  })

  it('lets the player choose up to six panels and blocks incomplete panels', async () => {
    const wrapper = mountModal({ scene: '雾港码头', round_number: 5 }, [])
    expect(wrapper.findAll('.storyboard-panel')).toHaveLength(1)
    await wrapper.findAll('textarea')[1].setValue('')
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.text()).toContain('请补全每一格的地点和描述。')
    expect(wrapper.emitted('generate')).toBeUndefined()
  })

  it('groups continuous sentences into scene beats and keeps detected characters', () => {
    const players: Player[] = [
      { user_id: 'watcher', character_name: '观者' },
      { user_id: 'emotion', character_name: '情緒' },
      { user_id: 'albina', character_name: '阿尔比娜' },
    ]
    const wrapper = mountModal(
      { scene: '砖墙暗道', round_number: 12 },
      [{ round: 11, gm_response: '观者侧身贴上砖墙。门在他身后合拢。他继续沿甬道前进。站前窄巷里，情緒与阿尔比娜赶到巷口。' }],
      players,
    )

    expect(wrapper.find('select').exists()).toBe(false)
    const panels = wrapper.findAll('.storyboard-panel')
    expect(panels).toHaveLength(1)
  })

  it('restores an unapplied persisted candidate without marking it as applied', async () => {
    storyboardApi.fetchStoryboardDraft.mockResolvedValueOnce({
      ok: true,
      panels: [],
      candidate: {
        requested_panel_count: null,
        panels: [{
          location: '码头石阶',
          participants: ['观者'],
          description: '持灯的人在雾中回头。',
        }, {
          location: '塔底残骸',
          participants: ['情緒'],
          description: '残骸深处传来潮湿的回声。',
        }],
      },
    })
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: '公开叙事' }],
      [],
      true,
    )
    await nextTick()
    await Promise.resolve()
    expect(wrapper.find('[data-testid="storyboard-candidate"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('持灯的人在雾中回头。')
    expect(wrapper.text()).not.toContain('尚未分析分镜')
    // Candidate preview is not silently copied into the editable/applied
    // panels until the user explicitly clicks 应用分镜.
    expect(wrapper.find('.storyboard-panel').isVisible()).toBe(false)
  })

  it('keeps a background analysis candidate after closing and reopening the dialog', async () => {
    let resolveAnalysis!: (value: unknown) => void
    const pending = new Promise(resolve => { resolveAnalysis = resolve })
    storyboardApi.fetchStoryboardDraft.mockResolvedValue({ ok: false })
    storyboardApi.analyzeStoryboard.mockReset().mockReturnValueOnce(pending)

    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, gm_response: '队伍在码头重新会合。' }],
      [],
      true,
    )
    await wrapper.find('.storyboard-header button').trigger('click')
    expect(wrapper.text()).toContain('正在分析公开剧情')

    await wrapper.setProps({ open: false })
    expect(wrapper.find('.modal').exists()).toBe(false)
    resolveAnalysis({
      ok: true,
      panels: [{ location: '码头石阶', participants: ['观者'], description: '后台完成的候选画面' }],
    })
    await pending
    await nextTick()

    await wrapper.setProps({ open: true })
    await nextTick()
    expect(wrapper.find('[data-testid="storyboard-candidate"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('后台完成的候选画面')
  })

  it('keeps local prompt edits when the same scene is closed and reopened', async () => {
    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, current_swipe: 1, gm_response: '队伍在码头重新会合。' }],
      [],
      true,
    )
    const prompt = wrapper.get<HTMLTextAreaElement>('textarea')
    await prompt.setValue('保留这段用户补充的画面要求')

    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await nextTick()

    expect(wrapper.get<HTMLTextAreaElement>('textarea').element.value).toBe('保留这段用户补充的画面要求')
  })

  it('does not reuse a candidate after switching to another game with the same narration', async () => {
    let resolveAnalysis!: (value: unknown) => void
    const pending = new Promise(resolve => { resolveAnalysis = resolve })
    storyboardApi.analyzeStoryboard.mockReset().mockReturnValueOnce(pending)

    const wrapper = mountModal(
      { scene: '雾港码头', round_number: 5 },
      [{ round: 4, current_swipe: 0, gm_response: '相同的公开叙事。' }],
      [],
      true,
    )
    await wrapper.find('.storyboard-header button').trigger('click')
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ gameKey: 'web|room|another-game' })
    await wrapper.setProps({ open: true })

    resolveAnalysis({
      ok: true,
      panels: [{ location: '旧游戏地点', participants: [], description: '不应跨游戏显示的候选' }],
    })
    await pending
    await nextTick()

    expect(wrapper.find('[data-testid="storyboard-candidate"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('不应跨游戏显示的候选')
  })

  it('defaults to combining portraits even when storyboard mode is disabled', async () => {
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: 'Alice在码头。' }], [{
      user_id: 'alice', character_name: 'Alice',
      character_sheet: { portrait: { kind: 'upload', asset_id: 'avatar' } },
    }])
    await wrapper.get('.avatar-reference-toggle input').setValue(true)
    expect(wrapper.get<HTMLInputElement>('.avatar-combine-toggle input').element.checked).toBe(true)
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('generate')?.[0]?.[0]).toMatchObject({
      use_avatar_references: true, combine_avatar_references: true, panel_count: 1,
    })
    await wrapper.get('.avatar-combine-toggle input').setValue(false)
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('generate')?.[1]?.[0]).toMatchObject({ combine_avatar_references: false })
    expect(storyboardApi.analyzeStoryboard).not.toHaveBeenCalled()
  })

  function candidate(count: number) {
    return Array.from({ length: count }, (_, index) => ({
      location: '码头', participants: ['alice'], description: `连续关键动作${index + 1}`,
    }))
  }

  it.each([1, 2, 3, 4, 5, 6])('analyzes, previews, applies and generates exactly %i panels', async (count) => {
    storyboardApi.analyzeStoryboard.mockResolvedValueOnce({ ok: true, panels: candidate(count), requested_panel_count: count })
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: '公开剧情' }], [], true)
    await flushPromises()
    expect(storyboardApi.analyzeStoryboard).not.toHaveBeenCalled()
    await wrapper.get('select').setValue(count)
    expect(wrapper.text()).toContain(`按 ${count} 格分析分镜`)
    expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
    await wrapper.get('.storyboard-header button').trigger('click')
    await flushPromises()
    expect(storyboardApi.analyzeStoryboard).toHaveBeenCalledWith('web|room|game', 1, count)
    expect(wrapper.findAll('.storyboard-candidate-panel')).toHaveLength(count)
    expect(wrapper.text()).toContain(`连续关键动作${count}`)
    await wrapper.get('.storyboard-header .primary').trigger('click')
    expect(wrapper.get<HTMLSelectElement>('select').element.value).toBe(String(count))
    expect(wrapper.findAll('.storyboard-panel')).toHaveLength(count)
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('generate')?.[0]?.[0]).toMatchObject({
      panel_count: count, panels: candidate(count),
    })
  })

  it('keeps automatic mode after applying a three-panel candidate', async () => {
    storyboardApi.analyzeStoryboard.mockResolvedValueOnce({ ok: true, panels: candidate(3) })
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: '公开剧情' }], [], true)
    await wrapper.get('.storyboard-header button').trigger('click')
    await flushPromises()
    await wrapper.get('.storyboard-header .primary').trigger('click')
    expect(wrapper.get<HTMLSelectElement>('select').element.value).toBe('0')
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('generate')?.[0]?.[0]).toMatchObject({ panels: candidate(3), panel_count: undefined })
  })

  it('never displays or applies three panels returned for a six-panel request', async () => {
    storyboardApi.analyzeStoryboard.mockResolvedValueOnce({ ok: true, panels: candidate(3) })
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: '公开剧情' }], [], true)
    await wrapper.get('select').setValue(6)
    await wrapper.get('.storyboard-header button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('要求 6 格，返回 3 格。请重新分析。')
    expect(wrapper.find('.storyboard-candidate').exists()).toBe(false)
    expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
  })

  it('preserves the applied draft while discarding a mismatched in-flight result', async () => {
    let resolveAnalysis!: (value: unknown) => void
    storyboardApi.analyzeStoryboard.mockReturnValueOnce(new Promise(resolve => { resolveAnalysis = resolve }))
    const wrapper = mountModal({ scene: '码头' }, [{
      round: 1, gm_response: '公开剧情', scene_panels: candidate(3),
    }], [], true)
    await wrapper.findAll('.storyboard-panel textarea')[0].setValue('用户编辑内容')
    await wrapper.get('select').setValue(6)
    await wrapper.get('.storyboard-header button').trigger('click')
    await wrapper.get('select').setValue(2)
    resolveAnalysis({ ok: true, panels: candidate(6) })
    await flushPromises()
    expect(wrapper.find('.storyboard-candidate').exists()).toBe(false)
    expect(wrapper.findAll('.storyboard-panel')).toHaveLength(3)
    expect(wrapper.get<HTMLTextAreaElement>('.storyboard-panel textarea').element.value).toBe('用户编辑内容')
    expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
  })

  it('invalidates an existing candidate when the count changes', async () => {
    storyboardApi.analyzeStoryboard.mockResolvedValueOnce({ ok: true, panels: candidate(6) })
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: '公开剧情' }], [], true)
    await wrapper.get('select').setValue(6)
    await wrapper.get('.storyboard-header button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.storyboard-candidate').exists()).toBe(true)
    await wrapper.get('select').setValue(0)
    expect(wrapper.find('.storyboard-candidate').exists()).toBe(false)
  })

  it('does not restore a fixed-count persisted candidate into automatic mode', async () => {
    storyboardApi.fetchStoryboardDraft.mockResolvedValueOnce({
      ok: true, panels: [], candidate: { requested_panel_count: 3, panels: candidate(3) },
    })
    const wrapper = mountModal({ scene: '码头' }, [{ round: 1, gm_response: '公开剧情' }], [], true)
    await flushPromises()
    expect(wrapper.find('.storyboard-candidate').exists()).toBe(false)
  })

})
