import { expect, test } from './fixtures'
import { accessToken } from './support'

const BACKGROUND_URL = '/v2-assets/ui/campaign-mountain-city.jpg'
const TOKEN_URL = '/v2-assets/ui/rules/rule-tavern-free.webp'

function asset(sourcePath: string, mime: string) {
  return { asset_id: `e2e-${sourcePath.split('/').pop()}`, digest: '', source_path: sourcePath, mime }
}

function chapelScene() {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'e2e-board-chapel',
    display_name: 'Chapel of the Shield',
    dimensions: { width: 1200, height: 900, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 600, y: 450, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
    background: asset(BACKGROUND_URL, 'image/jpeg'),
    placeables: {
      tokens: {},
      walls: {
        w1: { schema_version: 1, kind: 'wall', canonical_id: 'w1', endpoints: { x0: 0, y0: 0, x1: 1200, y1: 0 } },
        w2: { schema_version: 1, kind: 'wall', canonical_id: 'w2', endpoints: { x0: 0, y0: 0, x1: 0, y1: 900 }, door: 1 },
        w3: { schema_version: 1, kind: 'wall', canonical_id: 'w3', endpoints: { x0: 1200, y0: 0, x1: 1200, y1: 900 } },
      },
      lights: {
        l1: { schema_version: 1, kind: 'light', canonical_id: 'l1', position: { x: 300, y: 450 }, radius: { dim: 40, bright: 20 }, color: '#ffaa00', intensity: 0.6 },
        l2: { schema_version: 1, kind: 'light', canonical_id: 'l2', position: { x: 900, y: 300 }, radius: { dim: 60, bright: 30 }, color: '#88bbff', intensity: 0.8 },
      },
    },
  }
}

function keepScene() {
  return {
    schema_version: 1,
    kind: 'scene',
    canonical_id: 'e2e-board-keep',
    display_name: 'Ruined Keep',
    dimensions: { width: 1200, height: 900, padding: 0.25, offset_x: 0, offset_y: 0, initial_view: { x: 600, y: 450, zoom: 1 } },
    grid: { type: 'square', size: 100, scale: 1, units: 'ft', color: '#000000', alpha: 0.2, distance: 5 },
    background: asset('/v2-assets/ui/campaign-moonlit-ruins.jpg', 'image/jpeg'),
    placeables: {
      tokens: {
        t1: {
          schema_version: 1, kind: 'token', canonical_id: 't1',
          position: { x: 300, y: 400 }, size: { width: 1, height: 1 }, rotation: 45,
          texture: asset(TOKEN_URL, 'image/webp'), disposition: -1,
        },
      },
      walls: {},
      lights: {},
    },
  }
}

async function createScene(request: import('@playwright/test').APIRequestContext, gameKey: string, scene: Record<string, unknown>, expectedRevision: number) {
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const base = `/api/games/${encodeURIComponent(gameKey)}`
  const created = await (await request.post(`${base}/tactical-scenes`, {
    headers, data: { scene, expected_revision: expectedRevision },
  })).json()
  expect(created.ok, JSON.stringify(created)).toBe(true)
  return created.revision as number
}

test('read-only board renders a seeded scene, switches scenes and zooms', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const games = await (await request.get('/api/games', { headers })).json()
  // Isolate the two projects into different games so seeded scenes never collide.
  const gameKey = (info.project.name === 'mobile' ? games.games[1] : games.games[0]).game_key as string
  const base = `/api/games/${encodeURIComponent(gameKey)}`

  const list = await (await request.get(`${base}/tactical-scenes`, { headers })).json()
  let revision = list.revision as number
  revision = await createScene(request, gameKey, chapelScene(), revision)
  revision = await createScene(request, gameKey, keepScene(), revision)
  const activated = await (await request.post(`${base}/tactical-scenes/e2e-board-chapel/activate`, {
    headers, data: { expected_revision: revision },
  })).json()
  expect(activated.ok, JSON.stringify(activated)).toBe(true)

  // The board holds an SSE connection open, so networkidle never settles;
  // wait for the actual image responses instead.
  const backgroundLoaded = page.waitForResponse(response => response.url().includes('campaign-mountain-city.jpg') && response.status() === 200)
  await page.goto(`/#/tactical-scene?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('tactical-scene-page')).toBeVisible()
  await expect(page.getByTestId('tactical-board')).toBeVisible()
  await backgroundLoaded

  // Chapel: real background image, three walls (one door), two lights.
  await expect(page.getByTestId('board-background-image')).toBeVisible()
  await expect(page.getByTestId('board-background-fallback')).toHaveCount(0)
  await expect(page.getByTestId('board-wall')).toHaveCount(3)
  await expect(page.getByTestId('board-light')).toHaveCount(2)
  expect(await page.getByTestId('board-wall').nth(1).getAttribute('class')).toContain('board-wall-door')
  // Vertical SVG lines have a zero-width bounding box, so assert presence and
  // geometry rather than Playwright visibility.
  const gridLines = page.getByTestId('board-grid').locator('line')
  expect(await gridLines.count()).toBeGreaterThan(0)
  expect(await gridLines.first().getAttribute('x1')).toBe('-300')

  const svg = page.getByTestId('tactical-board-svg')
  const widthOf = async () => Number((await svg.getAttribute('viewBox'))!.split(' ')[2])
  const initialWidth = await widthOf()

  // Button zoom (works with touch on the mobile project).
  await page.getByTestId('board-zoom-in').click()
  expect(await widthOf()).toBeLessThan(initialWidth)
  await page.getByTestId('board-reset-view').click()
  expect(await widthOf()).toBeCloseTo(initialWidth, 3)

  // Wheel zoom is a desktop gesture; mobile emulation does not deliver mouse
  // wheel events (touch uses pinch / the zoom buttons verified above).
  if (info.project.name === 'desktop') {
    const box = await svg.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.wheel(0, -240)
    await expect.poll(widthOf).toBeLessThan(initialWidth)
    await page.getByTestId('board-reset-view').click()
  }

  // Switch to the Ruined Keep: its token texture renders as a real image.
  // Name the select button explicitly: GM sessions also render an edit button.
  const tokenLoaded = page.waitForResponse(response => response.url().includes('rule-tavern-free.webp') && response.status() === 200)
  await page.getByTestId('tactical-scene-item').filter({ hasText: 'Ruined Keep' }).getByRole('button', { name: 'Ruined Keep' }).click()
  await expect(page.getByTestId('tactical-board')).toContainText('Ruined Keep')
  await tokenLoaded
  await expect(page.getByTestId('board-tokens').locator('image')).toBeVisible()
  await expect(page.getByTestId('board-token')).toHaveCount(1)
  await page.screenshot({ path: info.outputPath(`tactical-board-${info.project.name}.png`), fullPage: true })
})