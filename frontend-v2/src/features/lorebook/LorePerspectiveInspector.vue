<script setup lang="ts">
import type { LoreActivationPreviewResponse, LoreEntry, LorePreviewResponse, LoreProjection, Player } from '@/api/types'
import type { MessageKey } from '@/i18n'
import { useLocale } from '@/composables/useLocale'
import LoreVisibilityBadge from './LoreVisibilityBadge.vue'

defineProps<{
  players: Player[]
  viewer: string
  viewerFallback: boolean
  characterViewerLocked: boolean
  lockedReason: 'standalone' | 'peer' | ''
  preview: LorePreviewResponse | null
  previewError: string
  selectedEntry: LoreEntry | null
  selectedProjection: LoreProjection | null
  filter: 'all' | 'visible' | 'hidden'
  actionText: string
  activation: LoreActivationPreviewResponse | null
  activationLoading: boolean
  activationError: string
}>()

const emit = defineEmits<{
  (e: 'select-viewer', viewer: string): void
  (e: 'select-filter', filter: 'all' | 'visible' | 'hidden'): void
  (e: 'close'): void
  (e: 'update:action-text', value: string): void
  (e: 'refresh-activation'): void
}>()
const { t } = useLocale()

function playerLabel(p: Player): string {
  return String(p.character_name || p.user_id)
}

// trace 的 final_state / reason_code 保持底层原值（title 里可见），展示层给用户可读的话术。
const TRACE_STATE_KEYS: Record<string, 'loreTraceIncluded' | 'loreTraceOmitted'> = {
  included: 'loreTraceIncluded',
  omitted: 'loreTraceOmitted',
}
const TRACE_REASON_KEYS: Record<string, MessageKey> = {
  keyword: 'loreReasonKeyword',
  not_a_candidate: 'loreReasonNotCandidate',
  constant: 'loreReasonConstant',
  recursive: 'loreReasonRecursive',
  budget: 'loreReasonBudget',
  hidden: 'loreReasonHidden',
  disabled: 'loreReasonDisabled',
  matched: 'loreReasonMatched',
  not_matched: 'loreReasonNotMatched',
  probability_rejected: 'loreReasonProbability',
}
function traceStateLabel(state: string): string {
  const key = TRACE_STATE_KEYS[state]
  return key ? t(key) : state
}
function traceReasonLabel(reason: string): string {
  if (!reason) return '—'
  const key = TRACE_REASON_KEYS[reason]
  return key ? t(key) : reason
}
function traceRowText(row: { final_state?: unknown; reason_code?: unknown }): string {
  const state = String(row.final_state || 'candidate')
  const reason = String(row.reason_code || '')
  return `${traceStateLabel(state)} · ${traceReasonLabel(reason)}`
}
function traceRowRaw(row: { final_state?: unknown; reason_code?: unknown }): string {
  return `${row.final_state || 'candidate'} · ${row.reason_code || '—'}`
}
</script>

<template>
  <aside class="lore-perspective-inspector">
    <header class="lore-inspector-head">
      <h2>{{ t('lorePerspectiveTitle') }}</h2>
      <button class="lore-inspector-close" @click="emit('close')" :aria-label="t('close')">×</button>
    </header>

    <section class="lore-inspector-block">
      <span class="lore-inspector-label">{{ t('loreActivationInspectorTitle') }}</span>
      <textarea
        class="lore-activation-input"
        :value="actionText"
        rows="3"
        :placeholder="t('loreActivationInputPlaceholder')"
        @input="emit('update:action-text', ($event.target as HTMLTextAreaElement).value)"
      />
      <button type="button" :disabled="activationLoading" @click="emit('refresh-activation')">
        {{ activationLoading ? t('loreActivationRefreshing') : t('loreActivationPreview') }}
      </button>
      <p v-if="activationError" class="error-banner">{{ activationError }}</p>
      <ul v-else-if="activation?.trace?.length" class="lore-activation-trace">
        <li v-for="(row, index) in activation.trace.filter(item => viewer === 'gm' || Boolean(item.entry_id))" :key="`${row.entry_id || 'safe'}-${index}`">
          <code>{{ row.entry_id || 'hidden' }}</code>
          <span :title="traceRowRaw(row)">{{ traceRowText(row) }}</span>
        </li>
      </ul>
      <p v-else class="muted small">{{ t('loreActivationTraceEmpty') }}</p>
    </section>

    <section class="lore-inspector-block">
      <span class="lore-inspector-label">{{ t('loreViewerLabel') }}</span>
      <div class="lore-viewer-options">
        <button :class="{ active: viewer === 'gm' }" @click="emit('select-viewer', 'gm')">{{ t('loreViewerGm') }}</button>
        <button :class="{ active: viewer === 'party' }" @click="emit('select-viewer', 'party')">{{ t('loreViewerParty') }}</button>
        <button
          v-for="p in players"
          :key="p.user_id"
          :class="{ active: viewer === p.user_id }"
          :disabled="characterViewerLocked"
          @click="emit('select-viewer', p.user_id)"
        >{{ playerLabel(p) }}</button>
      </div>
      <p v-if="characterViewerLocked" class="muted small">{{ t(lockedReason === 'peer' ? 'loreViewerLockedPeer' : 'loreViewerLockedStandalone') }}</p>
      <p v-else-if="viewerFallback" class="muted small">{{ t('loreViewerFallbackHint') }}</p>
    </section>

    <section class="lore-inspector-block">
      <span class="lore-inspector-label">{{ t('loreFilterLabel') }}</span>
      <div class="lore-filter-options" role="group" :aria-label="t('loreFilterLabel')">
        <button :class="{ active: filter === 'all' }" @click="emit('select-filter', 'all')">{{ t('loreFilterAll') }}</button>
        <button :class="{ active: filter === 'visible' }" @click="emit('select-filter', 'visible')">{{ t('loreSummaryVisible') }}</button>
        <button :class="{ active: filter === 'hidden' }" @click="emit('select-filter', 'hidden')">{{ t('loreFilterHidden') }}</button>
      </div>
    </section>

    <section class="lore-inspector-block">
      <span class="lore-inspector-label">{{ t('loreVisibilitySummary') }}</span>
      <p v-if="previewError" class="error-banner">{{ previewError }}</p>
      <ul v-else-if="preview?.summary" class="lore-summary-list">
        <li><span>{{ t('loreSummaryVisible') }}</span><strong>{{ preview.summary.visible }} / {{ preview.summary.total }}</strong></li>
        <li><span>{{ t('loreAudiencePublic') }}</span><strong>{{ preview.summary.public }}</strong></li>
        <li><span>{{ t('loreAudienceCharacterOnlyShort') }}</span><strong>{{ preview.summary.character_only }}</strong></li>
        <li><span>{{ t('loreAudienceGmSecret') }}</span><strong>{{ preview.summary.gm_secret }}</strong></li>
      </ul>
      <p v-else class="muted small">{{ t('loreProjectionPending') }}</p>
    </section>

    <section class="lore-inspector-block">
      <span class="lore-inspector-label">{{ t('loreSelectedEntry') }}</span>
      <template v-if="selectedEntry">
        <strong class="lore-selected-name">{{ selectedEntry.name || t('unnamedLoreEntry') }}</strong>
        <template v-if="selectedProjection">
          <LoreVisibilityBadge :projection="selectedProjection" />
          <p class="muted small">{{ selectedProjection.visible ? t('loreVisibleInCurrentViewer') : t('loreHiddenInCurrentViewer') }}</p>
        </template>
        <p v-else class="muted small">{{ t('loreProjectionPending') }}</p>
      </template>
      <p v-else class="muted small">{{ t('loreSelectedEntryHint') }}</p>
    </section>
  </aside>
</template>
