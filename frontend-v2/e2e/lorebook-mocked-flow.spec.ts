import { expect, test } from './fixtures'

/**
 * frontend mocked flow —— **不是** Golden。
 *
 * 这条用例把 `**\/api/**` 全部 mock 掉，因此它只能证明「UI 对自己捏的响应是忠诚的」：
 * 它不经过 backend、SQLite、migration、resolver 或 matcher，任何后端回归都不会让它变红。
 * 真实链路验收在 lorebook-golden.spec.ts（并且已进 test:e2e:smoke，会在 CI 里跑）。
 */

test('Lorebook mocked frontend flow: import, select, activate safely, and export', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Lorebook golden uses the desktop inspector layout')

  const requests: Array<{ method: string; path: string; body?: unknown }> = []
  const primaryEntries = [{
    id: 'primary-entry',
    name: 'Primary clue',
    type: 'location',
    tier: 'core',
    keywords: ['castle'],
    content: 'The castle has a sealed gate.',
    visible_to: ['*'],
  }]
  const importedEntries = [{
    id: 'imported-entry',
    name: 'Imported clue',
    type: 'event',
    tier: 'background',
    keywords: ['sigil'],
    content: 'The sigil opens the hidden archive.',
    visible_to: ['*'],
  }]

  await page.addInitScript(() => {
    localStorage.setItem('currentGame', 'web|lorebook-golden|gm')
    localStorage.setItem('lore_inspector_open', '1')
  })

  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    let body: unknown
    try { body = request.postDataJSON() } catch { body = undefined }
    requests.push({ method: request.method(), path, body })

    if (request.method() === 'GET' && path === '/announcements') {
      await route.fulfill({ json: { hash: 'lorebook-golden' } }); return
    }
    if (request.method() === 'GET' && path === '/worlds') {
      await route.fulfill({ json: { worlds: [{ id: 'w-golden', name: 'Golden World', language: 'zh-CN', entry_count: 1 }] } }); return
    }
    if (request.method() === 'GET' && path === '/games') {
      await route.fulfill({ json: { games: [{ game_key: 'web|lorebook-golden|gm', world_id: 'w-golden', language: 'zh-CN' }] } }); return
    }
    if (request.method() === 'GET' && path.startsWith('/games/') && path.endsWith('/characters')) {
      await route.fulfill({ json: { players: [{ user_id: 'alice', character_name: 'Alice' }] } }); return
    }
    if (request.method() === 'GET' && path === '/lorebooks') {
      await route.fulfill({ json: { books: [
        { id: 'world:w-golden', name: 'Primary World', primary: true, scope: 'world', enabled: true },
        { id: 'book:imported', name: 'Imported Book', scope: 'global', enabled: true },
      ] } }); return
    }
    if (request.method() === 'GET' && path === '/lorebook/w-golden') {
      await route.fulfill({ json: { entries: primaryEntries } }); return
    }
    if (request.method() === 'GET' && path === '/lorebooks/book%3Aimported/entries') {
      await route.fulfill({ json: { entries: importedEntries } }); return
    }
    if (request.method() === 'GET' && path === '/lorebook/w-golden/preview') {
      await route.fulfill({ json: {
        ok: true,
        world_id: 'w-golden',
        summary: { total: 2, visible: 1, public: 1, character_only: 0, gm_secret: 1 },
        projections: { 'primary-entry': { entry_id: 'primary-entry', visible: true, visibility: 'public' } },
      } }); return
    }
    if (request.method() === 'POST' && path === '/lorebooks/import/preview') {
      expect(body).toMatchObject({ spec: 'lorebook_v3' })
      await route.fulfill({ json: { format: 'lorebook_v3', counts: { entries: 1, mapped: 1, warnings: 0, unsupported: 0 }, warnings: [] } }); return
    }
    if (request.method() === 'POST' && path === '/lorebooks/import') {
      // #398 起 import 是 binding-aware 的：默认「新建独立 Book」+ 用户显式选择的绑定。
      expect(body).toMatchObject({ binding: { scope_kind: 'world', scope_id: 'w-golden' } })
      await route.fulfill({ json: { ok: true, book_id: 'world:w-golden', imported: 1 } }); return
    }
    if (request.method() === 'POST' && path === '/lorebooks/activation-preview') {
      expect(body).toMatchObject({ game_key: 'web|lorebook-golden|gm', action_text: 'open the sigil archive' })
      const viewer = (body as { viewer?: { is_gm?: boolean } } | undefined)?.viewer
      if (viewer?.is_gm) {
        await route.fulfill({ json: {
          ok: true,
          entries: [{ id: 'primary-entry' }, { id: 'secondary-entry' }],
          trace: [
            { entry_id: 'primary-entry', final_state: 'included', reason_code: 'keyword', matched_keys: ['sigil'] },
            { entry_id: 'secondary-entry', final_state: 'included', reason_code: 'recursive', secondary_matches: ['archive'] },
            { entry_id: 'gm-secret', final_state: 'hidden', reason_code: 'visibility' },
          ],
        } }); return
      }
      await route.fulfill({ json: {
        ok: true,
        entries: [{ id: 'primary-entry' }],
        trace: [{ entry_id: 'primary-entry', final_state: 'included', reason_code: 'keyword', matched_keys: ['sigil'] }],
      } }); return
    }
    if (request.method() === 'GET' && path.endsWith('/export')) {
      await route.fulfill({ json: { spec: 'lorebook_v3', data: { lorebook: { name: 'Primary World', entries: primaryEntries } }, native_backup: { format: 'diceframe_native_lorebook', entries: primaryEntries } } }); return
    }
    await route.continue()
  })

  await page.goto('/#/lorebook')
  await expect(page.locator('.lorebook-page')).toBeVisible()

  const bookSelect = page.locator('.lore-context-bar select').nth(2)
  const bookMenu = page.locator('.lore-context-bar details.lore-menu').nth(1)
  // 默认当前 Book = 主世界书伪 id（world:<world id>）。
  await expect(bookSelect).toHaveValue('world:w-golden')

  const importPayload = JSON.stringify({ spec: 'lorebook_v3', data: { lorebook: { name: 'Imported', entries: importedEntries } } })
  await page.locator('input[type=file]').setInputFiles({ name: 'golden.json', mimeType: 'application/json', buffer: Buffer.from(importPayload) })
  const importDialog = page.getByRole('dialog')
  await expect(importDialog).toContainText('lorebook_v3')
  await importDialog.getByRole('button', { name: '导入', exact: true }).click()
  await expect.poll(() => requests.filter(item => item.path === '/lorebooks/import').length).toBe(1)

  await bookSelect.selectOption('book:imported')
  await expect(bookSelect).toHaveValue('book:imported')
  await expect(page.locator('.lore-row strong')).toContainText('Imported clue')

  const activationInput = page.locator('.lore-activation-input')
  await activationInput.fill('open the sigil archive')
  await page.getByRole('button', { name: '预览触发', exact: true }).click()
  await expect(page.locator('.lore-activation-trace')).toContainText('gm-secret')
  await expect(page.locator('.lore-activation-trace')).toContainText('递归带入')

  await page.getByRole('button', { name: 'Alice', exact: true }).click()
  await page.getByRole('button', { name: '预览触发', exact: true }).click()
  await expect(page.locator('.lore-activation-trace')).toContainText('primary-entry')
  await expect(page.locator('.lore-activation-trace')).not.toContainText('gm-secret')
  await expect(page.locator('.lore-activation-trace')).not.toContainText('secondary-entry')

  await bookMenu.locator('summary').click()
  await bookMenu.getByRole('button', { name: '导出', exact: true }).click()
  await expect.poll(() => requests.filter(item => item.path.endsWith('/export')).length).toBe(1)
  expect(requests.some(item => item.path.endsWith('/export'))).toBe(true)
})
