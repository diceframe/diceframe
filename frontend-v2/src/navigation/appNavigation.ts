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
  { id: 'overview', labelKey: 'navOverview', icon: HomeOutline },
  { id: 'play', labelKey: 'navPlay', icon: GameControllerOutline },
  { id: 'characters', labelKey: 'navCharacters', icon: PersonOutline },
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

export const appNavGroups = [
  {
    id: 'content',
    labelKey: 'navContent',
    icon: BookOutline,
    defaultItemId: 'lorebook',
    itemIds: ['lorebook', 'worlds', 'adventures', 'modules', 'rules'],
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
  return appNavGroups.find(group => group.itemIds.some(itemId => itemId === routeName))?.id ?? null
}
