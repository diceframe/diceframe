<script setup lang="ts">
import { computed } from 'vue'
import type { CharacterPortrait } from '@/api/types'
import PortraitImage from '@/components/PortraitImage.vue'
import { initials } from '@/utils/portraits'
import type { BattleSide } from './battleView'

const props = withDefaults(defineProps<{
  name: string
  side: BattleSide
  size?: number
  /** Optional art. Combat actors carry none yet, so the initials tile is the default. */
  portrait?: CharacterPortrait | null
}>(), { size: 44, portrait: null })

// Stable tint per name so two allies are easy to tell apart without art.
const hue = computed(() => {
  let hash = 0
  for (const char of props.name) hash = (hash * 31 + char.codePointAt(0)!) % 360
  return props.side === 'enemy' ? 350 + (hash % 24) - 12 : 190 + (hash % 80)
})
const style = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  fontSize: `${Math.round(props.size * 0.38)}px`,
  '--avatar-hue': String(((hue.value % 360) + 360) % 360),
}))
const hasPortrait = computed(() => Boolean(props.portrait?.kind))
</script>

<template>
  <span :class="['battle-avatar', side]" :style="style" aria-hidden="true">
    <span v-if="hasPortrait" class="battle-avatar-art">
      <PortraitImage :portrait="portrait" :name="name" :size="size" />
    </span>
    <b v-else>{{ initials(name) }}</b>
  </span>
</template>

<style scoped>
.battle-avatar { position: relative; display: inline-grid; flex: 0 0 auto; place-items: center; overflow: hidden; border: 2px solid hsl(var(--avatar-hue) 55% 62%); border-radius: 50%; background: radial-gradient(circle at 32% 26%, hsl(var(--avatar-hue) 45% 38%), hsl(var(--avatar-hue) 50% 16%)); color: hsl(var(--avatar-hue) 90% 92%); box-shadow: inset 0 -6px 12px rgb(0 0 0 / 35%); user-select: none; }
.battle-avatar b { font-weight: 800; letter-spacing: .02em; text-shadow: 0 1px 2px rgb(0 0 0 / 55%); }
/* Art must not open its own lightbox while the avatar is a click target. */
.battle-avatar-art { display: contents; pointer-events: none; }
.battle-avatar-art :deep(.portrait-image) { border-radius: 50%; }
</style>
