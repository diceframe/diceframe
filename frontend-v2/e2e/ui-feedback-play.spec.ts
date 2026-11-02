import { expect, test } from './fixtures'

const game = 'web|e2e-room|web_bot'

for (const mode of ['dark', 'light']) test(`ordinary play entry preserves the character and illustrated story surfaces without message rails in ${mode} mode`, async ({ page }, info) => {
  await page.setViewportSize(info.project.name === 'desktop' ? { width: 1366, height: 900 } : { width: 390, height: 844 })
  await page.addInitScript(mode => {
    localStorage.setItem('play_rail_collapsed', '0')
    localStorage.setItem('diceframe_mode_v2', mode)
  }, mode)
  await page.goto(`/#/play?game=${encodeURIComponent(game)}`)
  await expect(page.getByTestId('timeline')).toBeVisible()
  const toggle = page.getByTestId('play-gm-toggle')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await page.goto('/#/overview')
  await expect(page.locator('.overview-hero h1')).toBeVisible()
  await page.goto(`/#/play?game=${encodeURIComponent(game)}`)
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.message.gm').first()).toBeVisible()
  const restored = await page.evaluate(() => {
    const style = (selector: string) => getComputedStyle(document.querySelector(selector)!)
    return {
      chat: style('.play-main').backgroundImage,
      scene: style('.scene-strip').backgroundImage,
      sidebar: style('.game-sidebar').backgroundImage,
      messageBorders: Array.from(document.querySelectorAll('.timeline .message')).map(element => {
        const card = getComputedStyle(element)
        return { left: card.borderLeft, right: card.borderRight, top: card.borderTop, bottom: card.borderBottom }
      }),
      gmRadius: parseFloat(style('.message.gm').borderTopLeftRadius),
      sceneHeight: document.querySelector('.scene-strip')!.getBoundingClientRect().height,
      overflow: document.documentElement.scrollWidth - innerWidth,
    }
  })
  expect(restored.chat).toContain('url(')
  expect(restored.scene).toContain('url(')
  if (info.project.name === 'desktop') expect(restored.sidebar).toContain('url(')
  else expect(restored.sidebar).not.toContain('url(')
  expect(restored.messageBorders.length).toBeGreaterThan(0)
  for (const border of restored.messageBorders) {
    expect(border.left).toBe(border.right)
    expect(border.left).toBe(border.top)
    expect(border.left).toBe(border.bottom)
    expect(border.left.startsWith('1px solid')).toBe(true)
  }
  expect(restored.gmRadius).toBeGreaterThanOrEqual(6)
  expect(restored.sceneHeight).toBeLessThanOrEqual(52)
  expect(restored.overflow).toBeLessThanOrEqual(1)
  if (info.project.name === 'desktop') {
    await expect(page.getByTestId('play-character-dock')).toBeVisible()
    const partyRail = page.getByTestId('play-party-rail')
    await expect(partyRail).toBeVisible()
    const invite = page.getByTestId('play-invite-player')
    await expect(invite).toBeVisible()
    const memberLayout = await partyRail.evaluate(element => {
      const members = element.querySelector('.party-rail__members')!.getBoundingClientRect()
      const actions = element.querySelector('.party-rail__members-actions')!.getBoundingClientRect()
      return { membersBottom: members.bottom, actionsTop: actions.top }
    })
    expect(memberLayout.actionsTop).toBeGreaterThanOrEqual(memberLayout.membersBottom)
    await expect(page.locator('.mobile-drawer-trigger').first()).toBeHidden()
  } else {
    await expect(page.getByTestId('play-quick-status')).toBeVisible()
    await expect(page.getByTestId('play-party-rail')).toBeHidden()
  }
  await page.screenshot({ path: info.outputPath(`ui-feedback-play-${info.project.name}.png`), scale: 'css' })
  if (info.project.name === 'mobile') {
    await page.getByTestId('play-quick-status').click()
    await expect(page.locator('.play-sidebar-drawer.open')).toBeVisible()
    await expect.poll(async () => {
      const box = await page.locator('.play-sidebar-drawer.open').boundingBox()
      return box && Math.abs(box.x) < 1 && box.x + box.width <= 390
    }).toBe(true)
    await expect(page.locator('.play-sidebar-drawer.open .character')).toBeInViewport()
    await page.screenshot({ path: info.outputPath('ui-feedback-character-mobile.png'), scale: 'css' })
  }
  await page.goto(`/#/play?game=${encodeURIComponent(game)}&workbench=1`)
  if (info.project.name === 'mobile') {
    await expect(page.getByTestId('play-gm-drawer')).toHaveClass(/mobile-open/)
    await expect(page.getByTestId('play-gm-drawer')).toBeVisible()
  }
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByTestId('gm-workspace')).toBeVisible()
  if (info.project.name === 'desktop') {
    // The workbench is a drawer, so a click on the story surface should be a
    // fast, discoverable close path in addition to the arrow button.
    await page.locator('.play-main').click({ position: { x: 18, y: 18 } })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  }
})

test('revealed percentile dice align their faces and keep one adopted tens caption', async ({ page }, info) => {
  await page.setViewportSize(info.project.name === 'desktop' ? { width: 1366, height: 900 } : { width: 390, height: 844 })
  const diceGame = `web|e2e-dice-tray-feedback-${info.project.name}|web_bot`
  await page.goto(`/#/play?game=${encodeURIComponent(diceGame)}`)
  const card = page.locator('.check-reveal-card').first()
  await expect(card).toBeVisible()
  await expect(card.locator('[data-die-value]')).toHaveCount(0)
  await card.locator('.check-reveal-actions button').click()
  const dice = card.locator('[data-die-value]')
  await expect(dice.first()).toBeVisible()
  await expect(card.locator('.dice-tray')).toHaveClass(/dice-tray-reveal/)
  if (!await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)) {
    const animationCount = await dice.first().evaluate(element => element.getAnimations().length)
    expect(animationCount).toBeGreaterThan(0)
  }
  await expect(dice.first().locator('small')).toHaveText('共享个位')
  await expect(dice.nth(1).locator('small')).toHaveText('十位骰')
  await expect(card.locator('.tray-die-caption').filter({ hasText: '采用' })).toHaveCount(1)
  await expect.poll(async () => dice.evaluateAll(elements => elements.every(element => getComputedStyle(element).transform === 'none'))).toBe(true)
  // A revealed roll remains available while the player reads the result; it
  // may still be collapsed deliberately with the tray toggle.
  await page.waitForTimeout(5200)
  await expect(card.locator('.dice-tray')).toBeVisible()
  const geometry = await dice.evaluateAll(elements => elements.map(element => {
    const face = element.querySelector('svg')!.getBoundingClientRect()
    return { top: face.top, transform: getComputedStyle(element).transform }
  }))
  expect(geometry.length).toBeGreaterThanOrEqual(2)
  for (const face of geometry) {
    expect(face.transform).toBe('none')
    expect(Math.abs(face.top - geometry[0].top)).toBeLessThanOrEqual(1)
  }
  const badge = card.locator('.check-mode-badge')
  if (await badge.count()) {
    const badgeBox = await badge.boundingBox()
    const headBox = await card.locator('.check-reveal-head').boundingBox()
    expect(badgeBox!.width).toBeLessThan(headBox!.width / 2)
  }
  await card.scrollIntoViewIfNeeded()
  await card.screenshot({ path: info.outputPath(`ui-feedback-dice-${info.project.name}.png`), scale: 'css' })
  await card.locator('details > summary').click()
  await expect(card.locator('details')).toHaveAttribute('open', '')
  const footer = card.locator('.check-reveal-footer')
  const footerBox = await footer.boundingBox()
  const detailsBox = await footer.locator('details').boundingBox()
  const revealerBox = await footer.locator('.check-revealed-by').boundingBox()
  expect(Math.abs(footerBox!.x - detailsBox!.x)).toBeLessThanOrEqual(1)
  expect(revealerBox!.x).toBeGreaterThanOrEqual(footerBox!.x)
  expect(revealerBox!.x + revealerBox!.width).toBeLessThanOrEqual(footerBox!.x + footerBox!.width + 1)
  await card.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  await card.screenshot({ path: info.outputPath(`ui-feedback-dice-details-${info.project.name}.png`), scale: 'css' })
})

test('world and book controls group their own information and management shares the content navigation layout', async ({ page }, info) => {
  await page.setViewportSize(info.project.name === 'desktop' ? { width: 1366, height: 900 } : { width: 390, height: 844 })
  await page.addInitScript(value => localStorage.setItem('currentGame', value), game)
  await page.goto('/#/lorebook')
  const context = page.getByTestId('lore-context')
  await page.getByTestId('lore-context-crumbs').click()
  await expect(context).toBeVisible()
  await expect(context.locator('.lore-context-row--world select')).not.toHaveValue('')
  await expect(context.locator('.lore-context-row--book .lore-book-meta').first()).toBeVisible()
  const groups = await context.locator('.lore-context-row--world, .lore-context-row--book').evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
  }))
  if (info.project.name === 'desktop') expect(Math.abs(groups[0].y - groups[1].y)).toBeLessThanOrEqual(1)
  else expect(groups[1].y).toBeGreaterThanOrEqual(groups[0].y + groups[0].height)
  await expect(page.locator('.lorebook-page .section-kicker')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: info.outputPath(`ui-feedback-lorebook-${info.project.name}.png`), scale: 'css' })
  for (const [route, group] of [['modules', 'content'], ['settings', 'management']] as const) {
    await page.goto(`/#/${route}`)
    const shell = page.locator(`.content-workspace[data-workspace="${group}"]`)
    await expect(shell.locator('.content-workspace-nav a.active')).toBeVisible()
    const layout = await shell.evaluate(element => {
      const rail = element.querySelector('.content-workspace-rail')!.getBoundingClientRect()
      const main = element.querySelector('.content-workspace-main')!.getBoundingClientRect()
      const links = Array.from(element.querySelectorAll('.content-workspace-nav a')).map(link => link.getBoundingClientRect())
      return { railBottom: rail.bottom, mainTop: main.top, tops: links.map(link => link.top), heights: links.map(link => link.height) }
    })
    expect(layout.railBottom).toBeLessThanOrEqual(layout.mainTop + 1)
    expect(Math.max(...layout.tops) - Math.min(...layout.tops)).toBeLessThanOrEqual(1)
    if (info.project.name === 'mobile') expect(Math.min(...layout.heights)).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    await expect(page.locator('.n-spin-body:visible')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath(`ui-feedback-${route}-${info.project.name}.png`), scale: 'css' })
  }
})
