import { computed, onScopeDispose, reactive, ref, watch, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { promptEnableAfterInstall } from './installEnable'
import { useHubDetail } from './useHubDetail'
import type {
  PluginInfo,
  PluginMarketplaceItem,
  PluginMarketplaceResponse,
  PluginMirror,
} from '@/api/types'

export function isNewerPluginVersion(latest?: string, current?: string): boolean {
  const latestText = String(latest || '').trim()
  const currentText = String(current || '').trim()
  if (!latestText || !currentText) return false
  const versionPattern = /^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
  const parse = (value: string) => {
    const match = versionPattern.exec(value)
    if (!match) return null
    return {
      core: [Number(match[1]), Number(match[2] || 0), Number(match[3] || 0)],
      prerelease: match[4] ? match[4].split('.') : null,
    }
  }
  const latestVersion = parse(latestText)
  const currentVersion = parse(currentText)
  if (!latestVersion || !currentVersion) return false
  for (let index = 0; index < latestVersion.core.length; index++) {
    const latestPart = latestVersion.core[index]
    const currentPart = currentVersion.core[index]
    if (latestPart > currentPart) return true
    if (latestPart < currentPart) return false
  }
  const latestPrerelease = latestVersion.prerelease
  const currentPrerelease = currentVersion.prerelease
  if (!latestPrerelease || !currentPrerelease) {
    return latestPrerelease === null && currentPrerelease !== null
  }
  for (let index = 0; index < Math.max(latestPrerelease.length, currentPrerelease.length); index++) {
    const latestPart = latestPrerelease[index]
    const currentPart = currentPrerelease[index]
    if (latestPart === undefined || currentPart === undefined) return currentPart === undefined
    if (latestPart === currentPart) continue
    const latestNumeric = /^\d+$/.test(latestPart)
    const currentNumeric = /^\d+$/.test(currentPart)
    if (latestNumeric && currentNumeric) return Number(latestPart) > Number(currentPart)
    if (latestNumeric !== currentNumeric) return !latestNumeric
    return latestPart > currentPart
  }
  return false
}

// 商店条目是否真有新版可更新。优先用索引同步的真实 latest.version；
// 没有 latest（索引未升级或同步失败）时回退到条目 version（收录时静态版本）。
export function marketItemHasNewerVersion(item: PluginMarketplaceItem | undefined, installedVersion?: string): boolean {
  if (!item) return false
  const latestVersion = item.latest?.version || item.version
  const current = installedVersion ?? item.installed_version
  if (!latestVersion || !current) return false
  return isNewerPluginVersion(latestVersion, current)
}

export function usePluginMarketplace(
  busy: Ref<string>,
  refreshSurfaces: () => Promise<void>,
  typeFilter: Ref<string>,
  onUninstalled?: (plugin: PluginInfo, result: { lorebook_removed?: number; cards_removed?: number; worlds_removed?: number; worlds_kept?: string[] }) => void,
) {
  const toast = useToast()
  const { t } = useLocale()
  const { confirm } = useConfirm()
  const marketplace = ref<PluginMarketplaceItem[]>([])
  const mirrors = ref<PluginMirror[]>([])
  const mirrorTests = ref<Record<string, string>>({})
  const marketplaceSource = ref<PluginMarketplaceResponse['source'] | null>(null)
  const marketKeyword = ref('')
  const marketLoading = ref(false)
  const mirrorLoading = ref(false)
  const sortMode = ref('')  // '' 默认 / stars / downloads / rating / likes / name-asc / name-desc
  const newMirror = reactive<PluginMirror>({
    id: '',
    name: '',
    raw_prefix: '',
    clone_prefix: '',
    enabled: true,
    priority: 1,
  })
  let marketRetryTimer: number | undefined
  let marketRetryAttempt = 0

  function clearMarketRetry() {
    if (marketRetryTimer !== undefined) window.clearTimeout(marketRetryTimer)
    marketRetryTimer = undefined
  }

  function scheduleMarketRetry() {
    if (marketRetryTimer !== undefined || marketRetryAttempt >= 2) return
    const delays = [15_000, 60_000]
    const delay = delays[marketRetryAttempt++]
    marketRetryTimer = window.setTimeout(() => {
      marketRetryTimer = undefined
      void loadMarketplace({ silent: true })
    }, delay)
  }

  // Hub detail / README / ratings live in their own composable so the online
  // content packages shelf can reuse them.
  const hub = useHubDetail(busy)

  onScopeDispose(clearMarketRetry)

  const filteredMarketplace = computed(() => {
    const type = typeFilter.value
    const keyword = marketKeyword.value.trim().toLowerCase()
    const items = marketplace.value.filter(item => {
      // W7 扩展中心：统一货架——content-pack 不再分流到 /modules，全部
      // .dfplugin 类型（内容包/冒险模组/ruleset-runtime/工具等）在同一货架
      // 发现与安装；Hub catalog 是共享索引，服务端本就不按货架过滤。
      if (type && item.plugin_type !== type) return false
      if (!keyword) return true
      return [item.id, item.name, item.description, item.repository_url, ...(item.tags || [])]
        .some(value => String(value || '').toLowerCase().includes(keyword))
    })
    if (sortMode.value === 'stars') {
      return [...items].sort((a, b) => (b.stars || 0) - (a.stars || 0))
    }
    if (sortMode.value === 'downloads') {
      return [...items].sort((a, b) => (b.stats?.downloads_total || 0) - (a.stats?.downloads_total || 0))
    }
    if (sortMode.value === 'rating') {
      return [...items].sort((a, b) => (b.stats?.rating_average || 0) - (a.stats?.rating_average || 0))
    }
    if (sortMode.value === 'likes') {
      return [...items].sort((a, b) => (b.stats?.likes || 0) - (a.stats?.likes || 0))
    }
    if (sortMode.value === 'name-asc') {
      return [...items].sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    }
    if (sortMode.value === 'name-desc') {
      return [...items].sort((a, b) => (b.name || '').localeCompare(a.name || ''))
    }
    return items
  })

  // 商店分页：筛选/排序后每页 12 个
  const page = ref(1)
  const pageSize = 12
  const totalMarketplace = computed(() => filteredMarketplace.value.length)
  const totalPages = computed(() => Math.max(1, Math.ceil(totalMarketplace.value / pageSize)))
  const paginatedMarketplace = computed(() => {
    const start = (page.value - 1) * pageSize
    return filteredMarketplace.value.slice(start, start + pageSize)
  })
  function goToPage(next: number) {
    page.value = Math.min(Math.max(1, next), totalPages.value)
  }
  // 筛选/排序/关键字变化时回到第 1 页
  watch([marketKeyword, typeFilter, sortMode], () => { page.value = 1 })

  function canUpdateFromStore(pluginId: string, installedVersion?: string) {
    const item = marketplace.value.find(candidate => candidate.id === pluginId)
    if (!item || item.distribution === 'bundled' || item.installable === false) return false
    // 只在该插件真有新版可更新时才显示"从商店更新"。
    return marketItemHasNewerVersion(item, installedVersion)
  }

  async function loadMarketplace(options?: { silent?: boolean }) {
    const silent = options?.silent === true
    if (!silent) {
      clearMarketRetry()
      marketRetryAttempt = 0
    }
    const showLoading = !silent || marketplace.value.length === 0
    if (showLoading) marketLoading.value = true
    try {
      const response = await pluginApi.marketplace()
      if (!response.ok) throw new Error(response.error || t('pluginMarketplaceLoadFailed'))
      marketplace.value = response.plugins || []
      marketplaceSource.value = response.source || null
      if (response.source?.stale) {
        scheduleMarketRetry()
      } else {
        clearMarketRetry()
        marketRetryAttempt = 0
      }
    } catch (error: unknown) {
      if (!silent) toast.error(errorMessage(error))
      scheduleMarketRetry()
    } finally {
      if (showLoading) marketLoading.value = false
    }
  }

  async function loadMirrors() {
    mirrorLoading.value = true
    try {
      const response = await pluginApi.mirrors()
      mirrors.value = response.mirrors || []
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      mirrorLoading.value = false
    }
  }

  async function installMarketPlugin(item: PluginMarketplaceItem) {
    if (item.risk_level === 'unrestricted-process') {
      const ok = await confirm({
        title: t('confirmPluginInstallTitle'),
        content: t('confirmProcessPluginInstall', { name: item.name }),
        positiveText: t('install'),
        type: 'warning',
      })
      if (!ok) return
    }
    if (item.needs_core_update) {
      const ok = await confirm({
        title: t('confirmPluginInstallTitle'),
        content: t('confirmCoreUpgrade', { name: item.name, version: item.min_app_version || '' }),
        positiveText: t('install'),
        type: 'warning',
      })
      if (!ok) return
    }
    busy.value = `market:${item.id}`
    try {
      const installed = await pluginApi.installMarketplace(item.id, Boolean(item.installed))
      toast.success(t(item.installed ? 'pluginNamedUpdated' : 'pluginNamedInstalled', { name: item.name }))
      // 新装默认禁用，装完立刻问一次是否启用；更新沿用用户原来的开关状态。
      if (!item.installed) {
        await promptEnableAfterInstall({
          id: installed?.id || item.id,
          name: installed?.name || item.name,
          enabled: installed?.enabled,
        })
      }
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function updateInstalledPlugin(plugin: PluginInfo) {
    const marketItem = marketplace.value.find(item => item.id === plugin.id)
    if (marketItem?.risk_level === 'unrestricted-process') {
      const ok = await confirm({
        title: t('confirmPluginUpdateTitle'),
        content: t('confirmProcessPluginUpdate', { name: plugin.name }),
        positiveText: t('updateFromStore'),
        type: 'warning',
      })
      if (!ok) return
    }
    if (marketItem?.needs_core_update) {
      const ok = await confirm({
        title: t('confirmPluginUpdateTitle'),
        content: t('confirmCoreUpgrade', { name: plugin.name, version: marketItem.min_app_version || '' }),
        positiveText: t('updateFromStore'),
        type: 'warning',
      })
      if (!ok) return
    }
    busy.value = `${plugin.id}:update`
    try {
      await pluginApi.update(plugin.id)
      toast.success(t('pluginNamedUpdated', { name: plugin.name }))
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function uninstallPlugin(plugin: PluginInfo) {
    const ok = await confirm({
      title: t('confirmPluginUninstallTitle'),
      content: t('confirmUninstallPlugin', { name: plugin.name }),
      positiveText: t('uninstallPlugin'),
      type: 'error',
    })
    if (!ok) return
    busy.value = `${plugin.id}:uninstall`
    try {
      const result = await pluginApi.uninstall(plugin.id)
      toast.success(t('pluginNamedUninstalled', { name: plugin.name }))
      onUninstalled?.(plugin, result)
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function addMirror() {
    busy.value = 'mirror:add'
    try {
      await pluginApi.addMirror(newMirror)
      toast.success(t('mirrorAdded'))
      Object.assign(newMirror, {
        id: '', name: '', raw_prefix: '', clone_prefix: '', enabled: true,
        priority: mirrors.value.length + 1,
      })
      await loadMirrors()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function saveMirror(mirror: PluginMirror, patch: Partial<PluginMirror>) {
    busy.value = `mirror:${mirror.id}`
    try {
      await pluginApi.updateMirror(mirror.id, patch)
      await loadMirrors()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function deleteMirror(mirror: PluginMirror) {
    const ok = await confirm({
      title: t('confirmMirrorDeleteTitle'),
      content: t('confirmDeleteMirror', { name: mirror.name }),
      positiveText: t('confirmDelete'),
      type: 'error',
    })
    if (!ok) return
    busy.value = `mirror:${mirror.id}`
    try {
      await pluginApi.deleteMirror(mirror.id)
      toast.success(t('mirrorDeleted'))
      await loadMirrors()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function testMirror(mirror?: PluginMirror) {
    const key = mirror?.id || 'all'
    busy.value = `mirror-test:${key}`
    try {
      const response = await pluginApi.testMirror(mirror?.id || '')
      for (const result of response.results || []) {
        const id = result.mirror_id || 'all'
        mirrorTests.value[id] = result.ok
          ? t('mirrorAvailable', { ms: result.elapsed_ms || 0 })
          : t('mirrorFailed', { reason: result.error || result.status || t('unknownError') })
      }
      toast[response.ok ? 'success' : 'error'](
        response.ok ? t('mirrorTestDone') : (response.error || t('allMirrorTestsFailed')),
      )
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  function openUrl(url?: string) {
    if (url) window.open(url, '_blank', 'noopener')
  }

  return {
    marketplace,
    mirrors,
    mirrorTests,
    marketplaceSource,
    marketKeyword,
    marketLoading,
    ...hub,
    mirrorLoading,
    newMirror,
    sortMode,
    filteredMarketplace,
    page,
    totalMarketplace,
    totalPages,
    paginatedMarketplace,
    goToPage,
    canUpdateFromStore,
    loadMarketplace,
    loadMirrors,
    installMarketPlugin,
    updateInstalledPlugin,
    uninstallPlugin,
    addMirror,
    saveMirror,
    deleteMirror,
    testMirror,
    openUrl,
    isNewerVersion: isNewerPluginVersion,
    marketItemHasNewerVersion,
  }
}
