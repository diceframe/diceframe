import { expect, test } from './fixtures'
import { accessToken } from './support'

function gameKeyFor(project: string): string {
  // Same isolation as foundry-import.spec.ts; workers=1 so specs run serially.
  return project === 'mobile' ? 'web|e2e-lore-golden|web_bot' : 'web|e2e-adventure|web_bot'
}

// A minimal portable scene document; normalize_tactical_scene_payload fills the
// rest. Same shape the tactical_scene_create route accepts.
function sceneDefinition(id: string, name: string) {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: id,
    display_name: name,
    dimensions: { width: 200, height: 200, padding: 0, offset_x: 0, offset_y: 0, initial_view: { x: 100, y: 100, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
    placeables: { tokens: {}, walls: {}, lights: {} },
  }
}

async function openDialog(page: import('@playwright/test').Page, gameKey: string) {
  await page.goto(`/#/tactical-scene?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
  await page.getByTestId('tactical-portable-import-open').click()
  await expect(page.getByTestId('portable-import-file')).toBeAttached()
}

async function selectAndPreview(page: import('@playwright/test').Page, definition: unknown) {
  await page.getByTestId('portable-import-file').setInputFiles({
    name: 'scene.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(definition)),
  })
  await expect(page.getByTestId('portable-import-preview')).toBeEnabled()
  await page.getByTestId('portable-import-preview').click()
}

async function createLocalScene(request: import('@playwright/test').APIRequestContext, gameKey: string, scene: Record<string, unknown>) {
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const list = await (await request.get(`${base}/tactical-scenes`, { headers })).json()
  const result = await (await request.post(`${base}/tactical-scenes`, {
    headers,
    data: { expected_revision: list.revision, scene },
  })).json()
  expect(result.ok, JSON.stringify(result)).toBe(true)
  return result
}

test('imports a portable scene definition and shows it in the list', async ({ page }, info) => {
  test.setTimeout(90_000)
  const gameKey = gameKeyFor(info.project.name)
  const id = `e2e-portable-${info.project.name}`
  const name = `Portable Plaza ${info.project.name}`

  await openDialog(page, gameKey)
  await selectAndPreview(page, sceneDefinition(id, name))
  await expect(page.getByTestId('portable-import-preview-plan')).toBeVisible()
  await expect(page.getByTestId('portable-preview-action')).toContainText('将在本局新建场景')
  await expect(page.getByTestId('portable-import-commit')).toBeEnabled()
  await page.screenshot({ path: info.outputPath(`portable-preview-${info.project.name}.png`), fullPage: true })

  await page.getByTestId('portable-import-commit').click()
  await expect(page.getByTestId('portable-import-receipt')).toBeVisible()
  await expect(page.getByTestId('portable-receipt-created')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByTestId('portable-import-receipt')).toHaveCount(0)
  const item = page.getByTestId('tactical-scene-item').filter({ hasText: name })
  await expect(item).toBeVisible()
  await page.screenshot({ path: info.outputPath(`portable-imported-${info.project.name}.png`), fullPage: true })
})

test('a same-named local scene needs explicit confirmation', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const gameKey = gameKeyFor(info.project.name)
  const name = `Portable Collision ${info.project.name}`
  await createLocalScene(request, gameKey, sceneDefinition(`e2e-portable-local-${info.project.name}`, name))

  await openDialog(page, gameKey)
  await selectAndPreview(page, sceneDefinition(`e2e-portable-twin-${info.project.name}`, name))
  await expect(page.getByTestId('portable-name-collision')).toBeVisible()
  await expect(page.getByTestId('portable-import-commit')).toBeDisabled()

  await page.getByTestId('portable-confirm-name').check()
  await expect(page.getByTestId('portable-import-commit')).toBeEnabled()
  await page.getByTestId('portable-import-commit').click()
  await expect(page.getByTestId('portable-receipt-created')).toBeVisible()
  await page.screenshot({ path: info.outputPath(`portable-name-confirm-${info.project.name}.png`), fullPage: true })
})

test('a different local scene with the same identity is refused, never overwritten', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const gameKey = gameKeyFor(info.project.name)
  const id = `e2e-portable-conflict-${info.project.name}`
  await createLocalScene(request, gameKey, sceneDefinition(id, 'Original'))

  await openDialog(page, gameKey)
  // Same identity, different content: the preview itself refuses the import.
  await selectAndPreview(page, sceneDefinition(id, 'Changed'))
  await expect(page.getByTestId('portable-import-error')).toBeVisible()
  await expect(page.getByTestId('portable-import-error')).toContainText('绝不覆盖')
  await expect(page.getByTestId('portable-import-preview-plan')).toHaveCount(0)
  await page.screenshot({ path: info.outputPath(`portable-conflict-${info.project.name}.png`), fullPage: true })
})