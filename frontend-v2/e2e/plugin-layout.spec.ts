import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// 标签顺序与 PluginSettings.vue 的 NTabPane 顺序一致。
const TAB = { installed: 0, marketplace: 1, content: 2, themes: 3, tools: 4, mirrors: 5 } as const

async function openPluginTab(page: Page, index: number) {
  await page.goto('/#/plugins')
  await expect(page.locator('.plugin-workspace')).toBeVisible()
  await page.locator('.plugin-surface-tabs > .n-tabs-nav .n-tabs-tab').nth(index).click()
}

// 等待 NTabs animated 切换动画结束：布局稳定后元素才回到最终几何，
// 避免在过渡帧把动画偏移误报成真实溢出。
async function waitContained(page: Page, selector: string) {
  await expect.poll(async () => {
    const overshoot = await page.locator(selector).evaluateAll(elements => {
      const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
      return Math.max(...elements.map(element => element.getBoundingClientRect().right - workspace.right))
    })
    return Math.ceil(overshoot || 0)
  }, { timeout: 4000 }).toBeLessThanOrEqual(2)
}

// 读取真实几何：元素不得横向溢出工作区；同一行内高度一致。
async function expectContainedAndEven(page: Page, selector: string) {
  const items = page.locator(selector)
  await expect(items.first()).toBeVisible()
  await waitContained(page, selector)
  const { workspaceRight, geometry } = await items.evaluateAll(elements => {
    const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
    return {
      workspaceRight: workspace.right,
      geometry: elements.map(element => {
        const rect = element.getBoundingClientRect()
        return { top: Math.round(rect.top), height: rect.height, right: rect.right }
      }),
    }
  })
  for (const item of geometry) {
    expect(item.right).toBeLessThanOrEqual(Math.ceil(workspaceRight) + 1)
  }
  const rows = new Map<number, number[]>()
  for (const item of geometry) rows.set(item.top, [...(rows.get(item.top) || []), item.height])
  for (const heights of rows.values()) {
    if (heights.length < 2) continue
    expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(2)
  }
}

test('theme cards stay contained and even on desktop and phone', async ({ page }) => {
  await openPluginTab(page, TAB.themes)
  await expectContainedAndEven(page, '.builtin-theme-card')
})

test('mirror form and rows stay contained on desktop and phone', async ({ page }) => {
  await openPluginTab(page, TAB.mirrors)
  const form = page.locator('.mirror-form')
  await expect(form).toBeVisible()
  await waitContained(page, '.mirror-form')
  const box = await form.evaluate(element => {
    const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
    return { right: element.getBoundingClientRect().right, workspaceRight: workspace.right }
  })
  expect(box.right).toBeLessThanOrEqual(Math.ceil(box.workspaceRight) + 1)

  const rows = page.locator('.mirror-row')
  if (await rows.count()) {
    await expectContainedAndEven(page, '.mirror-row')
  }
})

test('install panel and store cards stay contained on desktop and phone', async ({ page }) => {
  await openPluginTab(page, TAB.installed)
  const install = page.locator('.plugin-install')
  await expect(install).toBeVisible()
  await waitContained(page, '.plugin-install')
  const installBox = await install.evaluate(element => {
    const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
    return { right: element.getBoundingClientRect().right, workspaceRight: workspace.right }
  })
  expect(installBox.right).toBeLessThanOrEqual(Math.ceil(installBox.workspaceRight) + 1)

  await openPluginTab(page, TAB.marketplace)
  await expectContainedAndEven(page, '.market-card')
})

test('content toolbar and tool groups stay contained on desktop and phone', async ({ page }) => {
  await openPluginTab(page, TAB.content)
  const toolbar = page.locator('.content-pack-toolbar')
  await expect(toolbar).toBeVisible()
  await waitContained(page, '.content-pack-toolbar')
  const toolbarBox = await toolbar.evaluate(element => {
    const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
    return { right: element.getBoundingClientRect().right, workspaceRight: workspace.right }
  })
  expect(toolbarBox.right).toBeLessThanOrEqual(Math.ceil(toolbarBox.workspaceRight) + 1)

  await openPluginTab(page, TAB.tools)
  const groups = page.locator('.plugin-tool-groups')
  if (await groups.count()) {
    await waitContained(page, '.plugin-tool-groups')
    const groupsBox = await groups.evaluate(element => {
      const workspace = document.querySelector('.plugin-workspace')!.getBoundingClientRect()
      return { right: element.getBoundingClientRect().right, workspaceRight: workspace.right }
    })
    expect(groupsBox.right).toBeLessThanOrEqual(Math.ceil(groupsBox.workspaceRight) + 1)
  } else {
    await expect(page.locator('.plugin-tool-groups, .plugin-workspace .muted').first()).toBeVisible()
  }
})
