import { expect, test } from '@playwright/test'
import { accessToken, prepareAuthenticatedContext } from './support'

const DND_GAME_KEY = 'web|e2e-dnd2024|web_bot'

test('two clients establish a direct data channel through Hub signaling', async ({ browser, request }) => {
  const context = await browser.newContext({ locale: 'zh-CN' })
  try {
    await prepareAuthenticatedContext(context, request)
    const host = await context.newPage()
    const guest = await context.newPage()

  await host.goto('/#/peer')
  await host.getByLabel('要开放的多人冒险').selectOption('web|e2e-room|web_bot')
  await host.getByLabel('STUN 服务').selectOption('none')
  await host.locator('.peer-direct-consent input').check()
  await host.getByRole('button', { name: '创建临时直连房间' }).click()
  // 批量开房会同时生成已绑定角色与空闲席位的链接；该流程需要空闲席位来创建新角色。
  const invitePanel = host.locator('.peer-status .peer-invite')
  await expect(invitePanel).toContainText('玩家邀请码')
  const invite = await invitePanel.getByLabel('新玩家 1 的一次性链接码').getByRole('textbox').inputValue()
  expect(invite).toMatch(/^DFP2-/)

  await guest.goto('/#/peer')
  await guest.getByRole('button', { name: '我要加入' }).click()
  await guest.getByLabel('邀请链接或直连码').fill(invite)
  await guest.locator('.peer-direct-consent input').check()
  await guest.getByRole('button', { name: '连接房主' }).click()

  await expect(host.locator('.peer-status > header .peer-state-connected')).toHaveText('P2P 直连成功', {
    timeout: 20_000,
  })
  await expect(guest.locator('.peer-status > header .peer-state-connected')).toHaveText('P2P 直连成功', {
    timeout: 20_000,
  })
  await expect(host.locator('.peer-invite-peer-state.peer-state-connected')).toHaveCount(1)
  // 通道状态块进入 active 即代表心跳自检通过；不锁具体文案，避免文案调整破坏 e2e。
  await expect(host.locator('.peer-connection-check.active')).toBeVisible()
  await expect(guest.locator('.peer-connection-check.active')).toBeVisible()
  await expect(host.locator('.peer-message-form')).toHaveCount(0)
  await expect(guest.locator('.peer-message-form')).toHaveCount(0)

  await guest.getByRole('button', { name: '进入冒险' }).click()
  await expect(guest.getByRole('heading', { name: '创建你的角色' })).toBeVisible()
  await guest.getByLabel('角色名').fill('Peer E2E Player')
  await guest.getByRole('button', { name: '创建角色并进入' }).click()
  await expect(guest).toHaveURL(/#\/play\?/, { timeout: 20_000 })
  // 移动端角色面板默认收起，名字节点存在但隐藏；用 attached 断言身份已建立即可。
  await expect(guest.getByText('Peer E2E Player').first()).toBeAttached({ timeout: 20_000 })

  } finally {
    await context.close()
  }
})

test('a separate peer client joins a DND 2024 table with a canonical professional character', async ({ browser, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one full two-client authority check is sufficient')
  test.setTimeout(60_000)

  const hostContext = await browser.newContext({ locale: 'zh-CN' })
  const guestContext = await browser.newContext({ locale: 'zh-CN' })
  try {
    await Promise.all([
      prepareAuthenticatedContext(hostContext, request),
      prepareAuthenticatedContext(guestContext, request),
    ])
    const host = await hostContext.newPage()
    const guest = await guestContext.newPage()

  await host.goto('/#/peer')
  await guest.goto('/#/peer')
  await guest.getByRole('button', { name: '我要加入' }).click()
  await host.getByLabel('要开放的多人冒险').selectOption(DND_GAME_KEY)
  await host.getByLabel('STUN 服务').selectOption('none')
  await host.locator('.peer-direct-consent input').check()
  await host.getByRole('button', { name: '创建临时直连房间' }).click()
  const invite = await host
    .getByLabel('新玩家 1 的一次性链接码')
    .getByRole('textbox')
    .inputValue()

  await guest.getByLabel('邀请链接或直连码').fill(invite)
  await guest.locator('.peer-direct-consent input').check()
  await guest.getByRole('button', { name: '连接房主' }).click()
  await expect(guest.locator('.peer-state-connected')).toContainText('P2P 直连成功', { timeout: 20_000 })

  await guest.getByRole('button', { name: '进入冒险' }).click()
  const builder = guest.locator('.ruleset-experience--dnd2024')
  await expect(builder.getByRole('heading', { name: '创建你的冒险者' })).toBeVisible()
  await builder.locator('.preset-card').first().click()
  await builder.getByLabel('角色名').fill('直连守护者')
  await builder.getByRole('button', { name: '完成并使用这个角色' }).click()

  await expect(guest).toHaveURL(/#\/play\?/, { timeout: 20_000 })
  await expect(guest.getByText('直连守护者').first()).toBeAttached()
  await expect(guest.locator('[data-testid="dnd5e-campaign-tool"]:visible')).toBeVisible()

  // 专业角色的资料更新走独立 PATCH 语义；验证它也经由绑定身份的 P2P 白名单。
  await guest.getByRole('button', { name: '高级角色中心' }).click()
  const characterCenter = guest.locator('.professional-character-center')
  await characterCenter.getByRole('button', { name: '人物资料' }).click()
  await characterCenter.getByLabel('仅供自己记录').fill('由双客户端直连保存')
  await characterCenter.getByRole('button', { name: '保存人物资料' }).click()
  await expect(characterCenter).toHaveCount(0)

  const charactersResponse = await request.get(
    `/api/games/${encodeURIComponent(DND_GAME_KEY)}/characters`,
    { headers: { Authorization: `Bearer ${accessToken()}` } },
  )
  expect(charactersResponse.ok()).toBe(true)
  const characters = await charactersResponse.json() as {
    ruleset_runtime?: { id?: string }
    players?: Array<{
      character_name?: string
      character_sheet?: { ruleset_character?: { profile?: { notes?: string } } }
    }>
  }
  expect(characters.ruleset_runtime?.id).toBe('core:dnd2024')
  const joined = characters.players?.find(player => player.character_name === '直连守护者')
  expect(joined?.character_sheet?.ruleset_character).toBeTruthy()
  expect(joined?.character_sheet?.ruleset_character?.profile?.notes).toBe('由双客户端直连保存')

  } finally {
    await Promise.all([hostContext.close(), guestContext.close()])
  }
})
