<script setup lang="ts">
import type { Component } from 'vue'
import { NButton, NIcon, NTag } from 'naive-ui'
import { CloudDownloadOutline, Star } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { PluginMarketplaceItem } from '@/api/types'

// One marketplace entry, shared by the plugin marketplace and the online
// content packages shelf (content packs are plugins of type content-pack).
// Slots add what only one shelf needs: `summary` under the description,
// `tags` at the end of the tag row and `footer` under the actions.
defineProps<{
  item: PluginMarketplaceItem
  icon: Component
  typeLabel: string
  // Hub catalog only: download / rating / like stats and the detail button.
  showHubStats: boolean
  hasNewerVersion: boolean
  installing: boolean
}>()
const emit = defineEmits<{ install: []; 'open-url': [url: string]; detail: [] }>()

const { t } = useLocale()
</script>

<template>
  <article class="market-card">
    <div class="market-title">
      <NIcon :component="icon" :size="26" class="market-title-icon" />
      <div class="market-title-text">
        <h3>{{ item.name || item.id }}</h3>
        <p class="muted">{{ item.id }} · {{ item.version || t('unknownVersion') }}</p>
        <p v-if="item.author" class="muted market-author">{{ t('author') }}: {{ item.author }}</p>
      </div>
      <NTag v-if="item.stars" size="small" class="stars-tag" :title="t('pluginStars', { count: item.stars })">
        <template #icon><NIcon :component="Star" /></template>
        {{ item.stars }}
      </NTag>
    </div>
    <p class="market-desc" :title="item.description">{{ item.description || t('noDescription') }}</p>
    <slot name="summary" />
    <p v-if="showHubStats" class="hub-card-stats">
      <span>{{ t('hubDownloads') }} <strong>{{ item.stats?.downloads_total || 0 }}</strong></span>
      <span>{{ t('hubRating') }} <strong>{{ item.stats?.rating_average || 0 }}</strong></span>
      <span>{{ t('hubLikes') }} <strong>{{ item.stats?.likes || 0 }}</strong></span>
    </p>
    <div class="tag-row">
      <NTag v-if="item.plugin_type" size="small">{{ typeLabel }}</NTag>
      <NTag v-if="item.support?.level === 'partial'" type="warning" size="small">{{ t('pluginSupportPartial') }}</NTag>
      <NTag v-if="item.support?.level === 'reserved'" type="error" size="small">{{ t('pluginSupportReserved') }}</NTag>
      <NTag v-if="item.trust_level === 'official'" type="success" size="small">{{ t('pluginTrustOfficial') }}</NTag>
      <NTag v-else-if="item.trust_level === 'verified'" type="info" size="small">{{ t('pluginTrustVerified') }}</NTag>
      <NTag v-else size="small">{{ t('pluginTrustCommunity') }}</NTag>
      <NTag v-if="item.distribution === 'bundled'" type="success" size="small">{{ t('pluginBundled') }}</NTag>
      <NTag v-else-if="item.risk_level === 'declarative'" type="success" size="small">{{ t('pluginRiskDeclarative') }}</NTag>
      <NTag v-else-if="item.risk_level === 'unrestricted-process'" type="error" size="small">{{ t('pluginRiskProcess') }}</NTag>
      <NTag v-if="item.commit_sha" type="info" size="small">{{ t('pluginSourcePinned') }}</NTag>
      <NTag v-if="item.update_policy === 'approval-required'" type="error" size="small">{{ t('pluginUpdateApprovalRequired') }}</NTag>
      <NTag v-if="item.installed" type="success" size="small">{{ t('installedVersion', { version: item.installed_version || '' }) }}</NTag>
      <NTag v-if="item.installed && hasNewerVersion" type="warning" size="small">{{ t('newVersionAvailable', { version: item.latest?.version || item.version || '' }) }}</NTag>
      <NTag v-for="tag in item.tags || []" :key="tag" size="small">{{ tag }}</NTag>
      <slot name="tags" />
    </div>
    <p v-if="item.permissions?.length" class="muted market-permissions">
      {{ t('permissions') }}: {{ item.permissions.slice(0, 4).join(t('listSeparator')) }}{{ item.permissions.length > 4 ? t('andMore') : '' }}
    </p>
    <p v-if="item.support?.summary" class="muted market-permissions">{{ item.support.summary }}</p>
    <p v-if="item.verification_error" class="market-warning">{{ item.verification_error }}</p>
    <p v-else-if="item.needs_core_update" class="market-warning">{{ t('pluginNeedsCoreUpdate', { version: item.min_app_version || '' }) }}</p>
    <div class="market-actions">
      <NButton v-if="item.installed && !hasNewerVersion" secondary disabled>{{ t('installed') }}</NButton>
      <NButton v-else type="primary" :disabled="item.installable === false" :loading="installing" @click="emit('install')">
        <template #icon><NIcon :component="CloudDownloadOutline" /></template>
        {{ item.installed ? t('update') : t('install') }}
      </NButton>
      <NButton secondary :disabled="!item.repository_url && !item.homepage" @click="emit('open-url', item.repository_url || item.homepage || '')">
        {{ t('openRepository') }}
      </NButton>
      <NButton v-if="showHubStats" secondary @click="emit('detail')">
        {{ t('hubDataAndReviews') }}
      </NButton>
    </div>
    <slot name="footer" />
  </article>
</template>

<style scoped>
.market-card {
  position: relative;
  display: flex;
  box-sizing: border-box;
  min-width: 0;
  min-height: 250px;
  overflow: hidden;
  flex-direction: column;
  height: 100%;
  padding: 15px;
  border: 1px solid color-mix(in srgb, var(--df-accent) 24%, var(--df-border-soft));
  border-radius: 8px;
  /* The gradient sits on the theme surface so the card keeps the semantic surface colour. */
  background: radial-gradient(circle at 88% 5%, color-mix(in srgb, var(--df-interactive) 11%, transparent), transparent 26%),
    linear-gradient(145deg, var(--df-surface-2), var(--df-surface-1));
  background-color: var(--df-surface-1);
}

.market-card h3 {
  margin: 0;
}

.market-card::after {
  position: absolute;
  right: -35px;
  bottom: -48px;
  width: 115px;
  height: 115px;
  border: 1px solid var(--df-border-soft);
  border-radius: 50%;
  content: "";
  opacity: .22;
}

.market-title-icon {
  box-sizing: content-box;
  flex: 0 0 auto;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--df-interactive) 30%, var(--df-border-soft));
  border-radius: 8px;
  color: var(--df-interactive-strong);
  background: color-mix(in srgb, var(--df-interactive) 8%, var(--df-control-bg));
}

.market-title {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr) auto;
  column-gap: 12px;
  align-items: start;
}

.market-title-text {
  min-width: 0;
}

.market-title p, .market-desc {
  margin: 5px 0 0;
}

.market-author {
  margin-top: 2px;
}

.market-desc {
  min-height: 3.2em;
  max-height: 4.8em;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  color: var(--df-text);
  line-height: 1.6;
}

.hub-card-stats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 16px;
  margin: 9px 0 11px;
  color: var(--df-text-muted);
  font-size: 12px;
}

.hub-card-stats span {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
}

.hub-card-stats strong {
  color: var(--df-text-secondary);
  font-variant-numeric: tabular-nums;
}

.tag-row, .market-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tag-row {
  flex-wrap: wrap;
  margin: 12px 0 6px;
}

.market-permissions {
  min-height: 1.5em;
  margin: -4px 0 10px;
}

.market-warning {
  margin: -4px 0 10px;
  color: var(--df-danger-strong);
}

.market-actions {
  position: relative;
  z-index: 1;
  flex-wrap: nowrap;
  margin-top: auto;
}

.market-actions .n-button {
  flex: 1 1 0;
  min-width: 0;
  padding-inline: 8px;
  white-space: nowrap;
}

.stars-tag .n-icon {
  color: var(--df-accent-strong);
}

@media (max-width: 800px) {
  .market-card {
    min-height: 226px;
  }
}

@media (max-width: 560px) {
  .market-title {
    grid-template-columns: 44px minmax(0, 1fr);
  }
  .market-title .stars-tag {
    grid-column: 2;
    justify-self: start;
  }
}
</style>
