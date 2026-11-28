<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TacticalSceneAssetRef } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy } from './copy'

const props = defineProps<{
  background: TacticalSceneAssetRef | null
  rect: { x: number; y: number; width: number; height: number }
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))
// source_path is the preserved source reference; the backend does not fetch
// assets, so it is loaded as-is and a failed load degrades to a placeholder.
const url = computed(() => props.background?.source_path || '')
const failed = ref(false)
watch(url, () => { failed.value = false })
const noteSize = computed(() => Math.max(12, Math.min(props.rect.width, props.rect.height) / 18))
</script>

<template>
  <g data-testid="board-background">
    <image
      v-if="url && !failed"
      :href="url"
      :x="rect.x"
      :y="rect.y"
      :width="rect.width"
      :height="rect.height"
      preserveAspectRatio="none"
      data-testid="board-background-image"
      @error="failed = true"
    />
    <g v-else>
      <rect :x="rect.x" :y="rect.y" :width="rect.width" :height="rect.height" class="board-background-empty" />
      <text
        v-if="url && failed"
        :x="rect.x + rect.width / 2"
        :y="rect.y + rect.height / 2"
        :font-size="noteSize"
        text-anchor="middle"
        dominant-baseline="middle"
        class="board-background-note"
        data-testid="board-background-fallback"
      >{{ copy.backgroundUnavailable }}</text>
    </g>
  </g>
</template>