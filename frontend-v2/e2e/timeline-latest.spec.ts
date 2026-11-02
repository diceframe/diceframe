import { expect, test } from './fixtures'
import { PLAY_READY_TIMEOUT } from './support'

test('the back-to-bottom button sits below the scrolling timeline instead of covering message headers', async ({ page }, info) => {
  const log = Array.from({ length: 6 }, (_, index) => ({
    round: index + 1,
    actions: [{ user_id: 'e2e-player', text: `I check the gate in round ${index + 1}.` }],
    gm_response: 'Mist crosses the water.\nA door stands half open.',
  }))
  await page.route(url => url.pathname.startsWith('/api/games/') && url.pathname.endsWith('/log'), async route => {
    const response = await route.fetch()
    await route.fulfill({ response, json: { ...(await response.json()), log } })
  })
  const viewports = info.project.name === 'desktop' ? [{ width: 390, height: 844 }, { width: 1440, height: 1000 }] : [{ width: 390, height: 844 }]
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
    await expect(page.locator('.timeline .message.gm')).toHaveCount(6, { timeout: PLAY_READY_TIMEOUT })
    const timeline = page.getByTestId('timeline')
    const button = page.locator('.new-message')
    await expect(button).toBeHidden()
    for (const fraction of [0.2, 0.5, 0.8]) {
      await timeline.evaluate((element, share) => { element.scrollTop = (element.scrollHeight - element.clientHeight) * share }, fraction)
      await expect(button).toBeVisible()
      const geometry = await page.evaluate(() => {
        const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect()
        const pill = rect('.new-message'), scroller = rect('.timeline'), wrap = rect('.timeline-wrap')
        return { gap: pill.top - scroller.bottom, inside: wrap.bottom - pill.bottom, centred: Math.abs(pill.left + pill.width / 2 - (wrap.left + wrap.width / 2)) }
      })
      expect(geometry.gap).toBeGreaterThanOrEqual(0)
      expect(geometry.inside).toBeGreaterThanOrEqual(0)
      expect(geometry.centred).toBeLessThanOrEqual(1)
    }
    await button.click()
    await expect(button).toBeHidden()
    await expect.poll(() => timeline.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThanOrEqual(2)
  }
})
