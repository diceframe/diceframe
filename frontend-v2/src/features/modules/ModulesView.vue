<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { errorMessage } from '@/api/client'
import { moduleApi, type ModulePreviewResponse, type ModuleSummary } from '@/api/modules'
import { useLocale } from '@/composables/useLocale'

const { t } = useLocale()
const route = useRoute()
const modules = ref<ModuleSummary[]>([])
const error = ref('')
const loading = ref(false)
const importFile = ref<File | null>(null)
const preview = ref<ModulePreviewResponse | null>(null)
const importBusy = ref(false)
const totalContent = (module: ModuleSummary) => Object.values(module.content_counts)
  .reduce((total, count) => total + count, 0)
const installedModules = computed(() => modules.value.filter(module => module.status !== 'disabled'))
const recoveryAdventure = computed(() => String(route.query.adventure || '').trim())
const recoveryVersion = computed(() => String(route.query.version || '').trim())

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await moduleApi.list()
    modules.value = result.modules || []
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    loading.value = false
  }
}

onMounted(load)

function selectImportFile(event: Event) {
  importFile.value = (event.target as HTMLInputElement).files?.[0] || null
  preview.value = null
}

function importForm() {
  const form = new FormData()
  if (importFile.value) form.append('file', importFile.value)
  return form
}

async function previewImport() {
  if (!importFile.value) return
  importBusy.value = true
  error.value = ''
  try {
    preview.value = await moduleApi.previewImport(importForm())
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    importBusy.value = false
  }
}

async function importModule() {
  if (!preview.value || preview.value.blockers.length) return
  importBusy.value = true
  error.value = ''
  try {
    await moduleApi.import(importForm())
    importFile.value = null
    preview.value = null
    await load()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    importBusy.value = false
  }
}
</script>

<template>
  <section class="view modules-page">
    <p class="eyebrow">{{ t('modulesKicker') }}</p>
    <h1>{{ t('navModules') }}</h1>
    <p class="view-intro">{{ t('modulesIntro') }}</p>
    <section v-if="recoveryAdventure" class="module-recovery" role="status">
      <strong>{{ t('modulesRecoveryTitle') }}</strong>
      <p>{{ t('modulesRecoveryHint', { adventure: recoveryAdventure, version: recoveryVersion || t('modulesRecoveryUnknownVersion') }) }}</p>
    </section>
    <section class="module-import" :aria-label="t('modulesImport')">
      <label>{{ t('modulesImport') }} <input type="file" accept=".dfplugin" @change="selectImportFile"></label>
      <button type="button" :disabled="!importFile || importBusy" @click="previewImport">{{ t('modulesPreview') }}</button>
      <template v-if="preview">
        <p v-if="preview.blockers.length" class="error-text">{{ preview.blockers.join(', ') }}</p>
        <p v-if="preview.warnings.length" class="muted">{{ preview.warnings.join(', ') }}</p>
        <button type="button" :disabled="Boolean(preview.blockers.length) || importBusy" @click="importModule">{{ t('modulesInstall') }}</button>
      </template>
    </section>
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="loading" class="muted">{{ t('modulesLoading') }}</p>
    <div v-else-if="!installedModules.length" class="empty-state">
      <p>{{ t('modulesEmpty') }}</p>
    </div>
    <div v-else class="modules-grid">
      <RouterLink v-for="module in installedModules" :key="module.id" :to="{ name: 'module-detail', params: { moduleId: module.id } }" class="module-card">
        <div>
          <p class="eyebrow">{{ module.is_module ? t('modulesAdventureModule') : t('modulesContentPack') }}</p>
          <h2>{{ module.name }}</h2>
          <p class="muted">{{ t('modulesVersion', { version: module.version }) }}</p>
        </div>
        <dl>
          <div><dt>{{ t('modulesAdventures') }}</dt><dd>{{ module.adventure_count }}</dd></div>
          <div><dt>{{ t('modulesContentCount') }}</dt><dd>{{ totalContent(module) }}</dd></div>
        </dl>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.modules-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }
.module-import { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; margin: 1.5rem 0; padding: 1rem; border: 1px solid var(--border-color, #d6d0c4); border-radius: 1rem; }
.module-recovery { margin: 1rem 0; padding: 1rem; border-left: 3px solid var(--primary); background: var(--surface-color, #fff); }
.module-recovery p { margin: .4rem 0 0; }
.module-card { display: grid; gap: 1rem; padding: 1.25rem; border: 1px solid var(--border-color, #d6d0c4); border-radius: 1rem; background: var(--surface-color, #fff); }
.module-card h2 { margin: 0; }
.module-card dl { display: flex; gap: 1.5rem; margin: 0; }
.module-card dt { color: var(--text-muted, #69707d); font-size: .85rem; }
.module-card dd { margin: .2rem 0 0; font-size: 1.3rem; font-weight: 700; }
@media (max-width: 480px) {
  .module-import { align-items: stretch; flex-direction: column; margin: 1rem 0; }
  .module-import label, .module-import button { width: 100%; }
  .module-import input[type="file"] { display: block; width: 100%; margin-top: .45rem; }
  .modules-grid { grid-template-columns: 1fr; }
  .module-card { padding: 1rem; }
}
</style>
