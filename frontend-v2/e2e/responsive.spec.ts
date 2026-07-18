import { expect, test } from '@playwright/test'
import { accessToken } from './support'

test('layout has no document overflow', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()
  const sizes = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(sizes.scroll).toBe(sizes.client)
})

test('all required viewport widths remain contained', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, `overflow at ${width}px`).toBe(0)
  }
})

test('phone navigation stays on one compact row', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()

  const layout = await page.evaluate(() => {
    const header = document.querySelector<HTMLElement>('.top-nav')!
    const children = ['.top-brand', '.top-menu', '.top-right'].map(selector =>
      document.querySelector<HTMLElement>(selector)!.getBoundingClientRect(),
    )
    return {
      height: header.getBoundingClientRect().height,
      centers: children.map(rect => rect.top + rect.height / 2),
    }
  })

  expect(layout.height).toBeLessThanOrEqual(44)
  expect(Math.max(...layout.centers) - Math.min(...layout.centers)).toBeLessThanOrEqual(2)
})
