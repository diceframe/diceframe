<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { NIcon, NInput } from 'naive-ui'
import { ArrowBackOutline, CopyOutline, LinkOutline } from '@vicons/ionicons5'
import { storeToRefs } from 'pinia'
import { createRendezvousRoom, getRendezvousConfig } from '@/api/peer'
import { api, ApiError, errorMessage } from '@/api/client'
import type { CharacterListResponse, GamesResponse, GameSummary, Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { usePeerSessionStore } from '@/peer/store/peerSession'
import { copyToClipboard } from '@/utils/clipboard'
import { friendlyPeerDetail } from './friendlyDetail'
import { classifyJoinInvitation, joinInvitationErrorKeys } from './joinInvitation'
import {
  STUN_PRESETS,
  decodePeerInvite,
  encodePeerInvites,
  stunUrlsForPreset,
  type EncodedPeerInvite,
  type PeerInviteTarget,
  type StunPresetId,
} from './inviteCode'

const props = withDefaults(defineProps<{
  embedded?: boolean
  initialMode?: 'host' | 'guest'
  initialInvite?: string
}>(), { embedded: false, initialMode: 'host', initialInvite: '' })

type Mode = 'host' | 'guest'
type GuestStunChoice = 'invite' | StunPresetId

const STUN_PRESET_KEY = 'diceframe_peer_stun_preset'
const STUN_CUSTOM_KEY = 'diceframe_peer_stun_custom'

function savedStunPreset(): StunPresetId {
  const value = typeof localStorage === 'undefined' ? null : localStorage.getItem(STUN_PRESET_KEY)
  return value === 'cloudflare'
    || value === 'metered'
    || value === 'nextcloud'
    || value === 'multi'
    || value === 'none'
    || value === 'custom'
    ? value
    : 'cloudflare'
}

function savedCustomStunUrl(): string {
  return typeof localStorage === 'undefined' ? '' : localStorage.getItem(STUN_CUSTOM_KEY) || ''
}

function saveLocalValue(key: string, value: string): void {
  if (typeof localStorage !== 'undefined') localStorage.setItem(key, value)
}

const { t } = useLocale()
const toast = useToast()
const { confirm } = useConfirm()
const router = useRouter()
const peerSession = usePeerSessionStore()
const { connected, peerStates, roomCode, state, stateDetail } = storeToRefs(peerSession)
const mode = ref<Mode>(props.initialMode)
const busy = ref(false)
const availableGames = ref<GameSummary[]>([])
const hubMaxPeersPerRoom = ref(6)
const hubRetryAfter = ref(15)
const hubLoadLevel = ref<'normal' | 'busy' | 'nearly_full'>('normal')
const selectedGameKey = ref('')
const selectedPlayers = ref<Player[]>([])
const playersLoading = ref(false)
const hostStunPreset = ref<StunPresetId>(savedStunPreset())
const hostCustomStunUrl = ref(savedCustomStunUrl())
const guestStunChoice = ref<GuestStunChoice>('invite')
const guestCustomStunUrl = ref('')
const directConsent = ref(false)
const inviteCodes = ref<EncodedPeerInvite[]>([])
const inviteInput = ref(props.initialInvite)
const invitePanel = ref<HTMLElement | null>(null)

const stateLabel = computed(() => t(`peerState_${state.value}`))
const peerStage = computed<'setup' | 'invite' | 'connect' | 'enter'>(() => {
  if (connected.value) return 'enter'
  if (mode.value === 'host' && inviteCodes.value.length) return 'invite'
  if (state.value !== 'idle' && state.value !== 'closed' && state.value !== 'error') return 'connect'
  return 'setup'
})
const peerStages = computed(() => [
  { key: 'setup' as const, label: t('peerStepSetup') },
  { key: 'invite' as const, label: t('peerStepInvite') },
  { key: 'connect' as const, label: t('peerStepConnect') },
  { key: 'enter' as const, label: t('peerStepEnter') },
])
/** 状态详情里的 Hub/协议原始错误码转成人话，正常进度文案原样展示。 */
const displayDetail = computed(() => friendlyPeerDetail(stateDetail.value, t))
/** 失败/关闭状态的 detail 才用红色横幅；连接中的进度提示走中性样式。 */
const isFailureState = computed(() => (
  state.value === 'error' || state.value === 'closed'
))
/** 会话活跃期间（连接中/已连接）禁止切换模式或误触创建/加入。 */
const sessionActive = computed(() => (
  state.value === 'signaling'
  || state.value === 'waiting'
  || state.value === 'connecting'
  || state.value === 'connected'
))
const invitePreview = computed(() => {
  try {
    return decodePeerInvite(inviteInput.value)
  } catch {
    return null
  }
})
const joinInvitation = computed(() => classifyJoinInvitation(inviteInput.value))
const selectedGame = computed(() => (
  availableGames.value.find(game => game.game_key === selectedGameKey.value)
))
const selectedHostUserId = computed(() => (
  String(selectedGame.value?.gm_uid || '') || selectedPlayers.value[0]?.user_id || ''
))
const existingGuestPlayers = computed(() => {
  return selectedPlayers.value.filter(player => player.user_id !== selectedHostUserId.value)
})
function gameSlots(game: GameSummary | undefined): number {
  if (!game) return 0
  return Math.max(0, Number(game.max_players || 6) - Number(game.player_count || 0))
}
const automaticInviteTargets = computed<PeerInviteTarget[]>(() => {
  const game = selectedGame.value
  if (!game) return []
  const targets: PeerInviteTarget[] = existingGuestPlayers.value.map(player => ({
    actorId: player.user_id,
    actorName: player.character_name || player.user_id,
  }))
  for (let index = 0; index < gameSlots(game); index += 1) {
    targets.push({ actorName: t('peerNewPlayerNumber', { number: index + 1 }) })
  }
  return targets.slice(0, Math.max(0, hubMaxPeersPerRoom.value - 1))
})
const roomPeerCount = computed(() => automaticInviteTargets.value.length + 1)
const batchOmittedCount = computed(() => Math.max(
  0,
  existingGuestPlayers.value.length
    + gameSlots(selectedGame.value)
    - automaticInviteTargets.value.length,
))
const hasInviteCapacity = computed(() => automaticInviteTargets.value.length > 0)
const capacityHint = computed(() => {
  const game = selectedGame.value
  if (!game) return ''
  return t('peerCapacityHint', {
    used: String(Number(game.player_count || 0)),
    total: String(Number(game.max_players || 6)),
    existing: String(existingGuestPlayers.value.length),
    slots: String(gameSlots(game)),
    max: String(roomPeerCount.value),
  })
})
const hubLoadLabel = computed(() => t(`peerLoad_${hubLoadLevel.value}`))

watch(hostStunPreset, value => saveLocalValue(STUN_PRESET_KEY, value))
watch(hostCustomStunUrl, value => saveLocalValue(STUN_CUSTOM_KEY, value.trim()))

let playerLoadVersion = 0
async function loadSelectedPlayers(gameKey: string) {
  const version = ++playerLoadVersion
  selectedPlayers.value = []
  if (!gameKey) return
  playersLoading.value = true
  try {
    const result = await api<CharacterListResponse>(`/games/${encodeURIComponent(gameKey)}/characters`)
    if (version !== playerLoadVersion) return
    selectedPlayers.value = result.players || []
  } catch (error) {
    if (version === playerLoadVersion) toast.error(errorMessage(error))
  } finally {
    if (version === playerLoadVersion) playersLoading.value = false
  }
}
watch(selectedGameKey, gameKey => void loadSelectedPlayers(gameKey))

onMounted(async () => {
  const [gamesResult, configResult] = await Promise.allSettled([
    api<GamesResponse>('/games'),
    getRendezvousConfig(),
  ])
  if (gamesResult.status === 'fulfilled') {
    // 满员存档仍可把已存在的角色重新分享给玩家，不能从直连列表里隐藏。
    availableGames.value = gamesResult.value.games || []
    selectedGameKey.value = availableGames.value[0]?.game_key || ''
  }
  if (configResult.status === 'fulfilled') {
    const config = configResult.value
    hubMaxPeersPerRoom.value = Math.max(2, Math.min(32, Number(config.max_peers_per_room) || 6))
    hubRetryAfter.value = Math.max(1, Number(config.retry_after) || 15)
    if (config.load_level === 'busy' || config.load_level === 'nearly_full') {
      hubLoadLevel.value = config.load_level
    }
  }
})

function selectMode(next: Mode) {
  if (sessionActive.value) return
  peerSession.reset()
  mode.value = next
  inviteCodes.value = []
}

function selectedGuestStunUrls(inviteUrls: readonly string[]): string[] {
  if (guestStunChoice.value === 'invite') return [...inviteUrls]
  return stunUrlsForPreset(guestStunChoice.value, guestCustomStunUrl.value)
}

function stunConfigError(error: unknown): string | null {
  if (!(error instanceof Error)) return null
  if (error.message === 'too_many_stun_urls') return t('peerStunTooMany')
  if (error.message === 'invalid_stun_url') return t('peerStunInvalid')
  return null
}

async function createRoom() {
  if (!directConsent.value || sessionActive.value || playersLoading.value) return
  busy.value = true
  stateDetail.value = ''
  try {
    if (!selectedGameKey.value) throw new Error(t('peerGameRequired'))
    const game = selectedGame.value
    if (!game) throw new Error(t('peerGameRequired'))
    if (!hasInviteCapacity.value) throw new Error(t('peerNoInviteCapacity'))
    const shouldConvertSoloSave = game.solo_mode !== false
    if (shouldConvertSoloSave) {
      const accepted = await confirm({
        title: t('peerConvertSoloTitle'),
        content: t('peerConvertSoloContent'),
        positiveText: t('peerConvertSoloAction'),
        type: 'warning',
      })
      if (!accepted) return
    }
    // 与游戏页保持一致：开房前恢复存档绑定的 GM 会话。浏览器 Cookie 更新、
    // 静态前端换源或换设备后，当前 Web 会话 ID 可能与存档 gm_uid 不同；
    // 只放宽单个接口会让后续 GM 操作继续失败，因此必须先完成身份恢复。
    await api(`/games/${encodeURIComponent(selectedGameKey.value)}/claim-gm`, {
      method: 'POST',
      body: '{}',
    })
    if (shouldConvertSoloSave) {
      const converted = await api<{ ok?: boolean; solo_mode?: boolean; error?: string }>(
        `/games/${encodeURIComponent(selectedGameKey.value)}/mode`,
        { method: 'POST', body: JSON.stringify({ solo: false }) },
      )
      if (!converted.ok || converted.solo_mode !== false) {
        throw new Error(converted.error || t('peerConvertSoloFailed'))
      }
      game.solo_mode = false
    }
    const selectedStunUrls = stunUrlsForPreset(hostStunPreset.value, hostCustomStunUrl.value)
    const targets = automaticInviteTargets.value
    const room = await createRendezvousRoom(targets.length + 1)
    inviteCodes.value = encodePeerInvites(
      room,
      selectedStunUrls,
      selectedGameKey.value,
      targets,
    )
    const guestActorIds = Object.fromEntries(
      room.invitations
        .map((invitation, index) => [invitation.peer_id, targets[index]?.actorId || ''] as const)
        .filter(([, actorId]) => Boolean(actorId)),
    )
    peerSession.startMulti({
      isHost: true,
      localPeerId: room.host_peer_id,
      hostPeerId: room.host_peer_id,
      guestPeerIds: room.invitations.map(item => item.peer_id),
      roomCode: room.room_code,
      token: room.host_token,
      websocketUrl: room.websocket_url,
      stunUrls: selectedStunUrls,
      gameKey: selectedGameKey.value,
      guestActorIds,
      localApi: api,
    })
    await nextTick()
    invitePanel.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (error) {
    const stunError = stunConfigError(error)
    if (stunError) {
      peerSession.updateState('error', stunError)
    } else if (error instanceof ApiError && error.code === 'rendezvous_busy') {
      peerSession.updateState('error', t('peerBusyRetry', { seconds: error.retryAfter || hubRetryAfter.value }))
    } else {
      peerSession.updateState('error', errorMessage(error))
    }
  } finally {
    busy.value = false
  }
}

function joinRoom() {
  if (!directConsent.value || sessionActive.value || busy.value || joinInvitation.value.kind !== 'peer') return
  busy.value = true
  try {
    const invite = decodePeerInvite(inviteInput.value)
    if (Date.parse(invite.expiresAt) <= Date.now()) throw new Error(t('peerInviteExpired'))
    const selectedStunUrls = selectedGuestStunUrls(invite.stunUrls)
    peerSession.startMulti({
      isHost: false,
      localPeerId: invite.peerId,
      token: invite.guestToken,
      hostPeerId: invite.hostPeerId,
      guestPeerIds: [invite.peerId],
      roomCode: invite.roomCode,
      websocketUrl: invite.websocketUrl,
      stunUrls: selectedStunUrls,
      gameKey: invite.gameKey,
      assignedActorId: invite.actorId,
    })
  } catch (error) {
    const stunError = stunConfigError(error)
    if (stunError) {
      peerSession.updateState('error', stunError)
    } else {
      peerSession.updateState('error', error instanceof Error && error.message !== 'invalid_invite'
        ? error.message
        : t('peerInviteInvalid'))
    }
  } finally {
    busy.value = false
  }
}

async function copyInvite(inviteCode: string) {
  await copyToClipboard(inviteCode)
  toast.success(t('peerInviteCopied'))
}

/** 多码房间一键复制全部，方便房主整段发给玩家。 */
async function copyAllInvites() {
  const all = inviteCodes.value
    .map((invite, index) => `${invite.actorName || t('peerNewPlayerNumber', { number: index + 1 })}：${invite.inviteCode}`)
    .join('\n\n')
  await copyToClipboard(all)
  toast.success(t('peerAllInvitesCopied'))
}

async function enterGame() {
  if (!peerSession.gameKey) return
  if (peerSession.isHost) {
    router.push({ name: 'play', query: { game: peerSession.gameKey } })
  } else {
    if (peerSession.actorId) {
      const rebound = await peerSession.rebindIdentity()
      if (rebound && peerSession.actorId) {
        router.push({
          name: 'play',
          query: {
            game: peerSession.gameKey,
            user: peerSession.actorId,
            share: '1',
            peer: '1',
          },
        })
        return
      }
      toast.error(t('peerAssignedIdentityFailed'))
    }
    router.push({
      name: 'join',
      query: { game: peerSession.gameKey, share: '1', peer: '1' },
    })
  }
}
</script>

<template>
  <section class="peer-page" :class="{ 'peer-page-embedded': embedded }">
    <header v-if="!embedded" class="peer-header">
      <RouterLink :to="{ name: 'overview' }" class="peer-back">
        <NIcon :component="ArrowBackOutline" />{{ t('peerBack') }}
      </RouterLink>
      <span class="section-kicker">CONNECT THE TABLE</span>
      <h1>{{ t('peerTitle') }}</h1>
      <p>{{ t('peerSubtitle') }}</p>
    </header>

    <main class="peer-layout">
      <section class="peer-card peer-setup">
        <ol class="peer-flow-steps" :aria-label="t('peerProgressLabel')">
          <li v-for="(step, index) in peerStages" :key="step.key" :class="{ active: peerStage === step.key, complete: peerStages.findIndex(item => item.key === peerStage) > index }" :aria-current="peerStage === step.key ? 'step' : undefined">
            <span>{{ index + 1 }}</span>{{ step.label }}
          </li>
        </ol>
        <div class="peer-mode-tabs">
          <button :class="{ active: mode === 'host' }" :disabled="sessionActive" @click="selectMode('host')">{{ t('peerHostMode') }}</button>
          <button :class="{ active: mode === 'guest' }" :disabled="sessionActive" @click="selectMode('guest')">{{ t('peerGuestMode') }}</button>
        </div>

        <label v-if="mode === 'host'" class="peer-field">
          <span>{{ t('peerGame') }}</span>
          <select v-model="selectedGameKey" :disabled="state !== 'idle' && state !== 'closed' && state !== 'error'">
            <option value="">{{ t('peerSelectGame') }}</option>
            <option v-for="game in availableGames" :key="game.game_key" :value="game.game_key">{{ game.world_name || game.game_key }}{{ game.solo_mode === false ? '' : ` · ${t('peerSoloSave')}` }}</option>
          </select>
          <small>{{ availableGames.length ? t('peerGameHint') : t('peerNoMultiplayerGames') }}</small>
        </label>
        <label v-if="mode === 'host'" class="peer-field">
          <span>{{ t('peerStunServer') }}</span>
          <select v-model="hostStunPreset" :disabled="state !== 'idle' && state !== 'closed' && state !== 'error'">
            <option v-for="preset in STUN_PRESETS" :key="preset.id" :value="preset.id">{{ t(`peerStunPreset_${preset.id}`) }}</option>
          </select>
          <small>{{ t(`peerStunPresetHint_${hostStunPreset}`) }}</small>
        </label>
        <section v-if="mode === 'host' && selectedGame && !playersLoading && hasInviteCapacity" class="peer-room-batch">
          <header>
            <strong>{{ t('peerRoomBatchTitle', { count: automaticInviteTargets.length }) }}</strong>
            <small v-if="capacityHint">{{ capacityHint }}</small>
          </header>
          <ul>
            <li v-for="(target, index) in automaticInviteTargets" :key="target.actorId || `new-${index}`">
              <span>{{ index + 1 }}</span>
              <strong>{{ target.actorName }}</strong>
              <small>{{ target.actorId ? t('peerExistingPlayer') : t('peerNewPlayerSeat') }}</small>
            </li>
          </ul>
          <small>{{ t('peerRoomBatchHint') }}</small>
          <small v-if="batchOmittedCount" class="peer-batch-warning">{{ t('peerRoomBatchCapped', { count: batchOmittedCount }) }}</small>
          <small class="peer-load-level" :class="`peer-load-${hubLoadLevel}`">{{ t('peerHubLoad') }}：{{ hubLoadLabel }}</small>
        </section>
        <p v-else-if="mode === 'host' && selectedGame && !playersLoading" class="peer-no-capacity">
          {{ t('peerNoInviteCapacity') }}
        </p>
        <label v-if="mode === 'host' && hostStunPreset === 'custom'" class="peer-field">
          <span>{{ t('peerStunCustomAddress') }}</span>
          <textarea v-model.trim="hostCustomStunUrl" rows="3" :placeholder="t('peerStunCustomPlaceholder')" :disabled="state !== 'idle' && state !== 'closed' && state !== 'error'" />
          <small>{{ t('peerStunHint') }}</small>
        </label>

        <template v-if="mode === 'host'">
          <label class="peer-direct-consent">
            <input v-model="directConsent" type="checkbox">
            <span>{{ t('peerDirectConsent') }} <RouterLink :to="{ name: 'legal-privacy' }" target="_blank" rel="noopener">{{ t('legalPrivacyTitle') }}</RouterLink></span>
          </label>
          <button class="primary success peer-primary" :disabled="!directConsent || !selectedGameKey || playersLoading || !hasInviteCapacity || busy || sessionActive" @click="createRoom">
            <NIcon :component="LinkOutline" />{{ t('peerCreateRoom') }}
          </button>
        </template>

        <template v-else>
          <label class="peer-field">
            <span>{{ t('friendJoinLabel') }}</span>
            <textarea v-model.trim="inviteInput" rows="4" :placeholder="t('friendJoinPlaceholder')" autocomplete="off" autocapitalize="off" spellcheck="false" />
          </label>
          <div v-if="joinInvitation.kind === 'link'" class="peer-invitation-link" role="status">
            <strong>{{ t('friendJoinLinkReady') }}</strong>
            <span>{{ joinInvitation.origin }}</span>
            <a data-testid="peer-open-invitation" :href="joinInvitation.url" target="_blank" rel="noopener noreferrer">{{ t('friendJoinOpenLink') }}</a>
          </div>
          <template v-else>
            <p v-if="joinInvitation.kind === 'invalid'" class="error-banner" role="alert">{{ t(joinInvitationErrorKeys[joinInvitation.reason]) }}</p>
            <div v-if="invitePreview" class="peer-invite-preview">
              <span v-if="invitePreview.actorName">{{ t('peerInviteAssignedTo') }} <strong>{{ invitePreview.actorName }}</strong></span>
              <span>{{ t('peerInviteStun') }}</span>
              <div class="peer-invite-stun-list">
                <code v-for="url in invitePreview.stunUrls" :key="url">{{ url }}</code>
                <code v-if="!invitePreview.stunUrls.length">{{ t('peerStunPreset_none') }}</code>
              </div>
            </div>
            <label class="peer-field">
              <span>{{ t('peerGuestStunChoice') }}</span>
              <select v-model="guestStunChoice">
                <option value="invite">{{ t('peerStunUseInvite') }}</option>
                <option v-for="preset in STUN_PRESETS" :key="preset.id" :value="preset.id">{{ t(`peerStunPreset_${preset.id}`) }}</option>
              </select>
              <small>{{ t('peerGuestStunHint') }}</small>
            </label>
            <label v-if="guestStunChoice === 'custom'" class="peer-field">
              <span>{{ t('peerStunCustomAddress') }}</span>
              <textarea v-model.trim="guestCustomStunUrl" rows="3" :placeholder="t('peerStunCustomPlaceholder')" />
              <small>{{ t('peerStunHint') }}</small>
            </label>
            <label class="peer-direct-consent">
              <input v-model="directConsent" type="checkbox">
              <span>{{ t('peerDirectConsent') }} <RouterLink :to="{ name: 'legal-privacy' }" target="_blank" rel="noopener">{{ t('legalPrivacyTitle') }}</RouterLink></span>
            </label>
            <button class="primary success peer-primary" :disabled="!directConsent || joinInvitation.kind !== 'peer' || sessionActive || busy" @click="joinRoom">
              <NIcon :component="LinkOutline" />{{ t('peerJoinRoom') }}
            </button>
          </template>
        </template>
      </section>

      <section class="peer-card peer-status">
        <header>
          <div>
            <span>{{ t('peerConnectionStatus') }}</span>
            <strong :class="`peer-state-${state}`"><i />{{ stateLabel }}</strong>
          </div>
          <div v-if="roomCode" class="peer-status-room">
            <span>{{ t('peerRoomCode') }}</span>
            <code>{{ roomCode }}</code>
          </div>
        </header>
        <p v-if="stateDetail" :class="isFailureState ? 'error-banner' : 'peer-status-detail'">{{ displayDetail }}</p>
        <section v-if="mode === 'host' && inviteCodes.length" ref="invitePanel" class="peer-invite">
          <header class="peer-invite-header">
            <div class="peer-invite-heading">
              <strong>{{ t('peerInvitesReadyTitle', { count: inviteCodes.length }) }}</strong>
              <small>{{ t('peerInvitesReadyHint') }}</small>
            </div>
            <button v-if="inviteCodes.length > 1" class="peer-copy-all" @click="copyAllInvites">
              <NIcon :component="CopyOutline" />{{ t('peerCopyAllInvites') }}
            </button>
          </header>
          <div
            v-for="(invite, index) in inviteCodes"
            :key="invite.peerId"
            class="peer-invite-item"
          >
            <span class="peer-invite-index" aria-hidden="true">{{ index + 1 }}</span>
            <div class="peer-invite-code-wrap">
              <div class="peer-invite-meta">
                <strong>{{ invite.actorName || t('peerNewPlayerNumber', { number: index + 1 }) }}</strong>
                <span
                  class="peer-invite-peer-state"
                  :class="`peer-state-${peerStates[invite.peerId] || 'waiting'}`"
                >
                  <i />{{ t(`peerState_${peerStates[invite.peerId] || 'waiting'}`) }}
                </span>
              </div>
              <NInput
                class="peer-invite-code"
                :value="invite.inviteCode"
                type="textarea"
                readonly
                :autosize="{ minRows: 2, maxRows: 2 }"
                :aria-label="t('peerInviteForTarget', { name: invite.actorName || t('peerNewPlayerNumber', { number: index + 1 }) })"
              />
            </div>
            <button
              class="peer-invite-copy"
              :aria-label="t('peerCopyInvite')"
              @click="copyInvite(invite.inviteCode)"
            >
              <NIcon :component="CopyOutline" />{{ t('peerCopyInvite') }}
            </button>
          </div>
          <small>{{ t('peerInviteSecurityHint') }}</small>
        </section>
        <div v-if="Object.keys(peerStates).length && !(mode === 'host' && inviteCodes.length)" class="peer-member-states">
          <strong>{{ t('peerConnectedPeers') }}</strong>
          <code v-for="(peerState, peerId) in peerStates" :key="peerId">{{ peerId }} · {{ t(`peerState_${peerState}`) }}</code>
        </div>
        <button v-if="state !== 'idle' && state !== 'closed'" class="peer-disconnect" @click="peerSession.stop()">{{ t('peerDisconnect') }}</button>
        <button v-if="connected && peerSession.gameKey" class="primary success peer-enter-game" @click="enterGame">{{ t('peerEnterGame') }}</button>
        <p class="peer-boundary">{{ t('peerBoundary') }}</p>

        <div class="peer-connection-check" :class="{ active: connected }">
          <h2>{{ t('peerConnectionCheckTitle') }}</h2>
          <p>{{ t('peerConnectionCheckHint') }}</p>
          <strong><i />{{ t(connected ? 'peerConnectionCheckActive' : 'peerConnectionCheckWaiting') }}</strong>
        </div>
      </section>
    </main>
  </section>
</template>

<style scoped>
.peer-invitation-link { display: grid; gap: 12px; overflow-wrap: anywhere; }
.peer-invitation-link span { color: var(--df-text-muted); font-size: 13px; }
.peer-invitation-link a { color: var(--df-accent-strong); font-weight: 700; }
</style>
