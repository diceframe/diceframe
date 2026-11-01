import { expect, test } from './fixtures'
import { PLAY_READY_TIMEOUT } from './support'

test('phone featured save follows the archive card alignment', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/overview')
  const featured = page.getByTestId('overview-featured')
  await expect(featured).toBeVisible()
  await featured.screenshot({ path: info.outputPath('featured-phone.png') })
  const geometry = await featured.evaluate(element => {
    const card = element.getBoundingClientRect()
    const title = element.querySelector('h2')!.getBoundingClientRect()
    const button = element.querySelector('.overview-featured-enter')!.getBoundingClientRect()
    return { alignment: getComputedStyle(element.querySelector('.overview-featured-copy')!).textAlign,
      titleInset: title.left - card.left, buttonInset: button.left - card.left, rightInset: card.right - button.right }
  })
  expect(geometry.alignment).toBe('left')
  expect(Math.abs(geometry.titleInset - geometry.buttonInset)).toBeLessThanOrEqual(1)
  expect(Math.abs(geometry.buttonInset - geometry.rightInset)).toBeLessThanOrEqual(1)
})

for (const width of [320, 390, 740]) test(`phone play at ${width}px uses one navigation surface and a compact transparent character drawer`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  const status = page.getByTestId('play-quick-status')
  await expect(status).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
  await page.screenshot({ path: info.outputPath('play-phone.png') })
  expect((await status.boundingBox())!.height).toBeLessThanOrEqual(32)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  const quickActions = page.locator('.quick-actions button')
  await expect(quickActions).toHaveCount(4)
  const suggestions = await quickActions.evaluateAll(buttons => buttons.map(button => {
    const rect = button.getBoundingClientRect()
    return { top: rect.top, width: rect.width, height: rect.height, overflow: button.scrollWidth - button.clientWidth }
  }))
  expect(Math.max(...suggestions.map(button => button.top)) - Math.min(...suggestions.map(button => button.top))).toBeLessThanOrEqual(1)
  for (const button of suggestions) {
    expect(button.width).toBeGreaterThanOrEqual(44)
    expect(button.height).toBeGreaterThanOrEqual(44)
    expect(button.overflow).toBeLessThanOrEqual(1)
  }
  expect(await page.locator('.quick-actions').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  await expect(page.locator('.mobile-drawer-trigger:visible')).toHaveCount(1)
  const characterEntry = page.getByTestId('play-character-toggle')
  await expect(characterEntry).toBeVisible()
  expect((await characterEntry.boundingBox())!.x).toBe(0)
  await page.getByTestId('play-tools-toggle').click()
  const tools = page.getByTestId('play-tools-panel')
  await expect(tools.getByRole('button', { name: '角色', exact: true })).toBeVisible()
  await expect(tools.getByRole('button', { name: '场景地图', exact: true })).toBeVisible()
  await tools.getByRole('button', { name: '队伍', exact: true }).click()
  await expect(page.getByTestId('play-party-rail')).toBeVisible()
  await expect(page.getByTestId('play-invite-player')).toBeVisible()
  await page.locator('.play-drawer-backdrop').click({ position: { x: 8, y: 100 } })
  await characterEntry.click()
  const drawer = page.locator('.play-sidebar-drawer.open')
  await expect(drawer).toBeVisible()
  await expect.poll(() => drawer.evaluate(element => Math.round(element.getBoundingClientRect().x))).toBe(0)
  await page.screenshot({ path: info.outputPath('character-drawer-phone.png') })
  const style = await drawer.locator('.game-sidebar').evaluate(element => ({
    image: getComputedStyle(element).backgroundImage,
    filter: getComputedStyle(element).backdropFilter,
    top: element.getBoundingClientRect().top,
  }))
  expect(style.image).not.toContain('url(')
  expect(style.filter).toContain('blur')
  expect(style.top).toBe(0)
})
