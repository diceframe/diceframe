<script setup lang="ts">
import { NButton, NCheckbox, NCollapse, NCollapseItem, NIcon, NInput, NInputNumber, NSelect, NSwitch, NSpin, NTabPane, NTabs, NTag } from 'naive-ui'
import { CloudDownloadOutline, RefreshOutline, TrashOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { PluginField, PluginInfo } from '@/api/types'
import NapcatGuide from '@/components/plugins/NapcatGuide.vue'

defineProps<{
  loading: boolean
  plugins: PluginInfo[]
  filteredPlugins: PluginInfo[]
  expandedPluginNames: string[]
  typeFilter: string
  pluginTypeFilters: { value: string; labelKey: string }[]
  busy: string
  installFile: File | null
  overwriteInstall: boolean
  pluginDocs: Record<string, { content: string; name: string }>
  pluginDocsLoading: Record<string, boolean>
  canUpdateFromStore: (id: string, version: string | undefined) => boolean
  onPluginFile: (event: Event) => void
  installPlugin: () => Promise<void> | void
  rescanLocalPlugins: () => Promise<void> | void
  toggleRunning: (plugin: PluginInfo, running: boolean) => Promise<void> | void
  toggleEnabled: (plugin: PluginInfo, enabled: boolean) => Promise<void> | void
  ordered: (plugin: PluginInfo) => [string, PluginField][]
  value: (plugin: PluginInfo, key: string, field: PluginField) => unknown
  textValue: (plugin: PluginInfo, key: string, field: PluginField) => string
  selectValue: (plugin: PluginInfo, key: string, field: PluginField) => string | number | null
  numberValue: (plugin: PluginInfo, key: string, field: PluginField) => number | null
  set: (plugin: PluginInfo, key: string, next: unknown) => void
  listValue: (plugin: PluginInfo, key: string, field: PluginField) => string[]
  secretPlaceholder: (plugin: PluginInfo, key: string, field: PluginField) => string
  showGroup: (fields: [string, PluginField][], index: number) => boolean
  parseList: (input: string) => string[]
  save: (plugin: PluginInfo) => Promise<unknown> | unknown
  restart: (plugin: PluginInfo) => Promise<void> | void
  clearCardCache: (plugin: PluginInfo) => Promise<void> | void
  updateInstalledPlugin: (plugin: PluginInfo) => Promise<void> | void
  uninstallPlugin: (plugin: PluginInfo) => Promise<void> | void
  permissionDescription: (plugin: PluginInfo, permission: string) => string
  pluginTypeLabel: (type: string | undefined) => string
  loadPluginDocs: (pluginId: string) => Promise<void> | void
  renderDocsMarkdown: (markdown: string) => string
}>()
const emit = defineEmits<{
  'update:typeFilter': [value: string]
  'update:expandedPluginNames': [value: string[]]
  'update:overwriteInstall': [value: boolean]
}>()

const { t } = useLocale()
</script>

<template>
  <NSpin :show="loading && !plugins.length">
    <section class="plugin-install">
      <div>
        <h3>{{ t('installPluginTitle') }}</h3>
        <p class="muted">{{ t('installPluginHelp') }}</p>
      </div>
      <div class="install-controls">
        <input type="file" accept=".dfplugin" :aria-label="t('pluginZipAria')" @change="onPluginFile">
        <NCheckbox :checked="overwriteInstall" @update:checked="(v) => emit('update:overwriteInstall', !!v)">{{ t('overwriteSameIdPlugin') }}</NCheckbox>
        <NButton type="primary" :disabled="!installFile" :loading="busy === 'install'" @click="installPlugin">
          <template #icon><NIcon :component="CloudDownloadOutline" /></template>
          {{ t('install') }}
        </NButton>
        <NButton secondary :loading="busy === 'rescan'" @click="rescanLocalPlugins">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('rescanLocalPlugins') }}
        </NButton>
      </div>
    </section>

    <div class="type-filter-row">
      <NButton size="tiny" :type="typeFilter === '' ? 'primary' : 'default'" @click="emit('update:typeFilter', '')">{{ t('pluginFilterAll') }}</NButton>
      <NButton v-for="opt in pluginTypeFilters" :key="opt.value" size="tiny" :type="typeFilter === opt.value ? 'primary' : 'default'" @click="emit('update:typeFilter', opt.value)">{{ t(opt.labelKey as never) }}</NButton>
    </div>

    <p v-if="!filteredPlugins.length" class="muted">{{ plugins.length ? t('pluginTypeFilterEmpty') : t('noPluginsAvailable') }}</p>

    <NCollapse :expanded-names="expandedPluginNames" @update:expanded-names="(v) => emit('update:expandedPluginNames', (v as string[]))">
      <NCollapseItem v-for="p in filteredPlugins" :key="p.id" :name="p.id" class="plugin-collapsible">
        <template #header>
          <div class="plugin-head">
            <h3>
              {{ p.name }}
              <NTag v-if="p.version" size="small" class="plugin-version">{{ t('installedVersion', { version: p.version }) }}</NTag>
              <NTag v-if="canUpdateFromStore(p.id, p.version)" type="warning" size="small">{{ t('updateAvailable') }}</NTag>
              <NTag v-if="p.needs_core_update" type="warning" size="small">{{ t('pluginNeedsCoreUpdate', { version: p.min_app_version || '' }) }}</NTag>
            </h3>
            <p class="muted">{{ p.description }}</p>
          </div>
        </template>
        <template #header-extra>
          <div class="plugin-extra" @click.stop>
            <NTag size="small">{{ pluginTypeLabel(p.plugin_type) }}</NTag>
            <NTag :type="p.running ? 'success' : 'default'" size="small">{{ p.status }}</NTag>
            <NSwitch v-if="p.has_entrypoint" :value="p.running" :disabled="busy === p.id" @update:value="toggleRunning(p, $event)" />
            <NSwitch
              v-else-if="p.plugin_type === 'content-pack' || p.plugin_type === 'theme' || p.plugin_type === 'voice-pack'"
              :value="p.config?.enabled !== false"
              :disabled="busy === p.id"
              :aria-label="t('pluginEnabled')"
              @update:value="toggleEnabled(p, $event)"
            />
          </div>
        </template>

        <NTabs type="line" animated class="plugin-tabs" @update:value="(name: string) => name === 'docs' && loadPluginDocs(p.id)">
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
          <NTabPane v-else-if="p.docs" name="docs" :tab="t('guideDocs')">
            <div class="plugin-docs">
              <p v-if="pluginDocsLoading[p.id]" class="muted">{{ t('pluginLoading') }}</p>
              <div v-else-if="pluginDocs[p.id]" class="plugin-docs-content" v-html="renderDocsMarkdown(pluginDocs[p.id].content)" />
              <p v-else class="muted">{{ t('pluginNoDocs') }}</p>
            </div>
          </NTabPane>
        </NTabs>

        <div class="actions-row">
          <NButton type="primary" :loading="busy === p.id" @click="save(p)">{{ t('saveConfig') }}</NButton>
          <NButton v-if="p.has_entrypoint" :loading="busy === p.id" @click="restart(p)">
            <template #icon><NIcon :component="RefreshOutline" /></template>
            {{ t('restartPlugin') }}
          </NButton>
          <NButton v-if="canUpdateFromStore(p.id, p.version)" secondary :loading="busy === `${p.id}:update`" @click="updateInstalledPlugin(p)">
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
</template>

<style scoped>
.plugin-install h3 {
  margin: 0;
  color: var(--df-accent-strong);
}

.plugin-install p {
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
  border: 1px solid var(--df-border-soft);
  border-radius: 6px;
  background: var(--df-surface-3);
}

.permission-panel h4 {
  margin: 0;
  color: var(--df-accent-strong);
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

.field-wide {
  grid-column: 1 / -1;
}

.plugin-docs {
  padding: 4px 0;
}

.plugin-docs-content {
  font-size: 14px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.plugin-docs-content h2 {
  font-size: 18px;
  margin: 0 0 10px;
  color: var(--df-accent-strong);
}

.plugin-docs-content h3 {
  font-size: 15px;
  margin: 16px 0 8px;
  color: var(--df-accent-strong);
}

.plugin-docs-content h4 {
  font-size: 14px;
  margin: 12px 0 6px;
  color: var(--df-accent-strong);
}

.plugin-docs-content p {
  margin: 6px 0;
}

.plugin-docs-content ul {
  margin: 6px 0;
  padding-left: 20px;
}

.plugin-docs-content li {
  margin: 4px 0;
}

.plugin-docs-content code {
  background: color-mix(in srgb, var(--df-text) 8%, transparent);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 13px;
}

@media (max-width: 860px) {
  .plugin-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
