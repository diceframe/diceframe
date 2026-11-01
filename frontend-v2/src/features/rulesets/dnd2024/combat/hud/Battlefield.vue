<script setup lang="ts">
import { computed } from 'vue'
import BattlefieldLine from './BattlefieldLine.vue'
import BattleTokenCard from './BattleToken.vue'
import { battlefieldLayoutFor, type BattleToken } from './battleView'
import type { Floater } from './feedback'
import type { BattlefieldLabels } from './labels'

/**
 * Picks the layout for the server's `position_mode`. Each layout takes the same
 * props, so a new axis is one new component plus one entry in battleView.ts.
 */
const props = defineProps<{
  positionMode?: string
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

const layout = computed(() => battlefieldLayoutFor(props.positionMode))
</script>

<template>
  <BattlefieldLine
    v-if="layout === 'line'"
    :tokens="tokens"
    :movement-remaining="movementRemaining"
    :target-id="targetId"
    :selectable-ids="selectableIds"
    :selected-destination="selectedDestination"
    :can-reach="canReach"
    :labels="labels"
    :floaters="floaters"
    @select-token="emit('selectToken', $event)"
    @choose-position="emit('choosePosition', $event)"
  />
  <!-- Unknown position mode: show who is present without inventing geometry. -->
  <div v-else class="battlefield-roster">
    <BattleTokenCard
      v-for="token in tokens"
      :key="token.id"
      :token="token"
      :floaters="(floaters || []).filter(item => item.targetId === token.id)"
      :targeted="token.id === targetId"
      :selectable="selectableIds.includes(token.id)"
      :hp-label="labels.hp"
      :condition-label="labels.condition"
      :select-label="labels.selectTarget(token.name)"
      @select="emit('selectToken', $event)"
    />
  </div>
</template>

<style scoped>
.battlefield-roster { display: flex; flex-wrap: wrap; gap: 14px; justify-content: center; padding: 22px 12px; border: 1px solid rgb(90 110 125 / 55%); border-radius: 16px; background: linear-gradient(180deg, #131c26, #0b1118); }
</style>
