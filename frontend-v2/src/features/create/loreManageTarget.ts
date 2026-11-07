/** Where "edit this world's books" may actually lead.
 *
 * The lorebook page only edits worlds that live in the user's world library
 * (`GET /worlds`). Worlds that ship inside a content pack or a built-in
 * template appear in the create page's world picker (`GET /world-templates`)
 * but are not library rows: a `?world=<pack world>` link lands on the page's
 * "world unavailable" error. A pack world's books therefore cannot be edited
 * in place; the closest real target is the pack page, where the declared
 * resources can be inspected and forked.
 *
 * A library world can still be the wrong target: every world is created with
 * an empty primary book, so a world whose content all lives in a pack ("灰砧镇"
 * ships no starter book, the pack ships an 18-entry book instead) would send
 * the reader to an empty editor. When the world's own books are empty and a
 * pack started this launch, the pack page is the honest answer.
 */
export type LoreManageTarget =
  | { kind: 'world'; worldId: string }
  | { kind: 'module'; moduleId: string }
  | { kind: 'none' }

export function resolveLoreManageTarget(input: {
  /** The create page's lore source: `__builtin__`, `copy:<world>` or `__blank__`. */
  source: string
  /** Preview world for `__builtin__` (the world this game would inherit). */
  worldId: string
  /** Worlds the library can open; package/template worlds are absent by design. */
  editableWorldIds: readonly string[]
  /** The content pack that started this launch, when there is one. */
  moduleId?: string
  /** Whether the world's own books hold any entries; `undefined` = not known yet. */
  worldHasContent?: boolean
}): LoreManageTarget {
  const source = String(input.source || '')
  const worldId = source.startsWith('copy:')
    ? source.slice('copy:'.length)
    : source === '__builtin__' ? String(input.worldId || '') : ''
  if (!worldId) return { kind: 'none' }
  const moduleId = String(input.moduleId || '').trim()
  if (input.editableWorldIds.includes(worldId)) {
    if (input.worldHasContent === false && moduleId) return { kind: 'module', moduleId }
    return { kind: 'world', worldId }
  }
  if (moduleId) return { kind: 'module', moduleId }
  return { kind: 'none' }
}
