<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import {
  PlayForwardOutline, ArrowUndoOutline, ShareOutline, BugOutline,
  PersonOutline, BookOutline, PeopleOutline, LockOpenOutline, LockClosedOutline,
  SettingsOutline, PlaySkipForwardOutline, DownloadOutline, RefreshOutline,
  TrashOutline, SendOutline, ImageOutline, MapOutline,
  ReaderOutline,
  CashOutline,
  StopCircleOutline,
} from '@vicons/ionicons5'
import type { GameDetail, GmStyle, LogEntry, Player, StoryMode } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import GmWorkspace from '@/features/play/workbench/GmWorkspace.vue'
import { gameLorebookCopy } from './gameLorebookCopy'
import StoryPortraits from '@/features/play/story/StoryPortraits.vue'
import GameSessionPreferences from '@/features/play/preferences/GameSessionPreferences.vue'
import GameDefaultActions from '@/features/play/preferences/GameDefaultActions.vue'
import RunStoryPanel from '@/features/play/run-story/RunStoryPanel.vue'
import RunFinalePanel from '@/features/play/run-story/RunFinalePanel.vue'
import { pendingPostChecks } from '@/features/play/check-decisions/projection'

const props = defineProps<{ detail: GameDetail; players: Player[]; isGm: boolean; recapBusy?: boolean; manualImageEnabled?: boolean; manualImageBusy?: boolean; pendingCount?: number; needsAttention?: boolean; gameKey?: string; log?: LogEntry[]; ruleId?: string }>()
// 强制推进 = 中止在飞生成并重新处理本回合；只有本轮真的在判定/生成时才有意义
// （平时点它等同于「推进」，所以平时禁用，避免两个按钮行为重复）。
const forceAdvanceAvailable = computed(() => props.detail?.state === 'active_judgment')
// 判定阶段仍有未处理的幸运 / 孤注一掷：同一个强制推进会按原结果关闭它们。
const continueAsFailure = computed(() => forceAdvanceAvailable.value && pendingPostChecks(props.detail).length > 0)
const emit = defineEmits<{
  advance: []
  'force-advance': []
  rollback: []
  recap: []
  invite: []
  'bot-bind': []
  mode: []
  'narrative-perspective': [perspective: 'auto' | 'immersive' | 'third_person']
  'story-mode': [payload: { mode: StoryMode }]
  'gm-style': [payload: { gm_style: GmStyle | null }]
  'advancement-control': [payload: Record<string, string | number>]
  access: []
  command: [text: string]
  perception: [uid: string, text: string]
  export: []
  reset: []
  restart: []
  cards: []
  'world-switch': []
  'lorebooks': []
  'game-settings': []
  'scene-image': []
  'map-background': []
  payment: []
  'generate-current-round': []
  refresh: []
}>()

const cmdText = ref('')
const percTarget = ref('')
const percText = ref('')
const xpAwards = reactive<Record<string, number>>({})
const workspace = ref<InstanceType<typeof GmWorkspace> | null>(null)
const preferencesRevision = ref(0)
defineExpose({ openSection: (section: 'todo' | 'party' | 'scene' | 'rules' | 'settings') => workspace.value?.select(section) })
const { t, locale } = useLocale()
const gameLorebookText = computed(() => gameLorebookCopy(locale.value))

// 当前对局 GM 叙事风格：detail.gm_style_override === null/undefined → 跟随世界；
// 显式 dict（含全缺省）→ 对局覆盖。draft 用纯 string 承载控件值，保存时收敛。
const gmStyleFollowWorld = computed(() => props.detail?.gm_style_override == null)
const gmStyleDraft = reactive({ tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' })
watch(() => props.detail?.gm_style_override, (override) => {
  const style: GmStyle = override ?? {}
  gmStyleDraft.tone = String(style.tone ?? '')
  gmStyleDraft.verbosity = String(style.verbosity ?? 'normal')
  gmStyleDraft.pace = String(style.pace ?? 'normal')
  gmStyleDraft.custom_instructions = String(style.custom_instructions ?? '')
}, { immediate: true })

const tonePresets = [
  { value: '', key: 'gmStyleToneDefault' },
  { value: 'literary', key: 'gmStyleToneLiterary' },
  { value: 'direct', key: 'gmStyleToneDirect' },
  { value: 'humorous', key: 'gmStyleToneHumorous' },
  { value: 'dark', key: 'gmStyleToneDark' },
] as const
const verbosityOptions = [
  { value: 'brief', key: 'gmStyleVerbosityBrief' },
  { value: 'normal', key: 'gmStyleVerbosityNormal' },
  { value: 'detailed', key: 'gmStyleVerbosityDetailed' },
] as const
const paceOptions = [
  { value: 'slow', key: 'gmStylePaceSlow' },
  { value: 'normal', key: 'gmStylePaceNormal' },
  { value: 'fast', key: 'gmStylePaceFast' },
] as const

function saveGmStyle() {
  // draft 用宽松 string 承载控件值；服务端 normalize 才是取值 authority。
  emit('gm-style', { gm_style: { ...gmStyleDraft } as GmStyle })
}
function setGmStyleField(field: 'tone' | 'verbosity' | 'pace', value: string) {
  gmStyleDraft[field] = value
  saveGmStyle()
}
function setGmStyleSource(followWorld: boolean) {
  if (followWorld) {
    emit('gm-style', { gm_style: null })
    return
  }
  // 关闭"跟随世界"= 显式中性覆盖（区别于 null），随后控件可编辑。
  Object.assign(gmStyleDraft, { tone: '', verbosity: 'normal', pace: 'normal', custom_instructions: '' })
  saveGmStyle()
}

function run() { if (cmdText.value.trim()) { emit('command', cmdText.value.trim()); cmdText.value = '' } }
function sendPerc() { if (percTarget.value && percText.value.trim()) { emit('perception', percTarget.value, percText.value.trim()); percText.value = '' } }
function changeNarrativePerspective(event: Event) {
  const perspective = (event.target as HTMLSelectElement).value
  if (perspective === 'auto' || perspective === 'immersive' || perspective === 'third_person') {
    emit('narrative-perspective', perspective)
  }
}
function changeStoryMode(event: Event) {
  const mode = (event.target as HTMLSelectElement).value
  if (mode === 'prose' || mode === 'dialogue') emit('story-mode', { mode })
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
    <GmWorkspace ref="workspace" :round="detail.round_number || 0" :pending-count="pendingCount" :needs-attention="needsAttention">
      <template #todo>
        <div class="gm-group gm-flow-group">
          <h4>{{ t('flow') }}</h4>
          <button @click="emit('advance')"><NIcon :component="PlayForwardOutline" size="14" /> {{ t('advance') }}</button>
          <button
            type="button"
            :disabled="!forceAdvanceAvailable"
            :title="t(continueAsFailure ? 'gmContinueAsFailureHint' : 'gmForceAdvanceHint')"
            @click="emit('force-advance')"
          ><NIcon :component="StopCircleOutline" size="14" /> {{ t(continueAsFailure ? 'gmContinueAsFailure' : 'gmForceAdvance') }}</button>
          <button @click="emit('rollback')"><NIcon :component="ArrowUndoOutline" size="14" /> {{ t('rollback') }}</button>
          <button @click="emit('payment')"><NIcon :component="CashOutline" size="14" /> {{ t('createPaymentProposal') }}</button>
          <button :disabled="recapBusy" @click="emit('recap')"><NIcon :component="ReaderOutline" size="14" /> {{ recapBusy ? t('storyRecapGenerating') : t('storyRecapGenerate') }}</button>
        </div>
        <slot name="todo" />
      </template>
      <template #party>
        <details class="perc gm-perc gm-console-section" open><summary>{{ t('players') }}</summary>
          <div class="gm-console-section-actions">
            <button @click="emit('invite')"><NIcon :component="ShareOutline" size="14" /> {{ t('inviteLink') }}</button>
            <button @click="emit('bot-bind')" :title="t('botBindCopied')"><NIcon :component="BugOutline" size="14" /> {{ t('botBind') }}</button>
            <button @click="emit('cards')"><NIcon :component="PersonOutline" size="14" /> {{ t('characterPerspective') }}</button>
          </div>
        </details>
        <details class="perc gm-perc gm-console-section" open><summary>{{ t('perceptionPrivate') }}</summary>
          <div class="perc-row">
            <select v-model="percTarget"><option value="">{{ t('chooseCharacter') }}</option><option v-for="p in players" :key="p.user_id" :value="p.user_id">{{ p.character_name }}</option></select>
            <input v-model="percText" :placeholder="t('perceptionPlaceholder')" @keydown.enter="sendPerc">
            <button @click="sendPerc"><NIcon :component="SendOutline" size="14" /> {{ t('send') }}</button>
          </div>
        </details>

        <slot name="party" />
      </template>
      <template #scene>
        <RunStoryPanel v-if="gameKey" :game-key="gameKey" :revision="`${detail.round_number || 0}:${detail.gm_workspace_revision || ''}:${detail.run_lifecycle?.status}`" @changed="emit('refresh')" />
        <RunFinalePanel v-if="gameKey" :game-key="gameKey" :revision="`${detail.round_number || 0}:${detail.gm_workspace_revision || ''}:${detail.run_lifecycle?.status}`" :is-gm="isGm" @changed="emit('refresh')" />
        <div class="gm-group gm-scene-group">
          <button v-if="manualImageEnabled" :disabled="manualImageBusy" @click="emit('generate-current-round')"><NIcon :component="ImageOutline" size="14" /> {{ manualImageBusy ? t('generatingImage') : t('generateRoundImage') }}</button>
          <button @click="emit('scene-image')"><NIcon :component="ImageOutline" size="14" /> {{ t('sceneImageManage') }}</button>
          <button @click="emit('map-background')"><NIcon :component="MapOutline" size="14" /> {{ t('mapBackgroundManage') }}</button>
          <button @click="emit('world-switch')"><NIcon :component="BookOutline" size="14" /> {{ t('switchLorebook') }}</button>
          <button data-testid="gm-game-lorebooks" @click="emit('lorebooks')"><NIcon :component="BookOutline" size="14" /> {{ gameLorebookText.title }}</button>
        </div>
        <slot name="scene" />
      </template>
      <template #rules>
        <GameSessionPreferences v-if="gameKey" :game-key="gameKey" :revision="preferencesRevision" @changed="emit('refresh')" />
        <details v-if="detail.ruleset_runtime?.id === 'core:dnd2024' && detail.advancement" class="perc gm-perc gm-console-section gm-advancement-section" open>
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

        <slot name="rules" />
      </template>
      <template #settings>
        <GameSessionPreferences v-if="gameKey" section="actions" :game-key="gameKey" :revision="preferencesRevision" @changed="emit('refresh')" />
        <GameDefaultActions v-if="gameKey" :game-key="gameKey" @changed="preferencesRevision++; emit('refresh')" />
        <button @click="emit('game-settings')"><NIcon :component="SettingsOutline" size="14" /> {{ t('gameSettings') }}</button>
        <div class="gm-group gm-grow gm-command-group">
          <h4>{{ t('commandGroup') }}</h4>
          <input v-model="cmdText" :placeholder="t('gmCommandPlaceholder')" @keydown.enter="run">
          <button @click="run"><NIcon :component="PlaySkipForwardOutline" size="14" /> {{ t('execute') }}</button>
        </div>

        <details class="perc gm-perc gm-console-section" open><summary>{{ t('gmNarrativeStyleTitle') }}</summary>
          <div class="gm-console-section-actions">
            <label class="gm-narrative-setting">
              <span>{{ t('narrativePerspective') }}</span>
              <select :value="detail.narrative_perspective || 'auto'" @change="changeNarrativePerspective">
                <option value="auto">{{ t('narrativeAuto') }}</option>
                <option value="immersive">{{ t('narrativeImmersive') }}</option>
                <option value="third_person">{{ t('narrativeThirdPerson') }}</option>
              </select>
              <small>{{ t('narrativeChangeHint') }}</small>
            </label>
            <label class="gm-narrative-setting">
              <span>{{ t('storyMode') }}</span>
              <select data-testid="story-mode-select" :value="detail.story_mode || 'prose'" @change="changeStoryMode">
                <option value="prose">{{ t('storyModeProse') }}</option>
                <option value="dialogue">{{ t('storyModeDialogue') }}</option>
              </select>
              <small>{{ t('storyModeHint') }}</small>
            </label>
            <div class="gm-narrative-setting">
              <span>{{ t('gmStyleTitle') }}</span>
              <div class="gm-style-source-options">
                <button type="button" :class="{ active: gmStyleFollowWorld }" @click="setGmStyleSource(true)">{{ t('gmStyleFollowWorld') }}</button>
                <button type="button" :class="{ active: !gmStyleFollowWorld }" @click="setGmStyleSource(false)">{{ t('gmStyleOverrideCurrent') }}</button>
              </div>
              <small>{{ t('gmStyleHint') }}</small>
            </div>
            <template v-if="!gmStyleFollowWorld">
              <label class="gm-narrative-setting">
                <span>{{ t('gmStyleTone') }}</span>
                <div class="gm-style-options">
                  <button v-for="preset in tonePresets" :key="preset.value" type="button"
                          :class="{ active: gmStyleDraft.tone === preset.value }"
                          @click="setGmStyleField('tone', preset.value)">{{ t(preset.key) }}</button>
                </div>
              </label>
              <label class="gm-narrative-setting">
                <span>{{ t('gmStyleVerbosity') }}</span>
                <div class="gm-style-options">
                  <button v-for="option in verbosityOptions" :key="option.value" type="button"
                          :class="{ active: gmStyleDraft.verbosity === option.value }"
                          @click="setGmStyleField('verbosity', option.value)">{{ t(option.key) }}</button>
                </div>
              </label>
              <label class="gm-narrative-setting">
                <span>{{ t('gmStylePace') }}</span>
                <div class="gm-style-options">
                  <button v-for="option in paceOptions" :key="option.value" type="button"
                          :class="{ active: gmStyleDraft.pace === option.value }"
                          @click="setGmStyleField('pace', option.value)">{{ t(option.key) }}</button>
                </div>
              </label>
              <label class="gm-narrative-setting">
                <span>{{ t('gmStyleCustom') }}</span>
                <textarea v-model="gmStyleDraft.custom_instructions" rows="3" maxlength="2000"
                          :placeholder="t('gmStyleCustomPlaceholder')" @change="saveGmStyle"></textarea>
              </label>
            </template>
            <details v-if="gameKey && log" class="perc gm-perc gm-console-section" open>
              <summary>{{ t('storyPortraits') }}</summary>
              <StoryPortraits :game-key="gameKey" :log="log" :rule-id="ruleId" @refresh="emit('refresh')" />
            </details>
            <button @click="emit('access')"><NIcon :component="detail.player_access_open === false ? LockOpenOutline : LockClosedOutline" size="14" /> {{ detail.player_access_open === false ? t('openAccess') : t('closeAccess') }}</button>
          </div>
        </details>

        <details class="perc gm-perc gm-console-section" open><summary>{{ t('saveGroup') }}</summary>
          <div class="gm-console-section-actions">
            <button @click="emit('mode')"><NIcon :component="PeopleOutline" size="14" /> {{ t('switchToMode', { mode: detail.solo_mode ? t('multiplayer') : t('solo') }) }}</button>
            <button @click="emit('export')"><NIcon :component="DownloadOutline" size="14" /> {{ t('export') }}</button>
            <button @click="emit('restart')"><NIcon :component="RefreshOutline" size="14" /> {{ t('restart') }}</button>
            <button class="danger" @click="emit('reset')"><NIcon :component="TrashOutline" size="14" /> {{ t('reset') }}</button>
          </div>
        </details>
      </template>
    </GmWorkspace>
  </section>
</template>
