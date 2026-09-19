import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from './fixtures'
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

test('phone module recovery stays contained and exposes local import', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/modules?adventure=core%3Acastle&version=1.0.0')
  await expect(page.getByRole('heading', { name: '模组', exact: true })).toBeVisible()
  await expect(page.getByText('恢复本局冒险')).toBeVisible()
  await expect(page.locator('.module-import input[type="file"]')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
})

test('module library separates installed, local import, and online modules', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.goto('/#/modules')

  // §8：三个区块同页可见，不再把在线模组甩给插件页面。
  await expect(page.getByTestId('modules-installed')).toBeVisible()
  await expect(page.getByTestId('modules-local-import')).toBeVisible()
  await expect(page.getByTestId('modules-online')).toBeVisible()
  await expect(page.getByTestId('modules-installed').getByText('E2E Module')).toBeVisible()
})

test('module detail renders metadata and server-guarded actions', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.goto('/#/modules/e2e-module')

  await expect(page.getByTestId('module-metadata').getByText('e2e-module')).toBeVisible()
  await expect(page.getByTestId('module-metadata').getByText('catalog')).toBeVisible()
  await expect(page.getByTestId('module-adventures').getByText('plugin:module_quest')).toBeVisible()
  // 该模组没有被任何存档绑定 → 服务端 guard 判定受保护操作都可用；按钮文案则由
  // 服务端的模块状态决定（发现但未启用的包显示"启用"）。
  const actions = page.getByTestId('module-actions')
  await expect(actions.getByRole('button', { name: '更新' })).toBeEnabled()
  await expect(actions.getByRole('button', { name: '启用' })).toBeEnabled()
  await expect(actions.getByRole('button', { name: '卸载' })).toBeEnabled()
  await expect(page.getByTestId('module-action-blocked')).toHaveCount(0)
})

function adventureRecoveryUrl(digest: string): string {
  const dataDir = process.env.DICEFRAME_E2E_DATA_DIR!
  const save = JSON.parse(readFileSync(
    resolve(dataDir, 'saves', 'web#e2e-adventure#web_bot', 'state.json'), 'utf8',
  )) as { adventure_binding: Record<string, string> }
  const binding = save.adventure_binding
  const query = new URLSearchParams({
    adventure: binding.adventure_id,
    version: binding.version,
    digest,
    source_kind: binding.source_kind,
    source_id: binding.source_id,
    game: 'web|e2e-adventure|web_bot',
  })
  return `/#/modules?${query.toString()}`
}

test('module recovery validates the saved digest before offering resume', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  const dataDir = process.env.DICEFRAME_E2E_DATA_DIR!
  const save = JSON.parse(readFileSync(
    resolve(dataDir, 'saves', 'web#e2e-adventure#web_bot', 'state.json'), 'utf8',
  )) as { adventure_binding: Record<string, string> }

  // 指纹一致 → 服务端投影可用且来源一致 → 才出现"返回本局"。
  await page.goto(adventureRecoveryUrl(save.adventure_binding.content_digest))
  await expect(page.getByTestId('modules-recovery-ready')).toBeVisible()
  await expect(page.getByRole('link', { name: '返回本局' })).toBeVisible()

  // 同 id 不同 package：即使包可用也不得"恢复"，绝不自动绑定。
  await page.goto(adventureRecoveryUrl('sha256:0000000000000000000000000000000000000000000000000000000000000000'))
  await expect(page.getByTestId('modules-recovery-blocked')).toBeVisible()
  await expect(page.getByText('当前可用的模组内容与存档指纹不一致，不会自动绑定。')).toBeVisible()
  await expect(page.getByRole('link', { name: '返回本局' })).toHaveCount(0)
})

test('phone shell uses a compact header and fixed bottom navigation', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '游戏总览' })).toBeVisible()

  const layout = await page.evaluate(() => {
    const header = document.querySelector<HTMLElement>('.app-header')!
    const bottom = document.querySelector<HTMLElement>('.mobile-bottom-nav')!
    return {
      height: header.getBoundingClientRect().height,
      bottomPosition: getComputedStyle(bottom).position,
      bottomLinks: bottom.querySelectorAll('a').length,
      bottomLeft: bottom.getBoundingClientRect().left,
      bottomRight: bottom.getBoundingClientRect().right,
      viewportWidth: window.innerWidth,
    }
  })

  expect(layout.height).toBeLessThanOrEqual(52)
  expect(layout.bottomPosition).toBe('fixed')
  expect(layout.bottomLinks).toBeGreaterThan(0)
  expect(Math.abs(layout.bottomLeft)).toBeLessThanOrEqual(1)
  expect(Math.abs(layout.viewportWidth - layout.bottomRight)).toBeLessThanOrEqual(1)
})

test('phone security setup stacks ACME guidance above the action button', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/settings?section=security')

  // Let's Encrypt 申请流程默认折叠在「申请配置」里：先展开再断言
  await page.locator('.security-lets-encrypt-setup .n-collapse-item__header').click()

  const actions = page.locator('.security-acme-actions')
  const hint = actions.locator('small')
  const button = actions.getByRole('button', { name: '申请并启用' })
  await expect(actions).toBeVisible()
  await expect(hint).toContainText('填写完成后点击“申请并启用”')
  await expect(page.getByText(/证书签发和自动续期都要求公网 TCP 80 可访问/)).toBeVisible()
  await expect(button).toBeVisible()

  const layout = await actions.evaluate(element => {
    const hint = element.querySelector<HTMLElement>('small')!.getBoundingClientRect()
    const button = element.querySelector<HTMLElement>('button')!.getBoundingClientRect()
    const bounds = element.getBoundingClientRect()
    return {
      flexDirection: getComputedStyle(element).flexDirection,
      hintBottom: hint.bottom,
      buttonTop: button.top,
      buttonWidth: button.width,
      containerWidth: bounds.width,
    }
  })

  expect(layout.flexDirection).toBe('column')
  expect(layout.hintBottom).toBeLessThanOrEqual(layout.buttonTop)
  expect(Math.abs(layout.containerWidth - layout.buttonWidth)).toBeLessThanOrEqual(1)
})

test('long admin pages keep the workspace background through all content', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.addInitScript(() => localStorage.setItem('currentGame', 'web|e2e-room|web_bot'))
  await page.goto('/#/lorebook')
  await expect(page.locator('.lorebook-page')).toBeVisible()

  const geometry = await page.evaluate(() => {
    const workspace = document.querySelector<HTMLElement>('.app-workspace')!.getBoundingClientRect()
    const pageView = document.querySelector<HTMLElement>('.lorebook-page')!.getBoundingClientRect()
    return {
      viewportHeight: window.innerHeight,
      workspaceBottom: workspace.bottom,
      pageBottom: pageView.bottom,
      workspaceHeight: workspace.height,
    }
  })

  if (geometry.pageBottom > geometry.viewportHeight) {
    expect(geometry.workspaceHeight).toBeGreaterThan(geometry.viewportHeight)
  }
  expect(geometry.workspaceBottom).toBeGreaterThanOrEqual(Math.max(geometry.pageBottom, geometry.viewportHeight) - 1)
})

test('phone play side panels open as drawers without entering document flow', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  await expect(page.getByRole('heading', { name: 'E2E Adventure' })).toBeVisible()

  const sidebar = page.locator('.game-sidebar')
  const controls = page.locator('.play-control-rail')
  await expect(sidebar).toBeHidden()
  await expect(controls).toBeHidden()

  await page.getByRole('button', { name: '状态' }).click()
  await expect(sidebar).toBeVisible()
  const sidebarBounds = await sidebar.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return { top: bounds.top, bottom: bounds.bottom, viewportHeight: window.innerHeight }
  })
  expect(Math.abs(sidebarBounds.top)).toBeLessThanOrEqual(1)
  expect(Math.abs(sidebarBounds.viewportHeight - sidebarBounds.bottom)).toBeLessThanOrEqual(1)
  await sidebar.getByRole('button', { name: '收起侧栏' }).click()
  await expect(sidebar).toBeHidden()

  await page.getByRole('button', { name: '控台' }).click()
  await expect(controls).toBeVisible()
  const controlBounds = await controls.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return { top: bounds.top, bottom: bounds.bottom, viewportHeight: window.innerHeight }
  })
  expect(Math.abs(controlBounds.top)).toBeLessThanOrEqual(1)
  expect(Math.abs(controlBounds.viewportHeight - controlBounds.bottom)).toBeLessThanOrEqual(1)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
  await controls.locator('.rail-toggle').click()
  await expect(controls).toBeHidden()
})

test('equipment details modal stays above the open phone character drawer', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  await expect(page.getByRole('heading', { name: 'E2E Adventure' })).toBeVisible()

  await page.getByRole('button', { name: '状态' }).click()
  const sidebar = page.locator('.game-sidebar')
  await expect(sidebar).toBeVisible()
  await sidebar.locator('summary').filter({ hasText: '装备与背包' }).click()
  await sidebar.getByRole('button', { name: '查看全部详情' }).click()

  const modal = page.locator('body > .modal')
  await expect(modal).toBeVisible()
  await expect(modal.getByRole('heading', { name: '装备与背包' })).toBeVisible()
  const layers = await page.evaluate(() => ({
    modal: Number.parseInt(getComputedStyle(document.querySelector<HTMLElement>('body > .modal')!).zIndex, 10),
    drawer: Number.parseInt(getComputedStyle(document.querySelector<HTMLElement>('.game-sidebar')!).zIndex, 10),
  }))
  expect(layers.modal).toBeGreaterThan(layers.drawer)
})

test('phone play keeps scene metadata compact and actions anchored to the viewport bottom', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  await expect(page.locator('.composer')).toBeVisible()
  await page.locator('.composer').scrollIntoViewIfNeeded()

  const layout = await page.evaluate(() => {
    const scene = document.querySelector<HTMLElement>('.scene-strip')!
    const composer = document.querySelector<HTMLElement>('.composer')!
    const header = document.querySelector<HTMLElement>('.app-header')!
    const nav = document.querySelector<HTMLElement>('.mobile-bottom-nav')!
    return {
      sceneHeight: scene.getBoundingClientRect().height,
      composerBottom: composer.getBoundingClientRect().bottom,
      viewportHeight: window.innerHeight,
      headerHidden: getComputedStyle(header).display === 'none',
      navHidden: getComputedStyle(nav).display === 'none',
    }
  })
  // 沉浸式对局页：顶栏/底导隐藏，输入区贴到视口底边，不再受底部导航遮挡
  expect(layout.headerHidden).toBe(true)
  expect(layout.navHidden).toBe(true)
  expect(layout.sceneHeight).toBeLessThanOrEqual(82)
  expect(layout.composerBottom).toBeGreaterThanOrEqual(layout.viewportHeight - 12)
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight + 1)
})

test('phone play has no spacer bands around the game workspace', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  await expect(page.locator('.composer')).toBeVisible()

  const geometry = await page.evaluate(() => {
    const header = document.querySelector<HTMLElement>('.app-header')!
    const workspace = document.querySelector<HTMLElement>('.app-workspace')!.getBoundingClientRect()
    const pageView = document.querySelector<HTMLElement>('.play-page')!.getBoundingClientRect()
    const hud = document.querySelector<HTMLElement>('.play-hud')!.getBoundingClientRect()
    const main = document.querySelector<HTMLElement>('.play-main')!.getBoundingClientRect()
    const nav = document.querySelector<HTMLElement>('.mobile-bottom-nav')!
    return {
      headerHidden: getComputedStyle(header).display === 'none',
      navHidden: getComputedStyle(nav).display === 'none',
      workspaceTop: workspace.top,
      hudGap: hud.top - pageView.top,
      pageBottomGap: window.innerHeight - pageView.bottom,
      mainBottomGap: pageView.bottom - main.bottom,
    }
  })

  // 沉浸式对局页：顶栏/底导隐藏，对局内容从视口顶铺满到底，无 spacer band
  expect(geometry.headerHidden).toBe(true)
  expect(geometry.navHidden).toBe(true)
  expect(Math.abs(geometry.workspaceTop)).toBeLessThanOrEqual(1)
  expect(Math.abs(geometry.hudGap)).toBeLessThanOrEqual(1)
  expect(Math.abs(geometry.pageBottomGap)).toBeLessThanOrEqual(1)
  expect(Math.abs(geometry.mainBottomGap)).toBeLessThanOrEqual(1)
})

test('phone play opens the scene map as a full-screen workspace', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/play?game=web%7Ce2e-room%7Cweb_bot')
  await expect(page.locator('.composer')).toBeVisible()

  await page.getByRole('button', { name: '地图', exact: true }).click()
  const workspace = page.locator('.map-workspace-shell')
  await expect(workspace).toBeVisible()
  await expect(workspace.getByPlaceholder('搜索地点或关键词')).toBeVisible()
  const background = workspace.locator('.map-background-image')
  await expect(background).toHaveAttribute('src', /fantasy-region-v1\.webp$/)
  await expect(background).toBeVisible()
  const mapSvg = workspace.locator('.map-svg')
  const mapBounds = await mapSvg.boundingBox()
  if (!mapBounds) throw new Error('map viewport has no bounds')
  const backgroundBefore = await background.boundingBox()
  const viewBoxBefore = await mapSvg.getAttribute('viewBox')
  await page.mouse.move(mapBounds.x + mapBounds.width / 2, mapBounds.y + mapBounds.height / 2)
  await page.mouse.down()
  await page.mouse.move(mapBounds.x + mapBounds.width + 240, mapBounds.y + mapBounds.height + 240, { steps: 4 })
  await page.mouse.up()
  const viewBoxAfterDrag = await mapSvg.getAttribute('viewBox')
  expect(viewBoxAfterDrag).not.toBe(viewBoxBefore)
  await page.mouse.move(mapBounds.x + mapBounds.width / 2, mapBounds.y + mapBounds.height / 2)
  await page.mouse.wheel(0, -600)
  await expect.poll(() => mapSvg.getAttribute('viewBox')).not.toBe(viewBoxAfterDrag)
  const backgroundAfter = await background.boundingBox()
  expect(backgroundBefore).not.toBeNull()
  expect(backgroundAfter).not.toBeNull()
  for (const key of ['x', 'y', 'width', 'height'] as const) {
    expect(Math.abs(backgroundAfter![key] - backgroundBefore![key])).toBeLessThanOrEqual(1)
  }
  const coverage = await background.evaluate(element => {
    const image = element.getBoundingClientRect()
    const viewport = element.closest('.map-viewport')!.getBoundingClientRect()
    return {
      left: image.left - viewport.left,
      top: image.top - viewport.top,
      right: image.right - viewport.right,
      bottom: image.bottom - viewport.bottom,
    }
  })
  expect(coverage.left).toBeLessThanOrEqual(1)
  expect(coverage.top).toBeLessThanOrEqual(1)
  expect(coverage.right).toBeGreaterThanOrEqual(-1)
  expect(coverage.bottom).toBeGreaterThanOrEqual(-1)
  const bounds = await workspace.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom, width: innerWidth, height: innerHeight }
  })
  expect(Math.abs(bounds.top)).toBeLessThanOrEqual(1)
  expect(Math.abs(bounds.left)).toBeLessThanOrEqual(1)
  expect(Math.abs(bounds.width - bounds.right)).toBeLessThanOrEqual(1)
  expect(Math.abs(bounds.height - bounds.bottom)).toBeLessThanOrEqual(1)
  const titleIcon = await workspace.locator('.map-workspace-title-icon').evaluate(element => {
    const box = element.getBoundingClientRect()
    const svg = element.querySelector('svg')!.getBoundingClientRect()
    return {
      box: { left: box.left, top: box.top, right: box.right, bottom: box.bottom },
      svg: { left: svg.left, top: svg.top, right: svg.right, bottom: svg.bottom },
    }
  })
  expect(titleIcon.svg.left).toBeGreaterThanOrEqual(titleIcon.box.left)
  expect(titleIcon.svg.top).toBeGreaterThanOrEqual(titleIcon.box.top)
  expect(titleIcon.svg.right).toBeLessThanOrEqual(titleIcon.box.right)
  expect(titleIcon.svg.bottom).toBeLessThanOrEqual(titleIcon.box.bottom)
  await workspace.getByRole('button', { name: '关闭' }).click()
  await expect(workspace).toBeHidden()
})

test('phone character actions can scroll clear of bottom navigation', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/characters')
  const finalAction = page.locator('.shared-character-section button').last()
  await finalAction.scrollIntoViewIfNeeded()
  const positions = await page.evaluate(() => {
    const action = document.querySelectorAll<HTMLElement>('.shared-character-section button')
    const nav = document.querySelector<HTMLElement>('.mobile-bottom-nav')!
    return {
      actionBottom: action[action.length - 1].getBoundingClientRect().bottom,
      navTop: nav.getBoundingClientRect().top,
    }
  })
  expect(positions.actionBottom).toBeLessThanOrEqual(positions.navTop)
})

test('login card remains exactly centered at narrow widths', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.clear()
    localStorage.setItem('diceframe_locale', 'zh-CN')
  })

  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/login')
    await expect(page.locator('.login-card')).toBeVisible()
    const geometry = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>('.login-card')!.getBoundingClientRect()
      return {
        leftGap: card.left,
        rightGap: window.innerWidth - card.right,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }
    })
    expect(Math.abs(geometry.leftGap - geometry.rightGap), `login gap mismatch at ${width}px`).toBeLessThanOrEqual(1)
    expect(geometry.overflow, `login overflow at ${width}px`).toBe(0)
  }
})

test('character management remains contained on narrow phones', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => {
    localStorage.setItem('trpg_access_token', value)
    localStorage.setItem('diceframe_locale', 'zh-CN')
    localStorage.setItem('currentGame', 'web|e2e-room|web_bot')
  }, token)

  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/characters')
    await expect(page.locator('.characters-page')).toBeVisible()
    await expect(page.locator('.current-character-card').first()).toBeVisible()
    const geometry = await page.evaluate(() => {
      const pageView = document.querySelector<HTMLElement>('.characters-page')!.getBoundingClientRect()
      const cards = Array.from(document.querySelectorAll<HTMLElement>('.current-character-card'))
      const actions = Array.from(document.querySelectorAll<HTMLElement>('.current-character-actions'))
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        pageRight: pageView.right,
        cardRight: Math.max(...cards.map(card => card.getBoundingClientRect().right)),
        actionRight: Math.max(...actions.map(action => action.getBoundingClientRect().right)),
      }
    })
    expect(geometry.overflow, `character page overflow at ${width}px`).toBe(0)
    expect(geometry.pageRight).toBeLessThanOrEqual(width + 1)
    expect(geometry.cardRight).toBeLessThanOrEqual(width + 1)
    expect(geometry.actionRight).toBeLessThanOrEqual(width + 1)
  }
})

test('character management stays contained with German locale', async ({ page }) => {
  const token = accessToken()
  await page.addInitScript(value => {
    localStorage.setItem('trpg_access_token', value)
    localStorage.setItem('diceframe_locale', 'de')
    localStorage.setItem('currentGame', 'web|e2e-room|web_bot')
  }, token)

  for (const width of [900, 1280]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/characters')
    await expect(page.locator('.characters-page')).toBeVisible()
    await expect(page.locator('.current-character-card').first()).toBeVisible()
    const geometry = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll<HTMLElement>('.current-character-actions button'))
      const cards = Array.from(document.querySelectorAll<HTMLElement>('.current-character-card'))
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        pageRight: document.querySelector<HTMLElement>('.characters-page')!.getBoundingClientRect().right,
        cardRight: Math.max(...cards.map(card => card.getBoundingClientRect().right)),
        buttonRight: Math.max(...buttons.map(button => button.getBoundingClientRect().right)),
        longestText: buttons.map(button => button.textContent?.trim() ?? '').join('|'),
      }
    })
    expect(geometry.overflow, `character page overflow at ${width}px (de)`).toBe(0)
    expect(geometry.pageRight).toBeLessThanOrEqual(width + 1)
    expect(geometry.cardRight).toBeLessThanOrEqual(width + 1)
    expect(geometry.buttonRight).toBeLessThanOrEqual(width + 1)
    // 德语回退英文：操作按钮不应出现中文
    expect(geometry.longestText).not.toMatch(/[一-鿿]/)
  }
})
