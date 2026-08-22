import { expect, test } from './fixtures'
import { accessToken } from './support'

const token = accessToken

test('settings status summary stays structured and destructive confirmations are explicit', async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token())

  await page.goto('/#/settings')
  await expect(page.locator('.system-status-grid')).toBeVisible()
  const statusCards = page.locator('.system-status-card')
  await expect(statusCards.first()).toBeVisible()
  const summaries = await statusCards.evaluateAll(elements => elements.map(element => ({
    label: element.querySelector('.system-status-head > span')?.textContent?.trim() ?? '',
    value: element.querySelector('.system-status-tag')?.textContent?.trim() ?? '',
    detail: element.querySelector('p')?.textContent?.trim() ?? '',
  })))
  expect(summaries.length).toBeGreaterThan(0)
  for (const summary of summaries) {
    expect(summary.label).not.toBe('')
    expect(summary.value).not.toBe('')
    expect(summary.detail).not.toBe('')
  }

  await page.goto('/')
  await page.getByRole('button', { name: '删除' }).first().click()
  await expect(page.getByText('删除存档').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '删除存档' })).toBeVisible()
  await page.getByRole('button', { name: '取消', exact: true }).click()
})
test('rules page exposes structured editing for copied rules', async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem('trpg_access_token', value), token())
  await page.goto('/#/rules')
  await page.getByRole('button', { name: '复制并编辑' }).first().click()
  await expect(page.getByRole('heading', { name: '复制并编辑规则' })).toBeVisible()
  await expect(page.getByLabel('规则 ID')).toBeVisible()
  await expect(page.getByLabel('规则名称')).toBeVisible()
  await expect(page.locator('.rule-editor-section').filter({ hasText: '属性' })).toBeVisible()
  await expect(page.getByText('高级 JSON')).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()
})
