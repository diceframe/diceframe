import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { accessToken } from './support'

// ROAD PR-5 / workplan §6.14: the grid HUD renders the server board, drag/click
// only emit a target_cell move intent, and theater keeps its single-axis track.
const GAME = 'web|e2e-dnd2024|web_bot'
const BASE = `/api/games/${encodeURIComponent(GAME)}`
const PLAYER = 'player:e2e-gm'

function headers() {
  return { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' }
}

// A combat-valid goblin stat block (mirrors tests/rulesets goblin()); a very low
// initiative modifier biases the first turn to the player so the drag is testable.
const GOBLIN = {
  id: 'goblin-1', name: 'Goblin', hp: 40, armor_class: 12, speed: 30, position: 5,
  initiative_modifier: -10, creature_type: 'humanoid',
  abilities: { str: 8, dex: 14, con: 10, int: 10, wis: 8, cha: 8 },
  saving_throws: { dex: 2, wis: -1, con: 0 },
  attacks: [{ id: 'scimitar', name: 'Scimitar', attack_bonus: 4, damage: '1d6+2', damage_type: 'slashing', range: 5 }],
}

function scenePayload(canonicalId: string) {
  const actor = (kind: string, id: string) => ({ source_kind: 'diceframe', source_id: 'e2e', kind, id, digest: '' })
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: canonicalId,
    display_name: 'Grid E2E Chapel',
    ownership: { default: 0, gm: 3 },
    dimensions: { width: 1200, height: 900 },
    grid: { type: 'square', size: 100, distance: 5.0, units: 'ft' },
    placeables: {
      tokens: {
        token_hero: {
          schema_version: 1, kind: 'token', canonical_id: 'token_hero',
          actor_ref: actor('player', 'e2e-gm'),
          position: { x: 100.0, y: 100.0 }, size: { width: 1.0, height: 1.0 },
        },
        token_goblin: {
          schema_version: 1, kind: 'token', canonical_id: 'token_goblin',
          actor_ref: actor('enemy', 'goblin-1'),
          position: { x: 500.0, y: 500.0 }, size: { width: 1.0, height: 1.0 },
        },
      },
      walls: {
        wall_far: {
          schema_version: 1, kind: 'wall', canonical_id: 'wall_far',
          endpoints: { x0: 850.0, y0: 0.0, x1: 850.0, y1: 400.0 },
          restrictions: { move: 1, sight: 0, sound: 0, light: 0 },
        },
      },
      lights: {},
    },
  }
}

async function gameplay(request: APIRequestContext) {
  const response = await request.get(`${BASE}/available-actions`, { headers: headers() })
  return response.json()
}

async function endActiveCombat(request: APIRequestContext) {
  const view = await gameplay(request)
  if (view?.gameplay?.combat?.status !== 'active') return
  await request.post(`${BASE}/intents`, {
    headers: headers(),
    data: {
      intent_id: `e2e-grid-cleanup-${Date.now()}`, type: 'combat.end',
      expected_version: view.gameplay.state_version ?? 0,
    },
  })
}

async function startCombat(request: APIRequestContext, positionMode?: string) {
  await endActiveCombat(request)
  const view = await gameplay(request)
  const response = await request.post(`${BASE}/intents`, {
    headers: headers(),
    data: {
      intent_id: `e2e-grid-start-${Date.now()}`, type: 'combat.start',
      expected_version: view.gameplay.state_version ?? 0,
      mode: 'sandbox', enemies: [GOBLIN],
      ...(positionMode ? { position_mode: positionMode } : {}),
    },
  })
  const body = await response.json()
  expect(body.ok, JSON.stringify(body)).toBe(true)
}

async function startGridCombat(request: APIRequestContext) {
  await endActiveCombat(request)
  const list = await (await request.get(`${BASE}/tactical-scenes`, { headers: headers() })).json()
  const canonicalId = `scene:grid-e2e-${Date.now()}`
  const created = await (await request.post(`${BASE}/tactical-scenes`, {
    headers: headers(),
    data: { expected_revision: list.revision, scene: scenePayload(canonicalId) },
  })).json()
  expect(created.ok, JSON.stringify(created)).toBe(true)
  const activated = await (await request.post(`${BASE}/tactical-scenes/${encodeURIComponent(canonicalId)}/activate`, {
    headers: headers(), data: { expected_revision: list.revision + 1 },
  })).json()
  expect(activated.ok, JSON.stringify(activated)).toBe(true)
  await startCombat(request, 'grid')
}

async function openCombatTool(page: Page) {
  await page.goto(`/#/play?game=${encodeURIComponent(GAME)}`)
  await expect(page.getByTestId('timeline')).toBeVisible({ timeout: 30_000 })
  await page.locator('[data-testid="dnd5e-campaign-tool"]:visible').click()
  await page.locator('.dnd-toolbox-dialog').getByRole('button', { name: '战斗工具' }).click()
  await expect(page.locator('.dnd-combat')).toBeVisible()
}

async function waitForPlayerTurn(request: APIRequestContext) {
  await expect.poll(async () => {
    const view = await gameplay(request)
    return view?.gameplay?.combat?.current_actor_id || ''
  }, { timeout: 30_000 }).toBe(PLAYER)
}

test('grid combat renders the board and a token drag submits a target_cell move', async ({ page, request }) => {
  test.setTimeout(120_000)
  await startGridCombat(request)
  await waitForPlayerTurn(request)
  await openCombatTool(page)

  const panel = page.locator('.dnd-combat')
  await expect(panel.getByTestId('grid-board')).toBeVisible()
  await expect(panel.locator('.stage-scroll')).toHaveCount(0)
  await expect(panel.locator('[data-token="player:e2e-gm"]')).toBeVisible()
  // Reachable cells are highlighted within the movement budget.
  expect(await panel.locator('.grid-cell-reachable').count()).toBeGreaterThan(0)

  // Drag the acting token onto a reachable cell; this only stages a move intent.
  const token = panel.locator('[data-token="player:e2e-gm"]')
  const cell = panel.locator('[data-cell="3,1"]')
  await token.scrollIntoViewIfNeeded()
  await cell.scrollIntoViewIfNeeded()
  const from = await token.boundingBox()
  const to = await cell.boundingBox()
  expect(from && to).toBeTruthy()
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2)
  await page.mouse.down()
  await page.mouse.move(to!.x + to!.width / 2, to!.y + to!.height / 2, { steps: 8 })
  await page.mouse.up()

  // The move dock opens on the cell the drag landed on (no theater distance input).
  const selected = panel.locator('.grid-cell-reachable.selected')
  await expect(selected).toHaveCount(1)
  const chosenKey = await selected.getAttribute('data-cell')
  expect(chosenKey).toBeTruthy()
  const [cx, cy] = chosenKey!.split(',').map(Number)
  expect(`${cx},${cy}`).not.toBe('1,1')
  await expect(panel.locator('.mode-panel[data-mode="move"]')).toBeVisible()
  await expect(panel.locator('.mode-panel[data-mode="move"] input[type="number"]')).toHaveCount(0)

  // Confirm and submit; the server recomputes legality and lands the token.
  await panel.locator('.dock-confirm').click()
  await panel.locator('.confirm-card .combat-primary').click()

  await expect.poll(async () => {
    const view = await gameplay(request)
    return view?.gameplay?.combat?.spatial_position?.[PLAYER]?.x ?? -1
  }, { timeout: 20_000 }).toBe(cx)
  const after = await gameplay(request)
  expect(after.gameplay.combat.spatial_position[PLAYER]).toMatchObject({ x: cx, y: cy })

  await endActiveCombat(request)
})

test('theater combat keeps the single-axis track and never draws a grid board', async ({ page, request }) => {
  test.setTimeout(90_000)
  await startCombat(request)
  await openCombatTool(page)
  const panel = page.locator('.dnd-combat')
  await expect(panel.locator('.turn-banner')).toBeVisible({ timeout: 30_000 })
  await expect(panel.getByTestId('grid-board')).toHaveCount(0)
  await expect(panel.locator('.stage-scroll')).toHaveCount(1)
  await endActiveCombat(request)
})