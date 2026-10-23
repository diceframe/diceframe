import { defineAsyncComponent, type Component } from 'vue'

// Play view per gameplay mode (ADR-0012 §4). Each mode owns its view under
// features/<mode>/; the tabletop PlayView stays free of other modes' branches.
const PlayView = defineAsyncComponent(() => import('@/features/play/PlayView.vue').then(module => module.default))
const GalPlayView = defineAsyncComponent(() => import('@/features/gal/GalPlayView.vue').then(module => module.default))

const MODE_VIEWS: Readonly<Record<string, Component>> = { gal: GalPlayView }

/** Modes without their own view (free / adventure, or a mode this client does not know) keep the tabletop view. */
export function playViewFor(playMode: unknown): Component {
  return MODE_VIEWS[String(playMode ?? '').trim().toLowerCase()] ?? PlayView
}
