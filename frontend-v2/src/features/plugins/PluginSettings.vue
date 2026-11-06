<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { NButton, NTabPane, NTabs } from 'naive-ui'
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
import { usePluginTypes } from './usePluginTypes'
import { usePluginUninstallCleanup } from './usePluginUninstallCleanup'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import MirrorsTab from './tabs/MirrorsTab.vue'
import ContentTab from './tabs/ContentTab.vue'
import ToolsTab from './tabs/ToolsTab.vue'
import ThemesTab from './tabs/ThemesTab.vue'
import InstalledTab from './tabs/InstalledTab.vue'
import MarketplaceTab from './tabs/MarketplaceTab.vue'
import HubDetailModal from './modals/HubDetailModal.vue'

const { t } = useLocale()
const settingsStore = useSettingsStore()
const {
  skin, builtinSkins, applySkin,
  pluginThemes, pluginThemeId, loadPluginThemes, applyPluginTheme, clearPluginTheme,
} = useTheme()
const busy = ref('')
const surfaceError = ref('')
// 插件类型筛选（已装 + 商店共用同一筛选值）：筛选条由后端类型表驱动
const typeFilter = ref('')
const { pluginTypeFilters, marketTypeFilters, pluginTypeLabel, loadTypes } = usePluginTypes()

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
  contentByPlugin, contentGroupCount, contentLoading, contentTargetBookId, bookOptions,
  loadContentResources, loadLorebooks, contentTitle, contentSubtitle, importContent, importAllContent,
} = usePluginContent(busy)

async function refreshPluginSurfaces() {
  surfaceError.value = ''
  try {
    await load()
    await Promise.all([loadMarketplace(), loadPluginThemes(), loadContentResources()])
  } catch (error: unknown) {
    surfaceError.value = error instanceof Error ? error.message : String(error)
  }
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
} = usePluginMarketplace(busy, refreshPluginSurfaces, typeFilter, onUninstalled)
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
    loadMarketplace(), loadMirrors(), loadContentResources(), loadLorebooks(), loadTypes(), settingsStore.load(),
  ])
})
</script>

<template>
  <section class="plugin-workspace">
    <ContentPageHeader class="archive-hero" kicker="EXTENSIONS" :title="t('settingsSectionPlugins')" :description="t('pluginWorkspaceSubtitle')">
      <template #actions>
        <div class="plugin-header-actions">
          <NButton size="small" :loading="busy !== ''" @click="refreshPluginSurfaces">{{ t('refresh') }}</NButton>
        </div>
      </template>
    </ContentPageHeader>
    <div v-if="surfaceError" class="error-banner plugin-surface-error" role="alert">
      <span>{{ surfaceError }}</span>
      <button type="button" class="ghost" @click="refreshPluginSurfaces">{{ t('retry') }}</button>
    </div>
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
        :plugin-type-filters="marketTypeFilters"
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
      />
    </NTabPane>

    <NTabPane name="content" :tab="t('pluginContentImportTab')">
      <ContentTab
        :content-by-plugin="contentByPlugin"
        :content-group-count="contentGroupCount"
        :content-loading="contentLoading"
        v-model:content-target-book-id="contentTargetBookId"
        :book-options="bookOptions"
        :busy="busy"
        :load-content-resources="loadContentResources"
        :content-title="contentTitle"
        :content-subtitle="contentSubtitle"
        :import-content="importContent"
        :import-all-content="importAllContent"
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

</template>

<style scoped>
.plugin-header-actions { display: flex; align-items: center; gap: 8px; }
.plugin-surface-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 10px; }

.plugin-surface-tabs :deep(.n-tabs-nav) { min-height: 44px; }
.plugin-surface-tabs :deep(.n-tabs-tab) {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
}

@media (max-width: 560px) {
  .plugin-surface-tabs :deep(.n-tabs-nav-scroll-content) { min-width: max-content; }
}
</style>
