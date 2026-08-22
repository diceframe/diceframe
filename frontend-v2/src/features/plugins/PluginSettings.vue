<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import DOMPurify from 'dompurify'
import {
  ChatbubblesOutline, ColorPaletteOutline, ConstructOutline, CubeOutline,
  ExtensionPuzzleOutline, MicOutline,
} from '@vicons/ionicons5'
import { useTheme, type SkinName } from '@/composables/useTheme'
import { useLocale } from '@/composables/useLocale'
import { renderSafeMarkdown } from '@/utils/markdown'
import type { PluginInfo } from '@/api/types'
import { pluginApi } from '@/api/plugins'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { usePluginContent } from './usePluginContent'
import { useInstalledPlugins } from './useInstalledPlugins'
import { usePluginMarketplace } from './usePluginMarketplace'
import { usePluginTools } from './usePluginTools'
import { usePluginExport } from './usePluginExport'
import { usePluginTypes } from './usePluginTypes'
import { usePluginUninstallCleanup } from './usePluginUninstallCleanup'
import MirrorsTab from './tabs/MirrorsTab.vue'
import ContentTab from './tabs/ContentTab.vue'
import ToolsTab from './tabs/ToolsTab.vue'
import ThemesTab from './tabs/ThemesTab.vue'
import InstalledTab from './tabs/InstalledTab.vue'
import MarketplaceTab from './tabs/MarketplaceTab.vue'
import HubDetailModal from './modals/HubDetailModal.vue'
import ExportPackModal from './modals/ExportPackModal.vue'

const { t } = useLocale()
const settingsStore = useSettingsStore()
const {
  skin, builtinSkins, applySkin,
  pluginThemes, pluginThemeId, loadPluginThemes, applyPluginTheme, clearPluginTheme,
} = useTheme()
const busy = ref('')
// 插件类型筛选（已装 + 商店共用同一筛选值）：筛选条由后端类型表驱动
const typeFilter = ref('')
// 市场范围：插件市场 / 内容市场（内容市场只展示 content-pack）
const marketScope = ref<'plugins' | 'content'>('plugins')
const { pluginTypeFilters, pluginTypeFiltersFor, pluginTypeLabel, loadTypes } = usePluginTypes()
function switchMarketScope(scope: 'plugins' | 'content') {
  marketScope.value = scope
  typeFilter.value = scope === 'content' ? 'content-pack' : ''
}

// 插件类型 -> 图标映射（商店卡片标题左侧）
const pluginTypeIcons: Record<string, Component> = {
  'content-pack': CubeOutline,
  'theme': ColorPaletteOutline,
  'voice-pack': MicOutline,
  'tool': ConstructOutline,
  'channel-adapter': ChatbubblesOutline,
}
function pluginTypeIcon(type?: string): Component {
  return (type && pluginTypeIcons[type]) || ExtensionPuzzleOutline
}
const sortOptions = [
  { label: t('pluginSortDefault'), value: '' },
  { label: t('pluginSortStars'), value: 'stars' },
  { label: t('pluginSortDownloads'), value: 'downloads' },
  { label: t('pluginSortRating'), value: 'rating' },
  { label: t('pluginSortLikes'), value: 'likes' },
  { label: t('pluginSortNameAsc'), value: 'name-asc' },
  { label: t('pluginSortNameDesc'), value: 'name-desc' },
]
const {
  tools, toolInputs, toolResults, toolsLoading,
  loadTools, setToolInput, invokeTool,
} = usePluginTools(busy)
const {
  loading: authorLoading,
  packId, packName, packVersion, packDescription,
  selectedWorldId, selectedRuleId, selectedCardIds,
  includePortraits, includeSceneImages, worldSceneImageFile, ruleSceneImageFile,
  includeMap, mapBackgroundFile, mapIconFiles,
  worldOptions: authorWorldOptions, ruleOptions: authorRuleOptions, cardOptions: authorCardOptions,
  loadAuthorData, exportPack,
} = usePluginExport(busy)
function setExportSceneImage(kind: 'world' | 'rule', event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  if (kind === 'world') worldSceneImageFile.value = file
  else ruleSceneImageFile.value = file
}
function setExportMapAsset(kind: 'background' | 'icons', event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || [])
  if (kind === 'background') mapBackgroundFile.value = files[0] || null
  else mapIconFiles.value = files
}
const showExportModal = ref(false)
let authorLoaded = false
async function openExportModal() {
  showExportModal.value = true
  if (!authorLoaded) {
    authorLoaded = true
    await loadAuthorData()
  }
}
const {
  contentByPlugin, contentGroupCount, contentLoading, contentTargetWorldId, worldOptions,
  loadContentResources, loadWorlds, contentTitle, contentSubtitle, importContent, importAllContent,
} = usePluginContent(busy)

async function refreshPluginSurfaces() {
  await load()
  await Promise.all([loadMarketplace(), loadPluginThemes(), loadContentResources()])
}

const { onUninstalled } = usePluginUninstallCleanup()

const {
  mirrors, mirrorTests, marketplaceSource, marketKeyword,
  marketLoading, mirrorLoading, newMirror, sortMode, filteredMarketplace,
  hubDetail, hubReadmeHtml, hubDetailOpen, hubDetailLoading, hubReadmeLoading, hubRating, hubRatingSummary,
  page, totalPages, paginatedMarketplace, goToPage,
  canUpdateFromStore, loadMarketplace,
  loadMirrors, installMarketPlugin, openHubDetail, toggleHubLike, saveHubRating,
  updateInstalledPlugin, uninstallPlugin, addMirror, saveMirror,
  deleteMirror, testMirror, openUrl, marketItemHasNewerVersion,
} = usePluginMarketplace(busy, refreshPluginSurfaces, typeFilter, marketScope, onUninstalled)
const safeHubReadmeHtml = computed(() => DOMPurify.sanitize(hubReadmeHtml.value))
const {
  plugins, filteredPlugins, expandedPluginNames, loading, installFile, overwriteInstall,
  load, ordered, value, textValue, selectValue, numberValue, set,
  listValue, secretPlaceholder, parseList, save, restart,
  clearCardCache, toggleRunning, toggleEnabled, onPluginFile, installPlugin, rescanLocalPlugins,
} = useInstalledPlugins(
  busy,
  () => Promise.all([loadPluginThemes(), loadTools()]),
  refreshPluginSurfaces,
  typeFilter,
)
const themeOptions = computed(() => pluginThemes.value.map(theme => ({
  label: `${theme.name}${theme.plugin_name ? ` · ${theme.plugin_name}` : ''}`,
  value: theme.id,
})))
function permissionDescription(p: PluginInfo, permission: string): string {
  return p.permission_details?.find(item => item.id === permission)?.description || permission
}

// 工具页专用 UI registry：tool_ui 值 -> 渲染组件。未来进程插件声明新的 tool_ui
// 值并在此注册组件即可获得专用卡，无需改工具页分发逻辑。
function selectedThemeDescription(): string {
  const theme = pluginThemes.value.find(item => item.id === pluginThemeId.value)
  return theme?.description || ''
}
function selectPluginTheme(value: string | null) {
  applyPluginTheme(value)
}
function selectBuiltinSkin(value: SkinName) {
  clearPluginTheme()
  applySkin(value)
}
function updateNewMirror(patch: Partial<typeof newMirror>) {
  Object.assign(newMirror, patch)
}
const skinNameKeys = {
  midnight: 'skinMidnight',
  royal: 'skinRoyal',
  jade: 'skinJade',
  crimson: 'skinCrimson',
} as const satisfies Record<SkinName, string>
const skinDescriptionKeys = {
  midnight: 'skinMidnightHelp',
  royal: 'skinRoyalHelp',
  jade: 'skinJadeHelp',
  crimson: 'skinCrimsonHelp',
} as const satisfies Record<SkinName, string>
const pluginDocs = ref<Record<string, { content: string; name: string }>>({})
const pluginDocsLoading = ref<Record<string, boolean>>({})

async function loadPluginDocs(pluginId: string) {
  if (pluginDocs.value[pluginId] !== undefined || pluginDocsLoading.value[pluginId]) return
  pluginDocsLoading.value[pluginId] = true
  try {
    const response = await pluginApi.docs(pluginId)
    if (response.ok && response.content) {
      pluginDocs.value[pluginId] = { content: response.content, name: response.name || '' }
    }
  } catch {
    // 忽略读取失败，不展示说明 tab 内容
  } finally {
    pluginDocsLoading.value[pluginId] = false
  }
}

onMounted(async () => {
  await load()
  await Promise.all([
    loadMarketplace(), loadMirrors(), loadContentResources(), loadWorlds(), loadTypes(), settingsStore.load(),
  ])
})
</script>

<template>
  <section class="plugin-workspace">
    <header class="view-title archive-hero">
      <div>
        <span class="section-kicker">{{ t('pluginsKicker') }}</span>
        <h1>{{ t('settingsSectionPlugins') }}</h1>
        <p class="muted">{{ t('pluginWorkspaceSubtitle') }}</p>
      </div>
    </header>
  <NTabs type="line" animated class="plugin-surface-tabs">
    <NTabPane name="installed" :tab="t('pluginsInstalledTab')">
      <InstalledTab
        :loading="loading"
        :plugins="plugins"
        :ai-providers="settingsStore.config.ai_providers || []"
        :filtered-plugins="filteredPlugins"
        :expanded-plugin-names="expandedPluginNames"
        :type-filter="typeFilter"
        :plugin-type-filters="pluginTypeFilters"
        :busy="busy"
        :install-file="installFile"
        :overwrite-install="overwriteInstall"
        :plugin-docs="pluginDocs"
        :plugin-docs-loading="pluginDocsLoading"
        :can-update-from-store="canUpdateFromStore"
        :on-plugin-file="onPluginFile"
        :install-plugin="installPlugin"
        :rescan-local-plugins="rescanLocalPlugins"
        :toggle-running="toggleRunning"
        :toggle-enabled="toggleEnabled"
        :ordered="ordered"
        :value="value"
        :text-value="textValue"
        :select-value="selectValue"
        :number-value="numberValue"
        :set="set"
        :list-value="listValue"
        :secret-placeholder="secretPlaceholder"
        :parse-list="parseList"
        :save="save"
        :restart="restart"
        :clear-card-cache="clearCardCache"
        :update-installed-plugin="updateInstalledPlugin"
        :uninstall-plugin="uninstallPlugin"
        :permission-description="permissionDescription"
        :plugin-type-label="pluginTypeLabel"
        :load-plugin-docs="loadPluginDocs"
        :render-docs-markdown="renderSafeMarkdown"
        @update:type-filter="(v: string) => typeFilter = v"
        @update:expanded-plugin-names="(v: string[]) => expandedPluginNames = v"
        @update:overwrite-install="(v: boolean) => overwriteInstall = v"
      />
    </NTabPane>

    <NTabPane name="marketplace" :tab="t('pluginMarketplaceTab')">
      <MarketplaceTab
        :market-keyword="marketKeyword"
        :sort-mode="sortMode"
        :market-loading="marketLoading"
        :marketplace-source="marketplaceSource"
        :filtered-marketplace="filteredMarketplace"
        :paginated-marketplace="paginatedMarketplace"
        :total-pages="totalPages"
        :page="page"
        :type-filter="typeFilter"
        :scope="marketScope"
        :plugin-type-filters="pluginTypeFiltersFor(marketScope)"
        :sort-options="sortOptions"
        :busy="busy"
        :plugin-type-icon="pluginTypeIcon"
        :plugin-type-label="pluginTypeLabel"
        :market-item-has-newer-version="marketItemHasNewerVersion"
        :load-marketplace="loadMarketplace"
        :install-market-plugin="installMarketPlugin"
        :open-url="openUrl"
        :open-hub-detail="openHubDetail"
        :go-to-page="goToPage"
        @update:market-keyword="(v: string) => marketKeyword = v"
        @update:sort-mode="(v: string) => sortMode = v"
        @update:type-filter="(v: string) => typeFilter = v"
        @update:scope="(v: string) => switchMarketScope(v as 'plugins' | 'content')"
      />
    </NTabPane>

    <NTabPane name="content" :tab="t('contentPacks')">
      <ContentTab
        :content-by-plugin="contentByPlugin"
        :content-group-count="contentGroupCount"
        :content-loading="contentLoading"
        v-model:content-target-world-id="contentTargetWorldId"
        :world-options="worldOptions"
        :busy="busy"
        :load-content-resources="loadContentResources"
        :content-title="contentTitle"
        :content-subtitle="contentSubtitle"
        :import-content="importContent"
        :import-all-content="importAllContent"
        @open-export="openExportModal"
      />
    </NTabPane>

    <NTabPane name="themes" :tab="t('themes')">
      <ThemesTab
        :builtin-skins="builtinSkins"
        :skin="skin"
        :plugin-theme-id="pluginThemeId"
        :plugin-themes="pluginThemes"
        :theme-options="themeOptions"
        :skin-name-keys="skinNameKeys"
        :skin-description-keys="skinDescriptionKeys"
        :load-plugin-themes="loadPluginThemes"
        :select-builtin-skin="selectBuiltinSkin"
        :select-plugin-theme="selectPluginTheme"
        :clear-plugin-theme="clearPluginTheme"
        :selected-theme-description="selectedThemeDescription"
      />
    </NTabPane>

    <NTabPane name="tools" :tab="t('pluginToolsTab')">
      <ToolsTab
        :plugins="plugins"
        :tools="tools"
        :tool-inputs="toolInputs"
        :tool-results="toolResults"
        :tools-loading="toolsLoading"
        :busy="busy"
        :load-tools="loadTools"
        :set-tool-input="setToolInput"
        :invoke-tool="invokeTool"
      />
    </NTabPane>

    <NTabPane name="mirrors" :tab="t('mirrorSources')">
      <MirrorsTab
        :mirrors="mirrors"
        :mirror-tests="mirrorTests"
        :mirror-loading="mirrorLoading"
        :new-mirror="newMirror"
        :busy="busy"
        :load-mirrors="loadMirrors"
        :add-mirror="addMirror"
        :save-mirror="saveMirror"
        :delete-mirror="deleteMirror"
        :test-mirror="testMirror"
        @update-new-mirror="updateNewMirror"
      />
    </NTabPane>
  </NTabs>
  </section>

  <HubDetailModal
    v-model:show="hubDetailOpen"
    :hub-detail="hubDetail"
    :hub-detail-loading="hubDetailLoading"
    :hub-readme-loading="hubReadmeLoading"
    :hub-rating="hubRating"
    :hub-rating-summary="hubRatingSummary"
    :busy="busy"
    :safe-hub-readme-html="safeHubReadmeHtml"
    :plugin-type-icon="pluginTypeIcon"
    :plugin-type-label="pluginTypeLabel"
    :market-item-has-newer-version="marketItemHasNewerVersion"
    :install-market-plugin="installMarketPlugin"
    :open-url="openUrl"
    :toggle-hub-like="toggleHubLike"
    :save-hub-rating="saveHubRating"
  />

  <ExportPackModal
    v-model:show="showExportModal"
    :author-loading="authorLoading"
    :pack-id="packId"
    :pack-name="packName"
    :pack-version="packVersion"
    :pack-description="packDescription"
    :selected-world-id="selectedWorldId"
    :selected-rule-id="selectedRuleId"
    :selected-card-ids="selectedCardIds"
    :include-portraits="includePortraits"
    :include-scene-images="includeSceneImages"
    :include-map="includeMap"
    :map-background-file="mapBackgroundFile"
    :map-icon-files="mapIconFiles"
    :author-world-options="authorWorldOptions"
    :author-rule-options="authorRuleOptions"
    :author-card-options="authorCardOptions"
    :busy="busy"
    :set-pack-id="(v: string) => packId = v"
    :set-pack-name="(v: string) => packName = v"
    :set-pack-version="(v: string) => packVersion = v"
    :set-pack-description="(v: string) => packDescription = v"
    :set-selected-world-id="(v: string | null) => selectedWorldId = v || ''"
    :set-selected-rule-id="(v: string | null) => selectedRuleId = v || ''"
    :set-selected-card-ids="(v: (string | number)[] | null) => selectedCardIds = (v || []) as string[]"
    :set-include-portraits="(v: boolean) => includePortraits = v"
    :set-include-scene-images="(v: boolean) => includeSceneImages = v"
    :set-include-map="(v: boolean) => includeMap = v"
    :set-export-scene-image="setExportSceneImage"
    :set-export-map-asset="setExportMapAsset"
    :export-pack="exportPack"
  />
</template>
