import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { fetchRulesetAvailableActions } from '@/api/rulesets'
import type { GameDetail, RulesetCombatAction, RulesetGameplayView } from '@/api/types'

interface RulesetGameplayContext {
  gameKey: Ref<string>
  viewer: Ref<string>
  isGm: Ref<boolean>
  detail: Ref<GameDetail | null>
  stateSignal: Ref<number>
}

/** Viewer-filtered gameplay (including panels) is not a combat-only projection. */
export function useRulesetGameplay(context: RulesetGameplayContext) {
  const gameplay = ref<RulesetGameplayView | null>(null)
  const availableActions = ref<RulesetCombatAction[]>([])
  // Mirrors available-actions admission; other optional capabilities only gate UI.
  const enabled = computed(() => Boolean(context.detail.value?.ruleset_runtime?.capabilities?.authoritative_intents))
  let sequence = 0
  let stopped = false
  let poll: number | undefined

  function clear() {
    gameplay.value = null
    availableActions.value = []
  }

  async function refresh(): Promise<void> {
    const gameKey = context.gameKey.value
    if (stopped || !gameKey || !enabled.value) return
    const viewer = context.viewer.value
    const isGm = context.isGm.value
    const request = ++sequence
    const current = () => !stopped && request === sequence
      && context.gameKey.value === gameKey && context.viewer.value === viewer
      && context.isGm.value === isGm && enabled.value
    try {
      const response = await fetchRulesetAvailableActions(gameKey)
      if (!current()) return
      gameplay.value = response.gameplay || null
      availableActions.value = response.available_actions || []
    } catch {
      if (current()) clear()
    }
  }

  function stopPolling() {
    if (poll !== undefined) window.clearInterval(poll)
    poll = undefined
  }

  watch(
    [context.gameKey, context.viewer, context.isGm, enabled, () => context.detail.value?.ruleset_runtime?.id],
    () => {
      // Invalidate synchronously, including A -> B -> A switches before Vue's next tick.
      sequence += 1
      clear()
      stopPolling()
      if (!context.gameKey.value || !enabled.value) return
      void refresh()
      poll = window.setInterval(() => {
        if (document.visibilityState !== 'hidden') void refresh()
      }, 30000)
    },
    { immediate: true, flush: 'sync' },
  )
  watch([() => context.detail.value?.round_number, context.stateSignal], () => { void refresh() })
  onBeforeUnmount(() => {
    stopped = true
    sequence += 1
    stopPolling()
  })

  return { gameplay, availableActions, refresh }
}
