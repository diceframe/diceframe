export interface GameSsePayload {
  type?: string
  text?: string
}

export type GameSseEffect = 'baseline' | 'narration-delta' | 'narration-reset' | 'refresh'

/**
 * SSE baseline 只确认“HTTP 首次快照之后从哪里继续”，不得再触发一次完整刷新。
 * 其余公开/私有状态事件仍统一走 useGame 的合并刷新，避免各组件解释存档结构。
 */
export function gameSseEffect(payload: GameSsePayload | null): GameSseEffect {
  const type = payload?.type || ''
  if (type === 'baseline') return 'baseline'
  if (type === 'narration_delta') return 'narration-delta'
  if (type === 'narration_reset') return 'narration-reset'
  return 'refresh'
}

export function updatedGameSseCursor(current: string, eventId: string): string {
  return eventId || current
}
