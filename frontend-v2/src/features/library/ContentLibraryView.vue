<script setup lang="ts">
import { narrativeRuntimeId, adventureRequirementLabel } from '@/features/adventures/compatibility'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NIcon } from 'naive-ui'
import { ArrowForwardOutline, BookOutline, CompassOutline, ExtensionPuzzleOutline, MapOutline, SearchOutline } from '@vicons/ionicons5'
import { api, errorMessage } from '@/api/client'
import type { AdventureSummary, AdventuresResponse, RuleSummary, RulesResponse, WorldListResponse, WorldSummary, WorldTemplateSummary, WorldTemplatesResponse } from '@/api/types'
import { moduleApi, type ModuleSummary } from '@/api/modules'
import { useLocale } from '@/composables/useLocale'
import type { Locale } from '@/i18n'
import { CONTENT_LANGUAGE_OPTIONS, contentLanguageOf } from '@/utils/contentLanguage'
import Modal from '@/components/ui/Modal.vue'
import { adventureKey } from '@/features/create/adventureSelection'
import { adventureLaunchTarget, matchingAdventureRules } from './catalogue'
import { contentHubCopy } from './hubCopy'
import { lorebookCard, type LibraryLorebook } from './lorebookCards'

type ContentFilter = 'all' | 'adventure' | 'world' | 'lorebook' | 'source'
type ContentLanguageFilter = 'all' | Locale

const { locale, t } = useLocale()
const copy = computed(() => contentHubCopy(locale.value))
const adventures = ref<AdventureSummary[]>([])
const worlds = ref<WorldSummary[]>([])
const lorebooks = ref<LibraryLorebook[]>([])
const modules = ref<ModuleSummary[]>([])
const rules = ref<RuleSummary[]>([])
const loading = ref(false)
const error = ref('')
const search = ref('')
const contentFilter = ref<ContentFilter>('all')
const languageFilter = ref<ContentLanguageFilter>(locale.value)
const sourceFilter = ref('all')
const runtimeFilter = ref('all')
const detail = ref<AdventureSummary | null>(null)
let sequence = 0

async function load() {
  const current = ++sequence
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const results = await Promise.allSettled([
      api<AdventuresResponse>(`/adventures?language=${encodeURIComponent(locale.value)}`),
      api<WorldListResponse>('/worlds'),
      api<WorldTemplatesResponse>(`/world-templates?language=${encodeURIComponent(locale.value)}`),
      moduleApi.list(),
      api<RulesResponse>(`/rules?language=${encodeURIComponent(locale.value)}`),
      api<{ books?: typeof lorebooks.value }>('/lorebooks?scope=all'),
    ])
    if (current !== sequence) return
    const adventureResult = results[0]
    if (adventureResult.status === 'fulfilled' && adventureResult.value.ok) adventures.value = adventureResult.value.adventures || []
    else {
      adventures.value = []
      error.value = adventureResult.status === 'fulfilled' ? adventureResult.value.error || copy.value.unavailable : errorMessage(adventureResult.reason)
    }
    const worldResult = results[1]
    const templateResult = results[2]
    const templateByWorld = new Map<string, WorldTemplateSummary>()
    if (templateResult.status === 'fulfilled') {
      for (const template of templateResult.value.templates || []) {
        const id = String(template.world_id || template.id || '')
        if (id) templateByWorld.set(id, template)
      }
    }
    const worldItems = worldResult.status === 'fulfilled' ? worldResult.value.worlds || [] : []
    worlds.value = worldItems.map(world => {
      const id = worldId(world)
      const template = templateByWorld.get(id)
      return template ? {
        ...world,
        scene_image: world.scene_image || template.scene_image,
        scene_images: world.scene_image ? world.scene_images : (world.scene_images?.length ? world.scene_images : template.scene_images),
        default_rule: template.default_rule,
        source: template.source,
      } : world
    })
    const moduleResult = results[3]
    modules.value = moduleResult.status === 'fulfilled' && moduleResult.value.ok ? moduleResult.value.modules || [] : []
    const ruleResult = results[4]
    rules.value = ruleResult.status === 'fulfilled' ? ruleResult.value.rules || [] : []
    const loreResult = results[5]
    lorebooks.value = loreResult.status === 'fulfilled' ? loreResult.value.books || [] : []
    if (results.every(result => result.status === 'rejected')) throw new Error(copy.value.unavailable)
  } catch (cause: unknown) {
    if (current === sequence) error.value = errorMessage(cause)
  } finally {
    if (current === sequence) loading.value = false
  }
}

watch(locale, () => { clearFilters(); void load() }, { immediate: true })
onScopeDispose(() => { ++sequence })

function worldId(item: WorldSummary): string { return String(item.id || item.world_id || '') }
function worldName(item: WorldSummary): string { return String(item.name || item.world_name || item.id || item.world_id || '') }
function worldCanEdit(item: WorldSummary): boolean {
  const source = String((item as WorldSummary & { source?: string }).source || '')
  return !source || source === 'user'
}
function lorebookWorldId(book: typeof lorebooks.value[number]): string {
  if (book.ref?.kind === 'lorebook' && book.ref.source_kind === 'world' && book.ref.source_id) return String(book.ref.source_id)
  const binding = (book.bindings || []).find(item => item.scope_kind === 'world' && item.scope_id)
  return String(binding?.scope_id || '')
}
function lorebookOpenTarget(book: typeof lorebooks.value[number]) {
  const candidateWorld = lorebookWorldId(book)
  // A book's provenance may outlive a deleted world. Carry a world query only
  // when that canonical world is present in the current directory; the book
  // route remains the authoritative target for orphaned books.
  const world = candidateWorld && worlds.value.some(item => worldId(item) === candidateWorld) ? candidateWorld : ''
  return { name: 'lorebook', query: { ...(world ? { world } : {}), book: book.id } }
}
function sourceKind(item: AdventureSummary): string { return item.source_kind || (item.custom ? 'user' : 'builtin') }
function sourceLabel(item: AdventureSummary): string {
  return sourceKind(item) === 'plugin' ? copy.value.contentPack : sourceKind(item) === 'user' ? copy.value.user : copy.value.builtIn
}
function ruleLabel(item: AdventureSummary): string {
  if (item.required_runtime?.id === narrativeRuntimeId) return adventureRequirementLabel(item, locale.value)
  const rule = matchingAdventureRules(item, rules.value)[0]
  return rule?.rule_name || rule?.rule_id || item.required_runtime?.id || copy.value.unknownRuntime
}
function worldLabel(item: AdventureSummary): string {
  return item.world_policy === 'fixed' ? copy.value.fixedWorld : item.world_policy === 'portable' ? copy.value.portableWorld : copy.value.agnosticWorld
}
function incompatibilityHint(item: AdventureSummary): string {
  const reasons = new Set(item.incompatibility_reasons || [])
  if (reasons.has('runtime_id_mismatch') || reasons.has('runtime_version_too_old') || reasons.has('adventure_format_unsupported') || reasons.has('ruleset_runtime_unavailable')) {
    return `${item.required_runtime?.id || copy.value.ruleset} · ${item.format || 'adventure format'}`
  }
  if (reasons.has('world_mismatch')) return item.recommended_world_id ? `${copy.value.worldPolicy}: ${item.recommended_world_id}` : copy.value.chooseWorld
  return copy.value.openCreateHint
}
function launchTarget(item: AdventureSummary) {
  return matchingAdventureRules(item, rules.value).length ? adventureLaunchTarget(item) : null
}
function clearFilters() { search.value = ''; contentFilter.value = 'all'; languageFilter.value = locale.value; sourceFilter.value = 'all'; runtimeFilter.value = 'all' }
const query = computed(() => search.value.trim().toLocaleLowerCase(locale.value))
function matches(text: string): boolean { return !query.value || text.toLocaleLowerCase(locale.value).includes(query.value) }
function languageMatches(item: { language?: unknown }): boolean {
  return languageFilter.value === 'all' || contentLanguageOf(item) === languageFilter.value
}
const filteredAdventures = computed(() => adventures.value.filter(item => {
  const source = sourceKind(item)
  return (sourceFilter.value === 'all' || source === sourceFilter.value)
    && (runtimeFilter.value === 'all' || item.required_runtime?.id === runtimeFilter.value)
    && matches([item.name, item.summary, item.source_id || '', ruleLabel(item)].join(' '))
}))
const filteredWorlds = computed(() => worlds.value.filter(item => languageMatches(item) && matches([worldName(item), item.description || ''].join(' '))))
const filteredLorebooks = computed(() => lorebooks.value.filter(item => languageMatches(item) && matches([item.name || item.id, item.description || '', item.id, ...lorebookCard(item, worlds.value, copy.value).worldNames].join(' '))))
const lorebookCards = computed(() => visibleLorebooks.value.slice(0, 6).map(book => ({ book, ...lorebookCard(book, worlds.value, copy.value) })))
const filteredModules = computed(() => modules.value.filter(item => matches([item.name, item.id, item.content_profile].join(' '))))
const runtimeOptions = computed(() => [...new Map(adventures.value.filter(item => item.required_runtime?.id)
  .map(item => [item.required_runtime!.id, ruleLabel(item)])).entries()])
const hasFilters = computed(() => Boolean(query.value || contentFilter.value !== 'all' || languageFilter.value !== 'all' || sourceFilter.value !== 'all' || runtimeFilter.value !== 'all'))
const visibleAdventures = computed(() => contentFilter.value === 'world' || contentFilter.value === 'lorebook' || contentFilter.value === 'source' ? [] : filteredAdventures.value)
const visibleWorlds = computed(() => contentFilter.value === 'adventure' || contentFilter.value === 'lorebook' || contentFilter.value === 'source' ? [] : filteredWorlds.value)
const visibleLorebooks = computed(() => contentFilter.value === 'adventure' || contentFilter.value === 'world' || contentFilter.value === 'source' ? [] : filteredLorebooks.value)
const visibleModules = computed(() => contentFilter.value === 'adventure' || contentFilter.value === 'world' || contentFilter.value === 'lorebook' ? [] : filteredModules.value)

function moduleStatus(item: ModuleSummary): string {
  if (item.inventory_status === 'unknown') return copy.value.unknown
  if (item.status === 'disabled') return copy.value.disabled
  return copy.value.installed
}
function moduleCount(item: ModuleSummary): number {
  const adventureCount = item.adventure_count || 0
  const resourceCount = item.content_counts ? Object.values(item.content_counts).reduce((sum, count) => sum + count, 0) : 0
  return adventureCount + resourceCount
}

</script>

<template>
  <section class="view content-library content-hub" data-testid="content-library" :aria-busy="loading">
    <header class="library-heading">
      <div><span class="section-kicker">{{ copy.kicker }}</span><h1>{{ copy.title }}</h1><p>{{ copy.intro }}</p></div>
      <div class="library-heading-actions">
        <RouterLink class="content-button content-button-primary" :to="{ name: 'create', query: { return_to: 'library' } }">{{ copy.startFree }}</RouterLink>
        <RouterLink class="content-button" :to="{ name: 'creator' }">{{ copy.openStudio }}</RouterLink>
      </div>
    </header>

    <nav class="library-creation-tools" :aria-label="copy.openStudio">
      <RouterLink :to="{ name: 'adventures' }"><NIcon :component="MapOutline" />{{ copy.adventureWorkbench }}</RouterLink>
      <RouterLink :to="{ name: 'adventures', query: { intent: 'script-import' } }"><NIcon :component="BookOutline" />{{ copy.importScript }}</RouterLink>
      <RouterLink :to="{ name: 'modules' }"><NIcon :component="ExtensionPuzzleOutline" />{{ copy.manageSources }}</RouterLink>
      <RouterLink :to="{ name: 'worlds' }"><NIcon :component="CompassOutline" />{{ copy.worldsSection }}</RouterLink>
    </nav>

    <form class="library-filters" @submit.prevent>
      <label class="library-search"><NIcon :component="SearchOutline" /><input v-model="search" type="search" :aria-label="copy.title" :placeholder="copy.searchPlaceholder" data-testid="library-search"></label>
      <label class="sr-only" for="library-content-filter">{{ copy.title }}</label><select id="library-content-filter" v-model="contentFilter" aria-label="Content type" data-testid="library-content-filter"><option value="all">{{ copy.contentAll }}</option><option value="adventure">{{ copy.adventuresFilter }}</option><option value="world">{{ copy.worldsFilter }}</option><option value="lorebook">{{ copy.lorebooksFilter }}</option><option value="source">{{ copy.sourcesFilter }}</option></select>
      <select v-model="languageFilter" :aria-label="t('contentLanguage')" data-testid="library-language-filter"><option value="all">{{ copy.contentLanguageAll }}</option><option v-for="option in CONTENT_LANGUAGE_OPTIONS" :key="option.value" :value="option.value">{{ t(option.labelKey) }}</option></select>
      <select v-model="runtimeFilter" :aria-label="copy.adventuresSection" data-testid="library-rule-filter"><option value="all">{{ copy.allRulesets }}</option><option v-for="[id, label] in runtimeOptions" :key="id" :value="id">{{ label }}</option></select>
      <select v-model="sourceFilter" :aria-label="copy.adventuresSection" data-testid="library-source-filter"><option value="all">{{ copy.allAdventureSources }}</option><option value="builtin">{{ copy.builtIn }}</option><option value="user">{{ copy.user }}</option><option value="plugin">{{ copy.contentPack }}</option></select>
      <button type="button" class="content-button library-refresh" :disabled="loading" @click="load">{{ copy.refresh }}</button>
    </form>

    <div v-if="error" class="content-state content-state-error" role="alert"><p>{{ error }}</p><button type="button" class="content-button" data-testid="library-retry" @click="load">{{ copy.refresh }}</button></div>
    <div v-else-if="loading" class="content-state" role="status"><p>{{ copy.loading }}</p></div>
    <div v-else-if="!visibleAdventures.length && !visibleWorlds.length && !visibleLorebooks.length && !visibleModules.length" class="content-state" data-testid="library-empty"><NIcon :component="BookOutline" /><h2>{{ hasFilters ? copy.noMatches : copy.empty }}</h2><button v-if="hasFilters" type="button" class="content-button" data-testid="library-clear" @click="clearFilters">{{ copy.clearFilters }}</button><RouterLink v-else class="content-button" :to="{ name: 'creator' }">{{ copy.openStudio }}</RouterLink></div>

    <template v-if="!loading && (Boolean(error) || visibleAdventures.length || visibleWorlds.length || visibleLorebooks.length || visibleModules.length)">
      <section v-if="visibleWorlds.length || contentFilter === 'world'" class="hub-section" data-testid="library-worlds">
        <header class="hub-section-heading"><div><h2>{{ copy.worldsSection }}</h2><p>{{ copy.worldsHint }}</p></div><RouterLink class="content-button" :to="{ name: 'worlds' }">{{ copy.open }}</RouterLink></header>
        <div v-if="!visibleWorlds.length" class="hub-empty">{{ copy.noWorlds }}</div>
        <div v-else class="hub-card-grid" data-testid="library-world-grid"><article v-for="world in visibleWorlds.slice(0, 6)" :key="worldId(world)" class="hub-card hub-world-card" data-testid="library-world"><div class="hub-card-content"><span class="hub-card-icon"><NIcon :component="CompassOutline" /></span><div><h3>{{ worldName(world) }}</h3><p>{{ world.description || copy.worldsHint }}</p><small>{{ world.entry_count || 0 }} {{ locale.startsWith('zh') ? '条资料' : 'entries' }}</small></div></div><div class="hub-card-actions"><RouterLink class="content-button content-button-primary" :to="{ name: 'create', query: { world: worldId(world), return_to: 'library' } }">{{ copy.useWorld }}</RouterLink><RouterLink class="content-button" :to="{ name: 'lorebook', query: { world: worldId(world) } }">{{ copy.manageBooks }}</RouterLink><RouterLink v-if="worldCanEdit(world)" class="content-button" :to="{ name: 'worlds', query: { world: worldId(world), edit: '1' } }">{{ copy.editWorld }}</RouterLink></div></article></div>
      </section>

      <section v-if="visibleLorebooks.length || contentFilter === 'lorebook'" class="hub-section" data-testid="library-lorebooks">
        <header class="hub-section-heading"><div><h2>{{ copy.lorebooksSection }}</h2><p>{{ copy.lorebooksHint }}</p></div><RouterLink class="content-button" :to="{ name: 'lorebook' }">{{ copy.open }}</RouterLink></header>
        <div v-if="!visibleLorebooks.length" class="hub-empty">{{ copy.noLorebooks }}</div>
        <div v-else class="hub-card-grid"><article v-for="card in lorebookCards" :key="card.book.id" class="hub-card lorebook-card" data-testid="library-lorebook"><span class="hub-card-icon"><NIcon :component="BookOutline" /></span><div><span class="library-book-kind">{{ card.kind }}</span><h3>{{ card.title }}</h3><small v-if="card.worldNames.length" class="library-book-worlds">{{ copy.boundWorlds }}{{ card.worldNames.join(' · ') }}</small><p>{{ card.book.description || copy.lorebooksHint }}</p><small>{{ card.book.entry_count || 0 }} {{ locale.startsWith('zh') ? '条资料' : 'entries' }}</small></div><div class="hub-card-actions"><RouterLink class="content-button content-button-primary" :to="lorebookOpenTarget(card.book)" :data-book-id="card.book.id">{{ copy.open }}</RouterLink></div></article></div>
      </section>

      <section v-if="visibleAdventures.length || contentFilter === 'adventure'" class="hub-section" data-testid="library-adventures">
        <header class="hub-section-heading"><div><h2>{{ copy.adventuresSection }}</h2><p>{{ copy.adventuresHint }}</p></div><RouterLink class="content-button" :to="{ name: 'adventures' }">{{ copy.manageAdventures }}</RouterLink></header>
        <div v-if="!visibleAdventures.length" class="hub-empty">{{ copy.noAdventures }}</div>
        <div v-else class="library-grid"><article v-for="item in visibleAdventures.slice(0, 6)" :key="adventureKey(item)" class="library-adventure-card" data-testid="library-adventure"><div class="library-card-cover"><img :src="'/v2-assets/ui/campaign-moonlit-ruins.jpg'" alt=""><div class="library-card-top"><span class="library-card-emblem" aria-hidden="true"><NIcon :component="BookOutline" /></span><span class="content-badge">{{ sourceLabel(item) }}</span></div></div><div class="library-card-story"><h2>{{ item.name }}</h2><p>{{ item.summary || copy.adventuresHint }}</p></div><dl class="library-card-facts"><div><dt>{{ copy.ruleset }}</dt><dd>{{ ruleLabel(item) }}</dd></div><div><dt>{{ copy.worldPolicy }}</dt><dd>{{ worldLabel(item) }}</dd></div></dl><div class="library-card-footer"><span>{{ copy.version }} {{ item.version }}</span><span v-if="sourceKind(item) === 'plugin'" class="library-package-id">{{ item.source_id }}</span></div><div class="library-card-actions"><RouterLink v-if="launchTarget(item)" class="content-button content-button-primary" :to="launchTarget(item)!" data-testid="library-start">{{ copy.startAdventure }} <NIcon :component="ArrowForwardOutline" /></RouterLink><span v-else class="library-unavailable">{{ copy.notPlayable }}<small>{{ incompatibilityHint(item) }}</small></span><button type="button" class="content-button" data-testid="library-details" @click="detail = item">{{ copy.open }}</button></div></article></div>
      </section>

      <section v-if="visibleModules.length || contentFilter === 'source'" class="hub-section" data-testid="library-sources">
        <header class="hub-section-heading"><div><h2>{{ copy.sourcesSection }}</h2><p>{{ copy.sourcesHint }}</p></div></header>
        <div v-if="!visibleModules.length" class="hub-empty">{{ copy.noSources }}</div>
        <div v-else class="hub-card-grid"><article v-for="item in visibleModules.slice(0, 6)" :key="item.id" class="hub-card source-card" data-testid="library-source"><span class="hub-card-icon"><NIcon :component="ExtensionPuzzleOutline" /></span><div><h3>{{ item.name }}</h3><p>{{ item.content_profile === 'adventure-module' ? copy.adventureModule : copy.contentPack }}</p><small>{{ moduleCount(item) }} items · {{ moduleStatus(item) }}</small></div><div class="hub-card-actions"><RouterLink class="content-button content-button-primary" :to="{ name: 'module-detail', params: { moduleId: item.id } }">{{ copy.open }}</RouterLink></div></article></div>
      </section>
    </template>

    <Modal v-if="detail" :title="detail.name" dialog-class="library-detail-dialog" @close="detail = null"><p class="library-detail-summary">{{ detail.summary || copy.adventuresHint }}</p><dl class="library-detail-facts"><div><dt>{{ copy.ruleset }}</dt><dd>{{ ruleLabel(detail) }} <small>{{ detail.required_runtime?.id }} · v{{ detail.required_runtime?.minimum_version }}</small></dd></div><div><dt>{{ copy.source }}</dt><dd>{{ sourceLabel(detail) }} <small v-if="detail.source_id">{{ detail.source_id }}</small></dd></div><div><dt>{{ copy.version }}</dt><dd>{{ detail.version }}</dd></div><div><dt>{{ copy.worldPolicy }}</dt><dd>{{ worldLabel(detail) }} <small v-if="detail.recommended_world_id">{{ detail.recommended_world_id }}</small></dd></div></dl><p class="muted">{{ launchTarget(detail) ? copy.adventuresHint : incompatibilityHint(detail) }}</p><template #actions><RouterLink v-if="launchTarget(detail)" class="content-button content-button-primary" :to="launchTarget(detail)!">{{ copy.startAdventure }}</RouterLink><RouterLink v-if="detail.source_kind === 'plugin' && detail.source_id" class="content-button" :to="{ name: 'module-detail', params: { moduleId: detail.source_id } }">{{ copy.manageSources }}</RouterLink></template></Modal>
  </section>
</template>

<style scoped>
.content-hub { display: grid; gap: 1.25rem; }
.hub-section { display: grid; gap: .8rem; }
.hub-section-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }
.hub-section-heading h2 { margin: 0; font-size: 1.2rem; }
.hub-section-heading p { margin: .25rem 0 0; color: var(--df-text-muted); font-size: .88rem; }
.hub-card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: .75rem; }
.hub-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .75rem; align-items: start; min-height: 220px; padding: 1rem; border: 1px solid var(--df-border-soft); border-radius: 14px; background: var(--df-surface-raised); box-shadow: var(--df-shadow); }
.hub-card-icon { display: grid; place-items: center; width: 2.25rem; height: 2.25rem; border-radius: 10px; color: var(--df-accent-strong); background: color-mix(in srgb, var(--df-accent) 14%, transparent); }
.hub-card h3 { margin: 0; font-size: 1rem; }
.hub-card p { margin: .35rem 0; color: var(--df-text-muted); line-height: 1.45; }
.hub-card small { color: var(--df-text-muted); }
.hub-card-actions { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: stretch; gap: .5rem; margin-top: auto; min-width: 0; }
.hub-card-actions .content-button { width: 100%; min-width: 0; min-height: 40px; box-sizing: border-box; justify-content: center; text-align: center; white-space: normal; overflow-wrap: anywhere; line-height: 1.3; }
.hub-card-actions .content-button:only-child { grid-column: 1 / -1; }
.hub-world-card .hub-card-actions .content-button:first-child:nth-last-child(3) { grid-column: 1 / -1; }
.hub-section[data-testid="library-worlds"] .hub-card-grid { grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: .75rem; }
/* Worlds and lorebooks share the same compact knowledge-card rhythm. */
.hub-world-card { display: grid; grid-template-columns: auto minmax(0, 1fr); min-height: 220px; padding: 1rem; overflow: visible; }
.hub-world-card .hub-card-cover { display: none; }
.hub-world-card .hub-card-content { display: contents; }
.hub-world-card .hub-card-content > div { min-width: 0; }
.hub-world-card .hub-card-content h3 { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.hub-world-card .hub-card-content p { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.hub-world-card .hub-card-actions { margin-top: auto; margin-inline: 0; margin-bottom: 0; gap: .4rem; }
.lorebook-card { min-width: 0; overflow: hidden; }
.lorebook-card > div:not(.hub-card-actions) { min-width: 0; }
.lorebook-card h3 { display: -webkit-box; min-width: 0; overflow: hidden; overflow-wrap: anywhere; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.lorebook-card .library-book-worlds { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lorebook-card p { display: -webkit-box; min-width: 0; overflow: hidden; overflow-wrap: anywhere; -webkit-line-clamp: 4; -webkit-box-orient: vertical; }
.library-unavailable small { display: block; margin-top: .25rem; color: var(--df-text-muted); font-size: .72rem; }
.hub-empty { padding: 1rem; border: 1px dashed var(--df-border-soft); border-radius: 12px; color: var(--df-text-muted); }
@media (max-width: 720px) { .hub-section-heading { align-items: start; flex-direction: column; } .hub-section-heading .content-button { align-self: stretch; justify-content: center; } }
</style>
