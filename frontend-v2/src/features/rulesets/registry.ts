import { defineAsyncComponent, type Component } from 'vue'

type ExperienceLoader = () => Promise<{ default: Component }>
type ComponentLoader = () => Promise<{ default: Component }>

export type RulesetPlayTool = 'campaign' | 'combat'

export interface RulesetPlayCopy {
  menu: string
  campaign: string
  combat: string
  title: string
}

export interface RulesetPlayExtension {
  campaign: Component | null
  combat: Component | null
  copy: (language: string) => RulesetPlayCopy
}

export interface RulesetAdvancementExtension {
  component: Component
  title: (language: string) => string
}

const loaders: Record<string, ExperienceLoader> = {
  dnd2024: () => import('./dnd2024/create/Dnd2024CharacterBuilder.vue'),
}
const components = new Map<string, Component>()
const playLoaders: Record<string, Partial<Record<RulesetPlayTool, ComponentLoader>>> = {
  'core:dnd2024': {
    campaign: () => import('./dnd2024/campaign/Dnd2024CampaignPanel.vue'),
    combat: () => import('./dnd2024/combat/Dnd2024CombatPanel.vue'),
  },
}
const playComponents = new Map<string, Component>()
const advancementLoaders: Record<string, ComponentLoader> = {
  'core:dnd2024': () => import('./dnd2024/progression/Dnd2024AdvancementPanel.vue'),
}
const advancementComponents = new Map<string, Component>()
// UI shortcuts follow each registered adapter; server rest validation remains authoritative.
const characterCenterLoaders: Record<string, { load: ComponentLoader; rest: boolean }> = {
  'core:dnd2024': { load: () => import('./ProfessionalCharacterCenter.vue'), rest: true },
}
const characterCenterComponents = new Map<string, Component>()

// 规则专属的局内面板：侧栏面板挂在通用侧栏的 after-character 插槽，时间线面板挂在
// ruleset-log 插槽；clockControls 表示该规则向 GM 开放命刻编辑。
const sidebarLoaders: Record<string, { load: ComponentLoader; clockControls: boolean }> = {
  fabula_ultima: { load: () => import('./fabula_ultima/FuSidebarPanel.vue'), clockControls: true },
}
const sidebarComponents = new Map<string, Component>()
const timelineLoaders: Record<string, ComponentLoader> = {
  fabula_ultima: () => import('./fabula_ultima/FuCombatLog.vue'),
}
const timelineComponents = new Map<string, Component>()

function cachedComponent(
  cache: Map<string, Component>,
  runtimeId: string,
  loader: ComponentLoader | undefined,
): Component | null {
  if (!loader) return null
  let component = cache.get(runtimeId)
  if (!component) {
    component = defineAsyncComponent(loader)
    cache.set(runtimeId, component)
  }
  return component
}

export function resolveRulesetSidebarPanel(runtimeId: string): Component | null {
  return cachedComponent(sidebarComponents, runtimeId, sidebarLoaders[runtimeId]?.load)
}

export function hasRulesetClockControls(runtimeId: string): boolean {
  return sidebarLoaders[runtimeId]?.clockControls === true
}

export function resolveRulesetTimelinePanel(runtimeId: string): Component | null {
  return cachedComponent(timelineComponents, runtimeId, timelineLoaders[runtimeId])
}

function resolvePlayComponent(runtimeId: string, tool: RulesetPlayTool): Component | null {
  const loader = playLoaders[runtimeId]?.[tool]
  if (!loader) return null
  const key = `${runtimeId}:${tool}`
  const cached = playComponents.get(key)
  if (cached) return cached
  const component = defineAsyncComponent(loader)
  playComponents.set(key, component)
  return component
}

export function resolveRulesetExperience(profile: string): Component | null {
  const loader = loaders[profile]
  if (!loader) return null
  const cached = components.get(profile)
  if (cached) return cached
  const component = defineAsyncComponent(loader)
  components.set(profile, component)
  return component
}

export function resolveRulesetPlayExtension(runtimeId: string): RulesetPlayExtension | null {
  if (!playLoaders[runtimeId]) return null
  return {
    campaign: resolvePlayComponent(runtimeId, 'campaign'),
    combat: resolvePlayComponent(runtimeId, 'combat'),
    copy: language => language.startsWith('zh') ? {
      menu: 'DND5E工具', campaign: '冒险与战役', combat: '战斗工具', title: 'DND5E工具',
    } : {
      menu: 'DND5E Tools', campaign: 'Adventure & campaign', combat: 'Combat tools', title: 'DND5E Tools',
    },
  }
}

export function resolveRulesetAdvancementExtension(
  runtimeId: string,
): RulesetAdvancementExtension | null {
  const loader = advancementLoaders[runtimeId]
  if (!loader) return null
  let component = advancementComponents.get(runtimeId)
  if (!component) {
    component = defineAsyncComponent(loader)
    advancementComponents.set(runtimeId, component)
  }
  return {
    component,
    title: language => language.startsWith('zh')
      ? 'D&D 2024 职业升级'
      : 'D&D 2024 advancement',
  }
}

export function resolveRulesetCharacterCenter(runtimeId: string): Component | null {
  const loader = characterCenterLoaders[runtimeId]?.load
  if (!loader) return null
  const cached = characterCenterComponents.get(runtimeId)
  if (cached) return cached
  const component = defineAsyncComponent(loader)
  characterCenterComponents.set(runtimeId, component)
  return component
}

export function hasRulesetRestCenter(runtimeId: string): boolean {
  return characterCenterLoaders[runtimeId]?.rest === true
}
