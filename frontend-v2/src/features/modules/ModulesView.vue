<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import {
  moduleApi,
  type ModuleMarketplaceItem,
  type ModuleMarketplaceResponse,
  type ModulePreviewResponse,
  type ModuleSummary,
} from '@/api/modules'
import { useLocale } from '@/composables/useLocale'
import { SUPPORTED_LOCALES, type Locale, type MessageKey } from '@/i18n'
import { libraryCopy } from '@/features/library/copy'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import Modal from '@/components/ui/Modal.vue'
import DOMPurify from 'dompurify'
import { NTag } from 'naive-ui'
import { CubeOutline } from '@vicons/ionicons5'
import type { PluginMarketplaceItem } from '@/api/types'
import MarketplaceCard from '@/features/plugins/MarketplaceCard.vue'
import HubDetailModal from '@/features/plugins/modals/HubDetailModal.vue'
import { useHubDetail } from '@/features/plugins/useHubDetail'
import { contentPackSubmissionUrl } from '@/features/creator/submission'
import { localeLabel } from '@/utils/contentLanguage'
import { moduleCategoryLabel, moduleCopy, moduleInventory } from './copy'

type RecoveryAdventure = {
  binding?: {
    adventure_id?: string
    version?: string
    content_digest?: string
    source_kind?: string
    source_id?: string
  }
  available: boolean
  reason?: string
}
type RecoveryCheck = {
  ready: boolean
  reason: string
  digestMatches: boolean
  sourceMatches: boolean
}

const { t, locale } = useLocale()
const copy = computed(() => moduleCopy(locale.value))
const library = computed(() => libraryCopy(locale.value))
const route = useRoute()
const modules = ref<ModuleSummary[]>([])
const market = ref<ModuleMarketplaceItem[]>([])
const marketError = ref('')
const marketKeyword = ref('')
const marketRuleset = ref('all')
const marketProfile = ref('all')
const marketLanguage = ref('all')
const marketTag = ref('all')
const marketLicense = ref('all')
const marketTrust = ref('all')
const marketBusy = ref(false)
const installBusy = ref('')
const marketSource = ref<ModuleMarketplaceResponse['source'] | null>(null)
const installFeedback = ref<{ id: string; kind: 'busy' | 'success' | 'error'; message: string } | null>(null)
const submitGuideOpen = ref(false)
const directSubmissionUrl = contentPackSubmissionUrl()
const error = ref('')

// 在线列表的筛选全部在客户端完成：服务端只按关键字和规则目标收窄，
// 语言 / 标签 / 许可证 / 信任级别本来就在市场投影里，不需要新接口。
function uniqueFilterValues(values: Array<string | undefined>): string[] {
  const unique = new Set(values.map(value => String(value || '').trim()).filter(Boolean))
  return [...unique].sort((a, b) => a.localeCompare(b))
}
const marketLanguageOptions = computed(() => uniqueFilterValues(market.value.flatMap(item => item.languages || [])))
const marketTagOptions = computed(() => uniqueFilterValues(market.value.flatMap(item => item.tags || [])))
const marketLicenseOptions = computed(() => uniqueFilterValues(market.value.map(item => item.license)))
const marketTrustOptions = computed(() => uniqueFilterValues(market.value.map(item => item.trust_level)))
const marketFiltersActive = computed(() => (
  marketProfile.value !== 'all'
  || marketLanguage.value !== 'all'
  || marketTag.value !== 'all'
  || marketLicense.value !== 'all'
  || marketTrust.value !== 'all'
))
const filteredMarket = computed(() => market.value.filter(item => (
  (marketProfile.value === 'all' || item.content_profile === marketProfile.value)
  && (marketLanguage.value === 'all' || (item.languages || []).includes(marketLanguage.value))
  && (marketTag.value === 'all' || (item.tags || []).includes(marketTag.value))
  && (marketLicense.value === 'all' || item.license === marketLicense.value)
  && (marketTrust.value === 'all' || item.trust_level === marketTrust.value)
)))
function resetMarketFilters() {
  marketProfile.value = 'all'
  marketLanguage.value = 'all'
  marketTag.value = 'all'
  marketLicense.value = 'all'
  marketTrust.value = 'all'
}
const loading = ref(false)
const importFile = ref<File | null>(null)
const preview = ref<ModulePreviewResponse | null>(null)
const importBusy = ref(false)
const recoveryCheck = ref<RecoveryCheck | null>(null)
const recoveryBusy = ref(false)
const totalContent = (module: ModuleSummary) => module.content_counts == null ? null : Object.values(module.content_counts)
  .reduce((total, count) => total + count, 0)
function inventorySummary(module: ModuleSummary): string {
  const inventory = moduleInventory(module)
  if (inventory === 'unknown') return copy.value.inventoryUnknown
  if (inventory === 'adventures') return copy.value.adventureSummary(module.adventure_count || 0)
  const count = totalContent(module)
  return count == null ? copy.value.resourceBrowse : copy.value.resourceSummary(count)
}

// The online shelf is the plugin marketplace narrowed to content packs, so
// it uses the same card and Hub detail dialog as the plugin marketplace.
function marketCardItem(item: ModuleMarketplaceItem): PluginMarketplaceItem {
  return { ...item, version: item.latest_version || item.version, plugin_type: 'content-pack', latest: { version: item.latest_version } }
}

function openUrl(url?: string) {
  if (url) window.open(url, '_blank', 'noopener')
}

const hubBusy = ref('')
const {
  hubDetail, hubReadmeHtml, hubDetailOpen, hubDetailLoading, hubReadmeLoading, hubRating, hubRatingSummary,
  openHubDetail, toggleHubLike, saveHubRating,
} = useHubDetail(hubBusy)
const detailItem = ref<ModuleMarketplaceItem | null>(null)
const safeHubReadmeHtml = computed(() => DOMPurify.sanitize(hubReadmeHtml.value))
// The dialog shows the install spinner for `market:<id>`, like the plugin page.
const detailBusy = computed(() => hubBusy.value || (installBusy.value ? `market:${installBusy.value}` : ''))

function openMarketDetail(item: ModuleMarketplaceItem) {
  detailItem.value = item
  void openHubDetail(marketCardItem(item))
}

function installDetailItem() {
  if (detailItem.value) void installFromMarketplace(detailItem.value)
}

// After an install the shelf reloads; keep the open dialog on the fresh entry.
watch(market, items => {
  const fresh = detailItem.value && items.find(item => item.id === detailItem.value?.id)
  if (!fresh) return
  detailItem.value = fresh
  if (hubDetail.value?.id === fresh.id) {
    hubDetail.value = { ...hubDetail.value, installed: fresh.installed, installed_version: fresh.installed_version, installable: fresh.installable }
  }
})

// Index languages are locale codes; unknown codes are shown as-is.
function languageLabel(language: string): string {
  return (SUPPORTED_LOCALES as readonly string[]).includes(language) ? localeLabel(language as Locale) : language
}

// 信任级别只用来给筛选项起名字；卡片上的展示归 MarketplaceCard。
function marketTrustLabel(level: string): string {
  if (level === 'official') return t('pluginTrustOfficial')
  if (level === 'verified') return t('pluginTrustVerified')
  return t('pluginTrustCommunity')
}

// §8 Recovery：入口带过来的是**存档里的绑定身份**（adventure / version /
// content_digest / source），恢复完成的判定完全交给服务端：重新读取该局的冒险
// 投影，只有投影可用且指纹与来源都一致才算恢复，绝不自动绑定"同 id 不同包"。
const recoveryAdventure = computed(() => String(route.query.adventure || '').trim())
const recoveryVersion = computed(() => String(route.query.version || '').trim())
const recoveryDigest = computed(() => String(route.query.digest || '').trim())
const recoverySourceKind = computed(() => String(route.query.source_kind || '').trim())
const recoverySourceId = computed(() => String(route.query.source_id || '').trim())
const recoveryGame = computed(() => String(route.query.game || '').trim())
const recoveryModuleId = computed(() => (
  recoverySourceKind.value === 'plugin' ? recoverySourceId.value : ''
))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await moduleApi.list()
    modules.value = result.modules || []
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

async function loadMarketplace() {
  marketBusy.value = true
  marketError.value = ''
  try {
    const result = await moduleApi.marketplace(marketKeyword.value, marketRuleset.value)
    if (!result.ok) {
      market.value = []
      marketError.value = result.error || t('modulesOnlineUnavailable', { error: result.error_code || '' })
      return
    }
    market.value = result.modules || []
    marketSource.value = result.source || null
  } catch (cause: unknown) {
    market.value = []
    marketError.value = errorMessage(cause)
  } finally {
    marketBusy.value = false
  }
}

onMounted(() => {
  void load()
  void loadMarketplace()
  void checkRecovery()
})

// 恢复判定必须跟着**整组**恢复身份走：hash 导航（同页不同 query）不会重新挂载
// 组件，只 watch game 会让上一次的结论残留下来。
watch(
  [recoveryAdventure, recoveryVersion, recoveryDigest, recoverySourceKind, recoverySourceId, recoveryGame],
  () => { void checkRecovery() },
)

function selectImportFile(event: Event) {
  importFile.value = (event.target as HTMLInputElement).files?.[0] || null
  preview.value = null
}

function importForm() {
  const form = new FormData()
  if (importFile.value) form.append('file', importFile.value)
  return form
}

async function previewImport() {
  if (!importFile.value) return
  importBusy.value = true
  error.value = ''
  try {
    preview.value = await moduleApi.previewImport(importForm())
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    importBusy.value = false
  }
}

async function importModule() {
  if (!preview.value || preview.value.blockers.length) return
  importBusy.value = true
  error.value = ''
  try {
    await moduleApi.import(importForm())
    importFile.value = null
    preview.value = null
    await load()
    await checkRecovery()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    importBusy.value = false
  }
}

async function installFromMarketplace(item: ModuleMarketplaceItem) {
  installBusy.value = item.id
  installFeedback.value = { id: item.id, kind: 'busy', message: t('modulesInstallingOnline') }
  error.value = ''
  try {
    const result = await moduleApi.installFromMarketplace(item.id, item.installed)
    if (!result.ok) throw new Error(result.error || result.error_code || t('modulesInstallIncomplete'))
    installFeedback.value = { id: item.id, kind: 'success', message: t('modulesInstalledRefreshing') }
    await load()
    await loadMarketplace()
    await checkRecovery()
    installFeedback.value = { id: item.id, kind: 'success', message: t('modulesInstalledRefreshed') }
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
    installFeedback.value = { id: item.id, kind: 'error', message: errorMessage(cause) }
  } finally {
    installBusy.value = ''
  }
}

// 投影的 reason code → 文案键（显式映射，不做字符串拼接）。
const RECOVERY_REASON_KEYS: Record<string, MessageKey> = {
  package_unavailable: 'modulesRecoveryReason_package_unavailable',
  binding_changed: 'modulesRecoveryReason_binding_changed',
  source_changed: 'modulesRecoveryReason_source_changed',
  source_conflict: 'modulesRecoveryReason_source_conflict',
  package_invalid: 'modulesRecoveryReason_package_invalid',
}

function recoveryReasonLabel(reason: string): string {
  return t(RECOVERY_REASON_KEYS[reason] || 'modulesRecoveryReason_unknown')
}

async function checkRecovery() {
  const game = recoveryGame.value
  recoveryCheck.value = null
  if (!game) return
  recoveryBusy.value = true
  try {
    const response = await api<{ adventure: RecoveryAdventure | null }>(
      `/games/${encodeURIComponent(game)}/adventure`,
    )
    const adventure = response.adventure || null
    const binding = adventure?.binding || {}
    const digestMatches = !recoveryDigest.value
      || String(binding.content_digest || '') === recoveryDigest.value
    const sourceMatches = !recoverySourceKind.value
      || (String(binding.source_kind || '') === recoverySourceKind.value
        && String(binding.source_id || '') === recoverySourceId.value)
    recoveryCheck.value = {
      ready: Boolean(adventure?.available) && digestMatches && sourceMatches,
      reason: String(adventure?.reason || ''),
      digestMatches,
      sourceMatches,
    }
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    recoveryBusy.value = false
  }
}
</script>

<template>
  <section class="view modules-page">
    <ContentPageHeader
      class="modules-heading"
      kicker="CONTENT PACKS"
      :title="recoveryAdventure ? library.sourceTitle : library.sources"
      :description="library.sourceIntro"
    >
      <template #actions>
        <RouterLink class="content-button modules-return-link" :to="{ name: 'library' }">{{ library.backLibrary }}</RouterLink>
      </template>
    </ContentPageHeader>
    <p v-if="!recoveryAdventure" class="module-boundary-note">{{ t('modulesIntro') }} {{ t('modulesBoundaryNote') }}</p>

    <section v-if="recoveryAdventure" class="module-recovery" role="status" data-testid="modules-recovery">
      <strong>{{ t('modulesRecoveryTitle') }}</strong>
      <p>{{ t('modulesRecoveryHint', { adventure: recoveryAdventure, version: recoveryVersion || t('modulesRecoveryUnknownVersion') }) }}</p>
      <dl class="module-recovery-facts">
        <div v-if="recoveryDigest"><dt>{{ t('modulesRecoveryDigest') }}</dt><dd>{{ recoveryDigest }}</dd></div>
        <div v-if="recoverySourceKind"><dt>{{ t('modulesRecoverySource') }}</dt><dd>{{ recoverySourceKind }} · {{ recoverySourceId || '—' }}</dd></div>
        <div v-if="recoveryModuleId"><dt>{{ t('modulesRecoveryModule') }}</dt><dd>{{ recoveryModuleId }}</dd></div>
      </dl>
      <p v-if="!recoveryGame" class="muted">{{ t('modulesRecoveryNeedGame') }}</p>
      <template v-else>
        <p v-if="recoveryBusy" class="muted">{{ t('modulesRecoveryChecking') }}</p>
        <template v-else-if="recoveryCheck">
          <p v-if="recoveryCheck.ready" class="module-recovery-ready" data-testid="modules-recovery-ready">
            {{ t('modulesRecoveryReady') }}
          </p>
          <template v-else>
            <p class="error-text" data-testid="modules-recovery-blocked">
              {{ t('modulesRecoveryNotReady', { reason: recoveryReasonLabel(recoveryCheck.reason) }) }}
            </p>
            <p v-if="!recoveryCheck.digestMatches" class="error-text">{{ t('modulesRecoveryDigestMismatch') }}</p>
            <p v-if="!recoveryCheck.sourceMatches" class="error-text">{{ t('modulesRecoverySourceMismatch') }}</p>
          </template>
        </template>
        <div class="module-recovery-actions">
          <button type="button" :disabled="recoveryBusy" @click="checkRecovery">{{ t('modulesRecoveryCheck') }}</button>
          <RouterLink v-if="recoveryCheck?.ready" class="primary module-recovery-resume" :to="{ name: 'play', query: { game: recoveryGame } }">
            {{ t('modulesRecoveryResume') }}
          </RouterLink>
        </div>
      </template>
    </section>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section id="modules-installed" class="modules-section" data-testid="modules-installed">
      <div class="section-title-row"><h2>{{ t('modulesSectionInstalled') }}</h2><button type="button" :disabled="loading" @click="load">{{ t('refresh') }}</button></div>
      <p v-if="loading" class="muted">{{ t('modulesLoading') }}</p>
      <div v-else-if="!modules.length && !error" class="empty-state">
          <p>{{ t('modulesEmpty') }}</p>
      </div>
      <div v-else class="modules-grid">
        <RouterLink v-for="module in modules" :key="module.id" :to="{ name: 'module-detail', params: { moduleId: module.id } }" class="module-card">
          <div>
            <p class="eyebrow">{{ moduleCategoryLabel(module, copy) }}</p>
            <h3>{{ module.name }}</h3>
            <p class="muted">{{ t('modulesVersion', { version: module.version }) }}</p>
            <p v-if="module.status === 'disabled'" class="muted">{{ t('modulesDisabledBadge') }}</p>
            <p class="muted">{{ inventorySummary(module) }}</p>
            <p v-if="module.status === 'disabled' && moduleInventory(module) === 'adventures'" class="muted">{{ copy.disabledAdventures }}</p>
          </div>
          <dl>
            <div><dt>{{ t('modulesAdventures') }}</dt><dd>{{ module.adventure_count ?? '—' }}</dd></div>
            <div><dt>{{ copy.resourceContributions }}</dt><dd>{{ totalContent(module) ?? '—' }}</dd></div>
          </dl>
        </RouterLink>
      </div>
    </section>

    <section id="modules-local-import" class="modules-section" data-testid="modules-local-import">
      <h2>{{ t('modulesSectionLocal') }}</h2>
      <div class="module-import" :aria-label="t('modulesImport')">
        <label class="module-import-file">{{ t('modulesImport') }} <input type="file" accept=".dfplugin" @change="selectImportFile"></label>
        <button type="button" :disabled="!importFile || importBusy" @click="previewImport">{{ t('modulesPreview') }}</button>
        <button v-if="preview" type="button" :disabled="Boolean(preview.blockers.length) || importBusy" @click="importModule">{{ t('modulesInstall') }}</button>
      </div>
      <div v-if="preview && (preview.blockers.length || preview.warnings.length)" class="module-import-preview" data-testid="module-import-preview">
        <p v-if="preview.blockers.length" class="error-text">{{ preview.blockers.join(', ') }}</p>
        <p v-if="preview.warnings.length" class="muted">{{ preview.warnings.join(', ') }}</p>
      </div>
    </section>

    <section id="modules-online" class="modules-section" data-testid="modules-online">
      <div class="section-title-row">
        <h2>{{ t('modulesSectionOnline') }}</h2>
        <button type="button" class="module-submit-guide-link" data-testid="modules-submit-guide-open" @click="submitGuideOpen = true">{{ t('modulesSubmitGuideLink') }}</button>
      </div>
      <div class="module-market-search">
        <input v-model="marketKeyword" type="search" :placeholder="t('modulesOnlineSearchPlaceholder')" :aria-label="t('modulesOnlineSearch')" @keyup.enter="loadMarketplace">
        <label class="module-market-filter">
          <span>{{ t('modulesOnlineFilter') }}</span>
          <select v-model="marketRuleset" :aria-label="t('modulesOnlineFilter')" @change="loadMarketplace">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option value="dnd2024">D&amp;D 2024</option>
            <option value="coc">CoC</option>
            <option value="freeform">Freeform</option>
          </select>
        </label>
        <button type="button" :disabled="marketBusy" @click="loadMarketplace">{{ t('modulesOnlineSearch') }}</button>
      </div>
      <p v-if="marketBusy" class="muted">{{ t('modulesLoading') }}</p>
      <p v-else-if="marketError" class="error-text">{{ marketError }}</p>
      <div v-else-if="!market.length" class="empty-state" data-testid="modules-online-empty">
        <p class="muted">{{ t('modulesOnlineEmpty') }}</p>
        <p class="muted">{{ t('modulesOnlineEmptyHint') }}</p>
        <RouterLink :to="{ name: 'creator' }" class="module-submit-action">{{ t('modulesSubmitGoCreator') }}</RouterLink>
      </div>
      <template v-else>
        <div class="module-market-filters" data-testid="modules-market-filters">
          <label class="module-market-filter">
            <span>{{ t('modulesFilterProfile') }}</span>
            <select v-model="marketProfile">
              <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
              <option value="adventure-module">{{ copy.adventureLabel }}</option>
              <option value="content-pack">{{ copy.resourceLabel }}</option>
            </select>
          </label>
          <label v-if="marketLanguageOptions.length" class="module-market-filter">
            <span>{{ t('modulesFilterLanguage') }}</span>
            <select v-model="marketLanguage">
              <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
              <option v-for="language in marketLanguageOptions" :key="language" :value="language">{{ languageLabel(language) }}</option>
            </select>
          </label>
          <label v-if="marketTagOptions.length" class="module-market-filter">
            <span>{{ t('modulesFilterTag') }}</span>
            <select v-model="marketTag">
              <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
              <option v-for="tag in marketTagOptions" :key="tag" :value="tag">{{ tag }}</option>
            </select>
          </label>
          <label v-if="marketLicenseOptions.length" class="module-market-filter">
            <span>{{ t('modulesFilterLicense') }}</span>
            <select v-model="marketLicense">
              <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
              <option v-for="license in marketLicenseOptions" :key="license" :value="license">{{ license }}</option>
            </select>
          </label>
          <label v-if="marketTrustOptions.length" class="module-market-filter">
            <span>{{ t('modulesFilterTrust') }}</span>
            <select v-model="marketTrust">
              <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
              <option v-for="level in marketTrustOptions" :key="level" :value="level">{{ marketTrustLabel(level) }}</option>
            </select>
          </label>
          <button v-if="marketFiltersActive" type="button" class="module-filter-reset" data-testid="modules-filter-reset" @click="resetMarketFilters">{{ t('modulesFilterReset') }}</button>
        </div>
        <div v-if="!filteredMarket.length" class="empty-state" data-testid="modules-online-filtered-empty">
          <p class="muted">{{ t('modulesFilteredEmpty') }}</p>
          <button type="button" class="module-filter-reset" @click="resetMarketFilters">{{ t('modulesFilterReset') }}</button>
        </div>
        <div v-else class="module-market-grid">
        <MarketplaceCard
          v-for="item in filteredMarket"
          :key="item.id"
          :item="marketCardItem(item)"
          :icon="CubeOutline"
          :type-label="moduleCategoryLabel(item, copy)"
          :show-hub-stats="Boolean(marketSource?.hub)"
          :has-newer-version="item.update_available"
          :installing="installBusy === item.id"
          :data-testid="`modules-market-${item.id}`"
          @install="installFromMarketplace(item)"
          @open-url="openUrl"
          @detail="openMarketDetail(item)"
        >
          <template #summary>
            <p class="muted module-market-summary">{{ item.adventure_count > 0 ? copy.adventureSummary(item.adventure_count) : copy.resourceBrowse }}</p>
            <p v-if="item.ruleset_targets.length" class="muted module-market-summary">{{ t('modulesRuntimeTargets') }}: {{ item.ruleset_targets.join(' · ') }}</p>
          </template>
          <template #tags>
            <NTag v-for="language in item.languages" :key="language" size="small">{{ languageLabel(language) }}</NTag>
          </template>
          <template #footer>
            <p v-if="installFeedback?.id === item.id" class="module-install-feedback" :class="`is-${installFeedback.kind}`" role="status">{{ installFeedback.message }}</p>
          </template>
        </MarketplaceCard>
        </div>
      </template>
    </section>

    <HubDetailModal
      v-model:show="hubDetailOpen"
      :title="t('modulesDetailTitle')"
      :description-title="t('modulesDetailDescription')"
      :hub-detail="hubDetail"
      :hub-detail-loading="hubDetailLoading"
      :hub-readme-loading="hubReadmeLoading"
      :hub-rating="hubRating"
      :hub-rating-summary="hubRatingSummary"
      :busy="detailBusy"
      :safe-hub-readme-html="safeHubReadmeHtml"
      :plugin-type-icon="() => CubeOutline"
      :plugin-type-label="() => (detailItem ? moduleCategoryLabel(detailItem, copy) : copy.packLabel)"
      :market-item-has-newer-version="() => Boolean(detailItem?.update_available)"
      :install-market-plugin="installDetailItem"
      :open-url="openUrl"
      :toggle-hub-like="toggleHubLike"
      :save-hub-rating="saveHubRating"
    />

    <Modal v-if="submitGuideOpen" :title="t('contentPackSubmitTitle')" dialog-class="module-submit-guide" close-test-id="modules-submit-guide-close" @close="submitGuideOpen = false">
      <div data-testid="modules-submit-guide">
        <p class="muted">{{ t('modulesSubmitGuideIntro') }}</p>
        <ol class="module-submit-steps">
          <li>{{ t('contentPackSubmitStepExport') }}</li>
          <li>{{ t('contentPackSubmitStepForm') }}</li>
          <li>{{ t('contentPackSubmitStepReview') }}</li>
        </ol>
        <footer class="module-submit-actions">
          <RouterLink :to="{ name: 'creator' }" class="module-submit-action" data-testid="modules-submit-go-creator" @click="submitGuideOpen = false">{{ t('modulesSubmitGoCreator') }}</RouterLink>
          <a :href="directSubmissionUrl" class="module-submit-action is-primary" target="_blank" rel="noopener" data-testid="modules-submit-direct">{{ t('modulesSubmitDirect') }}</a>
        </footer>
      </div>
    </Modal>
  </section>
</template>

<style scoped>
.modules-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }
/* Same grid as the plugin marketplace, which these cards come from. */
.module-market-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 13px; align-items: stretch; }
.module-market-summary { margin: 5px 0 0; }
@media (max-width: 1120px) { .module-market-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 800px) { .module-market-grid { grid-template-columns: minmax(0, 1fr); } }
.modules-heading { position: relative; display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 1rem; isolation: isolate; padding: 24px 0 20px; border-bottom: 1px solid color-mix(in srgb, var(--df-accent) 22%, var(--df-border-soft)); }
.modules-heading::before { position: absolute; z-index: -1; inset: 0 0 0 30%; content: ''; background: linear-gradient(90deg, var(--df-canvas), transparent 72%), var(--df-bg-atmosphere-image) center / cover; mask-image: linear-gradient(90deg, transparent, black 42%, transparent); opacity: .2; pointer-events: none; }
.modules-heading .eyebrow { margin: 0; color: var(--df-interactive-strong); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.modules-heading h1 { margin: 7px 0 9px; color: var(--df-accent-strong); font-family: var(--df-font-title); font-size: clamp(30px, 3vw, 42px); line-height: 1.18; letter-spacing: .02em; }
.modules-heading .muted { margin: .25rem 0 0; max-width: 52rem; }
.module-boundary-note { margin: .55rem 0 0; max-width: 58rem; color: var(--df-accent-strong); font-size: .82rem; }
.module-empty-links { margin: .3rem 0 0; }
.modules-section { display: grid; gap: .9rem; margin-top: 2rem; }
.section-title-row { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.module-submit-guide-link { padding: 0; border: 0; background: none; color: var(--df-interactive-strong); font-size: .85rem; font-weight: 700; cursor: pointer; }
.module-submit-guide-link:hover, .module-submit-guide-link:focus-visible { text-decoration: underline; }
.module-submit-steps { display: grid; gap: .45rem; margin: .8rem 0 0; padding-left: 1.2rem; color: var(--df-text-secondary); line-height: 1.5; }
.module-submit-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .6rem; padding-top: 1rem; }
.module-submit-action { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 0 .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text); background: var(--df-surface-1); font-weight: 700; text-decoration: none; }
.module-submit-action.is-primary { border-color: color-mix(in srgb, var(--df-accent) 50%, var(--df-border-soft)); color: var(--df-accent-strong); background: color-mix(in srgb, var(--df-accent) 12%, var(--df-surface-1)); }
.empty-state .module-submit-action { justify-self: start; }
.modules-section h2 { margin: 0; font-size: 1.05rem; }
.module-import, .module-market-search { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; padding: 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-2); }
.module-market-filter { display: inline-flex; align-items: center; gap: .45rem; color: var(--df-text-muted); font-size: .85rem; }
.module-market-filter span { white-space: nowrap; }
.module-market-filter select { min-height: 34px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text); background: var(--df-surface-1); }
.module-market-search input { flex: 1 1 12rem; min-width: 0; }
/* 导入行只放「选文件 + 预览 + 安装」三件控件；预览结果单独成块，
   否则结果段落会变成同一行里的 flex item，把按钮挤歪。 */
.module-import-file { display: inline-flex; flex: 0 0 auto; flex-wrap: nowrap; align-items: center; gap: .55rem; min-width: 0; min-height: 36px; color: var(--df-text-muted); font-size: .85rem; white-space: nowrap; }
.module-import-file input[type="file"] { flex: 0 1 22rem; min-width: 8rem; max-width: 22rem; }
.module-import button { min-height: 36px; }
.module-import-preview { display: grid; gap: .35rem; margin: 0; padding: .7rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.module-import-preview p { margin: 0; }
.module-market-filters { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem .9rem; padding: .75rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.module-filter-reset { align-self: center; }
.module-recovery { margin: 1rem 0; padding: 1rem; border: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); border-left: 3px solid var(--df-accent); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.module-recovery p { margin: .4rem 0 0; }
.module-recovery-facts { display: grid; gap: .25rem; margin: .6rem 0 0; }
.module-recovery-facts dt { color: var(--df-text-muted); font-size: .78rem; }
.module-recovery-facts dd { margin: 0; font-size: .82rem; overflow-wrap: anywhere; }
.module-recovery-ready { color: var(--df-success); }
.module-recovery-actions { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .7rem; }
.module-recovery-resume { display: inline-flex; align-items: center; padding: .35rem .8rem; border-radius: .6rem; text-decoration: none; }
.module-card { display: grid; gap: 1.1rem; padding: 1.35rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); color: var(--df-text); text-decoration: none; }
.module-card h3 { margin: 0; font-size: 1rem; }
.module-card dl { display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 0; }
.module-card dt { color: var(--df-text-muted); font-size: .85rem; }
.module-card dd { margin: .2rem 0 0; font-size: 1.15rem; font-weight: 700; }
.module-market-actions { display: grid; gap: .45rem; align-content: start; margin-top: auto; }
.module-market-actions button { width: 100%; }
.module-installed-badge { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 0 .8rem; border: 1px solid color-mix(in srgb, var(--df-success) 42%, var(--df-border-soft)); border-radius: var(--df-radius-md); color: var(--df-success); background: color-mix(in srgb, var(--df-success) 8%, var(--df-surface-1)); font-size: .82rem; font-weight: 700; }
.module-install-feedback { margin: 0; color: var(--df-text-muted); font-size: .75rem; line-height: 1.35; }
.module-install-feedback.is-success { color: var(--df-success); }
.module-install-feedback.is-error { color: var(--df-danger, #e07a7a); }
@media (max-width: 480px) {
  .modules-heading { align-items: stretch; flex-direction: column; }
  .modules-return-link { width: 100%; min-height: 44px; justify-content: center; }
  .module-import, .module-market-search { align-items: stretch; flex-direction: column; }
  .module-import label, .module-import button, .module-market-search button { width: 100%; }
  .module-import-file { flex-direction: column; align-items: flex-start; gap: .3rem; }
  /* 竖排后 flex-basis 会变成高度，必须显式取消，否则文件框会被拉成一整屏。 */
  .module-import-file input[type="file"] { flex: none; }
  .module-import input[type="file"] { display: block; width: 100%; margin-top: .45rem; }
  .module-market-search input { flex: 0 0 auto; width: 100%; min-height: 36px; }
  .modules-grid { grid-template-columns: 1fr; }
  .module-card { padding: 1rem; }
}
</style>
