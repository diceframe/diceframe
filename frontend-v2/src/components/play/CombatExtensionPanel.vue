<script setup lang="ts">
import { computed, ref } from 'vue'
import { NIcon } from 'naive-ui'
import { FlashOutline, HeartOutline } from '@vicons/ionicons5'
import type { GameDetail } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { api } from '@/api/client'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ detail: GameDetail; gameKey: string; selfUid: string; isGm: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useLocale()
const toast = useToast()

const ext = computed(() => props.detail.combat_extension || null)
const busy = ref(false)
const targets = ref<Record<string, string>>({})
const actors = ref<Record<string, string>>({})

const ownEntity = computed(() => `player:${props.selfUid}`)
const inActionPhase = computed(() => !props.detail.state || props.detail.state === 'active_action')
const schedulerReady = computed(() => new Set(ext.value?.scheduler?.ready || []))
const schedulerInitialized = computed(() => (
  (ext.value?.scheduler?.participants?.length || 0) > 0
  || Object.keys(ext.value?.scheduler?.gauges || {}).length > 0
  || (ext.value?.scheduler?.ready?.length || 0) > 0
))
const actorOptions = computed(() => {
  const all = ext.value?.entities || []
  const participants = ext.value?.scheduler?.participants || []
  return schedulerInitialized.value && participants.length ? participants : all
})
const defaultGmActor = computed(() => {
  const entities = actorOptions.value
  return entities.includes(ownEntity.value) ? ownEntity.value : entities[0] || ownEntity.value
})
const actorEntity = computed(() => (props.isGm ? actors.value.__gm__ || defaultGmActor.value : ownEntity.value))
const actorEntityProxy = computed({
  get: () => actors.value.__gm__ || defaultGmActor.value,
  set: (value: string) => { actors.value.__gm__ = value },
})
const groupedEntities = computed(() => {
  const players: string[] = []
  const others: string[] = []
  for (const entity of ext.value?.entities || []) {
    if (entity.startsWith('player:')) players.push(entity)
    else others.push(entity)
  }
  return { players, others }
})
const schedulerEntities = computed(() => Array.from(new Set([
  ...(ext.value?.scheduler?.participants || []),
  ...Object.keys(ext.value?.scheduler?.gauges || {}),
  ...(ext.value?.scheduler?.ready || []),
])))
const selectedActorReady = computed(() => (
  !schedulerInitialized.value || schedulerReady.value.has(actorEntity.value)
))

const shownPools = computed<Record<string, Record<string, { current: number; maximum: number | null }>>>(() => {
  if (!ext.value?.pools) return {}
  if (props.isGm) return ext.value.pools
  const own = ext.value.pools[`player:${props.selfUid}`]
  return own ? { [`player:${props.selfUid}`]: own } : {}
})

function entityLabel(entityId: string): string {
  return ext.value?.entity_names?.[entityId] || entityId
}

function formatFormulaAmount(amount: unknown): string {
  if (amount == null) return '0'
  if (typeof amount === 'number' || typeof amount === 'string') return String(amount)
  if (typeof amount !== 'object') return String(amount)
  const node = amount as { op?: string; value?: unknown; formula?: unknown; id?: unknown; args?: unknown[]; raw?: unknown }
  if (node.op === 'constant') return String(node.value ?? 0)
  if (node.op === 'dice' && typeof node.formula === 'string') return node.formula
  if (node.op === 'attribute' && typeof node.id === 'string') return node.id
  if ((node.op === 'add' || node.op === 'multiply') && Array.isArray(node.args) && node.args.length === 2) {
    const left = formatFormulaAmount(node.args[0])
    const right = formatFormulaAmount(node.args[1])
    return node.op === 'add' ? `${left} + ${right}` : `${left} × ${right}`
  }
  if (typeof node.raw === 'string') return node.raw
  return JSON.stringify(node)
}

function formatCost(cost: { resource: string; amount: unknown }): string {
  return `${cost.resource} ${formatFormulaAmount(cost.amount)}`
}

function schedulerLabel(kind: string): string {
  if (kind === 'round_robin') return t('combatEditorRoundRobin')
  if (kind === 'initiative') return t('combatEditorInitiative')
  if (kind === 'threshold') return t('combatEditorThreshold')
  return kind
}

async function advanceScheduler() {
  if (busy.value || !inActionPhase.value) return
  busy.value = true
  try {
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(props.gameKey)}/combat/scheduler/advance`,
      { method: 'POST' },
    )
    if (result.error || result.ok === false) throw new Error(result.error || t('operationFailed'))
    toast.success(t('combatExtAdvanceDone'))
    emit('changed')
  } catch (e: unknown) {
    toast.error(e instanceof Error ? e.message : String(e || t('operationFailed')))
  } finally {
    busy.value = false
  }
}

async function runAction(actionId: string) {
  if (busy.value || !inActionPhase.value) return
  const target = String(targets.value[actionId] || '').trim()
  if (!target) {
    toast.error(t('combatExtSelectTarget'))
    return
  }
  busy.value = true
  try {
    const body: Record<string, unknown> = {
      intent_id: `ce-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      action_id: actionId,
    }
    if (target && target !== 'self') body.target_ids = [target]
    if (props.isGm && actorEntity.value) {
      body.actor_id = actorEntity.value
    }
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(props.gameKey)}/combat/action`,
      { method: 'POST', body: JSON.stringify(body) },
    )
    if (result.error || result.ok === false) throw new Error(result.error || t('operationFailed'))
    toast.success(t('combatExtDone'))
    emit('changed')
  } catch (e: unknown) {
    toast.error(e instanceof Error ? e.message : String(e || t('operationFailed')))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="ext" class="gm-group combat-ext panel">
    <h4><NIcon :component="FlashOutline" size="14" /> {{ t('combatExtTitle') }}</h4>
    <small v-if="!inActionPhase" class="combat-ext-phase-hint">{{ t('combatExtActionPhaseOnly') }}</small>
    <div v-if="ext.scheduler?.kind" class="combat-ext-scheduler">
      <div class="combat-ext-scheduler-head">
        <strong>{{ t('combatExtScheduler') }}</strong>
        <span>{{ schedulerLabel(ext.scheduler.kind) }}</span>
        <button v-if="isGm" type="button" :disabled="busy || !inActionPhase" @click="advanceScheduler">{{ t('combatExtAdvance') }}</button>
      </div>
      <div v-for="entity in schedulerEntities" :key="entity" class="combat-ext-scheduler-row" :class="{ ready: schedulerReady.has(entity) }">
        <strong>{{ entityLabel(entity) }}</strong>
        <span>{{ schedulerReady.has(entity) ? t('combatExtReady') : t('combatExtWaiting') }}</span>
        <small v-if="ext.scheduler.kind === 'threshold'">{{ t('combatExtGauge') }} {{ ext.scheduler.gauges?.[entity] ?? 0 }}</small>
      </div>
    </div>
    <div v-for="(pool, entityId) in shownPools" :key="entityId" class="combat-ext-pools">
      <strong>{{ entityLabel(entityId) }}</strong>
      <div v-for="(value, resource) in pool" :key="resource" class="combat-ext-pool">
        <NIcon :component="HeartOutline" size="12" />
        <span>{{ resource }}</span>
        <span>{{ value.current }} / {{ value.maximum ?? '∞' }}</span>
      </div>
    </div>
    <div v-if="isGm" class="combat-ext-action">
      <span class="cee-label">{{ t('combatExtActor') }}</span>
      <select v-model="actorEntityProxy">
        <option v-for="entity in actorOptions" :key="entity" :value="entity">{{ ext.entity_names?.[entity] || entity }}</option>
      </select>
    </div>
    <div v-for="action in ext.actions" :key="action.id" class="combat-ext-action">
      <select v-model="targets[action.id]" :disabled="busy || !inActionPhase">
        <option value="" disabled>{{ t('combatExtSelectTarget') }}</option>
        <option value="self">{{ t('combatExtSelf') }}</option>
        <optgroup v-if="groupedEntities.players.length" :label="t('combatExtPlayers')">
          <option v-for="entity in groupedEntities.players" :key="entity" :value="entity">{{ entityLabel(entity) }}</option>
        </optgroup>
        <optgroup v-if="groupedEntities.others.length" :label="t('combatExtOthers')">
          <option v-for="entity in groupedEntities.others" :key="entity" :value="entity">{{ entityLabel(entity) }}</option>
        </optgroup>
      </select>
      <button type="button" :disabled="busy || !inActionPhase || !selectedActorReady" @click="runAction(action.id)">
        <NIcon :component="FlashOutline" size="14" /> {{ action.name }}
      </button>
      <div class="combat-ext-action-meta">
        <small v-if="action.costs.length">{{ t('combatExtCosts') }}：{{ action.costs.map(formatCost).join(' · ') }}</small>
        <small v-if="action.consume_item">{{ t('combatExtConsumeItem') }}：{{ action.consume_item.item }} ×{{ action.consume_item.qty }}</small>
      </div>
    </div>
  </section>
</template>

<style scoped>
.combat-ext { display: grid; gap: 8px; }
.combat-ext h4 { display: flex; align-items: center; gap: 6px; margin: 0; }
.combat-ext-scheduler { display: grid; gap: 6px; padding: 8px; border: 1px solid var(--df-border-soft); border-radius: 8px; background: var(--df-surface-1); }
.combat-ext-scheduler-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.combat-ext-scheduler-head strong { font-size: 12px; }
.combat-ext-scheduler-head span { color: var(--df-text-muted); }
.combat-ext-scheduler-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; align-items: center; font-size: 12px; }
.combat-ext-scheduler-row.ready strong { color: var(--df-success, #2f8f61); }
.combat-ext-pools { display: grid; gap: 4px; }
.combat-ext-pools strong { font-size: 12px; color: var(--df-text-muted); }
.combat-ext-pool { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.combat-ext-action { display: grid; gap: 6px; }
.combat-ext-action > button { justify-self: start; }
.combat-ext-action-meta { display: grid; gap: 2px; padding-left: 2px; }
.combat-ext-action-meta small { color: var(--df-text-muted); font-size: 11px; }
.combat-ext-action select { flex: 1 1 auto; min-width: 0; }
.combat-ext-phase-hint { color: var(--df-warning, #9a6700); }
</style>
