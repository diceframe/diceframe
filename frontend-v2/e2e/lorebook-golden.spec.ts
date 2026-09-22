import { expect, test } from './fixtures'

/**
 * Lorebook Golden — 真实链路，不 mock 任何 API。
 *
 *   Browser → real frontend → real backend → real SQLite → real migration
 *           → real resolver → real matcher/retrieval
 *
 * 数据来自 scripts/prepare_e2e_data.py 落的**真 v4 老库**（lorebook.db 停在 schema
 * v4），服务器启动时跑真实 migration。因此这条用例同时验收：
 *
 *   - v4 老库 → startup migration → primary world book 可见
 *   - Book 产品流：create / rename / enable-disable / delete / binding 管理
 *   - Import：detect → preview → 选择导入目标与 binding → confirm → persist
 *   - 真实 matcher：keyword → recursion → visibility（GM 看得到密档、玩家看不到）
 *   - export → reimport 往返
 *
 * 与 lorebook-mocked-flow.spec.ts 的分工：那条只证明 UI 对自己
 * mock 的忠诚度），这条是唯一的 Golden。
 */

// 与 scripts/prepare_e2e_data.py 的 E2E_LORE_WORLD_ID / E2E_LORE_GAME_KEY 一致。
const LORE_WORLD_ID = 'e2e_lore_golden'
const LORE_GAME_KEY = 'web|e2e-lore-golden|web_bot'

/**
 * 导入 fixture 用 lorebook_v3：它能带 book 级 recursive_scanning，因此 recursion
 * 与 secondary filter 都能在真实 import → resolver → matcher 链上被验收，而不是
 * 靠测试自己断言一个没人执行过的配置。
 */
const IMPORT_FIXTURE = JSON.stringify({
  spec: 'lorebook_v3',
  data: {
    lorebook: {
      name: 'Golden Imported Book',
      recursive_scanning: true,
      entries: [
        { id: 'imp-bell', name: '潮汐钟', keys: ['潮汐钟'], content: '潮汐钟的钟摆连着沉船账本。' },
        { id: 'imp-ledger', name: '沉船账本', keys: ['沉船账本'], content: '账本记着走私航线。' },
        {
          id: 'imp-watch', name: '巡夜人', keys: ['潮汐钟'], secondary_keys: ['满月'],
          selective_logic: 'and_all', content: '满月之夜钟声会引来巡夜人。',
        },
      ],
    },
  },
})

/**
 * SillyTavern World Info fixture。第二个条目带一个只有 JavaScript 才合法的正则
 * （`(?<name>…)` 命名分组），用来在真实浏览器链路上验收 §7：pattern 原样保留、
 * 预览给出 unsupported warning，且不经过第二个 runtime 求值。
 */
const ST_FIXTURE = JSON.stringify({
  name: 'Golden ST World',
  entries: [
    { uid: 1, key: ['灯塔'], content: '灯塔的灯油来自北方。' },
    { uid: 2, key: ['灯塔'], keysecondary: ['风暴'], content: '风暴夜灯塔会熄灭。' },
    { uid: 3, key: ['(?<name>harbor)'], content: 'JS-only 正则条目。', useRegex: true },
  ],
})

/**
 * Character Card v3 fixture，带内嵌 character_book。验收 §8：内嵌世界书经真实的
 * `parse_character_card_document → lorebook_v3 adapter → canonical commit` 落库，
 * 并在预览里明确显示这是谁的角色世界书。
 */
const CCV3_FIXTURE = JSON.stringify({
  spec: 'chara_card_v3',
  spec_version: '3.0',
  data: {
    name: 'Golden Keeper',
    description: '灯塔守夜人',
    personality: '沉默',
    character_book: {
      name: 'Keeper Lore',
      entries: [
        { id: 'keeper-1', name: '守夜人日志', keys: ['守夜人'], content: '日志记着灯塔的班次。' },
      ],
    },
  },
})

test.describe('Lorebook Golden (real chain)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Golden runs on the CI browser only')

  test('migrated v4 lore, book management, binding-aware import and real activation', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'Golden uses the desktop inspector layout')

    const failures: string[] = []
    page.on('response', response => {
      const url = response.url()
      if (url.includes('/api/') && response.status() >= 500) failures.push(`${response.status()} ${url}`)
    })

    // Activation Inspector 走真实 retriever，需要一个绑定到该世界的存档。
    await page.addInitScript(gameKey => {
      localStorage.setItem('lore_inspector_open', '1')
      localStorage.setItem('currentGame', gameKey)
    }, LORE_GAME_KEY)

    // ---- 1. v4 老库经真实 migration 后应当出现在产品界面上 -------------------
    await page.goto('/#/lorebook')
    await expect(page.locator('.lorebook-page')).toBeVisible()

    const contextBar = page.locator('.lore-context-bar')
    const worldSelect = contextBar.locator('select').nth(1)
    const bookSelect = contextBar.locator('select').nth(2)
    const bookMenu = contextBar.locator('details.lore-menu').nth(1)
    await worldSelect.selectOption(LORE_WORLD_ID)

    // 迁移产物：主世界书是默认当前 Book，信息行表达 primary + 使用范围。
    await expect(bookSelect).toBeVisible()
    const bookMeta = contextBar.locator('.lore-book-meta')
    await expect(bookMeta).toContainText('主世界书')
    await expect(bookMeta).toContainText('使用范围：当前世界')
    // 真实条目来自迁移后的 SQLite，不是测试 fabricate 的 JSON。
    await expect(page.locator('.lore-row', { hasText: '旧城门' }).first()).toBeVisible()

    // 桌面布局 contract：无 Books 侧栏，主工作区占据主要宽度，检查器不越出视口，
    // 页面不得出现横向滚动。
    const layout = await page.evaluate(() => {
      const width = (selector: string) =>
        document.querySelector(selector)?.getBoundingClientRect().width ?? 0
      const inspector = document.querySelector('.lore-perspective-inspector')
      return {
        workspace: width('.lorebook-workspace'),
        inspectorRight: inspector ? inspector.getBoundingClientRect().right : 0,
        viewport: document.documentElement.clientWidth,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }
    })
    expect(layout.workspace).toBeGreaterThan(600)
    expect(layout.inspectorRight).toBeLessThanOrEqual(layout.viewport)
    expect(layout.pageOverflow).toBe(false)

    // ---- 2. 真实 matcher：keyword → recursion，GM 视角看得到密档 ------------
    const activationInput = page.locator('.lore-activation-input')
    await activationInput.fill('我去旧城门看看')
    await page.getByRole('button', { name: '预览触发', exact: true }).click()
    const trace = page.locator('.lore-activation-trace')
    await expect(trace).toContainText('legacy-gate')
    await expect(trace).toContainText('legacy-secret')

    // ---- 3. 玩家视角：hidden 条目连一行都不该出现 ---------------------------
    await page.getByRole('button', { name: '全队', exact: true }).click()
    await page.getByRole('button', { name: '预览触发', exact: true }).click()
    await expect(trace).toContainText('legacy-gate')
    await expect(trace).not.toContainText('legacy-secret')
    await page.getByRole('button', { name: 'GM 全知', exact: true }).click()

    // ---- 4. Book 产品流：新建 → 重命名 → 停用/启用 -------------------------
    page.once('dialog', dialog => dialog.accept('Golden Extra Book'))
    await contextBar.getByRole('button', { name: '新建世界书', exact: true }).click()
    // 新建后自动选中：book selector 立即指向它。
    await expect(bookSelect.locator('option', { hasText: 'Golden Extra Book' })).toHaveCount(1)

    page.once('dialog', dialog => dialog.accept('Golden Renamed Book'))
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '重命名', exact: true }).click()
    await expect(bookSelect.locator('option', { hasText: 'Golden Renamed Book' })).toHaveCount(1)

    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '停用', exact: true }).click()
    await expect(contextBar.locator('.lore-book-state')).toContainText('已停用')
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '启用', exact: true }).click()
    await expect(contextBar.locator('.lore-book-state')).toContainText('启用中')

    // 点击菜单外任意位置应自动收起下拉菜单。
    await bookMenu.locator('summary').click()
    await expect(bookMenu).toHaveAttribute('open')
    await page.locator('.view-title h1').click()
    await expect(bookMenu).not.toHaveAttribute('open')

    // ---- 5. 使用范围管理：新增一条 world 范围并移除 ------------------------
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '使用范围', exact: true }).click()
    const bindingsDialog = page.getByRole('dialog', { name: '使用范围管理' })
    await expect(bindingsDialog).toBeVisible()
    await bindingsDialog.getByRole('button', { name: '新增', exact: true }).click()
    await expect(bindingsDialog.locator('.lore-binding-row')).toHaveCount(1)
    await expect(bindingsDialog.locator('.lore-binding-row')).toContainText('world')
    await bindingsDialog.getByRole('button', { name: /^移除/ }).click()
    await expect(bindingsDialog.locator('.lore-binding-row')).toHaveCount(0)
    await bindingsDialog.getByRole('button', { name: '关闭', exact: true }).click()

    // ---- 6. Import：detect → preview → 选目标/binding → confirm → persist --
    await page.locator('input[type=file]').setInputFiles({
      name: 'golden-import.json', mimeType: 'application/json', buffer: Buffer.from(IMPORT_FIXTURE),
    })
    const importDialog = page.getByRole('dialog', { name: '导入世界书' })
    // 格式由真实后端 detect，不是前端猜的。
    await expect(importDialog).toContainText('lorebook_v3')
    // 默认是「新建独立 Book」，不会隐式写进主世界书。
    await expect(importDialog.locator('.lore-import-dialog__primary-warning')).toHaveCount(0)
    await importDialog.locator('.lore-import-dialog__binding input[value=world]').check()
    await importDialog.getByRole('button', { name: '导入', exact: true }).click()
    await expect(importDialog).toBeHidden()

    // 导入结果来自真实 SQLite：新 Book 出现，条目可读。
    await expect(page.locator('.lore-row', { hasText: '潮汐钟' }).first()).toBeVisible()

    // ---- 6b. 真实 matcher：recursion + secondary filter ---------------------
    // 「潮汐钟」直接命中，其正文提到「沉船账本」→ recursion 带出子条目。
    await activationInput.fill('我敲响潮汐钟')
    await page.getByRole('button', { name: '预览触发', exact: true }).click()
    await expect(trace).toContainText('递归带入')
    // trace 会列出作用域内**每一条**条目（含 omitted），所以要数真正进入结果的行。
    // 存档的 scene 本身也是检索锚点，因此这里只断言「多了一条」，不钉死绝对值。
    const included = trace.locator('li', { hasText: '已触发' })
    const withoutMoon = await included.count()
    expect(withoutMoon).toBeGreaterThan(0)
    // 「巡夜人」的 secondary key 是「满月」：只有提到满月才应额外激活它。
    await activationInput.fill('满月之夜我敲响潮汐钟')
    await page.getByRole('button', { name: '预览触发', exact: true }).click()
    await expect(included).toHaveCount(withoutMoon + 1)

    // ---- 7. 导入主世界书必须显式提示（不能隐式发生）------------------------
    await page.locator('input[type=file]').setInputFiles({
      name: 'golden-import-2.json', mimeType: 'application/json', buffer: Buffer.from(IMPORT_FIXTURE),
    })
    const secondDialog = page.getByRole('dialog', { name: '导入世界书' })
    await secondDialog.locator('.lore-import-dialog__target input[value=existing]').check()
    await secondDialog.locator('.lore-import-dialog__book').selectOption(`world:${LORE_WORLD_ID}`)
    await expect(secondDialog.locator('.lore-import-dialog__primary-warning'))
      .toContainText('将导入当前世界主世界书')
    await secondDialog.getByRole('button', { name: '取消', exact: true }).click()

    // ---- 8. export → reimport 往返（真实 exporter / importer）-------------
    const download = page.waitForEvent('download')
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '导出', exact: true }).click()
    const exported = await download
    const stream = await exported.createReadStream()
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(Buffer.from(chunk))
    const exportedJson = Buffer.concat(chunks).toString('utf-8')
    expect(JSON.parse(exportedJson)).toBeTruthy()

    await page.locator('input[type=file]').setInputFiles({
      name: 'golden-roundtrip.json', mimeType: 'application/json', buffer: Buffer.from(exportedJson),
    })
    const roundTripDialog = page.getByRole('dialog', { name: '导入世界书' })
    await expect(roundTripDialog).toContainText('lorebook_v3')
    await roundTripDialog.locator('.lore-import-dialog__binding input[value=none]').check()
    await roundTripDialog.getByRole('button', { name: '导入', exact: true }).click()
    await expect(roundTripDialog).toBeHidden()

    // ---- 9. 删除非主世界书；主世界书没有删除入口 --------------------------
    await bookSelect.selectOption({ label: 'Golden Lore World' })
    await bookMenu.locator('summary').click()
    await expect(bookMenu.getByRole('button', { name: '删除', exact: true })).toHaveCount(0)
    await bookMenu.locator('summary').click()

    await bookSelect.selectOption({ label: 'Golden Renamed Book' })
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '删除', exact: true }).click()
    await page.getByRole('button', { name: '删除世界书', exact: true }).click()
    await expect(bookSelect.locator('option', { hasText: 'Golden Renamed Book' })).toHaveCount(0)

    expect(failures, `backend 5xx during the golden run:\n${failures.join('\n')}`).toEqual([])
  })

  /**
   * §13 browser fixture：SillyTavern 与 Character Card v3 两种外部格式，同样走
   * 真实后端 detect → preview → commit，不 mock 任何 API。
   */
  test('SillyTavern and Character Card v3 fixtures import through the real chain', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'Golden uses the desktop inspector layout')

    const failures: string[] = []
    page.on('response', response => {
      const url = response.url()
      if (url.includes('/api/') && response.status() >= 500) failures.push(`${response.status()} ${url}`)
    })

    await page.goto('/#/lorebook')
    await expect(page.locator('.lorebook-page')).toBeVisible()
    await page.locator('.lore-context-bar select').nth(1).selectOption(LORE_WORLD_ID)

    // ---- SillyTavern World Info ------------------------------------------
    await page.locator('input[type=file]').setInputFiles({
      name: 'golden-st.json', mimeType: 'application/json', buffer: Buffer.from(ST_FIXTURE),
    })
    const stDialog = page.getByRole('dialog', { name: '导入世界书' })
    // 格式由真实后端 detect，不是前端猜的。
    await expect(stDialog).toContainText('sillytavern')
    // §7：JS-only 正则必须被报告为 unsupported，而不是静默执行。
    await expect(stDialog.locator('.lore-import-dialog__warnings')).toContainText('unsupported regex')
    await stDialog.locator('.lore-import-dialog__binding input[value=none]').check()
    await stDialog.getByRole('button', { name: '导入', exact: true }).click()
    await expect(stDialog).toBeHidden()
    // 真实 SQLite：条目与它原样保留的正则 key 都落了库。
    await expect(page.locator('.lore-row', { hasText: '灯塔' }).first()).toBeVisible()

    // ---- Character Card v3（内嵌 character_book）-------------------------
    await page.locator('input[type=file]').setInputFiles({
      name: 'golden-ccv3.json', mimeType: 'application/json', buffer: Buffer.from(CCV3_FIXTURE),
    })
    const ccDialog = page.getByRole('dialog', { name: '导入世界书' })
    await expect(ccDialog).toContainText('character_card_v3')
    // 产品流必须明确告诉用户这是谁的内嵌角色世界书。
    await expect(ccDialog.locator('.lore-import-dialog__character-summary')).toContainText('Golden Keeper')
    await ccDialog.locator('.lore-import-dialog__binding input[value=none]').check()
    await ccDialog.getByRole('button', { name: '导入', exact: true }).click()
    await expect(ccDialog).toBeHidden()
    await expect(page.locator('.lore-row', { hasText: '守夜人日志' }).first()).toBeVisible()

    expect(failures, `backend 5xx during the golden run:\n${failures.join('\n')}`).toEqual([])
  })

  /**
   * 内容语言选择器必须覆盖全局四个 Locale（zh-CN / en / ja / de），且与 UI 语言独立。
   * fixture 里备了 ja / de 标记的 world，切换内容语言后 world selector 按 filterByContentLanguage 过滤。
   */
  test('content language selector covers all four locales and filters worlds', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'Uses the desktop inspector layout')

    await page.goto('/#/lorebook')
    await expect(page.locator('.lorebook-page')).toBeVisible()

    const languageSelect = page.locator('.lore-language-filter select')
    const worldSelect = page.locator('.lore-context-bar select').nth(1)

    // 语言名统一走 i18n 自身名称契约，值与全局 Locale 一致。
    await expect(languageSelect.locator('option')).toHaveText(['简体中文', 'English', '日本語', 'Deutsch'])

    await languageSelect.selectOption('ja')
    const jaOption = worldSelect.locator('option', { hasText: 'ゴールデン・ロア・ワールド' })
    await expect(jaOption).toHaveCount(1)
    await worldSelect.selectOption('e2e_lore_golden_ja')

    await languageSelect.selectOption('de')
    await expect(worldSelect.locator('option', { hasText: 'Golden Lore Welt' })).toHaveCount(1)

    // 回到 zh-CN：legacy 未标记内容仍按 zh-CN 归类，不被语言切换破坏。
    await languageSelect.selectOption('zh-CN')
    await expect(worldSelect.locator('option', { hasText: 'Golden Lore World' })).toHaveCount(1)
  })
})
