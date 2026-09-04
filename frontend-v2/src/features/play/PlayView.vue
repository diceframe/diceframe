<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { BookOutline, ChatbubbleEllipsesOutline, ChevronBack, ChevronForward, MapOutline, ShieldOutline, StatsChartOutline, TerminalOutline } from '@vicons/ionicons5'
import { useRoute, useRouter } from 'vue-router'
import { api, apiBlob, hasAccessToken, isNotFoundError } from '@/api/client'
import { currentBackendUrl, isStandaloneFrontend } from '@/api/connection'
import type { BotBindTokenResponse, CharacterCard, CharacterCardsResponse, CharacterListResponse, CharacterPortrait, CharacterSheet, CheckResult, CommandResponse, GameDetail, HealthResponse, JsonObject, LuckDecisionResponse, PendingPayment, Player, PlayerContextResponse, PublicAction, RuleMeta, RulesetDirectorProposal, RulesetGameplayView, WorldCandidate, WorldListResponse, WorldTemplatesResponse } from '@/api/types'
import { queryString } from '@/stores/gameContext'
import { isStoredPlayerMember } from '@/utils/joinIdentity'
import { useGame } from '@/composables/useGame'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale, type Locale } from '@/composables/useLocale'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { buildJoinLink } from '@/utils/shareLink'
import { copyToClipboard } from '@/utils/clipboard'
import { contentLanguageOf, filterByContentLanguage } from '@/utils/contentLanguage'
import { characterCardNeedsConversion, characterCardRuleName } from '@/utils/characterCards'
import GameTimeline from '@/components/GameTimeline.vue'
import ActionComposer from '@/components/ActionComposer.vue'
import DirectorProposalCard from '@/components/play/DirectorProposalCard.vue'
import CombatMessageComposer from '@/components/play/CombatMessageComposer.vue'
import KpQuestionDialog from '@/components/play/KpQuestionDialog.vue'
import TableTalkFeed from '@/components/play/TableTalkFeed.vue'
import EconomyProposalCard from '@/components/play/EconomyProposalCard.vue'
import GmChargeComposer from '@/features/play/economy/GmChargeComposer.vue'
import GameSidebar from '@/components/GameSidebar.vue'
import PlayHelpCenter from '@/components/PlayHelpCenter.vue'
import HealthPanel from '@/components/HealthPanel.vue'
import Modal from '@/components/ui/Modal.vue'
import GmToolbar from '@/components/play/GmToolbar.vue'
import MultiplayerPanel from '@/components/play/MultiplayerPanel.vue'
import MapWorkspace from '@/components/play/MapWorkspace.vue'
import SceneGalleryModal from '@/components/play/SceneGalleryModal.vue'
import PortraitPicker from '@/components/admin/PortraitPicker.vue'
import AdventureSceneImagePicker from '@/components/common/AdventureSceneImagePicker.vue'
import MapBackgroundSettingsModal from '@/components/play/MapBackgroundSettingsModal.vue'
import RulesetCharacterCenterHost from '@/features/rulesets/RulesetCharacterCenterHost.vue'
import RulesetPlayHost from '@/features/rulesets/RulesetPlayHost.vue'
import { resolveRulesetPlayExtension } from '@/features/rulesets/registry'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'
import { fileToBase64, resolveGameSceneImageUrl, revokeSceneImageUrl, sceneImageStyle } from '@/api/sceneImages'
import { fetchRulesetAvailableActions } from '@/api/rulesets'
import { currencyLabel } from '@/utils/ruleSchema'
import { isEconomyProposalActionable, isNonBlockingPersonalPurchase, nextEconomyProposal } from '@/features/play/economyPrompts'

defineOptions({ name: 'PlayView' })

const BOT_BIND_COMMAND = '\u7ed1\u5b9a'

const route = useRoute(), router = useRouter()
const isPlayer = computed(() => !!(route.query.user || route.query.share))
function goBack() { router.push({ name: 'overview' }) }

const game = useGame()
const settings = useSettingsStore()
const toast = useToast()
const { confirm } = useConfirm()
const { locale, setLocale, t } = useLocale()
const help = ref(false), ruleMeta = ref<RuleMeta>({}), preview = ref(false), delegate = ref(false), cards = ref<CharacterCard[]>([]), showCards = ref(false), health = ref<HealthResponse>({ events: [] })
const showKpQuestion = ref(false)
const worldCandidates = ref<WorldCandidate[]>([]), showWorldSwitch = ref(false), showRoomPassword = ref(false), roomPasswordInput = ref(''), luckTimeoutInput = ref('')
const sidebarCollapsed = ref(localStorage.getItem('play_sidebar_collapsed') === '1')
const mobilePanel = ref<'sidebar' | 'controls' | ''>('')
const showMap = ref(false)
const showSceneGallery = ref(false)
const gmThinking = ref(false)
const storyRecapBusy = ref(false)
const luckBusyId = ref('')
const showPortraitEditor = ref(false)
const showCharacterCenter = ref(false)
const portraitDraft = ref<CharacterPortrait | null>()
const portraitBusy = ref(false)
const playSceneImageUrl = ref(ruleSceneUrl())
const showSceneImageEditor = ref(false)
const sceneImageDraft = ref<File | null>(null)
const sceneImageDefaultUrl = ref(ruleSceneUrl())
const sceneImageBusy = ref(false)
const showMapBackgroundEditor = ref(false)
const showPaymentComposer = ref(false)
const composerPayerUid = ref('')
const composerRecipientUid = ref('')
const paymentBusy = ref(false)
function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value; localStorage.setItem('play_sidebar_collapsed', sidebarCollapsed.value ? '1' : '0') }
const railCollapsed = ref(false)
function toggleRail() { railCollapsed.value = !railCollapsed.value; localStorage.setItem('play_rail_collapsed', railCollapsed.value ? '1' : '0') }
function toggleSidebarPanel() {
  if (mobilePanel.value === 'sidebar') { mobilePanel.value = ''; return }
  toggleSidebar()
}
function toggleRailPanel() {
  if (mobilePanel.value === 'controls') { mobilePanel.value = ''; return }
  toggleRail()
}
function openMobilePanel(panel: 'sidebar' | 'controls') {
  if (panel === 'sidebar' && sidebarCollapsed.value) toggleSidebar()
  if (panel === 'controls' && railCollapsed.value) toggleRail()
  mobilePanel.value = panel
}
function toggleMobilePanel(panel: 'sidebar' | 'controls') {
  if (mobilePanel.value === panel) { mobilePanel.value = ''; return }
  openMobilePanel(panel)
}
function openMap() {
  mobilePanel.value = ''
  showMap.value = true
}
function refreshMapAfterBackground() {
  void game.refresh(true)
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || t('operationFailed')) }
function openPaymentComposer() {
  const first = game.players.value[0]?.user_id || ''
  composerPayerUid.value = first
  composerRecipientUid.value = first
  showPaymentComposer.value = true
}
async function createPaymentProposal(payload: {
  payer_uid: string; recipient_uid: string; amount: number; reason: string; items: string[]
}) {
  if (!game.currentGame.value || !payload.payer_uid || payload.amount < 1) return
  paymentBusy.value = true
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/payments`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    showPaymentComposer.value = false
    await game.refresh(true)
    toast.success(t('paymentProposalCreated'))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
  } finally {
    paymentBusy.value = false
  }
}
function joinNames(names: string[]) { return names.filter(Boolean).join(t('listSeparator')) }
function onLocaleChange(event: Event) { setLocale((event.target as HTMLSelectElement).value as Locale) }

const actorId = computed(() => game.actorId.value || game.player.value?.user_id || '')
const canAskKp = computed(() => Boolean(
  game.actorId.value
  && game.players.value.some(player => player.user_id === game.actorId.value)
  && (!preview.value || delegate.value),
))
const rulesetRefreshKey = ref(0)
async function refreshRulesetPanels(): Promise<void> {
  rulesetRefreshKey.value += 1
  await game.refresh(true)
}
const hasAuthoritativeCombat = computed(() => Boolean(
  game.detail.value?.ruleset_runtime?.capabilities?.authoritative_intents
  && game.detail.value?.ruleset_runtime?.capabilities?.deterministic_combat,
))
const hasRulesAwareCharacters = computed(() => (
  game.detail.value?.ruleset_runtime?.capabilities?.character_lifecycle === 'rules_aware'
))
const hasCampaignGuidance = computed(() => Boolean(
  game.detail.value?.ruleset_runtime?.capabilities?.session_zero
  || game.detail.value?.ruleset_runtime?.capabilities?.tutorial_coach,
))
type RulesetTool = 'campaign' | 'combat'
const activeRulesetTool = ref<RulesetTool | ''>('')
const directorProposal = ref<RulesetDirectorProposal | null>(null)
const rulesetGameplay = ref<RulesetGameplayView | null>(null)
const rulesetCombatStatus = ref('none')
const rulesetCampaignStatus = ref('')
const hasProfessionalTools = computed(() => hasCampaignGuidance.value || hasAuthoritativeCombat.value)
const rulesetToolCopy = computed(() => (
  resolveRulesetPlayExtension(String(game.detail.value?.ruleset_runtime?.id || ''))
    ?.copy(String(locale.value))
  || { menu: 'Ruleset tools', campaign: 'Campaign', combat: 'Combat', title: 'Ruleset tools' }
))
function openRulesetTool(tool: RulesetTool): void {
  activeRulesetTool.value = tool
}

function navigateRulesetTool(tool: RulesetTool): void {
  if (tool === 'campaign' ? hasCampaignGuidance.value : hasAuthoritativeCombat.value) {
    activeRulesetTool.value = tool
  }
}

let rulesetToolPoll: number | undefined
let rulesetToolSequence = 0
let previousEncounterPhase = ''
async function syncEncounterTool(): Promise<void> {
  const gameKey = game.currentGame.value
  if (!hasAuthoritativeCombat.value || !gameKey) return
  const sequence = ++rulesetToolSequence
  try {
    const response = await fetchRulesetAvailableActions(gameKey)
    if (sequence !== rulesetToolSequence || game.currentGame.value !== gameKey) return
    rulesetGameplay.value = response.gameplay
    directorProposal.value = response.gameplay.director?.proposal?.kind === 'narrative'
      ? null
      : response.gameplay.director?.proposal || null
    rulesetCombatStatus.value = String(response.gameplay.combat?.status || 'none')
    rulesetCampaignStatus.value = String(response.gameplay.campaign?.tutorial?.status || '')
    const combatActive = response.gameplay.combat?.status === 'active'
    const step = response.gameplay.campaign?.tutorial?.current_step
    const narrativeEncounterPending = response.gameplay.encounter_request?.status === 'pending'
    const encounterPending = Boolean(
      step?.requires === 'combat_ended'
      && !response.gameplay.campaign?.tutorial?.requirement_met,
    )
    const phase = combatActive ? 'active' : encounterPending || narrativeEncounterPending ? 'pending' : ''
    if (phase && phase !== previousEncounterPhase) activeRulesetTool.value = 'combat'
    previousEncounterPhase = phase
  } catch {
    if (sequence !== rulesetToolSequence || game.currentGame.value !== gameKey) return
    // The normal play surface remains usable if the optional tool state cannot sync.
    directorProposal.value = null
    rulesetGameplay.value = null
    rulesetCombatStatus.value = 'none'
    rulesetCampaignStatus.value = ''
  }
}

function resetRulesetToolPolling(): void {
  rulesetToolSequence += 1
  if (rulesetToolPoll) window.clearInterval(rulesetToolPoll)
  rulesetToolPoll = undefined
  previousEncounterPhase = ''
  directorProposal.value = null
  rulesetGameplay.value = null
  rulesetCombatStatus.value = 'none'
  rulesetCampaignStatus.value = ''
  if (!hasAuthoritativeCombat.value || !game.currentGame.value) return
  void syncEncounterTool()
  rulesetToolPoll = window.setInterval(() => {
    if (document.visibilityState !== 'hidden') void syncEncounterTool()
  }, 30000)
}
const rulesetCombatActive = computed(() => rulesetGameplay.value?.combat?.status === 'active')
const canEditOwnPortrait = computed(() => Boolean(
  actorId.value
  && game.player.value?.user_id === actorId.value
  && (!preview.value || delegate.value),
))
const pendingLuckDecisions = computed(() => game.detail.value?.pending_luck_decisions || [])
const revealChecks = computed(() => game.detail.value?.round_check_results || pendingLuckDecisions.value)
const serverJudging = computed(() => game.detail.value?.state === 'active_judgment' && !pendingLuckDecisions.value.length)
const showGmThinking = computed(() => gmThinking.value || serverJudging.value)
const sceneTitle = computed(() => game.detail.value?.scene || t('unknownScene'))
const stateLabel = computed(() => {
  if (showGmThinking.value) return t('gmThinking')
  if (pendingLuckDecisions.value.length) return t('luckDecisionState')
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
const tokenBudgetHint = computed(() => {
  if (!game.isGm.value) return ''
  const bump = game.detail.value?.token_budget_bump
  if (!bump || bump.to <= bump.from) return ''
  return t('tokenBudgetBumped', { from: bump.from, to: bump.to })
})
const tableNotice = computed(() => {
  if (showGmThinking.value) return t('gmProcessingNotice')
  if (pendingLuckDecisions.value.length) {
    const own = pendingLuckDecisions.value.some(check => check.actor_uid === actorId.value)
    if (own || game.isGm.value) return t('luckDecisionOwnNotice')
    const names = joinNames(pendingLuckDecisions.value.map(check => String(check.actor_name || check.actor_uid || '')))
    return t('luckDecisionWaitingNotice', { names })
  }
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

function openPortraitEditor() {
  if (!canEditOwnPortrait.value || !game.player.value) return
  const current = game.player.value.character_sheet?.portrait
  portraitDraft.value = current ? { ...current } : null
  showPortraitEditor.value = true
}

async function savePortrait() {
  if (!canEditOwnPortrait.value || !game.currentGame.value || !actorId.value) return
  portraitBusy.value = true
  try {
    const suffix = hasRulesAwareCharacters.value ? '/profile' : ''
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(actorId.value)}${suffix}`, {
      method: hasRulesAwareCharacters.value ? 'PATCH' : 'PUT',
      body: JSON.stringify({ portrait: portraitDraft.value ? { ...portraitDraft.value } : null }),
    })
    await game.refresh(true)
    showPortraitEditor.value = false
    toast.success(t('avatarSaved'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally { portraitBusy.value = false }
}

async function onCharacterCenterSaved(_character?: CharacterSheet, reason?: 'profile' | 'rest') {
  showCharacterCenter.value = false
  await game.refresh(true)
  toast.success(reason === 'rest' ? t('restCompleted') : t('characterProfileSaved'))
}
async function onCharacterCenterRestPending() {
  await game.refresh(true)
}

let sceneImageSequence = 0
async function refreshPlaySceneImage() {
  const gameKey = game.currentGame.value
  const fallbackRule = String(game.detail.value?.rule_id || ruleMeta.value.rule_id || '')
  if (!gameKey) return
  const sequence = ++sceneImageSequence
  const resolved = await resolveGameSceneImageUrl(gameKey, fallbackRule)
  if (sequence !== sceneImageSequence) {
    revokeSceneImageUrl(resolved)
    return
  }
  const previous = playSceneImageUrl.value
  playSceneImageUrl.value = resolved
  if (previous !== resolved) revokeSceneImageUrl(previous)
}

async function openSceneImageEditor() {
  if (!game.currentGame.value) return
  sceneImageDraft.value = null
  showSceneImageEditor.value = true
  const previous = sceneImageDefaultUrl.value
  sceneImageDefaultUrl.value = await resolveGameSceneImageUrl(
    game.currentGame.value,
    String(game.detail.value?.rule_id || ruleMeta.value.rule_id || ''),
    true,
  )
  if (previous !== sceneImageDefaultUrl.value) revokeSceneImageUrl(previous)
}

function closeSceneImageEditor() {
  showSceneImageEditor.value = false
  sceneImageDraft.value = null
}

async function saveSceneImage() {
  if (!game.currentGame.value) return
  sceneImageBusy.value = true
  try {
    const payload: Record<string, unknown> = sceneImageDraft.value
      ? { file_data: await fileToBase64(sceneImageDraft.value), file_name: sceneImageDraft.value.name }
      : { use_default: true }
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(game.currentGame.value)}/scene-image`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
    if (result.ok === false || result.error) throw new Error(result.error || t('operationFailed'))
    await game.refresh(true)
    await refreshPlaySceneImage()
    closeSceneImageEditor()
    toast.success(t('sceneImageSaved'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    sceneImageBusy.value = false
  }
}

function openMapBackgroundEditor() {
  showMapBackgroundEditor.value = true
}

async function onMapBackgroundSaved() {
  await game.refresh(true)
}

async function onLuckDecision(check: CheckResult, spend: boolean) {
  const checkId = String(check.check_id || '')
  if (!checkId || luckBusyId.value) return
  const accepted = await confirm({
    title: spend ? t('luckSpendConfirmTitle') : t('luckDeclineConfirmTitle'),
    content: spend
      ? t('luckSpendConfirm', { cost: check.luck_cost || 0 })
      : t('luckDeclineConfirm'),
    positiveText: spend ? t('spendLuckForSuccess', { cost: check.luck_cost || 0 }) : t('keepFailure'),
    type: spend ? 'warning' : 'info',
  })
  if (!accepted) return
  luckBusyId.value = checkId
  const resolvingRound = pendingLuckDecisions.value.length === 1
  if (resolvingRound) gmThinking.value = true
  try {
    const result = await api<LuckDecisionResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/checks/${encodeURIComponent(checkId)}/luck`, {
      method: 'POST',
      body: JSON.stringify({ spend }),
    })
    if (result.ok === false || result.error) throw new Error(result.error || t('operationFailed'))
    toast.success(t('luckDecisionSaved'))
    await game.refresh()
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    luckBusyId.value = ''
    if (resolvingRound) gmThinking.value = false
  }
}
async function command(path: string, body: JsonObject = {}) {
  const thinkingCommand = path === 'advance'
  if (thinkingCommand) gmThinking.value = true
  try {
    const r = await api<CommandResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/${path}`, { method: 'POST', body: JSON.stringify(body) })
    if (r.error) { toast.error(r.error); return }
    if (r.forced_waiting?.length) toast.info(t('forcedWaitingToast', { names: r.forced_waiting.join(t('listSeparator')) }))
    // 剧情正文已在时间线/对话区展示，不再用 toast 重复弹出全文
    if (path !== 'advance' && r.narration) toast.success(r.narration)
    else toast.success(t('operationDone'))
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) } finally { if (thinkingCommand) gmThinking.value = false }
}

async function generateStoryRecap() {
  if (storyRecapBusy.value || !game.currentGame.value) return
  storyRecapBusy.value = true
  try {
    const result = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/story-recap`, {
      method: 'POST',
      body: '{}',
    })
    if (result.ok === false || result.error) throw new Error(result.error || t('operationFailed'))
    toast.success(t('storyRecapGenerated'))
    await game.refresh()
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    storyRecapBusy.value = false
  }
}

function onCommand(text: string) { command('gm-command', { command: text }) }
function onPerception(uid: string, text: string) { command('private-message', { user_id: uid, text }) }
function onMode() { command('mode', { solo: !game.detail.value?.solo_mode }) }
function onNarrativePerspective(perspective: 'auto' | 'immersive' | 'third_person') {
  command('settings/narrative-perspective', { perspective })
}
async function onAdvancementControl(payload: Record<string, string | number>) {
  if (!game.currentGame.value) return
  try {
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(game.currentGame.value)}/advancement/control`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
    if (result.ok === false || result.error) throw new Error(result.error || t('operationFailed'))
    await game.refresh(true)
    toast.success(t('advancementSaved'))
  } catch (cause: unknown) {
    toast.error(errorMessage(cause))
  }
}
function onAccess() { command('player-access', { open: game.detail.value?.player_access_open === false }) }

function onRoomPassword() {
  roomPasswordInput.value = ''
  luckTimeoutInput.value = ''
  showRoomPassword.value = true
}
async function setRoomPassword() {
  try {
    const r = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/room-password`, { method: 'POST', body: JSON.stringify({ password: roomPasswordInput.value }) })
    if (r.error || r.ok === false) throw new Error(r.error || t('settingFailed'))
    // 同弹窗一并设置幸运超时（仅在填写时）
    const lt = String(luckTimeoutInput.value || '').trim()
    if (lt !== '') {
      const ltR = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/settings/luck-timeout`, { method: 'POST', body: JSON.stringify({ seconds: Number(lt) }) })
      if (ltR.error || ltR.ok === false) throw new Error(ltR.error || t('settingFailed'))
      toast.success(t('luckTimeoutSaved', { seconds: lt }))
    }
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
  await copyToClipboard(buildJoinLink(
    game.currentGame.value,
    settings.config.public_base_url || (isStandaloneFrontend() ? location.origin : undefined),
    undefined,
    currentBackendUrl(),
  ))
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
    const gameLanguage = contentLanguageOf({ language: game.detail.value?.language || locale.value })
    const templates = filterByContentLanguage(templateData.templates || [], gameLanguage)
    const loreWorlds = filterByContentLanguage(worldData.worlds || [], gameLanguage)
    const seen = new Set<string>()
    const candidates: WorldCandidate[] = []
    for (const template of templates) {
      const id = template.world_id || template.id
      if (!id) continue
      seen.add(id)
      candidates.push({ id, name: template.world_name || template.name || id, description: template.description || '', source: t('templateSource'), default_rule: template.default_rule || '', entry_count: undefined })
    }
    for (const w of loreWorlds) {
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

// Reset clears the player roster.  Give the GM a direct way back to the
// normal join flow instead of requiring them to copy an invite link first.
function createCharacterForCurrentGame() {
  router.push({ name: 'join', query: { game: game.currentGame.value, share: '1' } })
}

async function selectCard(card: CharacterCard) {
  if (characterCardNeedsConversion(card, ruleMeta.value.rule_id)) {
    toast.error(t('cardRuleMismatchManage'))
    return
  }
  try {
    if (hasRulesAwareCharacters.value) {
      await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(actorId.value)}/adopt-card`, {
        method: 'POST',
        body: JSON.stringify({ card_id: String(card.card_id || card.id || '') }),
      })
    } else {
      await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(actorId.value)}`, { method: 'PUT', body: JSON.stringify(card) })
    }
    showCards.value = false
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function allocateLevelUp(attrs: Record<string, number>) {
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(actorId.value)}`, { method: 'PUT', body: JSON.stringify({ attributes: attrs }) })
    toast.success(t('attributePointsAllocated'))
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
  await copyToClipboard(buildJoinLink(
    game.currentGame.value,
    settings.config.public_base_url || (isStandaloneFrontend() ? location.origin : undefined),
    uid,
    currentBackendUrl(),
  ))
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
const payResolving = ref(false)
const dismissedPaymentIds = ref<Set<string>>(new Set())
const economyCurrencyName = computed(() => currencyLabel(ruleMeta.value))
const economyProposalList = computed(() => game.detail.value?.economy_proposals || [])
const actionableEconomyProposals = computed(() => economyProposalList.value.filter(proposal => (
  isEconomyProposalActionable(
    proposal,
    actorId.value,
    String(game.detail.value?.gm_uid || ''),
  )
)))
const pendingEconomyCount = computed(() => actionableEconomyProposals.value.length)

watch(actionableEconomyProposals, (list) => {
  const activeIds = new Set(list.map(item => String(item.id || item.payment_id || '')).filter(Boolean))
  const currentId = String(pendingPay.value?.id || pendingPay.value?.payment_id || '')
  if (currentId && !activeIds.has(currentId)) pendingPay.value = null
  dismissedPaymentIds.value = new Set(
    [...dismissedPaymentIds.value].filter(id => activeIds.has(id)),
  )
  const mine = nextEconomyProposal(
    list,
    actorId.value,
    String(game.detail.value?.gm_uid || ''),
    dismissedPaymentIds.value,
  )
  const mineId = String(mine?.id || mine?.payment_id || '')
  if (mine && (!pendingPay.value || currentId !== mineId)) pendingPay.value = mine
}, { immediate: true, deep: true })

function economyPlayerName(uid?: string): string {
  if (!uid) return '—'
  return game.players.value.find(player => player.user_id === uid)?.character_name || uid
}

function isTeamPayment(proposal: PendingPayment | null): boolean {
  return proposal?.approval_policy === 'all_contributors'
}

function postponePendingPay() {
  const id = String(pendingPay.value?.id || pendingPay.value?.payment_id || '')
  if (id) dismissedPaymentIds.value = new Set([...dismissedPaymentIds.value, id])
  pendingPay.value = null
}

function pendingEconomyDismissLabel(proposal: PendingPayment): string {
  return isNonBlockingPersonalPurchase(proposal) ? t('economyPostpone') : t('economyViewLater')
}

function pendingEconomyHelp(proposal: PendingPayment): string {
  if (isNonBlockingPersonalPurchase(proposal)) return t('economyPersonalPurchaseHelp')
  return proposal.kind === 'reward'
    ? t('economyRewardHelp')
    : isTeamPayment(proposal) ? t('economyTeamPaymentHelp') : t('gmPaymentHelp')
}

function reopenPendingEconomy() {
  const proposal = actionableEconomyProposals.value[0]
  if (!proposal) return
  const id = String(proposal.id || proposal.payment_id || '')
  if (id) {
    const next = new Set(dismissedPaymentIds.value)
    next.delete(id)
    dismissedPaymentIds.value = next
  }
  pendingPay.value = proposal
}

async function resolvePay(accepted: boolean) {
  const p = pendingPay.value
  if (!p || !p.id || payResolving.value) return
  payResolving.value = true
  try {
    const result = await api<{ committed?: boolean; awaiting_uids?: string[] }>(`/games/${encodeURIComponent(game.currentGame.value)}/payments/${encodeURIComponent(p.id)}`, { method: 'POST', body: JSON.stringify({ accepted }) })
    const resolvedId = String(p.id || p.payment_id || '')
    if (resolvedId) {
      const next = new Set(dismissedPaymentIds.value)
      next.delete(resolvedId)
      dismissedPaymentIds.value = next
    }
    pendingPay.value = null
    await game.refresh()
    toast.success(accepted && result.committed === false
      ? t('economyApprovalRecorded')
      : accepted
      ? (p.kind === 'reward' ? t('economyRewardApproved') : t('paid'))
      : (p.kind === 'reward' ? t('economyRewardRejected') : t('paymentRejected')))
  } catch (e: unknown) {
    toast.error(errorMessage(e))
    // 刷新 detail：若后端已自动取消该支付（如金币不足），pending 消失后 watch 不再重弹
    pendingPay.value = null
    await game.refresh().catch(() => undefined)
  } finally {
    payResolving.value = false
  }
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
  if (route.query.share && !route.query.user && !hasAccessToken()) {
    router.replace({ name: 'join', query: { game: game.currentGame.value, share: '1' } })
    return
  }
  // A player may return to a previously bookmarked /play URL after joining
  // through the connection flow.  Restore the local player identity before
  // loading actions; otherwise requests have no user query and the backend
  // correctly rejects them as "not joined".
  let memberValidated = false
  if (!route.query.user && !hasAccessToken()) {
    const storedUid = localStorage.getItem('trpg_play_user_' + game.currentGame.value) || ''
    if (storedUid) {
      try {
        const d = await api<GameDetail>(`/games/${encodeURIComponent(game.currentGame.value)}`)
        if (isStoredPlayerMember(d, storedUid)) {
          memberValidated = true
          // Fall through to the normal load below instead of returning, so
          // ruleMeta / player-context / health load with the restored identity.
          await router.replace({ name: 'play', query: { ...route.query, game: game.currentGame.value, user: storedUid, share: '1' } })
        }
      } catch {
        // Keep the existing page if the identity check is temporarily unavailable.
      }
    }
  }
  // 被踢/身份过期的玩家直连 play 链接时，SSE 与私聊接口会持续 403「未加入本局」。
  // 先独立校验一次成员资格（不依赖 refresh，因其 private-log 403 会中断整组请求），
  // 失效则清掉本地身份缓存，送回加入页走重新加入（GM 有 access_token，不受影响）。
  const linkUid = queryString(route.query.user)
  if (linkUid && !hasAccessToken() && !memberValidated) {
    try {
      const d = await api<GameDetail>(`/games/${encodeURIComponent(game.currentGame.value)}`)
      if (!isStoredPlayerMember(d, linkUid)) {
        localStorage.removeItem('trpg_play_user_' + game.currentGame.value)
        router.replace({ name: 'join', query: { game: game.currentGame.value, share: '1' } })
        return
      }
    } catch {
      // 校验请求本身失败（网络抖动/后端重启）：保持旧行为留在本页，下次刷新重试。
    }
  }
  if (!route.query.user) {
    try {
      await api(`/games/${encodeURIComponent(game.currentGame.value)}/claim-gm`, { method: 'POST', body: '{}' })
    } catch (e: unknown) {
      if (!isNotFoundError(e)) game.error.value = errorMessage(e)
    }
  }
  await game.refresh()
  if (!game.currentGame.value) return
  game.connect()
  try {
    const healthRequest: Promise<HealthResponse> = game.isGm.value ? api<HealthResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/health?include_resolved=true`) : Promise.resolve({ events: [] })
    const [chars, context, h] = await Promise.all([
      api<CharacterListResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/characters`),
      api<PlayerContextResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/player-context`).catch(() => ({ preview: false })),
      healthRequest,
    ])
    ruleMeta.value = {
      ...(chars.rule_meta || {}),
      attributes: (chars.rule_attrs || []).map(a => ({ key: a.key, name: a.name, name_en: a.name_en, min: a.min, max: a.max })),
      rule_special_stats: chars.rule_special_stats || [],
    }
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
watch(
  [() => game.currentGame.value, hasAuthoritativeCombat],
  resetRulesetToolPolling,
  { immediate: true },
)
watch(() => game.detail.value?.round_number, () => {
  if (hasAuthoritativeCombat.value) void syncEncounterTool()
})
watch(() => game.rulesetStateSignal.value, () => {
  if (!hasAuthoritativeCombat.value) return
  rulesetRefreshKey.value += 1
  void syncEncounterTool()
})
watch(
  [() => game.currentGame.value, () => JSON.stringify(game.detail.value?.scene_image || {})],
  () => { refreshPlaySceneImage() },
  { immediate: true },
)
watch(() => game.detail.value?.solo_mode, (solo, prev) => {
  if (solo === undefined || solo === prev) return
  if (solo) {
    railCollapsed.value = true
  } else {
    const stored = localStorage.getItem('play_rail_collapsed')
    railCollapsed.value = stored !== null ? stored === '1' : false
  }
}, { immediate: true })
watch(showGmThinking, (thinking) => {
  if (!thinking && game.liveNarration.value) game.liveNarration.value = ''
})
onBeforeUnmount(() => {
  if (rulesetToolPoll) window.clearInterval(rulesetToolPoll)
  sceneImageSequence += 1
  revokeSceneImageUrl(playSceneImageUrl.value)
  revokeSceneImageUrl(sceneImageDefaultUrl.value)
})
</script>

<template>
  <main
    v-if="game.currentGame.value"
    class="play-page play-page-immersive"
    :style="sceneImageStyle(playSceneImageUrl)"
  >
    <header class="topbar play-hud">
      <div class="play-hud-main">
        <button v-if="!isPlayer" class="icon play-back" :title="t('backToOverview')" @click="goBack">←</button>
        <div class="play-titleblock">
          <span class="play-eyebrow">{{ roleLabel }} · {{ tableMode }}</span>
          <h1>{{ game.detail.value?.world_name || 'DiceFrame' }}</h1>
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
        <button
          v-if="pendingEconomyCount && !pendingPay"
          class="play-economy-pending"
          @click="reopenPendingEconomy"
        >{{ t('economyPendingAction', { count: pendingEconomyCount }) }}</button>
        <button
          v-if="game.isGm.value && !game.players.value.length"
          class="play-economy-pending"
          @click="createCharacterForCurrentGame"
        >{{ t('createCharacterAndEnter') }}</button>
        <label v-if="isPlayer" class="locale-select play-locale-select">
          <span>{{ t('language') }}</span>
          <select :value="locale" @change="onLocaleChange">
            <option value="zh-CN">{{ t('chinese') }}</option>
            <option value="en">{{ t('english') }}</option>
          </select>
        </label>
        <button class="play-secondary-action" @click="openCards">{{ t('characters') }}</button>
        <button class="play-secondary-action play-map-action" @click="openMap">{{ t('mapTitle') }}</button>
        <button class="play-secondary-action" @click="showSceneGallery = true">{{ t('sceneGallery') }}</button>
        <button class="play-secondary-action" @click="help = true">{{ t('help') }}</button>
        <button class="play-secondary-action play-refresh" @click="game.refresh()">{{ t('refresh') }}</button>
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
        :class="{ 'mobile-open': mobilePanel === 'sidebar' }"
        :detail="game.detail.value"
        :player="game.player.value"
        :private-messages="game.privateMessages.value"
        :map="game.map.value"
        :rule-meta="ruleMeta"
        :collapsed="sidebarCollapsed"
        :portrait-editable="canEditOwnPortrait"
        :show-advanced-center="hasRulesAwareCharacters"
        @lore-click="onLoreClick"
        @open-map="openMap"
        @toggle-sidebar="toggleSidebarPanel"
        @portrait-click="openPortraitEditor"
        @allocate-level-up="allocateLevelUp"
        @open-character-center="showCharacterCenter = true"
      />

      <KpQuestionDialog
        v-if="showKpQuestion && canAskKp"
        :game-key="game.currentGame.value"
        @close="showKpQuestion = false"
        @shared="game.refresh(true)"
      />

      <Modal
        v-if="showCharacterCenter && game.player.value && hasRulesAwareCharacters"
        :title="t('advancedCharacterCenter')"
        dialog-class="professional-character-dialog"
        @close="showCharacterCenter = false"
      >
        <RulesetCharacterCenterHost
          :runtime-id="String(game.detail.value.ruleset_runtime?.id || '')"
          :character="game.player.value.character_sheet || {}"
          target="game"
          :rule-id="String(ruleMeta.rule_id || game.detail.value.rule_id || '')"
          :language="String(locale)"
          :game-key="game.currentGame.value"
          :user-id="actorId"
          :rest-session="game.detail.value.rest_session"
          @saved="onCharacterCenterSaved"
          @rest-pending="onCharacterCenterRestPending"
          @cancel="showCharacterCenter = false"
        />
      </Modal>

      <Modal v-if="showPortraitEditor && game.player.value" :title="t('changeAvatar')" @close="showPortraitEditor = false">
        <PortraitPicker
          v-model="portraitDraft"
          :rule-id="String(ruleMeta?.rule_id || '')"
          :seed="actorId"
          :name="game.player.value.character_name"
        />
        <template #actions>
          <button :disabled="portraitBusy" @click="showPortraitEditor = false">{{ t('cancel') }}</button>
          <button class="primary" :disabled="portraitBusy" @click="savePortrait">{{ portraitBusy ? t('savingAvatar') : t('saveAction') }}</button>
        </template>
      </Modal>

      <Modal v-if="showSceneImageEditor" :title="t('sceneImageManage')" @close="closeSceneImageEditor">
        <p class="muted">{{ t('sceneImageGmHint') }}</p>
        <AdventureSceneImagePicker v-model="sceneImageDraft" :default-url="sceneImageDefaultUrl" />
        <template #actions>
          <button :disabled="sceneImageBusy" @click="closeSceneImageEditor">{{ t('cancel') }}</button>
          <button class="primary" :disabled="sceneImageBusy" @click="saveSceneImage">{{ sceneImageBusy ? t('saving') : t('saveAction') }}</button>
        </template>
      </Modal>

      <MapBackgroundSettingsModal
        :open="showMapBackgroundEditor"
        :game-key="game.currentGame.value"
        :map="game.map.value"
        @close="showMapBackgroundEditor = false"
        @saved="onMapBackgroundSaved"
      />

      <RulesetPlayHost
        v-if="activeRulesetTool"
        :runtime-id="String(game.detail.value.ruleset_runtime?.id || '')"
        :active-tool="activeRulesetTool"
        :has-campaign="hasCampaignGuidance"
        :has-combat="hasAuthoritativeCombat"
        :game-key="game.currentGame.value"
        :actor-id="actorId"
        :character-name="game.player.value?.character_name || ''"
        :scene-name="sceneTitle"
        :world-name="game.detail.value.world_name || game.detail.value.world_id || ''"
        :map="game.map.value"
        :is-gm="game.isGm.value"
        :refresh-key="rulesetRefreshKey"
        @close="activeRulesetTool = ''"
        @refresh="refreshRulesetPanels"
        @navigate="navigateRulesetTool"
        @open-map="openMap"
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

        <DirectorProposalCard
          v-if="directorProposal"
          :proposal="directorProposal"
          :is-gm="game.isGm.value"
          @open-campaign="openRulesetTool('campaign')"
          @open-combat="openRulesetTool('combat')"
        />

        <GameTimeline
          :log="game.log.value"
          :live="game.detail.value.multiplayer?.submitted_actions || []"
          :players="game.players.value"
          :round="game.detail.value.round_number || 0"
          :lore="game.lore.value"
          :game-key="game.currentGame.value"
          :rule-id="String(ruleMeta?.rule_id || '')"
          :processing="showGmThinking"
          :is-gm="game.isGm.value"
          :live-narration="game.liveNarration.value"
          :pending-checks="pendingLuckDecisions"
          :reveal-checks="revealChecks"
          :current-user-id="actorId"
          :luck-busy-id="luckBusyId"
          @refresh="game.refresh"
          @luck="onLuckDecision"
        />

        <EconomyProposalCard
          v-if="pendingPay"
          :proposal="pendingPay"
          :busy="payResolving"
          :currency="economyCurrencyName"
          :player-name="economyPlayerName"
          :dismiss-label="pendingEconomyDismissLabel(pendingPay)"
          :help="pendingEconomyHelp(pendingPay)"
          :solo="Boolean(game.detail.value?.solo_mode)"
          @dismiss="postponePendingPay"
          @reject="resolvePay(false)"
          @confirm="resolvePay(true)"
        />

        <div v-if="tableNotice" class="table-notice notice">{{ tableNotice }}</div>
        <p v-if="tokenBudgetHint" class="token-budget-hint" aria-live="polite">{{ tokenBudgetHint }}</p>

        <TableTalkFeed :exchanges="game.tableTalk.value" />

        <CombatMessageComposer
          v-if="rulesetCombatActive && rulesetGameplay"
          :game-key="game.currentGame.value"
          :gameplay="rulesetGameplay"
          @refresh="refreshRulesetPanels"
          @open-combat="openRulesetTool('combat')"
        >
          <template #tools>
            <div v-if="hasProfessionalTools || canAskKp" class="ruleset-context-tools" :aria-label="rulesetToolCopy.menu">
              <button
                v-if="canAskKp"
                class="kp-question-tool-trigger"
                type="button"
                :title="t('kpQuestionHint')"
                :aria-label="t('kpQuestionAction')"
                @click="showKpQuestion = true"
              ><NIcon :component="ChatbubbleEllipsesOutline" /><span>{{ t('kpQuestionAction') }}</span></button>
              <button
                v-if="hasCampaignGuidance"
                class="campaign-tool-trigger"
                data-testid="dnd5e-campaign-tool"
                type="button"
                :title="rulesetToolCopy.campaign"
                :aria-label="rulesetToolCopy.campaign"
                @click="openRulesetTool('campaign')"
              ><NIcon :component="BookOutline" /><span>{{ rulesetToolCopy.campaign }}</span></button>
              <button
                v-if="hasAuthoritativeCombat"
                class="combat-tool-trigger"
                data-testid="dnd5e-combat-tool"
                type="button"
                :title="rulesetToolCopy.combat"
                :aria-label="rulesetToolCopy.combat"
                @click="openRulesetTool('combat')"
              ><NIcon :component="ShieldOutline" /><span>{{ rulesetToolCopy.combat }}</span></button>
            </div>
          </template>
        </CombatMessageComposer>

        <ActionComposer
          v-else
          :game-key="game.currentGame.value"
          :user-id="actorId"
          :detail="game.detail.value"
          :disabled="(preview && !delegate) || !!pendingLuckDecisions.length"
          @processing="gmThinking = $event"
          @refresh="game.refresh"
        >
          <template #tools>
            <div v-if="hasProfessionalTools || canAskKp" class="ruleset-context-tools" :aria-label="rulesetToolCopy.menu">
              <button
                v-if="canAskKp"
                class="kp-question-tool-trigger"
                type="button"
                :title="t('kpQuestionHint')"
                :aria-label="t('kpQuestionAction')"
                @click="showKpQuestion = true"
              ><NIcon :component="ChatbubbleEllipsesOutline" /><span>{{ t('kpQuestionAction') }}</span></button>
              <button
                v-if="hasCampaignGuidance"
                class="campaign-tool-trigger"
                data-testid="dnd5e-campaign-tool"
                type="button"
                :title="rulesetToolCopy.campaign"
                :aria-label="rulesetToolCopy.campaign"
                @click="openRulesetTool('campaign')"
              ><NIcon :component="BookOutline" /><span>{{ rulesetToolCopy.campaign }}</span></button>
              <button
                v-if="hasAuthoritativeCombat"
                class="combat-tool-trigger"
                data-testid="dnd5e-combat-tool"
                type="button"
                :title="rulesetToolCopy.combat"
                :aria-label="rulesetToolCopy.combat"
                @click="openRulesetTool('combat')"
              ><NIcon :component="ShieldOutline" /><span>{{ rulesetToolCopy.combat }}</span></button>
            </div>
          </template>
        </ActionComposer>
      </section>

      <aside
        v-if="game.isGm.value || game.detail.value.solo_mode === false"
        class="play-control-rail"
        :class="{ collapsed: railCollapsed, 'mobile-open': mobilePanel === 'controls' }"
      >
        <button class="rail-toggle" @click="toggleRailPanel" :title="mobilePanel === 'controls' ? t('close') : railCollapsed ? t('expandGmControls') : t('collapseGmControls')">
          <NIcon :component="railCollapsed ? ChevronBack : ChevronForward" size="16" />
        </button>
        <GmToolbar
          v-if="game.isGm.value"
          :detail="game.detail.value"
          :players="game.players.value"
          :is-gm="game.isGm.value"
          :recap-busy="storyRecapBusy"
          @advance="command('advance', { force: true })"
          @rollback="command('rollback')"
          @recap="generateStoryRecap"
          @invite="invite"
          @bot-bind="copyBotBind"
          @mode="onMode"
          @narrative-perspective="onNarrativePerspective"
          @advancement-control="onAdvancementControl"
          @access="onAccess"
          @command="onCommand"
          @perception="onPerception"
          @export="exportSave"
          @reset="lifecycle('reset')"
          @restart="lifecycle('restart')"
          @cards="openCards"
          @world-switch="openWorldSwitch"
          @room-password="onRoomPassword"
          @scene-image="openSceneImageEditor"
          @map-background="openMapBackgroundEditor"
          @payment="openPaymentComposer"
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
          @open-character-center="showCharacterCenter = true"
        />

      <HealthPanel v-if="game.isGm.value" :health="health" :detail="game.detail.value" :is-gm="game.isGm.value" @resolve="resolveHealth" />
      </aside>
      <button
        v-if="mobilePanel"
        class="play-drawer-backdrop"
        :aria-label="t('close')"
        @click="mobilePanel = ''"
      />
    </div>
    <GmChargeComposer
      v-if="showPaymentComposer"
      :players="game.players.value"
      :payer-uid="composerPayerUid"
      :recipient-uid="composerRecipientUid"
      :busy="paymentBusy"
      @close="showPaymentComposer = false"
      @submit="createPaymentProposal"
    />
    <MapWorkspace
      v-if="showMap"
      :map="game.map.value"
      :current-scene="game.detail.value?.scene"
      @close="showMap = false"
    />
    <SceneGalleryModal
      v-if="showSceneGallery"
      open
      :game-key="game.currentGame.value || ''"
      :is-gm="game.isGm.value"
      @close="showSceneGallery = false"
      @background-saved="refreshMapAfterBackground"
    />
    <button
      class="mobile-drawer-trigger mobile-drawer-trigger-left"
      :class="{ hidden: mobilePanel === 'sidebar' }"
      :aria-label="t('mobileStatusLabel')"
      :title="t('mobileStatusLabel')"
      @click="toggleMobilePanel('sidebar')"
    ><NIcon :component="StatsChartOutline" /></button>
    <button
      v-if="!showMap"
      class="mobile-drawer-trigger mobile-drawer-trigger-left mobile-drawer-trigger-map"
      :aria-label="t('mobileMapLabel')"
      :title="t('mobileMapLabel')"
      @click="openMap"
    ><NIcon :component="MapOutline" /></button>
    <button
      v-if="game.isGm.value || game.detail.value?.solo_mode === false"
      class="mobile-drawer-trigger mobile-drawer-trigger-right"
      :class="{ hidden: mobilePanel === 'controls' }"
      :aria-label="t('mobileConsoleLabel')"
      :title="t('mobileConsoleLabel')"
      @click="toggleMobilePanel('controls')"
    ><NIcon :component="TerminalOutline" /></button>
    <PlayHelpCenter
      v-if="help"
      :meta="ruleMeta"
      :is-dnd="hasProfessionalTools"
      :scene="sceneTitle"
      :combat-status="rulesetCombatStatus"
      :campaign-status="rulesetCampaignStatus"
      :multiplayer="game.detail.value?.solo_mode === false"
      @close="help = false"
    />

    <div v-if="showCards" class="modal" @click.self="showCards = false">
      <section class="dialog">
        <header><h2>{{ t('sharedCharacterLibrary') }}</h2><button @click="showCards = false">×</button></header>
        <p>{{ t('replaceCharacterHint') }}</p>
        <button v-for="c in cards" :key="c.character_name" class="card-choice" @click="selectCard(c)">
          <strong>{{ c.character_name }}</strong><span>{{ characterCardRuleName(c, t('unboundRule')) }} · {{ c.race }} · {{ c.class }}</span>
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
        <label>{{ t('luckTimeoutSeconds') }}<input type="number" v-model="luckTimeoutInput" :placeholder="t('luckTimeoutPlaceholder')" min="0" max="3600"></label>
        <div class="actions">
          <button @click="showRoomPassword = false">{{ t('cancel') }}</button>
          <button class="primary" @click="setRoomPassword">{{ t('saveAction') }}</button>
        </div>
      </section>
    </div>

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
