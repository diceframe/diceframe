<script setup lang="ts">
import BattleAvatar from './BattleAvatar.vue'
import type { BattleToken } from './battleView'

export interface TargetCardLabels {
  hp: string
  ac: string
  distance: string
  feet: string
  status: string
  none: string
  sideAlly: string
  sideEnemy: string
  empty: string
  listSeparator: string
  condition: (key: string) => string
}

defineProps<{
  token: BattleToken | null
  distance: number | null
  labels: TargetCardLabels
}>()
</script>

<template>
  <aside class="target-card" aria-live="polite">
    <p v-if="!token" class="target-empty">{{ labels.empty }}</p>
    <template v-else>
      <header>
        <BattleAvatar :name="token.name" :side="token.side" :size="54" />
        <div>
          <strong>{{ token.name }}</strong>
          <small>{{ token.side === 'enemy' ? labels.sideEnemy : labels.sideAlly }}</small>
        </div>
      </header>
      <dl class="target-stats">
        <div class="wide">
          <dt>{{ labels.hp }}</dt>
          <dd>{{ token.hp }}/{{ token.maxHp }}</dd>
        </div>
        <div class="bar wide">
          <i :class="token.side" :style="{ width: `${token.hpRatio * 100}%` }" />
        </div>
        <div v-if="token.armorClass !== undefined">
          <dt>{{ labels.ac }}</dt>
          <dd>{{ token.armorClass }}</dd>
        </div>
        <div v-if="distance !== null">
          <dt>{{ labels.distance }}</dt>
          <dd>{{ distance }} {{ labels.feet }}</dd>
        </div>
        <div class="wide">
          <dt>{{ labels.status }}</dt>
          <dd>{{ token.conditions.length ? token.conditions.map(labels.condition).join(labels.listSeparator) : labels.none }}</dd>
        </div>
      </dl>
    </template>
  </aside>
</template>

<style scoped>
.target-card { display: grid; align-content: start; gap: 10px; min-width: 0; padding: 12px; border: 1px solid rgb(228 183 94 / 35%); border-radius: 14px; background: linear-gradient(180deg, rgb(18 28 40 / 96%), rgb(10 16 24 / 96%)); color: #e8eef2; }
.target-empty { margin: 0; color: #8fa0ac; font-size: 12px; line-height: 1.5; }
.target-card header { display: flex; align-items: center; gap: 10px; padding-bottom: 10px; border-bottom: 1px solid rgb(255 255 255 / 10%); }
.target-card header div { display: grid; min-width: 0; gap: 2px; }
.target-card header strong { overflow: hidden; font: 700 16px/1.2 Georgia, serif; text-overflow: ellipsis; white-space: nowrap; }
.target-card header small { color: #9aa9b4; font-size: 11px; }
.target-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 10px; margin: 0; }
.target-stats > div { display: grid; gap: 2px; min-width: 0; }
.target-stats .wide { grid-column: 1 / -1; }
.target-stats dt { color: #9aa9b4; font-size: 11px; }
.target-stats dd { margin: 0; font-size: 15px; font-weight: 700; }
.target-stats .bar { height: 7px; margin-top: -4px; overflow: hidden; border-radius: 999px; background: rgb(0 0 0 / 55%); }
.target-stats .bar i { display: block; height: 100%; background: linear-gradient(90deg, #3f9b5f, #7fd48d); }
.target-stats .bar i.enemy { background: linear-gradient(90deg, #a52f35, #e0705a); }
</style>
