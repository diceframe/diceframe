<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronBack, ChevronForward } from '@vicons/ionicons5'
import { useRoute, useRouter } from 'vue-router'
import { api, apiBlob } from '../../api/client'
import type { BotBindTokenResponse, CharacterCard, CharacterCardsResponse, CharacterListResponse, CommandResponse, HealthResponse, JsonObject, PendingPayment, Player, PlayerContextResponse, PublicAction, RuleMeta, WorldCandidate, WorldListResponse, WorldTemplatesResponse } from '../../api/types'
import { useGame } from '../../composables/useGame'
import { useToast } from '../../composables/useToast'
import { useConfirm } from '../../composables/useConfirm'
import { useSettingsStore } from '../../stores/useSettingsStore'
import { buildJoinLink } from '../../utils/shareLink'
import GameTimeline from '../../components/GameTimeline.vue'
import ActionComposer from '../../components/ActionComposer.vue'
import GameSidebar from '../../components/GameSidebar.vue'
import RuleHelp from '../../components/RuleHelp.vue'
import HealthPanel from '../../components/HealthPanel.vue'
import Modal from '../../components/ui/Modal.vue'
import GmToolbar from '../../components/play/GmToolbar.vue'
import MultiplayerPanel from '../../components/play/MultiplayerPanel.vue'

defineOptions({ name: 'PlayView' })

const route = useRoute(), router = useRouter()
const isPlayer = computed(() => !!(route.query.user || route.query.share))
function goBack() { router.push({ name: 'overview' }) }

const game = useGame()
const settings = useSettingsStore()
const toast = useToast()
const { confirm } = useConfirm()
const help = ref(false), ruleMeta = ref<RuleMeta>({}), preview = ref(false), delegate = ref(false), cards = ref<CharacterCard[]>([]), showCards = ref(false), health = ref<HealthResponse>({ events: [] })
const worldCandidates = ref<WorldCandidate[]>([]), showWorldSwitch = ref(false), showRoomPassword = ref(false), roomPasswordInput = ref('')
const sidebarCollapsed = ref(localStorage.getItem('play_sidebar_collapsed') === '1')
const gmThinking = ref(false)
function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value; localStorage.setItem('play_sidebar_collapsed', sidebarCollapsed.value ? '1' : '0') }
const railCollapsed = ref(false)
function toggleRail() { railCollapsed.value = !railCollapsed.value; localStorage.setItem('play_rail_collapsed', railCollapsed.value ? '1' : '0') }
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || '操作失败') }

const actorId = computed(() => game.userId.value || game.player.value?.user_id || game.detail.value?.gm_uid || '')
const serverJudging = computed(() => game.detail.value?.state === 'active_judgment')
const showGmThinking = computed(() => gmThinking.value || serverJudging.value)
const sceneTitle = computed(() => game.detail.value?.scene || '未知场景')
const stateLabel = computed(() => {
  if (showGmThinking.value) return 'GM 思考中'
  const state = game.detail.value?.state || 'unknown'
  const labels: Record<string, string> = { setup: '准备中', waiting: '等待行动', action: '行动阶段', resolving: '判定中', paused: '暂停', ended: '已结束' }
  return labels[state] || state
})
const tableMode = computed(() => game.detail.value?.solo_mode ? '单人冒险' : '多人冒险')
const roleLabel = computed(() => game.isGm.value ? 'GM 控台' : isPlayer.value ? '玩家视角' : '观战视角')
const progressLabel = computed(() => {
  if (showGmThinking.value) return '生成剧情中'
  const detail = game.detail.value
  if (!detail) return '同步中'
  if (detail.solo_mode) return '连续行动'
  const ready = detail.multiplayer?.ready_count || 0
  const total = detail.multiplayer?.active_count ?? detail.multiplayer?.player_count ?? 0
  return `已行动 ${ready}/${total}`
})
const gameCode = computed(() => game.currentGame.value ? game.currentGame.value.slice(0, 8) : '')
const tableNotice = computed(() => {
  if (showGmThinking.value) return 'GM 正在处理本轮行动，生成叙事、检定结果和状态变化，请稍候。'
  const detail = game.detail.value
  if (!detail) return ''
  if (detail.state === 'paused') return game.isGm.value ? '当前游戏已暂停。玩家行动仍会记录，恢复或强制推进后进入下一轮。' : '当前游戏已暂停，等待 GM 恢复或推进。'
  const waiting = detail.multiplayer?.waiting_players || []
  if (!detail.solo_mode && waiting.length) {
    const names = waiting.map((p: Player) => p.character_name || p.user_id).filter(Boolean).join('、')
    return names ? `等待 ${names} 行动。` : '等待其他玩家行动。'
  }
  const away = detail.multiplayer?.away_players || []
  if (!detail.solo_mode && away.length) {
    const names = away.map((p: Player) => p.character_name || p.user_id).filter(Boolean).join('、')
    return names ? `${names} 暂离中，剧情默认跟随队伍。` : '有玩家暂离中，剧情默认跟随队伍。'
  }
  const submitted = detail.multiplayer?.submitted_actions?.some((a: PublicAction) => a.user_id === actorId.value)
  if (!detail.solo_mode && submitted) return '本轮行动已提交，可在 GM 推进前修改行动。'
  return ''
})
async function command(path: string, body: JsonObject = {}) {
  const thinkingCommand = path === 'advance'
  if (thinkingCommand) gmThinking.value = true
  try {
    const r = await api<CommandResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/${path}`, { method: 'POST', body: JSON.stringify(body) })
    if (r.error) { toast.error(r.error); return }
    if (r.forced_waiting?.length) toast.info('已为 ' + r.forced_waiting.join('、') + ' 补记暂不行动')
    if (r.narration) toast.success(r.narration)
    else toast.success('操作完成')
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
    if (r.error || r.ok === false) throw new Error(r.error || '设置失败')
    showRoomPassword.value = false
    toast.success(roomPasswordInput.value ? '房间密码已更新' : '已取消房间密码')
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
  navigator.clipboard.writeText(buildJoinLink(game.currentGame.value, settings.config.public_base_url))
  toast.success('邀请链接已复制')
}

async function copyBotBind() {
  try {
    const r = await api<BotBindTokenResponse>(`/games/${encodeURIComponent(game.currentGame.value)}/bot-bind-token`, { method: 'POST', body: JSON.stringify({ rotate: true }) })
    await navigator.clipboard.writeText(`绑定 ${game.currentGame.value} ${r.bind_token}`)
    toast.success('新的一次性 Bot 绑定命令已复制，旧绑定码已作废')
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function openWorldSwitch() {
  try {
    const [templateData, worldData] = await Promise.all([api<WorldTemplatesResponse>('/world-templates'), api<WorldListResponse>('/worlds')])
    const seen = new Set<string>()
    const candidates: WorldCandidate[] = []
    for (const t of templateData.templates || []) {
      const id = t.world_id || t.id
      if (!id) continue
      seen.add(id)
      candidates.push({ id, name: t.world_name || t.name || id, description: t.description || '', source: '模板', default_rule: t.default_rule || '', entry_count: undefined })
    }
    for (const w of worldData.worlds || []) {
      const id = w.id || w.world_id
      if (!id || seen.has(id)) continue
      candidates.push({ id, name: w.name || w.world_name || id, description: w.description || '', source: '世界书', default_rule: '', entry_count: w.entry_count || 0 })
    }
    worldCandidates.value = candidates
    showWorldSwitch.value = true
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function switchWorld(worldId: string) {
  try {
    const r = await api<{ ok?: boolean; error?: string; world_name?: string }>(`/games/${encodeURIComponent(game.currentGame.value)}/switch-world`, { method: 'POST', body: JSON.stringify({ world_id: worldId }) })
    if (r.error || r.ok === false) throw new Error(r.error || '切换失败')
    showWorldSwitch.value = false
    toast.success(`已切换到 ${r.world_name || worldId}`)
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
  const ok = await confirm({ title: '踢出玩家', content: '确定踢出该玩家吗？该操作会删除其角色。', positiveText: '踢出玩家', negativeText: '取消', type: 'error' })
  if (!ok) return
  try {
    await api(`/games/${encodeURIComponent(game.currentGame.value)}/character/${encodeURIComponent(uid)}`, { method: 'DELETE' })
    toast.success('已踢出')
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function setAway(uid: string, away: boolean) {
  try {
    const r = await api<{ ok?: boolean; error?: string; character_name?: string }>(
      `/games/${encodeURIComponent(game.currentGame.value)}/players/${encodeURIComponent(uid)}/away`,
      { method: 'POST', body: JSON.stringify({ away }) },
    )
    if (r.error || r.ok === false) throw new Error(r.error || '状态切换失败')
    toast.success(`${r.character_name || uid} 已${away ? '暂离' : '回来'}`)
    await game.refresh()
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function copyLink(uid: string) {
  await ensureSettingsLoaded()
  navigator.clipboard.writeText(buildJoinLink(game.currentGame.value, settings.config.public_base_url, uid))
  toast.success('操作链接已复制（该玩家可直接恢复身份）')
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
    toast.success(accepted ? '已支付' : '已拒绝支付')
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

async function lifecycle(action: string) {
  const ok = await confirm({
    title: action === 'reset' ? '重置当前进度' : '重新开始本局',
    content: action === 'reset' ? '重置会清空当前进度，确定继续吗？' : '确定重新开始本局吗？',
    positiveText: action === 'reset' ? '重置进度' : '重新开始', negativeText: '取消', type: 'warning',
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
    toast.success('已导出存档')
  } catch (e: unknown) { toast.error(errorMessage(e)) }
}

function onLoreClick(name: string) {
  toast.info(`场景：${name}`)
}

function syncPlayRoute() {
  if (route.name === 'play' && game.currentGame.value && !route.query.game) {
    router.replace({ name: 'play', query: { ...route.query, game: game.currentGame.value } })
  }
}

async function loadPlayContext() {
  if (!game.currentGame.value) return
  syncPlayRoute()
  // 玩家分享链接进入但未带 user（未创建角色）：跳创建角色页
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
        <button v-if="!isPlayer" class="icon play-back" title="返回总览" @click="goBack">←</button>
        <div class="play-titleblock">
          <span class="play-eyebrow">{{ roleLabel }} · {{ tableMode }}</span>
          <h1>{{ game.detail.value?.world_name || 'DiceFrame' }}</h1>
          <p>{{ sceneTitle }} · 第 {{ game.detail.value?.round_number || 0 }} 轮</p>
        </div>
      </div>
      <div class="play-hud-stats" aria-label="游戏状态">
        <span class="hud-stat"><strong>{{ stateLabel }}</strong><small>状态</small></span>
        <span class="hud-stat"><strong>{{ progressLabel }}</strong><small>进度</small></span>
        <span v-if="gameCode" class="hud-stat"><strong>{{ gameCode }}</strong><small>存档</small></span>
      </div>
      <div class="toolbar play-toolbar">
        <span v-if="preview" class="busy">房主预览</span>
        <button v-if="preview" @click="toggleDelegate">{{ delegate ? '关闭代操作' : '允许代操作' }}</button>
        <span v-if="game.loading.value" class="busy">更新中</span>
        <button @click="openCards">角色</button>
        <button @click="help = true">规则</button>
        <button @click="game.refresh()">刷新</button>
      </div>
    </header>

    <div v-if="game.error.value" class="error-banner">{{ game.error.value }}</div>
    <div v-else-if="game.loading.value || !game.detail.value" class="play-loading">
      <span class="spinner"></span>
      <h2>正在进入游戏桌</h2>
      <p>正在同步剧情、角色和地图。</p>
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
            <span class="scene-label">当前场景</span>
            <h2>{{ sceneTitle }}</h2>
            <p>{{ tableMode }} · {{ stateLabel }}</p>
          </div>
          <div class="scene-chips">
            <span>第 {{ game.detail.value.round_number || 0 }} 轮</span>
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
        <button class="rail-toggle" @click="toggleRail" :title="railCollapsed ? '展开 GM 操作' : '收起 GM 操作'">
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
        <header><h2>共享角色卡库</h2><button @click="showCards = false">×</button></header>
        <p>选择后替换当前角色卡，行动席位保持不变。</p>
        <button v-for="c in cards" :key="c.character_name" class="card-choice" @click="selectCard(c)">
          <strong>{{ c.character_name }}</strong><span>{{ c.race }} · {{ c.class }}</span>
        </button>
        <p v-if="!cards.length" class="muted">共享卡库为空。</p>
      </section>
    </div>

    <div v-if="showWorldSwitch" class="modal" @click.self="showWorldSwitch = false">
      <section class="dialog world-switch-dialog">
        <header><h2>切换当前局世界书</h2><button @click="showWorldSwitch = false">×</button></header>
        <p>当前绑定：{{ game.detail.value?.world_id || '未关联' }}。切换后会刷新剧情高亮、地图和世界书上下文。</p>
        <div class="world-switch-list">
          <button
            v-for="w in worldCandidates"
            :key="w.id"
            class="world-choice"
            :class="{ active: w.id === game.detail.value?.world_id }"
            @click="switchWorld(w.id)"
          >
            <strong>{{ w.name }}</strong>
            <span>{{ w.source }}<template v-if="w.default_rule"> · {{ w.default_rule }}</template><template v-if="w.entry_count !== undefined"> · {{ w.entry_count }} 条</template></span>
            <small>{{ w.description || w.id }}</small>
          </button>
          <p v-if="!worldCandidates.length" class="muted">没有可用的世界或世界书。</p>
        </div>
      </section>
    </div>

    <div v-if="showRoomPassword" class="modal" @click.self="showRoomPassword = false">
      <section class="dialog">
        <header><h2>{{ game.detail.value?.has_room_password ? '修改房间密码' : '设置房间密码' }}</h2><button @click="showRoomPassword = false">×</button></header>
        <p>留空保存即取消密码保护，玩家可自由加入。修改密码后，已进入的玩家需重新输入新密码。</p>
        <label>新密码<input type="password" v-model="roomPasswordInput" placeholder="留空=取消密码" @keyup.enter="setRoomPassword"></label>
        <div class="actions">
          <button @click="showRoomPassword = false">取消</button>
          <button class="primary" @click="setRoomPassword">保存</button>
        </div>
      </section>
    </div>

    <Modal v-if="pendingPay" title="GM 建议支付" @close="pendingPay = null">
      <p>GM 建议支付 <strong>{{ pendingPay.amount }}</strong> 金币{{ pendingPay.reason ? `（${pendingPay.reason}）` : '' }}。</p>
      <p class="muted">请确认是否购买。拒绝则不扣金币，GM 会收到通知。</p>
      <template #actions>
        <button @click="pendingPay = null">稍后</button>
        <button class="danger" @click="resolvePay(false)">拒绝</button>
        <button class="primary" @click="resolvePay(true)">确认购买</button>
      </template>
    </Modal>
  </main>

  <main v-else class="empty empty-game">
    <section>
      <h1>选择一局冒险</h1>
      <p class="muted">从总览进入已有存档，或创建一个新的跑团世界。</p>
      <div class="actions">
        <button class="primary" @click="goBack">查看存档</button>
        <button @click="router.push({ name: 'create' })">创建新冒险</button>
      </div>
    </section>
  </main>
</template>
