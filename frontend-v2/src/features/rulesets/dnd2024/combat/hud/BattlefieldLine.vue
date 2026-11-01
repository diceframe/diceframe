<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import BattleAvatar from './BattleAvatar.vue'
import BattleTokenCard from './BattleToken.vue'
import {
  battleDistance, lineExtent, lineLaneCount, lineSlots,
  type BattleToken,
} from './battleView'
import type { Floater } from './feedback'
import type { BattlefieldLabels } from './labels'

const CELL = 44
const LANE_HEIGHT = 124

const props = defineProps<{
  tokens: BattleToken[]
  movementRemaining: number
  targetId: string
  selectableIds: string[]
  selectedDestination: number | null
  canReach: (x: number) => boolean
  labels: BattlefieldLabels
  floaters?: Floater[]
}>()
const emit = defineEmits<{ selectToken: [id: string]; choosePosition: [x: number] }>()

const extent = computed(() => lineExtent(props.tokens, props.movementRemaining))
const slots = computed(() => lineSlots(props.tokens, extent.value))
const cellCount = computed(() => extent.value.ticks.length)
const stageStyle = computed(() => ({ minWidth: `${cellCount.value * CELL}px` }))
const unitsStyle = computed(() => ({ height: `${lineLaneCount(slots.value) * LANE_HEIGHT + 14}px` }))
const current = computed(() => props.tokens.find(token => token.isCurrent))
const target = computed(() => props.tokens.find(token => token.id === props.targetId))

// On a narrow screen the stage scrolls; start with the acting unit in view.
const scroller = ref<HTMLElement | null>(null)
function centerOnCurrent(): void {
  const el = scroller.value
  const slot = current.value ? slots.value.get(current.value.id) : undefined
  if (!el || !slot || el.scrollWidth <= el.clientWidth) return
  const cell = el.scrollWidth / cellCount.value
  el.scrollLeft = Math.max(0, (slot.index + 0.5) * cell - el.clientWidth / 2)
}
onMounted(() => void nextTick(centerOnCurrent))
watch(() => current.value?.id, () => void nextTick(centerOnCurrent))

function unitStyle(token: BattleToken): Record<string, string> {
  const slot = slots.value.get(token.id)!
  return {
    left: `${(slot.index + 0.5) / cellCount.value * 100}%`,
    top: `${14 + slot.lane * LANE_HEIGHT}px`,
  }
}

// The range bridge links the acting unit to the picked target and says how far
// apart they are; it is the line-layout answer to the mockup's attack arc.
const bridge = computed(() => {
  if (!current.value || !target.value || current.value.id === target.value.id) return null
  const from = slots.value.get(current.value.id)!.index
  const to = slots.value.get(target.value.id)!.index
  const left = Math.min(from, to)
  return {
    style: {
      left: `${(left + 0.5) / cellCount.value * 100}%`,
      width: `${Math.abs(to - from) / cellCount.value * 100}%`,
    },
    feet: battleDistance(current.value.coord, target.value.coord),
  }
})

function floatersFor(id: string): Floater[] {
  return (props.floaters || []).filter(item => item.targetId === id)
}

function cellLabel(x: number): string {
  return `${props.labels.position} ${x} ${props.labels.feet}`
}
</script>

<template>
  <div class="battlefield-line">
    <div ref="scroller" class="stage-scroll">
      <div class="stage" :style="stageStyle">
        <div class="stage-units" :style="unitsStyle">
          <div
            v-for="token in tokens"
            :key="token.id"
            class="stage-unit"
            :style="unitStyle(token)"
            :title="`${token.name} · ${token.coord.x} ${labels.feet}`"
          >
            <BattleTokenCard
              :token="token"
              :floaters="floatersFor(token.id)"
              :targeted="token.id === targetId"
              :selectable="selectableIds.includes(token.id)"
              :hp-label="labels.hp"
              :condition-label="labels.condition"
              :select-label="labels.selectTarget(token.name)"
              @select="emit('selectToken', $event)"
            />
          </div>
        </div>
        <div class="stage-range">
          <div v-if="bridge" class="range-bridge" :style="bridge.style">
            <span>{{ bridge.feet }} {{ labels.feet }}</span>
          </div>
        </div>
        <div class="stage-floor" :style="{ gridTemplateColumns: `repeat(${cellCount}, minmax(${CELL}px, 1fr))` }">
          <button
            v-for="x in extent.ticks"
            :key="x"
            type="button"
            :class="{ reachable: canReach(x), selected: selectedDestination === x, tenth: x % 10 === 0 }"
            :disabled="!canReach(x)"
            :aria-label="cellLabel(x)"
            @click="emit('choosePosition', x)"
          >
            <BattleAvatar
              v-if="selectedDestination === x && current"
              class="move-ghost"
              :name="current.name"
              :side="current.side"
              :size="26"
            />
            <span v-else>{{ x % 10 === 0 || canReach(x) ? x : '' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.battlefield-line { position: relative; border: 1px solid rgb(90 110 125 / 55%); border-radius: 16px; overflow: hidden; background:
  radial-gradient(120% 90% at 50% 0%, rgb(60 82 104 / 38%), transparent 60%),
  repeating-linear-gradient(90deg, rgb(255 255 255 / 3%) 0 1px, transparent 1px 44px),
  linear-gradient(180deg, #131c26 0%, #0b1118 62%, #1a1510 100%); box-shadow: inset 0 0 60px rgb(0 0 0 / 55%); }
.stage-scroll { overflow-x: auto; overflow-y: hidden; scrollbar-gutter: stable; }
.stage { position: relative; display: grid; width: 100%; padding: 10px 0 0; }
.stage-units { position: relative; }
.stage-unit { position: absolute; z-index: 1; transform: translateX(-50%); transition: left .45s cubic-bezier(.22, .8, .3, 1); }
.stage-range { position: relative; height: 22px; }
.range-bridge { position: absolute; top: 9px; height: 0; border-top: 2px dashed #ff8a8a; filter: drop-shadow(0 0 4px rgb(255 90 90 / 55%)); }
.range-bridge::before, .range-bridge::after { position: absolute; top: -6px; width: 2px; height: 10px; background: #ff8a8a; content: ''; }
.range-bridge::before { left: 0; }
.range-bridge::after { right: 0; }
.range-bridge span { position: absolute; top: -10px; left: 50%; padding: 1px 9px; transform: translate(-50%, -50%); border: 1px solid #ff8a8a; border-radius: 999px; background: #3a1519; color: #ffd6d6; font-size: 11px; font-weight: 700; white-space: nowrap; }
.stage-floor { display: grid; width: 100%; }
.stage-floor button { display: grid; min-height: 38px; place-items: center; padding: 0; border: 0; border-top: 1px solid rgb(255 255 255 / 10%); border-right: 1px solid rgb(255 255 255 / 6%); border-radius: 0; background: rgb(255 255 255 / 2%); color: #6c7a85; font-size: 10px; cursor: default; }
.stage-floor button.tenth { color: #8c9aa5; }
.stage-floor button.reachable { background: linear-gradient(180deg, rgb(228 183 94 / 28%), rgb(228 183 94 / 8%)); color: #f4d48d; cursor: pointer; }
.stage-floor button.reachable:hover { background: linear-gradient(180deg, rgb(228 183 94 / 48%), rgb(228 183 94 / 16%)); }
.stage-floor button.selected { background: linear-gradient(180deg, rgb(228 183 94 / 62%), rgb(228 183 94 / 22%)); box-shadow: inset 0 2px 0 #e4b75e; }
.stage-floor button:disabled { opacity: 1; }
.move-ghost { opacity: .75; }
@media (prefers-reduced-motion: reduce) { .stage-unit { transition: none; } }
</style>
