<script setup lang="ts">
import BattleAvatar from './BattleAvatar.vue'
import type { BattleToken } from './battleView'

defineProps<{
  /** Tokens in initiative order. */
  order: BattleToken[]
  roundLabel: string
  turnLabel: string
  actingName: string
  listLabel: string
}>()
</script>

<template>
  <section class="turn-banner" aria-live="polite">
    <div class="turn-title">
      <small>{{ roundLabel }}</small>
      <strong>{{ turnLabel }}</strong>
      <span>{{ actingName }}</span>
    </div>
    <ol class="initiative" :aria-label="listLabel">
      <li
        v-for="token in order"
        :key="token.id"
        :class="[token.side, { current: token.isCurrent, defeated: token.isDefeated, dying: token.isDying }]"
        :aria-current="token.isCurrent ? 'step' : undefined"
      >
        <BattleAvatar :name="token.name" :side="token.side" :size="token.isCurrent ? 46 : 36" />
        <strong>{{ token.name }}</strong>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.turn-banner { display: flex; align-items: center; gap: 18px; min-height: 84px; padding: 10px 16px; border: 1px solid rgb(90 110 125 / 55%); border-radius: 14px; background: linear-gradient(180deg, rgb(18 28 40 / 96%), rgb(10 16 24 / 96%)); color: #e8eef2; }
.turn-title { display: grid; flex: 0 0 auto; gap: 2px; min-width: 0; }
.turn-title small { color: #9db7c6; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.turn-title strong { font-size: 17px; }
.turn-title span { color: #f4d48d; font: 700 14px/1.2 Georgia, serif; }
.initiative { display: flex; flex: 1; align-items: flex-end; gap: 14px; min-width: 0; margin: 0; padding: 4px 6px; overflow-x: auto; overflow-y: hidden; list-style: none; }
.initiative li { display: grid; flex: 0 0 auto; justify-items: center; gap: 3px; min-width: 56px; opacity: .78; }
/* Centred while it fits; auto margins (unlike justify-content: center) keep the
   first units reachable by scrolling once a long order overflows. */
.initiative li:first-child { margin-left: auto; }
.initiative li:last-child { margin-right: auto; }
.initiative li strong { max-width: 72px; overflow: hidden; font-size: 11px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.initiative li.current { opacity: 1; }
.initiative li.current :deep(.battle-avatar) { border-color: #e4b75e; box-shadow: 0 0 0 3px rgb(228 183 94 / 45%), 0 0 16px rgb(228 183 94 / 55%); }
.initiative li.current strong { color: #ffe5a8; }
.initiative li.defeated { opacity: .4; filter: grayscale(.8); }
.initiative li.dying { outline: 1px dashed #ff8a65; }
@media (max-width: 700px) {
  .turn-banner { flex-direction: column; align-items: stretch; gap: 8px; }
  .initiative li:first-child { margin-left: 0; }
}
</style>
