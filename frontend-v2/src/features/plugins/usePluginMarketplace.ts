import { computed, reactive, ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
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
  const latestParts = latestText.replace(/^v/i, '').split('.').map(Number)
  const currentParts = currentText.replace(/^v/i, '').split('.').map(Number)
  for (let index = 0; index < Math.max(latestParts.length, currentParts.length); index++) {
    const latestPart = latestParts[index] || 0
    const currentPart = currentParts[index] || 0
    if (latestPart > currentPart) return true
    if (latestPart < currentPart) return false
  }
  return false
}

export function usePluginMarketplace(busy: Ref<string>, refreshSurfaces: () => Promise<void>) {
  const toast = useToast()
  const { t } = useLocale()
  const marketplace = ref<PluginMarketplaceItem[]>([])
  const mirrors = ref<PluginMirror[]>([])
  const mirrorTests = ref<Record<string, string>>({})
  const marketplaceSource = ref<PluginMarketplaceResponse['source'] | null>(null)
  const marketKeyword = ref('')
  const marketLoading = ref(false)
  const mirrorLoading = ref(false)
  const newMirror = reactive<PluginMirror>({
    id: '',
    name: '',
    raw_prefix: '',
    clone_prefix: '',
    enabled: true,
    priority: 1,
  })

  const filteredMarketplace = computed(() => {
    const keyword = marketKeyword.value.trim().toLowerCase()
    if (!keyword) return marketplace.value
    return marketplace.value.filter(item => [
      item.id, item.name, item.description, item.repository_url, ...(item.tags || []),
    ].some(value => String(value || '').toLowerCase().includes(keyword)))
  })

  function canUpdateFromStore(pluginId: string) {
    const item = marketplace.value.find(candidate => candidate.id === pluginId)
    return Boolean(item && item.distribution !== 'bundled' && item.installable !== false)
  }

  async function loadMarketplace() {
    marketLoading.value = true
    try {
      const response = await pluginApi.marketplace()
      if (!response.ok) throw new Error(response.error || t('pluginMarketplaceLoadFailed'))
      marketplace.value = response.plugins || []
      marketplaceSource.value = response.source || null
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      marketLoading.value = false
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
    if (item.risk_level === 'unrestricted-process' && !window.confirm(t('confirmProcessPluginInstall', { name: item.name }))) return
    busy.value = `market:${item.id}`
    try {
      await pluginApi.installMarketplace(item.id, Boolean(item.installed))
      toast.success(t(item.installed ? 'pluginNamedUpdated' : 'pluginNamedInstalled', { name: item.name }))
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function updateInstalledPlugin(plugin: PluginInfo) {
    const marketItem = marketplace.value.find(item => item.id === plugin.id)
    if (marketItem?.risk_level === 'unrestricted-process' && !window.confirm(t('confirmProcessPluginUpdate', { name: plugin.name }))) return
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
    if (!window.confirm(t('confirmUninstallPlugin', { name: plugin.name }))) return
    busy.value = `${plugin.id}:uninstall`
    try {
      await pluginApi.uninstall(plugin.id)
      toast.success(t('pluginNamedUninstalled', { name: plugin.name }))
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
    if (!window.confirm(t('confirmDeleteMirror', { name: mirror.name }))) return
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
    mirrorLoading,
    newMirror,
    filteredMarketplace,
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
  }
}
