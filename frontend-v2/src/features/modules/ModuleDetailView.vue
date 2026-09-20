<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { errorMessage } from '@/api/client'
import {
  moduleApi,
  type ModuleActionGuard,
  type ModuleCompatibilityResponse,
  type ModuleDetail,
  type ModuleUsageResponse,
} from '@/api/modules'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'

const route = useRoute()
const router = useRouter()
const { t } = useLocale()
const { confirm } = useConfirm()
const moduleId = computed(() => String(route.params.moduleId || ''))
const module = ref<ModuleDetail | null>(null)
const compatibility = ref<ModuleCompatibilityResponse | null>(null)
const usage = ref<ModuleUsageResponse | null>(null)
const error = ref('')
const status = ref('')
const busy = ref('')

// §8：按钮可用性一律取服务端 guard 结果（详情接口的 actions），前端不自行推断；
// 点击后服务端会用同一判定再拦一次（MODULE_IN_USE → 409）。
const guardFor = (action: string): ModuleActionGuard => (
  module.value?.actions?.[action] || { allowed: true, reason: '', games: [] }
)
const boundGames = computed(() => module.value?.bound_games || [])
const isDisabled = computed(() => module.value?.status === 'disabled')

async function loadDetail() {
  const detail = await moduleApi.detail(moduleId.value)
  if (!detail.ok || !detail.module) throw new Error(t('modulesNotFound'))
  module.value = detail.module
}

async function load() {
  error.value = ''
  try {
    const [compatibilityResult, usageResult] = await Promise.all([
      moduleApi.compatibility(moduleId.value),
      moduleApi.usages(moduleId.value),
      loadDetail(),
    ])
    compatibility.value = compatibilityResult
    usage.value = usageResult
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  }
}

onMounted(load)
// 同页 hash 导航到另一个模组不会重新挂载组件，必须跟着 id 重新加载。
watch(moduleId, () => { void load() })

// guard 可能在页面加载后被新存档改变：重读详情刷新"使用中的存档"，但不覆盖
// 当前操作失败的提示（否则用户看不到服务端的拒绝原因）。
async function refreshGuard() {
  try {
    await loadDetail()
  } catch {
    // 详情读不到时保留原始错误提示。
  }
}

async function run(action: 'update' | 'disable' | 'enable' | 'uninstall') {
  if (action === 'uninstall') {
    const ok = await confirm({
      title: t('modulesUninstallConfirmTitle'),
      content: t('modulesUninstallConfirmBody'),
      type: 'error',
    })
    if (!ok) return
  }
  busy.value = action
  error.value = ''
  status.value = ''
  try {
    if (action === 'update') await moduleApi.update(moduleId.value)
    else if (action === 'disable') await moduleApi.disable(moduleId.value)
    else if (action === 'enable') await moduleApi.enable(moduleId.value)
    else {
      await moduleApi.uninstall(moduleId.value)
      // 卸载后详情不再存在，回列表（留在页面上只会得到 404 文案）。
      await router.push({ name: 'modules' })
      return
    }
    status.value = t('modulesActionDone')
    await load()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
    await refreshGuard()
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <section class="view modules-page">
    <RouterLink :to="{ name: 'modules' }" class="back-link">{{ t('modulesBack') }}</RouterLink>
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-if="status" class="module-status" role="status">{{ status }}</p>
    <template v-if="module">
      <p class="eyebrow">{{ module.content_profile }}</p>
      <h1>{{ module.name }}</h1>

      <section class="module-detail-section" data-testid="module-metadata">
        <h2>{{ t('modulesMetadata') }}</h2>
        <dl class="module-facts">
          <div><dt>{{ t('modulesModuleId') }}</dt><dd>{{ module.id }}</dd></div>
          <div><dt>{{ t('modulesVersionLabel') }}</dt><dd>{{ module.version }}</dd></div>
          <div><dt>{{ t('modulesStatus') }}</dt><dd>{{ module.status }}</dd></div>
          <div><dt>{{ t('modulesContentDelivery') }}</dt><dd>{{ module.content_delivery_mode }}</dd></div>
        </dl>
      </section>

      <section class="module-detail-section" data-testid="module-compatibility">
        <h2>{{ t('modulesCompatibility') }}</h2>
        <p v-if="compatibility?.blockers?.length" class="error-text">{{ compatibility.blockers.join(', ') }}</p>
        <p v-else>{{ t('modulesCompatible') }}</p>
        <p v-if="compatibility?.warnings?.length" class="muted">{{ compatibility.warnings.join(', ') }}</p>
      </section>

      <section class="module-detail-section" data-testid="module-adventures">
        <h2>{{ t('modulesAdventures') }}</h2>
        <p v-if="!module.adventures.length" class="muted">{{ t('modulesNoAdventures') }}</p>
        <ul v-else><li v-for="adventure in module.adventures" :key="adventure.adventure_id">{{ adventure.adventure_id }} · {{ adventure.version }}</li></ul>
      </section>

      <section class="module-detail-section" data-testid="module-lorebooks">
        <h2>{{ t('modulesLorebooks') }}</h2>
        <p v-if="!(module.lorebooks || []).length" class="muted">{{ t('modulesNoLorebooks') }}</p>
        <ul v-else>
          <li v-for="book in (module.lorebooks || [])" :key="book.id">{{ book.name }} · {{ book.language }}</li>
        </ul>
      </section>

      <section class="module-detail-section" data-testid="module-content">
        <h2>{{ t('modulesContentCount') }}</h2>
        <ul><li v-for="(items, kind) in module.content" :key="kind">{{ kind }} · {{ items.length }}</li></ul>
      </section>

      <section class="module-detail-section" data-testid="module-usage">
        <h2>{{ t('modulesInUse') }}</h2>
        <p>{{ t('modulesGameCount', { count: usage?.total_games ?? boundGames.length }) }}</p>
        <p v-if="!boundGames.length" class="muted">{{ t('modulesBoundNone') }}</p>
        <ul v-else>
          <li v-for="game in boundGames" :key="game.game_key">{{ game.game_key }} · {{ game.adventure_id }}</li>
        </ul>
      </section>

      <section class="module-detail-section" data-testid="module-actions">
        <h2>{{ t('modulesActions') }}</h2>
        <p v-if="!guardFor('update').allowed || !guardFor('uninstall').allowed" class="error-text" data-testid="module-action-blocked">
          {{ t('modulesActionBlocked', { count: boundGames.length }) }}
        </p>
        <div class="module-actions">
          <button type="button" :disabled="busy !== '' || !guardFor('update').allowed" @click="run('update')">
            {{ t('modulesActionUpdate') }}
          </button>
          <button
            v-if="isDisabled"
            type="button"
            :disabled="busy !== ''"
            @click="run('enable')"
          >{{ t('modulesActionEnable') }}</button>
          <button
            v-else
            type="button"
            :disabled="busy !== '' || !guardFor('disable').allowed"
            @click="run('disable')"
          >{{ t('modulesActionDisable') }}</button>
          <button
            type="button"
            class="danger"
            :disabled="busy !== '' || !guardFor('uninstall').allowed"
            @click="run('uninstall')"
          >{{ t('modulesActionUninstall') }}</button>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.back-link { display: inline-block; margin-bottom: 1rem; }
.module-detail-section { margin-top: 1.5rem; padding: 1rem 0; border-top: 1px solid var(--border-color, #d6d0c4); }
.module-detail-section h2 { margin: 0 0 .5rem; }
.module-facts { display: grid; gap: .35rem; margin: 0; }
.module-facts dt { color: var(--text-muted, #69707d); font-size: .8rem; }
.module-facts dd { margin: 0; overflow-wrap: anywhere; }
.module-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.module-actions button { min-height: 34px; }
.module-actions .danger { color: var(--error, #c0392b); }
.module-status { color: var(--primary); }
@media (max-width: 480px) {
  .module-actions { flex-direction: column; }
  .module-actions button { width: 100%; }
}
</style>
