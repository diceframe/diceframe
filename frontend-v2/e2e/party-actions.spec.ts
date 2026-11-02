import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { PLAY_READY_TIMEOUT, seatTakeoverPath } from './support'

async function openParty(page: Page) {
  const rail = page.getByTestId('play-party-rail')
  if (!await rail.isVisible()) {
    await page.getByTestId('play-tools-toggle').click()
    await page.getByTestId('play-tools-panel').getByRole('button', { name: '队伍', exact: true }).click()
  }
  await expect(rail).toBeVisible()
  return rail
}

test('party operations directly create, postpone, reopen and resolve a real player roll', async ({ page, browser, request }, info) => {
  test.setTimeout(75_000)
  const key = `web|e2e-party-actions-${info.project.name}|web_bot`
  const label = `队伍操作验收-${info.project.name}`
  await page.goto(`/#/play?game=${encodeURIComponent(key)}`)
  await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
  const rail = await openParty(page)
  const actions = rail.getByTestId('party-actions')
  await expect(actions.getByRole('heading', { name: '队伍操作' })).toBeVisible()
  expect(await actions.evaluate(element => {
    const rules = element.parentElement!.querySelector('.party-rail__recap')
    return Boolean(rules && (element.compareDocumentPosition(rules) & Node.DOCUMENT_POSITION_FOLLOWING))
  })).toBe(true)
  await expect(rail.locator('.party-rail__action-state')).toHaveCount(2)
  await expect(actions.getByTestId('party-rest')).toHaveCount(0)
  await actions.getByTestId('manual-roll-create').click()
  const composer = page.getByRole('dialog', { name: '发起手动投掷' })
  await composer.getByPlaceholder('例如：察觉检定').fill(label)
  await composer.getByPlaceholder('d20 + 2', { exact: true }).fill('d20+2')
  await composer.locator('.manual-roll-targets label').filter({ hasText: 'E2E GM' }).locator('input').uncheck()
  const created = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/roll-requests') && response.request().method() === 'POST')
  await composer.getByRole('button', { name: '发送请求', exact: true }).click()
  expect((await created).ok()).toBe(true)
  await expect(actions.getByTestId('manual-roll-pending')).toHaveText('待投掷 · 1')
  await actions.getByTestId('manual-roll-history').click()
  const gmHistory = page.getByRole('dialog', { name: '投掷记录', exact: true })
  await expect(gmHistory.getByRole('button', { name: 'GM 代投 · E2E Player', exact: true })).toBeVisible()
  await expect(gmHistory.getByRole('button', { name: '取消请求', exact: true })).toBeVisible()
  await gmHistory.locator('.modal-x').click()

  const context = await browser.newContext({ viewport: info.project.name === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 900 } })
  try {
    const player = await context.newPage()
    await player.goto(await seatTakeoverPath(request, key, 'e2e-player'))
    // The mobile party drawer starts closed: its one roll controller must
    // still receive the request and present its confirmation outside the rail.
    const confirmation = player.getByRole('dialog', { name: label })
    await expect(confirmation).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
    await confirmation.getByRole('button', { name: '稍后', exact: true }).click()
    const playerRail = await openParty(player)
    await expect(playerRail.getByTestId('manual-roll-create')).toHaveCount(0)
    await expect(playerRail.getByTestId('manual-roll-pending')).toHaveText('待投掷 · 1')
    const spacing = await playerRail.evaluate(element => {
      const actions = element.querySelector('[data-testid="party-actions"]')!.getBoundingClientRect()
      const members = element.querySelector('.party-rail__members')!.parentElement!.getBoundingClientRect()
      const rules = element.querySelector('.party-rail__recap')!.getBoundingClientRect()
      return { above: actions.top - members.bottom, below: rules.top - actions.bottom }
    })
    expect(spacing.above).toBeGreaterThanOrEqual(8)
    expect(spacing.below).toBeGreaterThanOrEqual(8)
    await player.screenshot({ path: info.outputPath('party-player-pending.png'), scale: 'css' })
    await playerRail.getByTestId('manual-roll-pending').click()
    await expect(confirmation).toBeVisible()
    const rolled = player.waitForResponse(response => new URL(response.url()).pathname.endsWith('/roll') && response.request().method() === 'POST')
    await confirmation.getByRole('button', { name: '投掷', exact: true }).click()
    expect((await rolled).ok()).toBe(true)
    await expect(confirmation).toHaveCount(0)
    await expect(playerRail.getByTestId('manual-roll-pending')).toHaveCount(0)
    await playerRail.getByTestId('manual-roll-history').click()
    const history = player.getByRole('dialog', { name: '投掷记录', exact: true })
    await expect(history).toContainText(label)
    await expect(history).toContainText('已完成')
    await expect(history).toContainText('E2E Player')
    await expect(history.getByRole('button', { name: /代投|取消请求/ })).toHaveCount(0)
    expect(await player.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    await player.screenshot({ path: info.outputPath('party-player-history.png'), scale: 'css' })
  } finally { await context.close() }
})

test('party rest opens the rest controls directly without applying a rest', async ({ page }, info) => {
  await page.goto('/#/play?game=web%7Ce2e-dnd2024%7Cweb_bot')
  await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
  const rail = await openParty(page)
  await expect(rail.getByTestId('party-rest')).toHaveText('队伍休息')
  const writes: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/rest')) writes.push(request.url()) })
  await rail.getByTestId('party-rest').click()
  const dialog = page.getByRole('dialog', { name: '高级角色中心', exact: true })
  await expect(dialog.locator('.rest-center')).toBeVisible()
  await expect(dialog.locator('.rest-confirm input')).not.toBeChecked()
  await expect(dialog.locator('.rest-submit')).toBeDisabled()
  await expect(dialog.locator('.rest-center')).toBeInViewport()
  expect(writes).toEqual([])
  await page.screenshot({ path: info.outputPath('party-rest-direct.png'), scale: 'css' })
})
