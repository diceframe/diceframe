// Shared content-overview logic used by CreatorStudioView and CreatorAssistantWindow.

export type ContentStat = { label: string; value: number }
export type ContentOverviewResult = { kind: string; stats: ContentStat[]; hints: string[] }

export function detectProjectKind(files: Record<string, string>): string {
  try {
    const manifest = JSON.parse(files['plugin.json'] || '{}')
    if (manifest.kind) return manifest.kind
    if (manifest.plugin_type === 'tool') return 'toolkit'
    const caps: string[] = manifest.capabilities || []
    if (caps.includes('content.rule')) return 'rule'
    if (manifest.plugin_type === 'gameplay') return 'gameplay'
  } catch { /* fall through */ }
  return 'adventure'
}

export function computeContentOverview(
  files: Record<string, string>,
  kind: string,
  zh: boolean,
): ContentOverviewResult | null {
  if (kind === 'adventure') {
    let scenes = 0, npcs = 0, chapters = 0
    for (const [p, content] of Object.entries(files)) {
      if (p.endsWith('/adventure.json') || p === 'adventure.json') {
        try {
          const adv = JSON.parse(content)
          chapters += adv.chapters?.length ?? 0
          const nodes: Array<{ type?: string }> = adv.nodes ?? []
          // encounter 也是场景节点（战斗/遭遇场景），不算会把总览少报。
          scenes += nodes.filter(n => !n.type || n.type === 'scene' || n.type === 'encounter').length
          npcs += nodes.filter(n => n.type === 'npc').length
        } catch { /* skip */ }
      }
      // NPC 可以作为独立实体文件存在（工程根或冒险包内的 content/npcs/），
      // 不只是 adventure.json 节点。
      if (/(^|\/)content\/npcs\/[^/]+\.json$/.test(p)) npcs += 1
    }
    const hasMap = Object.keys(files).some(p => p.includes('map'))
    return {
      kind,
      stats: [
        { label: zh ? '章节' : 'Chapters', value: chapters },
        { label: zh ? '场景' : 'Scenes', value: scenes },
        { label: zh ? 'NPC' : 'NPCs', value: npcs },
      ],
      hints: [
        ...(!npcs ? [zh ? '还没有 NPC，试试说"加两个 NPC"' : 'No NPCs yet — try "add two NPCs"'] : []),
        ...(!hasMap ? [zh ? '还没有地图' : 'No map yet'] : []),
        ...(scenes < 2 ? [zh ? '场景很少，可以继续丰富' : 'Few scenes — keep building'] : []),
      ],
    }
  }
  if (kind === 'rule') {
    let attrs = 0, skills = 0
    for (const [, content] of Object.entries(files)) {
      try {
        const r = JSON.parse(content)
        if (Array.isArray(r.attributes)) attrs += r.attributes.length
        if (Array.isArray(r.skills)) skills += r.skills.length
      } catch { /* skip */ }
    }
    return {
      kind,
      stats: [
        { label: zh ? '属性' : 'Attributes', value: attrs },
        { label: zh ? '技能' : 'Skills', value: skills },
      ],
      hints: !skills ? [zh ? '还没有技能定义' : 'No skills defined yet'] : [],
    }
  }
  if (kind === 'toolkit') {
    let tools = 0
    try {
      const manifest = JSON.parse(files['plugin.json'] || '{}')
      tools = (manifest.capabilities as string[] || []).filter((c: string) => c.startsWith('tool.')).length
    } catch { /* skip */ }
    return {
      kind,
      stats: [{ label: zh ? '工具函数' : 'Tools', value: tools }],
      hints: [],
    }
  }
  if (kind === 'gameplay') {
    const docs = Object.keys(files).filter(p => p.startsWith('docs/')).length
    return {
      kind,
      stats: [{ label: zh ? '设计文档' : 'Design docs', value: docs }],
      hints: docs < 1 ? [zh ? '先在 docs/ 写清楚设计' : 'Write your design in docs/'] : [],
    }
  }
  return null
}
