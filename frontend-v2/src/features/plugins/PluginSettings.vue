<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton, NCheckbox, NCollapse, NCollapseItem, NIcon, NInput, NInputNumber,
  NSelect, NSpin, NSwitch, NTabPane, NTabs, NTag,
} from 'naive-ui'
import {
  AddOutline, ChevronDown, ChevronUp, CloudDownloadOutline, CreateOutline,
  ExtensionPuzzleOutline, RefreshOutline, TrashOutline,
} from '@vicons/ionicons5'
import { useTheme } from '@/composables/useTheme'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'
import type { PluginInfo } from '@/api/types'
import NapcatGuide from '@/components/plugins/NapcatGuide.vue'
import { usePluginContent } from './usePluginContent'
import { useInstalledPlugins } from './useInstalledPlugins'
import { usePluginMarketplace } from './usePluginMarketplace'
import { usePluginTools } from './usePluginTools'

const { t } = useLocale()
const { pluginThemes, pluginThemeId, loadPluginThemes, applyPluginTheme, clearPluginTheme } = useTheme()
const busy = ref('')
const {
  tools, toolInputs, toolResults, toolsLoading,
  toolKey, loadTools, setToolInput, invokeTool,
} = usePluginTools(busy)
const {
  contentGroups, contentByPlugin, contentGroupCount, contentLoading, contentTargetWorldId, worldOptions,
  loadContentResources, loadWorlds, contentTitle, contentSubtitle, importContent,
} = usePluginContent(busy)

async function refreshPluginSurfaces() {
  await load()
  await Promise.all([loadMarketplace(), loadPluginThemes(), loadContentResources()])
}

const {
  mirrors, mirrorTests, marketplaceSource, marketKeyword,
  marketLoading, mirrorLoading, newMirror, filteredMarketplace,
  canUpdateFromStore, loadMarketplace, loadMirrors, installMarketPlugin,
  updateInstalledPlugin, uninstallPlugin, addMirror, saveMirror,
  deleteMirror, testMirror, openUrl, isNewerVersion,
} = usePluginMarketplace(busy, refreshPluginSurfaces)
const {
  plugins, expandedPluginNames, loading, installFile, overwriteInstall,
  load, ordered, value, textValue, selectValue, numberValue, set,
  listValue, secretPlaceholder, showGroup, parseList, save, restart,
  clearCardCache, toggleRunning, toggleEnabled, onPluginFile, installPlugin, rescanLocalPlugins,
} = useInstalledPlugins(
  busy,
  () => Promise.all([loadPluginThemes(), loadTools()]),
  refreshPluginSurfaces,
)
const themeOptions = computed(() => pluginThemes.value.map(theme => ({
  label: `${theme.name}${theme.plugin_name ? ` · ${theme.plugin_name}` : ''}`,
  value: theme.id,
})))
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
            <input type="file" accept=".dfplugin" :aria-label="t('pluginZipAria')" @change="onPluginFile">
            <NCheckbox v-model:checked="overwriteInstall">{{ t('overwriteSameIdPlugin') }}</NCheckbox>
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
                <NSwitch
                  v-else-if="p.plugin_type === 'content-pack' || p.plugin_type === 'theme'"
                  :value="p.config?.enabled !== false"
                  :disabled="busy === p.id"
                  :aria-label="t('pluginEnabled')"
                  @update:value="toggleEnabled(p, $event)"
                />
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
              <NButton v-if="canUpdateFromStore(p.id)" secondary :loading="busy === `${p.id}:update`" @click="updateInstalledPlugin(p)">
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
              <NTag v-if="item.support?.level === 'partial'" type="warning" size="small">{{ t('pluginSupportPartial') }}</NTag>
              <NTag v-if="item.support?.level === 'reserved'" type="error" size="small">{{ t('pluginSupportReserved') }}</NTag>
              <NTag v-if="item.trust_level === 'official'" type="success" size="small">{{ t('pluginTrustOfficial') }}</NTag>
              <NTag v-else-if="item.trust_level === 'verified'" type="info" size="small">{{ t('pluginTrustVerified') }}</NTag>
              <NTag v-else size="small">{{ t('pluginTrustCommunity') }}</NTag>
              <NTag v-if="item.distribution === 'bundled'" type="success" size="small">{{ t('pluginBundled') }}</NTag>
              <NTag v-else-if="item.risk_level === 'declarative'" type="success" size="small">{{ t('pluginRiskDeclarative') }}</NTag>
              <NTag v-else-if="item.risk_level === 'unrestricted-process'" type="error" size="small">{{ t('pluginRiskProcess') }}</NTag>
              <NTag v-if="item.commit_sha" type="info" size="small">{{ t('pluginSourcePinned') }}</NTag>
              <NTag v-if="item.update_policy === 'automatic'" type="success" size="small">{{ t('pluginUpdateAutomatic') }}</NTag>
              <NTag v-else-if="item.update_policy === 'notify'" type="warning" size="small">{{ t('pluginUpdateNotify') }}</NTag>
              <NTag v-else-if="item.update_policy === 'approval-required'" type="error" size="small">{{ t('pluginUpdateApprovalRequired') }}</NTag>
              <NTag v-if="item.installed" type="success" size="small">{{ t('installedVersion', { version: item.installed_version || '' }) }}</NTag>
              <NTag v-if="item.installed && isNewerVersion(item.version, item.installed_version)" type="warning" size="small">{{ t('newVersionAvailable', { version: item.version || '' }) }}</NTag>
              <NTag v-for="tag in item.tags || []" :key="tag" size="small">{{ tag }}</NTag>
            </div>
            <p v-if="item.permissions?.length" class="muted market-permissions">
              {{ t('permissions') }}: {{ item.permissions.slice(0, 4).join(t('listSeparator')) }}{{ item.permissions.length > 4 ? t('andMore') : '' }}
            </p>
            <p v-if="item.support?.summary" class="muted market-permissions">{{ item.support.summary }}</p>
            <p v-if="item.verification_error" class="market-warning">{{ item.verification_error }}</p>
            <div class="market-actions">
              <NButton type="primary" :disabled="item.installable === false" :loading="busy === `market:${item.id}`" @click="installMarketPlugin(item)">
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

    <NTabPane name="tools" :tab="t('pluginToolsTab')">
      <section class="toolbar-row">
        <div>
          <h3>{{ t('pluginToolsTitle') }}</h3>
          <p class="muted">{{ t('pluginToolsHelp') }}</p>
        </div>
        <NButton :loading="toolsLoading" @click="loadTools">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('refresh') }}
        </NButton>
      </section>
      <NSpin :show="toolsLoading">
        <div v-if="tools.length" class="tool-grid">
          <article v-for="tool in tools" :key="toolKey(tool)" class="tool-card">
            <div class="tool-heading">
              <div>
                <h3>{{ tool.title || tool.name }}</h3>
                <p class="muted">{{ tool.plugin_name }} · {{ tool.name }}</p>
              </div>
              <NTag size="small">{{ tool.plugin_id }}</NTag>
            </div>
            <p>{{ tool.description || t('noDescription') }}</p>
            <details>
              <summary>{{ t('pluginToolInputSchema') }}</summary>
              <pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
            </details>
            <label class="input-label">
              <span class="field-title">{{ t('pluginToolArguments') }}</span>
              <NInput
                type="textarea"
                :rows="5"
                :value="toolInputs[toolKey(tool)] || '{}'"
                :placeholder="t('pluginToolArgumentsPlaceholder')"
                @update:value="setToolInput(tool, $event)"
              />
            </label>
            <NButton type="primary" :loading="busy === `tool:${toolKey(tool)}`" @click="invokeTool(tool)">
              {{ t('pluginToolInvoke') }}
            </NButton>
            <pre v-if="toolResults[toolKey(tool)]" class="tool-result">{{ toolResults[toolKey(tool)] }}</pre>
          </article>
        </div>
        <p v-else class="muted">{{ t('noRunningPluginTools') }}</p>
      </NSpin>
    </NTabPane>

    <NTabPane name="content" :tab="t('contentPacks')">
      <section class="toolbar-row">
        <NSelect
          v-model:value="contentTargetWorldId"
          :options="worldOptions"
          :placeholder="t('selectLorebook')"
          class="content-world-select"
        />
        <span class="muted">{{ t('contentTotalCount', { count: contentGroupCount }) }}</span>
        <NButton :loading="contentLoading" @click="loadContentResources">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          {{ t('refresh') }}
        </NButton>
      </section>
      <NSpin :show="contentLoading">
        <p v-if="!contentByPlugin.length" class="muted">{{ t('noPluginsAvailable') }}</p>
        <NCollapse v-else class="content-collapse">
          <NCollapseItem v-for="plugin in contentByPlugin" :key="plugin.plugin_id" :name="plugin.plugin_id">
            <template #header>
              <div class="content-plugin-head">
                <h3>{{ plugin.plugin_name }}</h3>
                <span class="muted">{{ plugin.plugin_id }}</span>
              </div>
            </template>
            <template #header-extra>
              <NTag size="small">{{ plugin.groups.reduce((sum, g) => sum + g.items.length, 0) }}</NTag>
            </template>
            <div class="content-plugin-body">
              <section v-for="group in plugin.groups" :key="group.key" class="content-group">
                <h4>{{ t(group.labelKey) }} <span class="muted">{{ group.items.length }}</span></h4>
                <div v-if="group.items.length" class="content-list">
                  <article
                    v-for="item in group.items"
                    :key="`${group.key}:${item.plugin_id}:${item.id || item.name || item.character_name}`"
                    class="content-item"
                  >
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
          </NCollapseItem>
        </NCollapse>
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

.content-collapse {
  margin-top: 4px;
}

.content-plugin-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.content-plugin-head h3 {
  margin: 0;
  color: var(--gold-2);
  font-size: 15px;
}

.content-plugin-body {
  display: grid;
  gap: 12px;
  padding-top: 4px;
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

.content-group h4 {
  margin: 0 0 8px;
  color: var(--gold-2);
  font-size: 14px;
}

.content-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}

.content-item {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: rgba(255, 255, 255, .03);
  display: grid;
  gap: 10px;
  align-content: start;
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

.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.tool-card {
  min-width: 0;
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, var(--panel), var(--panel-2));
}

.tool-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.tool-heading h3,
.tool-heading p,
.tool-card > p {
  margin: 0;
}

.tool-card details summary {
  cursor: pointer;
  color: var(--gold-2);
}

.tool-card pre {
  max-height: 220px;
  overflow: auto;
  margin: 8px 0 0;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--ink);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-result {
  border-color: var(--gold) !important;
}

.content-world-select {
  width: min(360px, 100%);
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

.market-warning {
  color: var(--red-2);
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
