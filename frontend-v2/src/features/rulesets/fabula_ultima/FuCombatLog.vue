<script setup lang="ts">
// Fabula Ultima 冲突的逐步战斗记录，挂在通用时间线的 ruleset-log 插槽。
import { computed } from 'vue'
import type { GameDetail } from '@/api/types'
import { useFuCopy, type FuCopyKey } from './copy'
import type { CombatHistoryEntry } from './types'

const props = defineProps<{ detail: GameDetail }>()
const { t } = useFuCopy()
const combatHistory = computed<CombatHistoryEntry[]>(() => {
  const view = (props.detail as unknown as { fabula_ultima?: { conflict?: { history?: unknown[] } } }).fabula_ultima
  const rows = view?.conflict?.history
  return Array.isArray(rows) ? rows as CombatHistoryEntry[] : []
})
const visibleCombatHistory = computed(() => combatHistory.value.slice(-80))
// 服务端落库的判定结论是 canonical 值（不随语言变化）；这里只负责显示。
const VERDICT_KEYS: Record<string, FuCopyKey> = {
  命中: 'verdictHit', 未命中: 'verdictMiss', 被干涉: 'verdictIntercepted',
  成功: 'verdictSuccess', 失败: 'verdictFailure', 大成功: 'verdictCritical', 大失败: 'verdictFumble',
}
function verdictLabel(verdict: string): string {
  const key = VERDICT_KEYS[verdict]
  return key ? t(key) : verdict
}
function combatEffectText(effect: { type?: string; target?: string; amount?: number; status?: string; stat?: string; value?: number; affinity?: string; multiplier?: number }): string {
  const target = effect.target || ''
  const affinity = effect.affinity
    ? t('affinityNote', { affinity: effect.affinity, multiplier: effect.multiplier === 2 ? '×2' : effect.multiplier === 0.5 ? '÷2' : '' })
    : ''
  if (effect.type === 'damage') return `${target} -${effect.amount || 0}${affinity}`
  if (effect.type === 'heal') return `${target} +${effect.amount || 0}`
  if (effect.type === 'status') return `${target} ${effect.status || ''}`
  if (effect.type === 'buff') return `${target} ${effect.stat || ''} +${effect.value || 0}`
  if (effect.type === 'cleanse') return t('effectCleanse', { target })
  if (effect.type === 'reveal') return t('effectReveal', { target, detail: effect.status || '' })
  return `${target} ${effect.type || ''}`
}
</script>

<template>
  <div v-if="visibleCombatHistory.length" class="combat-history" data-testid="combat-history">
    <div class="combat-history-title">{{ t('combatLog') }}</div>
    <div v-for="entry in visibleCombatHistory" :key="`combat-${entry.seq}-${entry.round}`" class="combat-history-card">
      <div class="combat-history-head">
        <strong>{{ entry.actor_name || '—' }}</strong>
        <span>{{ entry.skill || entry.action || entry.item || '' }}</span>
        <span v-if="entry.targets?.length" class="combat-history-target">→ {{ entry.targets.join(t('listSeparator')) }}</span>
        <small v-if="entry.step">{{ t('stepNumber', { step: entry.step }) }}</small>
        <small v-else-if="entry.round">{{ t('roundNumber', { round: entry.round }) }}</small>
      </div>
      <div v-if="entry.targets?.length" class="combat-history-targets">
        {{ t('targetsLine', { targets: entry.targets.join(t('listSeparator')) }) }}
      </div>
      <div v-if="entry.rolls?.length" class="combat-history-dice">
        {{ t('rollLine', { rolls: entry.rolls.join('+'), total: entry.total }) }}<template v-if="entry.dc != null"> vs {{ entry.dc }}</template>
        <template v-if="entry.verdict"> → {{ verdictLabel(entry.verdict) }}</template>
      </div>
      <div v-if="entry.text" class="combat-history-text">{{ entry.text }}</div>
      <div v-if="entry.effects?.length" class="combat-history-effects">
        <span v-for="(effect, index) in entry.effects" :key="index">{{ combatEffectText(effect) }}</span>
      </div>
      <div v-if="entry.note" class="combat-history-note">{{ entry.note }}</div>
    </div>
  </div>
</template>

<style scoped>
.combat-history { display: grid; gap: 8px; margin: 12px 0 4px; }
.combat-history-title { color: var(--df-text-muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.combat-history-card { border: 1px solid var(--df-border-soft); border-radius: 8px; background: color-mix(in srgb, var(--df-surface-2) 70%, transparent); padding: 8px 10px; display: grid; gap: 4px; }
.combat-history-head { display: flex; align-items: baseline; gap: 8px; }
.combat-history-head strong { color: var(--df-text); font-size: 13px; }
.combat-history-head span { color: var(--df-accent-strong); font-size: 12px; }
.combat-history-head small { margin-left: auto; color: var(--df-text-muted); font-size: 11px; }
.combat-history-dice { color: var(--df-text); font-family: var(--df-font-mono, monospace); font-size: 12px; }
.combat-history-targets { color: var(--df-text-muted); font-size: 12px; }
.combat-history-target { color: var(--df-accent-strong); font-size: 12px; font-weight: 600; }
.combat-history-text { color: var(--df-text-muted); font-size: 12px; }
.combat-history-effects { display: flex; flex-wrap: wrap; gap: 8px; color: var(--df-text); font-size: 12px; }
.combat-history-note { color: var(--df-text-muted); font-size: 11px; font-style: italic; }
</style>
