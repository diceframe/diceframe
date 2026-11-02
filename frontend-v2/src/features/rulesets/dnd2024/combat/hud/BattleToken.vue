<script setup lang="ts">
import { computed } from 'vue'
import BattleAvatar from './BattleAvatar.vue'
import type { BattleToken } from './battleView'
import type { Floater } from './feedback'

const props = defineProps<{
  token: BattleToken
  /** Results to float above this unit; the unit shakes when it takes damage. */
  floaters?: Floater[]
  /** Clicking picks this unit as the action target. */
  selectable?: boolean
  targeted?: boolean
  hpLabel: string
  conditionLabel: (key: string) => string
  /** Spoken label for the hit area, e.g. "Select Goblin as target". */
  selectLabel?: string
}>()
const emit = defineEmits<{ select: [id: string] }>()

const shownConditions = computed(() => props.token.conditions.slice(0, 2))
const hiddenConditions = computed(() => Math.max(0, props.token.conditions.length - 2))
const struck = computed(() => (props.floaters || []).some(
  item => item.kind === 'damage' || item.kind === 'crit',
))
</script>

<template>
  <div
    :class="['battle-token', token.side, {
      current: token.isCurrent, targeted, defeated: token.isDefeated, dying: token.isDying, own: token.isOwn, selectable, struck,
    }]"
    :data-actor-id="token.id"
  >
    <span v-if="token.isCurrent" class="token-marker" aria-hidden="true" />
    <BattleAvatar :name="token.name" :side="token.side" :size="46" />
    <div class="token-plate">
      <strong class="token-name">{{ token.name }}</strong>
      <div
        class="hp-track"
        role="progressbar"
        :aria-label="`${token.name} ${hpLabel}`"
        aria-valuemin="0"
        :aria-valuemax="token.maxHp"
        :aria-valuenow="token.hp"
      ><i :style="{ width: `${token.hpRatio * 100}%` }" /></div>
      <small class="token-hp">{{ hpLabel }} {{ token.hp }}/{{ token.maxHp }}</small>
    </div>
    <div v-if="shownConditions.length" class="token-conditions">
      <em v-for="key in shownConditions" :key="key">{{ conditionLabel(key) }}</em>
      <em v-if="hiddenConditions" class="more">+{{ hiddenConditions }}</em>
    </div>
    <span
      v-for="item in floaters"
      :key="item.key"
      :class="['floater', item.kind]"
      :style="{ animationDelay: `${item.delay}s`, '--slot': item.slot }"
      aria-hidden="true"
    >{{ item.text }}</span>
    <button
      v-if="selectable"
      type="button"
      class="token-hit"
      :aria-label="selectLabel || token.name"
      :aria-pressed="targeted"
      @click="emit('select', token.id)"
    />
  </div>
</template>

<style scoped>
.battle-token { position: relative; display: grid; justify-items: center; gap: 5px; width: 88px; padding: 8px 6px 7px; border: 1px solid rgb(120 150 170 / 40%); border-radius: 14px; text-align: center; background: linear-gradient(160deg, rgb(15 26 38 / 92%), rgb(8 13 20 / 92%)); box-shadow: 0 6px 16px rgb(0 0 0 / 45%); color: #e8eef2; }
.battle-token.enemy { border-color: rgb(205 105 105 / 55%); background: linear-gradient(160deg, rgb(44 18 22 / 92%), rgb(16 9 12 / 92%)); }
.battle-token.own { border-color: rgb(120 190 240 / 80%); }
.battle-token.current { border-color: #e4b75e; box-shadow: 0 0 0 2px rgb(228 183 94 / 55%), 0 0 18px rgb(228 183 94 / 45%), 0 6px 16px rgb(0 0 0 / 45%); }
.battle-token.targeted { border-color: #ff6b6b; box-shadow: 0 0 0 2px rgb(255 107 107 / 65%), 0 0 20px rgb(255 80 80 / 50%), 0 6px 16px rgb(0 0 0 / 45%); }
.battle-token.defeated { opacity: .55; filter: grayscale(.7); }
.battle-token.dying { border-style: dashed; border-color: #ff8a65; }
.token-marker { position: absolute; top: -11px; left: 50%; width: 0; height: 0; transform: translateX(-50%); border-top: 8px solid #e4b75e; border-right: 7px solid transparent; border-left: 7px solid transparent; filter: drop-shadow(0 0 4px rgb(228 183 94 / 80%)); }
.token-plate { display: grid; width: 100%; min-width: 0; gap: 3px; }
.token-name { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.token-hp { color: #aebcc6; font-size: 10px; line-height: 1; }
.hp-track { height: 6px; overflow: hidden; border-radius: 999px; background: rgb(0 0 0 / 55%); box-shadow: inset 0 0 0 1px rgb(255 255 255 / 10%); }
.hp-track i { display: block; height: 100%; background: linear-gradient(90deg, #3f9b5f, #7fd48d); transition: width .35s ease-out; }
.battle-token.enemy .hp-track i { background: linear-gradient(90deg, #a52f35, #e0705a); }
.token-conditions { position: absolute; bottom: -9px; left: 50%; display: flex; gap: 3px; transform: translateX(-50%); }
.token-conditions em { padding: 1px 6px; border: 1px solid rgb(228 183 94 / 55%); border-radius: 999px; background: rgb(35 27 12 / 95%); color: #f4d48d; font-size: 9px; font-style: normal; white-space: nowrap; }
.token-hit { position: absolute; inset: 0; border: 0; border-radius: inherit; background: transparent; cursor: pointer; }
.token-hit:hover { background: rgb(255 255 255 / 7%); }
.token-hit:focus-visible { outline: 3px solid #e4b75e; outline-offset: 2px; }
.battle-token.struck { animation: token-shake .38s ease-out; }
.floater { position: absolute; top: calc(30px + var(--slot, 0) * 24px); left: 50%; z-index: 3; padding: 1px 8px; border-radius: 999px; background: rgb(8 13 20 / 82%); font: 800 20px/1.2 Georgia, serif; white-space: nowrap; pointer-events: none; opacity: 0; transform: translate(-50%, 0); animation: floater-rise 1.5s cubic-bezier(.2, .7, .3, 1) both; text-shadow: 0 2px 0 rgb(0 0 0 / 70%); }
.floater.damage { color: #ff6b6b; }
.floater.crit { color: #ffd166; font-size: 26px; text-shadow: 0 0 10px rgb(255 160 40 / 80%), 0 2px 0 rgb(0 0 0 / 70%); }
.floater.heal { color: #7fe29a; }
.floater.hit { color: #ffb4a8; font-size: 16px; }
.floater.miss, .floater.blocked { color: #b9c4cc; font-size: 16px; }
.floater.save { color: #8fd0ff; font-size: 16px; }
.floater.fail, .floater.status { color: #f4d48d; font-size: 16px; }
@keyframes floater-rise {
  0% { opacity: 0; transform: translate(-50%, 10px) scale(.7); }
  15% { opacity: 1; transform: translate(-50%, -6px) scale(1.15); }
  30% { transform: translate(-50%, -14px) scale(1); }
  75% { opacity: 1; }
  100% { opacity: 0; transform: translate(-50%, -48px) scale(1); }
}
@keyframes token-shake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-5px); }
  40% { transform: translateX(5px); }
  60% { transform: translateX(-3px); }
  80% { transform: translateX(2px); }
}
@media (prefers-reduced-motion: reduce) {
  .hp-track i { transition: none; }
  .battle-token.struck { animation: none; }
  .floater { animation: floater-fade 1.5s linear both; }
  @keyframes floater-fade { 0%, 80% { opacity: 1; } 100% { opacity: 0; } }
}
</style>
