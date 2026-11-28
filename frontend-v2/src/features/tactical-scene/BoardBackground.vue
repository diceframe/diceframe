<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneAssetRef } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalSceneCopy } from './copy'
import { useSceneAssetUrl } from './useSceneAssetUrl'

const props = defineProps<{
  background: TacticalSceneAssetRef | null
  rect: { x: number; y: number; width: number; height: number }
  gameKey?: string
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalSceneCopy(locale.value))
const sourcePath = computed(() => props.background?.source_path || '')
// DiceFrame-managed uploads / generated images resolve via authenticated blob
// endpoints; external/static paths render as-is and degrade to a placeholder.
const { resolvedUrl, failed, markFailed } = useSceneAssetUrl(sourcePath, () => props.gameKey)
const noteSize = computed(() => Math.max(12, Math.min(props.rect.width, props.rect.height) / 18))
</script>

<template>
  <g data-testid="board-background">
    <image
      v-if="resolvedUrl && !failed"
      :href="resolvedUrl"
      :x="rect.x"
      :y="rect.y"
      :width="rect.width"
      :height="rect.height"
      preserveAspectRatio="none"
      data-testid="board-background-image"
      @error="markFailed"
    />
    <g v-else>
      <rect :x="rect.x" :y="rect.y" :width="rect.width" :height="rect.height" class="board-background-empty" />
      <text
        v-if="sourcePath && failed"
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