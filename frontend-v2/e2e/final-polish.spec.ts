import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { accessToken } from './support'

async function authenticate(page: Page, options?: { light?: boolean }) {
  await page.addInitScript(({ token, light }) => {
    localStorage.setItem('trpg_access_token', token)
    localStorage.setItem('diceframe_locale', 'zh-CN')
    if (light) {
      localStorage.setItem('diceframe_mode_v2', 'light')
      localStorage.setItem('diceframe_skin_v2', 'midnight')
      localStorage.removeItem('diceframe_plugin_theme_v2')
    }
  }, { token: accessToken(), light: Boolean(options?.light) })
}

test('template adventure confirmation identifies the selected world instead of AI generation', async ({ page }) => {
  await authenticate(page)
  await page.goto('/#/create')
  await expect(page.locator('.create-mode-cards button').first()).toHaveClass(/active/)
  await page.locator('.create-actions .primary').click()
  await expect(page.locator('.create-game-settings-stage')).toBeVisible()
  await page.locator('.create-actions .primary').click()
  await expect(page.locator('.create-character-card')).toHaveCount(1)
  await page.locator('.create-actions .primary').click()

  const confirmation = page.locator('.create-confirm-cover p')
  await expect(confirmation).not.toBeEmpty()
  await expect(confirmation).not.toHaveText(/AI\s*生成/i)
  await expect(page.locator('.create-confirm-grid article').first().locator('strong')).toHaveText(await confirmation.textContent() || '')
})

test('light mode uses semantic neutral surfaces across rebuilt pages', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page, { light: true })

  const legacyColors = ['rgb(255, 240, 200)', 'rgb(234, 208, 160)']
  const assertNoLegacyGold = async (selector: string) => {
    const styles = await page.locator(selector).evaluateAll(elements => elements.map(element => {
      const style = getComputedStyle(element)
      return `${style.backgroundImage} ${style.backgroundColor} ${style.color}`
    }))
    for (const style of styles) {
      for (const legacy of legacyColors) expect(style).not.toContain(legacy)
    }
  }

  await page.goto('/#/overview')
  await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()
  await assertNoLegacyGold('.overview-stats article')

  await page.goto('/#/characters')
  await expect(page.locator('.characters-page')).toBeVisible()
  await assertNoLegacyGold('.characters-page article, .characters-page button')

  await page.goto('/#/lorebook')
  await expect(page.locator('.lorebook-page')).toBeVisible()
  await assertNoLegacyGold('.lore-type-tabs button, .lorebook-page article')

  await page.goto('/#/logs')
  await expect(page.locator('.reference-logs-page')).toBeVisible()
  await assertNoLegacyGold('.mode-tabs button, .log-reader article')
})

test('appearance and advanced settings obey the compact layout contract', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.goto('/#/settings?section=appearance')

  const modeButtons = page.locator('.appearance-mode-grid > button')
  await expect(modeButtons.first()).toBeVisible()
  const modeBoxes = await modeButtons.evaluateAll(elements =>
    elements.map(element => element.getBoundingClientRect()).map(rect => ({ width: rect.width, height: rect.height, top: rect.top })),
  )
  expect(modeBoxes.length).toBeGreaterThanOrEqual(2)
  for (const box of modeBoxes) {
    expect(box.height).toBeLessThanOrEqual(54)
    expect(box.width / box.height).toBeGreaterThan(2)
  }

  const backgroundCards = page.locator('.background-option-card')
  await expect(backgroundCards.first()).toBeVisible()
  for (let index = 0; index < await backgroundCards.count(); index += 1) {
    const card = backgroundCards.nth(index)
    await expect(card.getByText('内置', { exact: true })).toBeVisible()
    const alignment = await card.locator('.background-option-actions').evaluate(element => {
      const [choose, reset] = Array.from(element.children).map(child => child.getBoundingClientRect())
      return {
        centerDelta: Math.abs((choose.top + choose.height / 2) - (reset.top + reset.height / 2)),
        chooseHeight: choose.height,
        resetHeight: reset.height,
      }
    })
    expect(alignment.centerDelta).toBeLessThanOrEqual(2)
    expect(Math.abs(alignment.chooseHeight - alignment.resetHeight)).toBeLessThanOrEqual(2)
    const image = await card.locator('.background-option-preview').evaluate(element => getComputedStyle(element).backgroundImage)
    const imagePath = image.match(/url\(["']?([^"')]+)["']?\)/)?.[1]
    expect(imagePath).toContain('/v2-assets/ui/')
    expect((await page.request.get(imagePath!)).ok()).toBe(true)
  }

  await page.goto('/#/settings?section=advanced')
  const advancedPane = page.locator('.advanced-settings-pane:visible')
  await advancedPane.locator('.generation-settings > summary').click()
  await expect(advancedPane.getByRole('heading', { name: '生成参数' })).toBeVisible()
  await expect(advancedPane.getByRole('button', { name: '保存' }).first()).toBeEnabled()

  await page.goto('/#/settings?section=security')
  await expect(page.locator('#settings-hub-privacy').getByRole('heading', { name: 'DiceFrame Hub 与隐私' })).toBeVisible()

  await page.goto('/#/settings?section=about')
  await expect(page.locator('.about-card')).toBeVisible()
  await expect(page.getByText('1060613588')).toHaveCount(0)
})

test('model settings expose DeepSeek help and configurable test timeout', async ({ page }, testInfo) => {
  await authenticate(page)

  await page.goto('/#/settings?section=api')
  const providerHeader = page.locator('.ai-manager-header')
  await providerHeader.getByRole('button', { name: '帮助' }).click()
  await expect(page.getByRole('dialog').getByText('https://api.deepseek.com')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('deepseek-v4-pro')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('deepseek-v4-flash')).toBeVisible()
  await page.keyboard.press('Escape')

  await page.goto('/#/settings?section=models')
  await expect(page.locator('.settings-nav').getByText('向量记忆', { exact: true })).toHaveCount(0)
  const routingHeader = page.locator('.model-routing-header')
  await routingHeader.getByRole('button', { name: '帮助' }).click()
  await expect(page.getByRole('dialog').getByRole('heading', { name: '如何配置模型', exact: true })).toBeVisible()
  await expect(page.getByRole('dialog').getByText(/deepseek-v4-pro/)).toBeVisible()
  await page.keyboard.press('Escape')

  const modelCards = page.locator('.model-routing-grid')
  const mainCard = page.locator('.model-role-card-main')
  const embeddingCard = page.locator('.model-role-card-embedding')
  await expect(mainCard.locator('.model-fallback-slot')).toHaveCount(2)
  await expect(mainCard.getByText('备用 1', { exact: true })).toBeVisible()
  await expect(mainCard.getByText('备用 2', { exact: true })).toBeVisible()
  await expect(embeddingCard.getByText('向量检索', { exact: true })).toBeVisible()
  await expect(embeddingCard.getByRole('button', { name: '测试向量连接' })).toBeVisible()
  const modelLayout = await modelCards.evaluate(element => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    display: getComputedStyle(element).display,
    headerBackground: getComputedStyle(element.previousElementSibling!).backgroundImage,
  }))
  const capabilityLayout = await page.locator('.model-capability-grid').evaluate(element => ({
    display: getComputedStyle(element).display,
    columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
    alignItems: getComputedStyle(element).alignItems,
    columnCount: element.children.length,
  }))
  expect(modelLayout.scrollWidth).toBeLessThanOrEqual(modelLayout.clientWidth + 1)
  expect(modelLayout.display).toBe('grid')
  expect(modelLayout.headerBackground).toBe('none')
  expect(capabilityLayout).toEqual({
    display: 'grid',
    columns: testInfo.project.name === 'mobile' ? 1 : 2,
    alignItems: 'start',
    columnCount: 2,
  })

  await page.goto('/#/settings?section=system')
  const timeoutSection = page.locator('.test-timeout-section')
  await expect(timeoutSection.getByRole('heading', { name: '连接测试超时' })).toBeVisible()
  await expect(timeoutSection.locator('input')).toHaveValue('30')
  await expect(timeoutSection).toContainText('不改变正常游戏生成')

})

test('plugin marketplace cards align titles and stretch evenly per row', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.goto('/#/plugins')
  await page.locator('.n-tabs-tab').filter({ hasText: '市场' }).click()
  const cards = page.locator('.market-card')
  await expect(cards.first()).toBeVisible()

  const geometry = await cards.evaluateAll(elements => elements.map(element => {
    const card = element.getBoundingClientRect()
    const icon = element.querySelector<HTMLElement>('.market-title-icon')!.getBoundingClientRect()
    const text = element.querySelector<HTMLElement>('.market-title-text')!.getBoundingClientRect()
    return { top: Math.round(card.top), height: card.height, iconRight: icon.right, textLeft: text.left }
  }))
  expect(geometry.length).toBeGreaterThanOrEqual(2)
  for (const card of geometry) expect(card.iconRight).toBeLessThanOrEqual(card.textLeft)

  const rows = new Map<number, number[]>()
  for (const card of geometry) rows.set(card.top, [...(rows.get(card.top) || []), card.height])
  for (const heights of rows.values()) {
    if (heights.length < 2) continue
    expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(2)
  }
})

test('multi-player character actions stay in a separate even row', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.addInitScript(() => localStorage.setItem('currentGame', 'web|e2e-room|web_bot'))
  await page.setViewportSize({ width: 1940, height: 1080 })
  await page.goto('/#/characters')
  await page.getByRole('tab', { name: '本局角色', exact: true }).click()

  const cards = page.locator('.current-character-card')
  await expect(cards.first()).toBeVisible()
  await page.evaluate(() => {
    const grid = document.querySelector<HTMLElement>('.current-character-grid')
    const seed = grid?.querySelector<HTMLElement>('.current-character-card')
    if (!grid || !seed) return
    while (grid.querySelectorAll('.current-character-card').length < 5) {
      grid.append(seed.cloneNode(true))
    }
  })
  const geometry = await cards.evaluateAll(elements => elements.map(element => {
    const identity = element.querySelector<HTMLElement>('.current-character-identity')!.getBoundingClientRect()
    const actions = element.querySelector<HTMLElement>('.current-character-actions')!.getBoundingClientRect()
    const buttons = Array.from(element.querySelectorAll<HTMLElement>('.current-character-actions button'))
      .map(button => button.getBoundingClientRect())
      .map(rect => ({ top: rect.top, width: rect.width, height: rect.height }))
    return {
      identityBottom: identity.bottom,
      actionsTop: actions.top,
      cardRight: element.getBoundingClientRect().right,
      actionsRight: actions.right,
      buttons,
    }
  }))

  expect(geometry).toHaveLength(5)
  for (const { identityBottom, actionsTop, cardRight, actionsRight, buttons } of geometry) {
    expect(actionsTop).toBeGreaterThanOrEqual(identityBottom + 8)
    expect(actionsRight).toBeLessThanOrEqual(cardRight + 1)
    if (buttons.length !== 3) continue
    expect(Math.max(...buttons.map(button => button.top)) - Math.min(...buttons.map(button => button.top))).toBeLessThanOrEqual(2)
    expect(Math.max(...buttons.map(button => button.width)) - Math.min(...buttons.map(button => button.width))).toBeLessThanOrEqual(2)
    expect(Math.max(...buttons.map(button => button.height)) - Math.min(...buttons.map(button => button.height))).toBeLessThanOrEqual(2)
  }
})

test('avatar picker keeps distinct realistic and anime packs for every ruleset', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.addInitScript(() => localStorage.setItem('currentGame', 'web|e2e-room|web_bot'))
  await page.goto('/#/characters')
  await page.getByRole('tab', { name: '本局角色', exact: true }).click()

  await page.locator('.current-character-card .current-character-actions button').first().click()
  const currentOptions = page.locator('.portrait-picker > .portrait-options .portrait-option')
  await expect(currentOptions.first()).toBeVisible()
  const currentImages = await currentOptions.locator('.portrait-builtin').evaluateAll(elements =>
    elements.map(element => getComputedStyle(element).backgroundImage),
  )
  expect(currentImages.length).toBeGreaterThanOrEqual(2)
  expect(new Set(currentImages).size).toBe(currentImages.length)
  expect(currentImages.some(image => image.includes('/realistic-'))).toBe(true)
  expect(currentImages.some(image => image.includes('/anime-'))).toBe(true)

  await page.getByRole('button', { name: '从所有头像中选择', exact: true }).click()
  const groups = page.locator('.portrait-all-group')
  await expect(groups.first()).toBeVisible()
  for (let index = 0; index < await groups.count(); index += 1) {
    const images = await groups.nth(index).locator('.portrait-builtin').evaluateAll(elements =>
      elements.map(element => getComputedStyle(element).backgroundImage),
    )
    expect(images.length).toBeGreaterThanOrEqual(2)
    expect(images.some(image => image.includes('/realistic-'))).toBe(true)
    expect(images.some(image => image.includes('/anime-'))).toBe(true)
    const directories = new Set(images.map(image => image.match(/avatars\/v3\/([^/]+)\//)?.[1]).filter(Boolean))
    expect(directories.size).toBe(1)
  }
  const ruleDirectories = await page.locator('.portrait-all-group .portrait-builtin').evaluateAll(elements => [
    ...new Set(elements.map(element =>
      getComputedStyle(element).backgroundImage.match(/avatars\/v3\/([^/]+)\//)?.[1],
    ).filter(Boolean)),
  ])
  expect(ruleDirectories).toHaveLength(await groups.count())
})

test('settings status stays in one readable horizontal row', async ({ page }, testInfo) => {
  await authenticate(page)
  await page.goto('/#/settings')
  await expect(page.locator('.settings-status-disclosure')).toBeVisible()
  await page.locator('.settings-status-disclosure > summary').click()

  const grid = page.locator('.system-status-grid')
  const cards = page.locator('.system-status-card')
  await expect(cards.first()).toBeVisible()
  const layout = await grid.evaluate(element => ({
    display: getComputedStyle(element).display,
    flexWrap: getComputedStyle(element).flexWrap,
    overflowX: getComputedStyle(element).overflowX,
  }))
  const geometry = await cards.evaluateAll(elements => elements.map(element => {
    const card = element.getBoundingClientRect()
    const icon = element.querySelector<HTMLElement>('.system-status-icon')!.getBoundingClientRect()
    const head = element.querySelector<HTMLElement>('.system-status-head')!.getBoundingClientRect()
    const detail = element.querySelector<HTMLElement>('p')!
    return {
      cardTop: Math.round(card.top),
      cardHeight: card.height,
      iconTop: icon.top,
      headTop: head.top,
      detailTop: detail.getBoundingClientRect().top,
      detailWhiteSpace: getComputedStyle(detail).whiteSpace,
      detailLineHeight: Number.parseFloat(getComputedStyle(detail).lineHeight),
    }
  }))

  expect(geometry.length).toBeGreaterThan(0)
  expect(layout).toEqual(testInfo.project.name === 'mobile'
    ? { display: 'flex', flexWrap: 'nowrap', overflowX: 'auto' }
    : { display: 'grid', flexWrap: 'nowrap', overflowX: 'visible' })
  expect(new Set(geometry.map(item => item.cardTop)).size).toBeGreaterThanOrEqual(1)
  expect(geometry.every(item => item.cardHeight >= 100)).toBe(true)
  expect(geometry.every(item => item.detailWhiteSpace !== 'nowrap')).toBe(true)
  expect(geometry.every(item => item.detailLineHeight > 0 && item.cardHeight >= item.detailLineHeight * 3)).toBe(true)
  const rows = new Map<number, typeof geometry>()
  for (const item of geometry) {
    const row = rows.get(item.cardTop) || []
    row.push(item)
    rows.set(item.cardTop, row)
  }
  for (const row of rows.values()) {
    for (const key of ['cardHeight', 'iconTop', 'headTop', 'detailTop'] as const) {
      const values = row.map(item => item[key])
      expect(Math.max(...values) - Math.min(...values)).toBeLessThanOrEqual(2)
    }
  }
})

test('about, header and content-pack controls use the final layout contract', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)

  await page.goto('/#/settings?section=about')
  await expect(page.locator('.about-project-logo .brand-mark')).toBeVisible()
  await expect(page.locator('.about-monogram')).toHaveCount(0)
  await expect(page.locator('.update-card > .setting-row')).toHaveCount(0)
  await expect(page.locator('.update-card > .setting-hint')).toHaveCount(0)
  const updateMetaGeometry = await page.evaluate(() => {
    const current = document.querySelector<HTMLElement>('.update-meta > span:first-child')!.getBoundingClientRect()
    const channel = document.querySelector<HTMLElement>('.update-channel-inline')!.getBoundingClientRect()
    return {
      centerDelta: Math.abs((current.top + current.height / 2) - (channel.top + channel.height / 2)),
      channelWhiteSpace: getComputedStyle(document.querySelector<HTMLElement>('.update-channel-inline')!).whiteSpace,
    }
  })
  expect(updateMetaGeometry.centerDelta).toBeLessThanOrEqual(2)
  expect(updateMetaGeometry.channelWhiteSpace).toBe('nowrap')
  const aboutGeometry = await page.evaluate(() => {
    const about = document.querySelector<HTMLElement>('.about-card')!.getBoundingClientRect()
    const sponsor = document.querySelector<HTMLElement>('.about-card .sponsor-cta')!.getBoundingClientRect()
    const star = document.querySelector<HTMLElement>('.about-card .star-cta')!.getBoundingClientRect()
    return { aboutLeft: about.left, aboutRight: about.right, aboutBottom: about.bottom, sponsorLeft: sponsor.left, sponsorRight: sponsor.right, sponsorTop: sponsor.top, sponsorBottom: sponsor.bottom, starTop: star.top, starRight: star.right }
  })
  expect(aboutGeometry.sponsorLeft).toBeGreaterThan(aboutGeometry.aboutLeft)
  expect(aboutGeometry.sponsorRight).toBeLessThan(aboutGeometry.aboutRight)
  // Star 与 Sponsor 并排一行：同一水平带（顶部对齐），Sponsor 在 Star 右侧
  expect(Math.abs(aboutGeometry.sponsorTop - aboutGeometry.starTop)).toBeLessThanOrEqual(2)
  expect(aboutGeometry.sponsorLeft).toBeGreaterThan(aboutGeometry.starRight)
  expect(aboutGeometry.aboutBottom).toBeGreaterThan(aboutGeometry.sponsorBottom)
  const sponsorCopy = await page.locator('.about-card .sponsor-cta').evaluate(element => {
    const button = element.getBoundingClientRect()
    const title = element.querySelector<HTMLElement>('strong')!
    const detail = element.querySelector<HTMLElement>('small')!
    const titleRect = title.getBoundingClientRect()
    const detailRect = detail.getBoundingClientRect()
    const buttonStyle = getComputedStyle(element)
    return {
      titleOffset: titleRect.left - button.left,
      detailOffset: detailRect.left - button.left,
      expectedOffset: parseFloat(buttonStyle.paddingLeft) + parseFloat(buttonStyle.borderLeftWidth),
      titleAlign: getComputedStyle(title).textAlign,
      detailAlign: getComputedStyle(detail).textAlign,
      titleJustify: getComputedStyle(title).justifySelf,
      contentJustify: buttonStyle.justifyContent,
    }
  })
  expect(Math.abs(sponsorCopy.titleOffset - sponsorCopy.detailOffset)).toBeLessThanOrEqual(2)
  expect(Math.abs(sponsorCopy.titleOffset - sponsorCopy.expectedOffset)).toBeLessThanOrEqual(2)
  expect(sponsorCopy.titleAlign).toBe('left')
  expect(sponsorCopy.detailAlign).toBe('left')
  expect(sponsorCopy.titleJustify).toBe('stretch')
  expect(sponsorCopy.contentJustify).toBe('stretch')

  await expect(page.locator('.operator-avatar')).toHaveCount(0)

  await page.goto('/#/plugins')
  await page.locator('.plugin-surface-tabs > .n-tabs-nav .n-tabs-tab').nth(2).click()
  const toolbar = page.locator('.content-pack-toolbar')
  await expect(toolbar).toBeVisible()
  const buttonGeometry = await toolbar.locator('.n-button').evaluateAll(buttons => buttons.map(button => {
    const rect = button.getBoundingClientRect()
    return { top: rect.top, whiteSpace: getComputedStyle(button).whiteSpace }
  }))
  expect(buttonGeometry.length).toBeGreaterThanOrEqual(1)
  expect(Math.max(...buttonGeometry.map(button => button.top)) - Math.min(...buttonGeometry.map(button => button.top))).toBeLessThanOrEqual(2)
  expect(buttonGeometry.every(button => button.whiteSpace === 'nowrap')).toBe(true)
})

test('reference toolbars and rule headers keep stable single-line alignment', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.addInitScript(() => localStorage.setItem('currentGame', 'web|e2e-room|web_bot'))

  await page.goto('/#/memory')
  const memoryButtons = page.locator('.memory-search button')
  await expect(memoryButtons.first()).toBeVisible()
  const memoryGeometry = await memoryButtons.evaluateAll(buttons => buttons.map(button => {
    const rect = button.getBoundingClientRect()
    return { height: rect.height, whiteSpace: getComputedStyle(button).whiteSpace }
  }))
  const memoryHeights = memoryGeometry.map(item => item.height)
  expect(Math.max(...memoryHeights) - Math.min(...memoryHeights)).toBeLessThanOrEqual(2)
  for (const item of memoryGeometry) expect(item.whiteSpace).toBe('nowrap')

  await page.goto('/#/logs')
  const perPageLabel = page.locator('.log-toolbar label')
  await expect(perPageLabel).toBeVisible()
  await expect(perPageLabel).toHaveCSS('white-space', 'nowrap')

  await page.goto('/#/lorebook')
  const languageLabel = page.locator('.lore-language-filter > span')
  await expect(languageLabel).toBeVisible()
  await expect(languageLabel).toHaveCSS('white-space', 'nowrap')

  await page.goto('/#/rules')
  const ruleCards = page.locator('.rule-card')
  await expect(ruleCards.first()).toBeVisible()
  const headerGeometry = await ruleCards.evaluateAll(cards => cards.map(card => {
    const header = card.querySelector<HTMLElement>('h2')!.getBoundingClientRect()
    const badge = card.querySelector<HTMLElement>('h2 .badge')!.getBoundingClientRect()
    return { cardTop: card.getBoundingClientRect().top, headerHeight: header.height, badgeTop: badge.top }
  }))
  const firstRow = headerGeometry.filter(item => Math.abs(item.cardTop - headerGeometry[0].cardTop) <= 1)
  expect(Math.max(...firstRow.map(item => item.headerHeight)) - Math.min(...firstRow.map(item => item.headerHeight))).toBeLessThanOrEqual(2)
  expect(Math.max(...firstRow.map(item => item.badgeTop)) - Math.min(...firstRow.map(item => item.badgeTop))).toBeLessThanOrEqual(2)
})

test('overview keeps list selection controls beside the adventure library', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop visual contract')
  await authenticate(page)
  await page.goto('/#/overview')

  const heroActions = page.locator('.overview-actions')
  await expect(heroActions.getByRole('button', { name: '导入存档', exact: true })).toHaveCount(0)
  await expect(heroActions.getByRole('button', { name: '创建新冒险' })).toBeVisible()
  await expect(heroActions.getByRole('button', { name: '全选' })).toHaveCount(0)

  const libraryActions = page.locator('.library-heading-actions')
  await expect(libraryActions.getByRole('button', { name: '导入存档', exact: true })).toBeVisible()
  await expect(libraryActions.locator('.overview-archive-tools')).toBeVisible()
  // Batch actions stay hidden until the user explicitly enters save selection.
  await expect(libraryActions.getByRole('button', { name: '选择存档' })).toBeVisible()
  for (const name of ['全选', '反选', '取消选择', '批量导出']) {
    await expect(libraryActions.getByRole('button', { name, exact: true })).toHaveCount(0)
  }

  await page.getByTestId('overview-select-saves').click()
  const saveCount = await page.getByRole('checkbox', { name: '选择存档' }).count()
  expect(saveCount).toBeGreaterThan(0)
  await expect(libraryActions.getByRole('button', { name: '全选' })).toBeVisible()
  await expect(libraryActions.getByRole('button', { name: '反选' })).toBeVisible()
  await expect(libraryActions.getByRole('button', { name: '取消选择' })).toBeDisabled()
  await expect(libraryActions.getByRole('button', { name: '批量导出' })).toHaveCount(0)
  await libraryActions.getByRole('button', { name: '全选' }).click()
  await expect(libraryActions.getByRole('button', { name: `删除 ${saveCount}` })).toBeVisible()
  await libraryActions.getByRole('button', { name: '取消选择' }).click()
  await expect(libraryActions.getByRole('button', { name: /删除 \d+/ })).toHaveCount(0)

  const geometry = await libraryActions.locator(':scope > button').evaluateAll(buttons => buttons.map(button => {
    const rect = button.getBoundingClientRect()
    return { top: rect.top, height: rect.height }
  }))
  expect(Math.max(...geometry.map(item => item.top)) - Math.min(...geometry.map(item => item.top))).toBeLessThanOrEqual(2)
  expect(Math.max(...geometry.map(item => item.height)) - Math.min(...geometry.map(item => item.height))).toBeLessThanOrEqual(2)
})

test('primary section pages keep the original wide game frame', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop width contract')
  await authenticate(page)
  for (const width of [1920, 1366]) {
    await page.setViewportSize({ width, height: 1080 })

    for (const [route, selector] of [
      ['overview', '.overview-page'],
      ['characters', '.characters-page'],
      ['modules', '.modules-page'],
      ['settings', '.settings-page'],
    ] as const) {
      await page.goto(`/#/${route}`)
      const frame = page.locator(selector).first()
      await expect(frame).toBeVisible()
      const box = await frame.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.width).toBeGreaterThanOrEqual(width === 1920 ? 1559 : width - 1)
      expect(Math.abs(box!.x - (width - box!.width) / 2)).toBeLessThanOrEqual(1)
    }
  }
})
