<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import { readCurrentGame } from '@/stores/gameContext'
import { isKnownGameplayMode, playViewFor } from '@/features/gameplays/playViews'
import GameplayProjectionView from '@/features/gameplays/GameplayProjectionView.vue'
import ProfileBoard from './ProfileBoard.vue'
import type { ProfileView } from './types'
const route = useRoute(), { t } = useLocale(), loading = ref(false), error = ref(''), state = ref<ProfileView | null>(null), playMode = ref(''), providerDiagnostic = ref(''), providerView = ref<Record<string, unknown> | null>(null)
const key = computed(() => String(route.query.game || readCurrentGame() || ''))
const playView = computed(() => playViewFor(playMode.value))
let epoch = 0, resolvedPath = ''
async function load() {
  const current = ++epoch
  loading.value = !!key.value; error.value = ''; state.value = null; playMode.value = ''; providerDiagnostic.value = ''; providerView.value = null
  try {
    if (key.value) {
      const base = `/games/${encodeURIComponent(key.value)}`
      const detail = await api<{ game_profile?: object | null, play_mode?: string }>(base)
      if (current !== epoch) return
      playMode.value = String(detail.play_mode || '')
      if (detail.game_profile && isKnownGameplayMode(playMode.value)) {
        const value = await api<ProfileView>(`${base}/profile`)
        if (current === epoch) state.value = value.profile ? value : null
      }
      if (current === epoch && !isKnownGameplayMode(playMode.value)) {
        try {
          const ownerView = await api<Record<string, unknown> & { ok?: boolean; diagnostic?: string; code?: string }>(`${base}/gameplay-view`)
          if (current === epoch && ownerView?.ok) providerView.value = ownerView
          else if (current === epoch) providerDiagnostic.value = String(ownerView?.diagnostic || ownerView?.code || '')
        } catch (cause) {
          // Unknown modes remain a recoverable read-only entry. The endpoint
          // supplies the owner diagnostic when available; HTTP 409 is expected
          // while no provider is installed.
          if (current === epoch) providerDiagnostic.value = errorMessage(cause)
        }
      }
      if (current === epoch) resolvedPath = route.fullPath
    }
  } catch (cause) { if (current === epoch) error.value = errorMessage(cause) }
  finally { if (current === epoch) loading.value = false }
}
watch(() => route.fullPath, () => {
  if (route.name === 'play' && (route.fullPath !== resolvedPath || error.value)) void load()
}, { immediate: true })
</script>
<template>
  <section v-if="loading" class="view" role="status">{{ t('refresh') }}…</section>
  <section v-else-if="error" class="view"><p role="alert">{{ error }}</p><button @click="load">{{ t('refresh') }}</button></section>
  <ProfileBoard v-else-if="state" :key="key" :game-key="key" :initial="state" />
  <GameplayProjectionView v-else-if="providerView" :mode-id="playMode" :game-key="key" :projection="providerView" @refresh="load" />
  <component :is="playView" v-else :play-mode="playMode" :diagnostic="providerDiagnostic" />
</template>
