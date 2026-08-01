import type { CharacterPortrait } from '@/api/types'

export interface BuiltinPortrait {
  id: string
  ruleId: string
  index: number
  image: string
  position: string
}

const SUPPORTED_RULES = new Set([
  'dnd5e',
  'freeform_coc',
  'freeform_cyberpunk',
  'freeform_fantasy',
  'freeform_wuxia',
  'tavern_free',
])

const ATLAS_POSITIONS = ['0% 0%', '100% 0%', '0% 100%', '100% 100%']
const PORTRAITS_PER_ATLAS = ATLAS_POSITIONS.length
const BUILTIN_ATLAS_COUNT = 2

function builtinRule(ruleId?: string): string {
  const normalized = String(ruleId || '').replace(/_en$/, '')
  return SUPPORTED_RULES.has(normalized) ? normalized : 'freeform_fantasy'
}

function hash(value: string): number {
  let output = 2166136261
  for (let i = 0; i < value.length; i += 1) {
    output ^= value.charCodeAt(i)
    output = Math.imul(output, 16777619)
  }
  return output >>> 0
}

export function builtinPortraits(ruleId?: string): BuiltinPortrait[] {
  const rule = builtinRule(ruleId)
  return Array.from({ length: PORTRAITS_PER_ATLAS * BUILTIN_ATLAS_COUNT }, (_, index) => {
    const atlasIndex = Math.floor(index / PORTRAITS_PER_ATLAS)
    const atlasSuffix = atlasIndex === 0 ? '' : `_${atlasIndex + 1}`
    return {
      id: `${rule}:${index}`,
      ruleId: rule,
      index,
      image: `${import.meta.env.BASE_URL}avatars/${rule}${atlasSuffix}.webp`,
      position: ATLAS_POSITIONS[index % PORTRAITS_PER_ATLAS],
    }
  })
}

export function defaultBuiltinPortrait(ruleId?: string, seed?: string): BuiltinPortrait {
  const options = builtinPortraits(ruleId)
  return options[hash(`${builtinRule(ruleId)}|${seed || 'default'}`) % options.length]
}

export function resolveBuiltinPortrait(portrait?: CharacterPortrait, ruleId?: string, seed?: string): BuiltinPortrait {
  if (portrait?.kind === 'builtin' && portrait.id) {
    const [storedRule, rawIndex] = portrait.id.split(':')
    const options = builtinPortraits(storedRule)
    const index = Number(rawIndex)
    if (Number.isInteger(index) && index >= 0 && index < options.length) return options[index]
  }
  return defaultBuiltinPortrait(ruleId, seed)
}

export function initials(name?: string): string {
  const value = String(name || '?').trim()
  return Array.from(value).slice(0, 2).join('').toUpperCase() || '?'
}
