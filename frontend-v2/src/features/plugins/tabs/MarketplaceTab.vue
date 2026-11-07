<script setup lang="ts">
import { NButton, NIcon, NInput, NPagination, NSelect, NSpin, NTag } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { RefreshOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { PluginMarketplaceItem } from '@/api/types'
import MarketplaceCard from '../MarketplaceCard.vue'

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
      <MarketplaceCard
        v-for="item in paginatedMarketplace"
        :key="item.id"
        :item="item"
        :icon="pluginTypeIcon(item.plugin_type)"
        :type-label="pluginTypeLabel(item.plugin_type)"
        :show-hub-stats="Boolean(marketplaceSource?.hub)"
        :has-newer-version="marketItemHasNewerVersion(item)"
        :installing="busy === `market:${item.id}`"
        @install="installMarketPlugin(item)"
        @open-url="openUrl"
        @detail="openHubDetail(item)"
      />
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
