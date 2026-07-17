<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NButton, NCheckbox, NCollapse, NCollapseItem, NIcon, NInput, NInputNumber,
  NSelect, NSpin, NSwitch, NTabPane, NTabs, NTag,
} from 'naive-ui'
import {
  AddOutline, ChevronDown, ChevronUp, CloudDownloadOutline, CreateOutline,
  ExtensionPuzzleOutline, RefreshOutline, TrashOutline,
} from '@vicons/ionicons5'
import { api, errorMessage } from '@/api/client'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import type {
  PluginContentImportResponse, PluginContentResource, PluginContentResponse,
  PluginField, PluginInfo, PluginMarketplaceItem, PluginMarketplaceResponse,
  PluginMirror, PluginMirrorsResponse, PluginMirrorTestResponse, WorldListResponse,
} from '@/api/types'
import NapcatGuide from '@/components/plugins/NapcatGuide.vue'

const toast = useToast()
const { t } = useLocale()
const { pluginThemes, pluginThemeId, loadPluginThemes, applyPluginTheme, clearPluginTheme } = useTheme()
const plugins = ref<PluginInfo[]>([])
const contentResources = ref<Record<string, PluginContentResource[]>>({})
const marketplace = ref<PluginMarketplaceItem[]>([])
const mirrors = ref<PluginMirror[]>([])
const mirrorTests = ref<Record<string, string>>({})
const worlds = ref<WorldListResponse['worlds']>([])
const marketplaceSource = ref<PluginMarketplaceResponse['source'] | null>(null)
const expandedPluginNames = ref<string[]>([])
const loading = ref(false)
const marketLoading = ref(false)
const mirrorLoading = ref(false)
const busy = ref('')
const installFile = ref<File | null>(null)
const overwriteInstall = ref(false)
const marketKeyword = ref('')
const contentLoading = ref(false)
const contentTargetWorldId = ref('')
const newMirror = reactive<PluginMirror>({
  id: '',
  name: '',
  raw_prefix: '',
  clone_prefix: '',
  enabled: true,
  priority: 1,
})
const themeOptions = computed(() => pluginThemes.value.map(theme => ({
  label: `${theme.name}${theme.plugin_name ? ` · ${theme.plugin_name}` : ''}`,
  value: theme.id,
})))
const contentGroupDefs = [
  { key: 'character_template', labelKey: 'contentGroupCharacterTemplate' },
  { key: 'npc', labelKey: 'contentGroupNpc' },
  { key: 'item', labelKey: 'contentGroupItem' },
  { key: 'spell', labelKey: 'contentGroupSpell' },
  { key: 'class', labelKey: 'contentGroupClass' },
] satisfies { key: string; labelKey: MessageKey }[]
const contentGroups = computed(() => contentGroupDefs.map(group => ({ ...group, items: contentResources.value[group.key] || [] })))
const worldOptions = computed(() => (worlds.value || []).map(world => {
  const id = String(world.id || world.world_id || '')
  return {
    label: String(world.name || world.world_name || id),
    value: id,
  }
}).filter(item => item.value))

const filteredMarketplace = computed(() => {
  const keyword = marketKeyword.value.trim().toLowerCase()
  if (!keyword) return marketplace.value
  return marketplace.value.filter(item => [
    item.id, item.name, item.description, item.repository_url, ...(item.tags || []),
  ].some(value => String(value || '').toLowerCase().includes(keyword)))
})

async function load() {
  loading.value = true
  try {
    const r = await api<{ plugins: PluginInfo[] }>('/plugins')
    plugins.value = r.plugins || []
    if (!expandedPluginNames.value.length) expandedPluginNames.value = plugins.value.map(p => p.id)
    await loadPluginThemes()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    loading.value = false
  }
}

async function loadMarketplace() {
  marketLoading.value = true
  try {
    const r = await api<PluginMarketplaceResponse>('/plugins/marketplace')
    if (!r.ok) throw new Error(r.error || t('pluginMarketplaceLoadFailed'))
    marketplace.value = r.plugins || []
    marketplaceSource.value = r.source || null
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    marketLoading.value = false
  }
}

async function loadMirrors() {
  mirrorLoading.value = true
  try {
    const r = await api<PluginMirrorsResponse>('/plugins/mirrors')
    mirrors.value = r.mirrors || []
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    mirrorLoading.value = false
  }
}

async function loadContentResources() {
  contentLoading.value = true
  try {
    const r = await api<PluginContentResponse>('/plugins/content')
    if (!r.ok) throw new Error(r.error || t('pluginContentLoadFailed'))
    contentResources.value = r.resources || {}
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    contentLoading.value = false
  }
}

async function loadWorlds() {
  try {
    const r = await api<WorldListResponse>('/worlds')
    worlds.value = r.worlds || []
    if (!contentTargetWorldId.value && worldOptions.value.length) {
      contentTargetWorldId.value = String(worldOptions.value[0].value)
    }
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

function ordered(p: PluginInfo): [string, PluginField][] {
  return Object.entries(p.schema?.properties || {}).sort((a, b) => (a[1].ui?.order || 0) - (b[1].ui?.order || 0))
}
function value(p: PluginInfo, key: string, field: PluginField): unknown {
  const v = p.config?.[key]
  return typeof v === 'object' && field.ui?.sensitive ? '' : v ?? field.default ?? ''
}
function textValue(p: PluginInfo, key: string, field: PluginField): string {
  const v = value(p, key, field)
  return typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v)
}
function selectValue(p: PluginInfo, key: string, field: PluginField): string | number | null {
  const v = value(p, key, field)
  return typeof v === 'string' || typeof v === 'number' ? v : null
}
function numberValue(p: PluginInfo, key: string, field: PluginField): number | null {
  const v = value(p, key, field)
  return typeof v === 'number' ? v : v === '' || v === null || v === undefined ? null : Number(v)
}
function set(p: PluginInfo, key: string, v: unknown) {
  if (!p.config) p.config = {}
  p.config[key] = v
}
function listValue(p: PluginInfo, key: string, field: PluginField): string[] {
  const v = value(p, key, field)
  return Array.isArray(v) ? v : []
}
function secretPlaceholder(p: PluginInfo, key: string, field: PluginField): string {
  const v = p.config?.[key] as { configured?: boolean; masked?: string } | undefined
  return field.ui?.sensitive && v?.configured ? t('secretConfiguredPlaceholder', { masked: v.masked || '' }) : ''
}
function showGroup(fields: [string, PluginField][], index: number): boolean {
  const group = fields[index][1].ui?.group
  return !!group && (index === 0 || fields[index - 1][1].ui?.group !== group)
}
function parseList(input: string): string[] {
  return Array.from(new Set(input.split(/[\n,]+/).map(x => x.trim()).filter(Boolean)))
}
function validate(p: PluginInfo): string {
  for (const [key, field] of ordered(p)) {
    const v = value(p, key, field)
    if (field.type === 'number' || field.type === 'integer') {
      const n = Number(v)
      if (field.exclusiveMinimum !== undefined && n <= field.exclusiveMinimum) return t('validationGreaterThan', { field: field.title || key, value: field.exclusiveMinimum })
      if (field.minimum !== undefined && n < field.minimum) return t('validationAtLeast', { field: field.title || key, value: field.minimum })
      if (field.maximum !== undefined && n > field.maximum) return t('validationAtMost', { field: field.title || key, value: field.maximum })
    }
    if (field.type === 'string') {
      const s = String(v || '')
      if (field.minLength !== undefined && s.length > 0 && s.length < field.minLength) return t('validationMinLength', { field: field.title || key, value: field.minLength })
      if (field.maxLength !== undefined && s.length > field.maxLength) return t('validationMaxLength', { field: field.title || key, value: field.maxLength })
    }
  }
  return ''
}
async function save(p: PluginInfo) {
  const err = validate(p)
  if (err) { toast.error(err); return }
  busy.value = p.id
  try {
    const payload: Record<string, unknown> = {}
    for (const [key, field] of ordered(p)) {
      const current = p.config?.[key]
      if (field.ui?.sensitive) {
        if (typeof current === 'string' && current.trim()) payload[key] = current
      } else if (current !== undefined) {
        payload[key] = current
      }
    }
    await api(`/plugins/${encodeURIComponent(p.id)}/config`, { method: 'PUT', body: JSON.stringify(payload) })
    toast.success(t('pluginNamedSaved', { name: p.name }))
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function restart(p: PluginInfo) {
  busy.value = p.id
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}/restart`, { method: 'POST' })
    toast.success(t('pluginNamedRestartRequested', { name: p.name }))
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function clearCardCache(p: PluginInfo) {
  if (!window.confirm(t('confirmClearCardCache'))) return
  busy.value = `${p.id}:card-cache`
  try {
    const r = await api<{ deleted?: number; bytes_deleted?: number }>(`/plugins/${encodeURIComponent(p.id)}/card-cache/clear`, { method: 'POST' })
    const deleted = r.deleted || 0
    const mb = ((r.bytes_deleted || 0) / 1024 / 1024).toFixed(2)
    toast.success(t('cardCacheCleared', { count: deleted, mb }))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function toggleRunning(p: PluginInfo, on: boolean) {
  busy.value = p.id
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}/${on ? 'start' : 'stop'}`, { method: 'POST' })
    toast.success(t(on ? 'pluginNamedStarted' : 'pluginNamedStopped', { name: p.name }))
    await load()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
function onPluginFile(event: Event) {
  const input = event.target as HTMLInputElement
  installFile.value = input.files?.[0] || null
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
    await api('/plugins/install', { method: 'POST', body })
    toast.success(t('pluginZipInstalled'))
    installFile.value = null
    overwriteInstall.value = false
    await load()
    await loadMarketplace()
    await loadPluginThemes()
    await loadContentResources()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function installMarketPlugin(item: PluginMarketplaceItem) {
  busy.value = `market:${item.id}`
  try {
    await api('/plugins/marketplace/install', {
      method: 'POST',
      body: JSON.stringify({ plugin_id: item.id, overwrite: item.installed }),
    })
    toast.success(t(item.installed ? 'pluginNamedUpdated' : 'pluginNamedInstalled', { name: item.name }))
    await load()
    await loadMarketplace()
    await loadPluginThemes()
    await loadContentResources()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function updateInstalledPlugin(p: PluginInfo) {
  busy.value = `${p.id}:update`
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}/update`, { method: 'POST' })
    toast.success(t('pluginNamedUpdated', { name: p.name }))
    await load()
    await loadMarketplace()
    await loadPluginThemes()
    await loadContentResources()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function uninstallPlugin(p: PluginInfo) {
  const message = t('confirmUninstallPlugin', { name: p.name })
  if (!window.confirm(message)) return
  busy.value = `${p.id}:uninstall`
  try {
    await api(`/plugins/${encodeURIComponent(p.id)}`, { method: 'DELETE', body: JSON.stringify({ delete_data: false }) })
    toast.success(t('pluginNamedUninstalled', { name: p.name }))
    await load()
    await loadMarketplace()
    await loadPluginThemes()
    await loadContentResources()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function addMirror() {
  busy.value = 'mirror:add'
  try {
    await api('/plugins/mirrors', { method: 'POST', body: JSON.stringify(newMirror) })
    toast.success(t('mirrorAdded'))
    Object.assign(newMirror, { id: '', name: '', raw_prefix: '', clone_prefix: '', enabled: true, priority: mirrors.value.length + 1 })
    await loadMirrors()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function saveMirror(mirror: PluginMirror, patch: Partial<PluginMirror>) {
  busy.value = `mirror:${mirror.id}`
  try {
    await api(`/plugins/mirrors/${encodeURIComponent(mirror.id)}`, { method: 'PUT', body: JSON.stringify(patch) })
    await loadMirrors()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function deleteMirror(mirror: PluginMirror) {
  if (!window.confirm(t('confirmDeleteMirror', { name: mirror.name }))) return
  busy.value = `mirror:${mirror.id}`
  try {
    await api(`/plugins/mirrors/${encodeURIComponent(mirror.id)}`, { method: 'DELETE' })
    toast.success(t('mirrorDeleted'))
    await loadMirrors()
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
async function testMirror(mirror?: PluginMirror) {
  const key = mirror?.id || 'all'
  busy.value = `mirror-test:${key}`
  try {
    const r = await api<PluginMirrorTestResponse>('/plugins/mirrors/test', {
      method: 'POST',
      body: JSON.stringify({ mirror_id: mirror?.id || '' }),
    })
    for (const result of r.results || []) {
      const id = result.mirror_id || 'all'
      mirrorTests.value[id] = result.ok
        ? t('mirrorAvailable', { ms: result.elapsed_ms || 0 })
        : t('mirrorFailed', { reason: result.error || result.status || t('unknownError') })
    }
    toast[r.ok ? 'success' : 'error'](r.ok ? t('mirrorTestDone') : (r.error || t('allMirrorTestsFailed')))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}
function openUrl(url?: string) {
  if (url) window.open(url, '_blank', 'noopener')
}
function pluginTypeLabel(type?: string): string {
  const labels: Record<string, MessageKey> = {
    'channel-adapter': 'pluginTypeChannelAdapter',
    'content-pack': 'pluginTypeContentPack',
    'theme': 'pluginTypeTheme',
    'map-pack': 'pluginTypeMapPack',
    'import-export': 'pluginTypeImportExport',
    'provider': 'pluginTypeProvider',
    'tool': 'pluginTypeTool',
  }
  return labels[type || ''] ? t(labels[type || '']) : type || t('uncategorized')
}
function permissionDescription(p: PluginInfo, permission: string): string {
  return p.permission_details?.find(item => item.id === permission)?.description || permission
}

function selectedThemeDescription(): string {
  const theme = pluginThemes.value.find(item => item.id === pluginThemeId.value)
  return theme?.description || ''
}
function selectPluginTheme(value: string | null) {
  applyPluginTheme(value)
}
function contentTitle(item: PluginContentResource): string {
  return String(item.character_name || item.name || item.id || t('unnamed'))
}
function contentSubtitle(item: PluginContentResource): string {
  return [item.plugin_name || item.plugin_id || '', item.description || ''].filter(Boolean).join(' · ')
}
async function importContent(kind: string, item: PluginContentResource) {
  if (kind !== 'character_template' && !contentTargetWorldId.value) {
    toast.error(t('selectLorebookTarget'))
    return
  }
  const key = `${kind}:${item.plugin_id}:${item.id || item.name || item.character_name}`
  busy.value = key
  try {
    const r = await api<PluginContentImportResponse>('/plugins/content/import', {
      method: 'POST',
      body: JSON.stringify({
        kind,
        id: item.id,
        plugin_id: item.plugin_id,
        target_world_id: kind === 'character_template' ? '' : contentTargetWorldId.value,
      }),
    })
    if (!r.ok) throw new Error(r.error || t('importFailed'))
    toast.success(kind === 'character_template' ? t('importedCharacterLibrary') : t('importedLorebook'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  await load()
  await Promise.all([loadMarketplace(), loadMirrors(), loadContentResources(), loadWorlds()])
})
</script>

<template>
  <NTabs type="line" animated>
    <NTabPane name="installed" :tab="t('pluginsInstalledTab')">
      <NSpin :show="loading">
        <section class="plugin-install">
          <div>
            <h3>{{ t('installPluginTitle') }}</h3>
            <p class="muted">{{ t('installPluginHelp') }}</p>
          </div>
          <div class="install-controls">
            <input type="file" accept=".zip,application/zip" :aria-label="t('pluginZipAria')" @change="onPluginFile">
            <NCheckbox v-model:checked="overwriteInstall">{{ t('overwriteSameIdPlugin') }}</NCheckbox>
            <NButton type="primary" :disabled="!installFile" :loading="busy === 'install'" @click="installPlugin">
              <template #icon><NIcon :component="CloudDownloadOutline" /></template>
              {{ t('install') }}
            </NButton>
          </div>
        </section>

        <p v-if="!plugins.length" class="muted">{{ t('noPluginsAvailable') }}</p>

        <NCollapse v-model:expanded-names="expandedPluginNames">
          <NCollapseItem v-for="p in plugins" :key="p.id" :name="p.id" class="plugin-collapsible">
            <template #header>
              <div class="plugin-head">
                <h3>{{ p.name }}</h3>
                <p class="muted">{{ p.description }}</p>
              </div>
            </template>
            <template #header-extra>
              <div class="plugin-extra" @click.stop>
                <NTag size="small">{{ pluginTypeLabel(p.plugin_type) }}</NTag>
                <NTag :type="p.running ? 'success' : 'default'" size="small">{{ p.status }}</NTag>
                <NSwitch v-if="p.has_entrypoint" :value="p.running" :disabled="busy === p.id" @update:value="toggleRunning(p, $event)" />
              </div>
            </template>

            <NTabs type="line" animated class="plugin-tabs">
              <NTabPane name="config" :tab="t('config')">
                <section v-if="p.permissions?.length" class="permission-panel">
                  <h4>{{ t('permissions') }}</h4>
                  <div class="permission-list">
                    <NTag v-for="permission in p.permissions" :key="permission" size="small">
                      {{ permission }}
                    </NTag>
                  </div>
                  <p class="muted">{{ p.permissions.map(permission => permissionDescription(p, permission)).join('；') }}</p>
                </section>
                <div class="plugin-form-grid">
                  <template v-for="(entry, i) in ordered(p)" :key="entry[0]">
                    <h4 v-if="showGroup(ordered(p), i)" class="field-group">{{ entry[1].ui?.group }}</h4>
                    <div class="field" :class="{ 'field-wide': entry[1].type === 'array' }">
                      <label v-if="entry[1].type === 'boolean'" class="switch-label">
                        <NSwitch :value="!!value(p, entry[0], entry[1])" :aria-label="entry[1].title || entry[0]" @update:value="set(p, entry[0], $event)" />
                        <span>{{ entry[1].title || entry[0] }}</span>
                      </label>
                      <label v-else class="input-label">
                        <span class="field-title">{{ entry[1].title || entry[0] }}</span>
                        <NSelect
                          v-if="entry[1].enum"
                          :value="selectValue(p, entry[0], entry[1])"
                          :options="(entry[1].enum || []).map(x => ({ label: x, value: x }))"
                          @update:value="set(p, entry[0], $event)"
                        />
                        <NInput
                          v-else-if="entry[1].type === 'array'"
                          type="textarea"
                          :rows="4"
                          :input-props="{ 'aria-label': entry[1].title || entry[0] }"
                          :value="listValue(p, entry[0], entry[1]).join('\n')"
                          :placeholder="t('arrayInputPlaceholder')"
                          @update:value="set(p, entry[0], parseList($event))"
                        />
                        <NInput
                          v-else-if="entry[1].ui?.sensitive"
                          type="password"
                          show-password-on="click"
                          :placeholder="secretPlaceholder(p, entry[0], entry[1])"
                          :value="textValue(p, entry[0], entry[1])"
                          @update:value="set(p, entry[0], $event)"
                        />
                        <NInputNumber
                          v-else-if="entry[1].type === 'number' || entry[1].type === 'integer'"
                          :value="numberValue(p, entry[0], entry[1])"
                          @update:value="set(p, entry[0], $event)"
                        />
                        <NInput
                          v-else
                          :value="textValue(p, entry[0], entry[1])"
                          @update:value="set(p, entry[0], $event)"
                        />
                      </label>
                      <small v-if="entry[1].description" class="muted">{{ entry[1].description }}</small>
                    </div>
                  </template>
                </div>
              </NTabPane>
              <NTabPane v-if="p.id === 'qq-napcat'" name="guide" :tab="t('guideDocs')">
                <NapcatGuide />
              </NTabPane>
            </NTabs>

            <div class="actions-row">
              <NButton type="primary" :loading="busy === p.id" @click="save(p)">{{ t('saveConfig') }}</NButton>
              <NButton v-if="p.has_entrypoint" :loading="busy === p.id" @click="restart(p)">
                <template #icon><NIcon :component="RefreshOutline" /></template>
                {{ t('restartPlugin') }}
              </NButton>
              <NButton secondary :loading="busy === `${p.id}:update`" @click="updateInstalledPlugin(p)">
                <template #icon><NIcon :component="CloudDownloadOutline" /></template>
                {{ t('updateFromStore') }}
              </NButton>
              <NButton v-if="p.id === 'qq-napcat'" secondary :loading="busy === `${p.id}:card-cache`" @click="clearCardCache(p)">{{ t('clearCardCache') }}</NButton>
              <NButton tertiary type="error" :loading="busy === `${p.id}:uninstall`" @click="uninstallPlugin(p)">
                <template #icon><NIcon :component="TrashOutline" /></template>
                {{ t('uninstallPlugin') }}
              </NButton>
            </div>
            <p v-if="p.has_entrypoint" class="muted hint">{{ t('pluginRestartHint') }}</p>
            <p v-else class="muted hint">{{ t('declarativePluginHint') }}</p>
          </NCollapseItem>
        </NCollapse>
      </NSpin>
    </NTabPane>

    <NTabPane name="marketplace" :tab="t('pluginMarketplaceTab')">
      <section class="toolbar-row">
        <NInput v-model:value="marketKeyword" :placeholder="t('pluginSearchPlaceholder')" clearable />
        <NButton :loading="marketLoading" @click="loadMarketplace">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('refresh') }}
        </NButton>
      </section>
      <p v-if="marketplaceSource?.mirror_name" class="muted source-line">
        {{ t('source') }}: {{ marketplaceSource.mirror_name }}, {{ marketplaceSource.elapsed_ms || 0 }} ms
      </p>
      <NSpin :show="marketLoading">
        <div class="market-grid">
          <article v-for="item in filteredMarketplace" :key="item.id" class="market-card">
            <div class="market-title">
              <NIcon :component="ExtensionPuzzleOutline" />
              <div>
                <h3>{{ item.name }}</h3>
                <p class="muted">{{ item.id }} · {{ item.version || t('unknownVersion') }}</p>
              </div>
            </div>
            <p class="market-desc">{{ item.description || t('noDescription') }}</p>
            <div class="tag-row">
              <NTag v-if="item.plugin_type" size="small">{{ pluginTypeLabel(item.plugin_type) }}</NTag>
              <NTag v-if="item.installed" type="success" size="small">{{ t('installedVersion', { version: item.installed_version || '' }) }}</NTag>
              <NTag v-for="tag in item.tags || []" :key="tag" size="small">{{ tag }}</NTag>
            </div>
            <p v-if="item.permissions?.length" class="muted market-permissions">
              {{ t('permissions') }}: {{ item.permissions.slice(0, 4).join(t('listSeparator')) }}{{ item.permissions.length > 4 ? t('andMore') : '' }}
            </p>
            <div class="market-actions">
              <NButton type="primary" :loading="busy === `market:${item.id}`" @click="installMarketPlugin(item)">
                <template #icon><NIcon :component="CloudDownloadOutline" /></template>
                {{ item.installed ? t('update') : t('install') }}
              </NButton>
              <NButton secondary :disabled="!item.repository_url && !item.homepage" @click="openUrl(item.repository_url || item.homepage)">
                {{ t('openRepository') }}
              </NButton>
            </div>
          </article>
        </div>
        <p v-if="!filteredMarketplace.length" class="muted">{{ t('marketplaceNoMatches') }}</p>
      </NSpin>
    </NTabPane>

    <NTabPane name="themes" :tab="t('themes')">
      <section class="theme-plugin-panel">
        <div>
          <h3>{{ t('pluginThemes') }}</h3>
          <p class="muted">{{ t('pluginThemesHelp') }}</p>
        </div>
        <div class="theme-plugin-controls">
          <NSelect
            :value="pluginThemeId || null"
            :options="themeOptions"
            :placeholder="t('selectEnabledThemePlugin')"
            clearable
            @update:value="selectPluginTheme"
          />
          <NButton :disabled="!pluginThemeId" @click="clearPluginTheme">{{ t('clear') }}</NButton>
          <NButton @click="loadPluginThemes">{{ t('refresh') }}</NButton>
        </div>
        <p v-if="selectedThemeDescription()" class="muted">{{ selectedThemeDescription() }}</p>
        <p v-if="!pluginThemes.length" class="muted">{{ t('noEnabledThemePlugins') }}</p>
      </section>
    </NTabPane>

    <NTabPane name="content" :tab="t('contentPacks')">
      <section class="toolbar-row">
        <NSelect
          v-model:value="contentTargetWorldId"
          :options="worldOptions"
          :placeholder="t('selectLorebook')"
          class="content-world-select"
        />
        <NButton :loading="contentLoading" @click="loadContentResources">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('refresh') }}
        </NButton>
      </section>
      <NSpin :show="contentLoading">
        <div class="content-catalog">
          <section v-for="group in contentGroups" :key="group.key" class="content-group">
            <h3>{{ t(group.labelKey) }} <span class="muted">{{ group.items.length }}</span></h3>
            <div v-if="group.items.length" class="content-list">
              <article v-for="item in group.items" :key="`${group.key}:${item.plugin_id}:${item.id || item.name || item.character_name}`" class="content-item">
                <div class="content-item-main">
                  <strong>{{ contentTitle(item) }}</strong>
                  <p class="muted">{{ contentSubtitle(item) || t('noDescription') }}</p>
                </div>
                <NButton
                  size="small"
                  secondary
                  :disabled="group.key !== 'character_template' && !contentTargetWorldId"
                  :loading="busy === `${group.key}:${item.plugin_id}:${item.id || item.name || item.character_name}`"
                  @click="importContent(group.key, item)"
                >
                  {{ group.key === 'character_template' ? t('importCharacterCard') : t('importLorebook') }}
                </NButton>
              </article>
            </div>
            <p v-else class="muted">{{ t('none') }}</p>
          </section>
        </div>
      </NSpin>
    </NTabPane>

    <NTabPane name="mirrors" :tab="t('mirrorSources')">
      <section class="toolbar-row">
        <NButton :loading="mirrorLoading" @click="loadMirrors">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('refresh') }}
        </NButton>
        <NButton :loading="busy === 'mirror-test:all'" @click="testMirror()">
          {{ t('testAll') }}
        </NButton>
      </section>

      <div class="mirror-form">
        <NInput v-model:value="newMirror.id" :placeholder="t('mirrorIdPlaceholder')" />
        <NInput v-model:value="newMirror.name" :placeholder="t('name')" />
        <NInput v-model:value="newMirror.raw_prefix" class="mirror-url-input" :placeholder="t('rawPrefix')" />
        <NInput v-model:value="newMirror.clone_prefix" class="mirror-url-input" :placeholder="t('clonePrefix')" />
        <NInputNumber v-model:value="newMirror.priority" :min="1" :placeholder="t('priority')" />
        <NSwitch v-model:value="newMirror.enabled" />
        <NButton type="primary" :loading="busy === 'mirror:add'" @click="addMirror">
          <template #icon><NIcon :component="AddOutline" /></template>
          {{ t('add') }}
        </NButton>
      </div>

      <NSpin :show="mirrorLoading">
        <div class="mirror-list">
          <article v-for="mirror in mirrors" :key="mirror.id" class="mirror-row">
            <div class="mirror-main">
              <div class="mirror-heading">
                <NSwitch :value="mirror.enabled" @update:value="saveMirror(mirror, { enabled: $event })" />
                <strong>{{ mirror.name }}</strong>
                <NTag size="small">{{ mirror.id }}</NTag>
                <NTag size="small">{{ t('priority') }} {{ mirror.priority }}</NTag>
              </div>
              <p class="muted">Raw：{{ mirror.raw_prefix }}</p>
              <div class="mirror-edit-grid">
                <NInput v-model:value="mirror.name" :placeholder="t('name')" />
                <NInput v-model:value="mirror.raw_prefix" class="mirror-url-input" :placeholder="t('rawPrefix')" />
                <NInput v-model:value="mirror.clone_prefix" class="mirror-url-input" :placeholder="t('downloadPrefix')" />
                <NInputNumber v-model:value="mirror.priority" :min="1" />
              </div>
              <p v-if="mirrorTests[mirror.id]" class="mirror-test">{{ mirrorTests[mirror.id] }}</p>
            </div>
            <div class="mirror-actions">
              <NButton size="small" :loading="busy === `mirror-test:${mirror.id}`" @click="testMirror(mirror)">{{ t('test') }}</NButton>
              <NButton size="small" @click="saveMirror(mirror, { priority: Math.max(1, mirror.priority - 1) })">
                <template #icon><NIcon :component="ChevronUp" /></template>
              </NButton>
              <NButton size="small" @click="saveMirror(mirror, { priority: mirror.priority + 1 })">
                <template #icon><NIcon :component="ChevronDown" /></template>
              </NButton>
              <NButton size="small" @click="saveMirror(mirror, mirror)">
                <template #icon><NIcon :component="CreateOutline" /></template>
                {{ t('saveAction') }}
              </NButton>
              <NButton size="small" type="error" tertiary @click="deleteMirror(mirror)">
                <template #icon><NIcon :component="TrashOutline" /></template>
              </NButton>
            </div>
          </article>
        </div>
      </NSpin>
    </NTabPane>
  </NTabs>
</template>

<style scoped>
.plugin-head h3,
.market-card h3 {
  margin: 0;
}

.plugin-install,
.theme-plugin-panel,
.mirror-form,
.mirror-row,
.market-card {
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, var(--panel), var(--panel-2));
}

.plugin-install {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
  padding: 16px;
}

.theme-plugin-panel {
  display: grid;
  gap: 14px;
  padding: 16px;
}

.theme-plugin-panel h3 {
  margin: 0;
  color: var(--gold-2);
}

.theme-plugin-panel p {
  margin: 4px 0 0;
}

.theme-plugin-controls {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto auto;
  gap: 10px;
  align-items: center;
}

.content-catalog {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.content-world-select {
  width: min(360px, 100%);
}

.content-group {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-soft);
}

.content-group h3 {
  margin: 0 0 10px;
  color: var(--gold-2);
  font-size: 15px;
}

.content-list {
  display: grid;
  gap: 8px;
}

.content-item {
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: rgba(255, 255, 255, .03);
  display: grid;
  gap: 8px;
}

.content-item strong,
.content-item p {
  overflow-wrap: anywhere;
}

.content-item-main {
  min-width: 0;
}

.content-item p {
  margin: 4px 0 0;
  line-height: 1.45;
}

.plugin-install h3 {
  margin: 0;
  color: var(--gold-2);
}

.plugin-install p {
  margin: 4px 0 0;
}

.install-controls,
.plugin-extra,
.actions-row,
.toolbar-row,
.tag-row,
.market-actions,
.mirror-heading,
.mirror-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.install-controls {
  justify-content: flex-end;
}

.toolbar-row {
  margin-bottom: 14px;
}

.source-line {
  margin: -4px 0 14px;
}

.plugin-head p {
  margin: 4px 0 0;
}

.plugin-tabs {
  margin-top: 4px;
}

.permission-panel {
  display: grid;
  gap: 8px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--panel-soft);
}

.permission-panel h4 {
  margin: 0;
  color: var(--gold-2);
  font-size: 14px;
}

.permission-panel p {
  margin: 0;
  line-height: 1.55;
}

.permission-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.plugin-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 14px 18px;
  align-items: start;
}

.field-group {
  grid-column: 1 / -1;
  margin: 10px 0 -2px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, .08);
  color: var(--gold-2, #d99b45);
  font-size: 14px;
}

.field-group:first-child {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}

.field {
  min-width: 0;
}

.field-wide {
  grid-column: 1 / -1;
}

.input-label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.switch-label {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 34px;
}

.field-title {
  font-size: 13px;
  color: var(--text, #d7d1c5);
}

.field small {
  display: block;
  margin-top: 5px;
  line-height: 1.45;
}

.actions-row {
  margin-top: 16px;
}

.hint {
  margin-top: 8px;
}

.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.market-card {
  padding: 16px;
  min-width: 0;
}

.market-title {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 10px;
  align-items: start;
}

.market-title p,
.market-desc {
  margin: 5px 0 0;
}

.market-desc {
  min-height: 42px;
  color: var(--text);
  line-height: 1.55;
}

.market-permissions {
  min-height: 1.5em;
  margin: -4px 0 10px;
}

.tag-row {
  margin: 12px 0;
}

.mirror-form {
  display: grid;
  grid-template-columns: minmax(120px, .7fr) minmax(140px, .8fr) minmax(180px, 1.2fr) minmax(180px, 1.2fr) minmax(96px, .5fr) auto auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  padding: 14px;
  max-width: 100%;
  overflow: hidden;
}

.mirror-list {
  display: grid;
  gap: 12px;
}

.mirror-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: flex-start;
  padding: 14px;
  max-width: 100%;
  overflow: hidden;
}

.mirror-main {
  min-width: 0;
}

.mirror-main p {
  margin: 6px 0 0;
  word-break: break-all;
}

.mirror-edit-grid {
  display: grid;
  grid-template-columns: minmax(120px, .8fr) minmax(160px, 1.2fr) minmax(160px, 1.2fr) minmax(90px, .5fr);
  gap: 8px;
  margin-top: 10px;
  min-width: 0;
}

.mirror-test {
  color: var(--gold-2);
}

.mirror-actions {
  justify-content: flex-end;
  max-width: 100%;
}

.mirror-form :deep(.n-input),
.mirror-form :deep(.n-input-number),
.mirror-edit-grid :deep(.n-input),
.mirror-edit-grid :deep(.n-input-number) {
  min-width: 0;
  width: 100%;
}

@media (max-width: 1180px) {
  .mirror-form {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .mirror-form .mirror-url-input {
    grid-column: 1 / -1;
  }

  .mirror-edit-grid {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .mirror-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .mirror-form,
  .mirror-edit-grid {
    grid-template-columns: 1fr;
  }

  .plugin-install {
    align-items: stretch;
    flex-direction: column;
  }

  .theme-plugin-controls {
    grid-template-columns: 1fr;
  }

  .install-controls,
  .mirror-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 860px) {
  .plugin-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
