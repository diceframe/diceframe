import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoreImportDialog from '../src/features/lorebook/LoreImportDialog.vue'
import { i18n } from '../src/i18n'

// jsdom 的 navigator 是 en-US；这些断言固定在产品默认语言 zh-CN 上。
i18n.global.locale.value = 'zh-CN'

const ST_PREVIEW = {
  format: 'sillytavern',
  counts: { entries: 3, mapped: 3, warnings: 0, unsupported: 0 },
  warnings: [],
}

const CARD_PREVIEW = {
  format: 'character_card_v3',
  counts: { entries: 2, mapped: 2, warnings: 0, unsupported: 0 },
  warnings: [],
  character: { name: 'Alice', book_name: "Alice's lore", entries: 2 },
}

const BASE_PROPS = {
  open: true,
  books: [
    { id: 'world:w-golden', name: 'Primary World', primary: true },
    { id: 'book:other', name: 'Other Book' },
  ],
  characters: [{ uid: 'alice', name: 'Alice' }, { uid: 'bob', name: 'Bob' }],
  worldId: 'w-golden',
  worldName: 'Golden World',
  gameKey: 'web|golden|gm',
  primaryBookId: 'world:world:w-golden',
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(LoreImportDialog, {
    props: { ...BASE_PROPS, primaryBookId: 'world:w-golden', preview: ST_PREVIEW, ...props },
    global: { plugins: [i18n] },
  })
}

async function confirmDecision(wrapper: ReturnType<typeof mountDialog>) {
  const buttons = wrapper.findAll('.lore-import-dialog__buttons button')
  await buttons[buttons.length - 1].trigger('click')
  return wrapper.emitted('confirm')?.[0]?.[0]
}

describe('LoreImportDialog binding selector', () => {
  it('defaults to a new standalone book bound to the current world', async () => {
    const wrapper = mountDialog()
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'world', scope_id: 'w-golden' },
    })
  })

  it('offers every canonical binding target', () => {
    const wrapper = mountDialog()
    const values = wrapper.findAll('.lore-import-dialog__binding input[type=radio]')
      .map(input => (input.element as HTMLInputElement).value)
    expect(values).toEqual(['world', 'game', 'character', 'global', 'none'])
  })

  it('can import as a standalone unbound book', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__binding input[value=none]').setValue(true)
    expect(await confirmDecision(wrapper)).toEqual({ bookId: null, binding: null })
  })

  it('binds to the current game', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__binding input[value=game]').setValue(true)
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'game', scope_id: 'web|golden|gm' },
    })
  })

  it('binds to a chosen character', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__binding input[value=character]').setValue(true)
    await wrapper.get('.lore-import-dialog__character-select').setValue('bob')
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'character', scope_id: 'bob' },
    })
  })

  it('binds globally', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__binding input[value=global]').setValue(true)
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'global', scope_id: '' },
    })
  })

  it('disables game binding when there is no current game', () => {
    const wrapper = mountDialog({ gameKey: '' })
    const game = wrapper.get('.lore-import-dialog__binding input[value=game]')
    expect((game.element as HTMLInputElement).disabled).toBe(true)
  })

  it('requires a character before confirming a character binding', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__binding input[value=character]').setValue(true)
    await wrapper.get('.lore-import-dialog__character-select').setValue('')
    const buttons = wrapper.findAll('.lore-import-dialog__buttons button')
    expect((buttons[buttons.length - 1].element as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('LoreImportDialog target selection', () => {
  it('never silently targets a book: existing target must be chosen', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__target input[value=existing]').setValue(true)
    const buttons = wrapper.findAll('.lore-import-dialog__buttons button')
    expect((buttons[buttons.length - 1].element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.find('.lore-import-dialog__primary-warning').exists()).toBe(false)
  })

  it('warns explicitly before importing into the primary world book', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__target input[value=existing]').setValue(true)
    await wrapper.get('.lore-import-dialog__book').setValue('world:w-golden')
    const warning = wrapper.get('.lore-import-dialog__primary-warning')
    expect(warning.text()).toContain('将导入当前世界主世界书')
    expect(warning.text()).toContain('Golden World')
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: 'world:w-golden',
      binding: { scope_kind: 'world', scope_id: 'w-golden' },
    })
  })

  it('does not warn for a non-primary existing book', async () => {
    const wrapper = mountDialog()
    await wrapper.get('.lore-import-dialog__target input[value=existing]').setValue(true)
    await wrapper.get('.lore-import-dialog__book').setValue('book:other')
    expect(wrapper.find('.lore-import-dialog__primary-warning').exists()).toBe(false)
  })
})

describe('LoreImportDialog character_book flow', () => {
  it('announces the embedded book and defaults to the matching character', async () => {
    const wrapper = mountDialog({ preview: CARD_PREVIEW })
    expect(wrapper.get('.lore-import-dialog__character-summary').text()).toContain('角色 Alice')
    expect(wrapper.get('.lore-import-dialog__character-summary').text()).toContain('世界书 2 条')
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'character', scope_id: 'alice' },
    })
  })

  it('falls back to the current world and marks imported lore without a canonical character', async () => {
    const wrapper = mountDialog({
      preview: { ...CARD_PREVIEW, character: { name: 'Unknown NPC', entries: 2 } },
    })
    expect(wrapper.get('.lore-import-dialog__imported-lore').text()).toContain('Imported character lore')
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'world', scope_id: 'w-golden' },
    })
  })

  it('has no fake opt-out: the embedded book is always part of this flow', async () => {
    const wrapper = mountDialog({ preview: CARD_PREVIEW })
    expect(wrapper.find('.lore-import-dialog__character input[type=checkbox]').exists()).toBe(false)
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'character', scope_id: 'alice' },
    })
  })

  it('stays usable for a character card without an embedded book', async () => {
    const wrapper = mountDialog({
      preview: { ...CARD_PREVIEW, character: { name: 'Alice', entries: 0 } },
    })
    expect(wrapper.find('.lore-import-dialog__character').exists()).toBe(false)
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'character', scope_id: 'alice' },
    })
  })

  it('works with a character card that carries no book identity at all', async () => {
    const wrapper = mountDialog({ preview: { ...CARD_PREVIEW, character: null } })
    expect(wrapper.find('.lore-import-dialog__character').exists()).toBe(false)
    expect(await confirmDecision(wrapper)).toEqual({
      bookId: null,
      binding: { scope_kind: 'world', scope_id: 'w-golden' },
    })
  })

  it('shows no character section for a plain lorebook import', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('.lore-import-dialog__character').exists()).toBe(false)
  })
})
