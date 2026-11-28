import { expect, test } from './fixtures'
import { accessToken } from './support'

// GM scene editor (frontend-03). Isolated from tactical-scene.spec (games[0]/[1])
// and foundry-import.spec (e2e-adventure / e2e-lore-golden) by using fixed keys.
function gameKeyFor(project: string): string {
  return project === 'mobile' ? 'web|e2e-room|web_bot' : 'web|e2e-dnd2024|web_bot'
}

function uniqueId(prefix: string, project: string): string {
  return `${prefix}-${project}-${Date.now().toString(36)}`
}

async function openEditor(page: import('@playwright/test').Page, gameKey: string) {
  await page.goto(`/#/tactical-scene?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
}

test('GM creates a blank scene, places a token, draws a wall, saves and reloads consistently', async ({ page, request }, info) => {
  test.setTimeout(120_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const gameKey = gameKeyFor(info.project.name)
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const games = await (await request.get('/api/games', { headers })).json()
  expect(games.games.some((game: { game_key: string }) => game.game_key === gameKey)).toBe(true)

  await openEditor(page, gameKey)
  const sceneId = uniqueId('e2e-editor', info.project.name)
  await page.getByTestId('tactical-scene-create-open').click()
  await expect(page.getByTestId('create-scene-dialog')).toBeVisible()
  await page.getByTestId('create-scene-name').fill('Editor Keep')
  await page.getByTestId('create-scene-id').fill(sceneId)
  await page.getByTestId('create-scene-submit').click()

  // The editor opens on the freshly created scene.
  await expect(page.getByTestId('scene-editor')).toBeVisible()
  await expect(page.getByTestId('editor-gm-hint')).toBeVisible()
  await expect(page.getByTestId('editor-status')).toHaveText('无更改')

  // Place a token at the board center. Scroll the canvas into view first so
  // raw mouse coordinates land inside the viewport on the mobile project.
  const svg = page.getByTestId('editor-board-svg')
  await expect(svg).toBeVisible()
  await svg.scrollIntoViewIfNeeded()
  const box = await svg.boundingBox()
  expect(box).not.toBeNull()
  const cx = box!.x + box!.width / 2
  const cy = box!.y + box!.height / 2
  await page.getByTestId('editor-tool-token').click()
  await page.mouse.click(cx, cy)
  await expect(page.getByTestId('editor-token')).toHaveCount(1)
  await expect(page.getByTestId('editor-status')).toHaveText('有未保存更改')

  // Draw a wall by dragging across the board.
  await page.getByTestId('editor-tool-wall').click()
  await page.mouse.move(cx - 120, cy - 80)
  await page.mouse.down()
  await page.mouse.move(cx + 120, cy - 80, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('editor-wall')).toHaveCount(1)

  // Save through the write API; the status flips to saved.
  await page.getByTestId('editor-save').click()
  await expect(page.getByTestId('editor-status')).toHaveText('已保存')
  await expect(page.getByTestId('editor-conflict')).toHaveCount(0)

  // The committed scene is readable through the API with the token and wall.
  const read = await (await request.get(`${base}/tactical-scenes/${sceneId}`, { headers })).json()
  expect(read.ok, JSON.stringify(read)).toBe(true)
  expect(Object.keys(read.scene.placeables.tokens)).toHaveLength(1)
  expect(Object.keys(read.scene.placeables.walls)).toHaveLength(1)

  // Reload the page: the read-only board renders the persisted placeables.
  await page.reload()
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
  const item = page.getByTestId('tactical-scene-item').filter({ hasText: 'Editor Keep' })
  await expect(item).toBeVisible()
  await item.getByRole('button', { name: 'Editor Keep' }).click()
  await expect(page.getByTestId('tactical-board')).toBeVisible()
  await expect(page.getByTestId('board-token')).toHaveCount(1)
  await expect(page.getByTestId('board-wall')).toHaveCount(1)
  await page.screenshot({ path: info.outputPath(`tactical-editor-${info.project.name}.png`), fullPage: true })
})

test('a concurrent write produces a conflict banner; reload recovers and overwrite keeps the draft', async ({ page, request }, info) => {
  test.setTimeout(120_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const gameKey = gameKeyFor(info.project.name)
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const sceneId = uniqueId('e2e-conflict', info.project.name)

  // Seed a scene directly, then open its editor.
  const list = await (await request.get(`${base}/tactical-scenes`, { headers })).json()
  const created = await (await request.post(`${base}/tactical-scenes`, {
    headers,
    data: {
      expected_revision: list.revision,
      scene: {
        schema_version: 1,
        kind: 'scene',
        canonical_id: sceneId,
        display_name: 'Conflict Hall',
        dimensions: { width: 1200, height: 900, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 600, y: 450, zoom: 1 } },
        grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
        placeables: { tokens: {}, walls: {}, lights: {} },
      },
    },
  })).json()
  expect(created.ok, JSON.stringify(created)).toBe(true)

  await openEditor(page, gameKey)
  const item = page.getByTestId('tactical-scene-item').filter({ hasText: 'Conflict Hall' })
  await expect(item).toBeVisible()
  await item.getByTestId('tactical-scene-edit').click()
  await expect(page.getByTestId('scene-editor')).toBeVisible()

  // Make a local draft change.
  await page.getByTestId('editor-scene-name').fill('My Draft Name')
  await page.getByTestId('editor-scene-name').blur()
  await expect(page.getByTestId('editor-status')).toHaveText('有未保存更改')

  // A concurrent write moves the slot revision forward.
  const bump = await (await request.post(`${base}/tactical-scenes`, {
    headers,
    data: {
      expected_revision: created.revision,
      scene: {
        schema_version: 1,
        kind: 'scene',
        canonical_id: uniqueId('e2e-bump', info.project.name),
        display_name: 'Bump',
        dimensions: { width: 100, height: 100, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 50, y: 50, zoom: 1 } },
        grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
        placeables: { tokens: {}, walls: {}, lights: {} },
      },
    },
  })).json()
  expect(bump.ok, JSON.stringify(bump)).toBe(true)

  // Saving the stale draft surfaces the conflict banner instead of overwriting.
  await page.getByTestId('editor-save').click()
  await expect(page.getByTestId('editor-conflict')).toBeVisible()
  await expect(page.getByTestId('editor-conflict')).toContainText('场景已在别处更新')

  // Explicit overwrite keeps the draft and commits it against the fresh revision.
  await page.getByTestId('editor-conflict-overwrite').click()
  await expect(page.getByTestId('editor-conflict')).toHaveCount(0)
  await expect(page.getByTestId('editor-status')).toHaveText('已保存')
  const read = await (await request.get(`${base}/tactical-scenes/${sceneId}`, { headers })).json()
  expect(read.scene.display_name).toBe('My Draft Name')

  // A second conflict resolved by reload discards the draft.
  await page.getByTestId('editor-scene-name').fill('Discarded Draft')
  await page.getByTestId('editor-scene-name').blur()
  const bump2 = await (await request.post(`${base}/tactical-scenes`, {
    headers,
    data: {
      expected_revision: read.revision,
      scene: {
        schema_version: 1,
        kind: 'scene',
        canonical_id: uniqueId('e2e-bump2', info.project.name),
        display_name: 'Bump 2',
        dimensions: { width: 100, height: 100, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 50, y: 50, zoom: 1 } },
        grid: { type: 'square', size: 100, scale: 1, units: '', color: '', alpha: 0.2, distance: 5 },
        placeables: { tokens: {}, walls: {}, lights: {} },
      },
    },
  })).json()
  expect(bump2.ok, JSON.stringify(bump2)).toBe(true)
  await page.getByTestId('editor-save').click()
  await expect(page.getByTestId('editor-conflict')).toBeVisible()
  await page.getByTestId('editor-conflict-reload').click()
  await expect(page.getByTestId('editor-conflict')).toHaveCount(0)
  await expect(page.getByTestId('editor-status')).toHaveText('无更改')
  await expect(page.getByTestId('editor-scene-name')).toHaveValue('My Draft Name')
})