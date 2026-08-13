<script setup lang="ts">
import { ref } from 'vue'
import { NIcon } from 'naive-ui'
import {
  PlayForwardOutline, ArrowUndoOutline, ShareOutline, BugOutline,
  PersonOutline, BookOutline, PeopleOutline, LockOpenOutline, LockClosedOutline,
  KeyOutline, PlaySkipForwardOutline, DownloadOutline, RefreshOutline,
  TrashOutline, SendOutline, ImageOutline, MapOutline,
  ReaderOutline,
} from '@vicons/ionicons5'
import type { GameDetail, Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

defineProps<{ detail: GameDetail; players: Player[]; isGm: boolean; recapBusy?: boolean }>()
const emit = defineEmits<{
  advance: []
  rollback: []
  recap: []
  invite: []
  'bot-bind': []
  mode: []
  access: []
  command: [text: string]
  perception: [uid: string, text: string]
  export: []
  reset: []
  restart: []
  cards: []
  'world-switch': []
  'room-password': []
  'scene-image': []
  'map-background': []
}>()

const cmdText = ref('')
const percTarget = ref('')
const percText = ref('')
const { t } = useLocale()

function run() { if (cmdText.value.trim()) { emit('command', cmdText.value.trim()); cmdText.value = '' } }
function sendPerc() { if (percTarget.value && percText.value.trim()) { emit('perception', percTarget.value, percText.value.trim()); percText.value = '' } }
</script>

<template>
  <section v-if="isGm" class="gm-toolbar panel">
    <header class="gm-console-head">
      <span aria-hidden="true">GM</span>
      <div><h2>{{ t('gmConsole') }}</h2><small>{{ t('roundLabel', { round: detail.round_number || 0 }) }}</small></div>
    </header>
    <div class="gm-group gm-flow-group">
      <h4>{{ t('flow') }}</h4>
      <button @click="emit('advance')"><NIcon :component="PlayForwardOutline" size="14" /> {{ t('advance') }}</button>
      <button @click="emit('rollback')"><NIcon :component="ArrowUndoOutline" size="14" /> {{ t('rollback') }}</button>
      <button :disabled="recapBusy" @click="emit('recap')"><NIcon :component="ReaderOutline" size="14" /> {{ recapBusy ? t('storyRecapGenerating') : t('storyRecapGenerate') }}</button>
    </div>
    <div class="gm-group gm-grow gm-command-group">
      <h4>{{ t('commandGroup') }}</h4>
      <input v-model="cmdText" :placeholder="t('gmCommandPlaceholder')" @keydown.enter="run">
      <button @click="run"><NIcon :component="PlaySkipForwardOutline" size="14" /> {{ t('execute') }}</button>
    </div>
    <details class="perc gm-perc gm-console-section"><summary>{{ t('players') }}</summary>
      <div class="gm-console-section-actions">
        <button @click="emit('invite')"><NIcon :component="ShareOutline" size="14" /> {{ t('inviteLink') }}</button>
        <button @click="emit('bot-bind')" :title="t('botBindCopied')"><NIcon :component="BugOutline" size="14" /> {{ t('botBind') }}</button>
        <button @click="emit('cards')"><NIcon :component="PersonOutline" size="14" /> {{ t('characterPerspective') }}</button>
        <button @click="emit('world-switch')"><NIcon :component="BookOutline" size="14" /> {{ t('switchLorebook') }}</button>
      </div>
    </details>
    <details class="perc gm-perc gm-console-section"><summary>{{ t('mode') }}</summary>
      <div class="gm-console-section-actions">
        <button @click="emit('mode')"><NIcon :component="PeopleOutline" size="14" /> {{ t('switchToMode', { mode: detail.solo_mode ? t('multiplayer') : t('solo') }) }}</button>
        <button @click="emit('access')"><NIcon :component="detail.player_access_open === false ? LockOpenOutline : LockClosedOutline" size="14" /> {{ detail.player_access_open === false ? t('openAccess') : t('closeAccess') }}</button>
        <button @click="emit('room-password')"><NIcon :component="KeyOutline" size="14" /> {{ detail.has_room_password ? t('changeRoomPassword') : t('setRoomPassword') }}</button>
      </div>
    </details>
    <details class="perc gm-perc gm-console-section"><summary>{{ t('saveGroup') }}</summary>
      <div class="gm-console-section-actions">
        <button @click="emit('export')"><NIcon :component="DownloadOutline" size="14" /> {{ t('export') }}</button>
        <button @click="emit('scene-image')"><NIcon :component="ImageOutline" size="14" /> {{ t('sceneImageManage') }}</button>
        <button @click="emit('map-background')"><NIcon :component="MapOutline" size="14" /> {{ t('mapBackgroundManage') }}</button>
        <button @click="emit('restart')"><NIcon :component="RefreshOutline" size="14" /> {{ t('restart') }}</button>
        <button class="danger" @click="emit('reset')"><NIcon :component="TrashOutline" size="14" /> {{ t('reset') }}</button>
      </div>
    </details>
    <details class="perc gm-perc gm-console-section"><summary>{{ t('perceptionPrivate') }}</summary>
      <div class="perc-row">
        <select v-model="percTarget"><option value="">{{ t('chooseCharacter') }}</option><option v-for="p in players" :key="p.user_id" :value="p.user_id">{{ p.character_name }}</option></select>
        <input v-model="percText" :placeholder="t('perceptionPlaceholder')" @keydown.enter="sendPerc">
        <button @click="sendPerc"><NIcon :component="SendOutline" size="14" /> {{ t('send') }}</button>
      </div>
    </details>
  </section>
</template>
