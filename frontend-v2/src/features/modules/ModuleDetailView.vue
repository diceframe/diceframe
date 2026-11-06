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
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import ModuleResourceBrowser from './resources/ModuleResourceBrowser.vue'
import { contentKindLabel, moduleCategory, moduleCopy } from './copy'
import { libraryCopy } from '@/features/library/copy'
import type { Locale } from '@/i18n'

const route = useRoute()
const router = useRouter()
const { t, locale } = useLocale()
const copy = computed(() => moduleCopy(locale.value))
const library = computed(() => libraryCopy(locale.value))
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
const canLaunch = computed(() => !isDisabled.value && module.value?.inventory_status !== 'unknown' && compatibility.value?.ok === true && !compatibility.value.blockers?.length)

type ModuleAction = 'update' | 'disable' | 'enable' | 'uninstall'

// Keep the management controls in one declarative list.  The old template
// duplicated the guard/disabled wiring for each button, which made the
// enabled and disabled states drift as new actions were added.
const actionButtons = computed(() => [
  {
    action: 'update' as ModuleAction,
    label: t('modulesActionUpdate'),
    guard: 'update',
    variant: 'secondary',
  },
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

type ModuleIdentityCopy = {
  title: string
  description: string
  identityLabel: string
  identityHint: string
  packageLabel: string
  packageHint: string
}

const identityCopies: Record<Locale, ModuleIdentityCopy> = {
  'zh-CN': {
    title: '来源与复制说明', description: '选择包内冒险开局，或查看资料并创建可编辑的内容副本。',
    identityLabel: '内容包身份', identityHint: '包名称、ID 和版本帮助你认出来源；需要分享或排查问题时，可以据此准确说明使用的是哪一份内容。',
    packageLabel: '复制方式', packageHint: '可复制的资料会在资源列表提供“创建副本”；规则、地图等由所属系统管理的资料会保持只读。',
  },
  en: {
    title: 'Source and copy notes', description: 'See where this content came from and which resources you can copy into your library to edit.',
    identityLabel: 'Package identity', identityHint: 'The package name, ID, and version help you recognize the source and describe exactly which content you are using when sharing or troubleshooting.',
    packageLabel: 'Copy options', packageHint: 'Resources you can edit offer a “Create copy” action in the resource list. Rules and maps managed by their ruleset remain read-only.',
  },
  ja: {
    title: 'ソースとコピーについて', description: 'このコンテンツの出所と、自分のライブラリにコピーして編集できる資料を確認できます。',
    identityLabel: 'コンテンツの出所', identityHint: 'パッケージ名、ID、バージョンで出所を確認できます。共有や問題の確認時にも、どのコンテンツかを正確に伝えられます。',
    packageLabel: 'コピーできる資料', packageHint: '編集できる資料には一覧に「コピーを作成」入口があります。ルールセットが管理するルールやマップは読み取り専用です。',
  },
  de: {
    title: 'Quelle und Kopie', description: 'Hier sehen Sie, woher dieser Inhalt kommt und welche Ressourcen Sie zum Bearbeiten in Ihre Bibliothek kopieren können.',
    identityLabel: 'Inhaltsquelle', identityHint: 'Paketname, ID und Version zeigen, aus welcher Quelle der Inhalt stammt. So lässt sich bei Fragen genau angeben, welche Ausgabe verwendet wird.',
    packageLabel: 'Kopiermöglichkeiten', packageHint: 'Bearbeitbare Ressourcen bieten in der Liste „Kopie erstellen“. Regeln und Karten, die vom Regelsystem verwaltet werden, bleiben schreibgeschützt.',
  },
  ru: {
    title: 'Источник и копирование', description: 'Здесь указано, откуда взялся этот контент и какие материалы можно скопировать в свою библиотеку для редактирования.',
    identityLabel: 'Источник контента', identityHint: 'Название пакета, ID и версия помогают узнать источник. При обмене или разборе проблемы по ним можно точно указать используемый контент.',
    packageLabel: 'Возможности копирования', packageHint: 'Для материалов, которые можно редактировать, в списке есть действие «Создать копию». Правила и карты, которыми управляет система правил, остаются только для чтения.',
  },
}

const identity = computed(() => identityCopies[locale.value] || identityCopies.en)

function launch(adventureId: string) {
  if (!canLaunch.value) return
  void router.push({ name: 'create', query: { module_id: moduleId.value, adventure_id: adventureId } })
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
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-if="status" class="module-status" role="status">{{ status }}</p>
    <div v-if="!module" class="module-detail-fallback" data-testid="module-fallback-navigation">
      <nav class="module-detail-fallback-actions" :aria-label="t('modulesBack')">
        <a href="#/modules" class="content-button">{{ t('modulesBack') }}</a>
        <a href="#/library" class="content-button">{{ library.backLibrary }}</a>
      </nav>
      <p v-if="!error" class="muted" data-testid="module-loading">{{ t('modulesLoading') }}</p>
    </div>
    <template v-if="module">
      <ContentPageHeader
        kicker="CONTENT PACKS"
        :title="module.name"
        :description="`${module.version} · ${isDisabled ? t('modulesDisabledBadge') : t('modulesInstalledBadge')}`"
      >
        <template #actions>
          <RouterLink :to="{ name: 'modules' }" class="content-button content-button-primary">{{ t('modulesBack') }}</RouterLink>
        </template>
      </ContentPageHeader>

      <section class="module-identity-card" data-testid="module-identity-note">
        <div class="module-identity-heading">
          <div>
            <p class="eyebrow">{{ identity.title }}</p>
            <p class="module-identity-description">{{ identity.description }}</p>
          </div>
          <span class="module-status-badge" :class="{ 'is-disabled': isDisabled }">
            {{ isDisabled ? t('modulesDisabledBadge') : t('modulesInstalledBadge') }}
          </span>
        </div>
        <dl class="module-quick-facts">
          <div><dt>{{ identity.identityLabel }}</dt><dd>{{ module.id }}</dd></div>
          <div><dt>{{ t('modulesVersionLabel') }}</dt><dd>{{ module.version }}</dd></div>
          <div><dt>{{ copy.contentProfile }}</dt><dd>{{ module.content_profile }}</dd></div>
        </dl>
        <div class="module-identity-notes">
          <p><strong>{{ identity.identityLabel }}</strong>{{ identity.identityHint }}</p>
          <p><strong>{{ identity.packageLabel }}</strong>{{ identity.packageHint }}</p>
        </div>
      </section>

      <section class="module-detail-section module-actions-section" data-testid="module-actions">
        <div class="module-section-heading">
          <div>
            <p class="eyebrow">{{ t('modulesActions') }}</p>
            <h2>{{ t('modulesActions') }}</h2>
          </div>
          <span v-if="busy" class="module-busy-label" role="status">{{ t('modulesLoading') }}</span>
        </div>
        <p v-if="!guardFor('update').allowed || !guardFor('uninstall').allowed" class="error-text" data-testid="module-action-blocked">
          {{ t('modulesActionBlocked', { count: boundGames.length }) }}
        </p>
        <div class="module-actions module-action-toolbar" data-testid="module-action-toolbar">
          <button
            v-for="item in actionButtons"
            :key="item.action"
            type="button"
            class="content-button"
            :class="{ 'content-button-primary': item.variant === 'secondary', danger: item.variant === 'danger' }"
            :disabled="busy !== '' || Boolean(item.guard && !guardFor(item.guard).allowed)"
            @click="run(item.action)"
          >{{ item.label }}</button>
        </div>
      </section>

      <section class="module-detail-section" data-testid="module-adventures">
        <div class="module-section-heading">
          <div>
            <p class="eyebrow">{{ copy.packLabel }}</p>
            <h2>{{ module.adventures.length ? (canLaunch ? copy.playableAdventures : copy.installedAdventures) : moduleCategory(module) === 'resource' ? copy.resourcePack : copy.packLabel }}</h2>
          </div>
          <span class="module-section-count">{{ module.adventures.length }}</span>
        </div>
        <p v-if="module.inventory_status === 'unknown'" class="muted" role="status">{{ copy.inventoryUnknown }}</p>
        <p v-else-if="!module.adventures.length && moduleCategory(module) === 'resource'" class="muted">{{ copy.noFixedStory }}</p>
        <p v-if="isDisabled && module.adventures.length" class="muted">{{ copy.disabledAdventures }}</p>
        <ul v-if="module.adventures.length" class="module-adventure-list"><li v-for="adventure in module.adventures" :key="adventure.adventure_id" class="module-adventure-card">
          <div class="module-adventure-copy"><strong>{{ adventure.name || adventure.adventure_id }}</strong><small>{{ adventure.adventure_id }} · {{ adventure.version }}</small><p v-if="adventure.summary">{{ adventure.summary }}</p></div>
          <button type="button" class="content-button content-button-primary" :disabled="!canLaunch || busy !== ''" @click="launch(adventure.adventure_id)">{{ t('moduleAdventureLaunch') }}</button>
        </li></ul>
        <p v-if="module.adventures.length && !canLaunch" class="muted">{{ t('moduleAdventureLaunchBlocked') }}</p>
      </section>

      <section class="module-detail-section" data-testid="module-content">
        <div class="module-section-heading">
          <div><p class="eyebrow">{{ copy.resourceContributions }}</p><h2>{{ copy.resourceContributions }}</h2></div>
          <span class="module-section-count">{{ Object.values(module.content || {}).reduce((total, items) => total + items.length, 0) }}</span>
        </div>
        <ul class="module-content-list"><li v-for="(items, kind) in module.content" :key="kind"><span>{{ contentKindLabel(kind, locale) }} · <strong>{{ items.length }}</strong></span></li></ul>
      </section>

      <ModuleResourceBrowser :key="moduleId" :module-id="moduleId" />

      <details class="module-detail-section module-technical" data-testid="module-compatibility" :open="Boolean(compatibility?.blockers?.length)">
        <summary>{{ t('modulesCompatibility') }}</summary>
        <p v-if="compatibility?.blockers?.length" class="error-text">{{ compatibility.blockers.join(', ') }}</p>
        <p v-else>{{ t('modulesCompatible') }}</p>
        <p v-if="compatibility?.warnings?.length" class="muted">{{ compatibility.warnings.join(', ') }}</p>
      </details>

      <details class="module-detail-section module-technical" data-testid="module-metadata">
        <summary>{{ t('modulesMetadata') }}</summary>
        <dl class="module-facts">
          <div><dt>{{ t('modulesModuleId') }}</dt><dd>{{ module.id }}</dd></div>
          <div><dt>{{ copy.contentProfile }}</dt><dd>{{ module.content_profile }}</dd></div>
          <div><dt>{{ t('modulesVersionLabel') }}</dt><dd>{{ module.version }}</dd></div>
          <div><dt>{{ t('modulesStatus') }}</dt><dd>{{ module.status }}</dd></div>
          <div><dt>{{ t('modulesContentDelivery') }}</dt><dd>{{ module.content_delivery_mode }}</dd></div>
        </dl>
      </details>

      <section class="module-detail-section" data-testid="module-lorebooks">
        <h2>{{ t('modulesLorebooks') }}</h2>
        <p v-if="!(module.lorebooks || []).length" class="muted">{{ t('modulesNoLorebooks') }}</p>
        <ul v-else>
          <li v-for="book in (module.lorebooks || [])" :key="book.id">{{ book.name }} · {{ book.language }}</li>
        </ul>
      </section>

      <details class="module-detail-section module-technical" data-testid="module-usage">
        <summary>{{ t('modulesInUse') }}</summary>
        <p>{{ t('modulesGameCount', { count: usage?.total_games ?? boundGames.length }) }}</p>
        <p v-if="!boundGames.length" class="muted">{{ t('modulesBoundNone') }}</p>
        <ul v-else>
          <li v-for="game in boundGames" :key="game.game_key">{{ game.game_key }} · {{ game.adventure_id }}</li>
        </ul>
      </details>

    </template>
  </section>
</template>

<style scoped>
.module-detail-fallback { display: grid; gap: 1rem; padding-top: 1rem; }
.module-detail-fallback-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.module-detail-fallback-actions .content-button { min-width: 10rem; }
.module-detail-section { margin-top: 1.5rem; padding: 1.25rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: linear-gradient(145deg, color-mix(in srgb, var(--df-surface-1) 94%, var(--df-interactive) 6%), var(--df-surface-1)); }
.module-detail-section h2 { margin: 0; font-size: 1.15rem; }
.module-detail-section > p { margin: .65rem 0 0; }
.module-identity-card { display: grid; gap: 1.1rem; padding: 1.25rem; border: 1px solid color-mix(in srgb, var(--df-interactive) 36%, var(--df-border-soft)); border-radius: var(--df-radius-lg); background: linear-gradient(135deg, color-mix(in srgb, var(--df-interactive) 12%, var(--df-surface-1)), var(--df-surface-1) 64%); box-shadow: 0 12px 28px color-mix(in srgb, var(--df-canvas) 42%, transparent); }
.module-identity-heading { display: flex; align-items: start; justify-content: space-between; gap: 1rem; }
.module-identity-heading .eyebrow, .module-section-heading .eyebrow { margin: 0 0 .3rem; color: var(--df-interactive-strong); font-size: .7rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.module-identity-description { margin: 0; color: var(--df-text-muted); line-height: 1.55; }
.module-status-badge { display: inline-flex; align-items: center; min-height: 28px; padding: .25rem .65rem; border: 1px solid color-mix(in srgb, var(--df-success) 50%, var(--df-border-soft)); border-radius: 999px; color: var(--df-success); background: color-mix(in srgb, var(--df-success) 10%, var(--df-surface-1)); font-size: .76rem; font-weight: 700; white-space: nowrap; }
.module-status-badge.is-disabled { border-color: color-mix(in srgb, var(--df-warning) 50%, var(--df-border-soft)); color: var(--df-warning); background: color-mix(in srgb, var(--df-warning) 10%, var(--df-surface-1)); }
.module-quick-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .65rem; margin: 0; }
.module-quick-facts > div { min-width: 0; padding: .65rem .75rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-surface-2) 74%, transparent); }
.module-quick-facts dt { color: var(--df-text-muted); font-size: .72rem; }
.module-quick-facts dd { margin: .25rem 0 0; overflow-wrap: anywhere; font-weight: 700; }
.module-identity-notes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.module-identity-notes p { margin: 0; color: var(--df-text-muted); font-size: .82rem; line-height: 1.55; }
.module-identity-notes strong { display: block; margin-bottom: .2rem; color: var(--df-text); font-size: .78rem; }
.module-actions-section { border-color: color-mix(in srgb, var(--df-interactive) 30%, var(--df-border-soft)); }
.module-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: .8rem; }
.module-section-count { display: inline-flex; align-items: center; justify-content: center; min-width: 28px; min-height: 28px; padding-inline: .45rem; border: 1px solid var(--df-border-soft); border-radius: 999px; color: var(--df-text-muted); font-size: .78rem; }
.module-busy-label { color: var(--df-text-muted); font-size: .78rem; }
.module-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.module-actions .content-button { min-width: 7rem; min-height: 42px; }
.module-actions .danger { border-color: color-mix(in srgb, var(--df-danger) 60%, var(--df-border-soft)); color: var(--df-danger); }
.module-status { margin: 1rem 0 0; color: var(--df-success); }
.module-adventure-list { list-style: none; display: grid; gap: .7rem; margin: 1rem 0 0; padding: 0; }
.module-adventure-card { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; padding: .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-surface-2) 75%, transparent); }
.module-adventure-copy { min-width: 0; flex: 1 1 18rem; }
.module-adventure-copy strong { display: block; font-size: .97rem; }
.module-adventure-copy small { display: block; margin-top: .25rem; overflow-wrap: anywhere; color: var(--df-text-muted); }
.module-adventure-copy p { margin: .45rem 0 0; color: var(--df-text-muted); font-size: .86rem; line-height: 1.5; }
.module-adventure-card .content-button { min-width: 11rem; }
.module-content-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr)); gap: .6rem; list-style: none; margin: 1rem 0 0; padding: 0; }
.module-content-list li { display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: .7rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-surface-2) 72%, transparent); }
.module-content-list li span { color: var(--df-text-muted); }
.module-content-list li strong { color: var(--df-interactive-strong); }
.module-technical { padding: 0; overflow: hidden; }
.module-technical summary { cursor: pointer; min-height: 48px; display: flex; align-items: center; padding: .7rem 1.25rem; font-weight: 700; list-style-position: inside; }
.module-technical[open] summary { border-bottom: 1px solid var(--df-border-soft); }
.module-technical > :not(summary) { margin-inline: 1.25rem; }
.module-technical > p:last-child, .module-technical > ul:last-child, .module-technical > .module-facts:last-child { margin-bottom: 1.25rem; }
.module-facts { display: grid; gap: .45rem; margin-top: 1rem; }
.module-facts > div { display: grid; grid-template-columns: minmax(7rem, .35fr) minmax(0, 1fr); gap: .8rem; padding-bottom: .45rem; border-bottom: 1px solid color-mix(in srgb, var(--df-border-soft) 60%, transparent); }
.module-facts dt { color: var(--df-text-muted); font-size: .8rem; }
.module-facts dd { margin: 0; overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .module-identity-heading, .module-section-heading { align-items: stretch; flex-direction: column; }
  .module-status-badge, .module-section-count { align-self: flex-start; }
  .module-quick-facts, .module-identity-notes { grid-template-columns: 1fr; }
  .module-actions { display: grid; grid-template-columns: 1fr; }
  .module-actions .content-button, .module-adventure-card .content-button { width: 100%; }
  .module-detail-section, .module-identity-card { padding: 1rem; }
}
</style>
