<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { LinkOutline, CreateOutline, CloseCircleOutline } from '@vicons/ionicons5'
import type { GameDetail, Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { playerColor } from '@/utils/play'

const props = defineProps<{ players: Player[]; detail: GameDetail; isGm: boolean; currentUserId?: string }>()
const emit = defineEmits<{
  kick: [uid: string]
  'copy-link': [uid: string]
  edit: [uid: string]
  'set-away': [uid: string, away: boolean]
}>()
const { t } = useLocale()

const actedSet = computed(() => new Set((props.detail.multiplayer?.submitted_actions || []).map(a => a.user_id)))
const actionByUser = computed(() => new Map((props.detail.multiplayer?.submitted_actions || []).map(a => [a.user_id, a])))
const awaySet = computed(() => new Set((props.detail.multiplayer?.away_players || []).map(p => p.user_id)))
const canKick = computed(() => props.isGm && props.players.length > 1)
function hasActed(p: Player) { return actedSet.value.has(p.user_id) }
function needsDice(p: Player) { return Boolean(actionByUser.value.get(p.user_id)?.dice_pending) }
function isAway(p: Player) { return awaySet.value.has(p.user_id) }
</script>

<template>
  <section class="multiplayer panel">
    <h2>{{ t('playerList') }}（{{ players.length }}）</h2>
    <ul class="player-list">
      <li v-for="p in players" :key="p.user_id" :style="{ '--pc': playerColor(p.user_id) }">
        <div class="player-head">
          <span class="dot" />
          <strong>{{ p.character_name || p.user_id }}</strong>
          <small v-if="p.user_id === currentUserId" class="tag tag-self">{{ t('you') }}</small>
          <small v-if="p.user_id === detail.gm_uid" class="tag tag-gm">GM</small>
          <small v-if="isAway(p)" class="tag">{{ t('away') }}</small>
        </div>
        <div class="player-meta" v-if="detail.solo_mode === false">
          <span :class="['acted', isAway(p) ? 'done' : needsDice(p) ? 'wait' : hasActed(p) ? 'done' : 'wait']">
            {{ isAway(p) ? t('awayFollowing') : needsDice(p) ? t('needsRoll') : hasActed(p) ? t('acted') : t('waitingAction') }}
          </span>
        </div>
        <div v-if="isGm" class="player-actions">
          <button @click="emit('copy-link', p.user_id)"><NIcon :component="LinkOutline" size="14" /> {{ t('operationLink') }}</button>
          <button @click="emit('edit', p.user_id)"><NIcon :component="CreateOutline" size="14" /> {{ t('edit') }}</button>
          <button v-if="p.user_id !== currentUserId" @click="emit('set-away', p.user_id, !isAway(p))">{{ isAway(p) ? t('back') : t('away') }}</button>
          <button v-if="canKick && p.user_id !== currentUserId && p.user_id !== detail.gm_uid" class="danger" @click="emit('kick', p.user_id)"><NIcon :component="CloseCircleOutline" size="14" /> {{ t('kick') }}</button>
        </div>
      </li>
    </ul>
  </section>
</template>
