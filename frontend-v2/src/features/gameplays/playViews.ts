import { defineAsyncComponent, type Component } from 'vue'

// Play view per gameplay mode (ADR-0013 §4). Each mode owns its view under
// features/<mode>/; the tabletop PlayView stays free of other modes' branches.
const PlayView = defineAsyncComponent(() => import('@/features/play/PlayView.vue').then(module => module.default))
const GalPlayView = defineAsyncComponent(() => import('@/features/gal/GalPlayView.vue').then(module => module.default))
const UnsupportedGameplayView = defineAsyncComponent(() => import('@/features/gameplays/UnsupportedGameplayView.vue').then(module => module.default))

const MODE_VIEWS: Readonly<Record<string, Component>> = { gal: GalPlayView }
const TABLETOP_MODES = new Set(['', 'free', 'adventure'])

export function isKnownGameplayMode(playMode: unknown): boolean {
  const normalized = String(playMode ?? '').trim().toLowerCase()
  return TABLETOP_MODES.has(normalized) || normalized in MODE_VIEWS
}

/** Only established tabletop modes may use the legacy narrative view. */
export function playViewFor(playMode: unknown): Component {
  const normalized = String(playMode ?? '').trim().toLowerCase()
  if (TABLETOP_MODES.has(normalized)) return PlayView
  return MODE_VIEWS[normalized] ?? UnsupportedGameplayView
}
