import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const DND_GAME = 'web%7Ce2e-dnd2024%7Cweb_bot'

const PANEL_GEOMETRY = (panelSelector: string) => {
  const panel = document.querySelector<HTMLElement>(panelSelector)!
  const dialog = document.querySelector<HTMLElement>('.dnd-toolbox-dialog')!
  const overflowY = (element: HTMLElement) => getComputedStyle(element).overflowY
  // 只有 overflow:auto/scroll 且内容真的超出时，才算是用户可滚动的 owner
  const scrollable = (element: HTMLElement) => (
    (overflowY(element) === 'auto' || overflowY(element) === 'scroll')
    && element.scrollHeight - element.clientHeight > 1
  )
  let owner: HTMLElement | null = null
  for (let node: HTMLElement | null = panel; node && node !== document.documentElement; node = node.parentElement) {
    if (scrollable(node)) {
      owner = node
      break
    }
  }
  const tail = panel.lastElementChild?.getBoundingClientRect()
  return {
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panelOverflowY: overflowY(panel),
    panelBottom: Math.round(panel.getBoundingClientRect().bottom),
    dialogBottom: Math.round(dialog.getBoundingClientRect().bottom),
    dialogTop: Math.round(dialog.getBoundingClientRect().top),
    dialogLeft: Math.round(dialog.getBoundingClientRect().left),
    dialogWidth: Math.round(dialog.getBoundingClientRect().width),
    dialogHeight: Math.round(dialog.getBoundingClientRect().height),
    tailBottom: tail ? Math.round(tail.bottom) : null,
    panelScrollHeight: panel.scrollHeight,
    panelClientHeight: panel.clientHeight,
    owner: owner ? `${owner.tagName.toLowerCase()}.${String(owner.className).split(' ')[0]}` : null,
    ownerScrollTop: owner ? Math.round(owner.scrollTop) : null,
    ownerScrollRange: owner ? Math.round(owner.scrollHeight - owner.clientHeight) : null,
  }
}

async function endActiveCombat(page: Page, gameKey: string) {
  // 界面上的「结束战斗」只在轮到自己时出现；GM 结束战斗本身不受回合限制，
  // 所以收尾走接口，确保不把进行中的战斗留给同一存档上的后续用例。
  return page.evaluate(async (key: string) => {
    const authorization = `Bearer ${localStorage.getItem('trpg_access_token') || ''}`
    const headers = { 'Content-Type': 'application/json', Authorization: authorization }
    const path = `/api/games/${encodeURIComponent(key)}`
    const view = await (await fetch(`${path}/available-actions`, { headers })).json() as {
      gameplay?: { state_version?: number; combat?: { status?: string } }
    }
    if (view?.gameplay?.combat?.status !== 'active') return 'not-active'
    const response = await fetch(`${path}/intents`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        intent_id: `e2e-cleanup-combat-${Date.now()}`,
        type: 'combat.end',
        expected_version: view.gameplay.state_version ?? 0,
      }),
    })
    return response.ok ? 'ended' : `failed:${response.status}`
  }, gameKey)
}

async function combatStatus(page: Page, gameKey: string) {
  return page.evaluate(async (key: string) => {
    const authorization = `Bearer ${localStorage.getItem('trpg_access_token') || ''}`
    const body = await (await fetch(`/api/games/${encodeURIComponent(key)}/available-actions`, {
      headers: { Authorization: authorization },
    })).json() as { gameplay?: { combat?: { status?: string } } }
    return String(body?.gameplay?.combat?.status || '')
  }, gameKey)
}

async function expectToolPanelReachable(page: Page, panelSelector: string, label: string) {
  const before = await page.evaluate(PANEL_GEOMETRY, panelSelector)
  expect(before.documentOverflow, `${label}: document must not overflow horizontally`).toBe(0)
  // 弹窗是 overflow:hidden：面板盒子如果超出弹窗，就必须有人能滚，否则内容被永久裁掉
  expect(
    before.panelBottom <= before.dialogBottom + 2 || before.owner,
    `${label}: clipped panel needs a user-scrollable owner (geometry ${JSON.stringify(before)})`,
  ).toBe(true)

  // 滚轮必须真的滚动内容，并把面板最后一段带进弹窗视口
  await page.mouse.move(before.dialogLeft + before.dialogWidth / 2, before.dialogTop + Math.min(180, before.dialogHeight / 2))
  await page.mouse.wheel(0, 8000)
  await page.waitForTimeout(150)
  const after = await page.evaluate(PANEL_GEOMETRY, panelSelector)
  if (after.owner && after.ownerScrollRange) {
    expect(after.ownerScrollTop, `${label}: mouse wheel must scroll ${after.owner}`).toBeGreaterThan(0)
    expect(
      after.ownerScrollTop!,
      `${label}: scrolling must reach the end of ${after.owner}`,
    ).toBeGreaterThanOrEqual(after.ownerScrollRange - 2)
  }
  expect(
    after.tailBottom,
    `${label}: the last section must be reachable inside the dialog (geometry ${JSON.stringify(after)})`,
  ).toBeLessThanOrEqual(after.dialogBottom + 2)
}

async function openDndTable(page: Page) {
  await page.goto(`/#/play?game=${DND_GAME}`)
  await expect(page.getByRole('heading', { name: 'D&D 2024 新手桌' })).toBeVisible()
  await expect(page.getByTestId('timeline')).toBeVisible()
  await expect(page.locator('.composer')).toBeVisible()
}

function fieldset(page: Page, legend: string) {
  return page.locator('fieldset').filter({ has: page.locator('legend', { hasText: legend }) }).first()
}

async function chooseBuilderCard(page: Page, legend: string, name: string | RegExp, refreshesChoices = false) {
  const response = refreshesChoices
    ? page.waitForResponse(item => item.request().method() === 'POST' && item.url().includes('/builder/choices'))
    : null
  await fieldset(page, legend).getByRole('button').filter({ hasText: name }).click()
  if (response) await response
}

test('classic fantasy recommends the professional 2024 rules as the third card in one desktop row', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto('/#/create')
  const worldSelect = page.locator('.create-config-surface label').filter({ hasText: /^世界模板/ }).locator('select')
  await worldSelect.selectOption('default_fantasy')

  const cards = page.locator('.create-recommended-rules .rec-card')
  await expect(cards).toHaveCount(3)
  await expect(cards.nth(0)).toContainText('经典奇幻自由规则')
  await expect(cards.nth(1)).toContainText('D&D 5e')
  await expect(cards.nth(2)).toContainText('5E 2024 SRD 高级规则')
  await expect(cards.nth(0).locator('small')).toHaveText('推荐')
  await expect(cards.nth(1).locator('small')).toHaveText('推荐')
  await expect(cards.nth(2).locator('small.professional')).toHaveText('高级')

  const cardTops = await cards.evaluateAll(items => items.map(item => Math.round(item.getBoundingClientRect().top)))
  expect(new Set(cardTops).size).toBe(1)
})

test('professional toolbox remains contained at phone, tablet, and desktop widths', async ({ page }) => {
  for (const width of [320, 640, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await openDndTable(page)
    await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
    await expect(page.locator('.campaign-panel')).toBeVisible()

    const geometry = await page.evaluate(() => {
      const workspace = document.querySelector<HTMLElement>('.dnd-toolbox-dialog')!
      const bounds = workspace.getBoundingClientRect()
      const controls = Array.from(workspace.querySelectorAll<HTMLElement>('button, input:not([type="checkbox"]), select, textarea'))
        .filter(item => item.getBoundingClientRect().width > 0 && getComputedStyle(item).visibility !== 'hidden')
      const checkboxLabels = Array.from(workspace.querySelectorAll<HTMLElement>('label.check'))
      return {
        documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        workspaceOverflow: workspace.scrollWidth - workspace.clientWidth,
        workspaceTop: bounds.top,
        workspaceBottom: bounds.bottom,
        viewportHeight: window.innerHeight,
        minControlHeight: Math.min(...controls.map(item => item.getBoundingClientRect().height)),
        minCheckboxTargetHeight: Math.min(...checkboxLabels.map(item => item.getBoundingClientRect().height)),
      }
    })
    expect(geometry.documentOverflow, `document overflow at ${width}px`).toBe(0)
    expect(geometry.workspaceOverflow, `workspace overflow at ${width}px`).toBeLessThanOrEqual(1)
    expect(geometry.workspaceTop).toBeGreaterThanOrEqual(0)
    expect(geometry.workspaceBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1)
    expect(geometry.minControlHeight).toBeGreaterThanOrEqual(28)
    expect(geometry.minCheckboxTargetHeight).toBeGreaterThanOrEqual(43)
    await page.locator('.dnd-toolbox-dialog .modal-x').click()
    await expect(page.locator('.dnd-toolbox-dialog')).toHaveCount(0)
  }
})

test('professional rules keep one timeline and expose combat as a tool', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openDndTable(page)

  await expect(page.getByTestId('timeline')).toHaveCount(1)
  await expect(page.locator('.composer')).toHaveCount(1)
  await expect(page.locator('.campaign-panel')).toHaveCount(0)
  await expect(page.locator('.dnd-combat')).toHaveCount(0)
  await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
  await page.locator('.dnd-toolbox-dialog').getByRole('button', { name: '战斗工具' }).click()
  await expect(page.locator('.dnd-combat')).toBeVisible()
  await expect(page.getByTestId('timeline')).toBeVisible()
  await expect(page.locator('.dnd-party-feed')).toHaveCount(0)
})

test('professional toolbox keeps long tool panels scrollable inside the dialog', async ({ page }) => {
  for (const viewport of [
    { width: 1280, height: 620 },
    { width: 1024, height: 600 },
    { width: 390, height: 700 },
  ]) {
    await page.setViewportSize(viewport)
    await openDndTable(page)
    await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
    await expect(page.locator('.campaign-panel .session-card')).toBeVisible()

    const geometry = await page.evaluate(PANEL_GEOMETRY, '.campaign-panel')
    console.log(`[tool-panel] campaign ${viewport.width}x${viewport.height} ${JSON.stringify(geometry)}`)
    // 冒险面板一定比弹窗高：必须有真正可滚动的 owner，否则内容被 overflow:hidden 永久裁掉
    expect(geometry.owner, `campaign panel needs a scrollable owner at ${viewport.width}px: ${JSON.stringify(geometry)}`).toBeTruthy()
    await expectToolPanelReachable(page, '.campaign-panel', `campaign @ ${viewport.width}x${viewport.height}`)
    await expect(page.locator('.campaign-panel')).toBeVisible()

    await page.locator('.dnd-toolbox-dialog .modal-x').click()
    await expect(page.locator('.dnd-toolbox-dialog')).toHaveCount(0)
  }
})

test('professional combat tool keeps its content reachable at short desktop and phone viewports', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 620 })
  await openDndTable(page)
  await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
  const dialog = page.locator('.dnd-toolbox-dialog')
  const panel = page.locator('.dnd-combat')
  await dialog.getByRole('button', { name: '战斗工具' }).click()
  await expect(panel.locator('.encounter-start, .turn-banner').first()).toBeVisible()

  // 同一份 e2e 存档会被 desktop/mobile 两个 project 先后使用，战斗可能是
  // “未开始 / 已结束 / 进行中”三种之一；三者都必须能进入可滚动的战斗面板。
  if (await panel.locator('.turn-banner').count() === 0) {
    if (await panel.locator('.encounter-ended-actions .combat-primary').count() > 0) {
      await panel.locator('.encounter-ended-actions .combat-primary').click()
      const picker = panel.locator('.next-encounter-picker select')
      await expect(picker).toBeVisible()
      // 与手动准备一致：明确选中一条目录遭遇后再开战。
      await picker.selectOption({ index: 0 })
      await panel.locator('.next-encounter-picker .combat-primary').click()
    } else {
      await panel.getByRole('button', { name: '手动准备遭遇' }).click()
      await expect(panel.locator('.encounter-start select')).toBeVisible()
      await panel.getByRole('button', { name: '确认进入战斗' }).click()
    }
  }
  await expect(panel.locator('.turn-banner')).toBeVisible({ timeout: 20_000 })
  await expect(panel.locator('.actor-card')).not.toHaveCount(0)

  for (const viewport of [
    { width: 1280, height: 620 },
    { width: 1024, height: 600 },
    { width: 390, height: 700 },
  ]) {
    await page.setViewportSize(viewport)
    await expect(panel.locator('.turn-banner')).toBeVisible()
    const geometry = await page.evaluate(PANEL_GEOMETRY, '.dnd-combat')
    console.log(`[tool-panel] combat ${viewport.width}x${viewport.height} ${JSON.stringify(geometry)}`)
    await expectToolPanelReachable(page, '.dnd-combat', `combat @ ${viewport.width}x${viewport.height}`)
  }

  // 纵向修复不得破坏移动端的横向滚动（initiative / 行动者卡 / 动作卡）。
  const lane = page.locator('.dnd-combat .actor-grid').first()
  const laneGeometry = await lane.evaluate(element => ({
    overflowX: getComputedStyle(element).overflowX,
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
    scrollLeft: element.scrollLeft,
  }))
  console.log(`[tool-panel] lane ${JSON.stringify(laneGeometry)}`)
  expect(['auto', 'scroll']).toContain(laneGeometry.overflowX)
  if (laneGeometry.scrollWidth > laneGeometry.clientWidth) {
    // 目标值取最大值：卡片带使用 scroll-snap，中间偏移会被吸附回去。
    await lane.evaluate(element => { element.scrollLeft = element.scrollWidth })
    expect(await lane.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
  }

  // 键盘滚动仍可用：焦点在面板内部时 PageDown 必须滚动画板。
  await panel.locator('.combat-header button').first().focus()
  await panel.evaluate(element => { element.scrollTop = 0 })
  await page.keyboard.press('PageDown')
  await page.waitForTimeout(150)
  const keyboardAfter = await panel.evaluate(element => element.scrollTop)
  console.log(`[tool-panel] keyboard scrollTop 0 -> ${keyboardAfter}`)
  expect(keyboardAfter, 'PageDown must scroll the focused panel').toBeGreaterThan(0)

  // 收尾：结束这场遭遇，避免给同一存档上的后续用例留下进行中的战斗。
  const gameKey = decodeURIComponent(DND_GAME)
  await endActiveCombat(page, gameKey)
  await expect.poll(() => combatStatus(page, gameKey), { timeout: 15_000 }).not.toBe('active')
})

test('professional surfaces keep explicit labels and readable light-mode colors', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    localStorage.setItem('diceframe_mode_v2', 'light')
    localStorage.setItem('diceframe_skin_v2', 'midnight')
  })
  await openDndTable(page)
  await expect(page.locator('body')).toHaveClass(/light/)
  await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()

  const unlabeled = await page.locator('.campaign-panel input').evaluateAll(inputs => inputs.filter(input => {
    const element = input as HTMLInputElement
    return !element.closest('label') && !element.getAttribute('aria-label') && !element.getAttribute('aria-labelledby')
  }).length)
  expect(unlabeled).toBe(0)

  const appearance = await page.locator('.session-card').evaluate(element => {
    const channels = (value: string) => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number)
    const luminance = (value: string) => {
      const converted = channels(value).map(channel => {
        const normalized = channel / 255
        return normalized <= .04045 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4
      })
      return .2126 * converted[0] + .7152 * converted[1] + .0722 * converted[2]
    }
    const contrast = (a: string, b: string) => {
      const [lighter, darker] = [luminance(a), luminance(b)].sort((left, right) => right - left)
      return (lighter + .05) / (darker + .05)
    }
    const cardStyle = getComputedStyle(element)
    const mutedStyle = getComputedStyle(element.querySelector<HTMLElement>('.muted')!)
    return {
      textContrast: contrast(cardStyle.color, cardStyle.backgroundColor),
      mutedContrast: contrast(mutedStyle.color, cardStyle.backgroundColor),
      transitionDuration: getComputedStyle(element.querySelector<HTMLElement>('summary')!).transitionDuration,
      animationDuration: getComputedStyle(element.querySelector<HTMLElement>('summary')!).animationDuration,
    }
  })
  expect(appearance.textContrast).toBeGreaterThanOrEqual(4.5)
  expect(appearance.mutedContrast).toBeGreaterThanOrEqual(4.5)
  expect(appearance.transitionDuration).toBe('0s')
  expect(appearance.animationDuration).toBe('0s')
})

test('Chinese professional play area explains the route and localizes campaign enums', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await openDndTable(page)
  await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
  await expect(page.getByRole('heading', { name: '快速完成开团设置' })).toBeVisible()
  await expect(page.locator('.session-card summary')).toContainText('手动设置 / 多人开团')

  const agreement = page.locator('.agreement-grid')
  const tone = agreement.locator('label').filter({ hasText: /^基调/ }).locator('select')
  const difficulty = agreement.locator('label').filter({ hasText: /^难度/ }).locator('select')
  const rating = agreement.locator('label').filter({ hasText: /^内容分级/ }).locator('select')
  const pvp = agreement.locator('label').filter({ hasText: /^玩家对抗/ }).locator('select')
  await expect(tone.locator('option').first()).toHaveText('英雄冒险，保留轻松幽默')
  await expect(difficulty.locator('option')).toHaveText(['剧情优先', '标准', '挑战', '致命'])
  await expect(rating.locator('option')).toHaveText(['全龄', '青少年', '成人'])
  await expect(pvp.locator('option')).toHaveText(['禁止', '仅经同意', '允许'])
  await expect(agreement.locator('textarea').nth(2)).toHaveValue(/让每位玩家都有表现机会/)
})

test('professional quick builder remains usable on a 320px first-time-player join flow', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 })
  await page.goto(`/#/join?game=${DND_GAME}&share=1`)
  await expect(page.getByRole('heading', { name: '创建你的冒险者' })).toBeVisible()
  await expect(page.getByRole('tablist', { name: '创建模式' })).toBeVisible()
  await expect(page.getByText('第一次玩？从这里开始。')).toBeVisible()

  const geometry = await page.locator('.ruleset-experience--dnd2024').evaluate(element => {
    const controls = Array.from(element.querySelectorAll<HTMLElement>('button, input:not([type="checkbox"]):not([type="radio"]), select'))
      .filter(item => item.getBoundingClientRect().width > 0)
    return {
      panelOverflow: element.scrollWidth - element.clientWidth,
      documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      minControlHeight: Math.min(...controls.map(item => item.getBoundingClientRect().height)),
      offenders: Array.from(document.querySelectorAll<HTMLElement>('body *'))
        .filter(item => item.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 8)
        .map(item => ({ tag: item.tagName, className: item.className, right: item.getBoundingClientRect().right, width: item.getBoundingClientRect().width })),
    }
  })
  expect(geometry.panelOverflow).toBeLessThanOrEqual(1)
  expect(geometry.documentOverflow, JSON.stringify(geometry.offenders)).toBe(0)
  expect(geometry.minControlHeight).toBeGreaterThanOrEqual(43)

  const finishButton = page.getByRole('button', { name: '完成并使用这个角色' })
  await finishButton.scrollIntoViewIfNeeded()
  const finishBottom = await finishButton.evaluate(element => element.getBoundingClientRect().bottom)
  expect(finishBottom).toBeLessThanOrEqual(845)

  const firstTab = page.getByRole('tab', { name: '快速创建' })
  await firstTab.focus()
  await firstTab.press('ArrowRight')
  await expect(page.getByRole('tab', { name: '引导创建' })).toBeFocused()
  await expect(page.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'builder-mode-guided')
})

test('guided creation enforces proficiency limits and enters the saved game even when opening narration is unavailable', async ({ page }) => {
  test.setTimeout(60_000)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/create')

  const ruleSelect = page.locator('.create-config-surface label').filter({ hasText: /^规则/ }).locator('select')
  await expect(ruleSelect).toBeVisible()
  await ruleSelect.selectOption('dnd2024_srd')
  await page.locator('.create-actions .primary').click()
  await expect(page.locator('.create-game-settings-stage')).toBeVisible()
  await page.locator('.create-actions .primary').click()

  await page.locator('.create-character-actions .primary').click()
  await expect(page.getByRole('heading', { name: '创建你的冒险者' })).toBeVisible()
  await page.getByRole('tab', { name: '引导创建' }).click()
  const alignment = page.locator('.guided-builder label').filter({ hasText: /^阵营/ }).locator('select')
  await expect(alignment.locator('option')).toHaveText([
    'LG · 守序善良', 'NG · 中立善良', 'CG · 混乱善良',
    'LN · 守序中立', 'N · 绝对中立', 'CN · 混乱中立',
  ])
  await expect(alignment.locator('xpath=..').locator('.field-help')).toContainText('缩写与常见 D&D 资料一致')
  await page.locator('.name-field input').fill('新手验收者')
  await chooseBuilderCard(page, '职业', '战士', true)
  await chooseBuilderCard(page, '物种', '人类', true)
  await chooseBuilderCard(page, '背景', '士兵', true)
  await page.locator('.builder-actions .primary').click()
  await expect(page.getByRole('heading', { name: '属性决定你做事时的基础优势' })).toBeVisible()
  await page.locator('.builder-actions .primary').click()
  await expect(page.getByRole('heading', { name: '完成会影响规则的选择' })).toBeVisible()

  const classSkills = fieldset(page, '职业技能')
  const speciesSkills = fieldset(page, '物种技能')
  for (const skill of ['察觉', '求生']) {
    await classSkills.locator('label').filter({ hasText: skill }).locator('input').check()
  }
  await expect(classSkills.locator('input:not(:checked):not(:disabled)')).toHaveCount(0)
  for (const skill of ['察觉', '求生']) {
    await expect(speciesSkills.locator('label').filter({ hasText: skill }).locator('input')).toBeDisabled()
  }
  await speciesSkills.locator('label').filter({ hasText: '杂技' }).locator('input').check()
  await expect(speciesSkills.locator('input:not(:checked):not(:disabled)')).toHaveCount(0)

  await chooseBuilderCard(page, '物种专长', '警觉', true)
  await fieldset(page, '体型').locator('label').filter({ hasText: '中型' }).locator('input').check()
  await fieldset(page, '职业起始装备').locator('[data-choice-ref="equipment_package:fighter_a"]').click()
  await fieldset(page, '背景起始装备').locator('[data-choice-ref="equipment_package:soldier_a"]').click()
  const languages = fieldset(page, '语言')
  for (const language of ['矮人语', '精灵语']) {
    await languages.locator('label').filter({ hasText: language }).locator('input').check()
  }
  await expect(languages.locator('input:not(:checked):not(:disabled)')).toHaveCount(0)

  const validateCharacter = page.locator('.builder-actions .primary')
  await expect(validateCharacter).toBeEnabled()
  await validateCharacter.click()
  await expect(page.getByRole('heading', { name: '角色已通过规则检查' })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '完成角色' }).click()
  await expect(page.locator('.create-character-card').filter({ hasText: '新手验收者' })).toBeVisible()
  await page.locator('.create-actions .primary').click()
  await page.getByRole('button', { name: /创建并进入/ }).click()

  await expect(page).toHaveURL(/#\/play\?game=web(?:%7C|\|)/, { timeout: 20_000 })
  await expect(page.getByTestId('timeline')).toBeVisible()
  await expect(page.locator('.composer')).toBeVisible()
  const gameKey = await page.evaluate(() => localStorage.getItem('currentGame'))
  expect(gameKey).toBeTruthy()
  const saved = await page.evaluate(async key => {
    const authorization = `Bearer ${localStorage.getItem('trpg_access_token') || ''}`
    const path = `/api/games/${encodeURIComponent(key || '')}`
    const [detail, characters] = await Promise.all([
      fetch(path, { headers: { Authorization: authorization } }),
      fetch(`${path}/characters`, { headers: { Authorization: authorization } }),
    ])
    return {
      detailStatus: detail.status,
      characterStatus: characters.status,
      detail: await detail.json(),
      characters: await characters.json(),
    }
  }, gameKey)
  expect(saved.detailStatus).toBe(200)
  expect(saved.characterStatus).toBe(200)
  expect(saved.detail.rule_id).toBe('dnd2024_srd')
  expect(saved.characters.players?.[0]?.character_name).toBe('新手验收者')
})
