<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import {
  moduleApi,
  type ModuleMarketplaceItem,
  type ModulePreviewResponse,
  type ModuleSummary,
} from '@/api/modules'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'

type RecoveryAdventure = {
  binding?: {
    adventure_id?: string
    version?: string
    content_digest?: string
    source_kind?: string
    source_id?: string
  }
  available: boolean
  reason?: string
}
type RecoveryCheck = {
  ready: boolean
  reason: string
  digestMatches: boolean
  sourceMatches: boolean
}

const { t } = useLocale()
const route = useRoute()
const modules = ref<ModuleSummary[]>([])
const market = ref<ModuleMarketplaceItem[]>([])
const marketError = ref('')
const marketKeyword = ref('')
const marketRuleset = ref('all')
const marketBusy = ref(false)
const installBusy = ref('')
const error = ref('')
const loading = ref(false)
const importFile = ref<File | null>(null)
const preview = ref<ModulePreviewResponse | null>(null)
const importBusy = ref(false)
const recoveryCheck = ref<RecoveryCheck | null>(null)
const recoveryBusy = ref(false)
const totalContent = (module: ModuleSummary) => Object.values(module.content_counts)
  .reduce((total, count) => total + count, 0)

// §8 Recovery：入口带过来的是**存档里的绑定身份**（adventure / version /
// content_digest / source），恢复完成的判定完全交给服务端：重新读取该局的冒险
// 投影，只有投影可用且指纹与来源都一致才算恢复，绝不自动绑定"同 id 不同包"。
const recoveryAdventure = computed(() => String(route.query.adventure || '').trim())
const recoveryVersion = computed(() => String(route.query.version || '').trim())
const recoveryDigest = computed(() => String(route.query.digest || '').trim())
const recoverySourceKind = computed(() => String(route.query.source_kind || '').trim())
const recoverySourceId = computed(() => String(route.query.source_id || '').trim())
const recoveryGame = computed(() => String(route.query.game || '').trim())
const recoveryModuleId = computed(() => (
  recoverySourceKind.value === 'plugin' ? recoverySourceId.value : ''
))

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

async function loadMarketplace() {
  marketBusy.value = true
  marketError.value = ''
  try {
    const result = await moduleApi.marketplace(marketKeyword.value, marketRuleset.value)
    if (!result.ok) {
      market.value = []
      marketError.value = result.error || t('modulesOnlineUnavailable', { error: result.error_code || '' })
      return
    }
    market.value = result.modules || []
  } catch (cause: unknown) {
    market.value = []
    marketError.value = errorMessage(cause)
  } finally {
    marketBusy.value = false
  }
}

onMounted(() => {
  void load()
  void loadMarketplace()
  void checkRecovery()
})

// 恢复判定必须跟着**整组**恢复身份走：hash 导航（同页不同 query）不会重新挂载
// 组件，只 watch game 会让上一次的结论残留下来。
watch(
  [recoveryAdventure, recoveryVersion, recoveryDigest, recoverySourceKind, recoverySourceId, recoveryGame],
  () => { void checkRecovery() },
)

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
    await checkRecovery()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    importBusy.value = false
  }
}

async function installFromMarketplace(item: ModuleMarketplaceItem) {
  installBusy.value = item.id
  error.value = ''
  try {
    await moduleApi.installFromMarketplace(item.id, item.installed)
    await load()
    await loadMarketplace()
    await checkRecovery()
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    installBusy.value = ''
  }
}

// 投影的 reason code → 文案键（显式映射，不做字符串拼接）。
const RECOVERY_REASON_KEYS: Record<string, MessageKey> = {
  package_unavailable: 'modulesRecoveryReason_package_unavailable',
  binding_changed: 'modulesRecoveryReason_binding_changed',
  source_changed: 'modulesRecoveryReason_source_changed',
  source_conflict: 'modulesRecoveryReason_source_conflict',
  package_invalid: 'modulesRecoveryReason_package_invalid',
}

function recoveryReasonLabel(reason: string): string {
  return t(RECOVERY_REASON_KEYS[reason] || 'modulesRecoveryReason_unknown')
}

async function checkRecovery() {
  const game = recoveryGame.value
  recoveryCheck.value = null
  if (!game) return
  recoveryBusy.value = true
  try {
    const response = await api<{ adventure: RecoveryAdventure | null }>(
      `/games/${encodeURIComponent(game)}/adventure`,
    )
    const adventure = response.adventure || null
    const binding = adventure?.binding || {}
    const digestMatches = !recoveryDigest.value
      || String(binding.content_digest || '') === recoveryDigest.value
    const sourceMatches = !recoverySourceKind.value
      || (String(binding.source_kind || '') === recoverySourceKind.value
        && String(binding.source_id || '') === recoverySourceId.value)
    recoveryCheck.value = {
      ready: Boolean(adventure?.available) && digestMatches && sourceMatches,
      reason: String(adventure?.reason || ''),
      digestMatches,
      sourceMatches,
    }
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    recoveryBusy.value = false
  }
}
</script>

<template>
  <section class="view modules-page">
    <p class="eyebrow">{{ t('modulesKicker') }}</p>
    <h1>{{ t('navModules') }}</h1>
    <p class="view-intro">{{ t('modulesIntro') }}</p>

    <section v-if="recoveryAdventure" class="module-recovery" role="status" data-testid="modules-recovery">
      <strong>{{ t('modulesRecoveryTitle') }}</strong>
      <p>{{ t('modulesRecoveryHint', { adventure: recoveryAdventure, version: recoveryVersion || t('modulesRecoveryUnknownVersion') }) }}</p>
      <dl class="module-recovery-facts">
        <div v-if="recoveryDigest"><dt>{{ t('modulesRecoveryDigest') }}</dt><dd>{{ recoveryDigest }}</dd></div>
        <div v-if="recoverySourceKind"><dt>{{ t('modulesRecoverySource') }}</dt><dd>{{ recoverySourceKind }} · {{ recoverySourceId || '—' }}</dd></div>
        <div v-if="recoveryModuleId"><dt>{{ t('modulesRecoveryModule') }}</dt><dd>{{ recoveryModuleId }}</dd></div>
      </dl>
      <p v-if="!recoveryGame" class="muted">{{ t('modulesRecoveryNeedGame') }}</p>
      <template v-else>
        <p v-if="recoveryBusy" class="muted">{{ t('modulesRecoveryChecking') }}</p>
        <template v-else-if="recoveryCheck">
          <p v-if="recoveryCheck.ready" class="module-recovery-ready" data-testid="modules-recovery-ready">
            {{ t('modulesRecoveryReady') }}
          </p>
          <template v-else>
            <p class="error-text" data-testid="modules-recovery-blocked">
              {{ t('modulesRecoveryNotReady', { reason: recoveryReasonLabel(recoveryCheck.reason) }) }}
            </p>
            <p v-if="!recoveryCheck.digestMatches" class="error-text">{{ t('modulesRecoveryDigestMismatch') }}</p>
            <p v-if="!recoveryCheck.sourceMatches" class="error-text">{{ t('modulesRecoverySourceMismatch') }}</p>
          </template>
        </template>
        <div class="module-recovery-actions">
          <button type="button" :disabled="recoveryBusy" @click="checkRecovery">{{ t('modulesRecoveryCheck') }}</button>
          <RouterLink v-if="recoveryCheck?.ready" class="primary module-recovery-resume" :to="{ name: 'play', query: { game: recoveryGame } }">
            {{ t('modulesRecoveryResume') }}
          </RouterLink>
        </div>
      </template>
    </section>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section class="modules-section" data-testid="modules-installed">
      <h2>{{ t('modulesSectionInstalled') }}</h2>
      <p v-if="loading" class="muted">{{ t('modulesLoading') }}</p>
      <div v-else-if="!modules.length" class="empty-state">
        <p>{{ t('modulesEmpty') }}</p>
      </div>
      <div v-else class="modules-grid">
        <RouterLink v-for="module in modules" :key="module.id" :to="{ name: 'module-detail', params: { moduleId: module.id } }" class="module-card">
          <div>
            <p class="eyebrow">{{ module.is_module ? t('modulesAdventureModule') : t('modulesContentPack') }}</p>
            <h3>{{ module.name }}</h3>
            <p class="muted">{{ t('modulesVersion', { version: module.version }) }}</p>
            <p v-if="module.status === 'disabled'" class="muted">{{ t('modulesDisabledBadge') }}</p>
          </div>
          <dl>
            <div><dt>{{ t('modulesAdventures') }}</dt><dd>{{ module.adventure_count }}</dd></div>
            <div><dt>{{ t('modulesContentCount') }}</dt><dd>{{ totalContent(module) }}</dd></div>
          </dl>
        </RouterLink>
      </div>
    </section>

    <section class="modules-section" data-testid="modules-local-import">
      <h2>{{ t('modulesSectionLocal') }}</h2>
      <div class="module-import" :aria-label="t('modulesImport')">
        <label>{{ t('modulesImport') }} <input type="file" accept=".dfplugin" @change="selectImportFile"></label>
        <button type="button" :disabled="!importFile || importBusy" @click="previewImport">{{ t('modulesPreview') }}</button>
        <template v-if="preview">
          <p v-if="preview.blockers.length" class="error-text">{{ preview.blockers.join(', ') }}</p>
          <p v-if="preview.warnings.length" class="muted">{{ preview.warnings.join(', ') }}</p>
          <button type="button" :disabled="Boolean(preview.blockers.length) || importBusy" @click="importModule">{{ t('modulesInstall') }}</button>
        </template>
      </div>
    </section>

    <section class="modules-section" data-testid="modules-online">
      <h2>{{ t('modulesSectionOnline') }}</h2>
      <div class="module-market-search">
        <input v-model="marketKeyword" type="search" :placeholder="t('modulesOnlineSearchPlaceholder')" :aria-label="t('modulesOnlineSearch')" @keyup.enter="loadMarketplace">
        <label class="module-market-filter">
          <span>{{ t('modulesOnlineFilter') }}</span>
          <select v-model="marketRuleset" :aria-label="t('modulesOnlineFilter')" @change="loadMarketplace">
            <option value="all">{{ t('modulesOnlineFilterAll') }}</option>
            <option value="dnd2024">D&amp;D 2024</option>
            <option value="coc">CoC</option>
            <option value="freeform">Freeform</option>
          </select>
        </label>
        <button type="button" :disabled="marketBusy" @click="loadMarketplace">{{ t('modulesOnlineSearch') }}</button>
      </div>
      <p v-if="marketBusy" class="muted">{{ t('modulesLoading') }}</p>
      <p v-else-if="marketError" class="error-text">{{ marketError }}</p>
      <p v-else-if="!market.length" class="muted">{{ t('modulesOnlineEmpty') }}</p>
      <div v-else class="modules-grid">
        <article v-for="item in market" :key="item.id" class="module-card" :data-testid="`modules-market-${item.id}`">
          <div>
            <p class="eyebrow">{{ item.content_profile === 'adventure-module' ? t('modulesAdventureModule') : t('modulesContentPack') }}</p>
            <h3>{{ item.name || item.id }}</h3>
            <p class="muted">{{ t('modulesVersion', { version: item.latest_version || item.version }) }}</p>
            <p v-if="item.description" class="muted">{{ item.description }}</p>
            <p v-if="item.update_available" class="muted">{{ t('modulesUpdateAvailable') }}</p>
            <p v-if="item.verification_error" class="error-text">{{ item.verification_error }}</p>
          </div>
          <dl>
            <div><dt>{{ t('modulesAdventures') }}</dt><dd>{{ item.adventure_count }}</dd></div>
            <div><dt>{{ t('modulesRuntimeTargets') }}</dt><dd>{{ item.ruleset_targets.join(' · ') || '—' }}</dd></div>
          </dl>
          <button
            v-if="!item.installed"
            type="button"
            :disabled="!item.installable || installBusy === item.id"
            @click="installFromMarketplace(item)"
          >{{ t('modulesInstallOnline') }}</button>
          <button
            v-else-if="item.update_available"
            type="button"
            :disabled="installBusy === item.id"
            @click="installFromMarketplace(item)"
          >{{ t('modulesUpdateOnline', { version: item.latest_version }) }}</button>
          <span v-else class="muted">{{ t('modulesInstalledBadge') }}</span>
        </article>
      </div>
    </section>
  </section>
</template>

<style scoped>
.modules-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }
.modules-section { display: grid; gap: .75rem; margin-top: 1.75rem; }
.modules-section h2 { margin: 0; font-size: 1.05rem; }
.module-import, .module-market-search { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; padding: 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-2); }
.module-market-filter { display: inline-flex; align-items: center; gap: .45rem; color: var(--df-text-muted); font-size: .85rem; }
.module-market-filter select { min-height: 34px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text); background: var(--df-surface-1); }
.module-market-search input { flex: 1 1 12rem; min-width: 0; }
.module-recovery { margin: 1rem 0; padding: 1rem; border: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); border-left: 3px solid var(--df-accent); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.module-recovery p { margin: .4rem 0 0; }
.module-recovery-facts { display: grid; gap: .25rem; margin: .6rem 0 0; }
.module-recovery-facts dt { color: var(--df-text-muted); font-size: .78rem; }
.module-recovery-facts dd { margin: 0; font-size: .82rem; overflow-wrap: anywhere; }
.module-recovery-ready { color: var(--df-success); }
.module-recovery-actions { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .7rem; }
.module-recovery-resume { display: inline-flex; align-items: center; padding: .35rem .8rem; border-radius: .6rem; text-decoration: none; }
.module-card { display: grid; gap: 1rem; padding: 1.25rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); color: var(--df-text); }
.module-card h3 { margin: 0; font-size: 1rem; }
.module-card dl { display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 0; }
.module-card dt { color: var(--df-text-muted); font-size: .85rem; }
.module-card dd { margin: .2rem 0 0; font-size: 1.15rem; font-weight: 700; }
@media (max-width: 480px) {
  .module-import, .module-market-search { align-items: stretch; flex-direction: column; }
  .module-import label, .module-import button, .module-market-search button { width: 100%; }
  .module-import input[type="file"] { display: block; width: 100%; margin-top: .45rem; }
  .modules-grid { grid-template-columns: 1fr; }
  .module-card { padding: 1rem; }
}
</style>
