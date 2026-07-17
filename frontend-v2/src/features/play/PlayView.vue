<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronBack, ChevronForward } from '@vicons/ionicons5'
import { useRoute, useRouter } from 'vue-router'
import { api, apiBlob } from '@/api/client'
import type { BotBindTokenResponse, CharacterCard, CharacterCardsResponse, CharacterListResponse, CommandResponse, HealthResponse, JsonObject, PendingPayment, Player, PlayerContextResponse, PublicAction, RuleMeta, WorldCandidate, WorldListResponse, WorldTemplatesResponse } from '@/api/types'
import { useGame } from '@/composables/useGame'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { buildJoinLink } from '@/utils/shareLink'
import GameTimeline from '@/components/GameTimeline.vue'
import ActionComposer from '@/components/ActionComposer.vue'
import GameSidebar from '@/components/GameSidebar.vue'
import RuleHelp from '@/components/RuleHelp.vue'
import HealthPanel from '@/components/HealthPanel.vue'
import Modal from '@/components/ui/Modal.vue'
import GmToolbar from '@/components/play/GmToolbar.vue'
import MultiplayerPanel from '@/components/play/MultiplayerPanel.vue'

defineOptions({ name: 'PlayView' })

const BOT_BIND_COMMAND = '\u7ed1\u5b9a'

const route = useRoute(), router = useRouter()
const isPlayer = computed(() => !!(route.query.user || route.query.share))
function goBack() { router.push({ name: 'overview' }) }

const game = useGame()
const settings = useSettingsStore()
const toast = useToast()
const { confirm } = useConfirm()
const { t } = useLocale()
const help = ref(false), ruleMeta = ref<RuleMeta>({}), preview = ref(false), delegate = ref(false), cards = ref<CharacterCard[]>([]), showCards = ref(false), health = ref<HealthResponse>({ events: [] })
const worldCandidates = ref<WorldCandidate[]>([]), showWorldSwitch = ref(false), showRoomPassword = ref(false), roomPasswordInput = ref('')
const sidebarCollapsed = ref(localStorage.getItem('play_sidebar_collapsed') === '1')
const gmThinking = ref(false)
function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value; localStorage.setItem('play_sidebar_collapsed', sidebarCollapsed.value ? '1' : '0') }
const railCollapsed = ref(false)
function toggleRail() { railCollapsed.value = !railCollapsed.value; localStorage.setItem('play_rail_collapsed', railCollapsed.value ? '1' : '0') }
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || t('operationFailed')) }
function joinNames(names: string[]) { return names.filter(Boolean).join(t('listSeparator')) }

async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text)
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try { document.execCommand('copy') } finally { document.body.removeChild(ta) }
}

const actorId = computed(() => game.userId.value || game.player.value?.user_id || game.detail.value?.gm_uid || '')
const serverJudging = computed(() => game.detail.value?.state === 'active_judgment')
const showGmThinking = computed(() => gmThinking.value || serverJudging.value)
const sceneTitle = computed(() => game.detail.value?.scene || t('unknownScene'))
const stateLabel = computed(() => {
  if (showGmThinking.value) return t('gmThinking')
  const state = game.detail.value?.state || 'unknown'
  const labels: Record<string, string> = {
    setup: t('preparing'),
    waiting: t('waitingActionState'),
    action: t('actionPhase'),
    active_action: t('actionPhase'),
    resolving: t('resolvingState'),
    active_judgment: t('gmThinking'),
    paused: t('statePaused'),
    ended: t('stateEnded'),
  }
  return labels[state] || state || t('stateUnknown')
})
const tableMode = computed(() => game.detail.value?.solo_mode ? t('soloAdventure') : t('multiplayerAdventure'))
const roleLabel = computed(() => game.isGm.value ? t('gmConsole') : isPlayer.value ? t('playerView') : t('spectatorView'))
const progressLabel = computed(() => {
  if (showGmThinking.value) return t('generatingStory')
  const detail = game.detail.value
  if (!detail) return t('syncing')
  if (detail.solo_mode) return t('continuousAction')
  const ready = detail.multiplayer?.ready_count || 0
  const total = detail.multiplayer?.active_count ?? detail.multiplayer?.player_count ?? 0
  return t('actedProgress', { ready, total })
})
const gameCode = computed(() => game.currentGame.value ? game.currentGame.value.slice(0, 8) : '')
const tableNotice = computed(() => {
  if (showGmThinking.value) return t('gmProcessingNotice')
  const detail = game.detail.value
  if (!detail) return ''
  if (detail.state === 'paused') return game.isGm.value ? t('pausedNoticeGm') : t('pausedNoticePlayer')
  const waiting = detail.multiplayer?.waiting_players || []
  if (!detail.solo_mode && waiting.length) {
    const names = joinNames(waiting.map((p: Player) => p.character_name || p.user_id))
    return names ? t('waitingPlayersNotice', { names }) : t('waitingOthersNotice')
  }
  const away = detail.multiplayer?.away_players || []
  if (!detail.solo_mode && away.length) {
    const names = joinNames(away.map((p: Player) => p.character_name || p.user_id))
    return names ? t('awayPlayersNotice', { names }) : t('awayGenericNotice')
  }
  const submitted = detail.multiplayer?.submitted_actions?.some((a: PublicAction) => a.user_id === actorId.value)
  if (!detail.solo_mode && submitted) return t('submittedNotice')
  return ''
})
async function command(path: string, body: JsonObject = {}) {
  const thinkingCommand = path === 'advance'
  if (thinkingCommand) gmThinking.value = true
  try {
    const r = await api<CommandResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/${path}`, { method: 'POST', body: JSON.stringify(body) })
    if (r.error) { toast.error(r.error); return }
    if (r.forced_waiting?.length) toast.info(t('forcedWaitingToast', { names: r.forced_waiting.join(t('listSeparator')) }))
    if (r.narration) toast.success(r.narration)
    else toast.success(t('operationDone'))
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) } finally { if (thinkingCommand) gmThinking.value = false }
}

function onCommand(text: string) { command('gm-command', { command: text }) }
function onPerception(uid: string, text: string) { command('private-message', { user_id: uid, text }) }
function onMode() { command('mode', { solo: !game.detail.value?.solo_mode }) }
function onAccess() { command('player-access', { open: game.detail.value?.player_access_open === false }) }

function onRoomPassword() {
  roomPasswordInput.value = ''
  showRoomPassword.value = true
}
async function setRoomPassword() {
  try {
    const r = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/room-password`, { method: 'POST', body: JSON.stringify({ password: roomPasswordInput.value }) })
    if (r.error || r.ok === false) throw new Error(r.error || t('settingFailed'))
    showRoomPassword.value = false
    toast.success(roomPasswordInput.value ? t('roomPasswordUpdated') : t('roomPasswordCleared'))
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function ensureSettingsLoaded() {
  if (!Object.keys(settings.config).length && !settings.loading) {
    await settings.load().catch(() => undefined)
  }
}

async function invite() {
  await ensureSettingsLoaded()
  await copyToClipboard(buildJoinLink(game.currentGame.value, settings.config.public_base_url))
  toast.success(t('inviteCopied'))
}

async function copyBotBind() {
  try {
    const r = await api<BotBindTokenResponse & { ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/bot-bind-token`, { method: 'POST', body: JSON.stringify({ rotate: true }) })
    if (r?.ok === false || !r?.bind_token) throw new Error(r?.error || t('botBindFailed'))
    await copyToClipboard(`${BOT_BIND_COMMAND} ${game.currentGame.value} ${r.bind_token}`)
    toast.success(t('botBindCopied'))
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function openWorldSwitch() {
  try {
    const [templateData, worldData] = await Promise.all([api<WorldTemplatesResponse>('/world-templates'), api<WorldListResponse>('/worlds')])
    const seen = new Set<string>()
    const candidates: WorldCandidate[] = []
    for (const template of templateData.templates || []) {
      const id = template.world_id || template.id
      if (!id) continue
      seen.add(id)
      candidates.push({ id, name: template.world_name || template.name || id, description: template.description || '', source: t('templateSource'), default_rule: template.default_rule || '', entry_count: undefined })
    }
    for (const w of worldData.worlds || []) {
      const id = w.id || w.world_id
      if (!id || seen.has(id)) continue
      candidates.push({ id, name: w.name || w.world_name || id, description: w.description || '', source: t('lorebookSourceShort'), default_rule: '', entry_count: w.entry_count || 0 })
    }
    worldCandidates.value = candidates
    showWorldSwitch.value = true
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function switchWorld(worldId: string) {
  try {
    const r = await api<{ ok?: boolean; error?: string; world_name?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/switch-world`, { method: 'POST', body: JSON.stringify({ world_id: worldId }) })
    if (r.error || r.ok === false) throw new Error(r.error || t('switchFailed'))
    showWorldSwitch.value = false
    toast.success(t('switchedWorld', { name: r.world_name || worldId }))
    await loadPlayContext()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

function toggleDelegate() {
  delegate.value = !delegate.value
  router.replace({ query: { ...route.query, delegate: delegate.value ? '1' : undefined } })
}

async function openCards() {
  try {
    const r = await api<CharacterCardsResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/character-cards`)
    cards.value = r.cards || []
    showCards.value = true
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function selectCard(card: CharacterCard) {
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(actorId.value)}`, { method: 'PUT', body: JSON.stringify(card) })
    showCards.value = false
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function kick(uid: string) {
  const ok = await confirm({ title: t('kickPlayerTitle'), content: t('kickPlayerContent'), positiveText: t('kickPlayerTitle'), negativeText: t('cancel'), type: 'error' })
  if (!ok) return
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(uid)}`, { method: 'DELETE' })
    toast.success(t('kicked'))
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function setAway(uid: string, away: boolean) {
  try {
    const r = await api<{ ok?: boolean; error?: string; character_name?: string }>(
      `/games/${encodeURIComponent(game.currentGame.value)}/players/${encodeURIComponent(uid)}/away`,
      { method: 'POST', body: JSON.stringify({ away }) },
    )
    if (r.error || r.ok === false) throw new Error(r.error || t('statusSwitchFailed'))
    toast.success(t('playerAwayChanged', { name: r.character_name || uid, state: away ? t('away') : t('returned') }))
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function copyLink(uid: string) {
  await ensureSettingsLoaded()
  await copyToClipboard(buildJoinLink(game.currentGame.value, settings.config.public_base_url, uid))
  toast.success(t('controlLinkCopied'))
}

function onEdit(uid: string) {
  router.push({ name: 'characters', query: { edit_user: uid, game: game.currentGame.value } })
}

async function resolveHealth(id: string, action: string) {
  await api(`/games/${encodeURIComponent(game.currentGame.value)}/health/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: '{}' })
  health.value = await api(`/games/${encodeURIComponent(game.currentGame.value)}/health`)
}

const pendingPay = ref<PendingPayment | null>(null)
watch(() => game.detail.value?.pending_payments, (list) => {
  const mine = (list || []).find(p => p.status === 'pending' && p.uid === actorId.value)
  if (mine && (!pendingPay.value || pendingPay.value.id !== mine.id)) pendingPay.value = mine
}, { immediate: true, deep: true })
async function resolvePay(accepted: boolean) {
  const p = pendingPay.value
  if (!p || !p.id) return
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/payments/${encodeURIComponent(p.id)}`, { method: 'POST', body: JSON.stringify({ accepted }) })
    pendingPay.value = null
    await game.refresh()
    toast.success(accepted ? t('paid') : t('paymentRejected'))
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function lifecycle(action: string) {
  const ok = await confirm({
    title: action === 'reset' ? t('resetCurrentProgress') : t('restartCurrentGame'),
    content: action === 'reset' ? t('resetCurrentContent') : t('restartCurrentContent'),
    positiveText: action === 'reset' ? t('resetProgress') : t('restartGameAction'), negativeText: t('cancel'), type: 'warning',
  })
  if (!ok) return
  await command(action)
}

async function exportSave() {
  try {
    const response = await apiBlob(`/games/${encodeURIComponent(game.currentGame.value)}/export`)
    const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = `${game.detail.value?.world_name || 'save'}.json`; link.click()
    URL.revokeObjectURL(url)
    toast.success(t('saveExported'))
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

function onLoreClick(name: string) {
  toast.info(t('sceneToast', { name }))
}

function syncPlayRoute() {
  if (route.name === 'play' && game.currentGame.value && !route.query.game) {
    router.replace({ name: 'play', query: { ...route.query, game: game.currentGame.value } })
  }
}

async function loadPlayContext() {
  if (!game.currentGame.value) return
  syncPlayRoute()
  // Shared player links without a user query belong on the character creation flow.
  if (route.query.share && !route.query.user && !localStorage.getItem('trpg_access_token')) {
    router.replace({ name: 'join', query: { game: game.currentGame.value, share: '1' } })
    return
  }
  if (!route.query.user) {
    try {
      await api(`/games/${encodeURIComponent(game.currentGame.value)}/claim-gm`, { method: 'POST', body: '{}' })
    } catch (e: unknown) {
      game.error.value = errorMessage(e)
    }
  }
  await game.refresh()
  game.connect()
  try {
    const healthRequest: Promise<HealthResponse> = game.isGm.value ? api<HealthResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/health?include_resolved=true`) : Promise.resolve({ events: [] })
    const [chars, context, h] = await Promise.all([
      api<CharacterListResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/characters`),
      api<PlayerContextResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/player-context`).catch(() => ({ preview: false })),
      healthRequest,
    ])
    ruleMeta.value = { ...(chars.rule_meta || {}), rule_special_stats: chars.rule_special_stats || [] }
    health.value = h
    preview.value = !!context.preview
    delegate.value = route.query.delegate === '1'
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  }
}

onMounted(() => {
  loadPlayContext()
  ensureSettingsLoaded()
})
watch(() => game.currentGame.value, (next, prev) => {
  if (next && next !== prev) loadPlayContext()
})
watch(() => game.detail.value?.solo_mode, (solo, prev) => {
  if (solo === undefined || solo === prev) return
  if (solo) {
    railCollapsed.value = true
  } else {
    const stored = localStorage.getItem('play_rail_collapsed')
    railCollapsed.value = stored !== null ? stored === '1' : false
  }
}, { immediate: true })
</script>

<template>
  <main v-if="game.currentGame.value" class="play-page">
    <header class="topbar play-hud">
      <div class="play-hud-main">
        <button v-if="!isPlayer" class="icon play-back" :title="t('backToOverview')" @click="goBack">←</button>
        <div class="play-titleblock">
          <span class="play-eyebrow">{{ roleLabel }} · {{ tableMode }}</span>
          <h1>{{ game.detail.value?.world_name || 'DiceFrame' }}</h1>
          <p>{{ sceneTitle }} · {{ t('roundLabel', { round: game.detail.value?.round_number || 0 }) }}</p>
        </div>
      </div>
      <div class="play-hud-stats" :aria-label="t('gameStatus')">
        <span class="hud-stat"><strong>{{ stateLabel }}</strong><small>{{ t('status') }}</small></span>
        <span class="hud-stat"><strong>{{ progressLabel }}</strong><small>{{ t('progress') }}</small></span>
        <span v-if="gameCode" class="hud-stat"><strong>{{ gameCode }}</strong><small>{{ t('save') }}</small></span>
      </div>
      <div class="toolbar play-toolbar">
        <span v-if="preview" class="busy">{{ t('hostPreview') }}</span>
        <button v-if="preview" @click="toggleDelegate">{{ delegate ? t('disableDelegate') : t('enableDelegate') }}</button>
        <span v-if="game.loading.value" class="busy">{{ t('updating') }}</span>
        <button @click="openCards">{{ t('characters') }}</button>
        <button @click="help = true">{{ t('rule') }}</button>
        <button @click="game.refresh()">{{ t('refresh') }}</button>
      </div>
    </header>

    <div v-if="game.error.value" class="error-banner">{{ game.error.value }}</div>
    <div v-else-if="!game.detail.value" class="play-loading">
      <span class="spinner"></span>
      <h2>{{ t('enteringTable') }}</h2>
      <p>{{ t('syncingTable') }}</p>
    </div>

    <div
      v-if="game.detail.value"
      class="play-layout"
      :class="{ collapsed: sidebarCollapsed, 'no-console': !game.isGm.value && game.detail.value.solo_mode !== false, 'rail-collapsed': railCollapsed }"
    >
      <GameSidebar
        :detail="game.detail.value"
        :player="game.player.value"
        :private-messages="game.privateMessages.value"
        :map="game.map.value"
        :rule-meta="ruleMeta"
        :collapsed="sidebarCollapsed"
        @lore-click="onLoreClick"
        @toggle-sidebar="toggleSidebar"
      />

      <section class="play-main">
        <section class="scene-strip">
          <div class="scene-title">
            <span class="scene-label">{{ t('currentScene') }}</span>
            <h2>{{ sceneTitle }}</h2>
            <p>{{ tableMode }} · {{ stateLabel }}</p>
          </div>
          <div class="scene-chips">
            <span>{{ t('roundLabel', { round: game.detail.value.round_number || 0 }) }}</span>
            <span>{{ progressLabel }}</span>
            <span v-if="game.detail.value.world_id">{{ game.detail.value.world_id }}</span>
          </div>
        </section>

        <GameTimeline
          :log="game.log.value"
          :live="game.detail.value.multiplayer?.submitted_actions || []"
          :players="game.players.value"
          :round="game.detail.value.round_number || 0"
          :lore="game.lore.value"
          :game-key="game.currentGame.value"
          :processing="showGmThinking"
          :is-gm="game.isGm.value"
          @refresh="game.refresh"
        />

        <div v-if="tableNotice" class="table-notice notice">{{ tableNotice }}</div>

        <ActionComposer :game-key="game.currentGame.value" :user-id="actorId" :detail="game.detail.value" :disabled="preview && !delegate" @processing="gmThinking = $event" @refresh="game.refresh" />
      </section>

      <aside v-if="game.isGm.value || game.detail.value.solo_mode === false" class="play-control-rail" :class="{ collapsed: railCollapsed }">
        <button class="rail-toggle" @click="toggleRail" :title="railCollapsed ? t('expandGmControls') : t('collapseGmControls')">
          <NIcon :component="railCollapsed ? ChevronBack : ChevronForward" size="16" />
        </button>
        <GmToolbar
          v-if="game.isGm.value"
          :detail="game.detail.value"
          :players="game.players.value"
          :is-gm="game.isGm.value"
          @advance="command('advance', { force: true })"
          @rollback="command('rollback')"
          @invite="invite"
          @bot-bind="copyBotBind"
          @mode="onMode"
          @access="onAccess"
          @command="onCommand"
          @perception="onPerception"
          @export="exportSave"
          @reset="lifecycle('reset')"
          @restart="lifecycle('restart')"
          @cards="openCards"
          @world-switch="openWorldSwitch"
          @room-password="onRoomPassword"
        />

        <MultiplayerPanel
          v-if="game.detail.value.solo_mode === false"
          :players="game.players.value"
          :detail="game.detail.value"
          :is-gm="game.isGm.value"
          :current-user-id="actorId"
          @kick="kick"
          @set-away="setAway"
          @copy-link="copyLink"
          @edit="onEdit"
        />

        <HealthPanel v-if="game.isGm.value" :health="health" :detail="game.detail.value" :is-gm="game.isGm.value" @resolve="resolveHealth" />
      </aside>
    </div>
    <RuleHelp v-if="help" :meta="ruleMeta" @close="help = false" />

    <div v-if="showCards" class="modal" @click.self="showCards = false">
      <section class="dialog">
        <header><h2>{{ t('sharedCharacterLibrary') }}</h2><button @click="showCards = false">×</button></header>
        <p>{{ t('replaceCharacterHint') }}</p>
        <button v-for="c in cards" :key="c.character_name" class="card-choice" @click="selectCard(c)">
          <strong>{{ c.character_name }}</strong><span>{{ c.race }} · {{ c.class }}</span>
        </button>
        <p v-if="!cards.length" class="muted">{{ t('emptyCharacterLibrary') }}</p>
      </section>
    </div>

    <div v-if="showWorldSwitch" class="modal" @click.self="showWorldSwitch = false">
      <section class="dialog world-switch-dialog">
        <header><h2>{{ t('switchWorldTitle') }}</h2><button @click="showWorldSwitch = false">×</button></header>
        <p>{{ t('currentWorldBinding', { id: game.detail.value?.world_id || t('notBound') }) }}</p>
        <div class="world-switch-list">
          <button
            v-for="w in worldCandidates"
            :key="w.id"
            class="world-choice"
            :class="{ active: w.id === game.detail.value?.world_id }"
            @click="switchWorld(w.id)"
          >
            <strong>{{ w.name }}</strong>
            <span>{{ w.source }}<template v-if="w.default_rule"> · {{ w.default_rule }}</template><template v-if="w.entry_count !== undefined"> · {{ t('entriesCount', { count: w.entry_count }) }}</template></span>
            <small>{{ w.description || w.id }}</small>
          </button>
          <p v-if="!worldCandidates.length" class="muted">{{ t('noWorldCandidates') }}</p>
        </div>
      </section>
    </div>

    <div v-if="showRoomPassword" class="modal" @click.self="showRoomPassword = false">
      <section class="dialog">
        <header><h2>{{ game.detail.value?.has_room_password ? t('editRoomPassword') : t('setRoomPassword') }}</h2><button @click="showRoomPassword = false">×</button></header>
        <p>{{ t('roomPasswordHelp') }}</p>
        <label>{{ t('newPassword') }}<input type="password" v-model="roomPasswordInput" :placeholder="t('emptyCancelsPassword')" @keyup.enter="setRoomPassword"></label>
        <div class="actions">
          <button @click="showRoomPassword = false">{{ t('cancel') }}</button>
          <button class="primary" @click="setRoomPassword">{{ t('saveAction') }}</button>
        </div>
      </section>
    </div>

    <Modal v-if="pendingPay" :title="t('gmPaymentTitle')" @close="pendingPay = null">
      <p>{{ t('gmPaymentContent', { amount: pendingPay.amount ?? 0, reason: pendingPay.reason ? t('gmPaymentReason', { reason: pendingPay.reason }) : '' }) }}</p>
      <p class="muted">{{ t('gmPaymentHelp') }}</p>
      <template #actions>
        <button @click="pendingPay = null">{{ t('later') }}</button>
        <button class="danger" @click="resolvePay(false)">{{ t('reject') }}</button>
        <button class="primary" @click="resolvePay(true)">{{ t('confirmPurchase') }}</button>
      </template>
    </Modal>
  </main>

  <main v-else class="empty empty-game">
    <section>
      <h1>{{ t('chooseAdventure') }}</h1>
      <p class="muted">{{ t('chooseAdventureHint') }}</p>
      <div class="actions">
        <button class="primary" @click="goBack">{{ t('viewSaves') }}</button>
        <button @click="router.push({ name: 'create' })">{{ t('createAdventure') }}</button>
      </div>
    </section>
  </main>
</template>
