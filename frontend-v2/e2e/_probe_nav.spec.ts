import { test } from './fixtures'
import { accessToken } from './support'

test('probe recovery nav styles', async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), accessToken())
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/modules?adventure=core%3Acastle&version=1.0.0')
  await page.waitForTimeout(1500)
  const info = await page.evaluate(() => {
    const aside = document.querySelector('aside')
    const nav = aside?.querySelector('nav')
    const workspace = document.querySelector('[data-workspace]')
    const styleOf = (el: HTMLElement | null | undefined) => {
      if (!el) return null
      const s = getComputedStyle(el)
      return { cls: el.className.toString().slice(0, 80), overflowX: s.overflowX, display: s.display }
    }
    return {
      aside: styleOf(aside ?? null),
      nav: styleOf(nav ?? null),
      workspaceAttr: workspace?.getAttribute('data-workspace') ?? null,
      workspaceCls: workspace?.className.toString().slice(0, 80) ?? null,
      links: [...(nav?.querySelectorAll('a') ?? [])].slice(0, 9).map(a => ({
        text: (a.textContent || '').trim().slice(0, 10),
        left: Math.round(a.getBoundingClientRect().left),
      })),
    }
  })
  console.log('PROBE:', JSON.stringify(info, null, 1))
})
