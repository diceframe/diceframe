<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { errorMessage } from '@/api/client'
import { moduleApi, type ModuleSummary } from '@/api/modules'
import { useLocale } from '@/composables/useLocale'

const { t } = useLocale()
const modules = ref<ModuleSummary[]>([])
const error = ref('')
const loading = ref(false)
const totalContent = (module: ModuleSummary) => Object.values(module.content_counts)
  .reduce((total, count) => total + count, 0)
const installedModules = computed(() => modules.value.filter(module => module.status !== 'disabled'))

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
</script>

<template>
  <section class="view modules-page">
    <p class="eyebrow">{{ t('modulesKicker') }}</p>
    <h1>{{ t('navModules') }}</h1>
    <p class="view-intro">{{ t('modulesIntro') }}</p>
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
.module-card { display: grid; gap: 1rem; padding: 1.25rem; border: 1px solid var(--border-color, #d6d0c4); border-radius: 1rem; background: var(--surface-color, #fff); }
.module-card h2 { margin: 0; }
.module-card dl { display: flex; gap: 1.5rem; margin: 0; }
.module-card dt { color: var(--text-muted, #69707d); font-size: .85rem; }
.module-card dd { margin: .2rem 0 0; font-size: 1.3rem; font-weight: 700; }
</style>
