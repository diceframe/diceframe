import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../src/api/client'
import {
  CLIENT_REF_PATTERN,
  commitContentImport,
  compendiumClientRef,
  compendiumSyncItem,
  contentInstallId,
  previewContentImport,
} from '../src/api/contentSync'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('contentSync helpers', () => {
  it('builds a foundry-compendium item on the lorebook slot', () => {
    expect(compendiumSyncItem('pack-1', '[]')).toEqual({
      client_ref: 'pack-1', kind: 'lorebook', format: 'foundry-compendium', document: '[]',
    })
  })

  it('derives a stable canonical client_ref from the file name', () => {
    expect(compendiumClientRef('journal_pack.json')).toBe('journal_pack')
    expect(compendiumClientRef('items_pack.jsonl')).toBe('items_pack')
    expect(compendiumClientRef('actors_scenes_pack.yaml')).toBe('actors_scenes_pack')
    expect(compendiumClientRef('My Pack (v2).json')).toBe('My-Pack-v2')
    expect(compendiumClientRef('灰moor.json')).toBe('moor')
    expect(compendiumClientRef('合集.json')).toBe('pack')
    expect(compendiumClientRef('_leading.json')).toBe('leading')
    for (const name of ['a.json', 'My Pack (v2).json', '_leading.json', '灰moor.json', '合集.json']) {
      expect(CLIENT_REF_PATTERN.test(compendiumClientRef(name))).toBe(true)
    }
  })

  it('keeps one stable install id per browser', () => {
    const first = contentInstallId()
    expect(first).toMatch(/^web-[0-9a-f-]{36}$/)
    expect(CLIENT_REF_PATTERN.test(first)).toBe(true)
    expect(contentInstallId()).toBe(first)
    expect(localStorage.getItem('df_content_install_id')).toBe(first)
  })
})

describe('contentSync requests', () => {
  it('posts source and items to the preview route', async () => {
    const apiMock = vi.spyOn(client, 'api').mockResolvedValue({ ok: true, plan_digest: 'sha256:x', items: [] })
    await previewContentImport({ kind: 'device', id: 'install-a' }, [compendiumSyncItem('pack-1', '[]')])
    expect(apiMock).toHaveBeenCalledWith('/content/import/preview', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse((apiMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body).toEqual({ source: { kind: 'device', id: 'install-a' }, items: [{ client_ref: 'pack-1', kind: 'lorebook', format: 'foundry-compendium', document: '[]' }] })
  })

  it('posts the digest and decisions to the commit route', async () => {
    const apiMock = vi.spyOn(client, 'api').mockResolvedValue({ ok: true, items: [] })
    await commitContentImport({ kind: 'device', id: 'install-a' }, [compendiumSyncItem('pack-1', '[]')], 'sha256:x', { 'pack-1.items': 'skip' })
    expect(apiMock).toHaveBeenCalledWith('/content/import/commit', expect.objectContaining({ method: 'POST' }))
    const body = JSON.parse((apiMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.plan_digest).toBe('sha256:x')
    expect(body.decisions).toEqual({ 'pack-1.items': 'skip' })
  })
})