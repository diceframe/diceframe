<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import {
  moduleApi,
  type ModuleActionGuard,
  type ModuleCompatibilityResponse,
  type ModuleDetail,
  type ModuleUsageResponse,
  type ModuleMarketplaceItem,
} from '@/api/modules'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import ModuleResourceBrowser from './resources/ModuleResourceBrowser.vue'
import { contentKindLabel, formatModuleBlocker, missingRuntimeFromBlocker, moduleCategoryLabel, moduleCopy, moduleInventory } from './copy'
import type { MessageKey } from '@/i18n'

const route = useRoute()
const router = useRouter()
const { t, locale } = useLocale()
const copy = computed(() => moduleCopy(locale.value))

// Readable labels for host enums; the raw value stays next to them in the
// technical section for troubleshooting. Unknown values are shown as-is.
const statusKeys: Record<string, MessageKey> = {
  running: 'modulesStatusRunning', disabled: 'modulesDisabledBadge', failed: 'modulesStatusFailed', stopping: 'modulesStatusStopping',
}
const deliveryKeys: Record<string, MessageKey> = {
  catalog: 'modulesDeliveryCatalog', legacy_autoimport: 'modulesDeliveryLegacy',
}
function statusLabel(status: string): string {
  return statusKeys[status] ? t(statusKeys[status]) : status
}
function deliveryLabel(mode: string): string {
  return deliveryKeys[mode] ? t(deliveryKeys[mode]) : mode
}
const { confirm } = useConfirm()
const moduleId = computed(() => String(route.params.moduleId || ''))
const module = ref<ModuleDetail | null>(null)
const compatibility = ref<ModuleCompatibilityResponse | null>(null)
// 缺规则运行时是「能自己解决」的阻塞：给出直达扩展中心的入口，而不是让用户
// 在一句错误文案里自己找。runtime id 允许带命名空间冒号，商店插件 id 用连字符，
// 所以搜索词按连字符形式传（fu:core → fu-core）。
const missingRuntime = computed(() => {
  for (const blocker of compatibility.value?.blockers || []) {
    const runtimeId = missingRuntimeFromBlocker(blocker)
    if (runtimeId) return runtimeId
  }
  return ''
})
function openExtensionsCenter(runtimeId: string): void {
  void router.push({
    name: 'plugins',
    query: { tab: 'marketplace', q: runtimeId.replace(/:/g, '-') },
  })
}
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

// 包内资料册的条目只在包里（catalog 交付不启用即灌注）。「查看包内资料」如果
// 只列书名，玩家永远读不到那 12 条——这里就地读声明内容的只读正文。
type ModuleBookEntry = { id?: string; name?: string; content?: string; type?: string; tier?: string; unreliable?: boolean }
const openLorebook = ref('')
const lorebookEntries = ref<Record<string, ModuleBookEntry[]>>({})
// issue #708：玩家视角下服务端只返回公开条目；隐藏数来自服务端，用于提示。
const lorebookHidden = ref<Record<string, number>>({})
const lorebookEntryErrors = ref<Record<string, boolean>>({})
const lorebookEntryBusy = ref('')
async function toggleLorebook(book: { id: string; key?: string }): Promise<void> {
  if (!book.key) return
  if (openLorebook.value === book.id) { openLorebook.value = ''; return }
  openLorebook.value = book.id
  if (lorebookEntries.value[book.id]) return
  lorebookEntryBusy.value = book.id
  try {
    const result = await api<{ ok: boolean; content?: { entries?: ModuleBookEntry[]; hidden_entries?: number } }>(
      `/modules/${encodeURIComponent(moduleId.value)}/content/lorebook/${encodeURIComponent(book.key)}`,
    )
    if (!result.ok || !Array.isArray(result.content?.entries)) throw new Error('entries unavailable')
    lorebookEntries.value = { ...lorebookEntries.value, [book.id]: result.content!.entries! }
    lorebookHidden.value = { ...lorebookHidden.value, [book.id]: Number(result.content?.hidden_entries || 0) }
    lorebookEntryErrors.value = { ...lorebookEntryErrors.value, [book.id]: false }
  } catch {
    lorebookEntryErrors.value = { ...lorebookEntryErrors.value, [book.id]: true }
  } finally {
    lorebookEntryBusy.value = ''
  }
}
const isDisabled = computed(() => module.value?.status === 'disabled')
const canLaunch = computed(() => !isDisabled.value && module.value?.inventory_status !== 'unknown' && compatibility.value?.ok === true && !compatibility.value.blockers?.length)

type ModuleAction = 'update' | 'disable' | 'enable' | 'uninstall'

// Keep the management controls in one declarative list.  The old template
// duplicated the guard/disabled wiring for each button, which made the
// enabled and disabled states drift as new actions were added.
// The online catalog entry for this pack, if any. Update is offered only when
// the catalog has a newer version: packs imported from a file, or not listed
// online, have nothing to update from.
const onlineEntry = ref<ModuleMarketplaceItem | null>(null)

const actionButtons = computed(() => [
  ...(onlineEntry.value?.update_available ? [{
    action: 'update' as ModuleAction,
    label: t('modulesActionUpdateTo', { version: onlineEntry.value.latest_version }),
    guard: 'update',
    variant: 'secondary',
  }] : []),
  {
    action: (isDisabled.value ? 'enable' : 'disable') as ModuleAction,
    label: t(isDisabled.value ? 'modulesActionEnable' : 'modulesActionDisable'),
    guard: isDisabled.value ? '' : 'disable',
    variant: 'secondary',
  },
  {
    action: 'uninstall' as ModuleAction,
    label: t('modulesActionUninstall'),
    guard: 'uninstall',
    variant: 'danger',
  },
])

function launch(adventureId: string) {
  if (!canLaunch.value) return
  void router.push({ name: 'create', query: { module_id: moduleId.value, adventure_id: adventureId } })
}

// 资源包没有固定剧情，但仍声明了资料册/规则等资源：用同一个 module_id 深链
// 把包带到创建页，创建页据此把它的资料册作为本局候选，开局仍走自由模式。
function launchWithPack() {
  if (!canLaunch.value) return
  void router.push({ name: 'create', query: { module_id: moduleId.value } })
}

let loadSequence = 0
async function loadDetail() {
  const id = moduleId.value
  const detail = await moduleApi.detail(id)
  if (!detail.ok || !detail.module) throw new Error(t('modulesNotFound'))
  if (id === moduleId.value) module.value = detail.module
}

async function load() {
  const current = ++loadSequence
  const id = moduleId.value
  module.value = null
  compatibility.value = null
  usage.value = null
  error.value = ''
  try {
    const [compatibilityResult, usageResult, detail] = await Promise.all([
      moduleApi.compatibility(id),
      moduleApi.usages(id),
      moduleApi.detail(id),
    ])
    if (current !== loadSequence) return
    if (!detail.ok || !detail.module) throw new Error(t('modulesNotFound'))
    module.value = detail.module
    compatibility.value = compatibilityResult
    usage.value = usageResult
  } catch (cause: unknown) {
    if (current === loadSequence) error.value = errorMessage(cause)
  }
  void loadOnlineEntry(id, current)
}

// Best effort: an unreachable catalog only hides the update action.
async function loadOnlineEntry(id: string, sequence: number) {
  onlineEntry.value = null
  try {
    const result = await moduleApi.marketplace(id)
    if (sequence !== loadSequence || !result.ok) return
    onlineEntry.value = (result.modules || []).find(item => item.id === id) || null
  } catch {
    // Keep the page usable offline.
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

async function run(action: ModuleAction) {
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
    let upToDate = false
    if (action === 'update') upToDate = Boolean((await moduleApi.update(moduleId.value)).up_to_date)
    else if (action === 'disable') await moduleApi.disable(moduleId.value)
    else if (action === 'enable') await moduleApi.enable(moduleId.value)
    else {
      await moduleApi.uninstall(moduleId.value)
      // 卸载后详情不再存在，回列表（留在页面上只会得到 404 文案）。
      await router.push({ name: 'modules' })
      return
    }
    status.value = t(upToDate ? 'modulesAlreadyLatest' : 'modulesActionDone')
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
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-if="status" class="module-status" role="status">{{ status }}</p>
    <div v-if="!module" class="module-detail-fallback" data-testid="module-fallback-navigation">
      <nav class="module-detail-fallback-actions">
        <a href="#/modules" class="content-button">{{ t('modulesBack') }}</a>
      </nav>
      <p v-if="!error" class="muted" data-testid="module-loading">{{ t('modulesLoading') }}</p>
    </div>

    <template v-if="module">
      <!-- ── 顶栏 ── -->
      <div class="mod-header">
        <RouterLink :to="{ name: 'modules' }" class="mod-back-link">← {{ t('modulesBack') }}</RouterLink>
        <div class="mod-header-main">
          <div class="mod-header-identity">
            <p class="mod-header-eyebrow">{{ moduleCategoryLabel(module, copy) }}</p>
            <h1 class="mod-header-title">{{ module.name }}</h1>
            <p class="mod-header-meta">
              <span>v{{ module.version }}</span>
              <span :class="['mod-status-badge', isDisabled ? 'is-disabled' : 'is-enabled']">
                {{ isDisabled ? t('modulesDisabledBadge') : t('modulesInstalledBadge') }}
              </span>
            </p>
          </div>
          <!-- 操作按钮 -->
          <div class="mod-header-actions" data-testid="module-action-toolbar">
            <button
              v-for="item in actionButtons"
              :key="item.action"
              type="button"
              class="mod-action-btn"
              :class="item.variant"
              :disabled="busy !== '' || Boolean(item.guard && !guardFor(item.guard).allowed)"
              @click="run(item.action)"
            >
              <span v-if="busy === item.action" class="mod-action-busy">…</span>
              {{ item.label }}
            </button>
          </div>
        </div>
        <p v-if="!guardFor('update').allowed || !guardFor('uninstall').allowed" class="mod-blocked-hint error-text">
          {{ t('modulesActionBlocked', { count: boundGames.length }) }}
        </p>
      </div>

      <!-- ── 主内容两列 ── -->
      <div class="mod-layout">
        <!-- 左列 -->
        <div class="mod-left">

          <!-- 冒险列表 -->
          <section v-if="module.adventures.length" class="mod-card" data-testid="module-adventures">
            <h2 class="mod-card-title">{{ canLaunch ? copy.playableAdventures : copy.installedAdventures }}</h2>
            <p v-if="isDisabled" class="muted">{{ copy.disabledAdventures }}</p>
            <ul class="mod-adventure-list">
              <li v-for="adv in module.adventures" :key="adv.adventure_id" class="mod-adventure-row">
                <div class="mod-adventure-copy">
                  <strong>{{ adv.name || adv.adventure_id }}</strong>
                  <small>{{ adv.adventure_id }} · v{{ adv.version }}</small>
                  <p v-if="adv.summary" class="muted">{{ adv.summary }}</p>
                </div>
                <button type="button" class="mod-action-btn secondary" :disabled="!canLaunch || busy !== ''" @click="launch(adv.adventure_id)">
                  {{ t('moduleAdventureLaunch') }}
                </button>
              </li>
            </ul>
            <p v-if="!canLaunch" class="muted mod-launch-hint">{{ t('moduleAdventureLaunchBlocked') }}</p>
          </section>

          <!-- 资源内容包（无冒险）启动 -->
          <section v-else-if="moduleInventory(module) === 'resources'" class="mod-card">
            <h2 class="mod-card-title">{{ copy.resourcePack }}</h2>
            <p class="muted">{{ copy.noFixedStory }}</p>
            <button v-if="module.lorebooks?.length" type="button" class="mod-action-btn secondary mod-launch-btn" :disabled="!canLaunch || busy !== ''" @click="launchWithPack">
              {{ t('moduleLorebookLaunch') }}
            </button>
            <p v-if="!canLaunch" class="muted mod-launch-hint">{{ t('moduleAdventureLaunchBlocked') }}</p>
          </section>

          <!-- 资料册 -->
          <section v-if="(module.lorebooks || []).length" class="mod-card" data-testid="module-lorebooks">
            <h2 class="mod-card-title">{{ t('modulesLorebooks') }}</h2>
            <ul class="mod-lorebook-list">
              <li v-for="book in (module.lorebooks || [])" :key="book.id" class="mod-lorebook-item">
                <div class="mod-lorebook-row">
                  <div class="mod-lorebook-info">
                    <strong>{{ book.name }}</strong>
                    <small>{{ book.language }}<template v-if="typeof book.entry_count === 'number'"> · {{ t('modulesLorebookEntries', { count: book.entry_count }) }}</template></small>
                  </div>
                  <div class="mod-lorebook-controls">
                    <span class="mod-lorebook-origin" :data-catalog="book.catalog === true">
                      {{ book.catalog ? t('modulesLorebookInPack') : t('modulesLorebookInLibrary') }}
                    </span>
                    <button v-if="book.catalog && book.key" type="button" class="mod-lorebook-toggle" :aria-expanded="openLorebook === book.id" @click="toggleLorebook(book)">
                      {{ openLorebook === book.id ? t('modulesLorebookClose') : t('modulesLorebookOpen') }}
                    </button>
                  </div>
                </div>
                <div v-if="openLorebook === book.id" class="mod-lorebook-entries" data-testid="module-lorebook-entries">
                  <p v-if="lorebookEntryBusy === book.id" class="muted">{{ t('modulesLorebookEntriesLoading') }}</p>
                  <p v-else-if="lorebookEntryErrors[book.id]" class="muted">{{ t('modulesLorebookEntriesFailed') }}</p>
                  <template v-else>
                    <article v-for="(entry, i) in (lorebookEntries[book.id] || [])" :key="entry.id || i" class="mod-entry">
                      <strong>{{ entry.name || entry.id }}</strong>
                      <p>{{ entry.content }}</p>
                    </article>
                    <p v-if="lorebookHidden[book.id]" class="muted">{{ t('modulesLorebookHiddenNote', { count: lorebookHidden[book.id] }) }}</p>
                    <p class="muted">{{ t('modulesLorebookReadonlyNote') }}</p>
                  </template>
                </div>
              </li>
            </ul>
            <p v-if="(module.lorebooks || []).some(b => b.catalog)" class="muted mod-lorebook-hint">{{ t('modulesLorebookPackHint') }}</p>
          </section>

          <!-- 资源浏览 -->
          <ModuleResourceBrowser :key="moduleId" :module-id="moduleId" />
        </div>

        <!-- 右列：元信息 -->
        <aside class="mod-right">
          <!-- 内容数量 -->
          <div class="mod-side-card" data-testid="module-content">
            <h3 class="mod-side-title">{{ copy.resourceContributions }}</h3>
            <ul class="mod-content-list">
              <li v-for="(items, kind) in module.content" :key="kind">
                <span>{{ contentKindLabel(kind, locale) }}</span>
                <strong>{{ items.length }}</strong>
              </li>
            </ul>
          </div>

          <!-- 兼容性 -->
          <div class="mod-side-card" data-testid="module-compatibility">
            <h3 class="mod-side-title">{{ t('modulesCompatibility') }}</h3>
            <p v-if="compatibility?.blockers?.length" class="error-text">{{ compatibility.blockers.map(blocker => formatModuleBlocker(blocker, locale)).join(locale === 'zh-CN' ? '；' : '; ') }}</p>
            <p v-else class="mod-compat-ok">{{ t('modulesCompatible') }}</p>
            <button
              v-if="missingRuntime"
              type="button"
              class="mod-runtime-cta"
              data-testid="module-install-runtime"
              @click="openExtensionsCenter(missingRuntime)"
            >{{ t('modulesInstallRuntimeCta') }}</button>
            <p v-if="compatibility?.warnings?.length" class="muted">{{ compatibility.warnings.join(', ') }}</p>
          </div>

          <!-- 技术信息 -->
          <details class="mod-side-card mod-technical" data-testid="module-metadata">
            <summary>{{ t('modulesMetadata') }}</summary>
            <dl class="mod-facts">
              <div><dt>{{ t('modulesModuleId') }}</dt><dd>{{ module.id }}</dd></div>
              <div><dt>{{ t('modulesVersionLabel') }}</dt><dd>{{ module.version }}</dd></div>
              <div><dt>{{ t('modulesStatus') }}</dt><dd>{{ statusLabel(module.status) }}</dd></div>
              <div><dt>{{ t('modulesContentDelivery') }}</dt><dd>{{ deliveryLabel(module.content_delivery_mode) }}</dd></div>
              <div v-for="runtime in module.required_runtimes || []" :key="runtime.id">
                <dt>{{ locale === 'zh-CN' ? '需要运行时' : 'Requires runtime' }}</dt>
                <dd>{{ runtime.id }} ≥{{ runtime.minimum_version }}</dd>
              </div>
            </dl>
          </details>

          <!-- 使用中的存档 -->
          <details class="mod-side-card" data-testid="module-usage">
            <summary>{{ t('modulesInUse') }} ({{ usage?.total_games ?? boundGames.length }})</summary>
            <p v-if="!boundGames.length" class="muted">{{ t('modulesBoundNone') }}</p>
            <ul v-else class="mod-game-list">
              <li v-for="game in boundGames" :key="game.game_key">{{ game.game_key }}</li>
            </ul>
          </details>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
.module-status { margin: 0; color: var(--df-success); font-size: .88rem; }
.module-detail-fallback { display: grid; gap: 1rem; padding-top: 1rem; }
.module-detail-fallback-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.mod-back-link { display: inline-flex; align-items: center; gap: .3rem; color: var(--df-text-muted); font-size: .84rem; text-decoration: none; margin-bottom: .9rem; }
.mod-back-link:hover { color: var(--df-text); }
.mod-header { display: grid; gap: .4rem; padding-bottom: 1.25rem; border-bottom: 1px solid var(--df-border-soft); }
.mod-header-main { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.mod-header-eyebrow { margin: 0 0 .3rem; color: var(--df-interactive-strong); font-size: .7rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.mod-header-title { margin: 0 0 .4rem; font-family: var(--df-font-title, inherit); font-size: clamp(1.3rem, 2.5vw, 2rem); line-height: 1.2; }
.mod-header-meta { display: flex; align-items: center; gap: .6rem; margin: 0; }
.mod-header-meta span { color: var(--df-text-muted); font-size: .84rem; }
.mod-status-badge { display: inline-flex; align-items: center; padding: .18rem .55rem; border-radius: 999px; font-size: .74rem; font-weight: 700; border: 1px solid; }
.mod-status-badge.is-enabled { border-color: color-mix(in srgb, var(--df-success) 50%, var(--df-border-soft)); color: var(--df-success); background: color-mix(in srgb, var(--df-success) 10%, transparent); }
.mod-status-badge.is-disabled { border-color: color-mix(in srgb, var(--df-warning) 50%, var(--df-border-soft)); color: var(--df-warning); background: color-mix(in srgb, var(--df-warning) 10%, transparent); }
.mod-header-actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.mod-blocked-hint { margin: .3rem 0 0; font-size: .82rem; }
.mod-action-btn { display: inline-flex; align-items: center; gap: .4rem; min-height: 36px; padding: 0 .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); color: var(--df-text); font: .88rem/1 inherit; cursor: pointer; white-space: nowrap; transition: background .12s, border-color .12s; }
.mod-action-btn:hover { background: var(--df-surface-1); border-color: var(--df-interactive); }
.mod-action-btn:disabled { opacity: .55; cursor: not-allowed; }
.mod-action-btn.secondary { border-color: color-mix(in srgb, var(--df-interactive) 50%, var(--df-border-soft)); color: var(--df-interactive-strong); background: color-mix(in srgb, var(--df-interactive) 8%, var(--df-surface-2)); }
.mod-action-btn.danger { border-color: color-mix(in srgb, var(--df-danger) 55%, var(--df-border-soft)); color: var(--df-danger-strong); }
.mod-action-busy { font-size: .9em; opacity: .7; }
.mod-launch-btn { margin-top: .6rem; }
.mod-launch-hint { margin-top: .4rem; font-size: .8rem; }
.mod-layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 1.5rem; margin-top: 1.5rem; align-items: start; }
.mod-left { display: grid; gap: 1.25rem; }
.mod-right { display: grid; gap: .85rem; }
.mod-card { padding: 1.1rem 1.25rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); }
.mod-card-title { margin: 0 0 .75rem; font-size: 1rem; font-weight: 700; }
.mod-adventure-list { list-style: none; display: grid; gap: .6rem; margin: 0; padding: 0; }
.mod-adventure-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .75rem; padding: .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.mod-adventure-copy { flex: 1 1 16rem; min-width: 0; display: grid; gap: .2rem; }
.mod-adventure-copy strong { font-size: .95rem; }
.mod-adventure-copy small { color: var(--df-text-muted); font-size: .78rem; }
.mod-adventure-copy p { margin: 0; color: var(--df-text-muted); font-size: .84rem; line-height: 1.5; }
.mod-lorebook-list { list-style: none; display: grid; gap: .5rem; margin: 0; padding: 0; }
.mod-lorebook-item { display: grid; gap: .5rem; }
.mod-lorebook-row { display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.mod-lorebook-info { display: grid; gap: .1rem; min-width: 0; }
.mod-lorebook-info strong { font-size: .88rem; }
.mod-lorebook-info small { color: var(--df-text-muted); font-size: .76rem; }
.mod-lorebook-controls { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; justify-content: flex-end; }
.mod-lorebook-toggle { padding: .2rem .55rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: transparent; color: var(--df-interactive-strong); font-size: .76rem; cursor: pointer; }
.mod-lorebook-origin { color: var(--df-text-muted); font-size: .76rem; white-space: nowrap; }
.mod-lorebook-origin[data-catalog="true"] { color: var(--df-interactive-strong); }
.mod-lorebook-entries { display: grid; gap: .5rem; padding: .65rem .75rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.mod-entry { display: grid; gap: .18rem; padding-bottom: .45rem; border-bottom: 1px solid color-mix(in srgb, var(--df-border-soft) 50%, transparent); }
.mod-entry:last-child { border-bottom: 0; padding-bottom: 0; }
.mod-entry strong { font-size: .86rem; }
.mod-entry p { margin: 0; color: var(--df-text-secondary); font-size: .8rem; line-height: 1.6; white-space: pre-wrap; }
.mod-lorebook-hint { margin-top: .4rem; font-size: .8rem; }
.mod-side-card { padding: .85rem 1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); }
.mod-side-title { margin: 0 0 .6rem; font-size: .88rem; font-weight: 700; }
.mod-content-list { list-style: none; display: grid; gap: .3rem; margin: 0; padding: 0; }
.mod-content-list li { display: flex; align-items: center; justify-content: space-between; gap: .6rem; padding: .3rem 0; border-bottom: 1px solid color-mix(in srgb, var(--df-border-soft) 55%, transparent); }
.mod-content-list li:last-child { border-bottom: 0; }
.mod-content-list li span { color: var(--df-text-muted); font-size: .82rem; }
.mod-content-list li strong { color: var(--df-interactive-strong); font-size: .88rem; }
.mod-compat-ok { margin: 0; color: var(--df-success); font-size: .84rem; font-weight: 600; }
.mod-runtime-cta { justify-self: start; min-height: 32px; padding: 0 .7rem; border: 1px solid var(--df-border-strong, var(--df-border)); border-radius: var(--df-radius-sm, 4px); background: var(--df-surface-2, transparent); color: var(--df-interactive-strong); font-size: .8rem; font-weight: 600; cursor: pointer; }
.mod-runtime-cta:hover { border-color: var(--df-accent-strong, var(--df-accent)); }
.mod-technical { overflow: hidden; }
.mod-technical summary { cursor: pointer; font-size: .84rem; font-weight: 700; color: var(--df-text-muted); list-style: none; }
.mod-technical[open] summary { color: var(--df-text); margin-bottom: .5rem; }
.mod-facts { display: grid; gap: .4rem; margin: 0; }
.mod-facts > div { display: grid; grid-template-columns: auto 1fr; gap: .6rem; padding-bottom: .35rem; border-bottom: 1px solid color-mix(in srgb, var(--df-border-soft) 50%, transparent); }
.mod-facts > div:last-child { border-bottom: 0; }
.mod-facts dt { color: var(--df-text-muted); font-size: .78rem; white-space: nowrap; }
.mod-facts dd { margin: 0; overflow-wrap: anywhere; font-size: .82rem; }
.mod-game-list { list-style: none; display: grid; gap: .3rem; margin: .4rem 0 0; padding: 0; }
.mod-game-list li { font-size: .8rem; color: var(--df-text-muted); overflow-wrap: anywhere; }
@media (max-width: 800px) { .mod-layout { grid-template-columns: 1fr; } .mod-right { grid-row: 1; } }
@media (max-width: 520px) { .mod-header-main { flex-direction: column; } .mod-header-actions { width: 100%; } }
</style>