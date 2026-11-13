import type { Component } from 'vue'
import type { MessageKey } from '@/i18n'
import {
  BookOutline,
  CloudOutline,
  DocumentTextOutline,
  EarthOutline,
  ExtensionPuzzleOutline,
  GameControllerOutline,
  HomeOutline,
  MapOutline,
  OptionsOutline,
  PersonOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

export type AppNavItemId =
  | 'overview'
  | 'play'
  | 'characters'
  | 'creator'
  | 'library'
  | 'worlds'
  | 'adventures'
  | 'modules'
  | 'lorebook'
  | 'rules'
  | 'memory'
  | 'logs'
  | 'plugins'
  | 'settings'

export type AppNavGroupId = 'content' | 'management'

export type AppNavItem = {
  id: AppNavItemId
  labelKey: MessageKey
  icon: Component
}

export type AppNavGroup = {
  id: AppNavGroupId
  labelKey: MessageKey
  icon: Component
  defaultItemId: AppNavItemId
  itemIds: readonly AppNavItemId[]
}

// Route names are canonical identity. Locale only resolves labelKey for display.
export const appNavItems = [
  // The archive is the app's overview surface. Keep the primary label stable
  // with the product terminology instead of the older "My games" wording.
  { id: 'overview', labelKey: 'navOverview', icon: HomeOutline },
  { id: 'play', labelKey: 'navPlay', icon: GameControllerOutline },
  { id: 'characters', labelKey: 'navCharacters', icon: PersonOutline },
  { id: 'library', labelKey: 'navContentLibrary', icon: BookOutline },
  { id: 'creator', labelKey: 'navCreator', icon: MapOutline },
  { id: 'worlds', labelKey: 'navWorlds', icon: EarthOutline },
  { id: 'adventures', labelKey: 'navAdventures', icon: MapOutline },
  { id: 'modules', labelKey: 'navModules', icon: BookOutline },
  { id: 'lorebook', labelKey: 'navLorebook', icon: BookOutline },
  { id: 'rules', labelKey: 'navRules', icon: OptionsOutline },
  { id: 'memory', labelKey: 'navMemory', icon: CloudOutline },
  { id: 'logs', labelKey: 'navLogs', icon: DocumentTextOutline },
  { id: 'plugins', labelKey: 'navPlugins', icon: ExtensionPuzzleOutline },
  { id: 'settings', labelKey: 'navSettings', icon: SettingsOutline },
] as const satisfies readonly AppNavItem[]

// W1 拍板 F：创作工坊从一级入口移入「内容」组，使用动线排序
// （内容库=用什么 → 创作工坊=做什么 → 内容包=装什么 → 规则=怎么跑 → 世界书）。
export const appNavGroups = [
  {
    id: 'content',
    labelKey: 'navContent',
    icon: BookOutline,
    defaultItemId: 'library',
    itemIds: ['library', 'creator', 'modules', 'rules', 'lorebook'],
  },
  {
    id: 'management',
    labelKey: 'navManagement',
    icon: SettingsOutline,
    defaultItemId: 'settings',
    itemIds: ['memory', 'logs', 'plugins', 'settings'],
  },
] as const satisfies readonly AppNavGroup[]

export const primaryNavItemIds = ['overview', 'play', 'characters'] as const satisfies readonly AppNavItemId[]

const itemById = new Map<AppNavItemId, AppNavItem>(appNavItems.map(item => [item.id, item]))

export function navItem(id: AppNavItemId): AppNavItem {
  const item = itemById.get(id)
  if (!item) throw new Error(`Unknown app navigation item: ${id}`)
  return item
}

export function navGroupItems(group: AppNavGroup): AppNavItem[] {
  return group.itemIds.map(navItem)
}

export function navGroupForRoute(routeName: string): AppNavGroupId | null {
  if (routeName === 'worlds' || routeName === 'adventures' || routeName === 'creator' || routeName === 'module-detail') return 'content'
  return appNavGroups.find(group => group.itemIds.some(itemId => itemId === routeName))?.id ?? null
}

const navRouteAliases: Partial<Record<AppNavItemId, readonly string[]>> = {
  library: ['adventures'],
  modules: ['module-detail'],
  lorebook: ['worlds'],
}

export function isNavItemActive(itemId: AppNavItemId, routeName: string): boolean {
  return itemId === routeName || Boolean(navRouteAliases[itemId]?.includes(routeName))
}

/** Keep play navigation bound to its explicit game/player identity context. */
export function playContextQuery(query: Record<string, unknown>, fallbackGame = ''): Record<string, string> | null {
  const first = (value: unknown): string => Array.isArray(value) ? String(value[0] || '') : String(value || '')
  const game = first(query.game) || fallbackGame
  if (!game) return null
  const result: Record<string, string> = { game }
  for (const key of ['user', 'share'] as const) {
    const value = first(query[key])
    if (value) result[key] = value
  }
  return result
}
