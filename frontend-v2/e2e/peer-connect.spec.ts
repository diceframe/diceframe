import { expect, test } from '@playwright/test'
import { prepareAuthenticatedContext } from './support'

test('two clients establish a direct data channel through Hub signaling', async ({ browser, request }) => {
  const context = await browser.newContext({ locale: 'zh-CN' })
  await prepareAuthenticatedContext(context, request)
  const host = await context.newPage()
  const guest = await context.newPage()

  await host.goto('/#/peer')
  await host.getByLabel('要开放的多人冒险').selectOption({ index: 1 })
  await host.getByLabel('STUN 服务').selectOption('none')
  await host.locator('.peer-direct-consent input').check()
  await host.getByRole('button', { name: '创建临时直连房间' }).click()
  const invite = await host.locator('.peer-invite textarea').inputValue()
  expect(invite).toMatch(/^DFP2-/)

  await guest.goto('/#/peer')
  await guest.getByRole('button', { name: '我要加入' }).click()
  await guest.getByLabel('粘贴房主发来的链接码').fill(invite)
  await guest.locator('.peer-direct-consent input').check()
  await guest.getByRole('button', { name: '连接房主' }).click()

  await expect(host.getByText('P2P 直连成功', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(guest.getByText('P2P 直连成功', { exact: true })).toBeVisible({ timeout: 20_000 })
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

  await context.close()
})
