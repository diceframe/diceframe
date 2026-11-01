<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import {
  CheckmarkCircleOutline,
  ChevronDownOutline,
  HourglassOutline,
  PlayForwardOutline,
  ShieldOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { planRulesetTemporaryEncounter } from '@/features/rulesets/dnd2024/api'
import type {
  JsonObject,
  RulesetGameplayResponse,
  RulesetTemporaryEncounter,
  RulesetTemporaryEncounterEnemy,
} from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'
import { combatCopy } from './combatCopy'
import { friendlyCombatError, intentId, localizedTerm as localizedTermFor } from './combatSupport'

/**
 * Everything that happens before the first initiative roll: the story bridge,
 * encounter choice, party readiness, the AI-drafted temporary encounter and
 * its enemy editor, and the "next encounter" picker after a fight. The panel
 * owns the live HUD and hands intents over through `submit`.
 */
const props = defineProps<{
  gameKey: string
  actorId: string
  isGm: boolean
  data: RulesetGameplayResponse | null
  busy: boolean
  /** Sends one intent through the panel; resolves to false when it was rejected. */
  submit: (payload: JsonObject) => Promise<boolean>
}>()
const emit = defineEmits<{ navigate: [target: 'campaign'] }>()
const { locale, t } = useLocale()
const copy = computed(() => combatCopy(locale.value))
const localizedTerm = (value: unknown): string => localizedTermFor(value, locale.value)

const gameplay = computed(() => props.data?.gameplay)
const combat = computed(() => gameplay.value?.combat)
const actions = computed(() => props.data?.available_actions || [])
const action = (type: string) => actions.value.find(item => item.type === type)

const selectedPresetId = ref('')
const nextEncounterOpen = ref(false)
const manualEncounterOpen = ref(false)
const selectableEncounterPresets = computed(() => (gameplay.value?.encounter_presets || []).filter(
  preset => String(preset.difficulty || '') !== 'tutorial',
))
const selectedPreset = computed(() => gameplay.value?.encounter_presets?.find(
  preset => preset.id === selectedPresetId.value,
))
const guidedCombatStep = computed(() => {
  const step = gameplay.value?.campaign?.tutorial?.current_step
  return step?.requires === 'combat_ended' && step.encounter_preset_id ? step : null
})
const guidedCombatPreset = computed(() => guidedCombatStep.value
  ? gameplay.value?.encounter_presets?.find(preset => preset.id === guidedCombatStep.value?.encounter_preset_id)
  : undefined)
// 非 GM 视图没有预设目录（含怪物数据块，GM-only）；展示只用服务端的无数值摘要。
function encounterSummary(presetId: string | undefined): { name?: string; description?: string } | undefined {
  const preview = gameplay.value?.encounter_preview
  return presetId && preview?.id === presetId ? preview : undefined
}
const guidedCombatSummary = computed(() => (
  guidedCombatPreset.value || encounterSummary(guidedCombatStep.value?.encounter_preset_id)
))
const narrativeCombatPending = computed(() => gameplay.value?.encounter_request?.status === 'pending')
const encounterReadiness = computed(() => gameplay.value?.encounter_request?.readiness)
// 服务端权威模式：story=剧情绑定遭遇，sandbox=自由遭遇，
// blocked+unprepared=活动冒险包尚未配置专业战斗遭遇。
const encounterAccess = computed(() => gameplay.value?.encounter_access)
const encounterMode = computed(() => String(encounterAccess.value?.mode || ''))
const storyUnprepared = computed(() => Boolean(encounterAccess.value?.unprepared))
const sandboxFlow = computed(() => encounterMode.value !== 'story')
const sandboxDeclared = ref(false)
// AI 临时遭遇：AI 生成 → 只读摘要 → 需要时才打开弹窗编辑草稿 → combat.start
// 确认，与手动自由遭遇流程并行；确认走现有 combat.start(mode=sandbox)，服务端
// 权威校验仍然完整生效。草稿只存在于组件状态，刷新丢失可接受，绝不持久化。
type DraftEnemy = RulesetTemporaryEncounterEnemy & {
  selected: boolean
  quantity: number
}
const aiProposal = ref<RulesetTemporaryEncounter | null>(null)
const aiDraft = ref<DraftEnemy[]>([])
const aiBusy = ref(false)
const aiError = ref('')
// 编辑弹窗只在 GM 显式点击「编辑敌人」后打开，并编辑一份工作副本；保存前
// 摘要与提交都只看 aiDraft，因此弹窗里的临时修改不会泄漏到摘要。
const enemyEditorOpen = ref(false)
const editDraft = ref<DraftEnemy[]>([])
const expandedEnemies = ref<string[]>([])
const readyAction = computed(() => action('encounter.ready'))
const unreadyAction = computed(() => action('encounter.unready'))
const isReady = computed(() => Boolean(
  encounterReadiness.value?.ready_player_ids?.includes(props.actorId),
))
const requestedCombatPreset = computed(() => {
  const presetId = gameplay.value?.encounter_request?.encounter_preset_id
  return presetId
    ? selectableEncounterPresets.value.find(preset => preset.id === presetId)
    : undefined
})
const requestedCombatSummary = computed(() => (
  requestedCombatPreset.value || encounterSummary(gameplay.value?.encounter_request?.encounter_preset_id)
))
const canPlanTemporaryEncounter = computed(() => Boolean(
  props.isGm
  && combat.value?.status !== 'active'
  && encounterMode.value !== 'story'
  && !requestedCombatPreset.value
  && !aiProposal.value
  // 冒险包未准备遭遇时，只有 AI 已发出交战请求才开放兜底；
  // 标准自由对局则直接允许 GM 从战斗工具发起临时遭遇。
  && (
    narrativeCombatPending.value
    || Boolean(action('combat.start') || sandboxDeclared.value)
  )
))

// Pre-select a catalogue entry whenever the encounter choice is on screen;
// otherwise the start button would stay disabled until the select is touched.
function syncPresetSelection(): void {
  const guidedPresetId = guidedCombatStep.value?.encounter_preset_id
  const preset = guidedCombatPreset.value || requestedCombatPreset.value || selectableEncounterPresets.value[0]
  const shouldSelectEncounter = Boolean(
    guidedCombatStep.value
    || requestedCombatPreset.value
    || manualEncounterOpen.value
    || nextEncounterOpen.value,
  )
  if (!shouldSelectEncounter) selectedPresetId.value = ''
  else if (guidedPresetId && guidedCombatPreset.value) selectedPresetId.value = guidedPresetId
  else if (!selectableEncounterPresets.value.some(item => item.id === selectedPresetId.value)) {
    selectedPresetId.value = preset?.id || ''
  }
}
watch(() => props.data, () => {
  if (combat.value?.status !== 'ended') nextEncounterOpen.value = false
  syncPresetSelection()
}, { immediate: true })

function openManualEncounter(): void {
  manualEncounterOpen.value = true
  syncPresetSelection()
}

// 与「手动准备遭遇」对称：打开下一场遭遇选择器时立即预选目录条目，
// 否则开始按钮会在用户没碰过下拉框前保持禁用。
function toggleNextEncounter(): void {
  nextEncounterOpen.value = !nextEncounterOpen.value
  if (nextEncounterOpen.value) syncPresetSelection()
}

// GM 明确选择“临时准备自由遭遇”后才允许使用通用目录。
function declareSandboxEncounter(): void {
  sandboxDeclared.value = true
  manualEncounterOpen.value = true
  syncPresetSelection()
}

function cancelAiProposal(): void {
  aiProposal.value = null
  aiDraft.value = []
  aiError.value = ''
  closeEnemyEditor()
}

// 生成/重新生成成功后以 AI 原稿为底稿重建草稿：默认全选、数量 1。
function buildDraftEnemies(proposal: RulesetTemporaryEncounter): DraftEnemy[] {
  return (proposal.enemies || []).map(enemy => ({
    ...enemy,
    attacks: (enemy.attacks || []).map(attack => ({ ...attack })),
    selected: true,
    quantity: 1,
  }))
}

// 工作副本：弹窗里的编辑只有点了「保存修改」才回到 aiDraft。
function cloneDraftEnemies(draft: DraftEnemy[]): DraftEnemy[] {
  return draft.map(enemy => ({
    ...enemy,
    attacks: (enemy.attacks || []).map(attack => ({ ...attack })),
  }))
}

function openEnemyEditor(): void {
  if (!aiProposal.value) return
  editDraft.value = cloneDraftEnemies(aiDraft.value)
  expandedEnemies.value = []
  enemyEditorOpen.value = true
}

function closeEnemyEditor(): void {
  enemyEditorOpen.value = false
  editDraft.value = []
  expandedEnemies.value = []
}

// 取消编辑：丢弃工作副本，摘要与确认仍然使用保存过的 aiDraft。
function cancelEnemyEditor(): void {
  closeEnemyEditor()
}

function saveEnemyEdits(): void {
  aiDraft.value = editDraft.value
  closeEnemyEditor()
}

// 恢复 AI 原稿：丢弃弹窗里的全部编辑，回到未动过的提案（仍需保存才生效）。
function restoreEditDraft(): void {
  if (aiProposal.value) editDraft.value = buildDraftEnemies(aiProposal.value)
}

function isEnemyExpanded(enemy: DraftEnemy): boolean {
  return expandedEnemies.value.includes(enemy.id || '')
}

function toggleEnemyExpanded(enemy: DraftEnemy): void {
  const id = enemy.id || ''
  expandedEnemies.value = isEnemyExpanded(enemy)
    ? expandedEnemies.value.filter(candidate => candidate !== id)
    : [...expandedEnemies.value, id]
}

function addDraftAttack(enemy: DraftEnemy): void {
  const attacks = enemy.attacks || []
  if (attacks.length >= 3) return
  const used = new Set(attacks.map(attack => String(attack.id || '')))
  let index = attacks.length + 1
  while (used.has(`attack-${index}`)) index += 1
  enemy.attacks = [...attacks, {
    id: `attack-${index}`, name: '', attack_bonus: 0,
    damage: '1d6', range: 5, long_range: 5,
  }]
}

function removeDraftAttack(enemy: DraftEnemy, attackId: string): void {
  if (!enemy.attacks || enemy.attacks.length <= 1) return
  enemy.attacks = enemy.attacks.filter(attack => attack.id !== attackId)
}

// 提交前把数量展开成独立实例：首个保留原 id，其余用稳定后缀并全局去重，
// 保证 combat.start 的敌人 id 唯一；不发送后端不支持的 count 字段。
function expandDraftEnemies(draft: DraftEnemy[]): RulesetTemporaryEncounterEnemy[] {
  const expanded: RulesetTemporaryEncounterEnemy[] = []
  const used = new Set<string>()
  const claimId = (base: string): string => {
    let candidate = base
    let suffix = 2
    while (used.has(candidate)) candidate = `${base}-${suffix++}`
    used.add(candidate)
    return candidate
  }
  for (const enemy of draft) {
    if (!enemy.selected) continue
    const quantity = Math.max(1, Math.floor(Number(enemy.quantity) || 1))
    for (let index = 0; index < quantity; index += 1) {
      expanded.push({
        id: claimId(enemy.id || `enemy-${expanded.length + 1}`),
        name: enemy.name,
        hp: enemy.hp,
        armor_class: enemy.armor_class,
        speed: enemy.speed,
        position: enemy.position,
        initiative_modifier: enemy.initiative_modifier,
        attacks: (enemy.attacks || []).map(attack => ({ ...attack })),
      })
    }
  }
  return expanded
}

const selectedDraftCount = computed(() => aiDraft.value.filter(enemy => enemy.selected).length)

// 摘要按名称合并同种敌人，只显示勾选保留的部分；数量语义与提交展开一致。
const aiSummary = computed<Array<{ name: string; quantity: number }>>(() => {
  const entries: Array<{ name: string; quantity: number }> = []
  for (const enemy of aiDraft.value) {
    if (!enemy.selected) continue
    const name = String(enemy.name || '').trim() || copy.value.aiName
    const quantity = Math.max(1, Math.floor(Number(enemy.quantity) || 1))
    const existing = entries.find(entry => entry.name === name)
    if (existing) existing.quantity += quantity
    else entries.push({ name, quantity })
  }
  return entries
})

// 生成与开战严格分两步：这里只拿提案并构建草稿，绝不自动开战；失败完全无副作用。
async function planTemporaryEncounter(): Promise<void> {
  const gameKey = props.gameKey
  aiBusy.value = true
  aiError.value = ''
  try {
    const response = await planRulesetTemporaryEncounter(gameKey)
    if (props.gameKey !== gameKey) return
    if (response.encounter) {
      aiProposal.value = response.encounter
      aiDraft.value = buildDraftEnemies(response.encounter)
      // 重新生成即换底稿：任何未保存的弹窗编辑都必须作废，不能留在旧 draft 上。
      closeEnemyEditor()
    } else {
      aiProposal.value = null
      aiDraft.value = []
      aiError.value = response.error || copy.value.aiFailed
      closeEnemyEditor()
    }
  } catch (cause: unknown) {
    if (props.gameKey !== gameKey) return
    aiProposal.value = null
    aiDraft.value = []
    aiError.value = friendlyCombatError(cause, locale.value) || copy.value.aiFailed
    closeEnemyEditor()
  } finally {
    if (props.gameKey === gameKey) aiBusy.value = false
  }
}

// 确认入口：过滤未勾选敌人、把数量展开成独立实例后，提交现有
// combat.start（mode=sandbox + enemies，不带 encounter_preset_id）。
// payload 携带 temporary_encounter 标记，服务端权威入口会重走生成期
// 同一套范围与队伍安全上限校验——标记只追加校验，不存在跳过语义，
// 篡改后的 payload 在 combat.start 一样会被整场拒绝。
async function confirmAiEncounter(): Promise<void> {
  if (!aiProposal.value) return
  const enemies = expandDraftEnemies(aiDraft.value)
  if (!enemies.length) return
  const ok = await props.submit({
    intent_id: intentId(),
    type: 'combat.start',
    expected_version: gameplay.value?.state_version ?? 0,
    mode: 'sandbox',
    temporary_encounter: true,
    enemies,
  })
  if (ok) {
    aiProposal.value = null
    aiDraft.value = []
    aiError.value = ''
    closeEnemyEditor()
  }
}

async function startCombat(): Promise<void> {
  if (!selectedPreset.value) return
  const payload: JsonObject = {
    intent_id: intentId(), type: 'combat.start',
    expected_version: gameplay.value?.state_version ?? 0,
    encounter_preset_id: selectedPreset.value.id,
    encounter_instance_id: action('combat.start')?.encounter_instance_id,
    enemies: selectedPreset.value.enemies,
  }
  // 自由遭遇必须由 GM 明确声明，服务端据此拒绝把通用预设当成剧情绑定。
  if (sandboxFlow.value) payload.mode = 'sandbox'
  await props.submit(payload)
}

async function toggleReady(): Promise<void> {
  const item = isReady.value ? unreadyAction.value : readyAction.value
  if (!item) return
  await props.submit({
    intent_id: intentId(),
    type: item.type,
    expected_version: gameplay.value?.state_version ?? 0,
  })
}
</script>

<template>
  <section v-if="combat?.status !== 'active'" class="encounter-start">
    <template v-if="combat?.status !== 'ended'">
    <div v-if="narrativeCombatPending && !guidedCombatStep" class="story-bridge">
      <span class="story-bridge-label">{{ copy.storyBridge }}</span>
      <p>{{ copy.narrativeRequest }}</p>
    </div>
    <template v-if="guidedCombatStep && combat?.status !== 'ended'">
      <div class="story-bridge">
        <span class="story-bridge-label">{{ copy.storyBridge }}</span>
        <h3>{{ guidedCombatStep.title }}</h3>
        <p>{{ guidedCombatStep.narration }}</p>
        <aside><b>{{ copy.storyPurpose }}</b> {{ copy.storyAction }}</aside>
      </div>
    </template>
    <p v-else>{{ combat?.status === 'ended' ? copy.ended : copy.noCombat }}</p>
    <div v-if="guidedCombatStep" class="guided-preset">
      <span>{{ copy.guidedEncounter }}</span>
      <strong>{{ guidedCombatSummary?.name || guidedCombatStep.encounter_preset_id }}</strong>
      <small class="encounter-source">{{ copy.encounterSource }}：{{ guidedCombatStep.encounter_preset_id }}</small>
      <p>{{ guidedCombatSummary?.description }}</p>
      <p v-if="isGm && !guidedCombatPreset" class="combat-error" role="alert">{{ copy.missingPreset }}</p>
      <small v-else>{{ copy.guidedOnly }}</small>
    </div>
    <div v-else-if="storyUnprepared && !sandboxDeclared" class="guided-preset unprepared">
      <strong>{{ copy.unpreparedTitle }}</strong>
      <p>{{ copy.unpreparedHint }}</p>
      <div class="unprepared-actions">
        <button type="button" @click="emit('navigate', 'campaign')">
          <NIcon :component="PlayForwardOutline" />{{ copy.returnToAdventure }}
        </button>
        <button v-if="canPlanTemporaryEncounter" type="button" class="combat-primary" :disabled="aiBusy" @click="planTemporaryEncounter">
          <NIcon :component="SparklesOutline" />{{ aiBusy ? copy.aiPreparing : copy.aiPrepare }}
        </button>
        <button v-if="isGm" type="button" @click="declareSandboxEncounter">
          <NIcon :component="ShieldOutline" />{{ copy.unpreparedAction }}
        </button>
      </div>
      <p v-if="isGm" class="combat-state">{{ copy.aiHint }}</p>
    </div>
    <div v-else-if="requestedCombatSummary" class="guided-preset">
      <span>{{ copy.recommendedEncounter }}</span>
      <strong>{{ requestedCombatSummary.name }}</strong>
      <p>{{ requestedCombatSummary.description }}</p>
      <details v-if="isGm && selectableEncounterPresets.length > 1" class="encounter-alternatives">
        <summary>{{ copy.changeEncounter }}</summary>
        <label>
          <span>{{ copy.chooseEncounter }}</span>
          <select v-model="selectedPresetId">
            <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
              {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
            </option>
          </select>
        </label>
      </details>
    </div>

    <p v-if="aiError && !aiProposal" class="combat-error" role="alert">{{ aiError }}</p>

    <section v-if="encounterReadiness?.required_count" class="party-readiness" aria-live="polite">
      <header>
        <strong>{{ copy.partyReady }}</strong>
        <span>{{ copy.readySummary }} {{ encounterReadiness.ready_count }}/{{ encounterReadiness.required_count }}</span>
      </header>
      <ul>
        <li v-for="player in encounterReadiness.players" :key="player.player_id" :class="{ ready: player.ready }">
          <NIcon :component="player.ready ? CheckmarkCircleOutline : HourglassOutline" />
          <span>{{ player.name }}</span>
          <strong>{{ player.ready ? copy.readyStatus : copy.unreadyStatus }}</strong>
        </li>
      </ul>
      <button v-if="!isGm && (readyAction || unreadyAction)" class="combat-primary" :disabled="busy" @click="toggleReady">
        <NIcon :component="isReady ? HourglassOutline : CheckmarkCircleOutline" />
        {{ isReady ? copy.cancelReady : copy.ready }}
      </button>
    </section>

    <template v-if="isGm && (action('combat.start') || sandboxDeclared)">
      <template v-if="!guidedCombatStep && !requestedCombatPreset">
        <div v-if="!manualEncounterOpen && !sandboxDeclared" class="unprepared-actions">
          <button type="button" class="manual-encounter-toggle" @click="openManualEncounter">
            <NIcon :component="ShieldOutline" />{{ copy.manualEncounter }}
          </button>
          <button v-if="canPlanTemporaryEncounter" type="button" class="ai-encounter-toggle" :disabled="aiBusy" @click="planTemporaryEncounter">
            <NIcon :component="SparklesOutline" />{{ aiBusy ? copy.aiPreparing : copy.aiPrepare }}
          </button>
        </div>
        <template v-else>
          <p v-if="sandboxDeclared" class="combat-warning">{{ copy.sandboxWarning }}</p>
          <p v-else-if="narrativeCombatPending" class="combat-state">{{ copy.sandboxHint }}</p>
          <label>
            <span>{{ copy.chooseFreeEncounter }}</span>
            <select v-model="selectedPresetId">
              <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
                {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
              </option>
            </select>
          </label>
          <p class="preset-description">{{ selectedPreset?.description }}</p>
        </template>
      </template>
      <p v-if="!guidedCombatStep && !selectableEncounterPresets.length" class="combat-state">{{ copy.noEncounter }}</p>
      <p v-if="encounterReadiness?.required_count" class="combat-state">{{ copy.startHint }}</p>
      <button
        v-if="guidedCombatStep || requestedCombatPreset || manualEncounterOpen || sandboxDeclared"
        class="combat-primary"
        :disabled="busy || !selectedPreset"
        @click="startCombat"
      >
        <NIcon :component="PlayForwardOutline" />{{ guidedCombatStep ? copy.guidedStart : copy.start }}
      </button>
    </template>
    <p v-else-if="(guidedCombatStep || narrativeCombatPending) && combat?.status !== 'ended'" class="combat-state">{{ copy.waitingForGm }}</p>
    </template>
    <section v-else class="encounter-ended" aria-live="polite">
      <strong>{{ copy.ended }}</strong>
      <p>{{ copy.nextEncounterHint }}</p>
      <div class="encounter-ended-actions">
        <button type="button" @click="emit('navigate', 'campaign')"><NIcon :component="PlayForwardOutline" />{{ copy.returnToAdventure }}</button>
        <button v-if="isGm && action('combat.start')" type="button" class="combat-primary" @click="toggleNextEncounter">
          <NIcon :component="PlayForwardOutline" />{{ nextEncounterOpen ? copy.cancelNextEncounter : copy.nextEncounter }}
        </button>
        <button v-if="canPlanTemporaryEncounter" type="button" class="ai-encounter-toggle" :disabled="aiBusy" @click="planTemporaryEncounter">
          <NIcon :component="SparklesOutline" />{{ aiBusy ? copy.aiPreparing : copy.aiPrepare }}
        </button>
      </div>
      <div v-if="nextEncounterOpen" class="next-encounter-picker">
        <label>
          <span>{{ copy.chooseEncounter }}</span>
          <select v-model="selectedPresetId">
            <option v-for="preset in selectableEncounterPresets" :key="preset.id" :value="preset.id">
              {{ preset.name }} · {{ localizedTerm(preset.difficulty) }}
            </option>
          </select>
        </label>
        <p class="preset-description">{{ selectedPreset?.description }}</p>
        <button class="combat-primary" :disabled="busy || !selectedPreset" @click="startCombat">
          <NIcon :component="PlayForwardOutline" />{{ copy.start }}
        </button>
      </div>
    </section>

    <!-- AI 临时遭遇草稿：战斗未开始与已结算两种状态都要可见（结算态可
         直接由 GM 发起下一场 AI 临时遭遇），因此挂在 encounter-start
         分支之外、不受 combat.status!=='ended' 的 v-if/v-else 链约束。 -->
    <!-- 默认只给摘要：十几个输入框直接铺开体验过重，编辑收进「编辑敌人」弹窗。 -->
    <div v-if="isGm && aiProposal" class="guided-preset ai-encounter-preview">
      <span>{{ copy.aiPreviewTag }}</span>
      <strong>{{ aiProposal.title }}</strong>
      <p>{{ aiProposal.description }}</p>
      <small class="encounter-source">{{ copy.aiDifficulty }}</small>
      <ul class="ai-encounter-summary" :aria-label="t('encounterSummary')">
        <li v-for="entry in aiSummary" :key="entry.name">{{ entry.name }} ×{{ entry.quantity }}</li>
      </ul>
      <p v-if="!aiSummary.length" class="combat-state">{{ t('encounterNoEnemiesSelected') }}</p>
      <p class="combat-state">{{ copy.aiEncounterNote }}</p>
      <div class="unprepared-actions">
        <button type="button" class="ai-encounter-edit" @click="openEnemyEditor">
          {{ t('encounterEditEnemies') }}
        </button>
        <button type="button" :disabled="aiBusy" @click="planTemporaryEncounter">
          <NIcon :component="SparklesOutline" />{{ copy.aiRegenerate }}
        </button>
        <button type="button" class="combat-primary" :disabled="busy || !selectedDraftCount" @click="confirmAiEncounter">
          <NIcon :component="PlayForwardOutline" />{{ copy.aiConfirmStart }}
        </button>
        <button type="button" @click="cancelAiProposal">{{ copy.cancel }}</button>
      </div>
    </div>

    <!-- 编辑弹窗：编辑工作副本，保存后才回到草稿与摘要；每个敌人默认折叠。 -->
    <Modal
      v-if="isGm && aiProposal && enemyEditorOpen"
      :title="t('encounterEditorTitle')"
      dialog-class="encounter-editor-dialog"
      @close="cancelEnemyEditor"
    >
      <p class="combat-state">{{ copy.aiDraftHint }}</p>
      <ul class="ai-encounter-enemies">
        <li v-for="enemy in editDraft" :key="enemy.id">
          <header class="ai-draft-head">
            <input v-model="enemy.selected" type="checkbox" :aria-label="enemy.name || copy.aiName" />
            <button
              type="button"
              class="ai-draft-toggle"
              :aria-expanded="isEnemyExpanded(enemy)"
              @click="toggleEnemyExpanded(enemy)"
            >
              <NIcon :component="ChevronDownOutline" :class="['ai-draft-chevron', { open: isEnemyExpanded(enemy) }]" />
              <span>{{ enemy.name || copy.aiName }}</span>
              <small>×{{ enemy.quantity }}</small>
            </button>
          </header>
          <template v-if="isEnemyExpanded(enemy)">
            <div class="ai-draft-fields">
              <label><span>{{ copy.aiName }}</span><input v-model="enemy.name" type="text" maxlength="60" :aria-label="copy.aiName" /></label>
              <label><span>{{ copy.aiQuantity }}</span><input v-model.number="enemy.quantity" type="number" min="1" max="12" :aria-label="copy.aiQuantity" /></label>
              <label><span>{{ copy.hp }}</span><input v-model.number="enemy.hp" type="number" min="1" max="500" :aria-label="copy.hp" /></label>
              <label><span>{{ copy.ac }}</span><input v-model.number="enemy.armor_class" type="number" min="8" max="25" :aria-label="copy.ac" /></label>
              <label><span>{{ copy.tempSpeed }}</span><input v-model.number="enemy.speed" type="number" min="0" max="80" :aria-label="copy.tempSpeed" /></label>
              <label><span>{{ copy.aiInitiative }}</span><input v-model.number="enemy.initiative_modifier" type="number" min="-5" max="10" :aria-label="copy.aiInitiative" /></label>
            </div>
            <div v-for="attack in enemy.attacks || []" :key="attack.id" class="ai-draft-attack">
              <label><span>{{ copy.attack }}</span><input v-model="attack.name" type="text" maxlength="60" :aria-label="copy.attack" /></label>
              <label><span>{{ copy.aiAttackBonus }}</span><input v-model.number="attack.attack_bonus" type="number" min="-2" max="15" :aria-label="copy.aiAttackBonus" /></label>
              <label><span>{{ copy.aiDamage }}</span><input v-model="attack.damage" type="text" maxlength="40" :aria-label="copy.aiDamage" /></label>
              <label><span>{{ copy.aiNormalRange }}</span><input v-model.number="attack.range" type="number" min="5" max="600" :aria-label="copy.aiNormalRange" /></label>
              <label><span>{{ copy.aiLongRange }}</span><input v-model.number="attack.long_range" type="number" min="5" max="600" :aria-label="copy.aiLongRange" /></label>
              <button type="button" :disabled="(enemy.attacks?.length || 0) <= 1" @click="removeDraftAttack(enemy, attack.id || '')">
                {{ copy.aiRemoveAttack }}
              </button>
            </div>
            <button type="button" class="ai-draft-add-attack" :disabled="(enemy.attacks?.length || 0) >= 3" @click="addDraftAttack(enemy)">
              <NIcon :component="SparklesOutline" />{{ copy.aiAddAttack }}
            </button>
          </template>
        </li>
      </ul>
      <template #actions>
        <button type="button" @click="restoreEditDraft">{{ copy.aiRestore }}</button>
        <button type="button" @click="cancelEnemyEditor">{{ copy.cancel }}</button>
        <button type="button" class="combat-primary" @click="saveEnemyEdits">{{ t('encounterSaveChanges') }}</button>
      </template>
    </Modal>
  </section>
</template>

<style scoped>
.preset-description, .combat-state { margin: 0; color: #bcb5aa; font-size: 13px; }
.combat-error { margin: 0; padding: 9px 11px; border: 1px solid #b85151; border-radius: 9px; background: rgb(115 28 28 / 25%); color: #ffd6d6; }
.combat-primary { display: inline-flex; align-items: center; justify-content: center; gap: 6px; border-color: #c59443; background: #a56f24; color: white; }
button, select, input { min-height: 44px; font: inherit; }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 3px solid #e4b75e; outline-offset: 2px; }
:global(body.light .dnd-combat .encounter-start select) { border-color: #908779; background: #fff; color: #211e1a; }
.story-bridge { display: grid; gap: 8px; padding: 14px; border: 1px solid #a17b3f; border-radius: 12px; background: linear-gradient(135deg, rgb(91 62 26 / 42%), rgb(27 30 34 / 88%)); }
.story-bridge-label { color: #f0c975; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.story-bridge h3, .story-bridge p { margin: 0; }
.story-bridge p { color: #e3d9c6; line-height: 1.6; }
.story-bridge aside { padding: 9px 11px; border-left: 3px solid #d5a64f; background: rgb(14 20 24 / 56%); color: #f2e6cf; line-height: 1.55; }
.guided-preset { display: grid; gap: 5px; padding: 11px 12px; border: 1px solid #a17b3f; border-radius: 10px; background: rgb(91 62 26 / 24%); }.guided-preset span, .guided-preset small { color: #f0c975; font-size: 12px; }.guided-preset strong { font-size: 17px; }.guided-preset p { margin: 0; color: #e3d9c6; line-height: 1.5; }
.guided-preset.unprepared { border-color: #b0803c; background: rgb(70 48 20 / 34%); }
.guided-preset.unprepared strong { color: #f4d9a4; }
.unprepared-actions { display: flex; column-gap: 14px; row-gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.unprepared-actions button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 38px; padding: 7px 12px; }
.ai-encounter-preview { border-color: #6d6f4a; background: rgb(52 56 24 / 28%); }
.ai-encounter-summary { display: flex; gap: 8px 14px; flex-wrap: wrap; margin: 2px 0; padding: 0; list-style: none; }
.ai-encounter-summary li { padding: 4px 10px; border: 1px solid #6d5a35; border-radius: 999px; color: #f0d79c; background: rgb(14 20 24 / 42%); font-size: 13px; }
.ai-encounter-enemies { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.ai-encounter-enemies li { display: grid; gap: 7px; padding: 8px 10px; border: 1px solid #6d5a35; border-radius: 9px; background: rgb(14 20 24 / 42%); }
.ai-draft-head { display: flex; align-items: center; gap: 8px; }
.ai-draft-head input[type="checkbox"] { min-height: 0; width: 16px; height: 16px; accent-color: #c59443; }
.ai-draft-toggle { display: inline-flex; flex: 1; align-items: center; gap: 8px; min-width: 0; min-height: 34px; padding: 4px 10px; border: 1px solid #4a5560; border-radius: 8px; color: #f3eee7; background: #0d1319; font-size: 13px; text-align: left; cursor: pointer; }
.ai-draft-toggle small { margin-left: auto; color: #cfc6b4; font-size: 11px; }
.ai-draft-chevron { transition: transform .15s ease; }
.ai-draft-chevron.open { transform: rotate(180deg); }
.ai-draft-quantity { display: inline-flex; align-items: center; gap: 6px; color: #cfc6b4; font-size: 11px; }
.ai-draft-quantity input { width: 64px; min-height: 34px; padding-inline: 8px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; color: #f3eee7; }
.ai-draft-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 6px; }
.ai-draft-fields label, .ai-draft-attack label { display: grid; gap: 3px; color: #cfc6b4; font-size: 11px; }
.ai-draft-fields input, .ai-draft-attack input { min-height: 34px; padding-inline: 8px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; color: #f3eee7; }
.ai-draft-attack { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 6px; align-items: end; padding: 7px 8px; border: 1px dashed #6d5a35; border-radius: 8px; }
.ai-draft-attack button { min-height: 34px; padding: 4px 10px; font-size: 12px; color: #e8b7a9; }
.ai-draft-add-attack { display: inline-flex; align-items: center; justify-content: center; gap: 6px; justify-self: start; min-height: 34px; padding: 4px 10px; font-size: 12px; }
.encounter-source { color: #d9cba6; font-size: 11px; }
.combat-warning { margin: 0; padding: 9px 11px; border: 1px solid #b0803c; border-radius: 9px; background: rgb(70 48 20 / 34%); color: #f2d9a8; font-size: 13px; line-height: 1.5; }
.encounter-alternatives { margin-top: 5px; }
.encounter-alternatives summary { color: #d5c5aa; cursor: pointer; font-size: 12px; }
.encounter-alternatives label { margin-top: 8px; }
.party-readiness { display: grid; gap: 8px; padding: 11px 12px; border: 1px solid #46525d; border-radius: 11px; background: rgb(14 21 28 / 72%); }
.party-readiness header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.party-readiness header span { color: #b9c3ca; font-size: 12px; }
.party-readiness ul { display: flex; gap: 7px; flex-wrap: wrap; margin: 0; padding: 0; list-style: none; }
.party-readiness li { display: inline-flex; align-items: center; gap: 6px; padding: 6px 8px; border: 1px solid #55464a; border-radius: 999px; color: #d6b9bb; background: #271d21; font-size: 12px; }
.party-readiness li.ready { border-color: #56745b; color: #c7e3ca; background: #18271d; }
.party-readiness li strong { font-size: 11px; }
.party-readiness button, .combat-primary { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.encounter-ended, .next-encounter-picker { display: grid; gap: 9px; }
.encounter-ended { padding: 13px; border: 1px solid #56745b; border-radius: 11px; background: rgb(24 45 29 / 68%); }
.encounter-ended > strong { color: #d2ebd3; font-size: 16px; }
.encounter-ended > p { margin: 0; color: #c5d5c6; line-height: 1.55; }
.encounter-ended-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.encounter-ended-actions button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.next-encounter-picker { margin-top: 4px; padding-top: 11px; border-top: 1px solid rgb(174 211 178 / 24%); }
.encounter-start label { display: grid; gap: 5px; color: #c8c2b8; font-size: 12px; }
.encounter-start select { width: 100%; min-height: 44px; padding-inline: 10px; border: 1px solid #4a5560; border-radius: 8px; background: #0d1319; color: #f3eee7; }
:global(body.light .dnd-combat .story-bridge) { border-color: #aa8546; background: #fff7e7; }
:global(body.light .dnd-combat .story-bridge p), :global(body.light .dnd-combat .story-bridge aside) { color: #403728; }
:global(body.light .dnd-combat .guided-preset.unprepared), :global(body.light .dnd-combat .combat-warning) { border-color: #b0803c; background: #fdf3de; }
:global(body.light .dnd-combat .guided-preset) { border-color: #b08a44; background: #fdf5e6; }
:global(body.light .dnd-combat .guided-preset p), :global(body.light .dnd-combat .guided-preset strong) { color: #3b3226; }
:global(body.light .dnd-combat .ai-draft-head input[type="text"]), :global(body.light .dnd-combat .ai-draft-fields input), :global(body.light .dnd-combat .ai-draft-attack input), :global(body.light .dnd-combat .ai-draft-quantity input) { border-color: #b08a44; background: #fffaf0; color: #3b3226; }
:global(body.light .dnd-combat .ai-draft-fields label), :global(body.light .dnd-combat .ai-draft-attack label), :global(body.light .dnd-combat .ai-draft-quantity) { color: #514b43; }
@media (max-width: 700px) {
  .party-readiness header { align-items: flex-start; flex-direction: column; }
  .story-bridge, .guided-preset { padding: 10px; }
}
</style>
