<script setup lang="ts">
import { NIcon } from 'naive-ui'
import { FootstepsOutline } from '@vicons/ionicons5'
import type { GridCombat } from './useGridCombat'

/**
 * The move dock for both spatial modes. Grid shows the staged target cell and
 * its Chebyshev cost (workplan §6.14); theater keeps the single-axis distance
 * input. Submitting only emits an intent — the server validates legality.
 */
defineProps<{
  grid: GridCombat
  remaining: number
  distance: number
  canStage: boolean
  labels: { move: string; moveHint: string; feet: string; none: string }
}>()
const emit = defineEmits<{ stage: []; 'update:distance': [value: number] }>()

function onDistanceInput(event: Event): void {
  emit('update:distance', Number((event.target as HTMLInputElement).value))
}
</script>

<template>
  <div class="mode-panel move-panel" data-mode="move">
    <template v-if="grid.isGrid">
      <p class="dock-hint">{{ grid.moveHint }}</p>
      <div class="move-row">
        <span class="move-cell" data-testid="grid-move-cell">
          {{ grid.targetCellLabel }}:
          <b v-if="grid.selectedCell">{{ grid.cellLabel(grid.selectedCell) }}</b>
          <b v-else>{{ labels.none }}</b>
        </span>
        <button type="button" class="move-submit" :disabled="!canStage" @click="emit('stage')">
          <NIcon :component="FootstepsOutline" />{{ labels.move }}
        </button>
      </div>
      <small class="move-range">{{ grid.moveRangeText }}</small>
    </template>
    <template v-else>
      <p class="dock-hint">{{ labels.moveHint }}</p>
      <div class="move-row">
        <label>{{ labels.move }}
          <input :value="distance" type="number" :min="-remaining" :max="remaining" step="5" @input="onDistanceInput">
        </label>
        <button type="button" class="move-submit" :disabled="!canStage" @click="emit('stage')">
          <NIcon :component="FootstepsOutline" />{{ labels.move }}
        </button>
      </div>
      <small class="move-range">± {{ remaining }} {{ labels.feet }}</small>
    </template>
  </div>
</template>

<style scoped>
.move-panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; align-items: end; padding: 10px; border: 1px solid rgb(120 150 175 / 28%); border-radius: 12px; background: rgb(9 15 22 / 70%); }
.move-panel > .dock-hint { grid-column: 1 / -1; }
.dock-hint { margin: 0; color: #9aa9b4; font-size: 12px; }
.move-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: end; }
.move-row label { display: grid; gap: 4px; margin: 0; min-width: 0; color: #aebcc6; font-size: 12px; }
.move-row input { min-height: 38px; }
.move-cell { display: flex; align-items: center; gap: 6px; min-height: 38px; color: #c8c2b8; font-size: 13px; }
.move-cell b { color: #f4d48d; }
.move-range { color: #9aa9b4; font-size: 11px; }
.move-submit { display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 38px; min-height: 38px; margin: 0; padding: 0 16px; border: 1px solid #e4b75e; border-radius: 10px; background: linear-gradient(180deg, #b87d27, #7a4f16); color: #fff6df; font-size: 13px; font-weight: 600; cursor: pointer; }
.move-submit:disabled { border-color: #5a5648; background: #2a2820; color: #8a8576; cursor: not-allowed; }
</style>