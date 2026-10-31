import { expect, test } from '@playwright/test'
import { prepareAuthenticatedPage } from './support'

test.beforeEach(async ({ page, request }) => {
  await prepareAuthenticatedPage(page, request, { firstRunTour: true })
})

test('first visit shows the interface tour once and it can be replayed', async ({ page }) => {
  await page.goto('/')
  const popover = page.locator('.driver-popover')
  await expect(popover.getByText('欢迎来到 DiceFrame')).toBeVisible()

  // Steps whose anchor is hidden (e.g. the mobile layout) are dropped, so read the total.
  const progress = popover.locator('.driver-popover-progress-text')
  await expect(progress).toHaveText(/^1 \/ \d+$/)
  const total = Number((await progress.innerText()).split('/')[1])
  expect(total).toBeGreaterThanOrEqual(6)

  // The welcome step is centered; every later step points at a visible element.
  for (let step = 2; step <= total; step += 1) {
    await popover.getByRole('button', { name: '下一步' }).click()
    await expect(progress).toHaveText(`${step} / ${total}`)
    await expect(page.locator('.driver-active-element')).toBeVisible()
  }
  await expect(popover.locator('.driver-popover-title')).toHaveText('开始第一局')
  await popover.getByRole('button', { name: '知道了' }).click()
  await expect(popover).toHaveCount(0)

  await page.reload()
  await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()
  await expect(page.locator('.driver-popover')).toHaveCount(0)

  await page.getByTestId('overview-start-tour').click()
  await expect(page.locator('.driver-popover').getByText('欢迎来到 DiceFrame')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.driver-popover')).toHaveCount(0)
})
