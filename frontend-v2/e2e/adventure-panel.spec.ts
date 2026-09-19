import { expect, test } from './fixtures'
import { accessToken, prepareAuthenticatedContext } from './support'

// FIX-00：Play 页的 AdventurePanel 走的是 game-scoped 投影端点。owner 与分享
// 玩家都必须能读到，且分享玩家只能看到公开部分（GM 秘密节点不得下发）。
const ADVENTURE_GAME = 'web|e2e-adventure|web_bot'

test('owner and shared player both read the bound adventure projection', async ({ browser, request }) => {
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const chars = await (await request.get(
    `/api/games/${encodeURIComponent(ADVENTURE_GAME)}/characters`,
    { headers },
  )).json()
  const player = chars.players.find((item: any) => item.user_id !== 'e2e-gm') || chars.players[0]

  const gmContext = await browser.newContext()
  await prepareAuthenticatedContext(gmContext, request)
  const gmPage = await gmContext.newPage()
  await gmPage.setViewportSize({ width: 1366, height: 900 })
  const gmAdventureStatuses: number[] = []
  gmPage.on('response', response => {
    if (response.url().includes('/adventure')) gmAdventureStatuses.push(response.status())
  })
  await gmPage.goto(`/#/play?game=${encodeURIComponent(ADVENTURE_GAME)}`)

  const playerContext = await browser.newContext()
  const playerPage = await playerContext.newPage()
  await playerPage.setViewportSize({ width: 390, height: 844 })
  const playerAdventureStatuses: number[] = []
  playerPage.on('response', response => {
    if (response.url().includes('/adventure')) playerAdventureStatuses.push(response.status())
  })
  await playerPage.goto(
    `/#/play?game=${encodeURIComponent(ADVENTURE_GAME)}&user=${encodeURIComponent(player.user_id)}&share=1`,
  )

  const gmPanel = gmPage.getByTestId('game-adventure-panel')
  const playerPanel = playerPage.getByTestId('game-adventure-panel')
  await expect(gmPanel).toBeVisible()
  await expect(playerPanel).toBeVisible()
  // GM 拿到完整图，分享玩家拿到过滤后的公开图。
  await expect(gmPanel).toContainText('E2E Secret Ritual')
  await expect(playerPanel).toContainText('E2E Public Gate')
  await expect(playerPanel).not.toContainText('E2E Secret Ritual')
  // 行动输入区仍然可见（Play 基础可用）。
  await expect(gmPage.locator('.composer')).toBeVisible()
  await expect(playerPage.locator('.composer')).toBeVisible()
  expect(gmAdventureStatuses.length).toBeGreaterThan(0)
  expect(gmAdventureStatuses.every(status => status === 200)).toBe(true)
  expect(playerAdventureStatuses.length).toBeGreaterThan(0)
  expect(playerAdventureStatuses.every(status => status === 200)).toBe(true)

  await gmContext.close()
  await playerContext.close()
})
