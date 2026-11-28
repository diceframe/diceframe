/**
 * 多面体骰子 2.5D 分面几何数据（viewBox: 0 0 100 104）。
 * 每个面带有独立受光等级，配合主题变量呈现立体的宝石/金属切面光影。
 */

export type FacetShade = 'top' | 'light' | 'center' | 'mid' | 'dark' | 'deep'

export interface PolyFacet {
  points: string
  shade: FacetShade
}

export interface PolyGeometry {
  outline: string
  facets: PolyFacet[]
  ridges: string
  /** 数字视觉中心 Y 偏移（px，相对 74px 容器） */
  numberTop: number
}

const D4: PolyGeometry = {
  outline: '50,6 94,88 6,88',
  facets: [
    { points: '50,6 50,64 6,88', shade: 'light' },
    { points: '50,6 94,88 50,64', shade: 'dark' },
    { points: '50,64 94,88 6,88', shade: 'deep' },
  ],
  ridges: 'M50 6 50 64M50 64 6 88M50 64 94 88',
  numberTop: 34,
}

const D6: PolyGeometry = {
  outline: '50,5 93,27 93,77 50,99 7,77 7,27',
  facets: [
    { points: '50,5 93,27 50,49 7,27', shade: 'top' },
    { points: '7,27 50,49 50,99 7,77', shade: 'mid' },
    { points: '93,27 93,77 50,99 50,49', shade: 'dark' },
    { points: '50,14 82,30 50,46 18,30', shade: 'center' },
  ],
  ridges: 'M7 27 50 49 93 27M50 49V99',
  numberTop: 25,
}

const D8: PolyGeometry = {
  outline: '50,4 94,52 50,100 6,52',
  facets: [
    { points: '50,4 28,52 6,52', shade: 'top' },
    { points: '50,4 72,52 28,52', shade: 'center' },
    { points: '50,4 94,52 72,52', shade: 'mid' },
    { points: '6,52 28,52 50,100', shade: 'dark' },
    { points: '28,52 72,52 50,100', shade: 'mid' },
    { points: '72,52 94,52 50,100', shade: 'deep' },
  ],
  ridges: 'M50 4 28 52 50 100M50 4 72 52 50 100M6 52H94',
  numberTop: 22,
}

const D10: PolyGeometry = {
  outline: '50,4 93,38 78,80 50,100 22,80 7,38',
  facets: [
    { points: '50,4 27,39 7,38', shade: 'top' },
    { points: '50,4 73,39 50,76 27,39', shade: 'center' },
    { points: '50,4 93,38 73,39', shade: 'light' },
    { points: '7,38 27,39 22,80', shade: 'mid' },
    { points: '93,38 78,80 73,39', shade: 'dark' },
    { points: '27,39 50,76 22,80', shade: 'dark' },
    { points: '73,39 78,80 50,76', shade: 'deep' },
    { points: '22,80 50,76 50,100', shade: 'dark' },
    { points: '78,80 50,100 50,76', shade: 'deep' },
  ],
  ridges: 'M50 4 27 39 50 76 73 39 50 4M7 38 27 39 22 80M93 38 73 39 78 80M50 76V100',
  numberTop: 21,
}

const D12: PolyGeometry = {
  outline: '50,4 93,34 80,85 50,100 20,85 7,34',
  facets: [
    { points: '50,4 93,34 76,43 50,23', shade: 'top' },
    { points: '50,4 50,23 24,43 7,34', shade: 'light' },
    { points: '7,34 24,43 34,74 20,85', shade: 'mid' },
    { points: '93,34 80,85 66,74 76,43', shade: 'dark' },
    { points: '20,85 34,74 66,74 80,85 50,100', shade: 'deep' },
    { points: '50,23 76,43 66,74 34,74 24,43', shade: 'center' },
  ],
  ridges: 'M50 23 76 43 66 74 34 74 24 43ZM50 4V23M93 34 76 43M80 85 66 74M20 85 34 74M7 34 24 43',
  numberTop: 23,
}

const D20: PolyGeometry = {
  outline: '50,4 94,28 94,76 50,100 6,76 6,28',
  facets: [
    { points: '50,4 25,36 6,28', shade: 'top' },
    { points: '50,4 75,36 25,36', shade: 'light' },
    { points: '50,4 94,28 75,36', shade: 'top' },
    { points: '6,28 25,36 6,76', shade: 'mid' },
    { points: '94,28 94,76 75,36', shade: 'dark' },
    { points: '25,36 75,36 50,79', shade: 'center' },
    { points: '6,76 25,36 50,79', shade: 'dark' },
    { points: '94,76 50,79 75,36', shade: 'deep' },
    { points: '6,76 50,79 50,100', shade: 'deep' },
    { points: '94,76 50,100 50,79', shade: 'deep' },
  ],
  ridges: 'M50 4 25 36 6 28M50 4 75 36 94 28M25 36 75 36 50 79 25 36M6 76 25 36M94 76 75 36M6 76 50 79 94 76M50 100 50 79',
  numberTop: 22,
}

export function polyGeometryForSides(sides: number): PolyGeometry {
  if (sides === 4) return D4
  if (sides === 6) return D6
  if (sides === 8) return D8
  if (sides === 10 || sides === 100) return D10
  if (sides === 12) return D12
  return D20
}
