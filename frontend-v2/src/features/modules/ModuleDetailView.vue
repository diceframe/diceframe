<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { errorMessage } from '@/api/client'
import { moduleApi, type ModuleCompatibilityResponse, type ModuleDetail, type ModuleUsageResponse } from '@/api/modules'
import { useLocale } from '@/composables/useLocale'

const route = useRoute()
const { t } = useLocale()
const moduleId = computed(() => String(route.params.moduleId || ''))
const module = ref<ModuleDetail | null>(null)
const compatibility = ref<ModuleCompatibilityResponse | null>(null)
const usage = ref<ModuleUsageResponse | null>(null)
const error = ref('')

async function load() {
  error.value = ''
  try {
    const [detail, compatibilityResult, usageResult] = await Promise.all([
      moduleApi.detail(moduleId.value),
      moduleApi.compatibility(moduleId.value),
      moduleApi.usages(moduleId.value),
    ])
    if (!detail.ok || !detail.module) throw new Error(t('modulesNotFound'))
    module.value = detail.module
    compatibility.value = compatibilityResult
    usage.value = usageResult
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  }
}

onMounted(load)
</script>

<template>
  <section class="view modules-page">
    <RouterLink :to="{ name: 'modules' }" class="back-link">{{ t('modulesBack') }}</RouterLink>
    <p v-if="error" class="error-text">{{ error }}</p>
    <template v-else-if="module">
      <p class="eyebrow">{{ module.content_profile }}</p>
      <h1>{{ module.name }}</h1>
      <p class="view-intro">{{ t('modulesVersion', { version: module.version }) }}</p>
      <section class="module-detail-section">
        <h2>{{ t('modulesAdventures') }}</h2>
        <p v-if="!module.adventures.length" class="muted">{{ t('modulesNoAdventures') }}</p>
        <ul v-else><li v-for="adventure in module.adventures" :key="adventure.adventure_id">{{ adventure.adventure_id }} · {{ adventure.version }}</li></ul>
      </section>
      <section class="module-detail-section">
        <h2>{{ t('modulesContentCount') }}</h2>
        <ul><li v-for="(items, kind) in module.content" :key="kind">{{ kind }} · {{ items.length }}</li></ul>
      </section>
      <section class="module-detail-section">
        <h2>{{ t('modulesCompatibility') }}</h2>
        <p v-if="compatibility?.blockers?.length" class="error-text">{{ compatibility.blockers.join(', ') }}</p>
        <p v-else>{{ t('modulesCompatible') }}</p>
        <p v-if="compatibility?.warnings?.length" class="muted">{{ compatibility.warnings.join(', ') }}</p>
      </section>
      <section class="module-detail-section">
        <h2>{{ t('modulesInUse') }}</h2>
        <p>{{ t('modulesGameCount', { count: usage?.total_games || 0 }) }}</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.back-link { display: inline-block; margin-bottom: 1rem; }
.module-detail-section { margin-top: 1.5rem; padding: 1rem 0; border-top: 1px solid var(--border-color, #d6d0c4); }
.module-detail-section h2 { margin: 0 0 .5rem; }
</style>
