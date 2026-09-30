<script setup lang="ts">
import { NCollapse, NCollapseItem, NButton, NIcon, NSelect, NSpin } from 'naive-ui'
import { CreateOutline, RefreshOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { CharacterPortrait, PluginContentResource } from '@/api/types'
import PortraitImage from '@/components/PortraitImage.vue'

interface ContentPluginGroup { key: string; labelKey: string; items: PluginContentResource[] }
interface ContentPluginEntry { plugin_id: string; plugin_name: string; groups: ContentPluginGroup[] }

defineProps<{
  contentByPlugin: ContentPluginEntry[]
  contentGroupCount: number
  contentLoading: boolean
  contentTargetBookId: string
  bookOptions: { label: string; value: string }[]
  busy: string
  loadContentResources: () => Promise<void> | void
  contentTitle: (item: PluginContentResource) => string
  contentSubtitle: (item: PluginContentResource) => string
  importContent: (kind: string, item: PluginContentResource) => Promise<void> | void
  importAllContent: (pluginId: string) => Promise<void> | void
}>()
const emit = defineEmits<{ 'update:contentTargetBookId': [value: string]; 'open-export': [] }>()

const { t } = useLocale()

function contentPortrait(item: PluginContentResource): CharacterPortrait | undefined {
  const portrait = item.portrait
  return portrait && typeof portrait === 'object' && 'kind' in portrait
    ? portrait as CharacterPortrait
    : undefined
}
</script>

<template>
  <section class="toolbar-row content-pack-toolbar">
    <NSelect
      :value="contentTargetBookId"
      :options="bookOptions"
      :placeholder="t('selectLorebook')"
      class="content-world-select"
      @update:value="(v) => emit('update:contentTargetBookId', String(v || ''))"
    />
    <span class="muted">{{ t('contentTotalCount', { count: contentGroupCount }) }}</span>
    <NButton :loading="contentLoading" @click="loadContentResources">
      <template #icon><NIcon :component="RefreshOutline" /></template>
      {{ t('refresh') }}
    </NButton>
    <NButton type="primary" @click="emit('open-export')" class="create-pack-btn">
      <template #icon><NIcon :component="CreateOutline" /></template>
      {{ t('createContentPack') }}
    </NButton>
  </section>
  <p class="muted content-auto-import-hint">{{ t('contentAutoImportHint') }}</p>
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
          <span class="content-count muted">{{ plugin.groups.reduce((sum, g) => sum + g.items.length, 0) }} {{ t('contentItems') }}</span>
          <NButton
            size="small"
            secondary
            class="import-all-btn"
            :loading="busy === `import-all:${plugin.plugin_id}`"
            @click.stop="importAllContent(plugin.plugin_id)"
          >
            {{ t('importAllContent') }}
          </NButton>
        </template>
        <div class="content-plugin-body">
          <section v-for="group in plugin.groups" :key="group.key" class="content-group">
            <h4>{{ t(group.labelKey as never) }} <span class="muted">{{ group.items.length }}</span></h4>
            <div v-if="group.items.length" class="content-list">
              <article
                v-for="item in group.items"
                :key="`${group.key}:${item.plugin_id}:${item.id || item.name || item.character_name}`"
                class="content-item"
              >
                <PortraitImage
                  v-if="contentPortrait(item)"
                  :portrait="contentPortrait(item)"
                  :rule-id="String(item.rule_id || '')"
                  :seed="String(item.id || item.name || item.character_name || '')"
                  :name="contentTitle(item)"
                  :size="54"
                />
                <div class="content-item-main">
                  <strong>{{ contentTitle(item) }}</strong>
                  <p class="muted">{{ contentSubtitle(item) || t('noDescription') }}</p>
                </div>
                <NButton
                  size="small"
                  secondary
                  :disabled="group.key !== 'character_template' && !contentTargetBookId"
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
</template>

<style scoped>
.content-collapse {
  margin-top: 4px;
}

.content-collapse :deep(.n-collapse-item) {
  margin-bottom: 12px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--df-accent) 28%, var(--df-border-soft));
  border-radius: var(--df-radius-md);
  background: color-mix(in srgb, var(--df-surface-2) 84%, transparent);
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--df-text) 4%, transparent);
}

.content-collapse :deep(.n-collapse-item:last-child) {
  margin-bottom: 0;
}

.content-collapse :deep(.n-collapse-item__header) {
  padding: 10px 12px;
  border-bottom: 1px solid transparent;
  background: color-mix(in srgb, var(--df-surface-raised) 76%, transparent);
}

.content-collapse :deep(.n-collapse-item--active > .n-collapse-item__header) {
  border-bottom-color: var(--df-border-soft);
}

.content-collapse :deep(.n-collapse-item__header-main) {
  min-width: 0;
}

.content-collapse :deep(.n-collapse-item__header-extra) {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.content-count {
  white-space: nowrap;
  font-size: 13px;
}

.import-all-btn {
  flex-shrink: 0;
}

.content-plugin-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.content-plugin-head h3 {
  margin: 0;
  color: var(--df-accent-strong);
  font-size: 15px;
}

.content-plugin-body {
  display: grid;
  gap: 12px;
  padding: 12px;
}

.content-group {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: 8px;
  background: var(--df-surface-3);
}

.content-group h3 {
  margin: 0 0 10px;
  color: var(--df-accent-strong);
  font-size: 15px;
}

.content-group h4 {
  margin: 0 0 8px;
  color: var(--df-accent-strong);
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
  border: 1px solid var(--df-border-soft);
  border-radius: 6px;
  background: color-mix(in srgb, var(--df-text) 3%, transparent);
  display: grid;
  gap: 10px;
  align-content: start;
}

.content-item:has(> .portrait-image) {
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
}

.content-item:has(> .portrait-image) > .n-button {
  grid-column: 1 / -1;
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
</style>
