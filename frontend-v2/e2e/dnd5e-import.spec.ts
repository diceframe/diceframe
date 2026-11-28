import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from './fixtures'
import { accessToken } from './support'

// The real synthetic dnd5e 6.1.x fixture (tests/fixtures/dnd5e). Playwright runs
// with cwd = frontend-v2 (see scripts/run_e2e.py).
const actorJson = readFileSync(
  resolve(process.cwd(), '../tests/fixtures/dnd5e/actor_srd_dwarf_fighter.json'),
  'utf8',
)
const DND_GAME = 'web|e2e-dnd2024|web_bot'
const ADVENTURE_GAME = 'web|e2e-adventure|web_bot'

async function openImporter(page: import('@playwright/test').Page, gameKey: string) {
  await page.goto(`/#/dnd5e-import?game=${encodeURIComponent(gameKey)}`)
  await expect(page.getByTestId('dnd5e-import-page')).toBeVisible()
}

async function uploadAndPreview(
  page: import('@playwright/test').Page,
  buffer: Buffer = Buffer.from(actorJson),
  name = 'actor_srd_dwarf_fighter.json',
) {
  await page.getByTestId('dnd5e-import-file').setInputFiles({ name, mimeType: 'application/json', buffer })
  await expect(page.getByTestId('dnd5e-import-preview')).toBeEnabled()
  await page.getByTestId('dnd5e-import-preview').click()
  await expect(page.getByTestId('dnd5e-import-display')).toBeVisible()
}

// The dwarf fighter fixture maps class/species/background and pre-fills its two
// class skills; what remains is the background bonus split, both equipment
// packages and the third Standard language.
async function completeDwarfFighterChoices(page: import('@playwright/test').Page) {
  await page.getByTestId('dnd5e-bonus-2-1').click()
  await page.getByTestId('dnd5e-select-equipment_package_ref').selectOption({ index: 1 })
  await page.getByTestId('dnd5e-select-background_equipment_package_ref').selectOption({ index: 1 })
  const boxes = page.locator('input[data-testid^="dnd5e-check-language_refs-"]')
  const count = await boxes.count()
  for (let index = 0; index < count; index += 1) {
    const box = boxes.nth(index)
    if (!(await box.isChecked()) && await box.isEnabled()) {
      await box.check()
      break
    }
  }
  await expect(page.getByTestId('dnd5e-import-stale')).toBeVisible()
  await page.getByTestId('dnd5e-import-repreview').click()
}

test('previews a dnd5e actor with the display DTO and the conversion report', async ({ page }, info) => {
  test.setTimeout(90_000)
  await openImporter(page, DND_GAME)
  await uploadAndPreview(page)

  await expect(page.getByTestId('dnd5e-display-name')).toContainText('Dain Ironfoot')
  await expect(page.getByTestId('dnd5e-item-mapped').filter({ hasText: 'item:longsword' })).toBeVisible()
  await expect(
    page.getByTestId('dnd5e-display-item').filter({ hasText: 'Blade of Testing' }).getByTestId('dnd5e-item-unmapped'),
  ).toContainText('可保存但不能执行')
  await expect(page.getByTestId('dnd5e-unsupported')).toContainText('blade-of-testing')
  await expect(page.getByTestId('dnd5e-missing-list')).toContainText('background_ability_bonuses')
  await expect(page.getByTestId('dnd5e-selection-form')).toBeVisible()
  await expect(page.getByTestId('dnd5e-import-commit')).toBeDisabled()
  await page.screenshot({ path: info.outputPath(`dnd5e-import-preview-${info.project.name}.png`), fullPage: true })
})

test('completes missing choices, previews again and creates the character', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}` }
  const games = await (await request.get('/api/games', { headers })).json()
  const game = games.games.find((entry: { game_key: string }) => entry.game_key === DND_GAME)
  expect(game, JSON.stringify(games)).toBeTruthy()

  await openImporter(page, DND_GAME)
  await uploadAndPreview(page)
  await completeDwarfFighterChoices(page)
  await expect(page.getByTestId('dnd5e-import-commit')).toBeEnabled()
  await page.getByTestId('dnd5e-import-commit').click()

  // The seeded table caps seats at 2 and the desktop/mobile projects share it;
  // whichever project runs second must see the localized room-full refusal
  // instead of a receipt. Both outcomes are the server's authoritative answer.
  const roomFull = Number(game.player_count) >= Number(game.max_players)
  if (roomFull) {
    await expect(page.getByTestId('dnd5e-import-error')).toContainText('房间席位已满')
    await expect(page.getByTestId('dnd5e-import-receipt')).toHaveCount(0)
  } else {
    await expect(page.getByTestId('dnd5e-import-receipt')).toBeVisible()
    await expect(page.getByTestId('dnd5e-receipt-name')).toContainText('Dain Ironfoot')
    await expect(page.getByTestId('dnd5e-reconciliation')).toBeVisible()
    const after = await (await request.get('/api/games', { headers })).json()
    const updated = after.games.find((entry: { game_key: string }) => entry.game_key === DND_GAME)
    expect(Number(updated.player_count)).toBe(Number(game.player_count) + 1)
  }
  await page.screenshot({ path: info.outputPath(`dnd5e-import-commit-${info.project.name}.png`), fullPage: true })
})

test('blocks a level-3 actor fail-closed until progression exists', async ({ page }, info) => {
  test.setTimeout(90_000)
  const actor = JSON.parse(actorJson) as { items: Array<{ type: string; system: { levels?: number } }> }
  for (const item of actor.items) {
    if (item.type === 'class') item.system.levels = 3
  }
  await openImporter(page, DND_GAME)
  await uploadAndPreview(page, Buffer.from(JSON.stringify(actor)), 'actor_level3.json')

  await expect(page.getByTestId('dnd5e-level-blocked')).toBeVisible()
  await expect(page.getByTestId('dnd5e-missing-list')).toContainText('progression.history')
  await expect(page.getByTestId('dnd5e-import-commit')).toBeDisabled()
  await page.screenshot({ path: info.outputPath(`dnd5e-import-level3-${info.project.name}.png`), fullPage: true })
})

test('refuses a non-dnd2024 game with a localized error', async ({ page }) => {
  test.setTimeout(90_000)
  await openImporter(page, ADVENTURE_GAME)
  await page.getByTestId('dnd5e-import-file').setInputFiles({
    name: 'actor_srd_dwarf_fighter.json',
    mimeType: 'application/json',
    buffer: Buffer.from(actorJson),
  })
  await page.getByTestId('dnd5e-import-preview').click()
  await expect(page.getByTestId('dnd5e-import-error')).toContainText('只能导入到 D&D 2024 规则的对局')
  await expect(page.getByTestId('dnd5e-import-display')).toHaveCount(0)
})