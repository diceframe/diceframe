<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage, retryOnRateLimit } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterListResponse, CharacterPortrait, CharacterSheet, CharacterSkill, GameDetail, Player, PlayerCreateResponse, RuleAttribute, RuleMeta, RulesetRuntimeMeta } from '@/api/types'
import { rememberCurrentGame } from '@/stores/gameContext'
import { isStoredPlayerMember } from '@/utils/joinIdentity'
import { ROOM_TOKEN_REJECTED_EVENT, SEAT_TOKEN_HEADER, readRoomToken, readSeatToken, storeRoomToken, storeSeatToken } from '@/utils/seatToken'
import { attrDisplayName, suggestedAttributes, skillPointCost } from '@/utils/ruleSchema'
import { useLocale, type Locale } from '@/composables/useLocale'
import { UI_LOCALES, uiLocaleLabel } from '@/i18n'
import { useConfirm } from '@/composables/useConfirm'
import { characterCardNeedsConversion, characterCardRuleName } from '@/utils/characterCards'
import { joinSkillsPayload } from '@/features/player/joinSkills'
import { activePeerGameClient } from '@/peer/game/bridge'
import { usePeerSessionStore } from '@/peer/store/peerSession'
import { friendlyPeerDetail } from '@/features/peer/friendlyDetail'
import { buildSpecialStats, playerColor, primaryResourceList } from '@/utils/play'
import PortraitPicker from '@/components/admin/PortraitPicker.vue'
import PortraitImage from '@/components/PortraitImage.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import RulesetExperienceHost from '@/features/rulesets/RulesetExperienceHost.vue'

interface JoinSkill { name: string; value: string | number; effect?: string }
interface JoinForm {
  character_name: string
  race: string
  class: string
  hp: string | number
  background: string
  attributes: Record<string, number>
  skills: JoinSkill[]
  identity?: Record<string, unknown>
  portrait?: CharacterPortrait | null
  [key: string]: unknown
}

const route = useRoute(), router = useRouter()
const { locale, setLocale, t } = useLocale()
const { confirm } = useConfirm()
const gameKey = computed(() => String(route.query.game || ''))
// A pre-token takeover link named the seat by its public uid; it no longer
// proves anything, so it only tells the player to ask for a new link.
const legacyLinkUser = computed(() => route.query.user ? String(route.query.user) : '')
const detail = ref<Partial<GameDetail>>({})
const partyPlayers = ref<Player[]>([])
const attrs = ref<RuleAttribute[]>([])
const attrTotal = ref(0)
const ruleMeta = ref<RuleMeta>({})
const cards = ref<CharacterCard[]>([])
const rulesetRuntime = ref<RulesetRuntimeMeta | null>(null)
const professionalInitial = ref<CharacterSheet | undefined>()
const professionalHostKey = ref(0)
const form = ref<JoinForm>({ character_name: '', race: '', class: '', hp: '', background: '', attributes: {}, skills: [{ name: '', value: '' }] })
// 效果说明默认折叠，避免技能多时铺开一排 textarea。
const openSkillEffects = ref<Set<number>>(new Set())
function toggleSkillEffect(i: number) {
  const next = new Set(openSkillEffects.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  openSkillEffects.value = next
}
const error = ref(''), busy = ref(false)
/** peer 会话恢复失败等场景的原始错误码转成人话；其它错误原样展示。 */
const displayError = computed(() => friendlyPeerDetail(error.value, t))
type JoinStage = 'password' | 'identity' | 'character' | 'enter'
const joinStage = computed<JoinStage>(() => {
  if (needRoomPassword.value) return 'password'
  if (resumeUser.value || !sheetReady.value) return 'identity'
  return 'character'
})
const joinStages = computed(() => [
  { key: 'password' as const, label: t('joinStepPassword') },
  { key: 'identity' as const, label: t('joinStepIdentity') },
  { key: 'character' as const, label: t('joinStepCharacter') },
  { key: 'enter' as const, label: t('joinStepEnter') },
])
const sheetReady = ref(false)
const needRoomPassword = ref(false), roomPasswordInput = ref('')
// Set while rejoining a seat through its token (takeover link).
const resumeUser = ref('')
const backgroundLimit = 8000
const usesProfessionalBuilder = computed(() => (
  rulesetRuntime.value?.capabilities.character_builder === 'professional'
))

const fallbackAttrs = computed<RuleAttribute[]>(() => [
  { key: 'str', name: t('attrStrength'), min: 1, max: 100 },
  { key: 'con', name: t('attrConstitution'), min: 1, max: 100 },
  { key: 'dex', name: t('attrDexterity'), min: 1, max: 100 },
  { key: 'int', name: t('attrIntelligence'), min: 1, max: 100 },
  { key: 'wis', name: t('attrWisdom'), min: 1, max: 100 },
  { key: 'cha', name: t('attrCharisma'), min: 1, max: 100 },
])

const attrSum = computed(() => Object.values(form.value.attributes || {}).reduce((sum, value) => sum + (Number(value) || 0), 0))
const inferredAttrTotal = computed(() => attrs.value.reduce((sum, attr) => {
  const min = Number(attr.min ?? 0) || 0
  const max = Number(attr.max ?? attr.min ?? 0) || min
  return sum + Math.floor((min + max) / 2)
}, 0))
const attrLimit = computed(() => attrTotal.value || Number(ruleMeta.value.attribute_points || 0) || inferredAttrTotal.value)
const attrRemaining = computed(() => Math.max(attrLimit.value, attrSum.value) - attrSum.value)
const attrOverLimit = computed(() => Boolean(attrLimit.value && attrSum.value > attrLimit.value))
const maxSkills = computed(() => Number(ruleMeta.value.max_skills || 0))
const skillPointTotal = computed(() => Number(ruleMeta.value.skill_point_total || 0))
const maxSkillValue = computed(() => Number(ruleMeta.value.max_skill_value || 0))
const filledSkills = computed(() => form.value.skills.filter(s => s.name.trim()))
const skillSpent = computed(() => filledSkills.value.reduce((sum, skill) => sum + skillPointCost(skill, ruleMeta.value), 0))
const skillOverLimit = computed(() =>
  Boolean((maxSkills.value && filledSkills.value.length > maxSkills.value)
    || (skillPointTotal.value && skillSpent.value > skillPointTotal.value)
    || (maxSkillValue.value && filledSkills.value.some(s => (Number(s.value || 0) || 0) > maxSkillValue.value)))
)
const diceHint = computed(() =>
  ruleMeta.value.mechanics === 'dnd5e_core'
    ? t('dndDiceHint')
    : ''
)

function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}

function skillToForm(skill: string | CharacterSkill): JoinSkill {
  if (typeof skill === 'string') return { name: skill, value: '' }
  const row: JoinSkill = { name: skill.name, value: skill.value ?? '' }
  const effect = String(skill.effect || '').trim()
  if (effect) row.effect = effect
  return row
}

/**
 * A takeover link carries the seat token in the hash route. Store it for this
 * game and drop it from the address bar right away, so it never lingers in
 * history, bookmarks or screenshots.
 */
function consumeSeatLink(): boolean {
  const seat = route.query.seat ? String(route.query.seat) : ''
  if (!seat || !gameKey.value) return false
  storeSeatToken(gameKey.value, seat)
  const query = { ...route.query }
  delete query.seat
  delete query.user
  router.replace({ name: 'join', query })
  return true
}

/** Whether this visit came from a takeover link (its seat token was just stored). */
let pendingSeatLink = false

/** The room token was refused mid-join (expired / password changed): ask again. */
function onRoomTokenRejected(event: Event) {
  const rejected = (event as CustomEvent<{ gameKey?: string }>).detail?.gameKey
  if (rejected !== gameKey.value) return
  roomPasswordInput.value = ''
  error.value = t('roomAccessExpired')
  needRoomPassword.value = true
}
window.addEventListener(ROOM_TOKEN_REJECTED_EVENT, onRoomTokenRejected)
onBeforeUnmount(() => window.removeEventListener(ROOM_TOKEN_REJECTED_EVENT, onRoomTokenRejected))

onMounted(async () => {
  const seatLink = consumeSeatLink()
  // P2P 直连局：刷新后先恢复 peer 会话（不落服务器 /games 接口）。
  if (route.query.peer === '1' && !activePeerGameClient()) {
    const peerSession = usePeerSessionStore()
    if (peerSession.hasPersistedSession()) {
      peerSession.restore(api)
      const savedGameKey = peerSession.gameKey || gameKey.value
      if (!peerSession.isHost) {
        // guest 等连接建立后向 host 重绑身份再进游玩页。
        watch(() => peerSession.state, async (next) => {
          if (next !== 'connected' && next !== 'error') return
          if (next === 'connected') await peerSession.rebindIdentity()
          rememberCurrentGame(savedGameKey)
          router.replace({
            name: 'play',
            query: { game: savedGameKey, share: '1', peer: '1' },
          })
        }, { immediate: true })
      } else {
        rememberCurrentGame(savedGameKey)
        router.replace({ name: 'play', query: { game: savedGameKey, peer: '1' } })
      }
      return
    }
  }
  pendingSeatLink = seatLink
  try {
    const d = await api<GameDetail>(`/games/${encodeURIComponent(gameKey.value)}`)
    detail.value = d
    // Behind a room password nothing else is decided until this browser holds
    // a room token: a returning member would only be bounced back from the
    // play page, and a newcomer cannot load the join data without one.
    if (d.has_room_password && !readRoomToken(gameKey.value)) {
      // The client sends a player here with notice=room when the token it
      // held was rejected (expired, or the GM changed the password).
      if (route.query.notice === 'room') error.value = t('roomAccessExpired')
      needRoomPassword.value = true
      return
    }
    await continueJoin(d)
  } catch (e: unknown) {
    // 详情获取失败：若本地有身份，按旧行为放行进游玩，避免成员被临时故障锁住。
    const stored = localStorage.getItem('trpg_play_user_' + gameKey.value)
    if (stored) {
      rememberCurrentGame(gameKey.value)
      router.replace({ name: 'play', query: { game: gameKey.value, user: stored, share: '1' } })
      return
    }
    error.value = errorMessage(e)
  }
})

async function continueJoin(d: Partial<GameDetail>) {
  const seatLink = pendingSeatLink
  const stored = localStorage.getItem('trpg_play_user_' + gameKey.value)
  if (stored && !seatLink) {
    // 校验本地身份是否仍是成员：被踢后缓存过期，不能再盲跳游玩界面。
    if (isStoredPlayerMember(d, stored)) {
      rememberCurrentGame(gameKey.value)
      router.replace({ name: 'play', query: { game: gameKey.value, user: stored, share: '1' } })
      return
    }
    // 被踢/身份过期：清掉本地缓存，走正常重新加入（尊重房间密码等门槛）。
    localStorage.removeItem('trpg_play_user_' + gameKey.value)
  }
  if (seatLink) resumeUser.value = 'seat'
  else if (legacyLinkUser.value) error.value = t('seatLinkExpired')
  else if (route.query.notice === 'seat') error.value = t('seatTokenMissing')
  await afterGate()
}

async function afterGate() {
  if (resumeUser.value) { await resumeSeat(); return }
  await loadGameData()
}

/** Rejoin the seat a takeover link's token belongs to (this device's session is rebound). */
async function resumeSeat() {
  const seatToken = readSeatToken(gameKey.value)
  if (!seatToken) {
    // Without the seat's token this would silently become a brand-new seat.
    error.value = t('seatTokenMissing')
    resumeUser.value = ''
    await loadGameData()
    return
  }
  busy.value = true; error.value = ''
  try {
    // Rejoining an existing seat is idempotent, so a transient rate limit is retried once.
    const r = await retryOnRateLimit(() => api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, {
      method: 'POST',
      // Sent explicitly so it also applies when the GM opens the link while logged in.
      headers: { [SEAT_TOKEN_HEADER]: seatToken },
      body: JSON.stringify({ join_as_new: false }),
    }))
    if (r.error) throw new Error(r.error)
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    storeSeatToken(gameKey.value, r.seat_token)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function loadGameData() {
  sheetReady.value = false
  try {
    const [c, k] = await Promise.all([
      api<CharacterListResponse>(`/games/${encodeURIComponent(gameKey.value)}/characters`),
      api<CharacterCardsResponse>(`/games/${encodeURIComponent(gameKey.value)}/character-cards`).catch(() => ({ cards: [] })),
    ])
    partyPlayers.value = c.players || []
    attrs.value = c.rule_attrs?.length ? c.rule_attrs : fallbackAttrs.value
    ruleMeta.value = c.rule_meta || {}
    rulesetRuntime.value = c.ruleset_runtime || null
    attrTotal.value = Number(c.rule_attrs_total || ruleMeta.value.attribute_points || 0) || inferredAttrTotal.value
    form.value.attributes = suggestedAttributes(attrs.value, attrLimit.value)
    cards.value = k.cards || []
    sheetReady.value = true
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function partyControlLabel(player: Player): string {
  if (player.control?.mode === 'ai') return 'AI 队友'
  if (player.control?.mode === 'unclaimed') return '待认领'
  return '玩家'
}

function partyHpLabel(player: Player): string {
  const sheet = player.character_sheet || {}
  const current = Number(sheet.hp)
  const max = Number(sheet.max_hp)
  if (!Number.isFinite(current) || !Number.isFinite(max) || max <= 0) return ''
  return `${current}/${max}`
}

function partyResources(player: Player) {
  const sheet = player.character_sheet || {}
  return [
    ...primaryResourceList(sheet, ruleMeta.value),
    ...buildSpecialStats(sheet, ruleMeta.value.rule_special_stats),
  ].slice(0, 4)
}

async function applyProfessionalCard(event: Event) {
  const select = event.target as HTMLSelectElement
  if (select.value === '') return
  const card = cards.value[Number(select.value)]
  if (!card) return
  if (characterCardNeedsConversion(card, ruleMeta.value.rule_id)) {
    const ok = await confirm({
      title: t('cardRuleMismatchTitle'),
      content: t('cardRuleMismatchContent', {
        source: characterCardRuleName(card, t('unboundRule')),
        target: String(ruleMeta.value.rule_name || ruleMeta.value.rule_id || ''),
      }),
      positiveText: t('continueAndReview'),
      negativeText: t('cancel'),
      type: 'warning',
    })
    if (!ok) { select.value = ''; return }
  }
  professionalInitial.value = JSON.parse(JSON.stringify(card)) as CharacterCard
  professionalHostKey.value += 1
}

async function createProfessional(character: CharacterSheet) {
  busy.value = true; error.value = ''
  try {
    const r = await api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, {
      method: 'POST', body: JSON.stringify({ ...character, join_as_new: true }),
    })
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    storeSeatToken(gameKey.value, r.seat_token)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

function fillSuggestedAttrs() {
  form.value.attributes = suggestedAttributes(attrs.value, attrLimit.value)
}

async function verifyRoomPassword() {
  busy.value = true; error.value = ''
  try {
    const r = await api<{ room_token: string }>(`/games/${encodeURIComponent(gameKey.value)}/verify-room-password`, { method: 'POST', body: JSON.stringify({ password: roomPasswordInput.value }) })
    storeRoomToken(gameKey.value, r.room_token)
    needRoomPassword.value = false
    // Behind a room password the lobby hides the scene (and the seat list a
    // returning player is matched against) until the room token is held;
    // fetch it again now that it is.
    try {
      detail.value = await api<GameDetail>(`/games/${encodeURIComponent(gameKey.value)}`)
    } catch {
      // Keep the lobby we have; the scene is only decoration here.
    }
    await continueJoin(detail.value)
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function retryJoin() {
  if (busy.value) return
  error.value = ''
  if (needRoomPassword.value) return verifyRoomPassword()
  if (resumeUser.value) {
    // Preserve the local "continue with a new identity" recovery choice.
    // Main's seat token, rather than a public user id, proves a returning seat.
    resumeUser.value = ''
    pendingSeatLink = false
  }
  busy.value = true
  try {
    const d = await api<GameDetail>(`/games/${encodeURIComponent(gameKey.value)}`)
    detail.value = d
    if (d.has_room_password && !readRoomToken(gameKey.value)) {
      needRoomPassword.value = true
      return
    }
    await continueJoin(d)
  } catch (e: unknown) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function applyCard(event: Event) {
  const select = event.target as HTMLSelectElement
  if (select.value === '') return
  const i = Number(select.value)
  const card = cards.value[i]
  if (!card) return
  const mismatch = characterCardNeedsConversion(card, ruleMeta.value.rule_id)
  if (mismatch) {
    const ok = await confirm({
      title: t('cardRuleMismatchTitle'),
      content: t('cardRuleMismatchContent', {
        source: characterCardRuleName(card, t('unboundRule')),
        target: String(ruleMeta.value.rule_name || ruleMeta.value.rule_id || ''),
      }),
      positiveText: t('continueAndReview'),
      negativeText: t('cancel'),
      type: 'warning',
    })
    if (!ok) { select.value = ''; return }
  }
  const copied = JSON.parse(JSON.stringify(card)) as CharacterCard
  const targetSuggested = suggestedAttributes(attrs.value, attrLimit.value)
  const targetAttributes = mismatch
    ? Object.fromEntries(attrs.value.map(attr => [attr.key, Number(card.attributes?.[attr.key] ?? targetSuggested[attr.key]) || 0]))
    : (card.attributes || {})
  form.value = { ...form.value, ...copied, attributes: targetAttributes, skills: (card.skills || []).map(skillToForm) }
  if (!form.value.skills.length) form.value.skills = [{ name: '', value: '' }]
}

async function create() {
  busy.value = true; error.value = ''
  try {
    const payload = {
      ...form.value,
      hp: form.value.hp === '' ? undefined : Number(form.value.hp),
      skills: joinSkillsPayload(form.value.skills),
      join_as_new: true,
    }
    const r = await api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, { method: 'POST', body: JSON.stringify(payload) })
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    storeSeatToken(gameKey.value, r.seat_token)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>
<template>
  <main class="join-page">
    <header class="join-hud">
      <div class="join-title-block">
        <BrandLogo :size="36" :with-text="false" />
        <div class="join-title-copy">
          <span class="section-kicker">JOIN THE TABLE</span>
          <h1>{{ detail.world_name || t('joinGame') }}</h1>
          <p>{{ detail.scene || t('createCharacterStartAdventure') }}</p>
        </div>
      </div>
      <div class="join-actions">
        <label class="locale-select">
          <span>{{ t('language') }}</span>
          <select :value="locale" @change="onLocaleChange">
            <option v-for="code in UI_LOCALES" :key="code" :value="code">{{ uiLocaleLabel(code) }}</option>
          </select>
        </label>
        <button @click="router.push({ name: 'overview' })">{{ t('backToOverview') }}</button>
      </div>
    </header>

    <nav class="join-progress" :aria-label="t('joinProgressLabel')">
      <ol>
        <li v-for="(step, index) in joinStages" :key="step.key" :class="{ active: joinStage === step.key, complete: joinStages.findIndex(item => item.key === joinStage) > index }" :aria-current="joinStage === step.key ? 'step' : undefined">
          <span class="join-progress-index">{{ index + 1 }}</span>
          <span>{{ step.label }}</span>
        </li>
      </ol>
    </nav>

    <div class="join-content-layout">
    <section v-if="needRoomPassword" class="join-form room-gate">
      <div class="sheet-head">
        <div>
          <h2>{{ t('roomNeedsPassword') }}</h2>
          <p>{{ t('roomPasswordPrompt', { name: detail.world_name || t('thisGame') }) }}</p>
        </div>
      </div>
      <label class="sheet-field">{{ t('roomPassword') }}<input type="password" v-model="roomPasswordInput" @keyup.enter="verifyRoomPassword" :placeholder="t('roomPassword')"></label>
      <div v-if="error" class="join-error"><p class="error-banner">{{ displayError }}</p><button type="button" @click="retryJoin">{{ t('retryJoin') }}</button></div>
      <button class="primary submit" :disabled="busy || !roomPasswordInput.trim()" @click="verifyRoomPassword">{{ busy ? t('validating') : t('verifyAndContinue') }}</button>
    </section>

    <section v-else-if="resumeUser" class="join-form resume-block">
      <div class="sheet-head">
        <div>
          <h2>{{ t('restoringCharacter') }}</h2>
          <p>{{ busy ? t('restoringViaLink') : t('preparingTable') }}</p>
        </div>
      </div>
      <div v-if="error" class="join-error"><p class="error-banner">{{ displayError }}</p><button type="button" @click="retryJoin">{{ t('continueWithNewIdentity') }}</button></div>
    </section>

    <section v-else-if="!sheetReady" class="join-form">
      <div class="sheet-head">
        <div>
          <h2>{{ t('readingCharacterRules') }}</h2>
          <p>{{ t('syncingCharacterRules') }}</p>
        </div>
      </div>
      <div v-if="error" class="join-error"><p class="error-banner">{{ displayError }}</p><button type="button" @click="retryJoin">{{ t('retryJoin') }}</button></div>
    </section>

    <section v-else-if="usesProfessionalBuilder" class="join-form professional-join-form">
      <div class="sheet-head">
        <div><h2>{{ t('createYourCharacter') }}</h2><p>{{ t('joinCharacterHelp') }}</p></div>
        <span class="badge badge-active">5E 2024 SRD</span>
      </div>
      <label v-if="cards.length" class="sheet-field">{{ t('chooseFromSharedLibrary') }}
        <select @change="applyProfessionalCard"><option value="">{{ t('newCharacterCard') }}</option><option v-for="(c, i) in cards" :key="c.id || i" :value="i">{{ c.character_name }} · {{ c.race }} {{ c.class }}</option></select>
      </label>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
      <RulesetExperienceHost :key="professionalHostKey" embedded :rule-id="String(ruleMeta.rule_id || detail.rule_id || '')" :language="locale" :initial="professionalInitial" @submit="createProfessional" @cancel="router.push({ name: 'overview' })" />
    </section>

    <section v-else class="join-form player-sheet-form">
      <div class="sheet-head">
        <div>
          <h2>{{ t('createYourCharacter') }}</h2>
          <p>{{ t('joinCharacterHelp') }}</p>
        </div>
        <span class="badge badge-active">{{ detail.solo_mode ? t('soloAdventure') : t('multiplayerAdventure') }}</span>
      </div>

      <label v-if="cards.length" class="sheet-field">{{ t('chooseFromSharedLibrary') }}
        <select @change="applyCard">
          <option value="">{{ t('newCharacterCard') }}</option>
          <option v-for="(c, i) in cards" :key="c.character_name || i" :value="i">{{ c.character_name }} · {{ c.race }} {{ c.class }}</option>
        </select>
      </label>

      <section class="sheet-section">
        <h3>{{ t('identityStep') }}</h3>
        <div class="two-cols">
          <label>{{ t('characterName') }}<input v-model="form.character_name" maxlength="40"></label>
          <label>{{ t('race') }}<input v-model="form.race"></label>
          <label>{{ t('classRole') }}<input v-model="form.class"></label>
          <label>HP<input type="number" v-model="form.hp" :placeholder="t('leaveBlankAuto')"></label>
        </div>
        <details class="join-avatar-details">
          <summary>
            <PortraitImage :portrait="form.portrait" :rule-id="String(ruleMeta.rule_id || '')" :seed="form.character_name" :name="form.character_name" :size="32" />
            <span><strong>{{ t('characterAvatar') }}</strong><small>{{ t('changeAvatar') }}</small></span>
          </summary>
          <!-- Keep the picker mounted while collapsed so uploads/generated portraits and v-model state survive. -->
          <PortraitPicker v-model="form.portrait" :rule-id="String(ruleMeta.rule_id || '')" :seed="form.character_name" :name="form.character_name" />
        </details>
      </section>

      <section class="sheet-section">
        <h3>{{ t('attributes') }}</h3>
        <p class="muted sheet-hint">{{ t('sliderNumberHint') }}</p>
        <p v-if="ruleMeta.attr_hint" class="muted sheet-hint">{{ ruleMeta.attr_hint }}</p>
        <p v-if="diceHint" class="muted sheet-hint">{{ diceHint }}</p>
        <p class="muted sheet-hint" :class="{ warn: attrOverLimit }">
          {{ t('attrSum') }} {{ attrSum }}<span v-if="attrLimit"> / {{ attrLimit }} · {{ t('pointsRemaining', { points: attrRemaining }) }}</span>
          <button type="button" class="chip" @click="fillSuggestedAttrs">{{ t('fillSuggestedValues') }}</button>
        </p>
        <div class="attrs">
          <label v-for="a in attrs" :key="a.key">
            <span>{{ attrDisplayName(a) }}</span>
            <input type="range" :min="a.min || 1" :max="a.max || 100" v-model.number="form.attributes[a.key]">
            <input type="number" v-model.number="form.attributes[a.key]" :title="t('directNumberInput')">
          </label>
        </div>
      </section>

      <section class="sheet-section">
        <div class="skills-title"><h3>{{ t('skills') }}</h3><button @click="form.skills.push({ name: '', value: '' })">{{ t('addSkill') }}</button></div>
        <p v-if="ruleMeta.skill_hint" class="muted sheet-hint">{{ ruleMeta.skill_hint }}</p>
        <p class="muted sheet-hint" :class="{ warn: skillOverLimit }">
          <span v-if="maxSkills">{{ t('skillCount', { count: filledSkills.length, max: maxSkills }) }}</span>
          <span v-if="skillPointTotal"> · {{ t('skillPointsSpent', { spent: skillSpent, total: skillPointTotal }) }}</span>
          <span v-if="maxSkillValue"> · {{ t('maxSingleSkill', { max: maxSkillValue }) }}</span>
        </p>
        <div class="skill-entry" v-for="(s, i) in form.skills" :key="i">
          <div class="skill-row">
            <input v-model="s.name" :placeholder="t('skillName')">
            <input type="number" v-model="s.value" :placeholder="t('numericValue')">
            <button
              type="button"
              class="chip skill-effect-toggle"
              :class="{ active: openSkillEffects.has(i) || Boolean(s.effect) }"
              :aria-expanded="openSkillEffects.has(i)"
              :title="t('skillEffect')"
              @click="toggleSkillEffect(i)"
            >{{ t('skillEffect') }}</button>
            <button @click="form.skills.splice(i, 1)" :title="t('deleteSkill')">×</button>
          </div>
          <textarea
            v-if="openSkillEffects.has(i)"
            class="skill-effect-input"
            rows="2"
            maxlength="500"
            v-model="s.effect"
            :placeholder="t('skillEffectPlaceholder')"
          />
        </div>
      </section>

      <label class="sheet-field">
        <span class="field-label-row"><span>{{ t('characterBackground') }}</span><small>{{ form.background.length }} / {{ backgroundLimit }}</small></span>
        <textarea class="character-background-input" rows="8" :maxlength="backgroundLimit" v-model="form.background" :placeholder="t('characterBackgroundLongHint')"></textarea>
      </label>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
      <button class="primary submit" :disabled="busy || !form.character_name.trim()" @click="create">{{ busy ? t('creating') : t('createCharacterAndEnter') }}</button>
    </section>
    <!-- Keep the table context visible even before the first player joins.  The
         invite page is also the read-only team/status surface, so hiding this
         rail when the list is empty made a freshly created room look broken. -->
    <aside v-if="sheetReady && !needRoomPassword && !resumeUser" class="join-party-preview" data-testid="join-party-preview">
      <header class="join-party-preview__head">
        <div>
          <span class="section-kicker">CURRENT TABLE</span>
          <h2>队伍状态</h2>
        </div>
        <span class="join-party-preview__count">{{ partyPlayers.length }}</span>
      </header>
      <p class="join-party-preview__hint">加入前先查看当前队伍与角色状态，创建角色后会自动回到本局。</p>
      <div v-if="partyPlayers.some(player => player.control?.mode === 'unclaimed')" class="join-party-preview__claim-hint" role="note">
        <strong>{{ t('claimUnclaimedTitle') }}</strong>
        <span>{{ t('claimUnclaimedHint') }}</span>
      </div>
      <ul class="join-party-preview__list">
        <li v-for="player in partyPlayers" :key="player.user_id" class="join-party-preview__member" :style="{ '--party-color': playerColor(player.user_id) }">
          <PortraitImage :portrait="player.character_sheet?.portrait" :rule-id="String(detail.rule_id || '')" :seed="player.user_id" :name="player.character_name" :size="42" />
          <div class="join-party-preview__identity">
            <strong>{{ player.character_name || player.user_id }}</strong>
            <span>{{ partyControlLabel(player) }}<template v-if="player.character_sheet?.race || player.character_sheet?.class"> · {{ [player.character_sheet?.race, player.character_sheet?.class].filter(Boolean).join(' · ') }}</template></span>
            <div v-if="partyResources(player).length" class="join-party-preview__resources">
              <span v-for="resource in partyResources(player)" :key="resource.key">{{ 'label' in resource ? resource.label : resource.name }} {{ resource.current }}<template v-if="resource.max">/{{ resource.max }}</template></span>
            </div>
          </div>
          <span v-if="partyHpLabel(player)" class="join-party-preview__hp">HP {{ partyHpLabel(player) }}</span>
        </li>
        <li v-if="!partyPlayers.length" class="join-party-preview__empty">当前还没有队伍成员，创建角色后会显示在这里。</li>
      </ul>
      <div class="join-party-preview__rule">
        <span>规则</span>
        <strong>{{ String(ruleMeta.rule_name || detail.rule_id || '当前规则') }}</strong>
      </div>
    </aside>
    </div>
  </main>
</template>

<style scoped>
.join-content-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  align-items: start;
  justify-content: center;
  gap: 16px;
  width: min(var(--df-page-max, 1560px), calc(100% - (2 * var(--df-page-gutter, 24px))));
  max-width: var(--df-page-max, 1560px);
  margin: 22px auto 0;
}
.join-content-layout > .join-form {
  width: 100%;
  margin: 0;
}
.join-party-preview {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 13px;
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-lg);
  background: linear-gradient(155deg, color-mix(in srgb, var(--df-interactive) 7%, var(--df-surface-1)), var(--df-surface-2));
  box-shadow: var(--df-shadow);
}
.join-party-preview__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--df-border-soft);
}
.join-party-preview__head h2 { margin: 2px 0 0; font-size: 20px; }
.join-party-preview__count {
  display: grid;
  min-width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid var(--df-interactive);
  border-radius: 999px;
  color: var(--df-interactive-strong);
  background: color-mix(in srgb, var(--df-interactive) 12%, transparent);
  font-weight: 800;
}
.join-party-preview__hint { margin: 0; color: var(--df-text-muted); font-size: 12px; line-height: 1.55; }
.join-party-preview__claim-hint { display: grid; gap: 4px; padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--df-accent) 35%, var(--df-border-soft)); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--df-accent) 8%, var(--df-surface-1)); color: var(--df-text-secondary); font-size: 12px; line-height: 1.55; }
.join-party-preview__claim-hint strong { color: var(--df-accent-strong); }
.join-party-preview__list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.join-party-preview__member {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  min-width: 0;
  padding: 8px;
  border: 1px solid color-mix(in srgb, var(--party-color, var(--df-border-soft)) 30%, var(--df-border-soft));
  border-radius: var(--df-radius-sm);
  background: color-mix(in srgb, var(--df-surface-1) 75%, transparent);
}
.join-party-preview__identity { display: grid; min-width: 0; gap: 3px; }
.join-party-preview__identity strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.join-party-preview__identity span,
.join-party-preview__hp { color: var(--df-text-muted); font-size: 11px; }
.join-party-preview__identity strong { color: var(--party-color, var(--df-text)); }
.join-party-preview__resources { display: flex; flex-wrap: wrap; gap: 3px 7px; color: var(--df-text-secondary); font-size: 10px; }
.join-party-preview__hp { white-space: nowrap; }
.join-party-preview__empty {
  padding: 14px 10px;
  border: 1px dashed var(--df-border-soft);
  border-radius: var(--df-radius-sm);
  color: var(--df-text-muted);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}
.join-party-preview__rule { display: grid; gap: 3px; padding-top: 10px; border-top: 1px solid var(--df-border-soft); color: var(--df-text-muted); font-size: 11px; }
.join-party-preview__rule strong { color: var(--df-accent-strong); font-size: 12px; }
.join-hud { padding: 10px 16px; }
.join-progress {
  width: min(var(--df-page-max, 1560px), calc(100% - (2 * var(--df-page-gutter, 24px))));
  max-width: var(--df-page-max, 1560px);
  margin: 12px auto 0;
}
.join-progress ol { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0; padding: 0; list-style: none; }
.join-progress li { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--df-text-muted); font-size: 12px; }
.join-progress-index { display: grid; flex: 0 0 28px; width: 28px; height: 28px; place-items: center; border: 1px solid var(--df-border-soft); border-radius: 50%; background: var(--df-surface-1); font-weight: 800; }
.join-progress li span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.join-progress li.active { color: var(--df-accent-strong); font-weight: 800; }
.join-progress li.active .join-progress-index { border-color: var(--df-accent); background: var(--df-accent-soft); }
.join-progress li.complete .join-progress-index { color: var(--df-success); border-color: color-mix(in srgb, var(--df-success) 55%, var(--df-border-soft)); }
.join-avatar-details { border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: color-mix(in srgb,var(--df-interactive) 4%,var(--df-surface-1)); }
.join-avatar-details summary { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 8px 12px; cursor: pointer; list-style: none; }
.join-avatar-details summary::-webkit-details-marker { display: none; }
.join-avatar-details summary::after { margin-left: auto; content: '＋'; color: var(--df-text-muted); font-size: 18px; }
.join-avatar-details[open] summary::after { content: '−'; }
.join-avatar-details summary span { display: grid; gap: 2px; }
.join-avatar-details summary small { color: var(--df-text-muted); }
.join-avatar-details > .portrait-picker { padding: 0 12px 12px; }
.join-error { display: grid; gap: 8px; }
.join-error .error-banner { margin: 0; }
.join-form input:not([type='range']), .join-form select, .join-form button { min-height: 44px; }
.join-error button { width: fit-content; }
@media (max-width: 560px) {
  .join-content-layout { display: block; width: calc(100% - 24px); margin-top: 14px; }
  .join-party-preview { position: static; margin-top: 14px; padding: 14px; }
  .join-hud { padding: 8px 12px; }
  .join-progress { margin-top: 8px; }
  .join-progress ol { gap: 4px; }
  .join-progress li { display: grid; justify-items: center; gap: 4px; text-align: center; }
  .join-progress li span:last-child { max-width: 100%; font-size: 11px; }
}
@media (min-width: 561px) and (max-width: 760px) {
  .join-content-layout { display: block; width: calc(100% - 24px); }
  .join-party-preview { position: static; margin-top: 14px; }
}
</style>
