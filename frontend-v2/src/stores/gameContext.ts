import type { LocationQuery } from 'vue-router'

export const CURRENT_GAME_KEY = 'currentGame'
export const LEGACY_CURRENT_GAME_KEY = 'trpg_current_game'
export const LEGACY_CURRENT_GAME_NAME_KEY = 'trpg_current_game_name'

export function queryString(value: unknown): string {
  return Array.isArray(value) ? String(value[0] || '') : String(value || '')
}

export function gameFromQuery(query: LocationQuery): string {
  return queryString(query.game)
}

export function readCurrentGame(): string {
  return localStorage.getItem(CURRENT_GAME_KEY) || localStorage.getItem(LEGACY_CURRENT_GAME_KEY) || ''
}

export function rememberCurrentGame(gameKey: string, worldName = '') {
  if (!gameKey) return
  localStorage.setItem(CURRENT_GAME_KEY, gameKey)
  localStorage.setItem(LEGACY_CURRENT_GAME_KEY, gameKey)
  if (worldName) localStorage.setItem(LEGACY_CURRENT_GAME_NAME_KEY, worldName)
}

export function clearCurrentGame(gameKey?: string) {
  const current = readCurrentGame()
  if (gameKey && current && current !== gameKey) return
  localStorage.removeItem(CURRENT_GAME_KEY)
  localStorage.removeItem(LEGACY_CURRENT_GAME_KEY)
  localStorage.removeItem(LEGACY_CURRENT_GAME_NAME_KEY)
}
