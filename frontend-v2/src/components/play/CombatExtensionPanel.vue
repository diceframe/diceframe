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

const shownPools = computed<Record<string, Record<string, { current: number; maximum: number | null }>>>(() => {
  if (!ext.value?.pools) return {}
  if (props.isGm) return ext.value.pools
  const own = ext.value.pools[`player:${props.selfUid}`]
  return own ? { [`player:${props.selfUid}`]: own } : {}
})

async function runAction(actionId: string) {
  if (busy.value) return
  busy.value = true
  try {
    const target = String(targets.value[actionId] || '').trim()
    const body: Record<string, unknown> = {
      intent_id: `ce-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      action_id: actionId,
    }
    if (target && target !== 'self') body.target_ids = [target]
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
      <strong>{{ entityId }}</strong>
      <div v-for="(value, resource) in pool" :key="resource" class="combat-ext-pool">
        <NIcon :component="HeartOutline" size="12" />
        <span>{{ resource }}</span>
        <span>{{ value.current }} / {{ value.maximum ?? '∞' }}</span>
      </div>
    </div>
    <div v-for="action in ext.actions" :key="action.id" class="combat-ext-action">
      <select v-model="targets[action.id]">
        <option value="self">{{ t('combatExtSelf') }}</option>
        <option v-for="entity in ext.entities || []" :key="entity" :value="entity">{{ entity }}</option>
      </select>
      <button :disabled="busy" @click="runAction(action.id)">
        <NIcon :component="FlashOutline" size="14" /> {{ action.name }}
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
