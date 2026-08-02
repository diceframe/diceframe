import { ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import { pluginApi } from '@/api/plugins'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import type { PluginField, PluginInfo } from '@/api/types'

export function useInstalledPlugins(
  busy: Ref<string>,
  afterLoad: () => Promise<unknown>,
  refreshSurfaces: () => Promise<void>,
) {
  const toast = useToast()
  const { t } = useLocale()
  const plugins = ref<PluginInfo[]>([])
  const expandedPluginNames = ref<string[]>([])
  const loading = ref(false)
  const installFile = ref<File | null>(null)
  const overwriteInstall = ref(false)

  async function load() {
    loading.value = true
    try {
      const response = await pluginApi.list()
      plugins.value = response.plugins || []
      // 默认收起全部插件，避免插件多时页面过长；用户展开的保持展开。
      await afterLoad()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      loading.value = false
    }
  }

  const ordered = (plugin: PluginInfo): [string, PluginField][] =>
    Object.entries(plugin.schema?.properties || {})
      .sort((left, right) => (left[1].ui?.order || 0) - (right[1].ui?.order || 0))

  function value(plugin: PluginInfo, key: string, field: PluginField): unknown {
    const current = plugin.config?.[key]
    return typeof current === 'object' && field.ui?.sensitive ? '' : current ?? field.default ?? ''
  }

  function textValue(plugin: PluginInfo, key: string, field: PluginField): string {
    const current = value(plugin, key, field)
    return typeof current === 'string' ? current : current == null ? '' : String(current)
  }

  function selectValue(plugin: PluginInfo, key: string, field: PluginField): string | number | null {
    const current = value(plugin, key, field)
    return typeof current === 'string' || typeof current === 'number' ? current : null
  }

  function numberValue(plugin: PluginInfo, key: string, field: PluginField): number | null {
    const current = value(plugin, key, field)
    return typeof current === 'number' ? current : current === '' || current == null ? null : Number(current)
  }

  function set(plugin: PluginInfo, key: string, next: unknown) {
    if (!plugin.config) plugin.config = {}
    plugin.config[key] = next
  }

  function listValue(plugin: PluginInfo, key: string, field: PluginField): string[] {
    const current = value(plugin, key, field)
    return Array.isArray(current) ? current.map(item => String(item)) : []
  }

  function secretPlaceholder(plugin: PluginInfo, key: string, field: PluginField): string {
    const configured = plugin.config?.[key] as { configured?: boolean; masked?: string } | undefined
    return field.ui?.sensitive && configured?.configured
      ? t('secretConfiguredPlaceholder', { masked: configured.masked || '' })
      : ''
  }

  const showGroup = (fields: [string, PluginField][], index: number) =>
    Boolean(fields[index][1].ui?.group && (index === 0 || fields[index - 1][1].ui?.group !== fields[index][1].ui?.group))

  const parseList = (input: string) =>
    Array.from(new Set(input.split(/[\n,]+/).map(item => item.trim()).filter(Boolean)))

  function validate(plugin: PluginInfo): string {
    for (const [key, field] of ordered(plugin)) {
      const current = value(plugin, key, field)
      if (field.type === 'number' || field.type === 'integer') {
        const numeric = Number(current)
        if (field.exclusiveMinimum !== undefined && numeric <= field.exclusiveMinimum) return t('validationGreaterThan', { field: field.title || key, value: field.exclusiveMinimum })
        if (field.minimum !== undefined && numeric < field.minimum) return t('validationAtLeast', { field: field.title || key, value: field.minimum })
        if (field.maximum !== undefined && numeric > field.maximum) return t('validationAtMost', { field: field.title || key, value: field.maximum })
      }
      if (field.type === 'string') {
        const text = String(current || '')
        if (field.minLength !== undefined && text.length > 0 && text.length < field.minLength) return t('validationMinLength', { field: field.title || key, value: field.minLength })
        if (field.maxLength !== undefined && text.length > field.maxLength) return t('validationMaxLength', { field: field.title || key, value: field.maxLength })
      }
    }
    return ''
  }

  async function save(plugin: PluginInfo) {
    const validationError = validate(plugin)
    if (validationError) return toast.error(validationError)
    busy.value = plugin.id
    try {
      const payload: Record<string, unknown> = {}
      for (const [key, field] of ordered(plugin)) {
        const current = plugin.config?.[key]
        if (field.ui?.sensitive && current === '') continue
        payload[key] = current
      }
      await pluginApi.updateConfig(plugin.id, payload)
      toast.success(t('pluginNamedSaved', { name: plugin.name }))
      await load()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function restart(plugin: PluginInfo) {
    busy.value = plugin.id
    try {
      await pluginApi.restart(plugin.id)
      toast.success(t('pluginNamedRestartRequested', { name: plugin.name }))
      await load()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function clearCardCache(plugin: PluginInfo) {
    if (!window.confirm(t('confirmClearCardCache'))) return
    busy.value = `${plugin.id}:card-cache`
    try {
      const response = await pluginApi.clearCardCache(plugin.id)
      const deleted = response.deleted || 0
      const mb = ((response.bytes_deleted || 0) / 1024 / 1024).toFixed(2)
      toast.success(t('cardCacheCleared', { count: deleted, mb }))
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function toggleRunning(plugin: PluginInfo, running: boolean) {
    busy.value = plugin.id
    try {
      await pluginApi.setRunning(plugin.id, running)
      toast.success(t(running ? 'pluginNamedStarted' : 'pluginNamedStopped', { name: plugin.name }))
      await load()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  // 静态插件（内容包/主题）没有进程开关，用 config 的 enabled 作为列表开关，勾选即保存生效。
  async function toggleEnabled(plugin: PluginInfo, enabled: boolean) {
    if (!plugin.config) plugin.config = {}
    plugin.config.enabled = enabled
    await save(plugin)
  }

  function onPluginFile(event: Event) {
    installFile.value = (event.target as HTMLInputElement).files?.[0] || null
  }

  async function installPlugin() {
    if (!installFile.value) {
      toast.error(t('selectPluginZip'))
      return
    }
    busy.value = 'install'
    try {
      const body = new FormData()
      body.append('file', installFile.value)
      body.append('overwrite', overwriteInstall.value ? 'true' : 'false')
      await pluginApi.install(body)
      toast.success(t('pluginZipInstalled'))
      installFile.value = null
      overwriteInstall.value = false
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  async function rescanLocalPlugins() {
    busy.value = 'rescan'
    try {
      await pluginApi.rescan()
      toast.success(t('pluginsRescanned'))
      await refreshSurfaces()
    } catch (error: unknown) {
      toast.error(errorMessage(error))
    } finally {
      busy.value = ''
    }
  }

  return {
    plugins,
    expandedPluginNames,
    loading,
    installFile,
    overwriteInstall,
    load,
    ordered,
    value,
    textValue,
    selectValue,
    numberValue,
    set,
    listValue,
    secretPlaceholder,
    showGroup,
    parseList,
    save,
    restart,
    clearCardCache,
    toggleRunning,
    toggleEnabled,
    onPluginFile,
    installPlugin,
    rescanLocalPlugins,
  }
}
