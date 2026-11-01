import { expect, test } from './fixtures'
import { PLAY_READY_TIMEOUT } from './support'

test('long GM paragraphs retain their indent and long player text wraps', async ({ page }, info) => {
  const first = '你示意恩恩把提灯压低，沿着树根寻找通向旧码头的小径。'.repeat(5)
  const second = '韦恩举着灯，朝身后的调查员招手，远处传来船只靠岸的声音。'.repeat(5)
  await page.route(url => url.pathname.startsWith('/api/games/') && url.pathname.endsWith('/log'), async route => {
    const response = await route.fetch()
    const payload = await response.json()
    await route.fulfill({ response, json: { ...payload, log: [{
      round: 12,
      actions: [{ user_id: 'e2e-player', text: `I examine the inscription: ${'AncientArchivePassage'.repeat(7)}` }],
      gm_response: `${first}\n${second}`,
    }] } })
  })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  const narration = page.locator('.message.gm .chat-gm')
  await expect(narration).toHaveCount(2, { timeout: PLAY_READY_TIMEOUT })
  await expect(narration.nth(0)).toContainText('你示意恩恩')
  await expect(narration.nth(1)).toContainText('韦恩举着灯')
  const geometry = await narration.evaluateAll(elements => elements.map(element => {
    const text = document.createTreeWalker(element, NodeFilter.SHOW_TEXT).nextNode()!
    const range = document.createRange()
    range.setStart(text, 0)
    range.setEnd(text, 1)
    return {
      firstLineInset: range.getBoundingClientRect().left - element.getBoundingClientRect().left,
      expected: 2 * parseFloat(getComputedStyle(element).fontSize),
    }
  }))
  for (const paragraph of geometry) {
    expect(Math.abs(paragraph.firstLineInset - paragraph.expected)).toBeLessThanOrEqual(1)
  }
  for (const message of await page.locator('.timeline .message').all()) {
    expect(await message.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  }
  await page.locator('.message.gm').screenshot({ path: info.outputPath(`gm-paragraphs-${info.project.name}.png`) })
})
