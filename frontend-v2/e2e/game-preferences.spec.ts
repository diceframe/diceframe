import { expect, test } from './fixtures'
import { accessToken, PLAY_READY_TIMEOUT, seatTakeoverPath } from './support'

const cardScreenshotStyle = '.app-header, .content-workspace-rail, .settings-nav, .mobile-bottom-nav, [data-testid="assistant-shell-fab"] { visibility: hidden !important; }'

test('players choose their own suggestion visibility while the GM only sets the initial default', async ({ page, request, browser }, info) => {
  const game = 'web|e2e-room|web_bot'
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const endpoint = `/api/games/${encodeURIComponent(game)}/preferences`
  const before = (await (await request.get(endpoint, { headers })).json()).preferences.quick_actions_enabled
  const defaults = (await (await request.get('/api/config', { headers })).json()).game_defaults
  const context = await browser.newContext({ viewport: info.project.name === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 900 } })
  try {
    expect((await request.post(endpoint, { headers, data: { preferences: { quick_actions_enabled: true } } })).ok()).toBe(true)
    const player = await context.newPage()
    await player.goto(await seatTakeoverPath(request, game, 'e2e-player'))
    await expect(player.locator('.composer .quick-actions')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    let personalWrites = 0
    player.on('request', req => { if (req.method() === 'POST' && new URL(req.url()).pathname.endsWith('/preferences')) personalWrites++ })
    await player.getByTestId('quick-actions-toggle').click()
    await expect(player.locator('.composer .quick-actions')).toHaveCount(0)
    await page.goto(`/#/play?game=${encodeURIComponent(game)}`)
    await expect(page.locator('.composer .quick-actions')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    await page.getByTestId('play-gm-toggle').click()
    const workspace = page.getByTestId('gm-workspace')
    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    const actions = workspace.getByTestId('game-action-preferences')
    const toggle = actions.getByRole('switch', { name: '默认显示快捷行动建议' })
    await expect(toggle).toBeChecked()
    await toggle.uncheck()
    await expect(actions.getByRole('status')).toHaveText('本局设置已保存')
    await expect(page.locator('.composer .quick-actions')).toHaveCount(0)
    await expect(player.locator('.composer .quick-actions')).toHaveCount(0)
    await expect(player.locator('.composer textarea')).toBeVisible()
    await player.getByTestId('quick-actions-toggle').click()
    await expect(player.locator('.composer .quick-actions')).toBeVisible()
    await expect(page.locator('.composer .quick-actions')).toHaveCount(0)
    await player.reload()
    await expect(player.locator('.composer .quick-actions')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    expect(await player.locator('.composer-title-row > strong').evaluate(el => el.getBoundingClientRect().height)).toBeLessThan(28)
    await expect(player.locator('.composer-guidance p')).toBeHidden()
    expect(personalWrites).toBe(0)
    expect((await (await request.get(endpoint, { headers })).json()).preferences.quick_actions_enabled).toBe(false)
    expect((await (await request.get('/api/config', { headers })).json()).game_defaults).toEqual(defaults)
    await page.screenshot({ path: info.outputPath('quick-actions-disabled.png'), scale: 'css' })
    await player.screenshot({ path: info.outputPath('personal-suggestions-visible.png'), scale: 'css' })
    await page.reload()
    await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    await expect(page.locator('.composer .quick-actions')).toHaveCount(0)
    await page.getByTestId('play-gm-toggle').click()
    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    await expect(toggle).not.toBeChecked()
    await toggle.check()
    await expect(page.locator('.composer .quick-actions')).toBeVisible()
    await expect(player.locator('.composer .quick-actions')).toBeVisible()
    await player.getByTestId('quick-actions-toggle').click()
    await player.reload()
    await expect(player.locator('.composer .quick-actions')).toHaveCount(0)
    await expect(player.getByTestId('quick-actions-toggle')).toHaveAttribute('aria-expanded', 'false')
    await expect(page.locator('.composer .quick-actions')).toBeVisible()
    expect(await player.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  } finally {
    await context.close()
    expect((await request.post(endpoint, { headers, data: { preferences: { quick_actions_enabled: before } } })).ok()).toBe(true)
  }
})

test('an older running backend gets a restart hint instead of an unknown preference error', async ({ page }) => {
  let unsupportedWrites = 0
  await page.route(/\/api\/games\/[^/]+\/preferences(?:\?|$)/, async route => {
    if (route.request().method() === 'POST') { unsupportedWrites++; await route.abort(); return }
    const response = await route.fetch()
    const body = await response.json()
    delete body.preferences.quick_actions_enabled
    await route.fulfill({ response, json: body })
  })
  await page.route(/\/api\/config(?:\?|$)/, async route => {
    const response = await route.fetch()
    const body = await response.json()
    if (body.game_defaults) delete body.game_defaults.quick_actions_enabled
    await route.fulfill({ response, json: body })
  })
  try {
    await page.goto(`/#/play?game=${encodeURIComponent('web|e2e-room|web_bot')}`)
    await expect(page.getByTestId('quick-actions-toggle')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    await page.getByTestId('quick-actions-toggle').click()
    await expect(page.locator('.composer .quick-actions')).toHaveCount(0)
    await page.getByTestId('play-gm-toggle').click()
    const workspace = page.getByTestId('gm-workspace')
    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    const actions = workspace.getByTestId('game-action-preferences')
    await expect(actions.getByRole('switch', { name: '默认显示快捷行动建议' })).toBeDisabled()
    await expect(actions).toContainText('请重启服务')
    await expect(actions.getByRole('alert')).toHaveCount(0)
    expect(unsupportedWrites).toBe(0)
    await page.goto('/#/settings?section=game')
    const defaults = page.getByTestId('game-defaults-settings')
    await expect(defaults.getByRole('switch', { name: '默认显示快捷行动建议' })).toBeDisabled()
    await expect(defaults).toContainText('请重启服务')
  } finally {
    await page.unrouteAll({ behavior: 'wait' })
  }
})

test('Game uses the General card spacing and keeps every control inside its card', async ({ page }, info) => {
  for (const mode of ['dark', 'light']) {
    await page.goto('/#/settings?section=general')
    await page.evaluate(mode => localStorage.setItem('diceframe_mode_v2', mode), mode)
    await page.reload()
    if (mode === 'light') await expect(page.locator('body')).toHaveClass(/light/)
    else await expect(page.locator('body')).not.toHaveClass(/light/)
    const general = page.locator('.settings-pane:visible')
    await expect(general.locator('.advanced-row').first()).toBeVisible()
    const expected = await general.evaluate(element => {
      const card = getComputedStyle(element.querySelector('.advanced-section')!)
      const head = getComputedStyle(element.querySelector('.advanced-section-head')!)
      const row = getComputedStyle(element.querySelector('.advanced-row')!)
      return { radius: card.borderRadius, headPadding: head.padding, rowPadding: row.padding, rowGap: row.gap }
    })
    await page.goto('/#/settings?section=game')
    const defaults = page.getByTestId('game-defaults-settings')
    await expect(defaults.getByRole('switch', { name: '默认显示快捷行动建议' })).toBeVisible()
    const actual = await defaults.evaluate(element => {
      const card = getComputedStyle(element.querySelector('.advanced-section')!)
      const head = getComputedStyle(element.querySelector('.advanced-section-head')!)
      const row = getComputedStyle(element.querySelector('.advanced-row')!)
      return { radius: card.borderRadius, headPadding: head.padding, rowPadding: row.padding, rowGap: row.gap }
    })
    expect(actual).toEqual(expected)
    const cards = await defaults.locator('.advanced-section').evaluateAll(elements => elements.map(element => {
      const box = element.getBoundingClientRect()
      const controlBoxes = [...element.querySelectorAll('select, .n-input-number, .n-switch')].map(control => control.getBoundingClientRect())
      return { left: box.left, right: box.right, overflow: element.scrollWidth - element.clientWidth,
        inset: Math.min(...controlBoxes.map(control => Math.min(control.left - box.left, box.right - control.right))) }
    }))
    expect(cards).toHaveLength(4)
    for (const card of cards) {
      expect(Math.abs(card.left - cards[0]!.left)).toBeLessThanOrEqual(1)
      expect(Math.abs(card.right - cards[0]!.right)).toBeLessThanOrEqual(1)
      expect(card.overflow).toBeLessThanOrEqual(1)
      expect(card.inset).toBeGreaterThanOrEqual(12)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    await defaults.screenshot({ path: info.outputPath(`game-settings-${mode}.png`), scale: 'css', style: cardScreenshotStyle })
  }
})

test('new-table defaults persist, populate creation and leave existing games unchanged', async ({ page, request, browser }, info) => {
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const previous = (await (await request.get('/api/config', { headers })).json()).game_defaults
  const key = encodeURIComponent('web|e2e-room|web_bot')
  const before = (await (await request.get(`/api/games/${key}/preferences`, { headers })).json()).preferences
  try {
    await page.goto('/#/settings?section=game')
    const defaults = page.getByTestId('game-defaults-settings')
    await expect(defaults).toBeVisible()
    await expect(defaults.getByRole('switch', { name: '允许孤注一掷' })).toBeChecked()
    await defaults.getByRole('switch', { name: '允许孤注一掷' }).uncheck()
    await expect(defaults.getByRole('switch', { name: '默认显示快捷行动建议' })).toBeChecked()
    await defaults.getByRole('switch', { name: '默认显示快捷行动建议' }).uncheck()
    await defaults.getByLabel('叙事表现', { exact: true }).selectOption('dialogue')
    await defaults.getByTestId('save-game-defaults').click()
    await expect(defaults.getByRole('status')).toHaveText('新对局默认已保存')
    const stored = (await (await request.get('/api/config', { headers })).json()).game_defaults
    expect(stored.push_enabled).toBe(false)
    expect(stored.story_mode).toBe('dialogue')
    expect(stored.quick_actions_enabled).toBe(false)
    await defaults.screenshot({ path: info.outputPath('new-game-defaults.png'), scale: 'css', style: cardScreenshotStyle })
    const unchanged = (await (await request.get(`/api/games/${key}/preferences`, { headers })).json()).preferences
    expect(unchanged).toEqual(before)
    await page.reload()
    await expect(defaults.getByRole('switch', { name: '允许孤注一掷' })).not.toBeChecked()
    await expect(defaults.getByRole('switch', { name: '默认显示快捷行动建议' })).not.toBeChecked()
    await page.goto('/#/create')
    await page.locator('.create-actions .primary').click()
    await expect(page.locator('[data-testid="create-story-mode"] button').last()).toHaveAttribute('aria-pressed', 'true')
    const context = await browser.newContext()
    try {
      const response = await context.request.get(`http://127.0.0.1:${process.env.TRPG_E2E_PORT || '18000'}/api/config`)
      expect((await response.json()).game_defaults).toBeUndefined()
    } finally { await context.close() }
    await page.screenshot({ path: info.outputPath('creation-with-defaults.png'), scale: 'css' })
  } finally {
    const restored = await request.post('/api/config', { headers, data: { game_defaults: previous } })
    expect(restored.ok()).toBe(true)
  }
})

test('GM can save current preferences as defaults without overwriting unsupported rule preferences', async ({ page, request }, info) => {
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const previous = (await (await request.get('/api/config', { headers })).json()).game_defaults
  const key = 'web|e2e-room|web_bot'
  const endpoint = `/api/games/${encodeURIComponent(key)}/preferences`
  try {
    const updated = await request.post(endpoint, { headers, data: { preferences: { push_enabled: false, narrative_perspective: 'third_person', dice_reveal_mode: 'click' } } })
    expect(updated.ok()).toBe(true)
    await page.goto(`/#/play?game=${encodeURIComponent(key)}`)
    await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    await page.getByTestId('play-gm-toggle').click()
    const workspace = page.getByTestId('gm-workspace')
    await workspace.getByRole('tab', { name: '规则', exact: true }).click()
    await expect(workspace.getByTestId('game-check-preferences').getByRole('switch')).toHaveCount(0)
    await workspace.getByRole('tab', { name: '设置', exact: true }).click()
    await workspace.getByTestId('save-current-game-defaults').click()
    await expect(workspace.getByTestId('game-default-actions').getByRole('status')).toHaveText('新对局默认已保存')
    const defaults = (await (await request.get('/api/config', { headers })).json()).game_defaults
    expect(defaults.narrative_perspective).toBe('third_person')
    expect(defaults.dice_reveal_mode).toBe('click')
    expect(defaults.push_enabled).toBe(previous.push_enabled)
    await page.screenshot({ path: info.outputPath('game-default-actions.png'), scale: 'css' })
    await workspace.getByRole('link', { name: '管理新对局默认' }).click()
    await expect(page.getByTestId('game-defaults-settings')).toBeVisible()
    await expect(page.locator('#settings-game-defaults')).toHaveClass(/settings-search-highlight/)
  } finally {
    expect((await request.post('/api/config', { headers, data: { game_defaults: previous } })).ok()).toBe(true)
  }
})

test('GM can disable Push while decisions are pending and restore the default later', async ({ page, request }, info) => {
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const key = `web|e2e-preferences-push-${info.project.name}|web_bot`
  await page.goto(`/#/play?game=${encodeURIComponent(key)}`)
  await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
  await page.getByTestId('play-gm-toggle').click()
  const workspace = page.getByTestId('gm-workspace')
  await workspace.getByRole('tab', { name: '规则', exact: true }).click()
  const checks = workspace.getByTestId('game-check-preferences')
  const toggle = checks.getByRole('switch', { name: '允许孤注一掷' })
  await expect(toggle).toBeChecked()
  await toggle.uncheck()
  await expect(checks.getByRole('status')).toHaveText('本局设置已保存')
  const detail = await (await request.get(`/api/games/${encodeURIComponent(key)}`, { headers })).json()
  expect(detail.pending_push_decisions).toEqual([])
  expect(detail.pending_luck_decisions.length).toBeGreaterThan(0)
  await page.screenshot({ path: info.outputPath('gm-check-preferences.png'), scale: 'css' })
  await workspace.getByRole('tab', { name: '设置', exact: true }).click()
  await workspace.getByTestId('apply-game-defaults').click()
  await expect(workspace.getByTestId('game-default-actions').getByRole('status')).toHaveText('当前默认已应用到本局')
  await workspace.getByRole('tab', { name: '规则', exact: true }).click()
  await expect(toggle).toBeChecked()
  const after = await (await request.get(`/api/games/${encodeURIComponent(key)}`, { headers })).json()
  expect(after.pending_push_decisions).toEqual([])
})
