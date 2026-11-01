import { onBeforeUnmount, ref } from 'vue'

import type { JsonObject } from '@/api/types'
import {
  floaterLifetimeMs, floatersFromEvents,
  type FeedbackLabels, type Floater,
} from './feedback'

/**
 * Holds the floaters of the latest resolution and clears them once their
 * animation is over. A new play() replaces whatever is still on screen.
 */
export function useFeedback(labels: () => FeedbackLabels) {
  const floaters = ref<Floater[]>([])
  let batch = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  function clear(): void {
    if (timer) clearTimeout(timer)
    timer = undefined
    floaters.value = []
  }

  function play(events: JsonObject[]): void {
    clear()
    const next = floatersFromEvents(events, labels(), ++batch)
    if (!next.length) return
    floaters.value = next
    timer = setTimeout(clear, floaterLifetimeMs(next))
  }

  onBeforeUnmount(clear)
  return { floaters, play, clear }
}
