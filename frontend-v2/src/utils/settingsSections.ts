export type SettingsSectionId = 'general' | 'game' | 'api' | 'connection' | 'models' | 'network' | 'sharing' | 'botapi' | 'appearance' | 'security' | 'access' | 'honors' | 'about'

const SETTINGS_SECTION_IDS = new Set<SettingsSectionId>([
  'api',
  'general',
  'game',
  'connection',
  'models',
  'network',
  'sharing',
  'botapi',
  'appearance',
  'security',
  'access',
  'honors',
  'about',
])

// Retired pages remain valid deep links into the combined General page.
const LEGACY_GENERAL_FOCUS: Record<string, string> = { voice: 'tts', agent: 'agent', system: 'logs' }
export function legacySettingsFocus(section: string): string | undefined {
  return Object.hasOwn(LEGACY_GENERAL_FOCUS, section) ? LEGACY_GENERAL_FOCUS[section] : undefined
}

export function normalizeSettingsSection(value: string): SettingsSectionId | null {
  if (value === 'memory') return 'models'
  if (value === 'advanced') return 'models'
  if (legacySettingsFocus(value)) return 'general'
  return SETTINGS_SECTION_IDS.has(value as SettingsSectionId) ? value as SettingsSectionId : null
}

export function isSettingsSectionAvailable(section: SettingsSectionId, standaloneFrontend: boolean): boolean {
  return section !== 'connection' || standaloneFrontend
}
