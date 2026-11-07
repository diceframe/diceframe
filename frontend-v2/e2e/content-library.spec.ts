import { expect, test } from './fixtures'
import { accessToken } from './support'

test('library has usable dark/light surfaces and preserves all content tools on desktop and phone', async ({ page }, info) => {
  if (info.project.name === 'desktop') await page.setViewportSize({ width: 1366, height: 900 })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/#/library')
  if (info.project.name === 'desktop') {
    await expect(page.getByTestId('library-adventure').first()).toBeVisible()
    const filterBoxes = await page.locator('.library-filters > .library-search, .library-filters > select, .library-filters > .library-refresh').evaluateAll(elements => elements.map(element => {
      const box = element.getBoundingClientRect()
      return { left: box.left, top: box.top, width: box.width, height: box.height }
    }))
    expect(filterBoxes).toHaveLength(6)
    expect(Math.max(...filterBoxes.map(box => box.top)) - Math.min(...filterBoxes.map(box => box.top))).toBeLessThanOrEqual(1)
    expect(Math.min(...filterBoxes.map(box => box.width))).toBeGreaterThan(100)
  }
  let darkSurface = ''
  for (const light of [false, true]) {
    await page.evaluate(value => localStorage.setItem('diceframe_mode_v2', value ? 'light' : 'dark'), light)
    await page.reload()
    const card = page.getByTestId('library-adventure').first()
    await expect(card).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-mode', light ? 'light' : 'dark')
    await expect(page.getByTestId('library-start').first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    const surface = await card.evaluate(element => getComputedStyle(element).backgroundColor)
    expect(surface).not.toBe('rgba(0, 0, 0, 0)')
    if (light) expect(surface).not.toBe(darkSurface)
    else darkSurface = surface
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: info.outputPath(`library-${light ? 'light' : 'dark'}.png`), fullPage: true })
    await page.screenshot({ path: info.outputPath(`library-${light ? 'light' : 'dark'}-viewport.png`) })
    await card.getByTestId('library-details').click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect(await page.getByRole('dialog').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
  }
  const viewport = page.viewportSize()!
  for (const width of [320, 900, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), `width=${width}`).toBeLessThanOrEqual(1)
  }
  await page.setViewportSize(viewport)
  await page.getByRole('link', { name: '管理内容包', exact: true }).click()
  await expect(page.getByRole('heading', { name: '内容包', exact: true })).toBeVisible()
  for (const id of ['modules-installed', 'modules-local-import', 'modules-online']) await expect(page.getByTestId(id)).toBeVisible()
  await page.getByRole('link', { name: '返回内容库', exact: true }).click()
  await expect(page.getByTestId('content-library')).toBeVisible()
  await page.getByTestId('library-worlds').getByRole('link', { name: '打开', exact: true }).click()
  await expect(page.locator('.worlds-grid')).toBeVisible()
  expect(errors).toEqual([])
})

test('an installed-source adventure reaches a real new game with its exact binding', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const enabled = await request.post('/api/plugins/e2e-module/start', { headers })
  expect(enabled.ok()).toBe(true)
  let createdGame = ''
  try {
    await page.goto('/#/library')
    await page.getByTestId('library-source-filter').selectOption('plugin')
    const card = page.getByTestId('library-adventure').filter({ hasText: 'e2e-module' }).first()
    await expect(card).toBeVisible()
    await card.getByTestId('library-start').click()
    await expect(page).toHaveURL(/adventure_id=plugin.*module_id=e2e-module/)
    await expect(page.getByTestId('create-adventure-summary')).toContainText('e2e-module')
    const selections = page.locator('.create-adventure-select-row select')
    await expect(selections.first()).toHaveValue('e2e-module')
    await expect(selections.last()).toHaveValue(JSON.stringify(['plugin', 'e2e-module', 'plugin:module_quest']))
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: info.outputPath('library-create-summary.png'), fullPage: true })
    const next = page.locator('.create-actions .primary')
    await expect(next).toBeEnabled()
    await next.click()
    await next.click()
    await page.locator('.create-character-actions .primary').click()
    await page.locator('.preset-card').first().click()
    await page.locator('.name-field input').fill(`内容库冒险者 ${info.project.name}`)
    await page.locator('.quick-actions .primary').click()
    await expect(page.locator('.create-character-card')).toHaveCount(1)
    await next.click()
    const response = page.waitForResponse(result => result.url().endsWith('/api/games/create') && result.request().method() === 'POST')
    await page.getByRole('button', { name: /创建并进入/ }).click()
    const created = await (await response).json()
    createdGame = String(created.game_key || '')
    expect(created.ok, JSON.stringify(created)).toBe(true)
    await expect(page).toHaveURL(/#\/play\?game=web/, { timeout: 20_000 })
    const projection = await (await request.get(`/api/games/${encodeURIComponent(created.game_key)}/adventure`, { headers })).json()
    expect(projection.adventure.available).toBe(true)
    expect(projection.adventure.binding.source_kind).toBe('plugin')
    expect(projection.adventure.binding.source_id).toBe('e2e-module')
    expect(projection.adventure.binding.adventure_id).toBe('plugin:module_quest')
    // This save binds the package; package guards must still prevent disabling it.
    const stopped = await request.post('/api/plugins/e2e-module/stop', { headers })
    expect(stopped.status()).toBe(409)
  } finally {
    // Keep the shared test fixture usable by the next viewport/project.
    if (createdGame) {
      const removed = await request.delete(`/api/games/${encodeURIComponent(createdGame)}`, { headers })
      expect(removed.ok()).toBe(true)
    }
    const stopped = await request.post('/api/plugins/e2e-module/stop', { headers })
    expect(stopped.ok()).toBe(true)
  }
})
