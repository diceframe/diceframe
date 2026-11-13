<script setup lang="ts">
// W2/W6 复核（用户拍板）：本页 = 规则编辑 + 玩法模式编辑页，不是开团看板。
// - 「规则编辑」为默认页签：原管理组规则模板编辑器整组件复用（含复制/编辑/删除）。
// - 「玩法模式」页签：只列玩法模式与其接线状态（fail closed），不放能力名牌。
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NIcon, NTag } from 'naive-ui'
import { GameControllerOutline, OptionsOutline } from '@vicons/ionicons5'
import { api, errorMessage } from '@/api/client'
import { useLocale } from '@/composables/useLocale'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import RulesView from '@/features/admin/RulesView.vue'

type GameplaySummary = {
  mode_id: string
  progression_mode: string
  bot_surface: string
  source: 'builtin' | 'provider' | string
  available?: boolean
  provider_registered?: boolean
  turn_driver_bound?: boolean
}

const { t } = useLocale()
const gameplays = ref<GameplaySummary[]>([])
const loading = ref(false)
const error = ref('')
const activeTab = ref<'editor' | 'gameplay'>('editor')

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const gameplayData = await api<{ ok: boolean; gameplays: GameplaySummary[] }>('/gameplays')
    gameplays.value = gameplayData.gameplays || []
  } catch (exc) {
    error.value = errorMessage(exc)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="view rules-gameplay-view">
    <ContentPageHeader class="view-title rules-gameplay-heading" kicker="RULES & GAMEPLAY" :title="t('rulesGameplayTitle')" :description="t('rulesGameplaySubtitle')">
      <template #actions>
        <div class="rules-gameplay-tabs" role="tablist">
          <button type="button" role="tab" :aria-selected="activeTab === 'editor'" :class="{ active: activeTab === 'editor' }" @click="activeTab = 'editor'">
            <NIcon :component="OptionsOutline" :size="16" />{{ t('rulesGameplayTabEditor') }}
          </button>
          <button type="button" role="tab" :aria-selected="activeTab === 'gameplay'" :class="{ active: activeTab === 'gameplay' }" @click="activeTab = 'gameplay'">
            <NIcon :component="GameControllerOutline" :size="16" />{{ t('rulesGameplayTabGameplay') }}
          </button>
        </div>
      </template>
    </ContentPageHeader>

    <section v-if="activeTab === 'editor'" class="rules-gameplay-editor-embed">
      <RulesView />
    </section>

    <section v-else class="rules-gameplay-board">
      <p v-if="loading" class="muted">{{ t('pluginLoading') }}</p>
      <p v-else-if="error" class="error-text">{{ error }}</p>
      <template v-else>
        <h2>{{ t('rulesGameplayGameplaySection') }}</h2>
        <p class="muted">{{ t('rulesGameplayGameplayHint') }}</p>
        <!-- W6 用户反馈：玩法「编辑」= 做玩法工程，把入口接明。 -->
        <div class="rules-gameplay-cta" data-testid="gameplay-create-cta">
          <span>{{ t('rulesGameplayGameplayEditHint') }}</span>
          <RouterLink class="rules-gameplay-cta-link" :to="{ name: 'creator' }">{{ t('rulesGameplayGameplayEditCta') }}</RouterLink>
        </div>
        <div class="rules-gameplay-grid">
          <article v-for="mode in gameplays" :key="mode.mode_id" class="rules-gameplay-card" :data-testid="`gameplay-card-${mode.mode_id}`">
            <div class="rules-card-heading">
              <NIcon :component="GameControllerOutline" :size="18" class="rules-card-icon" aria-hidden="true" />
              <div>
                <h3>{{ mode.mode_id }}</h3>
                <p class="muted rules-runtime-binding">{{ mode.progression_mode }}</p>
              </div>
              <NTag v-if="mode.source === 'builtin'" size="small" type="success">{{ t('rulesGameplayBuiltin') }}</NTag>
              <NTag v-else-if="mode.available" size="small" type="success">{{ t('rulesGameplayWired') }}</NTag>
              <NTag v-else size="small" type="warning">{{ t('rulesGameplayNotWired') }}</NTag>
            </div>
          </article>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.rules-gameplay-view { max-width: 100%; overflow-x: hidden; }
.rules-gameplay-tabs { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .8rem; max-width: 100%; }
.rules-gameplay-tabs button { display: inline-flex; align-items: center; gap: .4rem; padding: .45rem .95rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: transparent; color: var(--df-text-muted); font-weight: 600; cursor: pointer; max-width: 100%; }
.rules-gameplay-tabs button.active { border-color: color-mix(in srgb, var(--df-interactive-strong) 55%, var(--df-border-soft)); color: var(--df-text); background: color-mix(in srgb, var(--df-interactive-strong) 10%, var(--df-surface-1)); }
.rules-gameplay-board { max-width: 100%; margin-top: 1.4rem; }
.rules-gameplay-board h2 { margin: 0 0 .2rem; font-size: 1.05rem; }
.rules-gameplay-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .9rem; margin-top: .8rem; max-width: 100%; }
.rules-gameplay-card { display: grid; gap: .55rem; padding: 1rem 1.1rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-lg); background: var(--df-surface-1); min-width: 0; }
.rules-card-heading { display: flex; align-items: flex-start; gap: .6rem; min-width: 0; }
.rules-card-heading > div { flex: 1; min-width: 0; }
.rules-card-heading h3 { margin: 0; font-size: .98rem; overflow-wrap: anywhere; }
.rules-runtime-binding { margin: .15rem 0 0; font-size: .78rem; font-family: var(--df-font-mono, monospace); overflow-wrap: anywhere; }
.rules-card-icon { display: inline-flex; margin-top: .15rem; color: var(--df-interactive-strong); }
.rules-gameplay-editor-embed { margin-top: 1rem; max-width: 100%; }
.rules-gameplay-cta { display: flex; align-items: center; gap: .8rem; margin-top: .6rem; padding: .55rem .8rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-1); font-size: .84rem; }
.rules-gameplay-cta span { color: var(--df-text-muted); }
.rules-gameplay-cta-link { color: var(--df-interactive-strong); font-weight: 700; text-decoration: none; white-space: nowrap; }
</style>
