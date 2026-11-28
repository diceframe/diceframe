import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from './fixtures'
import { accessToken } from './support'

// The real synthetic v14 fixture (tests/fixtures/foundry/v14/scene). Playwright
// runs with cwd = frontend-v2 (see scripts/run_e2e.py).
const sceneJson = readFileSync(
  resolve(process.cwd(), '../tests/fixtures/foundry/v14/scene/scene_placeables.json'),
  'utf8',
)
const sceneName = 'Chapel of the Shield'

function gameKeyFor(project: string): string {
  // Isolate from tactical-scene.spec.ts, which seeds games[0]/games[1].
  return project === 'mobile' ? 'web|e2e-lore-golden|web_bot' : 'web|e2e-adventure|web_bot'
}

async function openWizard(page: import('@playwright/test').Page, gameKey: string) {
  await page.goto(`/#/tactical-scene?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
  await page.getByTestId('tactical-import-open').click()
  await expect(page.getByTestId('foundry-import-file')).toBeAttached()
}

async function selectFixtureAndPreview(page: import('@playwright/test').Page) {
  await page.getByTestId('foundry-import-file').setInputFiles({
    name: 'scene_placeables.json',
    mimeType: 'application/json',
    buffer: Buffer.from(sceneJson),
  })
  await expect(page.getByTestId('foundry-import-preview')).toBeEnabled()
  await page.getByTestId('foundry-import-preview').click()
  await expect(page.getByTestId('foundry-import-plan')).toBeVisible()
}

test('wizard previews a v14 fixture with the conversion report and per-item policies', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const games = await (await request.get('/api/games', { headers })).json()
  const gameKey = gameKeyFor(info.project.name)
  expect(games.games.some((game: { game_key: string }) => game.game_key === gameKey)).toBe(true)

  await openWizard(page, gameKey)
  await selectFixtureAndPreview(page)

  // The placeables fixture converts with display loss (walls/lights are display-only).
  await expect(page.getByTestId('foundry-level-badge')).toContainText('可显示，有损失')
  await expect(page.getByTestId('foundry-conversion-report')).toBeVisible()
  await expect(page.getByTestId('foundry-warnings')).toContainText('display')
  // A brand-new scene defaults to create and only offers valid policies.
  const policy = page.getByTestId('foundry-import-policy')
  await expect(policy).toHaveValue('create')
  await expect(policy.locator('option')).toHaveCount(3)
  await expect(page.getByTestId('foundry-import-commit')).toBeEnabled()
  await page.screenshot({ path: info.outputPath(`foundry-import-preview-${info.project.name}.png`), fullPage: true })
})

test('imports a v14 scene through the wizard, replays idempotently and shows it on the board', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const games = await (await request.get('/api/games', { headers })).json()
  const gameKey = gameKeyFor(info.project.name)
  expect(games.games.some((game: { game_key: string }) => game.game_key === gameKey)).toBe(true)

  await openWizard(page, gameKey)
  await selectFixtureAndPreview(page)
  await expect(page.getByTestId('foundry-import-policy')).toHaveValue('create')

  await page.getByTestId('foundry-import-commit').click()
  await expect(page.getByTestId('foundry-import-receipt')).toBeVisible()
  await expect(page.getByTestId('foundry-receipt-item')).toContainText('已新建')

  // Re-committing the same plan + operation id returns the stored receipt.
  await page.getByTestId('foundry-import-recommit').click()
  await expect(page.getByTestId('foundry-import-idempotent')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByTestId('foundry-import-receipt')).toHaveCount(0)

  // The imported scene appears in the list and renders on the read-only board.
  const item = page.getByTestId('tactical-scene-item').filter({ hasText: sceneName })
  await expect(item).toBeVisible()
  await item.getByRole('button', { name: sceneName }).click()
  await expect(page.getByTestId('tactical-board')).toBeVisible()
  await expect(page.getByTestId('tactical-board')).toContainText(sceneName)
  await page.screenshot({ path: info.outputPath(`foundry-import-${info.project.name}.png`), fullPage: true })
})

test('a stale preview is rejected and re-previewing recovers', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const gameKey = gameKeyFor(info.project.name)
  const base = `/api/games/${encodeURIComponent(gameKey)}`

  await openWizard(page, gameKey)
  await selectFixtureAndPreview(page)

  // Move the scene slot forward after the preview so the plan digest goes stale.
  const list = await (await request.get(`${base}/tactical-scenes`, { headers })).json()
  const bump = await (await request.post(`${base}/tactical-scenes`, {
    headers,
    data: {
      expected_revision: list.revision,
      scene: {
        schema_version: 1,
        kind: 'scene',
        canonical_id: `e2e-import-bump-${info.project.name}`,
        display_name: 'Stale Bump',
        dimensions: { width: 100, height: 100, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 50, y: 50, zoom: 1 } },
        grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
        placeables: { tokens: {}, walls: {}, lights: {} },
      },
    },
  })).json()
  expect(bump.ok, JSON.stringify(bump)).toBe(true)

  await page.getByTestId('foundry-import-commit').click()
  await expect(page.getByTestId('foundry-import-stale')).toBeVisible()
  await expect(page.getByTestId('foundry-import-receipt')).toHaveCount(0)

  await page.getByTestId('foundry-import-repreview').click()
  await expect(page.getByTestId('foundry-import-plan')).toBeVisible()
  await expect(page.getByTestId('foundry-import-stale')).toHaveCount(0)

  await page.getByTestId('foundry-import-commit').click()
  await expect(page.getByTestId('foundry-import-receipt')).toBeVisible()
})