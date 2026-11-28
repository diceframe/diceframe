import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from './fixtures'

// Real synthetic compendium fixtures (tests/fixtures/foundry/compendium).
// Playwright runs with cwd = frontend-v2 (see scripts/run_e2e.py).
const journalPack = readFileSync(resolve(process.cwd(), '../tests/fixtures/foundry/compendium/journal_pack.json'), 'utf8')
const itemsPack = readFileSync(resolve(process.cwd(), '../tests/fixtures/foundry/compendium/items_pack.jsonl'), 'utf8')

async function openDialog(page: import('@playwright/test').Page) {
  await page.goto('/#/modules')
  await expect(page.locator('.modules-my-btn')).toBeVisible()
  await page.locator('.modules-my-btn').click()
  await page.getByTestId('modules-compendium-open').click()
  await expect(page.getByTestId('compendium-import-file')).toBeAttached()
}

async function selectAndPreview(page: import('@playwright/test').Page, name: string, content: string, mimeType: string) {
  await page.getByTestId('compendium-import-file').setInputFiles({ name, mimeType, buffer: Buffer.from(content) })
  await expect(page.getByTestId('compendium-import-preview')).toBeEnabled()
  await page.getByTestId('compendium-import-preview').click()
  await expect(page.getByTestId('compendium-import-plan')).toBeVisible()
}

test('previews a compendium pack with the conversion report and commits new Books', async ({ page }, info) => {
  test.setTimeout(90_000)
  await openDialog(page)
  await selectAndPreview(page, 'journal_pack.json', journalPack, 'application/json')

  // One Journal Book is planned; the RollTable and Macro stay in the report only.
  await expect(page.getByTestId('compendium-plan-item')).toHaveCount(1)
  await expect(page.getByTestId('compendium-plan-item')).toContainText('journal.JrnlGreyMoor001')
  await expect(page.getByTestId('compendium-report')).toContainText('Moor Loot')
  await expect(page.getByTestId('compendium-report')).toContainText('Alarm macro')
  // A brand-new Book needs no decision and can be committed directly.
  await expect(page.getByTestId('compendium-import-commit')).toBeEnabled()
  await page.screenshot({ path: info.outputPath(`compendium-preview-${info.project.name}.png`), fullPage: true })

  await page.getByTestId('compendium-import-commit').click()
  await expect(page.getByTestId('compendium-import-receipt')).toBeVisible()
  await expect(page.getByTestId('compendium-receipt-ok')).toBeVisible()
  await expect(page.getByTestId('compendium-receipt-item')).toContainText('已创建')
  await page.screenshot({ path: info.outputPath(`compendium-receipt-${info.project.name}.png`), fullPage: true })
})

test('re-importing the same pack asks for an explicit decision', async ({ page }, info) => {
  test.setTimeout(90_000)
  await openDialog(page)
  await selectAndPreview(page, 'items_pack.jsonl', itemsPack, 'application/x-ndjson')
  await expect(page.getByTestId('compendium-import-commit')).toBeEnabled()
  await page.getByTestId('compendium-import-commit').click()
  await expect(page.getByTestId('compendium-import-receipt')).toBeVisible()

  // Push the identical source again: the Book now matches and is unchanged.
  await page.getByTestId('compendium-import-startover').click()
  await selectAndPreview(page, 'items_pack.jsonl', itemsPack, 'application/x-ndjson')
  await expect(page.getByTestId('compendium-plan-item')).toContainText('无变化')
  await expect(page.getByTestId('compendium-decision-required')).toBeVisible()
  await expect(page.getByTestId('compendium-import-commit')).toBeDisabled()

  await page.getByTestId('compendium-decision').selectOption('update')
  await expect(page.getByTestId('compendium-import-commit')).toBeEnabled()
  await page.getByTestId('compendium-import-commit').click()
  await expect(page.getByTestId('compendium-receipt-item')).toContainText('无变化')
  await page.screenshot({ path: info.outputPath(`compendium-repush-${info.project.name}.png`), fullPage: true })
})