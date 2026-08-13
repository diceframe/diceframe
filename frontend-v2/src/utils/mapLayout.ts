import type { MapLocation } from '@/api/types'

export interface MapNode {
  id: string
  name: string
  x: number
  y: number
  current: boolean
}

export interface MapLayoutOptions {
  /** 迭代次数，节点越多越该降低以控制耗时（O(n²) 斥力） */
  iterations?: number
  /** 布局画布半宽/半高，坐标落在 [-size, size] */
  size?: number
  /** 当前场景节点 id，作为锚点固定在中心 */
  anchorId?: string
}

const EPS = 1e-6

/**
 * 基于 connected_to 关系的力导向布局。
 *
 * - 相连节点用弹簧力拉近，不相连用库仑斥力推开，多轮迭代后收敛。
 * - 当前场景节点作为锚点钉在中心，保证剧情所在地始终可见。
 * - 迭代上限内优先收敛，节点很多时降低迭代次数避免卡顿。
 * - 无任何连接（孤立图）时回落为较松的网格，不再挤成一个圈。
 */
export function forceLayout(locations: MapLocation[], options: MapLayoutOptions = {}): MapNode[] {
  const n = locations.length
  if (n === 0) return []
  const { size = 42, anchorId } = options
  const iterations = options.iterations ?? (n > 300 ? 120 : n > 120 ? 180 : 260)

  const ids = locations.map(l => String(l.id ?? l.name ?? ''))
  const nodes = locations.map((l, i) => {
    const angle = (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2
    return {
      id: ids[i],
      name: l.name || l.id || `#${i}`,
      x: size * 0.35 * Math.cos(angle),
      y: size * 0.35 * Math.sin(angle),
      current: false,
    }
  })
  // 找当前场景节点；若没有匹配，只选择一个内部布局锚点，不把它误标为当前地点。
  let anchorIndex = -1
  if (anchorId) {
    anchorIndex = nodes.findIndex(node => node.id === anchorId || node.name === anchorId)
  }
  const hasCurrentAnchor = anchorIndex >= 0
  if (anchorIndex < 0) {
    anchorIndex = nodes.reduce((best, node, i) => (node.name.length > nodes[best].name.length ? i : best), 0)
  }

  // 邻接表：connected_to 可能引用 id 或 name，两边都建索引
  const nameToIndex = new Map<string, number>()
  nodes.forEach((node, i) => { nameToIndex.set(node.name, i); nameToIndex.set(node.id, i) })
  const adjacency: number[][] = Array.from({ length: n }, () => [])
  const edgeKey = new Set<string>()
  locations.forEach((loc, i) => {
    for (const target of loc.connected_to || []) {
      const j = nameToIndex.get(String(target))
      if (j === undefined || j === i) continue
      const key = i < j ? `${i}-${j}` : `${j}-${i}`
      if (edgeKey.has(key)) continue
      edgeKey.add(key)
      adjacency[i].push(j)
      adjacency[j].push(i)
    }
  })

  const nodeArr = nodes
  const K = size * 0.85 // 弹簧自然长度
  const dt = 0.08
  for (let iter = 0; iter < iterations; iter++) {
    const fx = new Float64Array(n)
    const fy = new Float64Array(n)
    // 斥力：所有节点对（超多节点时每隔几轮才全量，控制 O(n²) 成本）
    if (n <= 400 || iter % 2 === 0) {
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const dx = nodeArr[i].x - nodeArr[j].x
          const dy = nodeArr[i].y - nodeArr[j].y
          const dist = Math.hypot(dx, dy) + EPS
          const force = (K * K) / (dist * dist) * 0.5
          const ux = dx / dist, uy = dy / dist
          fx[i] += ux * force; fy[i] += uy * force
          fx[j] -= ux * force; fy[j] -= uy * force
        }
      }
    }
    // 弹簧力：沿连接
    for (let i = 0; i < n; i++) {
      for (const j of adjacency[i]) {
        if (j <= i) continue
        const dx = nodeArr[j].x - nodeArr[i].x
        const dy = nodeArr[j].y - nodeArr[i].y
        const dist = Math.hypot(dx, dy) + EPS
        const force = (dist - K) * 0.04
        const ux = dx / dist, uy = dy / dist
        fx[i] += ux * force; fy[i] += uy * force
        fx[j] -= ux * force; fy[j] -= uy * force
      }
    }
    for (let i = 0; i < n; i++) {
      if (i === anchorIndex) continue // 锚点不动
      nodeArr[i].x += fx[i] * dt
      nodeArr[i].y += fy[i] * dt
    }
  }

  // 中心化并归一化：锚点固定在原点，其余节点相对锚点做整体缩放
  const anchorX = nodeArr[anchorIndex].x
  const anchorY = nodeArr[anchorIndex].y
  let maxR = 0
  for (const node of nodeArr) {
    const r = Math.hypot(node.x - anchorX, node.y - anchorY)
    if (r > maxR) maxR = r
  }
  const scale = maxR > EPS ? size / maxR : 1
  for (const node of nodeArr) {
    node.x = (node.x - anchorX) * scale
    node.y = (node.y - anchorY) * scale
  }
  // 声明式地图可为部分或全部地点提供稳定坐标；未声明的地点仍沿用确定性力导向结果。
  locations.forEach((location, index) => {
    const x = Number(location.x)
    const y = Number(location.y)
    if (Number.isFinite(x) && Number.isFinite(y) && x >= -50 && x <= 50 && y >= -50 && y <= 50) {
      nodeArr[index].x = x
      nodeArr[index].y = y
    }
  })
  if (hasCurrentAnchor) nodeArr[anchorIndex].current = true
  return nodeArr
}
