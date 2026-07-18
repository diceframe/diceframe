import { expect, test } from '@playwright/test'
import { accessToken } from './support'

const token = accessToken

test('gm and player render the same game through shared play components', async ({ browser, request }) => {
  const headers = { Authorization: `Bearer ${token()}` }
  const games = await (await request.get('/api/games', { headers })).json()
  const game = games.games.find((item: any) => item.player_count > 1 || item.solo_mode === false) || games.games[0]
  const chars = await (await request.get(`/api/games/${encodeURIComponent(game.game_key)}/characters`, { headers })).json()
  const player = chars.players.find((item: any) => item.user_id !== game.gm_uid) || chars.players[0]

  const gmContext = await browser.newContext()
  await gmContext.addInitScript(value => localStorage.setItem('trpg_access_token', value), token())
  const gmPage = await gmContext.newPage()
  await gmPage.setViewportSize({ width: 1366, height: 768 })
  await gmPage.goto(`/#/play?game=${encodeURIComponent(game.game_key)}`)

  const playerContext = await browser.newContext()
  const playerPage = await playerContext.newPage()
  await playerPage.setViewportSize({ width: 390, height: 844 })
  await playerPage.goto(`/#/play?game=${encodeURIComponent(game.game_key)}&user=${encodeURIComponent(player.user_id)}`)

  await expect(gmPage.getByTestId('timeline')).toBeVisible()
  await expect(playerPage.getByTestId('timeline')).toBeVisible()
  const gmComposerBottom = await gmPage.locator('.composer').evaluate(element => element.getBoundingClientRect().bottom)
  const playerComposerBottom = await playerPage.locator('.composer').evaluate(element => element.getBoundingClientRect().bottom)
  expect(gmComposerBottom).toBeLessThanOrEqual(768)
  expect(playerComposerBottom).toBeLessThanOrEqual(844)
  await expect(playerPage.getByRole('heading', { name: player.character_name, exact: true })).toBeVisible()
  await expect(playerPage.getByPlaceholder('用自然语言描述行动，不必选择属性、技能或目标')).toBeVisible()
  await gmContext.close()
  await playerContext.close()
})

test('generic invite opens free character creation without gm password', async ({ page, request }) => {
  const games = await (await request.get('/api/games', { headers: { Authorization: `Bearer ${token()}` } })).json()
  const game = games.games.find((item: any) => item.solo_mode === false) || games.games[0]
  await page.goto(`/#/join?game=${encodeURIComponent(game.game_key)}&share=1`)
  await expect(page.getByRole('heading', { name: '创建你的角色' })).toBeVisible()
  await expect(page.getByText('数字框可直接输入任意数值')).toBeVisible()
  await expect(page.getByRole('button', { name: '创建角色并进入' })).toBeVisible()
})

test('plugin settings are generated from manifest schema', async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token())
  await page.goto('/')
  await page.getByRole('link', { name: '设置' }).click()
  await page.getByRole('button', { name: '插件' }).click()
  await expect(page.getByRole('heading', { name: 'QQ / NapCat', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'NapCat 连接', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '聊天过滤', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '群聊名单', exact: true })).toBeVisible()
  await expect(page.getByLabel('屏蔽 QQ 官方机器人')).toBeChecked()
})
