<script setup lang="ts">
import { ref } from 'vue'
import { NIcon } from 'naive-ui'
import {
  PlayForwardOutline, ArrowUndoOutline, ShareOutline, BugOutline,
  PersonOutline, BookOutline, PeopleOutline, LockOpenOutline, LockClosedOutline,
  KeyOutline, PlaySkipForwardOutline, DownloadOutline, RefreshOutline,
  TrashOutline, SendOutline,
} from '@vicons/ionicons5'
import type { GameDetail, Player } from '../../api/types'

defineProps<{ detail: GameDetail; players: Player[]; isGm: boolean }>()
const emit = defineEmits<{
  advance: []
  rollback: []
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
}>()

const cmdText = ref('')
const percTarget = ref('')
const percText = ref('')

function run() { if (cmdText.value.trim()) { emit('command', cmdText.value.trim()); cmdText.value = '' } }
function sendPerc() { if (percTarget.value && percText.value.trim()) { emit('perception', percTarget.value, percText.value.trim()); percText.value = '' } }
</script>

<template>
  <section v-if="isGm" class="gm-toolbar panel">
    <div class="gm-group">
      <h4>流程</h4>
      <button @click="emit('advance')"><NIcon :component="PlayForwardOutline" size="14" /> 推进</button>
      <button @click="emit('rollback')"><NIcon :component="ArrowUndoOutline" size="14" /> 撤回</button>
    </div>
    <div class="gm-group gm-player-group">
      <h4>玩家</h4>
      <button @click="emit('invite')"><NIcon :component="ShareOutline" size="14" /> 邀请链接</button>
      <button @click="emit('bot-bind')" title="复制一次性 Bot 绑定命令；绑定成功后自动作废"><NIcon :component="BugOutline" size="14" /> 一次性 Bot 绑定</button>
      <button @click="emit('cards')"><NIcon :component="PersonOutline" size="14" /> 角色视角</button>
      <button @click="emit('world-switch')"><NIcon :component="BookOutline" size="14" /> 切换世界书</button>
    </div>
    <div class="gm-group">
      <h4>模式</h4>
      <button @click="emit('mode')"><NIcon :component="PeopleOutline" size="14" /> 切为{{ detail.solo_mode ? '多人' : '单人' }}</button>
      <button @click="emit('access')"><NIcon :component="detail.player_access_open === false ? LockOpenOutline : LockClosedOutline" size="14" /> {{ detail.player_access_open === false ? '开放入口' : '关闭入口' }}</button>
      <button @click="emit('room-password')"><NIcon :component="KeyOutline" size="14" /> {{ detail.has_room_password ? '改房间密码' : '设房间密码' }}</button>
    </div>
    <div class="gm-group gm-grow">
      <h4>指令</h4>
      <input v-model="cmdText" placeholder="GM 指令 / 救档操作" @keydown.enter="run">
      <button @click="run"><NIcon :component="PlaySkipForwardOutline" size="14" /> 执行</button>
    </div>
    <div class="gm-group">
      <h4>存档</h4>
      <button @click="emit('export')"><NIcon :component="DownloadOutline" size="14" /> 导出</button>
      <button @click="emit('restart')"><NIcon :component="RefreshOutline" size="14" /> 重开</button>
      <button class="danger" @click="emit('reset')"><NIcon :component="TrashOutline" size="14" /> 重置</button>
    </div>
    <details class="perc gm-perc"><summary>角色感知（仅目标角色可见）</summary>
      <div class="perc-row">
        <select v-model="percTarget"><option value="">选择角色</option><option v-for="p in players" :key="p.user_id" :value="p.user_id">{{ p.character_name }}</option></select>
        <input v-model="percText" placeholder="输入感知内容" @keydown.enter="sendPerc">
        <button @click="sendPerc"><NIcon :component="SendOutline" size="14" /> 发送</button>
      </div>
    </details>
  </section>
</template>
