// Fabula Ultima 冲突投影里的逐步战斗记录条目。
export interface CombatHistoryEntry {
  seq?: number
  round?: number
  step?: number
  actor_name?: string
  action?: string
  skill?: string
  item?: string
  note?: string
  targets?: string[]
  rolls?: number[]
  total?: number
  dc?: number
  target_dcs?: Record<string, number>
  verdict?: string
  text?: string
  effects?: Array<{ type?: string; target?: string; amount?: number; status?: string; stat?: string; value?: number; affinity?: string; multiplier?: number }>
}
