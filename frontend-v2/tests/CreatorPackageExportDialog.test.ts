import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import CreatorPackageExportDialog from '../src/features/creator/CreatorPackageExportDialog.vue'
import type { CreatorPackageExportMode } from '../src/features/creator/copy'

const mocks = vi.hoisted(() => ({ api: vi.fn(), apiBlob: vi.fn() }))
vi.mock('../src/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, api: mocks.api, apiBlob: mocks.apiBlob }
})

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() }))
vi.mock('../src/composables/useToast', () => ({ useToast: () => toast }))

const fixedAdventure = {
  adventure_id: 'night-shift', source_kind: 'user', source_id: '', version: '1.0.0', format: 'adventure-bundle',
  world_policy: 'fixed', recommended_world_id: 'office', required_runtime: { id: 'core:generic', minimum_version: 1 },
  name: '夜班', summary: '', estimated_minutes: 60, compatibility: 'compatible', incompatibility_reasons: [], custom: true,
}

function respond(path: string) {
  if (path === '/worlds') return { worlds: [{ id: 'office', name: '深夜办公室' }] }
  if (path.startsWith('/adventures')) return { ok: true, adventures: [fixedAdventure] }
  if (path.startsWith('/lorebooks')) return { books: [{ id: 'handbook', name: '员工手册', entry_count: 3 }] }
  if (path.startsWith('/rules')) return { rules: [{ rule_id: 'office-rules', rule_name: '加班规则' }] }
  if (path === '/character-cards') return { cards: [{ id: 'card-mira', character_name: '米拉' }, { card_id: 'no-id', character_name: '没有 id' }] }
  if (path === '/scene-images') return { ok: true, scene_image: { kind: 'upload', id: 'cover-1' } }
  if (path === '/map-backgrounds') return { ok: true, map_background: { kind: 'upload', id: 'bg-1' } }
  throw new Error(`unexpected ${path}`)
}

async function render(mode: CreatorPackageExportMode) {
  const wrapper = mount(CreatorPackageExportDialog, { props: { mode }, global: { plugins: [i18n], stubs: { Teleport: true } } })
  await flushPromises()
  await wrapper.get('input[placeholder]').setValue(mode === 'adventure' ? 'night-module' : 'office-resources')
  await wrapper.findAll('.creator-package-grid input')[1].setValue('测试包')
  return wrapper
}

async function chooseFiles(wrapper: Awaited<ReturnType<typeof render>>, testId: string, files: File[]) {
  const input = wrapper.get(`[data-testid="${testId}"]`)
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
}

function image(name: string): File {
  return new File(['img'], name, { type: 'image/png' })
}

function exportBody(): Record<string, unknown> {
  const [path, init] = mocks.apiBlob.mock.calls[0] as [string, { body: string }]
  expect(path).toBe('/plugins/export')
  return JSON.parse(init.body) as Record<string, unknown>
}

let downloads: string[] = []

beforeEach(() => {
  localStorage.clear()
  downloads = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download) })
  i18n.global.locale.value = 'zh-CN'
  mocks.api.mockImplementation(async (path: string) => respond(path))
  mocks.apiBlob.mockResolvedValue({ blob: async () => new Blob(['pack']) })
  URL.createObjectURL = vi.fn(() => 'blob:pack')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks() })

describe('CreatorPackageExportDialog', () => {
  it('exports a resource pack without listing or sending adventures', async () => {
    const wrapper = await render('resource')
    expect(wrapper.text()).toContain('导出资源包')
    expect(mocks.api.mock.calls.some(([path]) => String(path).startsWith('/adventures'))).toBe(false)
    expect(wrapper.find('[data-testid="creator-export-adventures"]').exists()).toBe(false)

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    expect(wrapper.text()).toContain('至少选择一个世界、规则、资料册或角色卡。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()

    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    // Image switches are sent explicitly with the backend's defaults.
    expect(exportBody()).toMatchObject({
      world_id: 'office', adventure_refs: [], card_ids: [], flat: false,
      include_portraits: true, include_scene_images: true, include_map: true, map_icons: [],
    })
    expect(downloads).toEqual(['office-resources-0.1.0.dfplugin'])
    expect(wrapper.get('[data-testid="creator-export-done"]').text()).toContain('已下载 office-resources-0.1.0.dfplugin')
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.get('.creator-package-actions button:not(.primary)').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('guides the author through publishing to their own repository', async () => {
    const wrapper = await render('resource')
    await wrapper.findAll('.creator-package-grid input')[2].setValue('1.2.0')
    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()

    // 没填仓库地址之前，上传 / Release / 投稿三步是锁住的
    expect(wrapper.get('[data-testid="submission-wizard-step-upload"]').classes()).toContain('is-locked')
    expect(wrapper.find('[data-testid="submission-wizard-step-submit"] a').exists()).toBe(false)

    await wrapper.get('[data-testid="submission-wizard-repo-input"]').setValue('https://github.com/me/office-resources')
    await flushPromises()

    expect(wrapper.get('[data-testid="submission-wizard-step-upload"] a').attributes('href'))
      .toBe('https://github.com/me/office-resources/upload/main')
    expect(wrapper.get('[data-testid="submission-wizard-step-release"] a').attributes('href'))
      .toBe('https://github.com/me/office-resources/releases/new?tag=v1.2.0')

    const submit = wrapper.get('[data-testid="submission-wizard-step-submit"] a').attributes('href') || ''
    const url = new URL(submit)
    expect(`${url.origin}${url.pathname}`).toBe('https://github.com/diceframe/diceframe-plugins/issues/new')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      template: 'add-plugin.yml',
      title: '[Plugin Submission] office-resources',
      plugin_id: 'office-resources',
      repository: 'https://github.com/me/office-resources',
    })
    // 向导自己不再额外下载 zip：文件在导出时已经拿过
    expect(downloads).toEqual(['office-resources-1.2.0.dfplugin'])
  })

  it('declares the content languages, defaulting to the UI language', async () => {
    const wrapper = await render('resource')
    const languages = wrapper.get('[data-testid="creator-export-languages"]')
    const boxes = languages.findAll('input[type="checkbox"]')
    expect(boxes).toHaveLength(5)
    // 默认勾上当前界面语言（测试环境是 zh-CN），别的语言默认不勾
    expect((boxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((boxes[1].element as HTMLInputElement).checked).toBe(false)

    await boxes[1].setValue(true)
    await wrapper.findAll('[data-testid="creator-export-books"] input[type="checkbox"]')[0].setValue(true)
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({ supported_locales: ['zh-CN', 'en'] })
  })

  it('never lets the declared languages fall back to an empty list', async () => {
    const wrapper = await render('resource')
    const boxes = wrapper.get('[data-testid="creator-export-languages"]').findAll('input[type="checkbox"]')
    await boxes[0].setValue(false)
    await flushPromises()
    // 空数组在商店里等于「未标注」，所以取消最后一个勾选要回弹
    expect((boxes[0].element as HTMLInputElement).checked).toBe(true)
  })

  it('exports character cards on their own and sends the image switches the author turned off', async () => {    const wrapper = await render('resource')
    const cardChecks = wrapper.findAll('[data-testid="creator-export-cards"] input[type="checkbox"]')
    // A card without `id` cannot be matched by the backend, so it is not offered.
    expect(cardChecks).toHaveLength(1)
    expect(wrapper.get('[data-testid="creator-export-cards"]').text()).toContain('米拉')
    await cardChecks[0].setValue(true)

    // Without a world there is no map to package.
    expect(wrapper.get('[data-testid="creator-export-include-map"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="creator-export-media"]').text()).toContain('先选择一个世界')
    await wrapper.get('[data-testid="creator-export-include-portraits"]').setValue(false)
    await wrapper.get('[data-testid="creator-export-include-scene-images"]').setValue(false)

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({
      card_ids: ['card-mira'], world_id: '', include_portraits: false, include_scene_images: false, include_map: false,
    })
    expect(mocks.api.mock.calls.some(([path]) => path === '/scene-images' || path === '/map-backgrounds')).toBe(false)
  })

  it('uploads replacement covers and map images only for what will be packaged', async () => {
    const wrapper = await render('resource')
    expect(wrapper.find('[data-testid="creator-export-world-cover"]').exists()).toBe(false)
    const [worldSelect, ruleSelect] = wrapper.findAll('select')
    await worldSelect.setValue('office')
    await ruleSelect.setValue('office-rules')
    await chooseFiles(wrapper, 'creator-export-world-cover', [image('office.png')])
    await chooseFiles(wrapper, 'creator-export-map-background', [image('floor.png')])
    await chooseFiles(wrapper, 'creator-export-map-icons', [image('pantry.png'), image('server-room.png')])
    expect(wrapper.get('[data-testid="creator-export-media"]').text()).toContain('已选择 2 个图标')

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    // Map icons are read with FileReader, which settles on a later task.
    await vi.waitFor(() => expect(mocks.apiBlob).toHaveBeenCalled())
    const body = exportBody()
    expect(body).toMatchObject({
      world_id: 'office', rule_id: 'office-rules', include_map: true,
      world_scene_image: { kind: 'upload', id: 'cover-1' }, map_background: { kind: 'upload', id: 'bg-1' },
    })
    // No rule cover was chosen, so only the world cover is uploaded.
    expect(body.rule_scene_image).toBeUndefined()
    expect(mocks.api.mock.calls.filter(([path]) => path === '/scene-images')).toHaveLength(1)
    expect((body.map_icons as Array<{ id: string; file_name: string }>).map(({ id, file_name }) => ({ id, file_name }))).toEqual([
      { id: 'pantry', file_name: 'pantry.png' }, { id: 'server-room', file_name: 'server-room.png' },
    ])
  })

  it('does not upload map images when the map switch is off', async () => {
    const wrapper = await render('resource')
    await wrapper.get('select').setValue('office')
    await chooseFiles(wrapper, 'creator-export-map-background', [image('floor.png')])
    await wrapper.get('[data-testid="creator-export-include-map"]').setValue(false)
    expect(wrapper.find('[data-testid="creator-export-map-background"]').exists()).toBe(false)

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({ include_map: false, map_icons: [] })
    expect(exportBody().map_background).toBeUndefined()
    expect(mocks.api.mock.calls.some(([path]) => path === '/map-backgrounds')).toBe(false)
  })

  it('exports the flat repository source and hands it to the submission wizard', async () => {
    const wrapper = await render('resource')
    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-format-source"]').setValue(true)

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({ flat: true, world_id: 'office' })
    expect(downloads).toEqual(['office-resources-0.1.0-src.zip'])

    // 源码包走作者自己的仓库：没有「上传 zip」的按钮，而是同一个发布向导
    expect(wrapper.find('[data-testid="creator-export-submit-to-hub"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="submission-wizard"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="submission-wizard-step-prepare"]').text()).toContain('LICENSE')
  })

  it('rejects package IDs the plugin host would refuse before exporting', async () => {
    const wrapper = await render('resource')
    await wrapper.get('input[placeholder]').setValue('office_resources')
    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    expect(wrapper.text()).toContain('包 ID 只能使用小写字母、数字和连字符。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()
  })

  it('requires at least one adventure for an adventure module', async () => {
    const wrapper = await render('adventure')
    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    expect(wrapper.text()).toContain('至少选择一个冒险。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()
  })

  it('previews runtime and world policy, and warns when a fixed world is not exported', async () => {
    const wrapper = await render('adventure')
    expect(wrapper.find('[data-testid="creator-export-preview"]').exists()).toBe(false)

    await wrapper.get('[data-testid="creator-export-adventures"] input[type="checkbox"]').setValue(true)
    const preview = wrapper.get('[data-testid="creator-export-preview"]')
    expect(preview.text()).toContain('core:generic ≥ 1')
    expect(preview.text()).toContain('固定世界')
    expect(preview.text()).toContain('GM')
    expect(wrapper.findAll('[data-testid="creator-export-warning"]').map(item => item.text()))
      .toEqual(['「夜班」固定使用世界「深夜办公室」，但这次没有一并导出；安装方没有这个世界时无法开局。'])

    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-cards"] input[type="checkbox"]').setValue(true)
    expect(wrapper.find('[data-testid="creator-export-warning"]').exists()).toBe(false)
    const updated = wrapper.get('[data-testid="creator-export-preview"]').text()
    expect(updated).toContain('没有发现会阻碍开局的问题。')
    expect(updated).toContain('世界 1 · 角色卡 1')

    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await flushPromises()
    expect(exportBody()).toMatchObject({
      plugin_id: 'night-module', world_id: 'office', card_ids: ['card-mira'],
      adventure_refs: [{ adventure_id: 'night-shift', source_kind: 'user', source_id: '' }],
    })
  })

  it('does not treat the context-free listing compatibility as an export problem', async () => {
    // GET /adventures without rule/world always reports ruleset_runtime_unavailable.
    mocks.api.mockImplementation(async (path: string) => path.startsWith('/adventures')
      ? { ok: true, adventures: [{ ...fixedAdventure, world_policy: 'portable', compatibility: 'incompatible', incompatibility_reasons: ['ruleset_runtime_unavailable'] }] }
      : respond(path))
    const wrapper = await render('adventure')
    await wrapper.get('[data-testid="creator-export-adventures"] input[type="checkbox"]').setValue(true)
    expect(wrapper.find('[data-testid="creator-export-warning"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="creator-export-preview"]').text()).toContain('没有发现会阻碍开局的问题。')
  })

  it('surfaces a rejected step as a toast, not just a banner above the fold', async () => {
    const wrapper = await render('resource')
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')

    expect(wrapper.get('.error-banner').text()).toContain('至少选择一个世界')
    // 横幅在顶部、按钮在底部，所以同一个问题必须同时弹 toast。
    expect(toast.error).toHaveBeenCalledWith('至少选择一个世界、规则、资料册或角色卡。')
    expect(mocks.apiBlob).not.toHaveBeenCalled()
  })

  it('packages the chosen cover, previews it, and drops it again', async () => {
    const wrapper = await render('resource')
    await wrapper.get('select').setValue('office')
    expect(wrapper.find('.creator-package-cover-image').exists()).toBe(false)

    await chooseFiles(wrapper, 'creator-export-cover', [new File(['RIFF0000WEBP'], 'art.webp', { type: 'image/webp' })])
    expect(wrapper.find('.creator-package-cover-image').exists()).toBe(true)
    expect(wrapper.get('[data-testid="creator-export-cover-block"]').text()).toContain('art.webp')

    await wrapper.get('[data-testid="creator-export-cover-clear"]').trigger('click')
    expect(wrapper.find('.creator-package-cover-image').exists()).toBe(false)

    await chooseFiles(wrapper, 'creator-export-cover', [new File(['RIFF0000WEBP'], 'art.webp', { type: 'image/webp' })])
    await wrapper.get('[data-testid="creator-export-submit"]').trigger('click')
    await vi.waitFor(() => expect(mocks.apiBlob).toHaveBeenCalled())
    const body = exportBody()
    expect(body.cover_filename).toBe('art.webp')
    expect(body.cover_data).toBe(btoa('RIFF0000WEBP'))
  })

  it('refuses a cover the backend would not accept and explains why', async () => {
    const wrapper = await render('resource')
    await chooseFiles(wrapper, 'creator-export-cover', [new File(['GIF89a'], 'art.gif', { type: 'image/gif' })])

    expect(toast.error).toHaveBeenCalledWith('封面只支持 webp / png / jpg，且不超过 512 KB。')
    expect(wrapper.find('.creator-package-cover-image').exists()).toBe(false)
  })

  it('keeps the books and cards pickers collapsible but open by default', async () => {
    const wrapper = await render('resource')
    const books = wrapper.get('[data-testid="creator-export-books"]')
    const cards = wrapper.get('[data-testid="creator-export-cards"]')

    // 默认展开，summary 上直接显示已选/总数
    expect(books.attributes('open')).toBeDefined()
    expect(cards.attributes('open')).toBeDefined()
    expect(books.get('summary').text()).toContain('0 / 1')
    expect(cards.get('summary').text()).toContain('0 / 1')

    // 收起只是隐藏，复选框仍在 DOM 里（有已选项时不会丢）
    expect(cards.find('input[type="checkbox"]').exists()).toBe(true)
    expect(wrapper.findAll('.creator-package-media-card')).toHaveLength(3)
  })

  it('previews what a resource pack will contain, without adventure-only rows', async () => {
    const wrapper = await render('resource')
    expect(wrapper.find('[data-testid="creator-export-preview"]').exists()).toBe(false)

    await wrapper.get('select').setValue('office')
    await wrapper.get('[data-testid="creator-export-cards"] input[type="checkbox"]').setValue(true)

    const preview = wrapper.get('[data-testid="creator-export-preview"]')
    expect(preview.text()).toContain('导出预览')
    // 资源包没有冒险，所以运行时 / 世界策略 / GM 隐私提示都不应出现。
    expect(preview.text()).not.toContain('规则运行时')
    expect(preview.text()).not.toContain('GM')
    const contents = wrapper.get('[data-testid="creator-export-contents"]').text()
    expect(contents).toContain('深夜办公室')
    expect(contents).toContain('米拉')
  })
})
