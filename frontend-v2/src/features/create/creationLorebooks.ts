export interface CreationLorebook {
  id: string
  name: string
  enabled?: boolean
  scope?: string
  source_kind?: string
  source_id?: string
  ref?: { source_kind: string; source_id: string; kind: 'lorebook'; id: string; digest?: string }
  entry_count?: number
  bindings?: Array<{ scope_kind: string; scope_id: string; enabled?: boolean | number; role?: string }>
  /** 这本书来自本次开局的包（不一定已经在书库里）。 */
  module?: boolean
  /** 包内声明册：还在包里、尚未进书库；开局勾选时才物化成自己的书。 */
  catalog?: boolean
}

export interface CreationLorebookRow extends CreationLorebook {
  active: boolean
  primary: boolean
  /** 世界主册自动创建的 0 条占位：随世界带入，本身没有内容可选。 */
  placeholder: boolean
  /** 面板上是否给勾选框。占位行不给：勾它不会多带任何条目。 */
  selectable: boolean
  scope: 'world' | 'global' | 'reference' | 'pack'
}

/** Read-only explanation of the current resolver; this does not choose or bind books. */
export function projectCreationLorebooks(books: CreationLorebook[], worldId: string, source: string): CreationLorebookRow[] {
  const inheritedWorld = source === '__builtin__' ? worldId : ''
  const referenceWorldId = source.startsWith('copy:') ? source.slice(5) : ''
  const seen = new Set<string>()
  const rows: CreationLorebookRow[] = []
  for (const book of books) {
    if (!book.id || seen.has(book.id)) continue
    if (book.module) {
      // 包内册没有 world/global binding 可言：它跟着"这次开局用的是哪个包"
      // 出现，勾上就是"本局带上它"，落地发生在创建事务里。
      seen.add(book.id)
      rows.push({
        ...book,
        scope: 'pack',
        primary: false,
        placeholder: false,
        selectable: true,
        active: book.enabled !== false,
      })
      continue
    }
    const bindings = book.bindings || []
    const worldBindings = (inheritedWorld || referenceWorldId)
      ? bindings.filter(binding => binding.scope_kind === 'world' && binding.scope_id === (inheritedWorld || referenceWorldId))
      : []
    const globalBindings = bindings.filter(binding => binding.scope_kind === 'global')
    const reference = Boolean(referenceWorldId && worldBindings.length)
    if (!reference && !worldBindings.length && !globalBindings.length) continue
    seen.add(book.id)
    const activeGlobal = globalBindings.some(binding => binding.enabled !== false && binding.enabled !== 0)
    const primary = worldBindings.some(binding => binding.role === 'primary')
    // 世界主册是每个世界自动建的一本书，0 条时它只是容器：勾选它并不会把
    // 任何条目带进本局。把它当普通资料册列出来，用户会以为自己选了两本书，
    // 其中一本还"丢了内容"。
    const placeholder = primary && book.source_kind === 'world' && (book.entry_count ?? 0) === 0
    rows.push({
      ...book,
      scope: activeGlobal || (!reference && !worldBindings.length) ? 'global' : reference ? 'reference' : 'world',
      primary,
      placeholder,
      selectable: !placeholder,
      active: book.enabled !== false && (reference || activeGlobal || worldBindings.some(binding => binding.enabled !== false && binding.enabled !== 0)),
    })
  }
  return rows
}

/** 世界自己的资料册里有没有真内容；`undefined` = 还没确认。 */
export function worldBooksHaveContent(rows: CreationLorebookRow[]): boolean | undefined {
  if (!rows.length) return undefined
  return rows.some(row => row.scope !== 'pack' && row.active && (row.entry_count ?? 0) > 0)
}
