import { expect, test } from './fixtures'
import { accessToken } from './support'

test('home entries have equal dimensions and place new adventure after browsing', async ({ page }, info) => {
  for (const peerVisible of [false, true]) {
    await page.route('**/api/hub/rendezvous/config', route => route.fulfill({
      json: { entry_visible: peerVisible },
    }))
    await page.goto('/#/overview')
    await page.reload()
    const actions = page.locator('.overview-actions .overview-entry-action')
    await expect(actions).toHaveCount(3)
    const boxes = await actions.evaluateAll(elements => elements.map(element => {
      const rect = element.getBoundingClientRect()
      return { text: element.textContent?.trim(), width: rect.width, height: rect.height }
    }))
    expect(boxes.map(box => box.text)).toEqual(['加入好友', '浏览内容', '创建新冒险'])
    expect(Math.max(...boxes.map(box => box.width)) - Math.min(...boxes.map(box => box.width))).toBeLessThanOrEqual(1)
    expect(Math.max(...boxes.map(box => box.height)) - Math.min(...boxes.map(box => box.height))).toBeLessThanOrEqual(1)
    expect(Math.min(...boxes.map(box => box.height))).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    if (peerVisible) await page.screenshot({ path: info.outputPath('home-actions.png') })
    await page.unroute('**/api/hub/rendezvous/config')
  }
  await page.getByTestId('overview-create-adventure').click()
  await expect(page).toHaveURL(/#\/create/)
})

test('complete world background and opening can be viewed, cloned and edited persistently', async ({ page, request }, info) => {
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  const sourceResponse = await request.get('/api/worlds/coc_horror/definition?language=zh-CN', { headers })
  expect(sourceResponse.ok()).toBe(true)
  const source = (await sourceResponse.json()).world
  expect(source.world_setting.length).toBeGreaterThan(source.description.length)
  expect(source.starter_scene).toBeTruthy()
  let clonedId = ''
  const background = `验收背景 ${info.project.name}\n1928 年的港口被异常潮汐包围。调查员需要查明钟楼的秘密。`
  const opening = `验收开场 ${info.project.name}\n一封没有邮戳的信送到了调查员门前。`
  try {
    await page.goto('/#/worlds')
    const card = page.locator('.world-card[data-world-id="coc_horror"]')
    await card.getByRole('button', { name: '预览', exact: true }).click()
    let dialog = page.getByRole('dialog')
    await expect(dialog.getByTestId('world-setting')).toHaveValue(source.world_setting)
    await expect(dialog.getByTestId('world-setting')).toHaveAttribute('readonly', '')
    await expect(dialog.getByTestId('starter-scene')).toHaveValue(source.starter_scene)
    const cloned = page.waitForResponse(response => response.url().endsWith('/api/worlds/clone-from-template') && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: '克隆为我的世界并编辑', exact: true }).click()
    const result = await (await cloned).json()
    expect(result.ok).toBe(true)
    clonedId = result.world_id
    await expect(dialog.getByTestId('world-setting')).not.toHaveAttribute('readonly', '')
    await expect(dialog.getByTestId('world-setting')).toHaveValue(source.world_setting)
    await dialog.getByTestId('world-setting').fill(background)
    await dialog.getByTestId('starter-scene').fill(opening)
    await dialog.locator('.world-metadata-editor textarea').fill('验收用简要介绍')
    await page.screenshot({ path: info.outputPath('world-definition-editor.png') })
    expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    const saved = await request.get(`/api/worlds/${encodeURIComponent(clonedId)}/definition`, { headers })
    expect(saved.ok()).toBe(true)
    expect((await saved.json()).world).toMatchObject({
      source: 'user', editable: true, description: '验收用简要介绍',
      world_setting: background, starter_scene: opening,
    })
    // Re-enter from the public route after a reload, with no retained form state.
    await page.goto(`/#/worlds?world=${encodeURIComponent(clonedId)}&edit=1`)
    await page.reload()
    dialog = page.getByRole('dialog')
    await expect(dialog.getByTestId('world-setting')).toHaveValue(background)
    await expect(dialog.getByTestId('starter-scene')).toHaveValue(opening)
    await dialog.locator('.world-metadata-editor textarea').fill('再次修改简介')
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    const reloaded = (await (await request.get(`/api/worlds/${encodeURIComponent(clonedId)}/definition`, { headers })).json()).world
    expect(reloaded.world_setting).toBe(background)
    expect(reloaded.starter_scene).toBe(opening)
    const untouched = (await (await request.get('/api/worlds/coc_horror/definition?language=zh-CN', { headers })).json()).world
    expect(untouched).toEqual(source)
  } finally {
    if (clonedId) expect((await request.delete(`/api/worlds/${encodeURIComponent(clonedId)}`, { headers })).ok()).toBe(true)
  }
})
