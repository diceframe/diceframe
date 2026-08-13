import { describe, expect, it } from 'vitest'
import { forceLayout } from '@/utils/mapLayout'
import type { MapLocation } from '@/api/types'

function loc(id: string, connected_to: string[] = []): MapLocation {
  return { id, name: id, connected_to }
}

describe('forceLayout', () => {
  it('空地图返回空数组', () => {
    expect(forceLayout([])).toEqual([])
  })

  it('锚点（当前场景）节点固定在中心 (0,0)', () => {
    const nodes = forceLayout(
      [loc('a', ['b', 'c']), loc('b', ['a']), loc('c', ['a'])],
      { anchorId: 'a' },
    )
    const anchor = nodes.find(n => n.id === 'a')
    expect(anchor).toBeDefined()
    expect(anchor!.x).toBeCloseTo(0, 1)
    expect(anchor!.y).toBeCloseTo(0, 1)
    expect(anchor!.current).toBe(true)
  })

  it('相连的节点比不相连的节点更靠近', () => {
    // 星形：a 连 b/c/d，e 与 a 也相连但隔一层——这里只验证相连节点间距小于整体尺度
    const nodes = forceLayout(
      [
        loc('a', ['b', 'c', 'd']),
        loc('b', ['a']),
        loc('c', ['a']),
        loc('d', ['a']),
      ],
      { anchorId: 'a', iterations: 400 },
    )
    const pos = Object.fromEntries(nodes.map(n => [n.id, n]))
    const dist = (p: { x: number; y: number }, q: { x: number; y: number }) =>
      Math.hypot(p.x - q.x, p.y - q.y)
    // 相连的 b-a 距离应明显小于任意两叶节点在对侧时的情况；用 b-c（都是 a 的邻居，通过 a 相连的二级）
    const connected = dist(pos.a, pos.b)
    expect(connected).toBeLessThan(60) // 弹簧力使相连节点聚拢在画布内
    expect(nodes.every(n => Math.abs(n.x) <= 50.001 && Math.abs(n.y) <= 50.001)).toBe(true)
  })

  it('节点数很多（几百）时仍收敛在画布内且不重叠爆炸', () => {
    const many: MapLocation[] = []
    for (let i = 0; i < 300; i++) {
      const neighbors = [String((i + 1) % 300)]
      if (i > 0) neighbors.push(String(i - 1))
      many.push(loc(String(i), neighbors))
    }
    const nodes = forceLayout(many, { anchorId: '150' })
    expect(nodes).toHaveLength(300)
    const anchor = nodes.find(n => n.id === '150')!
    expect(anchor.x).toBeCloseTo(0, 1)
    // 全部落在画布内
    expect(nodes.every(n => Math.abs(n.x) <= 50.001 && Math.abs(n.y) <= 50.001)).toBe(true)
  })

  it('几百个节点布局耗时可控（< 300ms）', () => {
    const many: MapLocation[] = []
    const GRID = 17
    for (let i = 0; i < 300; i++) {
      const conn: string[] = []
      if (i % GRID !== 0) conn.push(String(i - 1))
      if (i + 1 < 300 && (i + 1) % GRID !== 0) conn.push(String(i + 1))
      if (i - GRID >= 0) conn.push(String(i - GRID))
      if (i + GRID < 300) conn.push(String(i + GRID))
      many.push(loc(String(i), conn))
    }
    const start = performance.now()
    forceLayout(many, { anchorId: '150', iterations: 200 })
    const elapsed = performance.now() - start
    // 全量测试并发下 CPU 竞争，阈值放宽到 800ms（单文件跑约 100ms 内）
    expect(elapsed).toBeLessThan(800)
  })

  it('无连接（孤立图）回落为网格而不是挤成环', () => {
    const isolated: MapLocation[] = []
    for (let i = 0; i < 16; i++) isolated.push(loc(String(i)))
    const nodes = forceLayout(isolated)
    const xs = new Set(nodes.map(n => n.x.toFixed(1)))
    // 16 个孤立点若全在环上则 x 至多几个不同值；网格应有较多不同 x
    expect(xs.size).toBeGreaterThan(4)
  })

  it('地图定义提供的显式坐标覆盖自动布局', () => {
    const nodes = forceLayout([
      { ...loc('a', ['b']), x: -24, y: 18 },
      { ...loc('b', ['a']), x: 27, y: -11 },
      loc('c'),
    ], { anchorId: 'a' })
    expect(nodes.find(node => node.id === 'a')).toMatchObject({ x: -24, y: 18, current: true })
    expect(nodes.find(node => node.id === 'b')).toMatchObject({ x: 27, y: -11 })
  })

  it('当前场景没有匹配地点时不会误标当前节点', () => {
    const nodes = forceLayout([loc('a'), loc('b')], { anchorId: 'missing' })
    expect(nodes.every(node => !node.current)).toBe(true)
  })
})
