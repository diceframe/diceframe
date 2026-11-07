<script setup lang="ts">
import { NButton, NIcon, NInput, NPagination, NSelect, NSpin, NTag } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { CloudDownloadOutline, RefreshOutline, Star } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { PluginMarketplaceItem } from '@/api/types'

defineProps<{
  marketKeyword: string
  sortMode: string
  marketLoading: boolean
  marketplaceSource: { mirror_name?: string; elapsed_ms?: number; hub?: boolean; stale?: boolean } | null | undefined
  filteredMarketplace: PluginMarketplaceItem[]
  paginatedMarketplace: PluginMarketplaceItem[]
  totalPages: number
  page: number
  typeFilter: string
  pluginTypeFilters: { value: string; labelKey: string }[]
  sortOptions: { label: string; value: string }[]
  busy: string
  pluginTypeIcon: (type?: string) => import('vue').Component
  pluginTypeLabel: (type?: string) => string
  marketItemHasNewerVersion: (item: PluginMarketplaceItem) => boolean
  loadMarketplace: () => Promise<void> | void
  installMarketPlugin: (item: PluginMarketplaceItem) => Promise<void> | void
  openUrl: (url?: string) => void
  openHubDetail: (item: PluginMarketplaceItem) => Promise<void> | void
  goToPage: (next: number) => void
}>()
const emit = defineEmits<{
  'update:marketKeyword': [value: string]
  'update:sortMode': [value: string]
  'update:typeFilter': [value: string]
}>()

const { t } = useLocale()
</script>

<template>
  <section class="toolbar-row">
    <NInput :value="marketKeyword" :placeholder="t('pluginSearchPlaceholder')" clearable @update:value="(v) => emit('update:marketKeyword', String(v))" />
    <NSelect :value="sortMode" class="market-sort-select" :options="sortOptions" :placeholder="t('pluginSort')" @update:value="(v) => emit('update:sortMode', String(v || ''))" />
    <NButton :loading="marketLoading" @click="loadMarketplace">
      <template #icon><NIcon :component="RefreshOutline" /></template>
      {{ t('refresh') }}
    </NButton>
  </section>
  <p class="muted content-store-moved" data-testid="plugin-market-content-hint">
    {{ t('contentStoreMovedHint') }}
    <RouterLink :to="{ name: 'modules' }">{{ t('contentStoreMovedAction') }} <span aria-hidden="true">→</span></RouterLink>
  </p>
  <div class="type-filter-row">
    <NButton size="tiny" :type="typeFilter === '' ? 'primary' : 'default'" @click="emit('update:typeFilter', '')">{{ t('pluginFilterAll') }}</NButton>
    <NButton v-for="opt in pluginTypeFilters" :key="opt.value" size="tiny" :type="typeFilter === opt.value ? 'primary' : 'default'" @click="emit('update:typeFilter', opt.value)">{{ t(opt.labelKey as never) }}</NButton>
  </div>
  <p v-if="marketplaceSource?.mirror_name" class="muted source-line">
    {{ t('source') }}: {{ marketplaceSource.mirror_name }}, {{ marketplaceSource.elapsed_ms || 0 }} ms
    <NTag v-if="marketplaceSource.stale" size="small" type="warning">{{ t('hubCachedCatalog') }}</NTag>
  </p>
  <NSpin :show="marketLoading">
    <div class="market-grid">
      <article v-for="item in paginatedMarketplace" :key="item.id" class="market-card">
        <div class="market-title">
          <NIcon :component="pluginTypeIcon(item.plugin_type)" :size="26" class="market-title-icon" />
          <div class="market-title-text">
            <h3>{{ item.name }}</h3>
            <p class="muted">{{ item.id }} · {{ item.version || t('unknownVersion') }}</p>
            <p v-if="item.author" class="muted market-author">{{ t('author') }}: {{ item.author }}</p>
          </div>
          <NTag v-if="item.stars" size="small" class="stars-tag" :title="t('pluginStars', { count: item.stars })">
            <template #icon><NIcon :component="Star" /></template>
            {{ item.stars }}
          </NTag>
        </div>
        <p class="market-desc" :title="item.description">{{ item.description || t('noDescription') }}</p>
        <p v-if="marketplaceSource?.hub" class="hub-card-stats">
          <span>{{ t('hubDownloads') }} <strong>{{ item.stats?.downloads_total || 0 }}</strong></span>
          <span>{{ t('hubRating') }} <strong>{{ item.stats?.rating_average || 0 }}</strong></span>
          <span>{{ t('hubLikes') }} <strong>{{ item.stats?.likes || 0 }}</strong></span>
        </p>
        <div class="tag-row">
          <NTag v-if="item.plugin_type" size="small">{{ pluginTypeLabel(item.plugin_type) }}</NTag>
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
          <NTag v-if="item.installed && marketItemHasNewerVersion(item)" type="warning" size="small">{{ t('newVersionAvailable', { version: item.latest?.version || item.version || '' }) }}</NTag>
          <NTag v-for="tag in item.tags || []" :key="tag" size="small">{{ tag }}</NTag>
        </div>
        <p v-if="item.permissions?.length" class="muted market-permissions">
          {{ t('permissions') }}: {{ item.permissions.slice(0, 4).join(t('listSeparator')) }}{{ item.permissions.length > 4 ? t('andMore') : '' }}
        </p>
        <p v-if="item.support?.summary" class="muted market-permissions">{{ item.support.summary }}</p>
        <p v-if="item.verification_error" class="market-warning">{{ item.verification_error }}</p>
        <p v-else-if="item.needs_core_update" class="market-warning">{{ t('pluginNeedsCoreUpdate', { version: item.min_app_version || '' }) }}</p>
        <div class="market-actions">
          <NButton v-if="item.installed && !marketItemHasNewerVersion(item)" secondary disabled>{{ t('installed') }}</NButton>
          <NButton v-else type="primary" :disabled="item.installable === false" :loading="busy === `market:${item.id}`" @click="installMarketPlugin(item)">
            <template #icon><NIcon :component="CloudDownloadOutline" /></template>
            {{ item.installed ? t('update') : t('install') }}
          </NButton>
          <NButton secondary :disabled="!item.repository_url && !item.homepage" @click="openUrl(item.repository_url || item.homepage)">
            {{ t('openRepository') }}
          </NButton>
          <NButton v-if="marketplaceSource?.hub" secondary @click="openHubDetail(item)">
            {{ t('hubDataAndReviews') }}
          </NButton>
        </div>
      </article>
    </div>
    <div v-if="totalPages > 1" class="market-pagination">
      <NPagination :page="page" :page-count="totalPages" @update:page="goToPage" />
    </div>
    <p v-if="!filteredMarketplace.length" class="muted">{{ t('marketplaceNoMatches') }}</p>
  </NSpin>
</template>

<style scoped>
.market-sort-select {
  width: 150px;
}
.source-line {
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>
