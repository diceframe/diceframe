import { describe, expect, it } from 'vitest'
import { parseGMText } from '../src/utils/renderer'

describe('renderer', () => {
  it('keeps bullet-like narration as separate readable items', () => {
    const block = parseGMText('你推开门。\n- 火把忽然熄灭\n- 石阶下传来脚步声')
    expect(block.paragraphs).toHaveLength(3)
    expect(block.paragraphs[1]).toContain('火把忽然熄灭')
    expect(block.paragraphs[1]).toContain('gm-list-marker')
    expect(block.paragraphs[2]).toContain('石阶下传来脚步声')
  })

  it('hides protocol tags after a nonstandard state heading', () => {
    const block = parseGMText(
      '玛尔塔把药草推到柜台上。\n【**状态**变更】\nPAY:u1:15\nLOOT:u1:解毒草\nSCENE:南街草药铺',
    )
    expect(block.paragraphs.join('')).toContain('玛尔塔把药草推到柜台上')
    expect(block.paragraphs.join('')).not.toContain('PAY:')
    expect(block.paragraphs.join('')).not.toContain('LOOT:')
    expect(block.tags.map(tag => tag.text)).toContain('解毒草')
  })

  it('splits cue-driven GM narration and marks important table facts', () => {
    const block = parseGMText('你靠近祭坛。随后进行 D20 感知检定，成功后获得线索：地砖下有钥匙。\n【资源变化】 HP -2')
    expect(block.paragraphs.length).toBeGreaterThanOrEqual(2)
    expect(block.paragraphs.join('\n')).toContain('kw-roll')
    expect(block.paragraphs.join('\n')).toContain('kw-key')
    expect(block.paragraphs.join('\n')).toContain('kw-change')
    expect(block.states).toHaveLength(1)
    expect(block.states[0].cls).toBe('warn')
  })
})
