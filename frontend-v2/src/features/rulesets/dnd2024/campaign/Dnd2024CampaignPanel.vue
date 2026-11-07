<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { fetchRulesetAvailableActions, submitRulesetIntent } from '@/features/rulesets/dnd2024/api'
import type {
  JsonObject,
  MapData,
  RulesetCampaignProposal,
  RulesetGameplayResponse,
  RulesetSessionZeroAgreement,
  RulesetTutorialChoice,
} from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{
  gameKey: string
  actorId: string
  characterName?: string
  sceneName?: string
  worldName?: string
  map?: MapData
  isGm: boolean
  refreshKey?: number
}>()
const emit = defineEmits<{
  refresh: []
  navigate: [target: 'combat']
  'open-map': []
}>()
const { locale } = useLocale()
const zh = computed(() => locale.value.startsWith('zh'))

const data = ref<RulesetGameplayResponse | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const responseComment = ref('')
const initializedRevision = ref(-1)
const agreement = reactive({
  tone: '', difficulty: 'standard', content_rating: 'teen', session_length_minutes: 120,
  pvp_policy: 'consent', lines: '', veils: '', table_rules: '',
})
const proposal = reactive({
  kind: 'task', title: '', summary: '', visibility: 'public', target_id: '',
})
let pollTimer: number | undefined

const copy = computed(() => zh.value ? {
  eyebrow: '新手护航 · 你现在只需要看这块', title: '当前冒险', standardEyebrow: '高级规则 · 标准模式', standardTitle: '当前对局', refresh: '刷新',
  loading: '正在同步战役状态…', sessionZero: '开团约定（Session 0）', sessionIntro: '先把题材、难度、安全边界和桌规说清楚；修改后需要所有玩家重新同意。',
  tone: '基调', difficulty: '难度', rating: '内容分级', minutes: '预计时长（分钟）', pvp: '玩家对抗',
  toneHint: '决定故事的整体气质，不限制角色必须怎样说话。', difficultyHint: '只影响挑战强度；第一次玩建议“标准”。',
  ratingHint: '决定叙事可以出现到什么程度的内容。', pvpHint: '建议仅在所有相关玩家明确同意时允许。',
  lines: '明确避开的内容（一行一项）', veils: '淡出处理的内容（一行一项）', rules: '桌规（一行一项）', coach: '启用教学教练',
  proposeAgreement: '提出这版约定', accept: '我同意这版约定', requestChanges: '请求修改', comment: '给 GM 的说明（可选）', lock: '所有人已同意，锁定约定',
  pending: '等待确认', locked: '约定已锁定', revision: '修订版', responses: '成员反馈',
  records: '战役记录', recordIntro: '任务、线索、事实、重要物品和关系先进入待确认区；只有第二次确认后才成为权威记录。',
  kind: '类型', recordTitle: '标题', summary: '说明', visibility: '可见范围', target: '关联角色（可选）', createProposal: '加入待确认区',
  public: '全员可见', gm: '仅 GM', confirm: '确认写入', reject: '拒绝', noRecords: '还没有已确认的战役记录。',
  tutorial: '正在进行的冒险', startTutorial: '进入当前冒险', minutesShort: '分钟', objective: '当前目标', binding: '内容绑定', bindingHint: '本局的世界书继续提供背景资料；当前剧情节点和场景优先，避免把其他世界的默认开场混进来。', bindingReview: '当前世界书与此冒险包不是推荐组合，请由 GM 确认后继续。',
  requirement: '这里的时间是预计游玩时长，不是倒计时。故事进入遭遇时会自动打开战斗工具。', completed: '短篇冒险已完成；结果已经保存。现在可以在公共行动框继续这个世界的故事。',
  sandboxTitle: '标准自由对局', sandboxIntro: '当前资料册、角色和高级规则均已准备好；回到公共行动框继续游玩。',
  whereAreYou: '当前场景', whereFallback: '当前场景正在读取', sharedContext: '本局共用资料', sharedContextHint: '资料册和地图仍来自 DiceFrame 当前游戏；这里显示的是同一场景，不会另起一套剧情资料。', map: '地图', mapFallback: '尚未设置地图', locationFallback: '当前位置未标记', openMap: '查看地图',
  latest: '最近操作', resolveParty: '结算队伍决定', resolveChoice: 'GM 指定分支', majorityChoice: '按多数意见结算', gmFallback: 'GM 故障兜底', gmFallbackHint: '仅在 AI GM 自动流程无法推进时使用；正常游玩请回到公共行动框。',
  automation: 'AI GM 自动化', automationIntro: '控制 AI GM 何时自动提出检定、队伍决策和战斗请求。规则结果仍由服务器结算。', auto: '自动', assist: '协助', manual: '手动', autoHint: '自动处理高置信度流程', assistHint: '重大推进交给 GM 确认', manualHint: '仅保留规则工具与手动控制',
  quickTitle: '第一次玩？一分钟开始冒险', standardQuickTitle: '快速完成开团设置', quickIntro: '采用推荐的英雄冒险、标准难度、青少年分级和“仅经同意的玩家对抗”，并直接进入第一段教学。以后仍可查看约定。',
  quickStart: '采用推荐设置，立即开始', manualSetup: '手动设置 / 多人开团', multiplayerSteps: '多人开团需要：GM 提出约定 → 每位玩家点同意 → GM 锁定。界面会逐步显示当前该做的按钮。',
} : {
  eyebrow: 'New-player care · look here first', title: 'Current adventure', standardEyebrow: 'Advanced rules · Standard mode', standardTitle: 'Current game', refresh: 'Refresh',
  loading: 'Synchronizing campaign state…', sessionZero: 'Session 0 Agreement', sessionIntro: 'Agree on tone, difficulty, safety boundaries, and table rules first. Every revision needs fresh consent from all players.',
  tone: 'Tone', difficulty: 'Difficulty', rating: 'Content rating', minutes: 'Expected minutes', pvp: 'Player conflict',
  toneHint: 'Sets the overall feel of the story without restricting how a character must behave.', difficultyHint: 'Controls challenge intensity; Standard is recommended for a first game.',
  ratingHint: 'Sets the upper boundary for content that may appear in narration.', pvpHint: 'Consent only is recommended unless every affected player explicitly agrees.',
  lines: 'Lines — do not include (one per line)', veils: 'Veils — fade out (one per line)', rules: 'Table rules (one per line)', coach: 'Enable tutorial coach',
  proposeAgreement: 'Propose this agreement', accept: 'Accept this revision', requestChanges: 'Request changes', comment: 'Note for the GM (optional)', lock: 'Everyone accepted — lock agreement',
  pending: 'Awaiting consent', locked: 'Agreement locked', revision: 'Revision', responses: 'Responses',
  records: 'Campaign Records', recordIntro: 'Tasks, clues, facts, important items, and relationships enter a pending area first. A separate confirmation makes them authoritative.',
  kind: 'Type', recordTitle: 'Title', summary: 'Summary', visibility: 'Visibility', target: 'Related character (optional)', createProposal: 'Add pending proposal',
  public: 'Everyone', gm: 'GM only', confirm: 'Confirm record', reject: 'Reject', noRecords: 'No confirmed campaign records yet.',
  tutorial: 'Current adventure', startTutorial: 'Enter current adventure', minutesShort: 'min', objective: 'Current objective', binding: 'Content binding', bindingHint: 'The world book still provides background material; the current adventure node and scene take precedence so another world’s starter scene is not mixed in.', bindingReview: 'This world book is not the adventure package’s recommended pairing. The GM should confirm before continuing.',
  requirement: 'The time shown is an estimate, not a countdown. The combat tool opens when the story reaches an encounter.', completed: 'The short adventure is complete and its outcomes are saved. Continue this world through the shared action composer.',
  sandboxTitle: 'Standard free play', sandboxIntro: 'The selected Worldbook, characters, and advanced rules are ready. Continue through the shared action composer.',
  whereAreYou: 'Current scene', whereFallback: 'The current scene is loading', sharedContext: 'Shared game context', sharedContextHint: 'The world book and map still come from this DiceFrame game.', map: 'Map', mapFallback: 'No map is set', locationFallback: 'No current location is marked', openMap: 'Open map',
  latest: 'Latest action', resolveParty: 'Resolve party decision', resolveChoice: 'GM-selected branch', majorityChoice: 'Resolve by majority', gmFallback: 'GM recovery controls', gmFallbackHint: 'Use only when the AI GM automation cannot advance. Normal play belongs in the shared action composer.',
  automation: 'AI GM automation', automationIntro: 'Controls when the AI GM proposes checks, party decisions, and combat requests. The server still resolves every rules result.', auto: 'Auto', assist: 'Assist', manual: 'Manual', autoHint: 'Advance high-confidence flows automatically', assistHint: 'Leave major transitions for GM confirmation', manualHint: 'Keep rules tools under manual control',
  quickTitle: 'First game? Start in one minute', standardQuickTitle: 'Quick game setup', quickIntro: 'Use recommended heroic tone, Standard difficulty, Teen rating, and consent-only PvP, then enter the first guided scene. You can review the agreement later.',
  quickStart: 'Use recommendations and start', manualSetup: 'Manual / multiplayer setup', multiplayerSteps: 'Multiplayer setup: the GM proposes → every player accepts → the GM locks. The current required button appears at each step.',
})

const gameplay = computed(() => data.value?.gameplay)
const campaign = computed(() => gameplay.value?.campaign)
const session = computed(() => campaign.value?.session_zero)
const tutorial = computed(() => campaign.value?.tutorial)
const hasAdventure = computed(() => Boolean(tutorial.value?.adventure?.id))
const worldBinding = computed(() => campaign.value?.world_binding)
const adventureBinding = computed(() => campaign.value?.adventure_binding)
const actions = computed(() => data.value?.available_actions || [])
const action = (type: string) => actions.value.find(item => item.type === type)
const quickStartAction = computed(() => action('session_zero.quick_start'))
const sceneLabel = computed(() => props.sceneName?.trim() || copy.value.whereFallback)
const worldLabel = computed(() => props.worldName?.trim() || worldBinding.value?.world_id || copy.value.mapFallback)
const mapLabel = computed(() => props.map?.active_map?.name?.trim() || copy.value.mapFallback)
const locationLabel = computed(() => {
  const currentId = String(props.map?.current_location_id || '')
  return props.map?.locations?.find(item => String(item.id || '') === currentId)?.name || copy.value.locationFallback
})
const hasMap = computed(() => Boolean(props.map?.active_map || props.map?.locations?.length))
const pendingProposals = computed(() => campaign.value?.proposals.filter(item => item.status === 'pending') || [])
const entityGroups = computed(() => Object.entries(campaign.value?.entities || {}).filter(([, values]) => values.length))
const activeAgreement = computed(() => session.value?.pending_agreement || session.value?.agreement)
const automationMode = computed(() => String(campaign.value?.automation?.mode || 'assist'))
const automationAction = computed(() => action('automation.set'))
const partyDecision = computed(() => campaign.value?.party_decision)
const partyWindowOpen = computed(() => partyDecision.value?.status === 'open')
const partyResolveAction = computed(() => action('party_decision.resolve'))
const partyChoices = computed(() => partyDecision.value?.choices || tutorial.value?.current_step?.choices || [])
const partyResolveChoice = ref('')

type LabelGroup = 'tone' | 'difficulty' | 'rating' | 'pvp' | 'kind' | 'visibility' | 'status' | 'response' | 'chapter'
const zhLabels: Record<LabelGroup, Record<string, string>> = {
  tone: {
    'Heroic adventure with room for humor': '英雄冒险，保留轻松幽默',
    'Heroic adventure': '英雄冒险',
    'Hopeful mystery': '充满希望的谜团',
    'Dark fantasy with hard choices': '包含艰难抉择的暗黑奇幻',
    'Lighthearted exploration': '轻松探索',
  },
  difficulty: { story: '剧情优先', standard: '标准', challenging: '挑战', lethal: '致命' },
  rating: { family: '全龄', teen: '青少年', mature: '成人' },
  pvp: { disabled: '禁止', consent: '仅经同意', enabled: '允许' },
  kind: { task: '任务', clue: '线索', fact: '事实', item: '重要物品', relationship: '关系' },
  visibility: { public: '全员可见', gm: '仅 GM' },
  status: { pending: '待确认', confirmed: '已确认', rejected: '已拒绝', active: '进行中', completed: '已完成' },
  response: { accept: '同意', request_changes: '请求修改' },
  chapter: { missing_light: '第一章：失踪的路灯', thorn_glade: '第二章：荆棘林地', old_shrine: '第三章：旧圣坛' },
}
const enLabels: Record<LabelGroup, Record<string, string>> = {
  tone: {
    'Heroic adventure with room for humor': 'Heroic adventure with room for humor',
    'Heroic adventure': 'Heroic adventure',
    'Hopeful mystery': 'Hopeful mystery',
    'Dark fantasy with hard choices': 'Dark fantasy with hard choices',
    'Lighthearted exploration': 'Lighthearted exploration',
  },
  difficulty: { story: 'Story-first', standard: 'Standard', challenging: 'Challenging', lethal: 'Lethal' },
  rating: { family: 'Family', teen: 'Teen', mature: 'Mature' },
  pvp: { disabled: 'Disabled', consent: 'Consent only', enabled: 'Enabled' },
  kind: { task: 'Task', clue: 'Clue', fact: 'Fact', item: 'Important item', relationship: 'Relationship' },
  visibility: { public: 'Everyone', gm: 'GM only' },
  status: { pending: 'Pending', confirmed: 'Confirmed', rejected: 'Rejected', active: 'Active', completed: 'Completed' },
  response: { accept: 'Accepted', request_changes: 'Changes requested' },
  chapter: { missing_light: 'Chapter 1: The Missing Light', thorn_glade: 'Chapter 2: Thorn Glade', old_shrine: 'Chapter 3: The Old Shrine' },
}
const tonePresets = [
  'Heroic adventure with room for humor',
  'Hopeful mystery',
  'Dark fantasy with hard choices',
  'Lighthearted exploration',
]
const toneOptions = computed(() => Array.from(new Set([
  ...tonePresets,
  agreement.tone,
])).filter(Boolean))
const defaultTableRuleLabels: Record<string, [string, string]> = {
  'Share spotlight': ['让每位玩家都有表现机会', 'Share spotlight'],
  'Pause when anyone asks': ['任何人提出暂停时立即停下确认', 'Pause when anyone asks'],
}

function enumLabel(group: LabelGroup, value: unknown): string {
  const key = String(value || '')
  return (zh.value ? zhLabels : enLabels)[group][key] || key.replaceAll('_', ' ')
}

function localizeTableRule(value: string): string {
  const pair = defaultTableRuleLabels[value]
  if (pair) return zh.value ? pair[0] : pair[1]
  const translated = Object.values(defaultTableRuleLabels).find(pairValue => pairValue.includes(value))
  return translated ? (zh.value ? translated[0] : translated[1]) : value
}

function canonicalTableRule(value: string): string {
  return Object.entries(defaultTableRuleLabels).find(([, pair]) => pair.includes(value))?.[0] || value
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
}

function intentId(): string {
  return globalThis.crypto?.randomUUID?.() || `campaign-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function copyAgreement(value: RulesetSessionZeroAgreement): void {
  agreement.tone = String(value.tone || '')
  agreement.difficulty = String(value.difficulty || 'standard')
  agreement.content_rating = String(value.content_rating || 'teen')
  agreement.session_length_minutes = Number(value.session_length_minutes || 120)
  agreement.pvp_policy = String(value.pvp_policy || 'consent')
  agreement.lines = (value.lines || []).join('\n')
  agreement.veils = (value.veils || []).join('\n')
  agreement.table_rules = (value.table_rules || []).map(localizeTableRule).join('\n')
}

function hydrateAgreement(): void {
  if (!campaign.value || !session.value) return
  const revision = Number(session.value.revision || 0)
  if (initializedRevision.value === revision) return
  copyAgreement(activeAgreement.value || campaign.value.session_zero_defaults)
  initializedRevision.value = revision
}

async function load(silent = false): Promise<void> {
  if (!props.gameKey || (busy.value && silent)) return
  if (!silent) busy.value = true
  try {
    data.value = await fetchRulesetAvailableActions(props.gameKey)
    error.value = ''
    hydrateAgreement()
  } catch (cause: unknown) {
    if (!silent || !data.value) error.value = cause instanceof Error ? cause.message : String(cause)
  } finally { if (!silent) busy.value = false }
}

async function submit(payload: JsonObject): Promise<void> {
  busy.value = true
  try {
    const payloadWithContext = {
      ...payload,
      intent_id: intentId(),
      expected_version: gameplay.value?.state_version ?? 0,
    }
    data.value = await submitRulesetIntent(props.gameKey, payloadWithContext)
    error.value = ''
    notice.value = String(payload.type || '')
    hydrateAgreement()
    emit('refresh')
    if (
      (payload.type === 'tutorial.choose' || payload.type === 'party_decision.resolve')
      && data.value?.gameplay.campaign?.tutorial.current_step?.requires === 'combat_ended'
      && !data.value.gameplay.campaign.tutorial.requirement_met
    ) emit('navigate', 'combat')
  } catch (cause: unknown) {
    error.value = cause instanceof Error ? cause.message : String(cause)
    await load(true)
  } finally { busy.value = false }
}

function proposeSession(): void {
  void submit({
    type: 'session_zero.propose',
    agreement: {
      tone: agreement.tone,
      difficulty: agreement.difficulty,
      content_rating: agreement.content_rating,
      session_length_minutes: Number(agreement.session_length_minutes),
      pvp_policy: agreement.pvp_policy,
      safety_tool: 'pause_and_check',
      lines: lines(agreement.lines),
      veils: lines(agreement.veils),
      table_rules: lines(agreement.table_rules).map(canonicalTableRule),
    },
  })
}

function respond(response: 'accept' | 'request_changes'): void {
  void submit({ type: 'session_zero.respond', response, comment: responseComment.value })
}

function proposeRecord(): void {
  void submit({ type: 'campaign.propose', ...proposal })
  proposal.title = ''
  proposal.summary = ''
  proposal.target_id = ''
}

function resolveProposal(item: RulesetCampaignProposal, option: 'confirm' | 'reject'): void {
  void submit({ type: 'campaign.proposal.resolve', proposal_id: item.proposal_id, option })
}

function choose(item: RulesetTutorialChoice): void {
  void submit({ type: 'tutorial.choose', choice_id: item.id })
}

function setAutomationMode(mode: 'auto' | 'assist' | 'manual'): void {
  if (mode !== automationMode.value) void submit({ type: 'automation.set', mode })
}

function resolvePartyDecision(): void {
  void submit({
    type: 'party_decision.resolve',
    ...(partyResolveChoice.value ? { choice_id: partyResolveChoice.value } : {}),
  })
}

watch(partyChoices, (choices) => {
  if (partyResolveChoice.value && !choices.some(item => item.id === partyResolveChoice.value)) {
    partyResolveChoice.value = choices[0]?.id || ''
  }
}, { immediate: true })

watch(() => props.gameKey, () => void load())
watch(() => props.refreshKey, () => void load(true))
onMounted(() => {
  void load()
  pollTimer = window.setInterval(() => {
    if (document.visibilityState !== 'hidden') void load(true)
  }, 7000)
})
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <section class="campaign-panel" aria-labelledby="dnd-campaign-title">
    <header class="campaign-head">
      <div><p>{{ hasAdventure ? copy.eyebrow : copy.standardEyebrow }}</p><h2 id="dnd-campaign-title">{{ hasAdventure ? copy.title : copy.standardTitle }}</h2></div>
      <button :disabled="busy" @click="load()">{{ copy.refresh }}</button>
    </header>
    <p v-if="busy && !data" role="status" class="muted">{{ copy.loading }}</p>
    <p v-if="error" role="alert" class="campaign-error">{{ error }}</p>

    <template v-if="campaign && session">
      <section v-if="session.status !== 'locked'" class="campaign-card quick-start-card">
        <span>{{ zh ? '第 1 步' : 'Step 1' }}</span>
        <h3>{{ hasAdventure ? copy.quickTitle : copy.standardQuickTitle }}</h3>
        <p>{{ quickStartAction ? (hasAdventure ? copy.quickIntro : copy.sandboxIntro) : copy.multiplayerSteps }}</p>
        <button v-if="quickStartAction" class="campaign-primary" :disabled="busy" @click="submit({ type: 'session_zero.quick_start' })">{{ copy.quickStart }}</button>
      </section>

      <details :open="session.status !== 'locked' && !quickStartAction" class="campaign-card session-card">
        <summary><strong>{{ session.status === 'locked' ? copy.sessionZero : copy.manualSetup }}</strong><span>{{ session.status === 'locked' ? copy.locked : copy.pending }} · {{ copy.revision }} {{ session.revision }}</span></summary>
        <p class="muted">{{ copy.sessionIntro }}</p>
        <div v-if="isGm" class="agreement-grid">
          <label>{{ copy.tone }}<select v-model="agreement.tone"><option v-for="value in toneOptions" :key="value" :value="value">{{ enumLabel('tone', value) }}</option></select><small>{{ copy.toneHint }}</small></label>
          <label>{{ copy.difficulty }}<select v-model="agreement.difficulty"><option v-for="value in ['story', 'standard', 'challenging', 'lethal']" :key="value" :value="value">{{ enumLabel('difficulty', value) }}</option></select><small>{{ copy.difficultyHint }}</small></label>
          <label>{{ copy.rating }}<select v-model="agreement.content_rating"><option v-for="value in ['family', 'teen', 'mature']" :key="value" :value="value">{{ enumLabel('rating', value) }}</option></select><small>{{ copy.ratingHint }}</small></label>
          <label>{{ copy.minutes }}<input v-model.number="agreement.session_length_minutes" type="number" min="30" max="480" step="30"></label>
          <label>{{ copy.pvp }}<select v-model="agreement.pvp_policy"><option v-for="value in ['disabled', 'consent', 'enabled']" :key="value" :value="value">{{ enumLabel('pvp', value) }}</option></select><small>{{ copy.pvpHint }}</small></label>
          <label class="wide">{{ copy.lines }}<textarea v-model="agreement.lines" rows="2" maxlength="2500" /></label>
          <label class="wide">{{ copy.veils }}<textarea v-model="agreement.veils" rows="2" maxlength="2500" /></label>
          <label class="wide">{{ copy.rules }}<textarea v-model="agreement.table_rules" rows="2" maxlength="2500" /></label>
          <button v-if="action('session_zero.propose')" class="campaign-primary wide" :disabled="busy || !agreement.tone" @click="proposeSession">{{ copy.proposeAgreement }}</button>
        </div>
        <div v-if="activeAgreement" class="agreement-preview">
          <span>{{ enumLabel('tone', activeAgreement.tone) }}</span><span>{{ enumLabel('difficulty', activeAgreement.difficulty) }}</span><span>{{ enumLabel('rating', activeAgreement.content_rating) }}</span><span>{{ activeAgreement.session_length_minutes }} {{ copy.minutesShort }}</span>
        </div>
        <div v-if="session.status === 'pending'" class="response-area">
          <h3>{{ copy.responses }}</h3>
          <ul><li v-for="(value, uid) in session.responses" :key="uid"><b>{{ uid }}</b> · {{ enumLabel('response', value.response) }} <small>{{ value.comment }}</small></li></ul>
          <template v-if="action('session_zero.respond')">
            <input v-model="responseComment" :aria-label="copy.comment" :placeholder="copy.comment" maxlength="500">
            <div><button class="campaign-primary" :disabled="busy" @click="respond('accept')">{{ copy.accept }}</button><button :disabled="busy" @click="respond('request_changes')">{{ copy.requestChanges }}</button></div>
          </template>
          <button v-if="action('session_zero.lock')" class="campaign-primary" :disabled="busy" @click="submit({ type: 'session_zero.lock' })">{{ copy.lock }}</button>
        </div>
      </details>

      <details v-if="session.status === 'locked'" class="campaign-card automation-card">
        <summary><strong>{{ copy.automation }}</strong><span>{{ copy[automationMode as 'auto' | 'assist' | 'manual'] }}</span></summary>
        <p class="muted">{{ copy.automationIntro }}</p>
        <div class="automation-options" role="group" :aria-label="copy.automation">
          <button
            v-for="mode in (['auto', 'assist', 'manual'] as const)"
            :key="mode"
            type="button"
            :class="{ active: automationMode === mode }"
            :disabled="busy || !isGm || !automationAction"
            @click="setAutomationMode(mode)"
          ><b>{{ copy[mode] }}</b><span>{{ copy[`${mode}Hint` as 'autoHint' | 'assistHint' | 'manualHint'] }}</span></button>
        </div>
      </details>

      <section v-if="session.status === 'locked' && tutorial?.status !== 'unavailable'" class="campaign-card adventure-card">
        <header><div><h3>{{ copy.tutorial }}</h3><p class="muted">{{ tutorial?.adventure.summary }}</p></div><span>{{ tutorial?.adventure.estimated_minutes }} {{ copy.minutesShort }}</span></header>
        <aside class="binding-note" :class="{ review: adventureBinding?.compatibility === 'review_required' }">
          <b>{{ copy.binding }}</b>
          <span>{{ worldBinding?.world_id || '—' }} · {{ adventureBinding?.adventure_id || tutorial?.adventure.id }}</span>
          <p>{{ adventureBinding?.compatibility === 'review_required' ? copy.bindingReview : copy.bindingHint }}</p>
        </aside>
        <section class="shared-context" aria-label="共享世界与地图">
          <div>
            <small>{{ copy.sharedContext }}</small>
            <strong>{{ worldLabel }}</strong>
            <span>{{ copy.sharedContextHint }}</span>
          </div>
          <div>
            <small>{{ copy.map }}</small>
            <strong>{{ mapLabel }}</strong>
            <span>{{ locationLabel }}</span>
          </div>
          <button v-if="hasMap" type="button" @click="emit('open-map')">{{ copy.openMap }}</button>
        </section>
        <button v-if="action('tutorial.start')" class="campaign-primary" :disabled="busy" @click="submit({ type: 'tutorial.start', adventure_id: tutorial?.adventure.id })">{{ copy.startTutorial }}</button>
        <template v-else-if="tutorial?.status === 'active' && tutorial.current_step">
          <section class="adventure-status" aria-label="当前冒险状态">
            <header>
              <div><p class="chapter">{{ enumLabel('chapter', tutorial.current_step.chapter_id) }}</p><h4>{{ tutorial.current_step.title }}</h4></div>
              <span>{{ enumLabel('status', tutorial.status) }}</span>
            </header>
            <div>
              <small>{{ copy.whereAreYou }}</small>
              <strong>{{ sceneLabel }}</strong>
            </div>
            <div>
              <small>{{ copy.objective }}</small>
              <strong>{{ tutorial.current_step.objective }}</strong>
            </div>
            <p v-if="!tutorial.requirement_met" class="requirement">{{ copy.requirement }}</p>
          </section>
          <details v-if="isGm && (action('tutorial.choose') || partyResolveAction)" class="gm-fallback">
            <summary><strong>{{ copy.gmFallback }}</strong><span>{{ copy.gmFallbackHint }}</span></summary>
            <div class="gm-fallback-body">
              <div v-if="isGm && partyResolveAction && partyWindowOpen" class="party-resolve">
                <label>{{ copy.resolveChoice }}
                  <select v-model="partyResolveChoice">
                    <option value="">{{ copy.majorityChoice }}</option>
                    <option v-for="item in partyChoices" :key="`resolve-${item.id}`" :value="item.id">{{ item.label }}</option>
                  </select>
                </label>
                <button class="campaign-primary" :disabled="busy || !tutorial.requirement_met" @click="resolvePartyDecision">{{ copy.resolveParty }}</button>
              </div>
              <div v-else class="choice-grid">
                <button v-for="item in tutorial.current_step.choices" :key="item.id" :disabled="busy || !tutorial.requirement_met" @click="choose(item)"><b>{{ item.label }}</b><span>{{ item.description }}</span></button>
              </div>
            </div>
          </details>
        </template>
        <p v-else-if="tutorial?.status === 'completed'" class="complete" role="status">{{ copy.completed }}</p>
        <ol v-if="campaign.chapter_summaries.length" class="chapter-summaries"><li v-for="item in campaign.chapter_summaries" :key="String(item.summary_id)">{{ item.summary }}</li></ol>
      </section>

      <section v-else-if="session.status === 'locked'" class="campaign-card sandbox-card">
        <h3>{{ copy.sandboxTitle }}</h3>
        <p class="beginner-next">{{ copy.sandboxIntro }}</p>
        <div class="agreement-preview"><span>{{ worldLabel }}</span><span>{{ enumLabel('difficulty', session.agreement?.difficulty) }}</span></div>
      </section>

      <section v-if="session.status === 'locked' && gameplay?.combat?.status === 'active'" class="campaign-card combat-redirect">
        <h3>{{ zh ? '现在：完成这场遭遇战' : 'Now: finish this encounter' }}</h3>
        <p>{{ zh ? '战斗中的移动、攻击、法术与资源由服务器结算；工具只显示当前合法动作。' : 'Movement, attacks, spells, and resources are resolved by the server; the tool shows only currently legal actions.' }}</p>
        <button class="campaign-primary" @click="emit('navigate', 'combat')">{{ zh ? '打开战斗工具' : 'Open combat tools' }}</button>
      </section>

      <details v-if="session.status === 'locked'" class="campaign-card records-card">
        <summary><strong>{{ zh ? '可选：战役记录（GM 工具）' : 'Optional: Campaign records (GM tools)' }}</strong><span>{{ copy.records }}</span></summary>
        <p class="muted">{{ copy.recordIntro }}</p>
        <div v-if="isGm && action('campaign.propose')" class="record-form">
          <label>{{ copy.kind }}<select v-model="proposal.kind"><option v-for="value in ['task', 'clue', 'fact', 'item', 'relationship']" :key="value" :value="value">{{ enumLabel('kind', value) }}</option></select></label>
          <label>{{ copy.visibility }}<select v-model="proposal.visibility"><option value="public">{{ copy.public }}</option><option value="gm">{{ copy.gm }}</option></select></label>
          <label>{{ copy.recordTitle }}<input v-model="proposal.title" maxlength="120"></label>
          <label>{{ copy.target }}<input v-model="proposal.target_id" maxlength="120"></label>
          <label class="wide">{{ copy.summary }}<textarea v-model="proposal.summary" rows="2" maxlength="1000" /></label>
          <button class="wide" :disabled="busy || !proposal.title || !proposal.summary" @click="proposeRecord">{{ copy.createProposal }}</button>
        </div>
        <div v-if="pendingProposals.length" class="proposal-list">
          <article v-for="item in pendingProposals" :key="item.proposal_id">
            <span>{{ enumLabel('kind', item.kind) }} · {{ enumLabel('visibility', item.visibility) }}</span><strong>{{ item.title }}</strong><p>{{ item.summary }}</p>
            <div v-if="isGm"><button class="campaign-primary" @click="resolveProposal(item, 'confirm')">{{ copy.confirm }}</button><button @click="resolveProposal(item, 'reject')">{{ copy.reject }}</button></div>
          </article>
        </div>
        <div v-if="entityGroups.length" class="entity-groups">
          <section v-for="([kind, values]) in entityGroups" :key="kind"><h4>{{ enumLabel('kind', kind) }}</h4><article v-for="item in values" :key="item.id"><b>{{ item.title }}</b><span v-if="item.status">{{ enumLabel('status', item.status) }}</span><p>{{ item.summary }}</p></article></section>
        </div>
        <p v-else class="muted">{{ copy.noRecords }}</p>
      </details>

    </template>
    <p v-if="notice" class="sr-only" aria-live="polite">{{ copy.latest }}: {{ notice }}</p>
  </section>
</template>

<style scoped>
.campaign-panel { display: grid; gap: 14px; margin: 14px 0; padding: 16px; border: 1px solid #3f6570; border-radius: 16px; background: linear-gradient(145deg, rgb(16 25 31 / 97%), rgb(22 24 34 / 97%)); color: #edf3f3; box-shadow: 0 18px 48px rgb(0 0 0 / 20%); }
.campaign-head, .campaign-card > header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.binding-note { display: grid; gap: 3px; margin: 12px 0; padding: 10px 12px; border-left: 3px solid #65c9b7; border-radius: 8px; background: rgb(71 176 157 / 11%); }.binding-note span { color: #a9c8c1; font-size: 12px; }.binding-note p { margin: 0; color: #b9ccca; line-height: 1.5; }.binding-note.review { border-left-color: #d5a64f; background: rgb(205 159 72 / 11%); }.binding-note.review span, .binding-note.review p { color: #e5c88b; }
.shared-context { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 10px 12px; border: 1px solid #38545a; border-radius: 10px; background: #101e24; }
.shared-context div { display: grid; min-width: 0; gap: 3px; }
.shared-context small { color: #82bbb8; font-size: 11px; }
.shared-context strong { overflow-wrap: anywhere; color: #e8f2ef; font-size: 13px; }
.shared-context span { overflow-wrap: anywhere; color: #aebfc1; font-size: 12px; line-height: 1.4; }
.shared-context button { min-height: 40px; white-space: nowrap; }
.campaign-head p, .chapter { margin: 0; color: #83c8c5; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.campaign-head h2 { margin: 3px 0 0; font: 700 clamp(19px, 2vw, 25px)/1.2 Georgia, serif; }
.campaign-card { display: grid; gap: 12px; padding: 14px; border: 1px solid #344952; border-radius: 13px; background: rgb(13 20 27 / 86%); }
.quick-start-card { border-color: #568e82; background: linear-gradient(135deg, rgb(35 92 80 / 42%), rgb(13 20 27 / 86%)); }.quick-start-card > span { color: #82d0c0; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }.quick-start-card > p { margin: 0; color: #c1d4d0; line-height: 1.55; }
.beginner-next { padding: 10px 12px; border-left: 4px solid #71c8ba; border-radius: 8px; background: rgb(38 99 89 / 28%); color: #d7ebe7; line-height: 1.6; }
.adventure-status { display: grid; grid-template-columns: minmax(0, .7fr) minmax(0, 1.3fr); gap: 10px; padding: 12px; border: 1px solid #47717a; border-radius: 10px; background: #12242b; }
.adventure-status > header { display: flex; grid-column: 1 / -1; align-items: flex-start; justify-content: space-between; gap: 12px; }
.adventure-status > header h4 { margin: 3px 0 0; }
.adventure-status > header > span { padding: 4px 8px; border-radius: 999px; background: #203b40; color: #bfe2dc; font-size: 11px; }
.adventure-status > div { display: grid; min-width: 0; gap: 3px; }
.adventure-status small { color: #8fc3c1; font-size: 11px; }
.adventure-status strong { overflow-wrap: anywhere; color: #edf6f1; font-size: 13px; line-height: 1.45; }
.adventure-status .requirement { grid-column: 1 / -1; }
.gm-fallback { border-top: 1px solid #2e474e; }
.gm-fallback summary { display: grid; gap: 3px; padding-top: 7px; color: #b9c8ca; }
.gm-fallback summary span { color: #8fa3a6; font-size: 11px; font-weight: 400; line-height: 1.45; }
.gm-fallback-body { display: grid; gap: 9px; padding-top: 10px; }
.campaign-card summary { display: flex; justify-content: space-between; gap: 12px; cursor: pointer; }
.campaign-card h3, .campaign-card h4, .campaign-card p { margin: 0; }
.muted { color: #aebec2; font-size: 13px; line-height: 1.55; }
.campaign-error, .requirement { padding: 9px 11px; border: 1px solid #b75c5c; border-radius: 9px; background: rgb(112 31 31 / 25%); color: #ffd8d8; }
.agreement-grid, .record-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
label { display: grid; gap: 5px; color: #c8d4d5; font-size: 12px; }
label small { color: #aebec2; font-weight: 400; line-height: 1.45; }
button, input, select, textarea, summary { font: inherit; }
button { min-height: 44px; }
input, select, textarea { width: 100%; min-height: 44px; box-sizing: border-box; padding-inline: 10px; border: 1px solid #49616a; border-radius: 8px; background: #0b1218; color: #edf3f3; }
textarea { padding-block: 8px; resize: vertical; }
.wide { grid-column: 1 / -1; }
.check { display: flex; align-items: center; }
.check { min-height: 44px; gap: 9px; }
.check input { width: 22px; height: 22px; min-height: 22px; }
.campaign-primary { border-color: #4e9b96; background: #287b78; color: white; }
.agreement-preview, .response-area > div { display: flex; flex-wrap: wrap; gap: 7px; }
.agreement-preview span { padding: 5px 9px; border-radius: 999px; background: #20343a; color: #cce3e2; font-size: 12px; }
.response-area { display: grid; gap: 8px; }
.response-area ul { margin: 0; padding-left: 20px; }
.response-area small { color: #a9b9bc; }
.proposal-list, .entity-groups, .choice-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 9px; }
.party-resolve { display: flex; align-items: end; gap: 8px; flex-wrap: wrap; }
.party-resolve label { display: grid; flex: 1 1 220px; gap: 5px; color: #c8d5d4; font-size: 12px; }
.party-resolve select { width: 100%; min-height: 42px; padding-inline: 9px; border: 1px solid #52686c; border-radius: 8px; background: #0d171b; color: #f3eee7; }
.automation-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.automation-options button { min-height: 62px; display: grid; gap: 4px; align-content: center; text-align: left; border: 1px solid #3b555c; border-radius: 7px; background: #101a21; color: #edf3f3; }
.automation-options button span { color: #a9b9bc; font-size: 11px; line-height: 1.35; }
.automation-options button.active { border-color: #58aaa5; background: #173237; }
.proposal-list article, .entity-groups section { display: grid; gap: 7px; padding: 11px; border: 1px solid #3a5058; border-radius: 10px; background: #101a21; }
.proposal-list span, .entity-groups h4 { color: #82bbb8; font-size: 11px; text-transform: uppercase; }
.proposal-list div { display: flex; gap: 7px; }
.entity-groups section article { display: grid; grid-template-columns: 1fr auto; gap: 3px 8px; padding-top: 7px; border-top: 1px solid #2c4048; }
.entity-groups section article p { grid-column: 1 / -1; color: #b6c4c7; font-size: 12px; }
.choice-grid button { display: grid; gap: 5px; min-height: 72px; text-align: left; }
.choice-grid span { color: #aab9bd; font-size: 12px; }
.complete { padding: 10px; border-radius: 9px; background: #253927; color: #d9ecd8; }
.chapter-summaries { margin: 0; padding-left: 20px; color: #b9c9ca; font-size: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.campaign-card summary { min-height: 44px; align-items: center; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 3px solid #6ad7cf; outline-offset: 2px; }
:global(body.light .campaign-panel) { border-color: #47757a; background: linear-gradient(145deg, #f7fbfa, #edf5f4); color: #182a2d; box-shadow: 0 14px 34px rgb(28 64 67 / 12%); }
:global(body.light .campaign-card), :global(body.light .proposal-list article), :global(body.light .entity-groups section) { border-color: #a9c6c7; background: #fff; }
:global(body.light .campaign-panel input), :global(body.light .campaign-panel select), :global(body.light .campaign-panel textarea) { border-color: #789a9c; background: #fff; color: #142528; }
:global(body.light .campaign-panel .muted), :global(body.light .campaign-panel .choice-grid span), :global(body.light .campaign-panel .entity-groups section article p), :global(body.light .campaign-panel .chapter-summaries) { color: #3f595c; }
:global(body.light .campaign-panel .party-resolve label) { color: #3f595c; }
:global(body.light .campaign-panel .automation-options button) { border-color: #9bbfbe; background: #f7fbfa; color: #183a3a; }
:global(body.light .campaign-panel .automation-options button.active) { border-color: #397f7b; background: #e1f1ef; }
:global(body.light .campaign-panel .agreement-preview span) { background: #dceceb; color: #244c4b; }
:global(body.light .campaign-panel .beginner-next) { background: #e5f3ef; color: #234b45; }
:global(body.light .campaign-panel .adventure-status) { border-color: #9bbfbe; background: #edf7f5; }
:global(body.light .campaign-panel .adventure-status small) { color: #39716e; }
:global(body.light .campaign-panel .adventure-status strong) { color: #183a3a; }
:global(body.light .campaign-panel .adventure-status > header > span) { background: #dceceb; color: #244c4b; }
:global(body.light .campaign-panel .gm-fallback) { border-color: #b7cbcc; }
:global(body.light .campaign-panel .gm-fallback summary), :global(body.light .campaign-panel .gm-fallback summary span) { color: #3f595c; }
:global(body.light .campaign-panel .shared-context) { border-color: #a9c6c7; background: #f4faf9; }
:global(body.light .campaign-panel .shared-context strong) { color: #183a3a; }
:global(body.light .campaign-panel .shared-context span) { color: #3f595c; }
@media (max-width: 720px) { .campaign-panel { margin-inline: 0; padding: 12px; border-radius: 12px; }.agreement-grid, .record-form, .adventure-status, .shared-context, .automation-options { grid-template-columns: 1fr; }.adventure-status > header, .adventure-status .requirement { grid-column: auto; }.wide { grid-column: auto; }.campaign-head, .campaign-card > header { align-items: stretch; flex-direction: column; }.shared-context button { width: 100%; } }
@media (prefers-reduced-motion: reduce) { .campaign-panel * { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }
</style>
