import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'
import { prepareAuthenticatedContext, seatTakeoverPath } from './support'

const GAME_KEY = 'web|e2e-room|web_bot'
const PLAYER = 'e2e-player'

async function openGame(page: Page, query = '', openGmControls = true, path?: string) {
  await page.goto(path || `/#/play?game=${encodeURIComponent(GAME_KEY)}${query}`)
  await expect(page.locator('.play-page')).toBeVisible()
  if (openGmControls) {
    const gmToggle = page.getByTestId('play-gm-toggle')
    await expect(gmToggle).toBeVisible()
    await expect(gmToggle).toHaveAttribute('aria-expanded', 'false')
    await gmToggle.click()
    await expect(gmToggle).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByTestId('gm-workspace')).toBeVisible()
  }
}

test.describe('UI redesign browser acceptance', () => {
  test('desktop table keeps readable narration beside character and party tools', async ({ page }) => {
    await page.setViewportSize({ width: 1179, height: 746 })
    await openGame(page)
    await expect(page.getByTestId('gm-workspace')).toBeVisible()
    await expect(page.getByTestId('timeline')).toBeVisible()
    await expect(page.locator('.play-sidebar-drawer')).toHaveCount(1)
    await expect(page.getByTestId('play-character-dock')).toBeVisible()
    const geometry = await page.evaluate(() => {
      const main = document.querySelector('.play-main')!.getBoundingClientRect()
      const rail = document.querySelector('.play-control-rail')!.getBoundingClientRect()
      const timeline = document.querySelector('.timeline')!.getBoundingClientRect()
      const railStyle = getComputedStyle(document.querySelector<HTMLElement>('.play-control-rail')!)
      return {
        left: main.left,
        width: main.width,
        railWidth: rail.width,
        railRight: rail.right,
        railPosition: railStyle.position,
        timelineHeight: timeline.height,
        viewport: innerWidth,
      }
    })
    expect(geometry.left).toBeGreaterThan(200)
    expect(geometry.width).toBeGreaterThan(geometry.viewport / 2)
    expect(geometry.railPosition).toBe('fixed')
    expect(geometry.railWidth).toBeGreaterThan(0)
    expect(geometry.railWidth).toBeLessThanOrEqual(410)
    expect(Math.abs(geometry.railRight - geometry.viewport)).toBeLessThanOrEqual(2)
    expect(geometry.timelineHeight).toBeGreaterThan(300)
    await page.getByTestId('play-gm-drawer').locator('.rail-toggle').click()
    await expect(page.getByTestId('play-gm-toggle')).toHaveAttribute('aria-expanded', 'false')
    await page.getByTestId('play-tools-toggle').click()
    await page.getByTestId('play-tools-panel').getByRole('button', { name: '共享角色卡库', exact: true }).click()
    const cards = page.locator('.modal').filter({ has: page.getByRole('heading', { name: '共享角色卡库', exact: true }) })
    await expect(cards).toBeVisible()
    await expect(page.getByTestId('play-tools-panel')).toHaveCount(0)
    await cards.getByRole('button', { name: '×', exact: true }).click()
    await page.locator('.play-toolbar').getByRole('button', { name: '角色', exact: true }).click()
    await expect(page.locator('.play-sidebar-drawer .game-sidebar')).toBeVisible()
    await expect(page.locator('.play-sidebar-drawer')).toContainText('技能')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('play-character-dock')).toBeVisible()
    await expect(page.getByTestId('play-gm-toggle')).toHaveAttribute('aria-expanded', 'false')
    await page.getByTestId('play-gm-toggle').click()
    await expect(page.getByTestId('gm-workspace')).toBeVisible()
    const workspace = page.getByTestId('gm-workspace')
    await workspace.getByRole('tab', { name: '场景', exact: true }).click()
    const sceneTools = await workspace.locator('.gm-scene-group').boundingBox()
    expect(sceneTools!.height, 'scene buttons must not stretch across the workstation').toBeLessThan(200)
    await workspace.getByRole('tab', { name: '规则', exact: true }).click()
    const health = await workspace.locator('.health-panel').boundingBox()
    expect(health!.height, 'status panel must keep its natural content height').toBeLessThan(180)
    await expect(workspace.getByRole('tab', { selected: true })).toHaveCount(1)
  })

  test('GM workspace exposes five tabs and preserves command drafts across tabs', async ({ page }, testInfo) => {
    await openGame(page)
    const workspace = page.getByTestId('gm-workspace')
    await expect(workspace).toBeVisible()
    await expect(workspace.getByRole('tab')).toHaveCount(5)
    for (const name of ['待办', '队伍', '场景', '规则', '设置']) {
      await expect(workspace.getByRole('tab', { name, exact: true })).toBeVisible()
    }

    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    const command = workspace.locator('input[placeholder="GM 指令 / 救档操作"]')
    await expect(command).toBeVisible()
    await command.fill('inspect the gate draft')
    await workspace.getByRole('tab', { name: '队伍', exact: true }).click()
    await expect(workspace.getByRole('tabpanel', { name: '队伍' })).toContainText('玩家')
    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    await expect(command).toHaveValue('inspect the gate draft')

    await workspace.getByRole('tab', { name: '规则', exact: true }).click()
    await expect(workspace.getByRole('tabpanel', { name: '规则' }).getByTestId('manual-rolls')).toHaveCount(0)
    await expect(page.getByTestId('play-party-rail').getByTestId('manual-roll-create')).toHaveCount(1)
    await page.screenshot({ path: testInfo.outputPath('gm-workspace.png'), fullPage: true })
  })

  test('player view has no GM workspace or GM toolbar', async ({ browser, request }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
    const page = await context.newPage()
    try {
      await openGame(page, '', false, await seatTakeoverPath(request, GAME_KEY, PLAYER))
      await expect(page.getByTestId('gm-workspace')).toHaveCount(0)
      await expect(page.locator('.gm-toolbar')).toHaveCount(0)
      await expect(page.locator('.composer')).toBeVisible()
    } finally {
      await context.close()
    }
  })

  test('seeded module data distinguishes playable adventure content from a resource pack', async ({ page }, testInfo) => {
    await page.goto('/#/modules')
    const installed = page.getByTestId('modules-installed')
    await expect(installed).toBeVisible()
    const module = installed.locator('a.module-card').filter({ hasText: 'E2E Module' })
    await expect(module).toBeVisible()
    // The shared seed intentionally leaves this package disabled. Its real API
    // projection still exposes the declared adventure inventory, while launch
    // remains unavailable until the package is enabled.
    await expect(module).toContainText('冒险模组')
    await expect(module).toContainText('包含 1 个冒险')
    await expect(module).toContainText('已禁用，启用后')
    // Marketplace readiness may wait for the fake Hub's bounded network timeout;
    // retain the enabled contract without racing the initial request.
    await expect(page.getByTestId('modules-online').getByRole('button', { name: '搜索在线内容包', exact: true })).toBeEnabled({ timeout: 45_000 })
    await page.screenshot({ path: testInfo.outputPath('content-pack-library.png'), fullPage: true })

    await page.goto('/#/modules/e2e-module')
    await expect(page.getByTestId('module-adventures')).toContainText('已安装冒险')
    await expect(page.getByTestId('module-adventures')).toContainText('plugin:module_quest')
    await expect(page.getByTestId('module-adventures').getByRole('button', { name: '用此冒险创建游戏', exact: true })).toBeDisabled()
    await page.screenshot({ path: testInfo.outputPath('content-pack-detail.png'), fullPage: true })
  })

  test('GM and player surfaces stay contained across viewports and both themes', async ({ browser, request }, testInfo) => {
    test.setTimeout(120_000)
    const playerPath = await seatTakeoverPath(request, GAME_KEY, PLAYER)
    // Resize the same authenticated session to verify responsive behavior.
    // Creating a new identity at every width only stresses the shared write
    // budget and does not improve the layout assertions.
    for (const theme of ['dark', 'light']) {
      for (const role of ['gm', 'player']) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 844 }, locale: 'zh-CN' })
        if (role === 'gm') await prepareAuthenticatedContext(context, request)
        await context.addInitScript(value => {
          localStorage.setItem('diceframe_mode_v2', value)
          localStorage.setItem('diceframe_locale', 'zh-CN')
        }, theme)
        const page = await context.newPage()
        const errors: string[] = []
        page.on('pageerror', error => errors.push(error.message))
        try {
          await openGame(page, '', false, role === 'player' ? playerPath : undefined)
          await expect(page.locator('html')).toHaveAttribute('data-mode', theme)
          if (role === 'player') await expect(page.locator('.gm-toolbar')).toHaveCount(0)
          for (const width of [1440, 1024, 736, 390, 320]) {
            await page.setViewportSize({ width, height: 844 })
            await expect(page.locator('.composer')).toBeVisible()
            if (width <= 800) {
              const stage = await page.locator('.scene-strip').boundingBox()
              expect(stage!.height, `${role} compact stage at ${width}px ${theme}`).toBeLessThanOrEqual(90)
              await expect(page.locator('.scene-strip .scene-chips')).toBeHidden()
            }
            const geometry = await page.evaluate(() => {
              const composer = document.querySelector<HTMLElement>('.composer')!.getBoundingClientRect()
              return { overflow: document.documentElement.scrollWidth - innerWidth, left: composer.left, right: composer.right, bottom: composer.bottom, height: innerHeight }
            })
            expect(geometry.overflow, `${role} overflow at ${width}px ${theme}`).toBeLessThanOrEqual(1)
            expect(geometry.left).toBeGreaterThanOrEqual(-1)
            expect(geometry.right).toBeLessThanOrEqual(width + 1)
            expect(geometry.bottom).toBeLessThanOrEqual(geometry.height + 1)
            if (width <= 800) expect(geometry.bottom).toBeGreaterThanOrEqual(geometry.height - 12)
            expect(errors).toEqual([])
            await page.screenshot({ path: testInfo.outputPath(`play-${role}-${width}-${theme}.png`), fullPage: true })
          }
        } finally { await context.close() }
      }
    }
  })

  test('Lorebook shows a static single Book, counts multiple Books accurately, and surfaces create errors', async ({ page }, testInfo) => {
    // The play/module checks above use the prepared real E2E data. This bounded route
    // fixture only supplies deterministic one-book/two-book states and a failed create
    // response so the browser can verify the new Book presentation without mutating a
    // shared database.
    let books = [{ id: 'world:ui-world', name: 'UI World Book', primary: true, scope: 'world', enabled: true, entry_count: 2 }]
    await page.route('**/api/**', async route => {
      const request = route.request()
      const url = new URL(request.url())
      const path = decodeURIComponent(url.pathname.replace(/^\/api/, ''))
      if (request.method() === 'GET' && path === '/worlds') {
        await route.fulfill({ json: { worlds: [{ id: 'ui-world', name: 'UI World', language: 'zh-CN', entry_count: 2 }] } })
        return
      }
      if (request.method() === 'GET' && path === '/lorebooks') {
        await route.fulfill({ json: { books } })
        return
      }
      if (request.method() === 'GET' && path.endsWith('/entries')) {
        await route.fulfill({ json: { entries: [{ id: 'one', name: 'One', content: 'one' }, { id: 'two', name: 'Two', content: 'two' }] } })
        return
      }
      if (request.method() === 'POST' && path === '/lorebooks') {
        await route.fulfill({ status: 409, json: { ok: false, error: 'book creation rejected by test fixture' } })
        return
      }
      await route.continue()
    })

    await page.goto('/#/lorebook')
    await expect(page.locator('.lorebook-page')).toBeVisible()
    await page.getByTestId('lore-context-crumbs').click()
    await expect(page.locator('.lore-book-static')).toContainText('UI World Book')
    await expect(page.locator('.lore-context-row--book .lore-book-meta')).toContainText('2 条')
    await expect(page.locator('.lore-context-bar .lore-context-select')).toHaveCount(1)

    books = [
      ...books,
      { id: 'book:second', name: 'Second Book', primary: false, scope: 'global', enabled: true, entry_count: 1 },
    ]
    await page.reload()
    await page.getByTestId('lore-context-crumbs').click()
    const bookSelect = page.locator('.lore-context-bar .lore-context-select').nth(1)
    await expect(bookSelect).toBeVisible()
    await expect(bookSelect.locator('option')).toHaveCount(2)
    await expect(bookSelect).toContainText('UI World Book')
    await expect(bookSelect).toContainText('2 条')
    await expect(bookSelect).toContainText('Second Book')
    await expect(bookSelect).toContainText('1 条')
    await page.screenshot({ path: testInfo.outputPath('world-lorebooks.png'), fullPage: true })

    await page.getByRole('button', { name: '新建资料册', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '新建资料册' })
    await expect(dialog).toBeVisible()
    await dialog.locator('.lore-book-create-name').fill('Rejected Book')
    await dialog.getByRole('button', { name: '创建资料册', exact: true }).click()
    await expect(dialog.getByRole('alert')).toContainText('book creation rejected by test fixture')
  })

  test('Book scope modal stays visible and contained on desktop and mobile', async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem('currentGame', 'web|e2e-lore-golden|web_bot'))
    await page.goto('/#/lorebook')
    await expect(page.locator('.lorebook-page')).toBeVisible()
    await page.getByTestId('lore-context-crumbs').click()
    const contextBar = page.locator('.lore-context-bar')
    await contextBar.locator('select').nth(1).selectOption('e2e_lore_golden')
    const bookMenu = contextBar.locator('details.lore-menu').filter({ hasText: '资料册操作' })
    await bookMenu.locator('summary').click()
    await bookMenu.getByRole('button', { name: '使用范围', exact: true }).click()
    const dialog = page.locator('.modal .dialog.lore-bindings-dialog')
    await expect(dialog).toBeVisible()
    const geometry = await dialog.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      const overlay = element.closest('.modal')?.getBoundingClientRect()
      return { left: rect.left, right: rect.right, top: rect.top, overlayTop: overlay?.top ?? -1, overflow: document.documentElement.scrollWidth > innerWidth }
    })
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(page.viewportSize()!.width)
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.overlayTop).toBe(0)
    expect(geometry.overflow).toBe(false)
    await page.screenshot({ path: testInfo.outputPath('lorebook-scope-modal.png'), fullPage: true })
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  })
})
