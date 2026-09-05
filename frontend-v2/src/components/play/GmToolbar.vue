<script setup lang="ts">
import { reactive, ref } from 'vue'
import { NIcon } from 'naive-ui'
import {
  PlayForwardOutline, ArrowUndoOutline, ShareOutline, BugOutline,
  PersonOutline, BookOutline, PeopleOutline, LockOpenOutline, LockClosedOutline,
  KeyOutline, PlaySkipForwardOutline, DownloadOutline, RefreshOutline,
  TrashOutline, SendOutline, ImageOutline, MapOutline,
  ReaderOutline,
  CashOutline,
} from '@vicons/ionicons5'
import type { GameDetail, Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ detail: GameDetail; players: Player[]; isGm: boolean; recapBusy?: boolean }>()
const emit = defineEmits<{
  advance: []
  rollback: []
  recap: []
  invite: []
  'bot-bind': []
  mode: []
  'narrative-perspective': [perspective: 'auto' | 'immersive' | 'third_person']
  'advancement-control': [payload: Record<string, string | number>]
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
  payment: []
}>()

const cmdText = ref('')
const percTarget = ref('')
const percText = ref('')
const xpAwards = reactive<Record<string, number>>({})
const { t } = useLocale()

function run() { if (cmdText.value.trim()) { emit('command', cmdText.value.trim()); cmdText.value = '' } }
function sendPerc() { if (percTarget.value && percText.value.trim()) { emit('perception', percTarget.value, percText.value.trim()); percText.value = '' } }
function changeNarrativePerspective(event: Event) {
  const perspective = (event.target as HTMLSelectElement).value
  if (perspective === 'auto' || perspective === 'immersive' || perspective === 'third_person') {
    emit('narrative-perspective', perspective)
  }
}
function configureAdvancement(field: 'mode' | 'authority', event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('advancement-control', {
    action: 'configure',
    mode: field === 'mode' ? value : (props.detail.advancement?.mode || 'milestone'),
    authority: field === 'authority' ? value : (props.detail.advancement?.authority || 'ai_gm'),
  })
}
function grantAdvancement(userId: string) {
  emit('advancement-control', { action: 'grant', user_id: userId })
}
function awardXp(userId: string) {
  const amount = Math.max(1, Math.trunc(Number(xpAwards[userId] || 0)))
  if (!amount) return
  emit('advancement-control', { action: 'award_xp', user_id: userId, amount })
}
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
      <button @click="emit('payment')"><NIcon :component="CashOutline" size="14" /> {{ t('createPaymentProposal') }}</button>
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
        <label class="gm-narrative-setting">
          <span>{{ t('narrativePerspective') }}</span>
          <select :value="detail.narrative_perspective || 'auto'" @change="changeNarrativePerspective">
            <option value="auto">{{ t('narrativeAuto') }}</option>
            <option value="immersive">{{ t('narrativeImmersive') }}</option>
            <option value="third_person">{{ t('narrativeThirdPerson') }}</option>
          </select>
          <small>{{ t('narrativeChangeHint') }}</small>
        </label>
        <button @click="emit('access')"><NIcon :component="detail.player_access_open === false ? LockOpenOutline : LockClosedOutline" size="14" /> {{ detail.player_access_open === false ? t('openAccess') : t('closeAccess') }}</button>
        <button @click="emit('room-password')"><NIcon :component="KeyOutline" size="14" /> {{ t('gameSettings') }}</button>
      </div>
    </details>
    <details v-if="detail.ruleset_runtime?.id === 'core:dnd2024' && detail.advancement" class="perc gm-perc gm-console-section gm-advancement-section">
      <summary>{{ t('advancementPolicy') }}</summary>
      <div class="gm-advancement-settings">
        <label>
          <span>{{ t('advancementMode') }}</span>
          <select :value="detail.advancement.mode" @change="configureAdvancement('mode', $event)">
            <option value="milestone">{{ t('advancementMilestone') }}</option>
            <option value="xp">{{ t('advancementXp') }}</option>
          </select>
        </label>
        <label>
          <span>{{ t('advancementAuthority') }}</span>
          <select :value="detail.advancement.authority" @change="configureAdvancement('authority', $event)">
            <option value="ai_gm">{{ t('advancementAiGm') }}</option>
            <option value="gm">{{ t('advancementHumanGm') }}</option>
          </select>
        </label>
      </div>
      <div class="gm-advancement-players">
        <article v-for="row in detail.advancement.players" :key="row.user_id">
          <div>
            <strong>{{ row.character_name }}</strong>
            <small v-if="row.entitled">{{ t('advancementGranted', { level: row.target_level }) }}</small>
            <small v-else-if="detail.advancement.mode === 'xp'">XP {{ row.xp }} / {{ row.next_level_xp }}</small>
            <small v-else>{{ t('advancementWaiting') }}</small>
          </div>
          <template v-if="detail.advancement.authority === 'gm' && !row.entitled && row.level < 20">
            <button v-if="detail.advancement.mode === 'milestone'" type="button" @click="grantAdvancement(row.user_id)">{{ t('advancementGrant') }}</button>
            <span v-else class="gm-xp-award">
              <input v-model.number="xpAwards[row.user_id]" type="number" min="1" :placeholder="String(Math.max(1, row.next_level_xp - row.xp))">
              <button type="button" @click="awardXp(row.user_id)">{{ t('advancementXpAward') }}</button>
            </span>
          </template>
        </article>
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
