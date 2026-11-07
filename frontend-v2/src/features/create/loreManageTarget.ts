/** Where "edit this world's books" may actually lead.
 *
 * The lorebook page only edits worlds that live in the user's world library
 * (`GET /worlds`). Worlds that ship inside a content pack or a built-in
 * template appear in the create page's world picker (`GET /world-templates`)
 * but are not library rows: a `?world=<pack world>` link lands on the page's
 * "world unavailable" error. A pack world's books therefore cannot be edited
 * in place; the closest real target is the pack page, where the declared
 * resources can be inspected and forked.
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
}): LoreManageTarget {
  const source = String(input.source || '')
  const worldId = source.startsWith('copy:')
    ? source.slice('copy:'.length)
    : source === '__builtin__' ? String(input.worldId || '') : ''
  if (!worldId) return { kind: 'none' }
  if (input.editableWorldIds.includes(worldId)) return { kind: 'world', worldId }
  const moduleId = String(input.moduleId || '').trim()
  if (moduleId) return { kind: 'module', moduleId }
  return { kind: 'none' }
}
