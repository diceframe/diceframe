<script setup lang="ts">
// W2「规则与玩法」：把散在管理组/后端注册表里的规则运行时与玩法模式
// 收敛成一个用户可见的板块（拍板 E）。
// - 「规则与玩法」页签：规则能力卡（/api/rules 已带 ruleset_runtime 元数据）
//   与玩法模式接线状态（/api/gameplays，fail closed 如实标注）。
// - 「模板编辑」页签：原管理组规则模板编辑器整组件复用，不复制逻辑。
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NIcon, NTag } from 'naive-ui'
import { GameControllerOutline, OptionsOutline } from '@vicons/ionicons5'
import { api, errorMessage } from '@/api/client'
import type { RuleSummary, RulesResponse } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import RulesView from '@/features/admin/RulesView.vue'

type GameplaySummary = {
  mode_id: string
  progression_mode: string
  bot_surface: string
  provider_registered: boolean
  turn_driver_bound: boolean
}

const { t } = useLocale()
const rules = ref<RuleSummary[]>([])
const gameplays = ref<GameplaySummary[]>([])
const loading = ref(false)
const error = ref('')
const activeTab = ref<'board' | 'editor'>('board')

type RuleCapabilityLabel =
  | 'rulesGameplayCapAuthoritative'
  | 'rulesGameplayCapBuilder'
  | 'rulesGameplayCapSequential'
  | 'rulesGameplayCapComposite'
  | 'rulesGameplayCapCombat'
  | 'rulesGameplayCapVersioned'

function ruleCapabilityChips(rule: RuleSummary): RuleCapabilityLabel[] {
  const capabilities = rule.ruleset_runtime?.capabilities
  if (!capabilities) return []
  const chips: RuleCapabilityLabel[] = []
  if (capabilities.authoritative_intents) chips.push('rulesGameplayCapAuthoritative')
  if (capabilities.character_builder === 'professional') chips.push('rulesGameplayCapBuilder')
  if (capabilities.sequential_checks) chips.push('rulesGameplayCapSequential')
  if (capabilities.composite_action) chips.push('rulesGameplayCapComposite')
  if (capabilities.deterministic_combat) chips.push('rulesGameplayCapCombat')
  if (capabilities.versioned_state) chips.push('rulesGameplayCapVersioned')
  return chips
}

function runtimeBinding(rule: RuleSummary): string {
  const meta = rule.ruleset_runtime
  if (!meta || !meta.id || meta.id === 'legacy') return t('rulesGameplayLegacyRuntime')
  return `${meta.id} · v${meta.version}`
}

function isExternalRuntime(rule: RuleSummary): boolean {
  const id = String(rule.ruleset_runtime?.id || '')
  return Boolean(id) && id !== 'legacy'
}

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [ruleData, gameplayData] = await Promise.all([
      api<RulesResponse>('/rules'),
      api<{ ok: boolean; gameplays: GameplaySummary[] }>('/gameplays'),
    ])
    rules.value = ruleData.rules || []
    gameplays.value = gameplayData.gameplays || []
  } catch (exc) {
    error.value = errorMessage(exc)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="rules-gameplay-view">
    <ContentPageHeader class="view-title rules-gameplay-heading" kicker="RULES & GAMEPLAY" :title="t('rulesGameplayTitle')" :description="t('rulesGameplaySubtitle')">
      <div class="rules-gameplay-tabs" role="tablist">
        <button type="button" role="tab" :aria-selected="activeTab === 'board'" :class="{ active: activeTab === 'board' }" @click="activeTab = 'board'">
          <NIcon :component="OptionsOutline" :size="16" />{{ t('rulesGameplayTabBoard') }}
        </button>
        <button type="button" role="tab" :aria-selected="activeTab === 'editor'" :class="{ active: activeTab === 'editor' }" @click="activeTab = 'editor'">
          <NIcon :component="GameControllerOutline" :size="16" />{{ t('rulesGameplayTabEditor') }}
        </button>
      </div>
    </ContentPageHeader>

    <section v-if="activeTab === 'board'" class="rules-gameplay-board">
      <p v-if="loading" class="muted">{{ t('pluginLoading') }}</p>
      <p v-else-if="error" class="error-text">{{ error }}</p>
      <template v-else>
        <section class="rules-gameplay-section" data-testid="rules-board">
          <h2>{{ t('rulesGameplayRulesSection') }}</h2>
          <p class="muted">{{ t('rulesGameplayRulesHint') }}</p>
          <div class="rules-gameplay-grid">
            <article v-for="rule in rules" :key="rule.rule_id" class="rules-gameplay-card" :data-testid="`rules-card-${rule.rule_id}`">
              <div class="rules-card-heading">
                <NIcon :component="isExternalRuntime(rule) ? OptionsOutline : OptionsOutline" :size="18" class="rules-card-icon" aria-hidden="true" />
                <div>
                  <h3>{{ rule.rule_name || rule.rule_id }}</h3>
                  <p class="muted rules-runtime-binding">{{ runtimeBinding(rule) }}</p>
                </div>
                <NTag v-if="rule.readonly" size="small" type="info">{{ t('rulesGameplayFromPlugin') }}</NTag>
              </div>
              <p class="muted rules-card-desc">{{ rule.description || t('noDescription') }}</p>
              <p class="rules-card-chips">
                <NTag v-for="labelKey in ruleCapabilityChips(rule)" :key="labelKey" size="small" round type="info">{{ t(labelKey) }}</NTag>
                <NTag v-if="!ruleCapabilityChips(rule).length" size="small" class="module-language-undeclared">{{ t('rulesGameplayNoCapability') }}</NTag>
              </p>
              <div class="rules-card-actions">
                <RouterLink class="rules-card-action" :to="{ name: 'characters', query: { rule: rule.rule_id } }">{{ t('rulesGameplayBuildCharacter') }}</RouterLink>
                <RouterLink class="rules-card-action" :to="{ name: 'create' }">{{ t('rulesGameplayStartGame') }}</RouterLink>
              </div>
            </article>
          </div>
        </section>

        <section class="rules-gameplay-section" data-testid="gameplay-board">
          <h2>{{ t('rulesGameplayGameplaySection') }}</h2>
          <p class="muted">{{ t('rulesGameplayGameplayHint') }}</p>
          <div class="rules-gameplay-grid">
            <article v-for="mode in gameplays" :key="mode.mode_id" class="rules-gameplay-card" :data-testid="`gameplay-card-${mode.mode_id}`">
              <div class="rules-card-heading">
                <NIcon :component="GameControllerOutline" :size="18" class="rules-card-icon" aria-hidden="true" />
                <div>
                  <h3>{{ mode.mode_id }}</h3>
                  <p class="muted rules-runtime-binding">{{ mode.progression_mode }}</p>
                </div>
                <NTag v-if="mode.provider_registered && mode.turn_driver_bound" size="small" type="success">{{ t('rulesGameplayWired') }}</NTag>
                <NTag v-else size="small" type="warning">{{ t('rulesGameplayNotWired') }}</NTag>
              </div>
            </article>
          </div>
        </section>
      </template>
    </section>

    <section v-else class="rules-gameplay-editor-embed">
      <RulesView />
    </section>
  </div>
</template>

<style scoped>
.rules-gameplay-tabs { display: flex; gap: .5rem; margin-top: .8rem; }
.rules-gameplay-tabs button { display: inline-flex; align-items: center; gap: .4rem; padding: .45rem .95rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font-weight: 600; cursor: pointer; }
.rules-gameplay-tabs button.active { border-color: color-mix(in srgb, var(--df-interactive-strong) 55%, var(--df-border-soft)); color: var(--df-text); background: color-mix(in srgb, var(--df-interactive-strong) 10%, var(--df-surface-1)); }
.rules-gameplay-section { margin-top: 1.4rem; }
.rules-gameplay-section h2 { margin: 0 0 .2rem; font-size: 1.05rem; }
.rules-gameplay-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: .9rem; margin-top: .8rem; }
.rules-gameplay-card { display: grid; gap: .55rem; padding: 1rem 1.1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); }
.rules-card-heading { display: flex; align-items: flex-start; gap: .6rem; }
.rules-card-heading > div { flex: 1; min-width: 0; }
.rules-card-heading h3 { margin: 0; font-size: .98rem; }
.rules-runtime-binding { margin: .15rem 0 0; font-size: .78rem; font-family: var(--df-font-mono, monospace); }
.rules-card-icon { display: inline-flex; margin-top: .15rem; color: var(--df-interactive-strong); }
.rules-card-desc { margin: 0; font-size: .85rem; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.rules-card-chips { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0; }
.rules-card-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.rules-card-action { padding: .3rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); color: var(--df-text-muted); font-size: .82rem; text-decoration: none; }
.rules-card-action:hover { color: var(--df-text); border-color: color-mix(in srgb, var(--df-interactive-strong) 50%, var(--df-border-soft)); }
.rules-gameplay-editor-embed { margin-top: 1rem; }
</style>
