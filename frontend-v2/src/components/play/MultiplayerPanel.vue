<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { BedOutline } from '@vicons/ionicons5'
import type { GameDetail, Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { playerColor } from '@/utils/play'

const props = defineProps<{ players: Player[]; detail: GameDetail; currentUserId?: string; hostingUid?: string }>()
const emit = defineEmits<{
  'open-character-center': []
}>()
const { t } = useLocale()

const actedSet = computed(() => new Set((props.detail.multiplayer?.submitted_actions || []).map(a => a.user_id)))
const actionByUser = computed(() => new Map((props.detail.multiplayer?.submitted_actions || []).map(a => [a.user_id, a])))
const awaySet = computed(() => new Set((props.detail.multiplayer?.away_players || []).map(p => p.user_id)))
const restSession = computed(() => props.detail.rest_session)
const myRestStatus = computed(() => restSession.value?.participants.find(row => row.user_id === props.currentUserId)?.status || '')
const currentPlayer = computed(() => props.players.find(player => player.user_id === props.currentUserId))
const currentPlayerUnclaimed = computed(() => currentPlayer.value?.control?.mode === 'unclaimed')
/** GM 自己的席位没人能认领：提示 GM 直接在成员菜单接手，而不是去找 GM 要链接。 */
const currentIsGmSeat = computed(() => !!props.currentUserId && props.currentUserId === props.detail.gm_uid)
function hasActed(p: Player) { return actedSet.value.has(p.user_id) }
function needsDice(p: Player) { return Boolean(actionByUser.value.get(p.user_id)?.dice_pending) }
function isAway(p: Player) { return awaySet.value.has(p.user_id) }
function controlMode(p: Player): 'human' | 'ai' | 'unclaimed' {
  const mode = p.control?.mode
  return mode === 'ai' || mode === 'unclaimed' ? mode : 'human'
}
/** 「AI 临时托管」（暂离托管）与「AI 托管」（GM/房间决定）是两种不同状态。 */
function isTemporaryHost(p: Player) { return controlMode(p) === 'ai' && Boolean(p.control?.temporary) }
function controlBadge(p: Player): string {
  const mode = controlMode(p)
  if (mode === 'ai') return isTemporaryHost(p) ? t('controlAiTemporary') : t('controlAi')
  if (mode === 'unclaimed') return t('controlUnclaimed')
  return t('controlHuman')
}
function controlBadgeClass(p: Player) {
  const mode = controlMode(p)
  return ['tag', 'tag-control', `control-${mode}`, isTemporaryHost(p) ? 'control-temporary' : '']
}
/** 服务器正在为这个席位接管：控制权切换请求还没回来。 */
function isHosting(p: Player) { return Boolean(props.hostingUid) && p.user_id === props.hostingUid }
</script>

<template>
  <section class="multiplayer panel">
    <h2>{{ t('playerList') }}（{{ players.length }}）</h2>
    <p v-if="currentPlayerUnclaimed && currentIsGmSeat" class="multiplayer-unclaimed-help" role="note">
      <span>{{ t('claimOwnSeatHint') }}</span>
    </p>
    <p v-else-if="currentPlayerUnclaimed" class="multiplayer-unclaimed-help" role="note">
      <strong>{{ t('claimUnclaimedTitle') }}</strong>
      <span>{{ t('claimUnclaimedHint') }}</span>
    </p>
    <div v-if="restSession?.active" class="party-rest-alert" role="status">
      <div class="party-rest-alert-head">
        <NIcon :component="BedOutline" size="18" />
        <strong>{{ restSession.rest === 'long' ? t('partyLongRest') : t('partyShortRest') }}</strong>
        <span>{{ t('partyRestProgress', { ready: restSession.ready_count, total: restSession.active_count }) }}</span>
      </div>
      <div class="party-rest-members">
        <span v-for="row in restSession.participants" :key="row.user_id" :class="row.status">
          {{ row.character_name }} · {{ row.status === 'submitted' ? t('restReady') : t('restWaiting') }}
        </span>
      </div>
      <button type="button" class="party-rest-open" @click="emit('open-character-center')">
        {{ myRestStatus === 'submitted' ? t('viewPartyRest') : t('respondPartyRest') }}
      </button>
    </div>
    <ul class="player-list">
      <li v-for="p in players" :key="p.user_id" :style="{ '--pc': playerColor(p.user_id) }">
        <div class="player-head">
          <span class="dot" />
          <strong>{{ p.character_name || p.user_id }}</strong>
          <small v-if="p.user_id === currentUserId" class="tag tag-self">{{ t('you') }}</small>
          <small v-if="p.user_id === detail.gm_uid" class="tag tag-gm">GM</small>
          <small :class="controlBadgeClass(p)">{{ controlBadge(p) }}</small>
          <small v-if="isAway(p)" class="tag">{{ t('away') }}</small>
        </div>
        <div class="player-meta" v-if="detail.solo_mode === false">
          <span :class="['acted', isHosting(p) ? 'wait' : isAway(p) ? 'done' : needsDice(p) ? 'wait' : hasActed(p) ? 'done' : 'wait']">
            {{ isHosting(p) ? t('controlAiTakingOver') : isAway(p) ? t('awayFollowing') : needsDice(p) ? t('needsRoll') : hasActed(p) ? t('acted') : t('waitingAction') }}
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.multiplayer-unclaimed-help {
  display: grid;
  gap: 4px;
  margin: 0 0 10px;
  padding: 9px 10px;
  border: 1px solid color-mix(in srgb, var(--df-accent) 35%, var(--df-border-soft));
  border-radius: var(--df-radius-sm);
  background: color-mix(in srgb, var(--df-accent) 8%, var(--df-surface-1));
  color: var(--df-text-secondary);
  font-size: 12px;
  line-height: 1.55;
}
.multiplayer-unclaimed-help strong { color: var(--df-accent-strong); }
</style>
