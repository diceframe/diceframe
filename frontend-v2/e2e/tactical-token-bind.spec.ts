import { expect, test } from './fixtures'
import { accessToken } from './support'

// Token detail + token→actor binding (frontend-05). Uses the same fixed games as
// tactical-scene-editor.spec (both seed player e2e-gm) with unique scene ids so
// the desktop / mobile projects never share a scene.
function gameKeyFor(project: string): string {
  return project === 'mobile' ? 'web|e2e-room|web_bot' : 'web|e2e-dnd2024|web_bot'
}

function uniqueId(prefix: string, project: string): string {
  return `${prefix}-${project}-${Date.now().toString(36)}`
}

function headers() {
  return { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' }
}

// A FVTT-imported token: actor_ref kind="actor" points at an actor M1 has not
// imported, so the detail panel must read "角色未导入" and never guess stats.
function fvttScene(sceneId: string, displayName: string) {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: sceneId,
    display_name: displayName,
    ownership: { default: 0, gm: 3 },
    dimensions: { width: 1200, height: 900, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 600, y: 450, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
    placeables: {
      tokens: {
        token_fvtt: {
          schema_version: 1, kind: 'token', canonical_id: 'token_fvtt',
          actor_ref: { source_kind: 'foundry', source_id: 'fvtt', kind: 'actor', id: 'actor00000000001', digest: '' },
          position: { x: 300, y: 400 }, size: { width: 1, height: 1 }, rotation: 0,
          disposition: -1, bars: { bar1: { attribute: 'attributes.hp' } },
          extensions: { name: 'Goblin' },
        },
      },
      walls: {},
      lights: {},
    },
  }
}

async function seedScene(request: import('@playwright/test').APIRequestContext, gameKey: string, sceneId: string, displayName: string) {
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const list = await (await request.get(`${base}/tactical-scenes`, { headers: headers() })).json()
  const created = await (await request.post(`${base}/tactical-scenes`, {
    headers: headers(), data: { scene: fvttScene(sceneId, displayName), expected_revision: list.revision },
  })).json()
  expect(created.ok, JSON.stringify(created)).toBe(true)
  return created.revision as number
}

async function readToken(request: import('@playwright/test').APIRequestContext, gameKey: string, sceneId: string) {
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const read = await (await request.get(`${base}/tactical-scenes/${sceneId}`, { headers: headers() })).json()
  expect(read.ok, JSON.stringify(read)).toBe(true)
  return read.scene.placeables.tokens.token_fvtt as Record<string, unknown>
}

async function openBoard(page: import('@playwright/test').Page, gameKey: string, sceneName: string) {
  await page.goto(`/#/tactical-scene?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
  const item = page.getByTestId('tactical-scene-item').filter({ hasText: sceneName })
  await expect(item).toBeVisible()
  await item.getByRole('button', { name: sceneName }).click()
  await expect(page.getByTestId('tactical-board')).toBeVisible()
}

test('GM sees the not-imported actor state, binds the token to a player and unbinds it', async ({ page, request }, info) => {
  test.setTimeout(120_000)
  const gameKey = gameKeyFor(info.project.name)
  const sceneId = uniqueId('e2e-bind', info.project.name)
  const sceneName = `Binding Chapel ${sceneId}`
  await seedScene(request, gameKey, sceneId, sceneName)

  await openBoard(page, gameKey, sceneName)
  const token = page.getByTestId('board-token').first()
  await expect(token).toBeVisible()
  await token.click()

  // Detail panel renders the token's own data and the not-imported actor state.
  const detail = page.getByTestId('token-detail')
  await expect(detail).toBeVisible()
  await expect(page.getByTestId('token-detail-name')).toHaveText('Goblin')
  await expect(page.getByTestId('token-actor-not-imported')).toHaveText('角色未导入')
  await expect(page.getByTestId('token-detail-disposition')).toHaveText('敌对')
  await expect(page.getByTestId('token-bar')).toContainText('attributes.hp')

  // Open the binding dialog and bind to the seeded player e2e-gm.
  await page.getByTestId('token-detail-bind').click()
  const dialog = page.getByTestId('token-bind-dialog')
  await expect(dialog).toBeVisible()
  await expect(page.getByTestId('token-bind-current')).toContainText('角色未导入')
  const playerTarget = page.getByTestId('token-bind-target-player-e2e-gm')
  await expect(playerTarget).toBeVisible()
  await playerTarget.check()
  await page.getByTestId('token-bind-submit').click()
  await expect(page.getByTestId('token-bind-success')).toHaveText('Token 绑定已保存。')

  // The committed actor_ref now matches the grid combat authority shape.
  const bound = await readToken(request, gameKey, sceneId)
  expect(bound.actor_ref).toMatchObject({ source_kind: 'diceframe', kind: 'player', id: 'e2e-gm', digest: '' })

  // Unbind clears the reference.
  await page.getByTestId('token-bind-unbind').click()
  await expect(page.getByTestId('token-bind-success')).toHaveText('Token 已解绑。')
  const unbound = await readToken(request, gameKey, sceneId)
  expect(unbound.actor_ref).toBeNull()

  await page.getByTestId('token-bind-cancel').click()
  await expect(dialog).toHaveCount(0)
  await page.screenshot({ path: info.outputPath(`tactical-bind-${info.project.name}.png`), fullPage: true })
})

test('a concurrent scene write surfaces a stale conflict; reload recovers the binding dialog', async ({ page, request }, info) => {
  test.setTimeout(120_000)
  const gameKey = gameKeyFor(info.project.name)
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const sceneId = uniqueId('e2e-bind-conflict', info.project.name)
  const sceneName = `Binding Chapel ${sceneId}`
  const revision = await seedScene(request, gameKey, sceneId, sceneName)

  await openBoard(page, gameKey, sceneName)
  await page.getByTestId('board-token').first().click()
  await page.getByTestId('token-detail-bind').click()
  await expect(page.getByTestId('token-bind-dialog')).toBeVisible()
  await page.getByTestId('token-bind-target-player-e2e-gm').check()

  // A concurrent write moves the slot revision forward.
  const bump = await (await request.post(`${base}/tactical-scenes`, {
    headers: headers(),
    data: {
      expected_revision: revision,
      scene: {
        schema_version: 1, kind: 'scene', canonical_id: uniqueId('e2e-bind-bump', info.project.name),
        display_name: 'Bump', dimensions: { width: 100, height: 100, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 50, y: 50, zoom: 1 } },
        grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
        placeables: { tokens: {}, walls: {}, lights: {} },
      },
    },
  })).json()
  expect(bump.ok, JSON.stringify(bump)).toBe(true)

  await page.getByTestId('token-bind-submit').click()
  const conflict = page.getByTestId('token-bind-conflict')
  await expect(conflict).toBeVisible()
  await expect(conflict).toContainText('场景已在别处更新')
  // Nothing was committed against the stale revision.
  expect((await readToken(request, gameKey, sceneId)).actor_ref).toMatchObject({ kind: 'actor' })

  // Reload pulls the current token and clears the conflict so binding can retry.
  await page.getByTestId('token-bind-conflict-reload').click()
  await expect(conflict).toHaveCount(0)
  await page.getByTestId('token-bind-target-player-e2e-gm').check()
  await page.getByTestId('token-bind-submit').click()
  await expect(page.getByTestId('token-bind-success')).toHaveText('Token 绑定已保存。')
  expect((await readToken(request, gameKey, sceneId)).actor_ref).toMatchObject({ kind: 'player', id: 'e2e-gm' })
})