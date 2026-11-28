<script setup lang="ts">
import { computed, ref } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import DiceTray from '@/features/play/dice/DiceTray.vue'
import { useLocale } from '@/composables/useLocale'
import type { CheckResult } from '@/api/types'
import { quickManualRoll } from './api'
import { createOperationId } from './useManualRolls'
import type { ManualRollResult, ManualRollVisibility } from './types'

const props = defineProps<{
  gameKey: string
  runId: string
  preview?: boolean
  delegate?: boolean
}>()

const emit = defineEmits<{
  close: []
  rolled: [result: ManualRollResult]
}>()

const { locale } = useLocale()
const zh = computed(() => locale.value.startsWith('zh'))
const copy = computed(() => zh.value ? {
  title: '自由掷骰',
  quickDice: '快捷骰面（连点可累加数量）',
  presets: '常用组合',
  adv: '优势 2d20kh1',
  dis: '劣势 2d20kl1',
  stat: '属性 4d6kh3',
  formula: '骰子公式',
  formulaHint: '支持 d20、2d6+3、2d20kh1、4d6kh3、1d8+1d6+2 等',
  label: '备注（可选）',
  labelPlaceholder: '例如：决定谁先守夜 / 随机伤害',
  visibility: '可见范围',
  party: '全队可见',
  private: '暗骰（仅自己与 GM 可见）',
  tellAi: '将结果告知 AI',
  roll: '掷骰',
  rolling: '掷骰中…',
  reroll: '重投',
  reset: '重置',
  close: '关闭',
  total: '总值',
  natural: '骰面和',
  modifier: '修正',
  recent: '本次掷骰记录',
  previewReadonly: '预览视角下需开启代操作权限才能掷骰。',
} : {
  title: 'Free Dice Roller',
  quickDice: 'Quick dice (click again to stack)',
  presets: 'Presets',
  adv: 'Adv 2d20kh1',
  dis: 'Dis 2d20kl1',
  stat: 'Stat 4d6kh3',
  formula: 'Dice formula',
  formulaHint: 'Supports d20, 2d6+3, 2d20kh1, 4d6kh3, 1d8+1d6+2, etc.',
  label: 'Note (optional)',
  labelPlaceholder: 'e.g. Who takes first watch / random damage',
  visibility: 'Visibility',
  party: 'Party',
  private: 'Private (you & GM only)',
  tellAi: 'Share result with AI',
  roll: 'Roll',
  rolling: 'Rolling…',
  reroll: 'Reroll',
  reset: 'Reset',
  close: 'Close',
  total: 'Total',
  natural: 'Natural',
  modifier: 'Mod',
  recent: 'Recent rolls',
  previewReadonly: 'Enable delegation in preview mode to roll.',
})

const QUICK_SIDES = [4, 6, 8, 10, 12, 20, 100] as const

const formula = ref('d20')
const label = ref('')
const visibility = ref<ManualRollVisibility>('party')
const includeInAi = ref(false)
const busy = ref(false)
const errorText = ref('')
const rollSeq = ref(0)
const latest = ref<{ label: string; visibility: ManualRollVisibility; result: ManualRollResult } | null>(null)
const recent = ref<Array<{ id: string; label: string; formula: string; visibility: ManualRollVisibility; result: ManualRollResult }>>([])

const canAct = computed(() => !props.preview || Boolean(props.delegate))

const trayCheck = computed<CheckResult>(() => {
  const res = latest.value?.result
  if (!res) return { dice: formula.value || 'd20', roll: 0, rolls: [], total: 0 }
  return {
    dice: res.formula,
    roll: res.natural ?? res.total,
    rolls: res.rolls,
    modifier: res.modifier,
    total: res.total,
  }
})

const trayFaces = computed(() => {
  const faces = latest.value?.result.faces
  if (!faces?.length) return undefined
  return faces.map(face => ({
    value: face.value,
    label: face.sign === -1 ? `-${face.value}` : String(face.value),
    sides: face.sides,
    taken: Boolean(face.taken),
  }))
})

function pickDie(sides: number) {
  const cleaned = formula.value.trim().toLowerCase().replace(/\s+/g, '')
  const match = cleaned.match(/^(\d*)d(\d+)([+-]\d+)?$/)
  if (match && Number(match[2]) === sides) {
    const nextCount = Math.min(100, (Number(match[1]) || 1) + 1)
    const mod = match[3] || ''
    formula.value = `${nextCount}d${sides}${mod}`
    return
  }
  const existingMod = match?.[3] || ''
  formula.value = `d${sides}${existingMod}`
}

function applyPreset(preset: string) {
  formula.value = preset
}

function adjustModifier(delta: number) {
  const cleaned = formula.value.trim().toLowerCase().replace(/\s+/g, '') || 'd20'
  const trailing = cleaned.match(/^(.*?)([+-])(\d+)$/)
  if (trailing && trailing[1] && !/d\d*$/i.test(trailing[1] + trailing[2])) {
    const current = (trailing[2] === '-' ? -1 : 1) * Number(trailing[3])
    const next = Math.max(-100, Math.min(100, current + delta))
    formula.value = next === 0 ? trailing[1] : `${trailing[1]}${next > 0 ? '+' : ''}${next}`
    return
  }
  if (delta !== 0) {
    formula.value = `${cleaned}${delta > 0 ? '+' : ''}${delta}`
  }
}

function resetFormula() {
  formula.value = 'd20'
  label.value = ''
  errorText.value = ''
}

function signedModifier(mod: number): string {
  return mod > 0 ? `+${mod}` : String(mod)
}

async function submitRoll(override?: { formula: string; label: string; visibility: ManualRollVisibility }) {
  if (!canAct.value || busy.value) return
  const targetFormula = (override?.formula ?? formula.value).trim()
  const targetLabel = (override?.label ?? label.value).trim()
  const targetVisibility = override?.visibility ?? visibility.value
  if (!targetFormula) return

  busy.value = true
  errorText.value = ''
  try {
    const response = await quickManualRoll(props.gameKey, {
      operation_id: createOperationId(),
      run_id: props.runId,
      formula: targetFormula,
      label: targetLabel,
      visibility: targetVisibility,
      include_in_ai_context: includeInAi.value,
    })
    rollSeq.value += 1
    latest.value = { label: targetLabel, visibility: targetVisibility, result: response.result }
    recent.value = [
      {
        id: response.request.id || String(rollSeq.value),
        label: targetLabel,
        formula: response.result.formula,
        visibility: targetVisibility,
        result: response.result,
      },
      ...recent.value,
    ].slice(0, 5)
    emit('rolled', response.result)
  } catch (cause: unknown) {
    errorText.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal :title="copy.title" dialog-class="free-roll-dialog" @close="emit('close')">
    <div class="free-roll" data-testid="free-roll-modal">
      <div class="free-roll__section">
        <span class="free-roll__caption">{{ copy.quickDice }}</span>
        <div class="free-roll__chips">
          <button
            v-for="sides in QUICK_SIDES"
            :key="sides"
            type="button"
            class="free-roll__die-btn"
            :data-testid="`free-roll-d${sides}`"
            :disabled="busy"
            @click="pickDie(sides)"
          >d{{ sides }}</button>
        </div>
      </div>

      <div class="free-roll__section">
        <span class="free-roll__caption">{{ copy.presets }}</span>
        <div class="free-roll__chips">
          <button type="button" class="free-roll__preset-btn" :disabled="busy" @click="applyPreset('2d20kh1')">{{ copy.adv }}</button>
          <button type="button" class="free-roll__preset-btn" :disabled="busy" @click="applyPreset('2d20kl1')">{{ copy.dis }}</button>
          <button type="button" class="free-roll__preset-btn" :disabled="busy" @click="applyPreset('4d6kh3')">{{ copy.stat }}</button>
        </div>
      </div>

      <form class="free-roll__form" @submit.prevent="submitRoll()">
        <label class="free-roll__field">
          <span>{{ copy.formula }}</span>
          <div class="free-roll__formula-row">
            <input
              v-model="formula"
              type="text"
              data-testid="free-roll-formula"
              placeholder="d20"
              :disabled="busy"
            />
            <button type="button" class="free-roll__mod-btn" :disabled="busy" @click="adjustModifier(-2)">-2</button>
            <button type="button" class="free-roll__mod-btn" :disabled="busy" @click="adjustModifier(-1)">-1</button>
            <button type="button" class="free-roll__mod-btn" :disabled="busy" @click="adjustModifier(1)">+1</button>
            <button type="button" class="free-roll__mod-btn" :disabled="busy" @click="adjustModifier(2)">+2</button>
            <button type="button" class="free-roll__mod-btn" :disabled="busy" @click="resetFormula">{{ copy.reset }}</button>
          </div>
          <small class="free-roll__hint">{{ copy.formulaHint }}</small>
        </label>

        <label class="free-roll__field">
          <span>{{ copy.label }}</span>
          <input
            v-model="label"
            type="text"
            maxlength="200"
            data-testid="free-roll-label"
            :placeholder="copy.labelPlaceholder"
            :disabled="busy"
          />
        </label>

        <div class="free-roll__options">
          <label class="free-roll__field free-roll__visibility">
            <span>{{ copy.visibility }}</span>
            <select v-model="visibility" data-testid="free-roll-visibility" :disabled="busy">
              <option value="party">{{ copy.party }}</option>
              <option value="private">{{ copy.private }}</option>
            </select>
          </label>
          <label class="free-roll__ai-toggle">
            <input v-model="includeInAi" type="checkbox" data-testid="free-roll-include-ai" :disabled="busy" />
            <span>{{ copy.tellAi }}</span>
          </label>
        </div>

        <p v-if="!canAct" class="free-roll__error" role="status">{{ copy.previewReadonly }}</p>
        <p v-if="errorText" class="free-roll__error" role="alert">{{ errorText }}</p>

        <div class="free-roll__submit-row">
          <button
            type="submit"
            class="primary free-roll__submit"
            data-testid="free-roll-submit"
            :disabled="busy || !canAct || !formula.trim()"
          >{{ busy ? copy.rolling : copy.roll }}</button>
        </div>
      </form>

      <section v-if="latest" class="free-roll__result" data-testid="free-roll-result" aria-live="polite">
        <div class="free-roll__result-head">
          <strong>{{ latest.label ? `${latest.label} · ` : '' }}{{ latest.result.formula }}</strong>
          <span class="free-roll__total" data-testid="free-roll-total">{{ latest.result.total }}</span>
        </div>
        <DiceTray
          :key="rollSeq"
          :check="trayCheck"
          :custom-faces="trayFaces"
          :revealed="true"
          :animate-reveal="true"
        />
        <p class="free-roll__breakdown">
          {{ copy.total }} {{ latest.result.total }}
          · {{ copy.natural }} {{ latest.result.natural ?? latest.result.total }}
          <template v-if="latest.result.modifier"> · {{ copy.modifier }} {{ signedModifier(latest.result.modifier) }}</template>
          <template v-if="latest.visibility === 'private'"> · {{ copy.private }}</template>
        </p>
      </section>

      <section v-if="recent.length > 1" class="free-roll__recent" data-testid="free-roll-recent">
        <span class="free-roll__caption">{{ copy.recent }}</span>
        <ul>
          <li v-for="item in recent" :key="item.id">
            <span class="free-roll__recent-info">
              <strong>{{ item.result.total }}</strong>
              <span>{{ item.label ? `${item.label} · ` : '' }}{{ item.formula }}</span>
            </span>
            <button
              type="button"
              class="free-roll__reroll-btn"
              :disabled="busy || !canAct"
              @click="submitRoll({ formula: item.formula, label: item.label, visibility: item.visibility })"
            >{{ copy.reroll }}</button>
          </li>
        </ul>
      </section>
    </div>

    <template #actions>
      <button type="button" @click="emit('close')">{{ copy.close }}</button>
    </template>
  </Modal>
</template>

<style scoped>
.free-roll { display: grid; gap: 12px; min-width: 0; }
.free-roll__section { display: grid; gap: 6px; }
.free-roll__caption { font-size: 12px; color: var(--df-text-muted); }
.free-roll__chips { display: flex; flex-wrap: wrap; gap: 6px; }
.free-roll__die-btn,
.free-roll__preset-btn,
.free-roll__mod-btn,
.free-roll__reroll-btn {
  min-height: 30px;
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-sm);
  background: var(--df-surface-2);
  color: var(--df-text);
  cursor: pointer;
}
.free-roll__die-btn:hover:not(:disabled),
.free-roll__preset-btn:hover:not(:disabled),
.free-roll__mod-btn:hover:not(:disabled),
.free-roll__reroll-btn:hover:not(:disabled) {
  border-color: var(--df-accent);
  color: var(--df-accent-strong);
}
.free-roll__form { display: grid; gap: 10px; }
.free-roll__field { display: grid; gap: 4px; font-size: 12px; color: var(--df-text-secondary); }
.free-roll__formula-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.free-roll__formula-row input { flex: 1 1 130px; min-width: 110px; }
.free-roll__hint { color: var(--df-text-muted); font-size: 11px; }
.free-roll__options { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; justify-content: space-between; }
.free-roll__visibility { flex: 1 1 180px; }
.free-roll__ai-toggle { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; font-size: 12px; color: var(--df-text-secondary); cursor: pointer; }
.free-roll__ai-toggle input { width: 16px; height: 16px; }
.free-roll__error { margin: 0; font-size: 12px; color: var(--df-danger-strong); }
.free-roll__submit-row { display: flex; }
.free-roll__submit { width: 100%; min-height: 38px; font-size: 14px; font-weight: 600; }
.free-roll__result { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: var(--df-surface-2); }
.free-roll__result-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.free-roll__total { font: 700 24px var(--df-font-title); color: var(--df-accent-strong); font-variant-numeric: tabular-nums; }
.free-roll__breakdown { margin: 0; font-size: 12px; color: var(--df-text-secondary); text-align: center; }
.free-roll__recent { display: grid; gap: 6px; }
.free-roll__recent ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.free-roll__recent li { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); background: var(--df-surface-1); font-size: 12px; }
.free-roll__recent-info { display: flex; align-items: baseline; gap: 8px; min-width: 0; overflow-wrap: anywhere; }
.free-roll__recent-info strong { color: var(--df-accent-strong); font-variant-numeric: tabular-nums; }
</style>
