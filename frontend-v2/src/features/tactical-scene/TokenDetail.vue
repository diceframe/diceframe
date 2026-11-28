<script setup lang="ts">
import { computed } from 'vue'
import type { TacticalSceneTokenDocument, TacticalSceneTokenProjection } from '@/api/tacticalScene'
import { useLocale } from '@/composables/useLocale'
import { tacticalTokenCopy } from './copy'
import {
  dispositionCopyKey,
  isGmTokenDocument,
  ownershipCopyKey,
  projectTokenBars,
  tokenActorStatus,
  tokenDisplayName,
} from './tokenDisplay'

const props = defineProps<{
  token: TacticalSceneTokenProjection | TacticalSceneTokenDocument
  isGm: boolean
  ownership?: Record<string, number> | null
}>()

const emit = defineEmits<{
  bind: []
  close: []
}>()

const { locale } = useLocale()
const copy = computed(() => tacticalTokenCopy(locale.value))

const gmToken = computed(() => (isGmTokenDocument(props.token) ? props.token : null))
const name = computed(() => tokenDisplayName(props.token))
const actorStatus = computed(() => tokenActorStatus(props.token.actor_ref))
const bars = computed(() => projectTokenBars(props.token.bars))
const dispositionLabel = computed(() => copy.value[dispositionCopyKey(props.token.disposition) as keyof typeof copy.value])

const actorKindLabel = computed(() => {
  const kind = props.token.actor_ref?.kind || ''
  if (kind === 'player') return copy.value.actorKindPlayer
  if (kind === 'enemy') return copy.value.actorKindEnemy
  if (kind === 'companion') return copy.value.actorKindCompanion
  if (kind === 'actor') return copy.value.actorKindActor
  return kind
})

const ownershipRows = computed(() => {
  if (!props.isGm || !props.ownership) return []
  return Object.entries(props.ownership).map(([uid, level]) => ({
    uid,
    label: uid === 'default' ? copy.value.ownershipDefault : uid,
    level: copy.value[ownershipCopyKey(level) as keyof typeof copy.value],
  }))
})

function barPercent(bar: { value: number | null; max: number | null }): number {
  if (bar.value === null || bar.max === null || bar.max <= 0) return 0
  return Math.min(100, Math.max(0, (bar.value / bar.max) * 100))
}
</script>

<template>
  <section class="tactical-token-detail" data-testid="token-detail" role="region" :aria-label="copy.detailTitle">
    <header class="token-detail-header">
      <h3>{{ copy.detailTitle }}</h3>
      <button type="button" class="token-detail-close" :aria-label="copy.close" data-testid="token-detail-close" @click="emit('close')">×</button>
    </header>

    <dl class="token-detail-fields">
      <div class="token-detail-row">
        <dt>{{ copy.tokenName }}</dt>
        <dd data-testid="token-detail-name">{{ name }}</dd>
      </div>
      <div class="token-detail-row">
        <dt>{{ copy.canonicalId }}</dt>
        <dd><code>{{ token.canonical_id }}</code></dd>
      </div>
      <div class="token-detail-row">
        <dt>{{ copy.disposition }}</dt>
        <dd data-testid="token-detail-disposition">{{ dispositionLabel }}</dd>
      </div>
      <div class="token-detail-row">
        <dt>{{ copy.position }}</dt>
        <dd>{{ Math.round(token.position.x) }}, {{ Math.round(token.position.y) }}</dd>
      </div>
      <div class="token-detail-row">
        <dt>{{ copy.size }}</dt>
        <dd>{{ token.size.width }} × {{ token.size.height }}</dd>
      </div>
      <div class="token-detail-row">
        <dt>{{ copy.rotation }}</dt>
        <dd>{{ token.rotation }}</dd>
      </div>
    </dl>

    <div class="token-detail-badges">
      <span v-if="gmToken?.hidden" class="editor-badge" data-testid="token-detail-hidden">{{ copy.hidden }}</span>
      <span v-if="token.locked" class="editor-badge" data-testid="token-detail-locked">{{ copy.locked }}</span>
    </div>

    <section class="token-detail-actor" data-testid="token-detail-actor">
      <h4>{{ copy.actor }}</h4>
      <p v-if="actorStatus === 'unbound'" class="editor-muted" data-testid="token-actor-unbound">{{ copy.actorUnbound }}</p>
      <template v-else-if="actorStatus === 'not_imported'">
        <p class="token-detail-warning" data-testid="token-actor-not-imported">{{ copy.actorNotImported }}</p>
        <p class="editor-muted">{{ copy.actorNotImportedHint }}</p>
      </template>
      <p v-else-if="actorStatus === 'bound'" data-testid="token-actor-bound">
        {{ copy.actorBound }} <strong>{{ actorKindLabel }}</strong> · <code>{{ token.actor_ref?.id }}</code>
      </p>
      <p v-else class="editor-muted" data-testid="token-actor-unknown">
        {{ copy.actorUnknown }} · <code>{{ token.actor_ref?.kind }}:{{ token.actor_ref?.id }}</code>
      </p>
    </section>

    <section class="token-detail-bars" data-testid="token-detail-bars">
      <h4>{{ copy.bars }}</h4>
      <p v-if="!bars.length" class="editor-muted" data-testid="token-bars-empty">{{ copy.noBars }}</p>
      <ul v-else class="token-bar-list">
        <li v-for="bar in bars" :key="bar.key" class="token-bar" data-testid="token-bar">
          <div class="token-bar-head">
            <span class="token-bar-key">{{ bar.key }}</span>
            <span v-if="bar.attribute" class="token-bar-attribute">{{ copy.barAttribute }}: <code>{{ bar.attribute }}</code></span>
          </div>
          <div v-if="bar.hasValues" class="token-bar-track" role="img" :aria-label="`${bar.key}: ${bar.value} / ${bar.max}`">
            <div class="token-bar-fill" :style="{ width: `${barPercent(bar)}%` }" />
            <span class="token-bar-value">{{ bar.value }} / {{ bar.max }}</span>
          </div>
          <p v-else class="editor-muted" data-testid="token-bar-no-value">{{ copy.barNoValue }}</p>
        </li>
      </ul>
    </section>

    <section v-if="ownershipRows.length" class="token-detail-ownership" data-testid="token-detail-ownership">
      <h4>{{ copy.ownership }}</h4>
      <ul class="token-ownership-list">
        <li v-for="row in ownershipRows" :key="row.uid">
          <span>{{ row.label }}</span>
          <span class="editor-badge">{{ row.level }}</span>
        </li>
      </ul>
    </section>

    <button
      v-if="isGm"
      type="button"
      class="token-detail-bind"
      data-testid="token-detail-bind"
      @click="emit('bind')"
    >{{ copy.bind }}</button>
  </section>
</template>