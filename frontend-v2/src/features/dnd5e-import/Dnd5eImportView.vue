<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ApiError, api, errorMessage, hasAccessToken } from '@/api/client'
import type { GamesResponse, GameSummary, JsonObject, RulesetBuilderChoices } from '@/api/types'
import {
  commitDnd5eCharacter,
  previewDnd5eCharacter,
  type Dnd5eCommitResponse,
  type Dnd5ePreviewResponse,
  type Dnd5eSelections,
} from '@/api/dnd5eImport'
import { fetchRulesetBuilderChoices } from '@/api/rulesets'
import { readCurrentGame, rememberCurrentGame, queryString } from '@/stores/gameContext'
import { useLocale } from '@/composables/useLocale'
import { dnd5eImportCopy } from './copy'
import ConversionDisplay from './ConversionDisplay.vue'
import Dnd5eReportPanel from './Dnd5eReportPanel.vue'
import SelectionsForm from './SelectionsForm.vue'

const route = useRoute(), router = useRouter()
const { locale } = useLocale()
const copy = computed(() => dnd5eImportCopy(locale.value))

const games = ref<GameSummary[]>([])
const gameKey = ref('')
const loadingGames = ref(false)
const payload = ref<JsonObject | null>(null)
const fileName = ref('')
const preview = ref<Dnd5ePreviewResponse | null>(null)
const choices = ref<RulesetBuilderChoices | null>(null)
const choicesError = ref('')
const selections = ref<Dnd5eSelections>({})
const result = ref<Dnd5eCommitResponse | null>(null)
const busy = ref<'idle' | 'preview' | 'commit'>('idle')
const error = ref('')
const dirty = ref(false)
const commitStale = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
let choiceSequence = 0

const selectedGame = computed(() => games.value.find(game => game.game_key === gameKey.value) || null)
const ruleId = computed(() => String(selectedGame.value?.rule_id || ''))

// Same identity rule as the tactical scene view: the owner session is GM unless
// a player preview is named. Every write is still enforced server-side.
const routeUser = computed(() => queryString(route.query.user))
const isGm = computed(() => {
  const game = selectedGame.value
  if (!game) return false
  const gmUid = game.gm_uid || ''
  return (!routeUser.value && hasAccessToken()) || (!!routeUser.value && routeUser.value === gmUid)
})

const levelBlocked = computed(() => (
  preview.value?.missing.some(entry => entry.field === 'progression.history') ?? false
))
const needsRepreview = computed(() => !!preview.value && !result.value && (dirty.value || commitStale.value))
const canCommit = computed(() => (
  !!preview.value?.valid && !needsRepreview.value && busy.value === 'idle' && isGm.value && !result.value
))

async function loadGames() {
  loadingGames.value = true
  error.value = ''
  try {
    const response = await api<GamesResponse>('/games')
    games.value = response.games || []
    const requested = queryString(route.query.game) || readCurrentGame()
    gameKey.value = games.value.some(game => game.game_key === requested)
      ? requested
      : games.value[0]?.game_key || ''
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    loadingGames.value = false
  }
}

function selectGame(value: string) {
  if (!games.value.some(game => game.game_key === value)) return
  gameKey.value = value
  const game = games.value.find(item => item.game_key === value)
  rememberCurrentGame(value, game?.world_name || '')
  router.replace({ query: { ...route.query, game: value } })
}

function resetPreviewState() {
  preview.value = null
  choices.value = null
  choicesError.value = ''
  selections.value = {}
  result.value = null
  dirty.value = false
  commitStale.value = false
}

watch(gameKey, () => { resetPreviewState(); error.value = '' })
watch(() => queryString(route.query.game), (value) => {
  if (value && value !== gameKey.value && games.value.some(game => game.game_key === value)) gameKey.value = value
})

async function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value !== 'idle') return
  error.value = ''
  resetPreviewState()
  payload.value = null
  fileName.value = file.name
  let parsed: unknown
  try {
    parsed = JSON.parse(await file.text())
  } catch {
    error.value = copy.value.invalidJson
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    error.value = copy.value.invalidJson
    return
  }
  payload.value = parsed as JsonObject
}

async function loadChoices(draft: JsonObject) {
  const current = ++choiceSequence
  choicesError.value = ''
  if (!ruleId.value) {
    choices.value = null
    choicesError.value = copy.value.choicesLoadError
    return
  }
  try {
    const response = await fetchRulesetBuilderChoices(ruleId.value, draft, locale.value)
    if (current === choiceSequence) choices.value = response.choices
  } catch (cause) {
    if (current === choiceSequence) {
      choices.value = null
      choicesError.value = errorMessage(cause)
    }
  }
}

async function runPreview() {
  if (!payload.value || busy.value !== 'idle' || !gameKey.value || !isGm.value) return
  busy.value = 'preview'
  error.value = ''
  commitStale.value = false
  try {
    const response = await previewDnd5eCharacter(gameKey.value, payload.value, selections.value, locale.value)
    preview.value = response
    result.value = null
    dirty.value = false
    await loadChoices(response.draft)
  } catch (cause) {
    preview.value = null
    choices.value = null
    error.value = errorMessage(cause)
  } finally {
    busy.value = 'idle'
  }
}

function onSelectionsChange(next: Dnd5eSelections) {
  selections.value = next
  if (preview.value) dirty.value = true
}

async function commit() {
  if (!canCommit.value || !payload.value) return
  busy.value = 'commit'
  error.value = ''
  try {
    result.value = await commitDnd5eCharacter(gameKey.value, payload.value, selections.value, locale.value)
    dirty.value = false
    commitStale.value = false
  } catch (cause) {
    if (cause instanceof ApiError && (cause.code === 'dnd5e_creation_incomplete' || cause.code === 'dnd5e_level_requires_progression')) {
      commitStale.value = true
    }
    error.value = errorMessage(cause)
  } finally {
    busy.value = 'idle'
  }
}

function startOver() {
  if (busy.value !== 'idle') return
  payload.value = null
  fileName.value = ''
  error.value = ''
  resetPreviewState()
  if (fileInput.value) fileInput.value.value = ''
}

onMounted(loadGames)
</script>

<template>
  <main class="dnd5e-import-page" data-testid="dnd5e-import-page">
    <header class="dnd5e-import-head">
      <div class="dnd5e-import-nav">
        <RouterLink data-testid="dnd5e-back-characters" :to="{ path: '/characters', query: gameKey ? { game: gameKey, pane: 'current' } : undefined }">{{ copy.backCharacters }}</RouterLink>
        <RouterLink v-if="gameKey" data-testid="dnd5e-back-play" :to="{ path: '/play', query: { game: gameKey } }">{{ copy.openPlay }}</RouterLink>
      </div>
      <h1>{{ copy.title }}</h1>
      <p>{{ copy.hint }}</p>
      <p class="dnd5e-import-limits">{{ copy.gmOnly }} {{ copy.rulesetHint }}</p>
    </header>

    <p v-if="loadingGames" role="status">{{ copy.loadingGames }}</p>
    <p v-else-if="!games.length" class="dnd5e-import-empty" data-testid="dnd5e-import-no-games">{{ copy.noGames }}</p>

    <template v-else>
      <label class="dnd5e-import-field">
        {{ copy.chooseGame }}
        <select v-model="gameKey" data-testid="dnd5e-import-game" :disabled="busy !== 'idle'" @change="selectGame(gameKey)">
          <option v-for="game in games" :key="game.game_key" :value="game.game_key">
            {{ game.world_name || game.game_key }} ({{ game.rule_id }})
          </option>
        </select>
      </label>

      <p v-if="!isGm" class="dnd5e-import-banner" role="note" data-testid="dnd5e-import-gm-only">{{ copy.gmOnly }}</p>

      <template v-if="!result">
        <label class="dnd5e-import-field">
          {{ copy.chooseFile }}
          <input
            ref="fileInput"
            type="file"
            accept=".json,application/json"
            data-testid="dnd5e-import-file"
            :disabled="busy !== 'idle' || !isGm"
            @change="selectFile"
          >
        </label>
        <p v-if="fileName" class="dnd5e-import-filename">{{ fileName }}</p>
        <p v-if="!payload && !preview" class="dnd5e-import-empty">{{ copy.emptyHint }}</p>
        <button
          type="button"
          class="primary"
          data-testid="dnd5e-import-preview"
          :disabled="!payload || busy !== 'idle' || !isGm"
          @click="runPreview"
        >{{ busy === 'preview' ? copy.previewing : copy.preview }}</button>
      </template>

      <p v-if="error" class="error-banner" role="alert" data-testid="dnd5e-import-error">{{ error }}</p>

      <div v-if="needsRepreview" class="error-banner" role="alert" data-testid="dnd5e-import-stale">
        <strong>{{ commitStale ? copy.commitStaleTitle : copy.staleTitle }}</strong>
        {{ commitStale ? copy.commitStaleHint : copy.staleHint }}
        <button type="button" data-testid="dnd5e-import-repreview" :disabled="busy !== 'idle' || !isGm" @click="runPreview">{{ copy.rePreview }}</button>
      </div>

      <div v-if="levelBlocked" class="error-banner" role="alert" data-testid="dnd5e-level-blocked">
        <strong>{{ copy.levelBlockedTitle }}</strong> {{ copy.levelBlockedHint }}
      </div>

      <template v-if="preview && !result">
        <ConversionDisplay :display="preview.display" />

        <section v-if="preview.missing.length" class="dnd5e-import-missing" data-testid="dnd5e-missing-list">
          <h3>{{ copy.missingTitle }}</h3>
          <ul>
            <li v-for="entry in preview.missing" :key="entry.field"><code>{{ entry.field }}</code> {{ entry.reason }}</li>
          </ul>
        </section>

        <section v-if="preview.errors.length" class="dnd5e-import-errors" role="alert" data-testid="dnd5e-validation-errors">
          <h3>{{ copy.errorsTitle }}</h3>
          <ul><li v-for="message in preview.errors" :key="message">{{ message }}</li></ul>
        </section>

        <p v-if="choicesError" class="error-banner" role="alert" data-testid="dnd5e-choices-error">{{ choicesError }}</p>
        <SelectionsForm
          v-if="choices"
          :draft="preview.draft"
          :choices="choices"
          :missing="preview.missing"
          :selections="selections"
          :disabled="busy !== 'idle'"
          @change="onSelectionsChange"
        />

        <Dnd5eReportPanel :report="preview.report" :reconciliation="preview.reconciliation" :source-unresolved="preview.source_unresolved" />

        <div class="dnd5e-import-actions">
          <button type="button" data-testid="dnd5e-import-startover" :disabled="busy !== 'idle'" @click="startOver">{{ copy.startOver }}</button>
          <button
            type="button"
            class="primary"
            data-testid="dnd5e-import-commit"
            :disabled="!canCommit"
            @click="commit"
          >{{ busy === 'commit' ? copy.committing : copy.commit }}</button>
        </div>
      </template>

      <section v-if="result" class="dnd5e-import-receipt" data-testid="dnd5e-import-receipt">
        <h3>{{ copy.receiptTitle }}</h3>
        <p role="status" data-testid="dnd5e-receipt-name">{{ result.created.character_name }}</p>
        <p v-if="result.created.reused" role="status" data-testid="dnd5e-receipt-reused">{{ copy.receiptReused }}</p>
        <p class="dnd5e-import-filename">{{ copy.receiptUser }}: <code>{{ result.created.user_id }}</code></p>
        <p class="dnd5e-import-filename">{{ copy.createdHint }}</p>
        <Dnd5eReportPanel :report="result.report" :reconciliation="result.reconciliation" />
        <div class="dnd5e-import-actions">
          <RouterLink class="dnd5e-import-play" :to="{ name: 'play', query: { game: gameKey } }" data-testid="dnd5e-import-open-play">{{ copy.openPlay }}</RouterLink>
          <button type="button" data-testid="dnd5e-import-startover" :disabled="busy !== 'idle'" @click="startOver">{{ copy.startOver }}</button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.dnd5e-import-page { display: grid; gap: .8rem; width: min(880px, 100%); margin: 0 auto; padding: 1.2rem 1rem 2rem; min-width: 0; overflow-wrap: anywhere; }
.dnd5e-import-nav { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: .35rem; font-size: .82rem; }
.dnd5e-import-nav a { color: var(--df-interactive-strong); text-decoration: none; }
.dnd5e-import-nav a:hover { text-decoration: underline; }
.dnd5e-import-head h1 { margin: 0 0 .3rem; font-size: 1.4rem; color: var(--df-text); }
.dnd5e-import-head p { margin: 0; color: var(--df-text-secondary); font-size: .88rem; }
.dnd5e-import-limits { margin-top: .3rem !important; color: var(--df-text-muted) !important; font-size: .78rem !important; }
.dnd5e-import-field { display: grid; gap: .3rem; color: var(--df-text-secondary); font-size: .85rem; }
.dnd5e-import-field select, .dnd5e-import-field input { width: 100%; min-width: 0; box-sizing: border-box; }
.dnd5e-import-filename { margin: 0; color: var(--df-text-muted); font-size: .78rem; overflow-wrap: anywhere; }
.dnd5e-import-empty { margin: 0; color: var(--df-text-muted); font-size: .82rem; }
.dnd5e-import-banner { margin: 0; padding: .5rem .7rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text-secondary); font-size: .82rem; }
.dnd5e-import-missing, .dnd5e-import-errors { display: grid; gap: .4rem; padding: .8rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.dnd5e-import-missing h3, .dnd5e-import-errors h3, .dnd5e-import-receipt h3 { margin: 0; color: var(--df-accent-strong); font-size: .95rem; }
.dnd5e-import-missing ul, .dnd5e-import-errors ul { display: grid; gap: .3rem; margin: 0; padding-left: 1.2rem; color: var(--df-text-secondary); font-size: .8rem; }
.dnd5e-import-errors { border-color: var(--df-danger); }
.dnd5e-import-errors ul { color: var(--df-danger-strong); }
.dnd5e-import-actions { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
.dnd5e-import-receipt { display: grid; gap: .6rem; padding: .9rem; border: 1px solid color-mix(in srgb, var(--df-success-strong) 40%, var(--df-border-soft)); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.dnd5e-import-receipt p { margin: 0; color: var(--df-text-secondary); font-size: .85rem; }
.dnd5e-import-play { color: var(--df-accent-strong); font-size: .85rem; }
.error-banner { margin: 0; padding: .6rem .8rem; border: 1px solid var(--df-danger); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-danger) 12%, var(--df-surface-2)); color: var(--df-danger-strong); font-size: .84rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
button { min-height: 40px; padding: .4rem .9rem; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-2); color: var(--df-text); cursor: pointer; }
button.primary { border-color: var(--df-interactive); background: var(--df-interactive); color: var(--df-on-accent); }
button:disabled { opacity: .48; cursor: not-allowed; }
code { padding: 0 .25rem; border-radius: var(--df-radius-sm); background: var(--df-control-bg); color: var(--df-text-secondary); font-size: .78rem; overflow-wrap: anywhere; }
</style>