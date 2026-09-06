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
const actorEntity = computed(() => actors.value[actionIdBeingRun.value] || ownEntity.value)
const actionIdBeingRun = ref('')
const actorEntityProxy = computed({
  get: () => actors.value.__gm__ || ownEntity.value,
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

const shownPools = computed<Record<string, Record<string, { current: number; maximum: number | null }>>>(() => {
  if (!ext.value?.pools) return {}
  if (props.isGm) return ext.value.pools
  const own = ext.value.pools[`player:${props.selfUid}`]
  return own ? { [`player:${props.selfUid}`]: own } : {}
})

async function runAction(actionId: string) {
  if (busy.value) return
  busy.value = true
  actionIdBeingRun.value = actionId
  try {
    const target = String(targets.value[actionId] || '').trim()
    const body: Record<string, unknown> = {
      intent_id: `ce-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      action_id: actionId,
    }
    if (target && target !== 'self') body.target_ids = [target]
    if (props.isGm && actorEntity.value && actorEntity.value !== ownEntity.value) {
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
  <section v-if="ext && ext.actions?.length" class="gm-group combat-ext panel">
    <h4><NIcon :component="FlashOutline" size="14" /> {{ t('combatExtTitle') }}</h4>
    <div v-for="(pool, entityId) in shownPools" :key="entityId" class="combat-ext-pools">
      <strong>{{ ext.entity_names?.[entityId] || entityId }}</strong>
      <div v-for="(value, resource) in pool" :key="resource" class="combat-ext-pool">
        <NIcon :component="HeartOutline" size="12" />
        <span>{{ resource }}</span>
        <span>{{ value.current }} / {{ value.maximum ?? '∞' }}</span>
      </div>
    </div>
    <div v-if="isGm" class="combat-ext-action">
      <span class="cee-label">{{ t('combatExtActor') }}</span>
      <select v-model="actorEntityProxy">
        <option v-for="entity in ext.entities || []" :key="entity" :value="entity">{{ ext.entity_names?.[entity] || entity }}</option>
      </select>
    </div>
    <div v-for="action in ext.actions" :key="action.id" class="combat-ext-action">
      <select v-model="targets[action.id]">
        <option value="self">{{ t('combatExtSelf') }}</option>
        <optgroup v-if="groupedEntities.players.length" :label="t('combatExtPlayers')">
          <option v-for="entity in groupedEntities.players" :key="entity" :value="entity">{{ entity }}</option>
        </optgroup>
        <optgroup v-if="groupedEntities.others.length" :label="t('combatExtOthers')">
          <option v-for="entity in groupedEntities.others" :key="entity" :value="entity">{{ entity }}</option>
        </optgroup>
      </select>
      <button :disabled="busy" @click="runAction(action.id)">
        <NIcon :component="FlashOutline" size="14" /> {{ action.name }}
        <small v-if="action.consume_item"> ({{ action.consume_item.item }} x{{ action.consume_item.qty }})</small>
      </button>
    </div>
  </section>
</template>

<style scoped>
.combat-ext-pools { display: grid; gap: 4px; }
.combat-ext-pools strong { font-size: 12px; color: var(--df-text-muted); }
.combat-ext-pool { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.combat-ext-action { display: flex; gap: 6px; align-items: center; }
.combat-ext-action select { flex: 1 1 auto; min-width: 0; }
</style>
