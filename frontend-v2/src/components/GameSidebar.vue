<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronBack, ChevronForward } from '@vicons/ionicons5'
import { Activity, Eye, MapIcon, ScrollText } from 'lucide-vue-next'
import type { GameDetail, MapData, Player, PrivateMessage, RuleMeta } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { advanceClock, createClock, deleteClock, renameClock } from '@/api/clocks'
import { assignClassLevel, spendSkillPoint, applyAttributeStep } from '@/api/progress'
import CharacterPanel from './CharacterPanel.vue'
import MapGraph from './play/MapGraph.vue'
import SidebarRowIcon from './SidebarRowIcon.vue'

interface Quest { title?: string; progress?: string; status?: string }
interface Relation { npc_name?: string; tier?: string }
interface Decision { title?: string; summary?: string; description?: string; round_number?: number }
interface Clock { clock_id?: string; title?: string; segments?: number; filled?: number; kind?: string }
interface Bond { owner?: string; target?: string; emotions?: string[] }
interface PlotTracker {
  quests?: Record<string, Quest>
  relations?: Record<string, Relation>
  decisions?: Array<Decision | string>
  clocks?: Record<string, Clock>
  bonds?: Record<string, Bond>
}

const props = defineProps<{ detail: GameDetail; player?: Player; privateMessages: PrivateMessage[]; map?: MapData | null; ruleMeta?: RuleMeta | null; collapsed?: boolean; docked?: boolean; portraitEditable?: boolean; showAdvancedCenter?: boolean; isGm?: boolean; clockControls?: boolean }>()
const emit = defineEmits<{ 'lore-click': [name: string]; 'open-map': []; 'toggle-sidebar': []; 'portrait-click': []; 'allocate-level-up': [attrs: Record<string, number>]; 'open-character-center': []; 'changed': [] }>()
const { t } = useLocale()
const toggleLabel = computed(() => props.docked === false ? t('close') : props.collapsed ? t('expandSidebar') : t('collapseSidebar'))

const pt = computed<PlotTracker>(() => (props.detail.plot_tracker && typeof props.detail.plot_tracker === 'object' ? props.detail.plot_tracker as PlotTracker : {}))
const activeQuests = computed(() => Object.values(pt.value.quests || {}).filter(q => q.status === 'active'))
const doneQuests = computed(() => Object.values(pt.value.quests || {}).filter(q => q.status === 'completed' || q.status === 'failed'))
const notableRelations = computed(() => Object.values(pt.value.relations || {}).filter(r => r.tier !== 'neutral'))
const recentDecisions = computed(() => (pt.value.decisions || []).slice(-5).reverse())
// 命刻只显示未填满的；羁绊按记录顺序展示强度（情感数）。
const activeClocks = computed(() => Object.values(pt.value.clocks || {})
  .filter(c => (c.filled || 0) < (c.segments || 0))
  .sort((a, b) => (b.filled || 0) - (a.filled || 0)))
const bonds = computed(() => Object.values(pt.value.bonds || {}))
const plotCount = computed(() => activeQuests.value.length + doneQuests.value.length + notableRelations.value.length + recentDecisions.value.length + activeClocks.value.length + bonds.value.length)
const hasPlot = computed(() => plotCount.value > 0 || (props.clockControls))
// GM 手动推进命刻：同一时间只允许一个请求，成功后让上层刷新详情。
const busyClock = ref('')
const fuClockTitle = ref('')
const fuClockSegments = ref(6)
const fuClockRenameId = ref('')
const fuClockRenameTitle = ref('')
const fuClockDeleteId = ref('')
async function addClock() {
  if (!props.isGm || !fuClockTitle.value.trim()) return
  const segments = Number(fuClockSegments.value)
  if (!Number.isInteger(segments) || segments < 4 || segments > 12) return
  try {
    await createClock(props.detail.game_key, fuClockTitle.value.trim(), segments)
    fuClockTitle.value = ''
    fuClockSegments.value = 6
    emit('changed')
  } catch { /* 下一次刷新校正 */ }
}
function startRenameClock(clock: Clock) {
  if (!props.isGm || !clock.clock_id) return
  fuClockRenameId.value = String(clock.clock_id)
  fuClockRenameTitle.value = String(clock.title || '')
}
async function commitRenameClock() {
  if (!props.isGm || !fuClockRenameId.value || !fuClockRenameTitle.value.trim()) return
  try {
    await renameClock(props.detail.game_key, fuClockRenameId.value, fuClockRenameTitle.value.trim())
    fuClockRenameId.value = ''
    fuClockRenameTitle.value = ''
    emit('changed')
  } catch { /* ignore */ }
}
function askDeleteClock(clock: Clock) {
  if (!props.isGm || !clock.clock_id) return
  fuClockDeleteId.value = String(clock.clock_id)
}
async function confirmDeleteClock() {
  if (!props.isGm || !fuClockDeleteId.value) return
  try {
    await deleteClock(props.detail.game_key, fuClockDeleteId.value)
    fuClockDeleteId.value = ''
    emit('changed')
  } catch { /* ignore */ }
}
async function stepClock(clock: Clock, delta: number) {
  const clockId = String(clock.clock_id || '')
  if (!props.isGm || !clockId || busyClock.value) return
  busyClock.value = clockId
  try {
    await advanceClock(props.detail.game_key, clockId, delta)
    emit('changed')
  } catch {
    // 失败不改本地状态；下一次静默刷新会校正格子数。
  } finally {
    busyClock.value = ''
  }
}

// FU 成长：待分配职业等级 + 技能点（本人或 GM 可操作）。
const sheet = computed<Record<string, unknown>>(() => (props.player?.character_sheet as Record<string, unknown>) || {})
const pendingClassLevels = computed(() => Number(sheet.value.pending_class_levels || 0))
const skillPoints = computed(() => Number(sheet.value.skill_points || 0))
const classLevels = computed<Record<string, number>>(() => {
  const raw = sheet.value.class_levels
  return raw && typeof raw === 'object' ? raw as Record<string, number> : {}
})
const classChoices = computed(() => Object.keys(classLevels.value).filter(name => Number(classLevels.value[name] || 0) < 10))
const skillChoices = computed(() => ((sheet.value.skills as Array<Record<string, unknown>>) || [])
  .map(row => String(row.name || ''))
  .filter(Boolean))
const hasGrowth = computed(() => pendingClassLevels.value > 0 || skillPoints.value > 0 || pendingAttributeSteps.value > 0)
const pendingAttributeSteps = computed(() => Number(sheet.value.pending_attribute_steps || 0))
const attributeChoices = computed(() => Object.keys((sheet.value.attributes as Record<string, unknown>) || {})
  .filter(key => Number(((sheet.value.attributes as Record<string, unknown>) || {})[key] || 0) < 12))
const attributeChoice = ref('')
function attrValue(key: string): number {
  const attrs = (sheet.value.attributes as Record<string, unknown>) || {}
  return Number(attrs[key] || 0)
}
const classChoice = ref('')
const skillChoice = ref('')
const growthBusy = ref(false)
const growthError = ref('')
watch(classChoices, (choices) => { if (!choices.includes(classChoice.value)) classChoice.value = choices[0] || '' }, { immediate: true })
watch(skillChoices, (choices) => { if (!choices.includes(skillChoice.value)) skillChoice.value = choices[0] || '' }, { immediate: true })
watch(attributeChoices, (choices) => { if (!choices.includes(attributeChoice.value)) attributeChoice.value = choices[0] || '' }, { immediate: true })
async function doAttributeStep() {
  if (!attributeChoice.value || growthBusy.value || !props.player) return
  growthBusy.value = true
  growthError.value = ''
  try {
    await applyAttributeStep(props.detail.game_key, props.player.user_id, attributeChoice.value, props.detail.run_id)
    emit('changed')
  } catch (e) {
    growthError.value = e instanceof Error ? e.message : String(e)
  } finally {
    growthBusy.value = false
  }
}
async function doAssignClass() {
  if (!classChoice.value || growthBusy.value || !props.player) return
  growthBusy.value = true
  growthError.value = ''
  try {
    await assignClassLevel(props.detail.game_key, props.player.user_id, classChoice.value, props.detail.run_id)
    emit('changed')
  } catch (e) {
    growthError.value = e instanceof Error ? e.message : String(e)
  } finally {
    growthBusy.value = false
  }
}
async function doSpendSkill() {
  if (!skillChoice.value || growthBusy.value || !props.player) return
  growthBusy.value = true
  growthError.value = ''
  try {
    await spendSkillPoint(props.detail.game_key, props.player.user_id, skillChoice.value, props.detail.run_id)
    emit('changed')
  } catch (e) {
    growthError.value = e instanceof Error ? e.message : String(e)
  } finally {
    growthBusy.value = false
  }
}
const recentPerceptions = computed(() => props.privateMessages.slice(-3).reverse())
function fmtDecision(d: Decision | string) {
  if (typeof d === 'string') return d
  const text = d.title || d.summary || d.description
  if (text) return (d.round_number ? `${t('roundLabel', { round: d.round_number })} · ` : '') + text
  return JSON.stringify(d)
}
function stateLabel(state?: string) {
  const labels: Record<string, string> = {
    active_action: t('actionPhase'),
    active_judgment: t('gmThinking'),
    paused: t('statePaused'),
    waiting: t('stateWaiting'),
    created: t('stateCreating'),
    ended: t('stateEnded'),
  }
  return (state && labels[state]) || state || t('stateUnknown')
}
function perceptionText(m: PrivateMessage | string) {
  return typeof m === 'string' ? m : m.text || ''
}
function perceptionKey(m: PrivateMessage | string, i: number) {
  if (typeof m === 'string') return `${i}:${m}`
  return `${m.user_id || ''}:${m.round || ''}:${m.timestamp || ''}:${m.text || i}`
}
</script>

<template>
  <aside class="game-sidebar" :class="{ collapsed }">
    <button class="sidebar-toggle" type="button" @click="emit('toggle-sidebar')" :title="toggleLabel" :aria-label="toggleLabel" :aria-expanded="!collapsed">
      <NIcon :component="collapsed ? ChevronForward : ChevronBack" size="16" />
    </button>
    <CharacterPanel :player="player" :rule-meta="ruleMeta" :portrait-editable="portraitEditable" :show-advanced-center="showAdvancedCenter" @portrait-click="emit('portrait-click')" @allocate-level-up="(attrs) => emit('allocate-level-up', attrs)" @open-character-center="emit('open-character-center')" />

    <details v-if="hasGrowth" class="panel sidebar-disclosure" open>
      <summary><SidebarRowIcon :icon="Activity" /><strong>{{ t('advancement') }}</strong><span>{{ pendingAttributeSteps + pendingClassLevels + skillPoints }}</span></summary>
      <div class="sidebar-disclosure-body">
        <div v-if="pendingAttributeSteps" class="growth-row">
          <p class="muted">{{ t('pendingAttributeSteps', { count: pendingAttributeSteps }) }}</p>
          <select v-model="attributeChoice" class="growth-select">
            <option v-for="key in attributeChoices" :key="key" :value="key">{{ key }} · d{{ attrValue(key) }}</option>
          </select>
          <button type="button" class="growth-btn" :disabled="growthBusy || !attributeChoice" @click="doAttributeStep">{{ t('assignAttributeStep') }}</button>
        </div>
        <div v-if="pendingClassLevels" class="growth-row">
          <p class="muted">{{ t('pendingClassLevels', { count: pendingClassLevels }) }}</p>
          <select v-model="classChoice" class="growth-select">
            <option v-for="name in classChoices" :key="name" :value="name">{{ name }} · Lv.{{ classLevels[name] || 0 }}</option>
          </select>
          <button type="button" class="growth-btn" :disabled="growthBusy || !classChoice" @click="doAssignClass">{{ t('assignClassLevel') }}</button>
        </div>
        <div v-if="skillPoints" class="growth-row">
          <p class="muted">{{ t('skillPointsAvailable', { count: skillPoints }) }}</p>
          <select v-model="skillChoice" class="growth-select">
            <option v-for="name in skillChoices" :key="name" :value="name">{{ name }}</option>
          </select>
          <button type="button" class="growth-btn" :disabled="growthBusy || !skillChoice" @click="doSpendSkill">{{ t('raiseSkill') }}</button>
        </div>
        <p v-if="growthError" class="form-hint warn">{{ growthError }}</p>
      </div>
    </details>

    <slot name="after-character" />

    <details class="panel sidebar-disclosure" v-if="hasPlot">
      <summary><SidebarRowIcon :icon="ScrollText" /><strong>{{ t('plotTracker') }}</strong><span>{{ plotCount }}</span></summary>
      <div class="sidebar-disclosure-body">
      <div v-if="clockControls" class="quest-group">
        <div class="growth-row">
          <input v-model="fuClockTitle" class="growth-input" :placeholder="t('clockTitlePlaceholder')">
          <select v-model.number="fuClockSegments" class="growth-select">
            <option v-for="n in 9" :key="n" :value="n + 3">{{ t('clockSegmentsOption', { count: n + 3 }) }}</option>
          </select>
          <button type="button" class="growth-btn" :disabled="!fuClockTitle.trim()" @click="addClock">{{ t('clockAdd') }}</button>
        </div>
      </div>
      <div v-if="activeClocks.length" class="quest-group">
        <strong class="quest-head">{{ t('clocks') }}</strong>
        <div v-for="(c, i) in activeClocks" :key="'clock' + i" class="clock-item">
          <div class="clock-head">
            <span class="clock-title">{{ c.title }}</span>
            <span class="clock-actions">
              <button
                v-if="isGm"
                type="button"
                class="clock-step"
                :disabled="busyClock === c.clock_id"
                :aria-label="t('clockStepBack')"
                @click="stepClock(c, -1)"
              >−</button>
              <span class="muted">{{ c.filled || 0 }}/{{ c.segments || 0 }}</span>
              <button
                v-if="isGm"
                type="button"
                class="clock-step"
                :disabled="busyClock === c.clock_id"
                :aria-label="t('clockStepForward')"
                @click="stepClock(c, 1)"
              >＋</button>
              <button v-if="clockControls" type="button" class="clock-step" :title="t('clockRename')" @click="startRenameClock(c)">✎</button>
              <button v-if="clockControls" type="button" class="clock-step" :title="t('delete')" @click="askDeleteClock(c)">×</button>
            </span>
          </div>
          <div v-if="fuClockRenameId === c.clock_id" class="growth-row">
            <input v-model="fuClockRenameTitle" class="growth-input" :placeholder="t('clockRenamePlaceholder')">
            <button type="button" class="growth-btn" @click="commitRenameClock">{{ t('saveAction') }}</button>
            <button type="button" class="growth-btn" @click="fuClockRenameId = ''">{{ t('cancel') }}</button>
          </div>
          <div v-if="fuClockDeleteId === c.clock_id" class="growth-row">
            <span class="muted">{{ t('clockDeleteConfirm') }}</span>
            <button type="button" class="growth-btn" @click="confirmDeleteClock">{{ t('confirmDelete') }}</button>
            <button type="button" class="growth-btn" @click="fuClockDeleteId = ''">{{ t('cancel') }}</button>
          </div>
          <div class="clock-track" role="progressbar" :aria-valuenow="c.filled || 0" :aria-valuemin="0" :aria-valuemax="c.segments || 0">
            <span
              v-for="n in (c.segments || 0)"
              :key="n"
              class="clock-pip"
              :class="{ filled: n <= (c.filled || 0), threat: c.kind === '威胁' || c.kind === 'threat' }"
            />
          </div>
        </div>
      </div>
      <div v-if="bonds.length" class="quest-group">
        <strong class="quest-head">{{ t('bonds') }}</strong>
        <div v-for="(b, i) in bonds" :key="'bond' + i" class="quest-item">
          <strong>{{ b.owner }} → {{ b.target }}</strong>
          <span class="muted"> {{ (b.emotions || []).join(t('listSeparator')) }}<template v-if="(b.emotions || []).length">{{ t('bondEmotionCount', { count: (b.emotions || []).length }) }}</template></span>
        </div>
      </div>
      <div v-if="activeQuests.length" class="quest-group">
        <strong class="quest-head">{{ t('currentQuests') }}</strong>
        <div v-for="(q, i) in activeQuests" :key="'qa' + i" class="quest-item"><strong>{{ q.title }}</strong><span v-if="q.progress" class="muted"> {{ q.progress }}</span></div>
      </div>
      <div v-if="doneQuests.length" class="quest-group">
        <strong class="quest-head muted">{{ t('finishedQuests') }}</strong>
        <div v-for="(q, i) in doneQuests" :key="'qd' + i" class="quest-item" :class="q.status === 'completed' ? 'good' : 'warn'"><strong>{{ q.title }}</strong> <span class="muted">{{ q.status }}</span></div>
      </div>
      <div v-if="notableRelations.length" class="quest-group">
        <strong class="quest-head">{{ t('npcRelations') }}</strong>
        <div v-for="(r, i) in notableRelations" :key="'r' + i" class="quest-item"><strong>{{ r.npc_name }}</strong> <span class="muted">{{ r.tier }}</span></div>
      </div>
      <div v-if="recentDecisions.length" class="quest-group">
        <strong class="quest-head">{{ t('recentDecisions') }}</strong>
        <div v-for="(d, i) in recentDecisions" :key="'d' + i" class="quest-item muted">{{ fmtDecision(d) }}</div>
      </div>
      </div>
    </details>

    <details class="panel sidebar-disclosure sidebar-map">
      <summary><SidebarRowIcon :icon="MapIcon" /><strong>{{ t('mapTitle') }}</strong></summary>
      <div class="sidebar-disclosure-body">
        <button class="sidebar-map-open" @click="emit('open-map')">{{ t('mapOpenLarge') }}</button>
        <MapGraph :map="map" :current-scene="detail.scene" :show-header="false" @lore-click="emit('lore-click', $event)" />
      </div>
    </details>

    <details class="panel sidebar-disclosure" :open="privateMessages.length > 0">
      <summary><SidebarRowIcon :icon="Eye" /><strong>{{ t('characterPerception') }}</strong><span>{{ privateMessages.length }}</span></summary>
      <div class="sidebar-disclosure-body">
      <div v-if="privateMessages.length" class="perceptions">
        <p
          v-for="(m, i) in recentPerceptions"
          :key="perceptionKey(m, i)"
          class="perception-item"
          :class="{ fresh: i === 0 }"
        >{{ perceptionText(m) }}</p>
      </div>
      <p v-else class="muted">{{ t('noPrivatePerception') }}</p>
      </div>
    </details>

    <slot name="after-perception" />

    <details class="panel sidebar-disclosure">
      <summary><SidebarRowIcon :icon="Activity" /><strong>{{ t('statusInfo') }}</strong><span>{{ stateLabel(detail.state) }}</span></summary>
      <div class="sidebar-disclosure-body">
      <div class="chips"><span>{{ detail.solo_mode ? t('solo') : t('multiplayer') }}</span><span>{{ stateLabel(detail.state) }}</span></div>
      <details><summary>{{ t('coordination') }}</summary>
        <p>{{ t('readyProgress', { ready: detail.multiplayer?.ready_count || 0, total: detail.multiplayer?.active_count ?? detail.multiplayer?.player_count ?? 0 }) }}</p>
        <p>{{ t('waitingList', { names: detail.multiplayer?.waiting_players?.map((p: Player) => p.character_name).join(t('listSeparator')) || t('none') }) }}</p>
        <p v-if="detail.multiplayer?.away_players?.length">{{ t('awayList', { names: detail.multiplayer.away_players.map((p: Player) => p.character_name).join(t('listSeparator')) }) }}</p>
      </details>
      </div>
    </details>
  </aside>
</template>

<style scoped>
.clock-item { margin: 4px 0 8px; }
.clock-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 12px; }
.clock-title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.clock-actions { display: inline-flex; align-items: center; gap: 4px; flex: 0 0 auto; }
.clock-step { border: 1px solid rgba(127, 127, 127, 0.35); background: transparent; color: inherit; border-radius: 4px; width: 18px; height: 18px; line-height: 1; font-size: 12px; cursor: pointer; padding: 0; }
.clock-step:disabled { opacity: 0.45; cursor: default; }
.clock-step:not(:disabled):hover { background: rgba(127, 127, 127, 0.18); }
.growth-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 6px 0; font-size: 12px; }
.growth-row .muted { flex: 1 1 100%; margin: 0; }
.growth-select { flex: 1 1 auto; min-width: 0; font-size: 12px; }
.growth-btn { border: 1px solid rgba(127, 127, 127, 0.35); background: transparent; color: inherit; border-radius: 4px; padding: 2px 8px; font-size: 12px; cursor: pointer; }
.growth-btn:disabled { opacity: 0.45; cursor: default; }
.clock-track { display: flex; gap: 2px; margin-top: 4px; }
.clock-pip { flex: 1 1 0; min-width: 4px; height: 6px; border-radius: 2px; background: rgba(127, 127, 127, 0.28); transition: background 0.2s ease; }
.clock-pip.filled { background: #7aa2f7; }
.clock-pip.threat.filled { background: #e05555; }
</style>
