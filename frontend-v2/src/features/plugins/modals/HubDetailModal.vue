<script setup lang="ts">
import type { Component } from 'vue'
import { NAlert, NButton, NIcon, NModal, NRate, NSpin, NTag } from 'naive-ui'
import { CloudDownloadOutline, OpenOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { HubRatingSummary, PluginMarketplaceItem } from '@/api/types'

defineProps<{
  show: boolean
  // The online content packages shelf reuses this dialog with its own wording.
  title?: string
  descriptionTitle?: string
  hubDetail: PluginMarketplaceItem | null
  hubDetailLoading: boolean
  hubReadmeLoading: boolean
  hubRating: number | null
  hubRatingSummary: HubRatingSummary | null
  busy: string
  safeHubReadmeHtml: string
  pluginTypeIcon: (type?: string) => Component
  pluginTypeLabel: (type?: string) => string
  marketItemHasNewerVersion: (item: PluginMarketplaceItem) => boolean
  installMarketPlugin: (item: PluginMarketplaceItem) => Promise<void> | void
  openUrl: (url?: string) => void
  toggleHubLike: () => Promise<void> | void
  saveHubRating: (stars: number | null) => Promise<void> | void
}>()
const emit = defineEmits<{ 'update:show': [value: boolean] }>()

const { t } = useLocale()

function authorLabel(author: unknown): string {
  if (typeof author === 'string') return author
  if (!author || typeof author !== 'object') return '-'
  const value = author as Record<string, unknown>
  for (const key of ['name', 'login', 'id']) {
    if (typeof value[key] === 'string' && value[key]) return value[key]
  }
  return '-'
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    class="hub-detail-modal"
    :title="title || t('hubPluginDetails')"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <NSpin :show="hubDetailLoading">
      <template v-if="hubDetail">
        <header class="hub-detail-hero">
          <NIcon :component="pluginTypeIcon(hubDetail.plugin_type)" :size="30" class="hub-detail-icon" />
          <div class="hub-detail-heading">
            <div class="hub-detail-title-row">
              <h2>{{ hubDetail.name }}</h2>
              <NTag size="small">v{{ hubDetail.version || t('unknownVersion') }}</NTag>
              <NTag size="small" :bordered="false">{{ pluginTypeLabel(hubDetail.plugin_type) }}</NTag>
            </div>
            <p>{{ hubDetail.description || t('noDescription') }}</p>
          </div>
          <NButton
            v-if="!hubDetail.installed || marketItemHasNewerVersion(hubDetail)"
            type="primary"
            size="large"
            :disabled="hubDetail.installable === false"
            :loading="busy === `market:${hubDetail.id}`"
            @click="installMarketPlugin(hubDetail)"
          >
            <template #icon><NIcon :component="CloudDownloadOutline" /></template>
            {{ hubDetail.installed ? t('update') : t('install') }}
          </NButton>
          <NButton v-else size="large" disabled>{{ t('installed') }}</NButton>
        </header>

        <div class="hub-detail-layout">
          <aside class="hub-detail-sidebar">
            <section class="hub-detail-panel">
              <h3>{{ t('hubStatistics') }}</h3>
              <dl class="hub-data-grid">
                <div><dt>{{ t('hubDownloads') }}</dt><dd>{{ hubDetail.stats?.downloads_total || 0 }}</dd></div>
                <div><dt>{{ t('hubRatingAverage') }}</dt><dd>{{ hubRatingSummary?.average ?? hubDetail.stats?.rating_average ?? 0 }}</dd></div>
                <div><dt>{{ t('hubLikes') }}</dt><dd>{{ hubDetail.stats?.likes || 0 }}</dd></div>
                <div><dt>{{ t('hubRatingCount') }}</dt><dd>{{ hubRatingSummary?.count ?? hubDetail.stats?.rating_count ?? 0 }}</dd></div>
              </dl>
              <dl class="hub-secondary-stats">
                <div><dt>{{ t('hubDownloads30d') }}</dt><dd>{{ hubDetail.stats?.downloads_30d || 0 }}</dd></div>
                <div><dt>{{ t('hubInstallsTotal') }}</dt><dd>{{ hubDetail.stats?.installs_total || 0 }}</dd></div>
              </dl>
              <div v-if="hubRatingSummary" class="hub-rating-distribution">
                <span v-for="stars in [5, 4, 3, 2, 1]" :key="stars">
                  {{ stars }}★ <strong>{{ hubRatingSummary.distribution[String(stars) as '1' | '2' | '3' | '4' | '5'] || 0 }}</strong>
                </span>
              </div>
              <div class="hub-interactions">
                <NButton :loading="busy === `hub-like:${hubDetail.id}`" @click="toggleHubLike">
                  {{ hubDetail.liked ? t('hubUnlike') : t('hubLike') }}
                </NButton>
                <div class="hub-own-rating">
                  <span>{{ t('hubYourRating') }}</span>
                  <NRate :value="hubRating || 0" :disabled="busy === `hub-rating:${hubDetail.id}`" @update:value="saveHubRating" />
                </div>
                <NButton v-if="hubRating" text @click="saveHubRating(null)">{{ t('hubClearRating') }}</NButton>
              </div>
            </section>

            <section class="hub-detail-panel">
              <h3>{{ t('hubBasicInfo') }}</h3>
              <dl class="hub-basic-info">
                <div><dt>{{ t('author') }}</dt><dd>{{ authorLabel(hubDetail.author) }}</dd></div>
                <div><dt>{{ t('hubVersionLabel') }}</dt><dd>v{{ hubDetail.version || t('unknownVersion') }}</dd></div>
                <div><dt>{{ t('hubPluginTypeLabel') }}</dt><dd>{{ pluginTypeLabel(hubDetail.plugin_type) }}</dd></div>
                <div><dt>{{ t('hubLicenseLabel') }}</dt><dd>{{ hubDetail.license || '-' }}</dd></div>
              </dl>
              <NButton
                secondary
                :disabled="!hubDetail.repository_url && !hubDetail.homepage"
                @click="openUrl(hubDetail.repository_url || hubDetail.homepage)"
              >
                <template #icon><NIcon :component="OpenOutline" /></template>
                {{ t('openRepository') }}
              </NButton>
            </section>
          </aside>

          <main class="hub-detail-content">
            <NAlert v-if="hubDetail.security?.install_allowed === false" type="error" :title="t('hubInstallBlocked')">
              {{ (hubDetail.security.blocking_reasons || []).join(t('listSeparator')) }}
            </NAlert>
            <h3>{{ descriptionTitle || t('hubPluginDescription') }}</h3>
            <section v-if="safeHubReadmeHtml" class="hub-readme safe-markdown" v-html="safeHubReadmeHtml" />
            <NSpin v-else-if="hubReadmeLoading" size="small" />
            <p v-else-if="!hubDetailLoading" class="muted">{{ t('hubReadmeUnavailable') }}</p>
          </main>
        </div>
      </template>
    </NSpin>
  </NModal>
</template>

<style scoped>
.hub-detail-hero {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  margin-bottom: 18px;
  padding: 16px;
  border: 1px solid var(--df-border-soft);
  border-radius: 10px;
  background: var(--df-surface-1);
}

.hub-detail-icon {
  box-sizing: content-box;
  padding: 12px;
  border-radius: 9px;
  color: var(--df-interactive-strong);
  background: color-mix(in srgb, var(--df-interactive) 10%, var(--df-surface-2));
}

.hub-detail-heading,
.hub-detail-title-row {
  min-width: 0;
}

.hub-detail-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.hub-detail-title-row h2,
.hub-detail-heading p,
.hub-detail-panel h3,
.hub-detail-content h3 {
  margin: 0;
}

.hub-detail-heading p {
  margin-top: 7px;
  color: var(--df-text-muted);
  line-height: 1.55;
}

.hub-detail-layout {
  display: grid;
  grid-template-columns: minmax(300px, .72fr) minmax(0, 1.48fr);
  gap: 18px;
  align-items: start;
}

.hub-detail-sidebar {
  display: grid;
  gap: 16px;
}

.hub-detail-panel,
.hub-detail-content {
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--df-border-soft);
  border-radius: 10px;
  background: var(--df-surface-1);
}

.hub-data-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 16px 0 12px;
}

.hub-data-grid div {
  min-width: 0;
  padding: 12px 6px;
  border: 1px solid var(--df-border-soft);
  border-radius: 8px;
  text-align: center;
  background: var(--df-surface-2);
}

.hub-data-grid dt,
.hub-secondary-stats dt,
.hub-basic-info dt {
  color: var(--df-text-muted);
  font-size: 12px;
}

.hub-data-grid dd {
  margin: 4px 0 0;
  font-size: 20px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
}

.hub-secondary-stats,
.hub-basic-info {
  display: grid;
  gap: 9px;
  margin: 0;
}

.hub-secondary-stats {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 10px 0;
  border-top: 1px solid var(--df-border-soft);
  border-bottom: 1px solid var(--df-border-soft);
}

.hub-secondary-stats div,
.hub-basic-info div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.hub-secondary-stats dd,
.hub-basic-info dd {
  margin: 0;
  color: var(--df-text-secondary);
  text-align: right;
  overflow-wrap: anywhere;
}

.hub-rating-distribution {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin: 12px 0;
  color: var(--df-text-muted);
  font-size: 12px;
}

.hub-interactions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.hub-own-rating {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--df-text-muted);
  font-size: 12px;
}

.hub-basic-info {
  margin: 15px 0;
}

.hub-readme {
  /* dvh 跟随移动端浏览器地址栏收放，避免 100vh 高于实际可视区导致底部溢出 */
  max-height: 62dvh;
  overflow: auto;
  margin-top: 16px;
  padding-right: 8px;
}

:global(.hub-detail-modal) {
  width: min(1180px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
}

:global(.hub-detail-modal > .n-card__content) {
  overflow: auto;
}

@media (max-width: 920px) {
  .hub-detail-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 620px) {
  .hub-readme {
    max-height: 46dvh;
  }

  .hub-detail-hero {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .hub-detail-hero > .n-button {
    grid-column: 1 / -1;
  }

  .hub-data-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
