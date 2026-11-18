import { describe, expect, it } from 'vitest'
import { computeContentOverview, detectProjectKind } from '../src/features/creator/contentOverview'

const adventureJson = {
  chapters: [{ id: 'ch1' }, { id: 'ch2' }],
  nodes: [
    { id: 'start', type: 'scene' },
    { id: 'square', type: 'scene' },
    { id: 'well', type: 'scene' },
    { id: 'tunnel', type: 'scene' },
    { id: 'guardian', type: 'encounter' },
    { id: 'ending', type: 'scene' },
  ],
}

function adventureFiles(extra: Record<string, string> = {}): Record<string, string> {
  return {
    'plugin.json': JSON.stringify({ kind: 'adventure' }),
    'adventures/pack-1/adventure.json': JSON.stringify(adventureJson),
    ...extra,
  }
}

describe('creator content overview', () => {
  it('detects the adventure kind from plugin.json', () => {
    expect(detectProjectKind(adventureFiles())).toBe('adventure')
  })

  it('counts chapters, scenes including encounters, and node NPCs', () => {
    const overview = computeContentOverview(adventureFiles(), 'adventure', true)
    const stat = (label: string) => overview!.stats.find(s => s.label === label)!.value
    expect(stat('章节')).toBe(2)
    // encounter 也是场景：5 scene + 1 encounter = 6
    expect(stat('场景')).toBe(6)
    expect(stat('NPC')).toBe(0)
  })

  it('counts standalone content/npcs files as NPCs', () => {
    const files = adventureFiles({
      'content/npcs/mara.json': '{}',
      'content/npcs/mayor.json': '{}',
      'content/npcs/guardian.json': '{}',
      'content/items/lantern.json': '{}',
      'docs/npcs-notes.md': 'not a npc file',
    })
    const overview = computeContentOverview(files, 'adventure', true)
    const stat = (label: string) => overview!.stats.find(s => s.label === label)!.value
    expect(stat('NPC')).toBe(3)
    // 有 NPC 后不再提示「还没有 NPC」
    expect(overview!.hints).not.toContain('还没有 NPC，试试说"加两个 NPC"')
  })

  it('keeps English labels when zh is false', () => {
    const overview = computeContentOverview(adventureFiles(), 'adventure', false)
    expect(overview!.stats.map(s => s.label)).toEqual(['Chapters', 'Scenes', 'NPCs'])
  })
})
