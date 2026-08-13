<script setup lang="ts">
import { NButton, NCheckbox, NIcon, NInput, NModal, NSelect, NSpin, type SelectOption } from 'naive-ui'
import { CloudDownloadOutline, CreateOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'

defineProps<{
  show: boolean
  authorLoading: boolean
  packId: string
  packName: string
  packVersion: string
  packDescription: string
  selectedWorldId: string
  selectedRuleId: string
  selectedCardIds: string[]
  includePortraits: boolean
  includeSceneImages: boolean
  includeMap: boolean
  mapBackgroundFile: File | null
  mapIconFiles: File[]
  authorWorldOptions: SelectOption[]
  authorRuleOptions: SelectOption[]
  authorCardOptions: SelectOption[]
  busy: string
  setPackId: (v: string) => void
  setPackName: (v: string) => void
  setPackVersion: (v: string) => void
  setPackDescription: (v: string) => void
  setSelectedWorldId: (v: string | null) => void
  setSelectedRuleId: (v: string | null) => void
  setSelectedCardIds: (v: (string | number)[] | null) => void
  setIncludePortraits: (v: boolean) => void
  setIncludeSceneImages: (v: boolean) => void
  setIncludeMap: (v: boolean) => void
  setExportSceneImage: (kind: 'world' | 'rule', event: Event) => void
  setExportMapAsset: (kind: 'background' | 'icons', event: Event) => void
  exportPack: (repoSource: boolean) => Promise<void> | void
}>()
const emit = defineEmits<{ 'update:show': [value: boolean] }>()

const { t } = useLocale()
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    class="export-pack-modal"
    :title="t('exportPackTitle')"
    :bordered="false"
    style="width: min(800px, calc(100vw - 24px)); max-height: calc(100dvh - 28px);"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <p class="muted export-pack-help">{{ t('exportPackHelp') }}</p>
    <NSpin :show="authorLoading">
      <div class="export-pack-scroll">
        <section class="export-pack-section">
          <h3>{{ t('exportPackBasicInfo') }}</h3>
          <div class="export-pack-meta-grid">
            <div class="field">
              <label class="input-label">
                <span class="field-title">{{ t('packId') }}</span>
                <NInput :value="packId" placeholder="my-cool-pack" @update:value="setPackId" />
              </label>
            </div>
            <div class="field">
              <label class="input-label">
                <span class="field-title">{{ t('packName') }}</span>
                <NInput :value="packName" @update:value="setPackName" />
              </label>
            </div>
            <div class="field export-version-field">
              <label class="input-label">
                <span class="field-title">{{ t('packVersion') }}</span>
                <NInput :value="packVersion" @update:value="setPackVersion" />
              </label>
            </div>
            <div class="field export-description-field">
              <label class="input-label">
                <span class="field-title">{{ t('packDescription') }}</span>
                <NInput :value="packDescription" type="textarea" :autosize="{ minRows: 1, maxRows: 2 }" @update:value="setPackDescription" />
              </label>
            </div>
          </div>
        </section>

        <section class="export-pack-section">
          <h3>{{ t('exportPackContentSelection') }}</h3>
          <div class="export-content-grid">
            <div class="export-content-column">
              <label class="input-label">
                <span class="field-title">{{ t('selectWorld') }}</span>
                <NSelect :value="selectedWorldId" :options="authorWorldOptions" clearable @update:value="setSelectedWorldId" />
              </label>
              <label class="compact-file-field" :class="{ disabled: !selectedWorldId || !includeSceneImages }">
                <span>{{ t('worldSceneImage') }}</span>
                <input type="file" accept="image/jpeg,image/png,image/webp" :disabled="!selectedWorldId || !includeSceneImages" @change="setExportSceneImage('world', $event)">
                <small class="muted">{{ t('worldSceneImageHint') }}</small>
              </label>
            </div>
            <div class="export-content-column">
              <label class="input-label">
                <span class="field-title">{{ t('selectRule') }}</span>
                <NSelect :value="selectedRuleId" :options="authorRuleOptions" clearable @update:value="setSelectedRuleId" />
              </label>
              <label class="compact-file-field" :class="{ disabled: !selectedRuleId || !includeSceneImages }">
                <span>{{ t('ruleSceneImage') }}</span>
                <input type="file" accept="image/jpeg,image/png,image/webp" :disabled="!selectedRuleId || !includeSceneImages" @change="setExportSceneImage('rule', $event)">
                <small class="muted">{{ t('ruleSceneImageHint') }}</small>
              </label>
            </div>
            <label class="input-label export-card-select">
              <span class="field-title">{{ t('selectCards') }}</span>
              <NSelect :value="selectedCardIds" :options="authorCardOptions" multiple clearable @update:value="setSelectedCardIds" />
            </label>
          </div>
        </section>

        <section class="export-pack-section export-map-section">
          <h3>{{ t('exportPackMapContent') }}</h3>
          <label class="export-resource-option" :class="{ disabled: !selectedWorldId }" :title="t('includeContentPackMapHint')">
            <NCheckbox
              :checked="includeMap"
              :disabled="!selectedWorldId"
              @update:checked="setIncludeMap"
            >
              {{ t('includeContentPackMap') }}
            </NCheckbox>
            <small class="muted">{{ t('includeContentPackMapHint') }}</small>
          </label>
          <div class="export-map-assets">
            <label class="compact-file-field" :class="{ disabled: !selectedWorldId || !includeMap }">
              <span>{{ t('contentPackMapBackground') }}</span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                :disabled="!selectedWorldId || !includeMap"
                @change="setExportMapAsset('background', $event)"
              >
              <small class="muted">
                {{ mapBackgroundFile ? mapBackgroundFile.name : t('contentPackMapBackgroundHint') }}
              </small>
            </label>
            <label class="compact-file-field" :class="{ disabled: !selectedWorldId || !includeMap }">
              <span>{{ t('contentPackMapIcons') }}</span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                :disabled="!selectedWorldId || !includeMap"
                @change="setExportMapAsset('icons', $event)"
              >
              <small class="muted">
                {{ mapIconFiles.length ? t('contentPackMapIconsSelected', { count: mapIconFiles.length }) : t('contentPackMapIconsHint') }}
              </small>
            </label>
          </div>
        </section>

        <section class="export-pack-section export-resource-section">
          <h3>{{ t('exportPackPortableAssets') }}</h3>
          <div class="export-resource-options">
            <label class="export-resource-option" :title="t('includeContentPackPortraitsHint')">
              <NCheckbox :checked="includePortraits" @update:checked="setIncludePortraits">{{ t('includeContentPackPortraits') }}</NCheckbox>
            </label>
            <label class="export-resource-option" :title="t('includeContentPackSceneImagesHint')">
              <NCheckbox :checked="includeSceneImages" @update:checked="setIncludeSceneImages">{{ t('includeContentPackSceneImages') }}</NCheckbox>
            </label>
          </div>
        </section>
      </div>
      <footer class="export-pack-footer">
        <p class="muted hint">{{ t('exportPackFormatsHint') }}</p>
        <div class="actions-row">
          <NButton type="primary" :loading="busy === 'export-pack'" @click="exportPack(false)">
            <template #icon><NIcon :component="CloudDownloadOutline" /></template>
            {{ t('exportPack') }}
          </NButton>
          <NButton :loading="busy === 'export-pack'" :title="t('exportRepoSourceHint')" @click="exportPack(true)">
            <template #icon><NIcon :component="CreateOutline" /></template>
            {{ t('exportRepoSource') }}
          </NButton>
        </div>
      </footer>
    </NSpin>
  </NModal>
</template>

<style scoped>
.export-pack-modal :deep(.n-card__content) {
  overflow: hidden;
  padding-top: 8px;
}

.export-pack-modal :deep(.n-card-header__close),
.export-pack-modal :deep(.n-base-close) {
  flex: 0 0 34px;
  display: grid;
  place-items: center;
  width: 34px;
  min-width: 34px;
  height: 34px;
  min-height: 34px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--df-border-soft);
  border-radius: 50%;
  color: var(--df-text-secondary);
  background: color-mix(in srgb, var(--df-control-bg) 90%, transparent);
  box-shadow: none;
}

.export-pack-modal :deep(.n-base-close:hover) {
  border-color: var(--df-interactive);
  color: var(--df-text);
  background: color-mix(in srgb, var(--df-interactive) 13%, var(--df-control-bg));
}

.export-pack-modal :deep(.n-base-close .n-base-icon) {
  width: 18px;
  height: 18px;
  line-height: 18px;
}

.portrait-export-option {
  display: grid;
  gap: 5px;
}

.export-pack-help {
  margin: 0 0 12px;
  line-height: 1.5;
}

.export-pack-scroll {
  display: grid;
  max-height: calc(100dvh - 238px);
  overflow-y: auto;
  gap: 10px;
  padding-right: 4px;
}

.export-pack-section {
  padding: 11px 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md);
  background: color-mix(in srgb, var(--df-surface-2) 76%, transparent);
}

.export-pack-section h3 {
  margin: 0 0 9px;
  color: var(--df-accent-strong);
  font-size: 13px;
}

.export-pack-section .field {
  margin: 0;
}

.export-pack-section label {
  margin: 0;
}

.export-pack-meta-grid {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) minmax(150px, 1fr) minmax(96px, .45fr);
  gap: 9px 12px;
}

.export-description-field {
  grid-column: 1 / -1;
}

.export-content-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
}

.export-content-column {
  display: grid;
  min-width: 0;
  gap: 8px;
}

.compact-file-field {
  display: grid;
  min-width: 0;
  gap: 4px;
  padding: 8px 9px;
  border: 1px dashed var(--df-border-soft);
  border-radius: var(--df-radius-sm);
  background: color-mix(in srgb, var(--df-control-bg) 72%, transparent);
  font-size: 12px;
}

.compact-file-field.disabled {
  opacity: .55;
}

.compact-file-field input {
  max-width: 100%;
  color: var(--df-text-secondary);
  font-size: 11px;
}

.compact-file-field small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.export-card-select {
  grid-column: 1 / -1;
}

.export-resource-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.export-map-section {
  display: grid;
  gap: 9px;
}

.export-map-section h3 {
  margin-bottom: 0;
}

.export-map-assets {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.export-resource-option {
  display: grid;
  align-content: center;
  gap: 4px;
  padding: 8px 9px;
  border-radius: var(--df-radius-sm);
  background: color-mix(in srgb, var(--df-control-bg) 68%, transparent);
}

.export-pack-footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding-top: 11px;
  border-top: 1px solid var(--df-border-soft);
}

.export-pack-footer .hint,
.export-pack-footer .actions-row {
  margin: 0;
}

.export-pack-footer .hint {
  font-size: 11px;
  line-height: 1.4;
}

@media (max-width: 560px) {
  .export-pack-scroll {
    max-height: calc(100dvh - 320px);
  }

  .export-content-grid,
  .export-map-assets,
  .export-resource-options,
  .export-pack-footer {
    grid-template-columns: 1fr;
  }

  .export-pack-meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .export-description-field,
  .export-card-select,
  .export-version-field {
    grid-column: auto;
  }

  .export-pack-footer .actions-row {
    justify-content: stretch;
  }

  .export-pack-footer .actions-row > * {
    flex: 1;
  }
}
</style>
