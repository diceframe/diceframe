<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, apiUrl, errorMessage } from '@/api/client'
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
import { CubeOutline } from '@vicons/ionicons5'
import type { PluginMarketplaceItem } from '@/api/types'
import ContentPackCard from './ContentPackCard.vue'
import HubDetailModal from '@/features/plugins/modals/HubDetailModal.vue'
import { promptEnableAfterInstall } from '@/features/plugins/installEnable'
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
// 规则下拉的选项从数据推导（不再写死 D&D/CoC/Freeform）：服务端按规则收窄后
// 列表会变小，所以把见过的规则值累计下来，选项不会越筛越少。
const UNLABELLED_RULESET = '__undeclared__'
const marketRulesetSeen = ref<string[]>([])
const marketProfile = ref('all')
const marketLanguage = ref('all')
const marketTag = ref('all')
const marketLicense = ref('all')
const marketTrust = ref('all')
const marketBusy = ref(false)
const installBusy = ref('')
const updateBusy = ref('')
const updateAllBusy = ref(false)
const marketSource = ref<ModuleMarketplaceResponse['source'] | null>(null)
const installFeedback = ref<{ id: string; kind: 'busy' | 'success' | 'error'; message: string } | null>(null)
const submitGuideOpen = ref(false)
const directSubmissionUrl = contentPackSubmissionUrl()
const error = ref('')

// ---- App Store 分类 chip ----
type MarketCategory = 'all' | 'adventure' | 'rules' | 'official' | string
const activeCategory = ref<MarketCategory>('all')

// 精选横幅：从 official/verified 里随机取一个，没有则从评分前5随机
const featuredItem = computed(() => {
  const items = market.value
  if (!items.length) return null
  const featured = items.filter(item => item.trust_level === 'official' || item.trust_level === 'verified')
  if (featured.length) return featured[Math.floor(Math.random() * featured.length)]
  const topRated = [...items].sort((a, b) => (b.stats?.rating_average || 0) - (a.stats?.rating_average || 0)).slice(0, 5)
  return topRated[Math.floor(Math.random() * topRated.length)] || null
})

// 应用分类筛选后的列表
const categoryFiltered = computed(() => {
  const all = filteredMarket.value
  if (activeCategory.value === 'all') return all
  if (activeCategory.value === 'adventure') return all.filter(item => (item.capabilities || []).includes('adventure'))
  if (activeCategory.value === 'rules') return all.filter(item => (item.capabilities || []).includes('rules'))
  if (activeCategory.value === 'official') return all.filter(item => item.trust_level === 'official')
  // ruleset 筛选
  return all.filter(item => (item.ruleset_targets || []).some(target => {
    const short = target.includes(':') ? target.split(':').slice(1).join(':') : target
    return short === activeCategory.value
  }))
})

// 货架分区
const shelfAdventure = computed(() => sortedMarket.value.filter(item => (item.capabilities || []).includes('adventure')).slice(0, 12))
const shelfTopDownloads = computed(() => [...market.value].sort((a, b) => (b.stats?.downloads_total || 0) - (a.stats?.downloads_total || 0)).slice(0, 12))
const shelfOfficial = computed(() => market.value.filter(item => item.trust_level === 'official' || item.trust_level === 'verified').slice(0, 12))
const hasRulesItems = computed(() => market.value.some(item => (item.capabilities || []).includes('rules')))

// 我的内容包面板
const myPanelOpen = ref(false)
// 筛选面板展开状态
const showFilters = ref(false)
// 跳转前保存面板状态，回来后恢复
watch(myPanelOpen, v => { try { sessionStorage.setItem('df_modules_panel', v ? '1' : '0') } catch { /* ignore */ } })
onMounted(() => {
  void load()
  void loadMarketplace()
  void checkRecovery()
  try { if (sessionStorage.getItem('df_modules_panel') === '1') myPanelOpen.value = true } catch { /* ignore */ }
})
function uniqueFilterValues(values: Array<string | undefined>): string[] {
  const unique = new Set(values.map(value => String(value || '').trim()).filter(Boolean))
  return [...unique].sort((a, b) => a.localeCompare(b))
}
const UNLABELLED_LANGUAGE = '__unlabelled__'
const marketLanguageOptions = computed(() => {
  const options = uniqueFilterValues(market.value.flatMap(item => item.languages || []))
  // 老包没有 supported_locales：给一个显式的「未标注」，不拿内容去猜语言。
  if (market.value.some(item => !(item.languages || []).length)) options.push(UNLABELLED_LANGUAGE)
  return options
})
function shortRuleset(target: string): string {
  const value = String(target || '').trim()
  return value.includes(':') ? value.split(':').slice(1).join(':') : value
}
// 老索引没有 ruleset_declared：按 target 列表是否为空推断，语义与新字段一致。
function itemHasDeclaredRuleset(item: ModuleMarketplaceItem): boolean {
  return item.ruleset_declared ?? (item.ruleset_targets || []).length > 0
}
function rulesetLabel(ruleset: string): string {
  if (ruleset === 'coc7') return 'CoC'
  if (ruleset === 'dnd2024') return 'D&D 2024'
  if (ruleset === 'freeform') return 'Freeform'
  return ruleset
}
// 兜底项：即使所有内容包都还没声明规则，规则筛选入口也不能消失。
// 顺序固定（与后端 alias 表一致：coc → coc7），其余声明值动态追加在后。
const CORE_RULESET_OPTIONS = ['dnd2024', 'coc7', 'freeform'] as const
const marketRulesetOptions = computed(() => {
  const seen = new Set<string>(CORE_RULESET_OPTIONS)
  for (const value of marketRulesetSeen.value) seen.add(value)
  const core = CORE_RULESET_OPTIONS.filter(value => seen.has(value))
  const extra = [...seen]
    .filter(value => !(CORE_RULESET_OPTIONS as readonly string[]).includes(value))
    .sort((a, b) => a.localeCompare(b))
  return [...core, ...extra]
})
const marketTagOptions = computed(() => uniqueFilterValues(market.value.flatMap(item => item.tags || [])))
const marketTrustOptions = computed(() => uniqueFilterValues(market.value.map(item => item.trust_level)))
const marketFiltersActive = computed(() => (
  marketRuleset.value !== 'all'
  || marketProfile.value !== 'all'
  || marketLanguage.value !== 'all'
  || marketTag.value !== 'all'
  || marketLicense.value !== 'all'
  || marketTrust.value !== 'all'
))
// 在线货架的排序。市场索引只保证这些计数存在，顺序由索引自己决定；玩家找
// 内容包时最先想按"装的人最多 / 点赞最多"看，所以在客户端排，不加接口参数。
type MarketSort = 'recommended' | 'downloads' | 'likes' | 'rating' | 'name'
const marketSort = ref<MarketSort>('recommended')
// 有任何 select 筛选器激活时，切到筛选结果视图
watch([marketProfile, marketLanguage, marketTag, marketLicense, marketTrust, marketSort], () => {
  if (marketFiltersActive.value) activeCategory.value = 'all'
})
const marketSortOptions: Array<{ value: MarketSort; key: 'modulesSortRecommended' | 'modulesSortDownloads' | 'modulesSortLikes' | 'modulesSortRating' | 'modulesSortName' }> = [
  { value: 'recommended', key: 'modulesSortRecommended' },
  { value: 'downloads', key: 'modulesSortDownloads' },
  { value: 'likes', key: 'modulesSortLikes' },
  { value: 'rating', key: 'modulesSortRating' },
  { value: 'name', key: 'modulesSortName' },
]
function marketStat(item: ModuleMarketplaceItem, key: 'downloads_total' | 'likes' | 'rating_average'): number {
  const value = item.stats?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}
const filteredMarket = computed(() => market.value.filter(item => (
  (marketRuleset.value === 'all'
    || (marketRuleset.value === UNLABELLED_RULESET
      ? !itemHasDeclaredRuleset(item)
      : item.system_agnostic
        || !itemHasDeclaredRuleset(item)
        || (item.ruleset_targets || []).some(target => shortRuleset(target) === marketRuleset.value)))
  && (marketProfile.value === 'all' || item.content_profile === marketProfile.value)
  && (marketLanguage.value === 'all'
    || (marketLanguage.value === UNLABELLED_LANGUAGE
      ? !(item.languages || []).length
      : (item.languages || []).includes(marketLanguage.value)))
  && (marketTag.value === 'all' || (item.tags || []).includes(marketTag.value))
  && (marketLicense.value === 'all' || item.license === marketLicense.value)
  && (marketTrust.value === 'all' || item.trust_level === marketTrust.value)
)))
const sortedMarket = computed(() => {
  if (marketSort.value === 'recommended') return filteredMarket.value
  const rows = [...filteredMarket.value]
  const byName = (a: ModuleMarketplaceItem, b: ModuleMarketplaceItem) => a.name.localeCompare(b.name, locale.value)
  rows.sort((a, b) => {
    if (marketSort.value === 'downloads') {
      return marketStat(b, 'downloads_total') - marketStat(a, 'downloads_total') || byName(a, b)
    }
    if (marketSort.value === 'likes') {
      // 点赞并列时用下载量兜底：都不动的话顺序会随索引抖动。
      return marketStat(b, 'likes') - marketStat(a, 'likes')
        || marketStat(b, 'downloads_total') - marketStat(a, 'downloads_total')
        || byName(a, b)
    }
    if (marketSort.value === 'rating') {
      return marketStat(b, 'rating_average') - marketStat(a, 'rating_average')
        || marketStat(b, 'downloads_total') - marketStat(a, 'downloads_total')
        || byName(a, b)
    }
    return byName(a, b)
  })
  return rows
})
function resetMarketFilters(): void {
  // 规则是服务端过滤的：只改本地值不会把被排除的包取回来，必须重新请求。
  const rulesetChanged = marketRuleset.value !== 'all'
  marketRuleset.value = 'all'
  marketProfile.value = 'all'
  marketLanguage.value = 'all'
  marketTag.value = 'all'
  marketLicense.value = 'all'
  marketTrust.value = 'all'
  marketSort.value = 'recommended'
  if (rulesetChanged) void loadMarketplace()
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

// 已安装包如果有封面，就用和存档卡相同的做法：整张图铺底 + 渐变压暗。
// 图片来自本机包目录（/api/modules/{id}/cover），不涉及任何第三方地址。
function moduleCoverStyle(module: ModuleSummary): Record<string, string> | undefined {
  if (!module.cover) return undefined
  const url = apiUrl(`/modules/${encodeURIComponent(module.id)}/cover?v=${encodeURIComponent(module.version)}`)
  return { '--df-bg-cover-image': `url("${url.replace(/"/g, '%22')}")` }
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

// 已安装 + 商店有更新版本：与插件页的「有更新」提示同源，数据来自同一份市场索引
// （update_available 由服务端用 installed_version 与 latest_version 比较得出）。
// 索引只在**未筛选**的响应上重建：用户搜关键词/切规则时列表会收窄，不能让
// 「有更新」提示跟着搜索结果消失或闪回。
const updatableModules = ref(new Map<string, ModuleMarketplaceItem>())

function rebuildUpdatableIndex(items: ModuleMarketplaceItem[]) {
  const next = new Map<string, ModuleMarketplaceItem>()
  for (const item of items) {
    if (item.update_available && item.installed) next.set(item.id, item)
  }
  updatableModules.value = next
}

async function updateModule(module: ModuleSummary) {
  if (updateBusy.value || updateAllBusy.value) return
  updateBusy.value = module.id
  error.value = ''
  try {
    const result = await moduleApi.update(module.id)
    if (!result.ok) throw new Error(t('modulesUpdateFailed'))
    // 更新会重新灌入包内容（用户改过的资料册由服务端按数据所有权保留），
    // 所以列表、市场索引与恢复判定都要重读一次。
    const next = new Map(updatableModules.value)
    next.delete(module.id)
    updatableModules.value = next
    await load()
    await loadMarketplace()
    await checkRecovery()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    updateBusy.value = ''
  }
}

async function updateAllModules() {
  if (updateAllBusy.value || updateBusy.value || !updatableModules.value.size) return
  updateAllBusy.value = true
  error.value = ''
  const failures: string[] = []
  const updated = new Set<string>()
  try {
    for (const moduleId of updatableModules.value.keys()) {
      updateBusy.value = moduleId
      try {
        const result = await moduleApi.update(moduleId)
        if (!result.ok) throw new Error(t('modulesUpdateFailed'))
        updated.add(moduleId)
      } catch (cause: unknown) {
        failures.push(`${moduleId}: ${errorMessage(cause)}`)
      } finally {
        updateBusy.value = ''
      }
    }
    const next = new Map(updatableModules.value)
    for (const moduleId of updated) next.delete(moduleId)
    updatableModules.value = next
    await load()
    await loadMarketplace()
    await checkRecovery()
    if (failures.length) error.value = failures.join('；')
  } finally {
    updateAllBusy.value = false
    updateBusy.value = ''
  }
}

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

let marketRequestSeq = 0

async function loadMarketplace() {
  const sequence = ++marketRequestSeq
  marketBusy.value = true
  marketError.value = ''
  try {
    const rulesetQuery = marketRuleset.value === UNLABELLED_RULESET ? 'all' : marketRuleset.value
    const result = await moduleApi.marketplace(marketKeyword.value, rulesetQuery)
    // 快速切换规则/重复点击时，旧响应不得覆盖新筛选结果。
    if (sequence !== marketRequestSeq) return
    if (!result.ok) {
      market.value = []
      marketError.value = result.error || t('modulesOnlineUnavailable', { error: result.error_code || '' })
      return
    }
    market.value = result.modules || []
    marketSource.value = result.source || null
    if (!marketKeyword.value.trim() && marketRuleset.value === 'all') {
      rebuildUpdatableIndex(market.value)
    }
    const seen = new Set(marketRulesetSeen.value)
    for (const item of market.value) {
      for (const target of item.ruleset_targets || []) {
        const short = shortRuleset(target)
        if (short) seen.add(short)
      }
    }
    marketRulesetSeen.value = [...seen]
  } catch (cause: unknown) {
    if (sequence !== marketRequestSeq) return
    market.value = []
    marketError.value = errorMessage(cause)
  } finally {
    if (sequence === marketRequestSeq) marketBusy.value = false
  }
}

onMounted(() => {
  void load()
  void loadMarketplace()
  void checkRecovery()
  try { if (sessionStorage.getItem('df_modules_panel') === '1') myPanelOpen.value = true } catch { /* ignore */ }
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
    const result = await moduleApi.import(importForm())
    if (result.ok) {
      // 本地导入同样是禁用状态，装完立刻问一次是否启用。
      await promptEnableAfterInstall({ id: result.id || '', name: result.name, enabled: result.enabled })
    }
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
    // 新装的内容包在宿主里是禁用状态：不启用既没有冒险来源也没有资料册。
    // 询问走全局 dialog，装完立刻离开本页也不会漏掉。
    if (!item.installed) {
      await promptEnableAfterInstall({ id: result.id || item.id, name: result.name || item.name, enabled: result.enabled })
    }
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
    <!-- 顶部栏：搜索 + 我的内容包 -->
    <div class="modules-topbar">
      <ContentPageHeader compact
        class="modules-heading"
        kicker="CONTENT PACKS"
        :title="recoveryAdventure ? library.sourceTitle : library.sources"
        :description="library.sourceIntro"
      >
        <template #actions>
          <RouterLink class="content-button modules-return-link" :to="{ name: 'library' }">{{ library.backLibrary }}</RouterLink>
        </template>
      </ContentPageHeader>
      <div class="modules-search-bar">
        <input v-model="marketKeyword" type="search" class="modules-search-input" :placeholder="t('modulesOnlineSearchPlaceholder')" @keyup.enter="loadMarketplace">
        <button type="button" class="modules-search-btn" :disabled="marketBusy" @click="loadMarketplace">{{ t('modulesOnlineSearch') }}</button>
        <div class="modules-my-panel-wrap">
          <button type="button" class="modules-my-btn" :class="{ active: myPanelOpen }" @click="myPanelOpen = !myPanelOpen">
            {{ locale === 'zh-CN' ? `我的内容包` : 'My packs' }}
            <span v-if="updatableModules.size" class="modules-my-count is-update">{{ updatableModules.size }}</span>
            <span v-else-if="modules.length" class="modules-my-count">{{ modules.length }}</span>
          </button>
          <!-- 我的内容包下拉面板 -->
          <div v-if="myPanelOpen" class="modules-my-panel" data-testid="modules-installed">
            <div class="modules-my-panel-header">
              <strong>{{ t('modulesSectionInstalled') }}</strong>
              <div class="modules-my-panel-actions">
                <button type="button" class="module-update-all-action" :disabled="loading || updateAllBusy || updateBusy !== '' || !updatableModules.size" @click="updateAllModules">
                  {{ t('modulesUpdateAll', { count: updatableModules.size }) }}
                </button>
                <button type="button" :disabled="loading || updateAllBusy" @click="load">{{ t('refresh') }}</button>
              </div>
            </div>
            <p v-if="loading" class="muted">{{ t('modulesLoading') }}</p>
            <p v-else-if="!modules.length" class="muted modules-my-empty">{{ t('modulesEmpty') }}</p>
            <div v-else class="modules-my-list">
              <RouterLink
                v-for="module in modules"
                :key="module.id"
                :to="{ name: 'module-detail', params: { moduleId: module.id } }"
                class="modules-my-row"
                :data-testid="`module-card-${module.id}`"
                @click="myPanelOpen = false"
              >
                <span class="modules-my-row-art" :style="moduleCoverStyle(module)" />
                <span class="modules-my-row-copy">
                  <strong>{{ module.name }}</strong>
                  <span class="muted">v{{ module.version }} · {{ inventorySummary(module) }}</span>
                </span>
                <span v-if="updateBusy === module.id || updateAllBusy" class="modules-my-row-updating">
                  <span class="modules-my-update-spinner" />
                  {{ locale === 'zh-CN' ? '更新中…' : 'Updating…' }}
                </span>
                <button v-else-if="updatableModules.has(module.id)" type="button" class="module-update-action" :disabled="updateBusy !== '' || updateAllBusy" @click.prevent.stop="updateModule(module)">
                  {{ t('modulesUpdateCta') }}
                </button>
              </RouterLink>
            </div>
            <div class="modules-my-panel-footer">
              <label class="module-import-file">
                {{ t('modulesSectionLocal') }}
                <input type="file" accept=".dfplugin" @change="selectImportFile">
              </label>
              <template v-if="importFile">
                <button type="button" :disabled="importBusy" @click="previewImport">{{ t('modulesPreview') }}</button>
                <button v-if="preview" type="button" :disabled="Boolean(preview.blockers.length) || importBusy" @click="importModule">{{ t('modulesInstall') }}</button>
              </template>
              <div v-if="preview && (preview.blockers.length || preview.warnings.length)" class="module-import-preview" data-testid="module-import-preview">
                <p v-if="preview.blockers.length" class="error-text">{{ preview.blockers.join(', ') }}</p>
                <p v-if="preview.warnings.length" class="muted">{{ preview.warnings.join(', ') }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <p v-if="!recoveryAdventure" class="module-boundary-note">{{ t('modulesIntro') }} {{ t('modulesBoundaryNote') }}</p>
    <p v-if="error" class="error-text">{{ error }}</p>

    <!-- 恢复模式 -->
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
          <p v-if="recoveryCheck.ready" class="module-recovery-ready" data-testid="modules-recovery-ready">{{ t('modulesRecoveryReady') }}</p>
          <template v-else>
            <p class="error-text" data-testid="modules-recovery-blocked">{{ t('modulesRecoveryNotReady', { reason: recoveryReasonLabel(recoveryCheck.reason) }) }}</p>
            <p v-if="!recoveryCheck.digestMatches" class="error-text">{{ t('modulesRecoveryDigestMismatch') }}</p>
            <p v-if="!recoveryCheck.sourceMatches" class="error-text">{{ t('modulesRecoverySourceMismatch') }}</p>
          </template>
        </template>
        <div class="module-recovery-actions">
          <button type="button" :disabled="recoveryBusy" @click="checkRecovery">{{ t('modulesRecoveryCheck') }}</button>
          <RouterLink v-if="recoveryCheck?.ready" class="primary module-recovery-resume" :to="{ name: 'play', query: { game: recoveryGame } }">{{ t('modulesRecoveryResume') }}</RouterLink>
        </div>
      </template>
    </section>

    <!-- 搜索结果模式 -->
    <template v-if="marketKeyword.trim()">
      <p v-if="marketBusy" class="muted">{{ t('modulesLoading') }}</p>
      <p v-else-if="marketError" class="error-text">{{ marketError }}</p>
      <div v-else-if="!filteredMarket.length" class="empty-state">
        <p class="muted">{{ t('modulesFilteredEmpty') }}</p>
        <button type="button" class="module-filter-reset" @click="marketKeyword = ''">{{ t('modulesFilterReset') }}</button>
      </div>
      <div v-else class="modules-search-grid" data-testid="modules-online">
        <ContentPackCard
          v-for="item in filteredMarket.slice(0, 40)"
          :key="item.id"
          :item="item"
          :installing="installBusy === item.id"
          :has-newer-version="item.update_available"
          :show-hub-stats="Boolean(marketSource?.hub)"
          :data-testid="`modules-market-${item.id}`"
          @install="installFromMarketplace(item)"
          @detail="openMarketDetail(item)"
        />
      </div>
    </template>

    <!-- App Store 浏览模式 -->
    <template v-else-if="!marketBusy && !marketError">
      <!-- 分类 chips -->
      <div class="modules-category-chips" data-testid="modules-category-chips">
        <button type="button" :class="['modules-category-chip', { active: activeCategory === 'all' }]" @click="activeCategory = 'all'">{{ locale === 'zh-CN' ? '全部' : 'All' }}</button>
        <button type="button" :class="['modules-category-chip', { active: activeCategory === 'adventure' }]" @click="activeCategory = 'adventure'">{{ locale === 'zh-CN' ? '冒险模组' : 'Adventures' }}</button>
        <button v-if="hasRulesItems" type="button" :class="['modules-category-chip', { active: activeCategory === 'rules' }]" @click="activeCategory = 'rules'">{{ locale === 'zh-CN' ? '规则·素材' : 'Rules & Assets' }}</button>
        <button type="button" :class="['modules-category-chip', { active: activeCategory === 'official' }]" @click="activeCategory = 'official'">{{ locale === 'zh-CN' ? '官方认证' : 'Official' }}</button>
        <button v-for="ruleset in marketRulesetOptions.slice(0, 3)" :key="ruleset" type="button" :class="['modules-category-chip', { active: activeCategory === ruleset }]" @click="activeCategory = ruleset">{{ rulesetLabel(ruleset) }}</button>
        <button type="button" class="modules-submit-link" @click="submitGuideOpen = true">{{ t('modulesSubmitGuideLink') }}</button>
        <button type="button" :class="['modules-filter-toggle', { active: showFilters || marketFiltersActive }]" @click="showFilters = !showFilters">
          {{ locale === 'zh-CN' ? (marketFiltersActive ? `筛选 (${[marketProfile !== 'all', marketLanguage !== 'all', marketTag !== 'all', marketLicense !== 'all', marketTrust !== 'all'].filter(Boolean).length})` : '筛选 ▾') : (marketFiltersActive ? `Filters (${[marketProfile !== 'all', marketLanguage !== 'all', marketTag !== 'all', marketLicense !== 'all', marketTrust !== 'all'].filter(Boolean).length})` : 'Filter ▾') }}
        </button>
      </div>

      <!-- 筛选面板（展开时显示） -->
      <div v-if="showFilters" class="modules-filter-panel" data-testid="modules-market-filters">
        <label v-if="marketLanguageOptions.length" class="module-market-filter">
          <span>{{ t('modulesFilterLanguage') }}</span>
          <select v-model="marketLanguage">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option v-for="language in marketLanguageOptions" :key="language" :value="language">{{ language === UNLABELLED_LANGUAGE ? t('modulesLanguagesUndeclared') : languageLabel(language) }}</option>
          </select>
        </label>
        <label class="module-market-filter">
          <span>{{ t('modulesFilterProfile') }}</span>
          <select v-model="marketProfile">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option value="adventure-module">{{ copy.adventureLabel }}</option>
            <option value="content-pack">{{ copy.resourceLabel }}</option>
          </select>
        </label>
        <label v-if="marketTagOptions.length" class="module-market-filter">
          <span>{{ t('modulesFilterTag') }}</span>
          <select v-model="marketTag">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option v-for="tag in marketTagOptions" :key="tag" :value="tag">{{ tag }}</option>
          </select>
        </label>
        <label v-if="marketTrustOptions.length" class="module-market-filter">
          <span>{{ t('modulesFilterTrust') }}</span>
          <select v-model="marketTrust">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option v-for="level in marketTrustOptions" :key="level" :value="level">{{ marketTrustLabel(level) }}</option>
          </select>
        </label>
        <label class="module-market-filter">
          <span>{{ t('modulesSortLabel') }}</span>
          <select v-model="marketSort" data-testid="modules-market-sort">
            <option v-for="option in marketSortOptions" :key="option.value" :value="option.value">{{ t(option.key) }}</option>
          </select>
        </label>
        <button v-if="marketFiltersActive" type="button" class="module-filter-reset" @click="resetMarketFilters">{{ t('modulesFilterReset') }}</button>
      </div>

      <!-- 分类筛选结果 grid（非 all 时显示，或有活跃筛选时显示） -->
      <div v-if="activeCategory !== 'all' || marketFiltersActive" class="modules-search-grid">
        <ContentPackCard
          v-for="item in (activeCategory !== 'all' ? categoryFiltered : filteredMarket).slice(0, 60)"
          :key="item.id"
          :item="item"
          :installing="installBusy === item.id"
          :has-newer-version="item.update_available"
          :show-hub-stats="Boolean(marketSource?.hub)"
          @install="installFromMarketplace(item)"
          @detail="openMarketDetail(item)"
        />
        <p v-if="!(activeCategory !== 'all' ? categoryFiltered : filteredMarket).length" class="muted">{{ t('modulesFilteredEmpty') }}</p>
      </div>

      <!-- 精选横幅 -->
      <div v-if="featuredItem && activeCategory === 'all'" class="modules-featured-banner"
        :class="featuredItem.trust_level === 'official' ? 'is-official' : featuredItem.trust_level === 'verified' ? 'is-verified' : 'is-community'"
        data-testid="modules-featured">
        <div class="modules-featured-art" aria-hidden="true" />
        <div class="modules-featured-body">
          <p class="modules-featured-eyebrow">{{ moduleCategoryLabel(featuredItem, copy) }} · {{ featuredItem.trust_level === 'official' ? t('pluginTrustOfficial') : t('pluginTrustVerified') }}</p>
          <h2 class="modules-featured-title">{{ featuredItem.name }}</h2>
          <p class="modules-featured-desc">{{ featuredItem.description }}</p>
          <div class="modules-featured-actions">
            <button type="button" class="modules-featured-install" :disabled="installBusy === featuredItem.id" @click="installFromMarketplace(featuredItem)">
              {{ featuredItem.installed ? t('update') : t('install') }}
            </button>
            <button v-if="Boolean(marketSource?.hub)" type="button" class="modules-featured-detail" @click="openMarketDetail(featuredItem)">{{ t('hubDataAndReviews') }}</button>
          </div>
        </div>
      </div>

      <template v-if="activeCategory === 'all'">
        <!-- 冒险模组货架 -->
        <div v-if="shelfAdventure.length" class="modules-shelf" data-testid="modules-shelf-adventure">
          <div class="modules-shelf-header">
            <h2>{{ locale === 'zh-CN' ? '冒险模组' : 'Adventures' }}</h2>
            <button type="button" class="modules-shelf-more" @click="activeCategory = 'adventure'">{{ locale === 'zh-CN' ? '查看全部 →' : 'See all →' }}</button>
          </div>
          <div class="modules-shelf-scroll">
            <ContentPackCard
              v-for="item in shelfAdventure"
              :key="item.id"
              :item="item"
              :installing="installBusy === item.id"
              :has-newer-version="item.update_available"
              :show-hub-stats="Boolean(marketSource?.hub)"
              @install="installFromMarketplace(item)"
              @detail="openMarketDetail(item)"
            />
          </div>
        </div>

        <!-- 下载最多货架 -->
        <div v-if="shelfTopDownloads.length" class="modules-shelf" data-testid="modules-shelf-downloads">
          <div class="modules-shelf-header">
            <h2>{{ locale === 'zh-CN' ? '下载最多' : 'Most Downloaded' }}</h2>
          </div>
          <div class="modules-shelf-scroll">
            <ContentPackCard
              v-for="item in shelfTopDownloads"
              :key="item.id"
              :item="item"
              :installing="installBusy === item.id"
              :has-newer-version="item.update_available"
              :show-hub-stats="Boolean(marketSource?.hub)"
              @install="installFromMarketplace(item)"
              @detail="openMarketDetail(item)"
            />
          </div>
        </div>

        <!-- 官方与认证货架 -->
        <div v-if="shelfOfficial.length" class="modules-shelf" data-testid="modules-shelf-official">
          <div class="modules-shelf-header">
            <h2>{{ locale === 'zh-CN' ? '官方与认证' : 'Official & Verified' }}</h2>
            <button type="button" class="modules-shelf-more" @click="activeCategory = 'official'">{{ locale === 'zh-CN' ? '查看全部 →' : 'See all →' }}</button>
          </div>
          <div class="modules-shelf-scroll">
            <ContentPackCard
              v-for="item in shelfOfficial"
              :key="item.id"
              :item="item"
              :installing="installBusy === item.id"
              :has-newer-version="item.update_available"
              :show-hub-stats="Boolean(marketSource?.hub)"
              @install="installFromMarketplace(item)"
              @detail="openMarketDetail(item)"
            />
          </div>
        </div>

        <div v-if="!market.length" class="empty-state" data-testid="modules-online-empty">
          <p class="muted">{{ t('modulesOnlineEmpty') }}</p>
          <p class="muted">{{ t('modulesOnlineEmptyHint') }}</p>
          <RouterLink :to="{ name: 'creator' }" class="module-submit-action">{{ t('modulesSubmitGoCreator') }}</RouterLink>
        </div>
      </template>
    </template>

    <p v-if="marketBusy && !marketKeyword.trim()" class="muted">{{ t('modulesLoading') }}</p>
    <p v-if="marketError && !marketKeyword.trim()" class="error-text">{{ marketError }}</p>

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
/* ── Page shell ───────────────────────────────────── */
.modules-page { display: grid; gap: 1.5rem; }
.modules-heading { position: relative; display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 1rem; isolation: isolate; padding: 24px 0 20px; border-bottom: 1px solid color-mix(in srgb, var(--df-accent) 22%, var(--df-border-soft)); }
.modules-heading::before { position: absolute; z-index: -1; inset: 0 0 0 30%; content: ''; background: linear-gradient(90deg, var(--df-canvas), transparent 72%), var(--df-bg-atmosphere-image) center / cover; mask-image: linear-gradient(90deg, transparent, black 42%, transparent); opacity: .2; pointer-events: none; }
.modules-heading h1 { margin: 7px 0 9px; color: var(--df-accent-strong); font-family: var(--df-font-title); font-size: clamp(30px, 3vw, 42px); line-height: 1.18; letter-spacing: .02em; }
.module-boundary-note { margin: 0; max-width: 58rem; color: var(--df-accent-strong); font-size: .82rem; }

/* ── Top search bar ───────────────────────────────── */
.modules-search-bar { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.modules-search-input { flex: 1 1 240px; min-width: 0; min-height: 36px; padding: 0 .75rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); color: var(--df-text); font: .9rem/1 inherit; }
.modules-search-btn { min-height: 36px; padding: 0 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); color: var(--df-text); font: .9rem/1 inherit; cursor: pointer; white-space: nowrap; }
.modules-search-btn:hover { background: var(--df-surface-1); border-color: var(--df-interactive); }

/* ── My packs button + dropdown ─────────────────── */
.modules-my-panel-wrap { position: relative; }
.modules-my-btn { display: inline-flex; align-items: center; gap: .4rem; min-height: 36px; padding: 0 .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); color: var(--df-text); font: .88rem/1 inherit; cursor: pointer; font-weight: 600; white-space: nowrap; }
.modules-my-btn.active, .modules-my-btn:hover { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 8%, var(--df-surface-1)); }
.modules-my-count { display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 20px; padding: 0 .35rem; border-radius: 999px; background: var(--df-interactive); color: #fff; font-size: .72rem; font-weight: 800; }
.modules-my-count.is-update { background: var(--df-warning, #d39b4d); color: #fff; }
.modules-my-panel { position: absolute; top: calc(100% + 6px); right: 0; z-index: 100; width: min(420px, 90vw); max-height: 70vh; overflow-y: auto; display: flex; flex-direction: column; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); box-shadow: 0 8px 32px rgba(0,0,0,.18); }
.modules-my-panel-header { display: flex; align-items: center; justify-content: space-between; gap: .6rem; padding: .75rem 1rem .5rem; border-bottom: 1px solid var(--df-border-soft); }
.modules-my-panel-header strong { font-size: .92rem; }
.modules-my-panel-actions { display: flex; gap: .4rem; }
.modules-my-empty { padding: .75rem 1rem; margin: 0; }
.modules-my-list { display: flex; flex-direction: column; }
.modules-my-row { display: flex; align-items: center; gap: .65rem; padding: .6rem 1rem; text-decoration: none; color: var(--df-text); border-bottom: 1px solid var(--df-border-soft); }
.modules-my-row:last-child { border-bottom: 0; }
.modules-my-row:hover { background: var(--df-surface-2); }
.modules-my-row-art { flex: 0 0 40px; height: 40px; border-radius: var(--df-radius-md); background: var(--df-surface-2) var(--df-bg-cover-image, none) center/cover; }
.modules-my-row-copy { flex: 1; min-width: 0; display: grid; gap: .1rem; }
.modules-my-row-copy strong { font-size: .88rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.modules-my-row-copy span { font-size: .76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.modules-my-panel-footer { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; padding: .65rem 1rem; border-top: 1px solid var(--df-border-soft); font-size: .84rem; }
.module-import-file { display: inline-flex; align-items: center; gap: .4rem; color: var(--df-text-muted); cursor: pointer; }
.module-import-file input[type="file"] { max-width: 160px; }
.module-import-preview { width: 100%; display: grid; gap: .3rem; padding: .5rem .75rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.module-import-preview p { margin: 0; font-size: .8rem; }
.module-update-all-action { border: 1px solid color-mix(in srgb, var(--df-warning) 55%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: transparent; color: var(--df-warning); font-size: .78rem; font-weight: 700; padding: .2rem .6rem; cursor: pointer; }
.module-update-all-action:disabled { opacity: .5; }
.module-update-action { min-height: 24px; padding: 0 .55rem; border: 1px solid color-mix(in srgb, var(--df-warning) 55%, var(--df-border-soft)); border-radius: 999px; background: transparent; color: var(--df-warning); font-size: .72rem; font-weight: 700; white-space: nowrap; cursor: pointer; }
.module-update-action:hover { background: color-mix(in srgb, var(--df-warning) 12%, transparent); }
.module-update-action:disabled { opacity: .6; cursor: progress; }
.modules-my-row-updating { display: inline-flex; align-items: center; gap: .4rem; font-size: .74rem; color: var(--df-interactive-strong); white-space: nowrap; }
.modules-my-update-spinner { width: 12px; height: 12px; border: 2px solid color-mix(in srgb, var(--df-interactive) 30%, transparent); border-top-color: var(--df-interactive); border-radius: 50%; animation: my-spin .7s linear infinite; flex: 0 0 auto; }
@keyframes my-spin { to { transform: rotate(360deg); } }

/* ── Category chips ───────────────────────────────── */
.modules-category-chips { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.modules-category-chip { padding: .3rem .9rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: var(--df-surface-2); color: var(--df-text-muted); font-size: .84rem; font-weight: 600; cursor: pointer; white-space: nowrap; transition: border-color .12s, background .12s; }
.modules-category-chip:hover { border-color: var(--df-interactive); color: var(--df-text); }
.modules-category-chip.active { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 14%, var(--df-surface-2)); color: var(--df-text); }
.modules-submit-link { margin-left: auto; padding: 0; border: 0; background: none; color: var(--df-interactive-strong); font-size: .82rem; font-weight: 700; cursor: pointer; white-space: nowrap; }
.modules-submit-link:hover { text-decoration: underline; }
.modules-filter-toggle { padding: .3rem .9rem; border: 1px solid var(--df-border-soft); border-radius: 999px; background: var(--df-surface-2); color: var(--df-text-muted); font-size: .84rem; font-weight: 600; cursor: pointer; white-space: nowrap; transition: border-color .12s, background .12s; }
.modules-filter-toggle:hover, .modules-filter-toggle.active { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 14%, var(--df-surface-2)); color: var(--df-text); }
.modules-filter-panel { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem .9rem; padding: .75rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); }

/* ── Featured banner ──────────────────────────────── */
.modules-featured-banner { position: relative; min-height: 220px; border-radius: var(--df-radius-lg); overflow: hidden; isolation: isolate; border: 1px solid var(--df-border-soft); display: flex; align-items: flex-end; }
.modules-featured-art { position: absolute; inset: 0; z-index: 0; background: var(--banner-cover, linear-gradient(135deg, #1e3a5f 0%, #2d1b69 100%)) center/cover; filter: saturate(.85) contrast(1.1); }
.modules-featured-banner.is-official .modules-featured-art { background: linear-gradient(135deg, #3d2800 0%, #6b4a00 100%); }
.modules-featured-banner.is-verified .modules-featured-art { background: linear-gradient(135deg, #0e2d4a 0%, #1a3a6b 100%); }
.modules-featured-banner.is-community .modules-featured-art { background: linear-gradient(135deg, #1e3a5f 0%, #2d1b69 100%); }
.modules-featured-art::after { position: absolute; inset: 0; content: ''; background: linear-gradient(90deg, rgba(0,0,0,.72) 0%, rgba(0,0,0,.35) 55%, transparent 100%); }
.modules-featured-body { position: relative; z-index: 1; padding: 1.5rem 2rem; max-width: 60%; }
.modules-featured-eyebrow { margin: 0 0 .35rem; color: rgba(255,255,255,.65); font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.modules-featured-title { margin: 0 0 .5rem; color: #fff; font-family: var(--df-font-title, inherit); font-size: clamp(1.3rem, 2.5vw, 2rem); line-height: 1.2; }
.modules-featured-desc { margin: 0 0 1rem; color: rgba(255,255,255,.75); font-size: .88rem; line-height: 1.5; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.modules-featured-actions { display: flex; gap: .6rem; }
.modules-featured-install { min-height: 36px; padding: 0 1.2rem; border: 0; border-radius: var(--df-radius-md); background: var(--df-interactive); color: #fff; font: 700 .88rem/1 inherit; cursor: pointer; }
.modules-featured-install:hover { background: var(--df-interactive-strong); }
.modules-featured-install:disabled { opacity: .6; cursor: progress; }
.modules-featured-detail { min-height: 36px; padding: 0 1rem; border: 1px solid rgba(255,255,255,.35); border-radius: var(--df-radius-md); background: rgba(255,255,255,.12); color: #fff; font: .88rem/1 inherit; cursor: pointer; }
.modules-featured-detail:hover { background: rgba(255,255,255,.2); }

/* ── Shelves ──────────────────────────────────────── */
.modules-shelf { display: grid; gap: .65rem; }
.modules-shelf-header { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
.modules-shelf-header h2 { margin: 0; font-size: 1.05rem; }
.modules-shelf-more { padding: 0; border: 0; background: none; color: var(--df-interactive-strong); font-size: .84rem; font-weight: 700; cursor: pointer; white-space: nowrap; }
.modules-shelf-more:hover { text-decoration: underline; }
.modules-shelf-scroll { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: .85rem; }
@media (min-width: 1200px) { .modules-shelf-scroll { grid-template-columns: repeat(6, 1fr); } }

/* ── Search results grid ──────────────────────────── */
.modules-search-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: .85rem; }
@media (min-width: 1200px) { .modules-search-grid { grid-template-columns: repeat(5, 1fr); } }

/* ── Recovery ─────────────────────────────────────── */
.module-recovery { padding: 1rem; border: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); border-left: 3px solid var(--df-accent); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.module-recovery p { margin: .4rem 0 0; }
.module-recovery-facts { display: grid; gap: .25rem; margin: .6rem 0 0; }
.module-recovery-facts dt { color: var(--df-text-muted); font-size: .78rem; }
.module-recovery-facts dd { margin: 0; font-size: .82rem; overflow-wrap: anywhere; }
.module-recovery-ready { color: var(--df-success); }
.module-recovery-actions { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .7rem; }
.module-recovery-resume { display: inline-flex; align-items: center; padding: .35rem .8rem; border-radius: .6rem; text-decoration: none; }

/* ── Submit guide modal ───────────────────────────── */
.module-submit-steps { display: grid; gap: .45rem; margin: .8rem 0 0; padding-left: 1.2rem; color: var(--df-text-secondary); line-height: 1.5; }
.module-submit-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .6rem; padding-top: 1rem; }
.module-submit-action { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 0 .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text); background: var(--df-surface-1); font-weight: 700; text-decoration: none; }
.module-submit-action.is-primary { border-color: color-mix(in srgb, var(--df-accent) 50%, var(--df-border-soft)); color: var(--df-accent-strong); background: color-mix(in srgb, var(--df-accent) 12%, var(--df-surface-1)); }
.empty-state .module-submit-action { justify-self: start; }
.module-filter-reset { padding: .3rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font-size: .84rem; cursor: pointer; }

/* ── Responsive ───────────────────────────────────── */
@media (max-width: 600px) {
  .modules-featured-body { max-width: 100%; padding: 1rem; }
  .modules-search-grid, .modules-shelf-scroll { grid-template-columns: repeat(2, 1fr); }
  .modules-my-panel { width: 100vw; right: auto; left: 0; }
}
@media (max-width: 400px) {
  .modules-search-grid, .modules-shelf-scroll { grid-template-columns: 1fr; }
}
</style>
