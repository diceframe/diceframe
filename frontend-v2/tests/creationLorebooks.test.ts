import { describe, expect, it } from 'vitest'
import { projectCreationLorebooks, worldBooksHaveContent, type CreationLorebook } from '../src/features/create/creationLorebooks'

const book = (id: string, scope: string, scopeId: string, enabled = true, bindingEnabled = true): CreationLorebook => ({
  id, name: id, enabled, entry_count: 3,
  bindings: [{ scope_kind: scope, scope_id: scopeId, enabled: bindingEnabled, role: id.startsWith('world:') ? 'primary' : 'supplemental' }],
})

describe('creation lorebook summary mirrors existing inheritance', () => {
  const books = [book('world:harbor', 'world', 'harbor'), book('npc', 'world', 'harbor'), book('global', 'global', ''), book('other', 'world', 'forest'), book('unused', 'none', '')]
  it('includes all matching world books and global books, without selecting unrelated or unbound books', () => {
    expect(projectCreationLorebooks(books, 'harbor', '__builtin__').map(item => item.id)).toEqual(['world:harbor', 'npc', 'global'])
  })
  it('shows disabled world books as inactive rather than presenting them as runtime sources', () => {
    const projected = projectCreationLorebooks([book('off-book', 'world', 'harbor', false), book('off-binding', 'world', 'harbor', true, false)], 'harbor', '__builtin__')
    expect(projected.map(item => item.active)).toEqual([false, false])
  })
  it('does not allow a disabled game binding to cancel a matching world binding', () => {
    const mixed = book('mixed', 'world', 'harbor')
    mixed.bindings!.push({ scope_kind: 'game', scope_id: 'some-game', enabled: false })
    expect(projectCreationLorebooks([mixed], 'harbor', '__builtin__')[0].active).toBe(true)
  })
  it('keeps global inheritance for a blank independent world', () => {
    expect(projectCreationLorebooks(books, 'harbor', '__blank__').map(item => item.id)).toEqual(['global'])
  })
  it('offers every bound source book for an independent world', () => {
    expect(projectCreationLorebooks(books, 'new-world', 'copy:harbor').map(item => [item.id, item.scope])).toEqual([['world:harbor', 'reference'], ['npc', 'reference'], ['global', 'global']])
  })
  it('keeps a reference inactive if its source book is disabled', () => {
    expect(projectCreationLorebooks([book('world:harbor', 'world', 'harbor', false)], '', 'copy:harbor')[0].active).toBe(false)
  })
  it('does not infer inheritance from a display scope when binding facts are missing', () => {
    expect(projectCreationLorebooks([{ id: 'unknown', name: 'Unknown', scope: 'world', enabled: true }], 'harbor', '__builtin__')).toEqual([])
  })
})

describe('one book, one identity', () => {
  const emptyPrimary: CreationLorebook = {
    id: 'world:ashfell', name: '灰砧镇：余烬厅', source_kind: 'world', entry_count: 0,
    bindings: [{ scope_kind: 'world', scope_id: 'ashfell', enabled: 1, role: 'primary' }],
  }
  const packBook: CreationLorebook = {
    id: 'module_book_embervault_notes', name: '余烬厅底牌', module: true, catalog: true, entry_count: 18,
    source_kind: 'module', source_id: 'dnd-embervault',
    ref: { source_kind: 'module', source_id: 'dnd-embervault', kind: 'lorebook', id: 'module_book_embervault_notes' },
  }

  it('shows the auto-created empty primary book as context, not as a selectable book', () => {
    const [row] = projectCreationLorebooks([emptyPrimary], 'ashfell', '__builtin__')
    expect(row.placeholder).toBe(true)
    expect(row.selectable).toBe(false)
    // 仍随世界带入：binding 语义不变，只是不再冒充"可勾选的资料册"。
    expect(row.active).toBe(true)
    expect(row.scope).toBe('world')
  })

  it('keeps a primary book that actually has entries selectable', () => {
    const [row] = projectCreationLorebooks([{ ...emptyPrimary, entry_count: 3 }], 'ashfell', '__builtin__')
    expect(row.placeholder).toBe(false)
    expect(row.selectable).toBe(true)
  })

  it('never treats a user-created empty book as a placeholder', () => {
    const [row] = projectCreationLorebooks([{
      id: 'my-empty', name: '我的空册', source_kind: 'native', entry_count: 0,
      bindings: [{ scope_kind: 'world', scope_id: 'ashfell', enabled: 1 }],
    }], 'ashfell', '__builtin__')
    expect(row.placeholder).toBe(false)
    expect(row.selectable).toBe(true)
  })

  it('shows a pack-declared book as pack content without inventing a world binding', () => {
    const rows = projectCreationLorebooks([emptyPrimary, packBook], 'ashfell', '__builtin__')
    const pack = rows.find(row => row.id === packBook.id)!
    expect(pack.scope).toBe('pack')
    expect(pack.primary).toBe(false)
    expect(pack.selectable).toBe(true)
    expect(pack.active).toBe(true)
    // 面板上只有一本书可以勾，另一行是世界的空白主册。
    expect(rows.filter(row => row.selectable)).toHaveLength(1)
  })

  it('knows whether the world itself contributes any content', () => {
    const empty = projectCreationLorebooks([emptyPrimary, packBook], 'ashfell', '__builtin__')
    expect(worldBooksHaveContent(empty)).toBe(false)
    const withContent = projectCreationLorebooks([{ ...emptyPrimary, entry_count: 2 }, packBook], 'ashfell', '__builtin__')
    expect(worldBooksHaveContent(withContent)).toBe(true)
    // 还在加载时不下结论。
    expect(worldBooksHaveContent([])).toBeUndefined()
  })
})
