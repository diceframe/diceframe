<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NIcon, NInput, NSelect, NSpin, NTag } from 'naive-ui'
import {
  OpenOutline,
  RefreshOutline,
  SearchOutline,
  SparklesOutline,
  Star,
  TrashOutline,
  TrophyOutline,
} from '@vicons/ionicons5'
import {
  activateHonorsToken,
  clearHonorsCredential,
  getHonorsState,
  getHonorsWall,
  setFeaturedHonorBadge,
  type HonorsActiveCredential,
  type HonorsBadgeDefinition,
  type HonorsWallItem,
  type HonorsWallResponse,
} from '@/api/honors'
import { errorMessage } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import BadgeInspectModal from './BadgeInspectModal.vue'
import { buildBadgeFrontSvg, resolveLocalizedText } from './badgeArtwork'

const { t, locale } = useLocale()
const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(true)
const activateBusy = ref(false)
const featuredBusy = ref(false)
const clearBusy = ref(false)
const wallLoading = ref(false)

const catalog = ref<HonorsBadgeDefinition[]>([])
const active = ref<HonorsActiveCredential | null>(null)
const redeemUrl = ref('https://diceframe.com/redeem')
const remoteConfigured = ref(false)
const tokenInput = ref('')
const tokenEditorOpen = ref(false)

const wallData = ref<HonorsWallResponse | null>(null)
const wallBadgeFilter = ref('')
const wallQuery = ref('')
const wallPage = ref(1)
const highlightSelf = ref(false)

const inspectOpen = ref(false)
const inspectBadge = ref<HonorsBadgeDefinition | null>(null)
const showcaseFilter = ref<'all' | 'owned' | string>('all')
const showcaseExpanded = ref(false)
const SHOWCASE_COLLAPSE_LIMIT = 8

const ownedSet = computed(() => new Set(active.value?.badges || []))

const catalogMap = computed(() => {
  const map = new Map<string, HonorsBadgeDefinition>()
  for (const item of catalog.value) map.set(item.id, item)
  return map
})

const mergedShowcaseBadges = computed<HonorsBadgeDefinition[]>(() => {
  const result = [...catalog.value]
  for (const bid of active.value?.badges || []) {
    if (!catalogMap.value.has(bid)) {
      result.push({
        id: bid,
        category: 'milestone',
        shape: 'coin',
        wall_mode: 'listed',
        theme: 'bronze',
        name: { 'zh-CN': bid, en: bid },
        description: {
          'zh-CN': '已点亮的纪念勋章，联网后将自动显示完整样式。',
          en: 'Collected commemorative badge; full artwork will appear when online.',
        },
      })
    }
  }
  // Owned badges float to the front so large future catalogs stay easy to scan.
  return result.sort((a, b) => Number(ownedSet.value.has(b.id)) - Number(ownedSet.value.has(a.id)))
})

const showcaseCategories = computed(() => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const item of mergedShowcaseBadges.value) {
    const cat = item.category || 'milestone'
    if (!seen.has(cat)) {
      seen.add(cat)
      out.push(cat)
    }
  }
  return out
})

const filteredShowcaseBadges = computed(() => {
  const f = showcaseFilter.value
  if (f === 'all') return mergedShowcaseBadges.value
  if (f === 'owned') return mergedShowcaseBadges.value.filter(b => ownedSet.value.has(b.id))
  return mergedShowcaseBadges.value.filter(b => (b.category || 'milestone') === f)
})

const visibleShowcaseBadges = computed(() => {
  if (showcaseExpanded.value || filteredShowcaseBadges.value.length <= SHOWCASE_COLLAPSE_LIMIT) {
    return filteredShowcaseBadges.value
  }
  return filteredShowcaseBadges.value.slice(0, SHOWCASE_COLLAPSE_LIMIT)
})

const useCompactWallSelector = computed(() => listedBadges.value.length > 6)

const wallBadgeSelectOptions = computed(() => [
  { label: t('honorsFilterAll'), value: '' },
  ...listedBadges.value.map(b => ({ label: badgeName(b), value: b.id })),
])

const selectedWallBadge = computed(() =>
  wallBadgeFilter.value ? catalogMap.value.get(wallBadgeFilter.value) || null : null,
)

function categoryLabel(cat: string): string {
  const map: Record<string, string> = {
    milestone: t('honorsCatMilestone'),
    creation: t('honorsCatCreation'),
    community: t('honorsCatCommunity'),
    tribute: t('honorsCatTribute'),
  }
  return map[cat] || cat
}

function badgeDescription(badge: HonorsBadgeDefinition): string {
  return resolveLocalizedText(badge.description, locale.value, '')
}

function badgeObtainHint(badge: HonorsBadgeDefinition): string {
  return resolveLocalizedText(badge.obtain_hint, locale.value, '')
}

const listedBadges = computed(() =>
  mergedShowcaseBadges.value.filter(item => item.wall_mode !== 'counter'),
)

const counterBadges = computed(() =>
  mergedShowcaseBadges.value.filter(item => item.wall_mode === 'counter'),
)

const featuredBadge = computed(() => {
  const id = active.value?.featured_badge_id
  if (!id) return null
  return mergedShowcaseBadges.value.find(b => b.id === id) || null
})

function badgeName(badge: HonorsBadgeDefinition): string {
  return resolveLocalizedText(badge.name, locale.value, badge.id)
}

function badgeCardSvg(badge: HonorsBadgeDefinition, prefix: string): string {
  return buildBadgeFrontSvg(badge, locale.value, `${prefix}-${badge.id}`, true)
}

function formatDate(unixSeconds?: number): string {
  if (!unixSeconds) return '-'
  const d = new Date(unixSeconds * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function openInspect(badge: HonorsBadgeDefinition) {
  inspectBadge.value = badge
  inspectOpen.value = true
}

async function loadState() {
  loading.value = true
  try {
    const state = await getHonorsState()
    catalog.value = state.catalog || []
    active.value = state.active || null
    redeemUrl.value = state.redeem_url || 'https://diceframe.com/redeem'
    remoteConfigured.value = Boolean(state.remote_configured)
    tokenEditorOpen.value = !state.active
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    loading.value = false
  }
}

async function loadWall(refresh = false) {
  wallLoading.value = true
  try {
    const res = await getHonorsWall({
      page: wallPage.value,
      limit: 60,
      badge: wallBadgeFilter.value,
      q: wallQuery.value.trim(),
      refresh,
    })
    wallData.value = res
    if (res.catalog?.length) {
      catalog.value = res.catalog
    }
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    wallLoading.value = false
  }
}

async function submitToken() {
  const value = tokenInput.value.trim()
  if (!value) {
    toast.warning(t('honorsTokenRequired'))
    return
  }
  activateBusy.value = true
  try {
    const res = await activateHonorsToken(value)
    catalog.value = res.catalog || catalog.value
    active.value = res.active
    tokenInput.value = ''
    tokenEditorOpen.value = false
    toast.success(t('honorsActivatedToast', { name: res.active?.name || '' }))
    await loadWall(false)
    if (featuredBadge.value) {
      openInspect(featuredBadge.value)
    }
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    activateBusy.value = false
  }
}

async function handleSetFeatured(badgeId: string) {
  featuredBusy.value = true
  try {
    const res = await setFeaturedHonorBadge(badgeId)
    active.value = res.active
    toast.success(t('honorsFeaturedUpdated'))
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    featuredBusy.value = false
  }
}

async function handleClear() {
  const ok = await confirm({
    title: t('honorsClearTitle'),
    content: t('honorsClearConfirm'),
    positiveText: t('honorsClearAction'),
    negativeText: t('cancel'),
    type: 'warning',
  })
  if (!ok) return
  clearBusy.value = true
  try {
    const res = await clearHonorsCredential()
    active.value = res.active
    tokenEditorOpen.value = true
    toast.success(t('honorsClearedToast'))
    await loadWall(false)
  } catch (err) {
    toast.error(errorMessage(err))
  } finally {
    clearBusy.value = false
  }
}

function selectWallFilter(badgeId: string) {
  wallBadgeFilter.value = badgeId
  wallPage.value = 1
  highlightSelf.value = false
  void loadWall(false)
}

function searchWall() {
  wallPage.value = 1
  highlightSelf.value = false
  void loadWall(false)
}

function locateMyself() {
  if (!active.value) return
  wallBadgeFilter.value = ''
  wallQuery.value = active.value.id
  wallPage.value = 1
  highlightSelf.value = true
  void loadWall(false)
}

function isSelfItem(item: HonorsWallItem): boolean {
  return Boolean(item.is_self || (active.value && item.id === active.value.id))
}

function itemBadgeDef(badgeId: string): HonorsBadgeDefinition | undefined {
  return catalogMap.value.get(badgeId)
}

onMounted(async () => {
  await loadState()
  await loadWall(false)
})
</script>

<template>
  <div id="settings-honors" class="honors-panel">
    <NSpin :show="loading">
      <div class="honors-stack">
      <!-- 上半区：我的纪念陈列柜 -->
      <section class="honors-card">
        <header class="honors-head">
          <div>
            <h3>
              <NIcon :component="TrophyOutline" class="head-icon" />
              {{ t('honorsCabinetTitle') }}
            </h3>
            <p class="muted">{{ t('honorsCabinetSubtitle') }}</p>
          </div>
          <div class="head-actions">
            <NButton
              v-if="active"
              size="small"
              quaternary
              @click="tokenEditorOpen = !tokenEditorOpen"
            >
              {{ tokenEditorOpen ? t('cancel') : t('honorsUpdateToken') }}
            </NButton>
            <NButton
              v-if="active"
              size="small"
              quaternary
              type="error"
              :loading="clearBusy"
              @click="handleClear"
            >
              <template #icon><NIcon :component="TrashOutline" /></template>
              {{ t('honorsClearAction') }}
            </NButton>
          </div>
        </header>

        <!-- 已点亮铭牌 -->
        <div v-if="active" class="holder-banner">
          <button
            v-if="featuredBadge"
            type="button"
            class="holder-emblem"
            :title="t('honorsInspectHint')"
            @click="openInspect(featuredBadge)"
          >
            <img
              v-if="featuredBadge.image_url"
              :src="featuredBadge.image_url"
              :alt="badgeName(featuredBadge)"
            >
            <span v-else class="emblem-svg" v-html="badgeCardSvg(featuredBadge, 'banner')" />
          </button>
          <div class="holder-info">
            <div class="holder-name-row">
              <strong>{{ active.name }}</strong>
              <span class="holder-serial">NO. {{ active.id }}</span>
              <NTag type="success" size="small" round>
                {{ t('honorsUnlockedCount', { count: active.badges.length }) }}
              </NTag>
            </div>
            <p class="muted holder-meta">
              {{ t('honorsIssuedAt', { date: formatDate(active.iat) }) }}
              <template v-if="featuredBadge">
                · {{ t('honorsFeaturedLabel', { badge: badgeName(featuredBadge) }) }}
              </template>
            </p>
          </div>
        </div>

        <!-- 凭证输入区 -->
        <div v-if="tokenEditorOpen || !active" class="token-box">
          <div class="token-row">
            <NInput
              v-model:value="tokenInput"
              :placeholder="t('honorsTokenPlaceholder')"
              clearable
              @keyup.enter="submitToken"
            />
            <NButton type="primary" :loading="activateBusy" @click="submitToken">
              <template #icon><NIcon :component="SparklesOutline" /></template>
              {{ t('honorsActivateAction') }}
            </NButton>
          </div>
          <div class="token-hints">
            <span class="muted">{{ t('honorsOfflineVerifyHint') }}</span>
            <a
              class="redeem-link"
              :href="redeemUrl"
              target="_blank"
              rel="noopener"
            >
              {{ t('honorsGoRedeem') }}
              <NIcon :component="OpenOutline" :size="13" />
            </a>
          </div>
        </div>

        <!-- 陈列柜分类筛选（为未来几十枚勋章提前做好分组与折叠） -->
        <div class="showcase-filter-bar">
          <button
            type="button"
            class="filter-chip"
            :class="{ active: showcaseFilter === 'all' }"
            @click="showcaseFilter = 'all'"
          >
            {{ t('honorsFilterAll') }} ({{ mergedShowcaseBadges.length }})
          </button>
          <button
            v-if="active?.badges?.length"
            type="button"
            class="filter-chip"
            :class="{ active: showcaseFilter === 'owned' }"
            @click="showcaseFilter = 'owned'"
          >
            {{ t('honorsBadgeOwned') }} ({{ active.badges.length }})
          </button>
          <button
            v-for="cat in showcaseCategories"
            :key="cat"
            type="button"
            class="filter-chip"
            :class="{ active: showcaseFilter === cat }"
            @click="showcaseFilter = cat"
          >
            {{ categoryLabel(cat) }}
          </button>
        </div>

        <!-- 陈列柜饰品网格（所有饰品均可点击 3D 检视） -->
        <div class="showcase-grid">
          <button
            v-for="badge in visibleShowcaseBadges"
            :key="badge.id"
            type="button"
            class="showcase-item"
            :class="{
              'is-owned': ownedSet.has(badge.id),
              'is-featured': active?.featured_badge_id === badge.id,
            }"
            :title="badgeObtainHint(badge) || badgeDescription(badge)"
            @click="openInspect(badge)"
          >
            <span v-if="active?.featured_badge_id === badge.id" class="featured-pin" :title="t('honorsFeaturedCurrent')">
              <NIcon :component="Star" :size="12" />
            </span>
            <div class="showcase-art">
              <img v-if="badge.image_url" :src="badge.image_url" :alt="badgeName(badge)">
              <span v-else class="emblem-svg" v-html="badgeCardSvg(badge, 'grid')" />
            </div>
            <div class="showcase-copy">
              <strong>{{ badgeName(badge) }}</strong>
              <span v-if="badgeObtainHint(badge) || badgeDescription(badge)" class="showcase-obtain">
                {{ badgeObtainHint(badge) || badgeDescription(badge) }}
              </span>
            </div>
            <span class="showcase-status">
              {{ ownedSet.has(badge.id) ? t('honorsInspectAction') : t('honorsPreviewAction') }}
            </span>
          </button>
        </div>

        <div
          v-if="filteredShowcaseBadges.length > SHOWCASE_COLLAPSE_LIMIT"
          class="showcase-expand-row"
        >
          <NButton size="small" quaternary @click="showcaseExpanded = !showcaseExpanded">
            {{
              showcaseExpanded
                ? t('honorsCollapseCatalog')
                : t('honorsExpandCatalog', { count: filteredShowcaseBadges.length })
            }}
          </NButton>
        </div>
      </section>

      <!-- 下半区：社区荣誉墙 -->
      <section class="honors-card">
        <header class="honors-head">
          <div>
            <h3>{{ t('honorsWallTitle') }}</h3>
            <p class="muted">
              {{ wallData?.source === 'remote' ? t('honorsWallSubtitleOnline') : t('honorsWallSubtitleOffline') }}
            </p>
          </div>
          <div class="head-actions">
            <NButton
              v-if="active"
              size="small"
              quaternary
              @click="locateMyself"
            >
              {{ t('honorsFindMyself') }}
            </NButton>
            <NButton
              size="small"
              quaternary
              :loading="wallLoading"
              @click="loadWall(true)"
            >
              <template #icon><NIcon :component="RefreshOutline" /></template>
              {{ t('refresh') }}
            </NButton>
          </div>
        </header>

        <!-- 全服计数型纪念印记汇总条 -->
        <div v-if="counterBadges.length" class="counter-strip">
          <button
            v-for="badge in counterBadges"
            :key="badge.id"
            type="button"
            class="counter-pill"
            @click="openInspect(badge)"
          >
            <span class="counter-mini" v-html="badgeCardSvg(badge, 'counter')" />
            <span class="counter-name">{{ badgeName(badge) }}</span>
            <span class="counter-value">
              {{ t('honorsCounterLit', { count: wallData?.counters?.[badge.id] ?? 0 }) }}
            </span>
          </button>
        </div>

        <!-- 筛选与搜索栏（勋章超过 6 枚时自动切紧凑下拉，避免多行撑爆） -->
        <div class="wall-toolbar">
          <div v-if="!useCompactWallSelector" class="wall-filters">
            <button
              type="button"
              class="filter-chip"
              :class="{ active: wallBadgeFilter === '' }"
              @click="selectWallFilter('')"
            >
              {{ t('honorsFilterAll') }}
            </button>
            <button
              v-for="badge in listedBadges"
              :key="badge.id"
              type="button"
              class="filter-chip"
              :class="{ active: wallBadgeFilter === badge.id }"
              @click="selectWallFilter(badge.id)"
            >
              {{ badgeName(badge) }}
            </button>
          </div>
          <div v-else class="wall-select-wrap">
            <NSelect
              :value="wallBadgeFilter"
              :options="wallBadgeSelectOptions"
              size="small"
              @update:value="(val: string) => selectWallFilter(val || '')"
            />
          </div>
          <div class="wall-search">
            <NInput
              v-model:value="wallQuery"
              size="small"
              clearable
              :placeholder="t('honorsSearchPlaceholder')"
              @keyup.enter="searchWall"
              @clear="searchWall"
            >
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
          </div>
        </div>

        <!-- 选中某枚勋章时的清晰含义与获取说明条 -->
        <div v-if="selectedWallBadge" class="wall-badge-callout">
          <div class="callout-text">
            <strong>{{ badgeName(selectedWallBadge) }}</strong>
            <span>{{ badgeDescription(selectedWallBadge) }}</span>
            <small v-if="badgeObtainHint(selectedWallBadge)">
              {{ t('honorsMetaObtain') }}：{{ badgeObtainHint(selectedWallBadge) }}
            </small>
          </div>
          <NButton size="tiny" quaternary @click="openInspect(selectedWallBadge)">
            {{ t('honorsPreviewAction') }}
          </NButton>
        </div>

        <NSpin :show="wallLoading">
          <div v-if="wallData?.items?.length" class="wall-grid">
            <div
              v-for="item in wallData.items"
              :key="item.id"
              class="wall-plate"
              :class="{ 'is-self': isSelfItem(item), 'is-highlighted': highlightSelf && isSelfItem(item) }"
            >
              <div class="plate-badges">
                <button
                  v-for="bid in item.badges.slice(0, 3)"
                  :key="bid"
                  type="button"
                  class="plate-mini-btn"
                  :title="itemBadgeDef(bid) ? badgeName(itemBadgeDef(bid)!) : bid"
                  @click="itemBadgeDef(bid) && openInspect(itemBadgeDef(bid)!)"
                >
                  <span
                    v-if="itemBadgeDef(bid)"
                    class="plate-mini-svg"
                    v-html="badgeCardSvg(itemBadgeDef(bid)!, `wall-${item.id}`)"
                  />
                </button>
              </div>
              <div class="plate-text">
                <span class="plate-name">{{ item.name }}</span>
                <span class="plate-id">{{ item.id }}</span>
              </div>
            </div>
          </div>

          <div v-else class="wall-empty">
            <p class="muted">
              {{ remoteConfigured ? t('honorsWallEmptySearch') : t('honorsWallEmptyLocal') }}
            </p>
          </div>

          <div v-if="(wallData?.total || 0) > (wallData?.limit || 60)" class="wall-pager">
            <NButton
              size="small"
              :disabled="wallPage <= 1"
              @click="wallPage -= 1; loadWall(false)"
            >
              {{ t('honorsPrevPage') }}
            </NButton>
            <span class="muted">
              {{ wallPage }} / {{ Math.max(1, Math.ceil((wallData?.total || 0) / (wallData?.limit || 60))) }}
            </span>
            <NButton
              size="small"
              :disabled="wallPage * (wallData?.limit || 60) >= (wallData?.total || 0)"
              @click="wallPage += 1; loadWall(false)"
            >
              {{ t('honorsNextPage') }}
            </NButton>
          </div>
        </NSpin>
      </section>
      </div>
    </NSpin>

    <BadgeInspectModal
      v-model:show="inspectOpen"
      :badge="inspectBadge"
      :active="active"
      :owned="Boolean(inspectBadge && ownedSet.has(inspectBadge.id))"
      :is-featured="Boolean(inspectBadge && active?.featured_badge_id === inspectBadge.id)"
      :featured-busy="featuredBusy"
      @set-featured="handleSetFeatured"
    />
  </div>
</template>

<style scoped>
.honors-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.honors-stack {
  display: flex;
  flex-direction: column;
  gap: var(--df-section-gap, 22px);
}

.honors-card {
  padding: 18px 20px;
  border-radius: var(--df-radius-lg, 12px);
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-1) 90%, transparent);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.honors-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.honors-head h3 {
  margin: 0;
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--df-text);
}

.head-icon {
  color: var(--df-accent-strong);
}

.honors-head p {
  margin: 4px 0 0;
  font-size: 13px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.muted {
  color: var(--df-text-muted);
}

.holder-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  border-radius: var(--df-radius-md, 10px);
  border: 1px solid color-mix(in srgb, var(--df-accent) 44%, transparent);
  background: linear-gradient(120deg, color-mix(in srgb, var(--df-accent) 12%, transparent), color-mix(in srgb, var(--df-surface-2) 86%, transparent));
}

.holder-emblem {
  width: 64px;
  height: 64px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  flex-shrink: 0;
  transition: transform 0.18s ease;
}

.holder-emblem:hover {
  transform: scale(1.06);
}

.holder-emblem img,
.emblem-svg,
.emblem-svg :deep(.df-badge-svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.holder-info {
  min-width: 0;
  flex: 1;
}

.holder-name-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.holder-name-row strong {
  font-size: 17px;
  font-family: var(--df-font-title);
  color: var(--df-text);
}

.holder-serial {
  font-size: 12px;
  font-family: var(--df-font-mono);
  color: var(--df-accent-strong);
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--df-accent) 40%, transparent);
}

.holder-meta {
  margin: 6px 0 0;
  font-size: 12px;
}

.token-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--df-radius-md, 10px);
  border: 1px dashed var(--df-border-soft);
  background: color-mix(in srgb, var(--df-control-bg) 68%, transparent);
}

.token-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.token-hints {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
}

.redeem-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--df-accent-strong);
  text-decoration: none;
}

.redeem-link:hover {
  text-decoration: underline;
}

.showcase-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.showcase-obtain {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--df-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.showcase-expand-row {
  display: flex;
  justify-content: center;
}

.wall-select-wrap {
  width: 220px;
  max-width: 100%;
}

.wall-badge-callout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--df-radius-md, 8px);
  border: 1px solid color-mix(in srgb, var(--df-accent) 34%, transparent);
  background: color-mix(in srgb, var(--df-accent) 8%, transparent);
}

.callout-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: var(--df-text-secondary);
}

.callout-text strong {
  color: var(--df-accent-strong);
  font-size: 13px;
}

.callout-text small {
  color: var(--df-text-muted);
}

.showcase-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(158px, 1fr));
  gap: 12px;
}

.showcase-item {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 16px 12px 13px;
  border-radius: var(--df-radius-md, 10px);
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-2) 72%, transparent);
  color: var(--df-text);
  cursor: pointer;
  text-align: center;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.showcase-item:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--df-accent) 60%, transparent);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.24);
}

.showcase-item:not(.is-owned) .showcase-art {
  opacity: 0.62;
  filter: saturate(0.55);
}

.showcase-item.is-owned {
  border-color: color-mix(in srgb, var(--df-accent) 46%, transparent);
  background: linear-gradient(180deg, color-mix(in srgb, var(--df-accent) 10%, transparent), color-mix(in srgb, var(--df-surface-2) 86%, transparent));
}

.showcase-item.is-featured {
  border-color: var(--df-accent-strong);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--df-accent) 45%, transparent);
}

.featured-pin {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--df-accent) 22%, transparent);
  color: var(--df-accent-strong);
}

.showcase-art {
  width: 78px;
  height: 78px;
  transition: transform 0.18s ease, filter 0.18s ease, opacity 0.18s ease;
  filter: drop-shadow(0 8px 14px rgba(0, 0, 0, 0.36));
}

.showcase-item:hover .showcase-art {
  transform: scale(1.06);
  opacity: 1;
  filter: drop-shadow(0 10px 18px rgba(0, 0, 0, 0.45));
}

.showcase-art img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.showcase-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  width: 100%;
}

.showcase-copy strong {
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.showcase-status {
  font-size: 11px;
  color: var(--df-accent-strong);
  opacity: 0.9;
}

.counter-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.counter-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-2) 82%, transparent);
  color: var(--df-text);
  font-size: 12px;
  cursor: pointer;
}

.counter-pill:hover {
  border-color: var(--df-accent);
}

.counter-mini,
.counter-mini :deep(.df-badge-svg) {
  width: 22px;
  height: 22px;
  display: block;
}

.counter-name {
  font-weight: 600;
}

.counter-value {
  color: var(--df-text-muted);
  font-variant-numeric: tabular-nums;
}

.wall-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.wall-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.filter-chip {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--df-border-soft);
  background: transparent;
  color: var(--df-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.filter-chip:hover {
  color: var(--df-text);
  border-color: var(--df-accent);
}

.filter-chip.active {
  background: color-mix(in srgb, var(--df-accent) 20%, transparent);
  border-color: var(--df-accent);
  color: var(--df-accent-strong);
  font-weight: 600;
}

.wall-search {
  width: 220px;
  max-width: 100%;
}

.wall-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(186px, 1fr));
  gap: 8px;
}

.wall-plate {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--df-radius-md, 8px);
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-2) 72%, transparent);
}

.wall-plate.is-self {
  border-color: color-mix(in srgb, var(--df-accent) 56%, transparent);
}

.wall-plate.is-highlighted {
  box-shadow: 0 0 0 2px var(--df-accent);
}

.plate-badges {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.plate-mini-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.plate-mini-svg,
.plate-mini-svg :deep(.df-badge-svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.plate-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.plate-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--df-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plate-id {
  font-size: 11px;
  font-family: var(--df-font-mono);
  color: var(--df-text-muted);
}

.wall-empty {
  padding: 20px 12px;
  text-align: center;
  font-size: 13px;
}

.wall-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 12px;
  font-size: 12px;
}

@media (max-width: 600px) {
  .token-row {
    flex-direction: column;
    align-items: stretch;
  }
  .wall-search {
    width: 100%;
  }
}
</style>
