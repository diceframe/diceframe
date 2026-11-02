import { expect, test } from './fixtures'
import { encodePeerInvites } from '../src/features/peer/inviteCode'
import { PLAY_READY_TIMEOUT } from './support'

const peerConfig = {
  ok: true,
  enabled: true,
  entry_visible: true,
  load_level: 'normal',
  max_peers_per_room: 6,
  retry_after: 15,
  message: '',
}

function makePeerInvite(): string {
  const [invite] = encodePeerInvites({
    ok: true,
    protocol_version: 2,
    topology: 'host-star',
    room_code: 'ABCDEFGH',
    host_peer_id: 'h_hostpeer12345',
    host_token: 'host-token',
    invitations: [{ peer_id: 'p_guestpeer12345', token: 'guest-token-with-at-least-thirty-two-characters' }],
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    websocket_url: 'ws://127.0.0.1:9/v1/rendezvous/rooms/ABCDEFGH/ws',
  }, [], 'web|e2e-room|web_bot')
  return invite.inviteCode
}

async function mockPeerConfig(page: import('@playwright/test').Page): Promise<void> {
  await page.route('**/api/hub/rendezvous/config*', route => route.fulfill({ json: peerConfig }))
}

test('homepage keeps a normal invitation URL intact without navigating', async ({ page }, testInfo) => {
  await mockPeerConfig(page)
  await page.goto('/#/overview')
  await page.getByTestId('overview-open-join').click()
  await expect(page.locator('.friend-join-panel h2')).toHaveText('加入好友')

  const beforePaste = page.url()
  const invitation = 'https://friends.example/backend/trpg/#/join?game=web%7Ce2e-room%7Cweb_bot&share=1&server=https%3A%2F%2Fapi.example%2Fv1&seat=seat-token%2Fwith%3Fchars'
  await page.getByTestId('friend-join-input').fill(invitation)

  await expect(page).toHaveURL(beforePaste)
  const openLink = page.getByTestId('friend-join-open-link')
  await expect(openLink).toHaveAttribute('href', invitation)
  await expect(openLink).toHaveAttribute('target', '_blank')
  await expect(openLink).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(page.getByTestId('friend-join-peer')).toHaveCount(0)
  await expect(page.locator('.peer-direct-consent')).toHaveCount(0)
  const dialog = await page.locator('.peer-connect-modal').boundingBox()
  const input = await page.getByTestId('friend-join-input').boundingBox()
  expect(input!.width).toBeGreaterThan(dialog!.width * 0.8)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: testInfo.outputPath('friend-invitation.png') })
})

test('direct-connect page opens an ordinary invitation locally without P2P controls', async ({ page }) => {
  await mockPeerConfig(page)
  await page.goto('/#/peer')
  await page.getByRole('button', { name: '我要加入', exact: true }).click()

  const invitation = 'https://friends.example/backend/trpg/#/join?game=web%7Ce2e-room%7Cweb_bot&share=1&server=https%3A%2F%2Fapi.example%2Fv1&seat=seat-token%2Fwith%3Fchars'
  await page.getByLabel('邀请链接或直连码').fill(invitation)
  const openLink = page.getByTestId('peer-open-invitation')
  await expect(openLink).toBeVisible()
  await expect(openLink).toHaveAttribute('href', invitation)
  await expect(page.locator('.peer-direct-consent')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '连接房主' })).toHaveCount(0)
})

test('homepage explains a missing game and recognizes the corrected invitation', async ({ page }) => {
  await mockPeerConfig(page)
  await page.goto('/#/overview')
  await page.getByTestId('overview-open-join').click()
  const input = page.getByTestId('friend-join-input')

  await input.fill('https://friends.example/#/join?share=1')
  await expect(page.getByRole('alert')).toContainText('链接缺少游戏信息')

  await input.fill('https://friends.example/#/join?game=web%7Ce2e-room%7Cweb_bot&share=1')
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByTestId('friend-join-open-link')).toBeVisible()
})

test('a valid direct invite enters guest setup without connecting before consent and submit', async ({ page }) => {
  await mockPeerConfig(page)
  const sockets: string[] = []
  const roomRequests: string[] = []
  page.on('websocket', socket => sockets.push(socket.url()))
  page.on('request', request => {
    if (request.url().includes('/api/hub/rendezvous/rooms')) roomRequests.push(request.url())
  })

  await page.goto('/#/overview')
  await page.getByTestId('overview-open-join').click()
  const code = makePeerInvite()
  await page.getByTestId('friend-join-input').fill(code)
  await page.getByTestId('friend-join-peer').click()

  const inviteInput = page.locator('.peer-connect-step').getByLabel('邀请链接或直连码')
  await expect(inviteInput).toHaveValue(code)
  const consent = page.locator('.peer-direct-consent input')
  const connect = page.getByRole('button', { name: '连接房主' })
  await expect(consent).not.toBeChecked()
  await expect(connect).toBeDisabled()
  await expect(page.locator('.peer-state-connected')).toHaveCount(0)
  expect(sockets).toEqual([])
  expect(roomRequests).toEqual([])

  await consent.check()
  await expect(connect).toBeEnabled()
  expect(sockets).toEqual([])
  expect(roomRequests).toEqual([])
})

test('the play invitation opens official multiplayer help while keeping the invitation available', async ({ page }, info) => {
  await page.goto(`/#/play?game=${encodeURIComponent('web|e2e-room|web_bot')}`)
  await expect(page.getByTestId('timeline')).toBeVisible({ timeout: PLAY_READY_TIMEOUT })
  const party = page.getByTestId('play-party-rail')
  if (!await party.isVisible()) {
    await page.getByTestId('play-tools-toggle').click()
    await page.getByTestId('play-tools-panel').getByRole('button', { name: '队伍', exact: true }).click()
  }
  await party.getByTestId('play-invite-player').click()
  const dialog = page.getByRole('dialog', { name: '邀请链接', exact: true })
  const help = dialog.getByTestId('invite-multiplayer-help')
  await expect(help).toBeVisible()
  await expect(help).toBeInViewport()
  await expect(help).toHaveAttribute('href', 'https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md#外网联机')
  await expect(dialog.locator('.invite-link')).toBeVisible()
  await expect(dialog).toContainText('首页“加入好友”')
  await page.screenshot({ path: info.outputPath('invitation-help.png') })

  await page.context().route('https://github.com/diceframe/diceframe-content/**', route => route.fulfill({
    contentType: 'text/html', body: '<title>Multiplayer guide</title>',
  }))
  const [guide] = await Promise.all([page.waitForEvent('popup'), help.click()])
  await expect(guide).toHaveTitle('Multiplayer guide')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: '复制链接', exact: true })).toBeEnabled()
  await guide.close()
})
