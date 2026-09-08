<script setup lang="ts">
import { computed } from 'vue'
import { type CombatDraft, type FormulaDraft } from '@/features/admin/combatExtensionDraft'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{ modelValue: CombatDraft; resourceOptions: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: CombatDraft] }>()
const { t } = useLocale()

const model = computed({
  get: () => props.modelValue,
  set: (value: CombatDraft) => emit('update:modelValue', value),
})

function update(mutate: (draft: CombatDraft) => void) {
  const copy = JSON.parse(JSON.stringify(model.value)) as CombatDraft
  mutate(copy)
  emit('update:modelValue', copy)
}

function addResource() {
  update(d => d.resources.push({ id: '', source: 'special_stat', costable: true }))
}
function removeResource(index: number) {
  update(d => d.resources.splice(index, 1))
}
function addAction() {
  update(d => d.actions.push({ id: '', kind: 'ability', name: '', costs: [], effects: [
    { kind: 'damage', amount: { type: 'dice', formula: '1d6' } },
  ] }))
}
function removeAction(index: number) {
  update(d => d.actions.splice(index, 1))
}
function formulaTypeLabel(type: FormulaDraft['type']): string {
  return {
    dice: t('combatEditorFormulaDice'),
    constant: t('combatEditorFormulaConstant'),
    attribute: t('combatEditorFormulaAttribute'),
    combine: t('combatEditorFormulaCombine'),
    json: t('combatEditorFormulaJson'),
  }[type]
}
function newFormula(type: string): FormulaDraft {
  if (type === 'dice') return { type: 'dice', formula: '1d6' }
  if (type === 'attribute') return { type: 'attribute', id: '' }
  return { type: 'constant', value: 1 }
}
function setCostFormulaType(actionIndex: number, costIndex: number, type: string) {
  update(d => { d.actions[actionIndex].costs[costIndex].amount = newFormula(type) })
}
function setEffectFormulaType(actionIndex: number, effectIndex: number, type: string) {
  update(d => { d.actions[actionIndex].effects[effectIndex].amount = newFormula(type) })
}
function setEffectKind(actionIndex: number, effectIndex: number, kind: string) {
  update(d => {
    const effect = d.actions[actionIndex].effects[effectIndex]
    effect.kind = kind
    if (kind === 'damage') {
      delete effect.resource
      delete effect.duration
    } else if (kind === 'resource_change') {
      effect.resource ||= props.resourceOptions[0] || ''
      delete effect.damage_type
      delete effect.duration
    } else if (kind === 'modify_stat') {
      effect.resource ||= 'action_speed'
      effect.duration ||= 1
      delete effect.damage_type
    }
  })
}
</script>

<template>
  <section class="combat-ext-editor">
    <div class="cee-title-row">
      <h4>{{ t('combatEditorTitle') }}</h4>
    </div>

    <div class="cee-scheduler">
      <label class="cee-field cee-field-wide">
        <span>{{ t('combatEditorScheduler') }}</span>
      <select :value="model.scheduler?.kind || ''" @change="update(d => {
        const kind = ($event.target as HTMLSelectElement).value
        d.scheduler = kind ? { kind, threshold: 100, overflow: 'carry', consume: 'reset', gauge: 'action_gauge', speed: 'action_speed' } : null
      })">
        <option value="">{{ t('combatEditorDisabled') }}</option>
        <option value="round_robin">{{ t('combatEditorRoundRobin') }}</option>
        <option value="initiative">{{ t('combatEditorInitiative') }}</option>
        <option value="threshold">{{ t('combatEditorThreshold') }}</option>
      </select>
      </label>
      <div v-if="model.scheduler?.kind === 'threshold'" class="cee-scheduler-grid">
        <label class="cee-field">
          <span>{{ t('combatEditorThresholdValue') }}</span>
          <input type="number" :value="model.scheduler.threshold" min="1"
                 @change="update(d => { if (d.scheduler) d.scheduler.threshold = Number(($event.target as HTMLInputElement).value) })">
        </label>
        <label class="cee-field">
          <span>{{ t('combatEditorOverflow') }}</span>
          <select :value="model.scheduler.overflow" @change="update(d => { if (d.scheduler) d.scheduler.overflow = ($event.target as HTMLSelectElement).value })">
            <option value="carry">{{ t('combatEditorOverflowCarry') }}</option>
            <option value="clamp">{{ t('combatEditorOverflowClamp') }}</option>
          </select>
        </label>
        <label class="cee-field">
          <span>{{ t('combatEditorConsume') }}</span>
          <select :value="model.scheduler.consume" @change="update(d => { if (d.scheduler) d.scheduler.consume = ($event.target as HTMLSelectElement).value })">
            <option value="reset">{{ t('combatEditorConsumeReset') }}</option>
            <option value="carry">{{ t('combatEditorConsumeCarry') }}</option>
          </select>
        </label>
      </div>
    </div>

    <div class="cee-section-head">
      <h5>{{ t('combatEditorResources') }}</h5>
      <button type="button" class="cee-add cee-add-inline" @click="addResource">+ {{ t('combatEditorAddResource') }}</button>
    </div>
    <div class="cee-resource-list">
      <article v-for="(resource, index) in model.resources" :key="index" class="cee-resource">
        <div class="cee-resource-grid">
          <label class="cee-field">
            <span>{{ t('combatEditorFieldId') }}</span>
            <input v-model="resource.id" :placeholder="t('combatEditorResourceIdPlaceholder')" @change="update(() => undefined)">
          </label>
          <label class="cee-field">
            <span>{{ t('combatEditorSource') }}</span>
            <select v-model="resource.source" @change="update(() => undefined)">
              <option value="hp">{{ t('combatEditorHp') }}</option>
              <option value="special_stat">{{ t('combatEditorSpecialStat') }}</option>
              <option value="combat_state">{{ t('combatEditorCombatState') }}</option>
            </select>
          </label>
          <button type="button" class="cee-remove" :aria-label="t('combatEditorRemove')" :title="t('combatEditorRemove')" @click="removeResource(index)">×</button>
          <label v-if="resource.source === 'special_stat'" class="cee-field">
            <span>{{ t('combatEditorStat') }}</span>
            <input v-model="resource.stat" :placeholder="t('combatEditorStatKeyPlaceholder')" @change="update(() => undefined)">
          </label>
          <label v-if="resource.source !== 'hp'" class="cee-field">
            <span>{{ t('combatEditorMaximum') }}</span>
            <input type="number" :value="resource.maximum ?? undefined" :placeholder="t('combatEditorMaximumPlaceholder')"
                   @change="update(d => { d.resources[index].maximum = ($event.target as HTMLInputElement).valueAsNumber || null })">
          </label>
        </div>
      </article>
    </div>

    <div class="cee-section-head">
      <h5>{{ t('combatEditorActions') }}</h5>
      <button type="button" class="cee-add cee-add-inline" @click="addAction">+ {{ t('combatEditorAddAction') }}</button>
    </div>
    <div class="cee-action-list">
      <article v-for="(action, aIndex) in model.actions" :key="aIndex" class="cee-action">
        <div class="cee-action-head">
          <label class="cee-field cee-action-id">
            <span>{{ t('combatEditorFieldId') }}</span>
            <input v-model="action.id" :placeholder="t('combatEditorActionIdPlaceholder')" @change="update(() => undefined)">
          </label>
          <label class="cee-field cee-action-kind">
            <span>{{ t('combatEditorActionKind') }}</span>
            <select v-model="action.kind" @change="update(() => undefined)">
              <option value="attack">{{ t('combatEditorAttack') }}</option>
              <option value="ability">{{ t('combatEditorAbility') }}</option>
              <option value="consumable">{{ t('combatEditorConsumable') }}</option>
            </select>
          </label>
          <label class="cee-field cee-action-name">
            <span>{{ t('combatEditorDisplayName') }}</span>
            <input v-model="action.name" :placeholder="t('combatEditorDisplayNamePlaceholder')" @change="update(() => undefined)">
          </label>
          <button type="button" class="cee-remove" :aria-label="t('combatEditorRemove')" :title="t('combatEditorRemove')" @click="removeAction(aIndex)">×</button>
        </div>
        <div v-for="(cost, cIndex) in action.costs" :key="`c${cIndex}`" class="cee-sub">
          <div class="cee-sub-head">
            <strong>{{ t('combatEditorCost') }}</strong>
            <button type="button" class="cee-remove" :aria-label="t('combatEditorRemove')" :title="t('combatEditorRemove')" @click="update(d => d.actions[aIndex].costs.splice(cIndex, 1))">×</button>
          </div>
          <div class="cee-sub-grid">
            <label class="cee-field">
              <span>{{ t('combatEditorResource') }}</span>
              <select v-model="cost.resource" @change="update(() => undefined)">
                <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
              </select>
            </label>
            <label class="cee-field">
              <span>{{ t('combatEditorFormula') }}</span>
              <select :value="cost.amount.type" @change="setCostFormulaType(aIndex, cIndex, ($event.target as HTMLSelectElement).value)">
                <option value="constant">{{ t('combatEditorFormulaConstant') }}</option>
                <option value="dice">{{ t('combatEditorFormulaDice') }}</option>
                <option value="attribute">{{ t('combatEditorFormulaAttribute') }}</option>
                <option v-if="cost.amount.type === 'combine'" value="combine">{{ t('combatEditorFormulaCombine') }}</option>
                <option v-if="cost.amount.type === 'json'" value="json">{{ t('combatEditorFormulaJson') }}</option>
              </select>
            </label>
            <label v-if="cost.amount.type === 'constant'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input type="number" :value="cost.amount.value" :placeholder="t('combatEditorFormulaValuePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'constant') value.value = Number(($event.target as HTMLInputElement).value) })">
            </label>
            <label v-else-if="cost.amount.type === 'dice'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input :value="cost.amount.formula" :placeholder="t('combatEditorFormulaDicePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'dice') value.formula = ($event.target as HTMLInputElement).value.trim() })">
            </label>
            <label v-else-if="cost.amount.type === 'attribute'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input :value="cost.amount.id" :placeholder="t('combatEditorFormulaAttributePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'attribute') value.id = ($event.target as HTMLInputElement).value.trim() })">
            </label>
            <div v-else class="cee-advanced-value">{{ formulaTypeLabel(cost.amount.type) }} · {{ t('combatEditorAdvancedFormula') }}</div>
          </div>
        </div>
        <div class="cee-sub">
          <div class="cee-sub-head"><strong>{{ t('combatEditorInventoryCost') }}</strong></div>
          <div class="cee-sub-grid">
            <label class="cee-field cee-item-field">
              <span>{{ t('combatEditorItem') }}</span>
              <input :value="action.consume_item?.item || ''" :placeholder="t('combatEditorItemPlaceholder')"
                     @change="update(d => {
                       const raw = ($event.target as HTMLInputElement).value.trim()
                       if (raw) d.actions[aIndex].consume_item = { item: raw, qty: d.actions[aIndex].consume_item?.qty || 1 }
                       else delete d.actions[aIndex].consume_item
                     })">
            </label>
            <label v-if="action.consume_item" class="cee-field cee-quantity-field">
              <span>{{ t('combatEditorQuantity') }}</span>
              <input type="number" min="1" :value="action.consume_item.qty"
                     @change="update(d => { if (d.actions[aIndex].consume_item) d.actions[aIndex].consume_item.qty = Number(($event.target as HTMLInputElement).value) || 1 })">
            </label>
          </div>
        </div>
        <div v-for="(effect, eIndex) in action.effects" :key="`e${eIndex}`" class="cee-sub">
          <div class="cee-sub-head">
            <strong>{{ t('combatEditorEffect') }}</strong>
            <button type="button" class="cee-remove" :aria-label="t('combatEditorRemove')" :title="t('combatEditorRemove')" @click="update(d => d.actions[aIndex].effects.splice(eIndex, 1))">×</button>
          </div>
          <div class="cee-sub-grid">
            <label class="cee-field">
              <span>{{ t('combatEditorEffect') }}</span>
              <select :value="effect.kind" @change="setEffectKind(aIndex, eIndex, ($event.target as HTMLSelectElement).value)">
                <option value="damage">{{ t('combatEditorDamage') }}</option>
                <option value="resource_change">{{ t('combatEditorResourceChange') }}</option>
                <option value="modify_stat">{{ t('combatEditorModifyStat') }}</option>
              </select>
            </label>
            <label v-if="effect.kind === 'resource_change'" class="cee-field">
              <span>{{ t('combatEditorResource') }}</span>
              <select v-model="effect.resource" @change="update(() => undefined)">
                <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
              </select>
            </label>
            <label v-if="effect.kind === 'modify_stat'" class="cee-field">
              <span>{{ t('combatEditorStat') }}</span>
              <input v-model="effect.resource" :placeholder="t('combatEditorStatKeyPlaceholder')" @change="update(() => undefined)">
            </label>
            <label v-if="effect.kind === 'modify_stat'" class="cee-field">
              <span>{{ t('combatEditorDuration') }}</span>
              <input type="number" min="1" v-model.number="effect.duration" :placeholder="t('combatEditorDurationPlaceholder')" @change="update(() => undefined)">
            </label>
            <label v-if="effect.kind === 'damage'" class="cee-field">
              <span>{{ t('combatEditorDamageType') }}</span>
              <input v-model="effect.damage_type" :placeholder="t('combatEditorDamageTypePlaceholder')" @change="update(() => undefined)">
            </label>
            <label class="cee-field">
              <span>{{ t('combatEditorFormula') }}</span>
              <select :value="(effect.amount || { type: 'constant' }).type" @change="setEffectFormulaType(aIndex, eIndex, ($event.target as HTMLSelectElement).value)">
                <option value="constant">{{ t('combatEditorFormulaConstant') }}</option>
                <option value="dice">{{ t('combatEditorFormulaDice') }}</option>
                <option value="attribute">{{ t('combatEditorFormulaAttribute') }}</option>
                <option v-if="effect.amount?.type === 'combine'" value="combine">{{ t('combatEditorFormulaCombine') }}</option>
                <option v-if="effect.amount?.type === 'json'" value="json">{{ t('combatEditorFormulaJson') }}</option>
              </select>
            </label>
            <label v-if="effect.amount?.type === 'constant'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input type="number" :value="effect.amount.value" :placeholder="t('combatEditorFormulaValuePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'constant') value.value = Number(($event.target as HTMLInputElement).value) })">
            </label>
            <label v-else-if="effect.amount?.type === 'dice'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input :value="effect.amount.formula" :placeholder="t('combatEditorFormulaDicePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'dice') value.formula = ($event.target as HTMLInputElement).value.trim() })">
            </label>
            <label v-else-if="effect.amount?.type === 'attribute'" class="cee-field">
              <span>{{ t('combatEditorAmount') }}</span>
              <input :value="effect.amount.id" :placeholder="t('combatEditorFormulaAttributePlaceholder')"
                     @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'attribute') value.id = ($event.target as HTMLInputElement).value.trim() })">
            </label>
            <div v-else-if="effect.amount" class="cee-advanced-value">{{ formulaTypeLabel(effect.amount.type) }} · {{ t('combatEditorAdvancedFormula') }}</div>
          </div>
        </div>
        <div class="cee-action-actions">
          <button type="button" class="cee-add" @click="update(d => d.actions[aIndex].costs.push({ resource: resourceOptions[0] || '', amount: { type: 'constant', value: 1 } }))">+ {{ t('combatEditorAddCost') }}</button>
          <button type="button" class="cee-add" @click="update(d => d.actions[aIndex].effects.push({ kind: 'damage', amount: { type: 'dice', formula: '1d6' } }))">+ {{ t('combatEditorAddEffect') }}</button>
        </div>
      </article>
    </div>
    <p class="cee-hint">{{ t('combatEditorHint') }}</p>
  </section>
</template>

<style scoped>
.combat-ext-editor { display: grid; gap: 12px; min-width: 0; padding: 12px; border: 1px solid var(--df-border-soft); border-radius: 8px; background: color-mix(in srgb, var(--df-surface-1) 88%, var(--df-interactive) 12%); }
.combat-ext-editor *, .combat-ext-editor *::before, .combat-ext-editor *::after { box-sizing: border-box; min-width: 0; }
.cee-title-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.combat-ext-editor h4 { margin: 0; font-size: 14px; color: var(--df-accent-strong); }
.combat-ext-editor h5 { margin: 0; font-size: 12px; color: var(--df-text-muted); }
.cee-scheduler { display: grid; gap: 10px; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 7px; background: color-mix(in srgb, var(--df-interactive) 6%, transparent); }
.cee-scheduler-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.cee-field { display: grid; gap: 4px; min-width: 0; margin: 0; color: var(--df-text-muted); font-size: 11px; }
.cee-field > span { color: var(--df-text-muted); line-height: 1.4; word-break: keep-all; overflow-wrap: anywhere; }
.cee-field input, .cee-field select { width: 100%; min-width: 0; }
.cee-section-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.cee-resource-list, .cee-action-list { display: grid; gap: 8px; min-width: 0; }
.cee-resource { min-width: 0; padding: 9px; border: 1px solid var(--df-border-soft); border-radius: 7px; background: color-mix(in srgb, var(--df-surface-2) 90%, var(--df-interactive) 10%); }
.cee-resource-grid { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(130px, 1.25fr) auto; gap: 8px; align-items: end; min-width: 0; }
.cee-action { display: grid; gap: 9px; min-width: 0; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 7px; background: color-mix(in srgb, var(--df-surface-2) 92%, var(--df-interactive) 8%); }
.cee-action-head { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(105px, .7fr) minmax(150px, 1fr) auto; gap: 8px; align-items: end; min-width: 0; }
.cee-sub { display: grid; gap: 8px; min-width: 0; padding: 9px; border: 1px dashed var(--df-border-soft); border-radius: 6px; background: color-mix(in srgb, var(--df-interactive) 4%, transparent); }
.cee-sub-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.cee-sub-head strong { color: var(--df-accent-strong); font-size: 11px; }
.cee-sub-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; min-width: 0; }
.cee-item-field { grid-column: span 2; }
.cee-advanced-value { display: flex; align-items: center; min-height: 36px; padding: 0 9px; border: 1px solid var(--df-border-soft); border-radius: 6px; color: var(--df-text-muted); font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.cee-action-actions { display: flex; flex-wrap: wrap; gap: 7px; }
.cee-add { min-height: 30px; padding: 4px 9px; font-size: 12px; }
.cee-add-inline { flex: 0 0 auto; }
.cee-remove { display: inline-flex; align-items: center; justify-content: center; width: 30px; min-width: 30px; min-height: 30px; padding: 0; border: 1px solid transparent; border-radius: 5px; background: transparent; color: var(--df-danger, #e5484d); cursor: pointer; }
.cee-remove:hover { border-color: color-mix(in srgb, var(--df-danger, #e5484d) 40%, transparent); background: color-mix(in srgb, var(--df-danger, #e5484d) 10%, transparent); transform: none; }
.cee-hint { margin: 0; color: var(--df-text-muted); font-size: 11px; line-height: 1.45; }

@media (max-width: 700px) {
  .cee-action-head { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 30px; grid-template-areas: "id id remove" "name kind kind"; }
  .cee-action-id { grid-area: id; }
  .cee-action-name { grid-area: name; }
  .cee-action-kind { grid-area: kind; }
  .cee-action-head > .cee-remove { grid-area: remove; }
  .cee-sub-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cee-item-field { grid-column: auto; }
}

@media (max-width: 480px) {
  .cee-scheduler-grid { grid-template-columns: 1fr; }
  .cee-resource-grid { grid-template-columns: minmax(0, 1fr) 30px; }
  .cee-resource-grid > .cee-field { grid-column: 1; }
  .cee-resource-grid > .cee-remove { grid-column: 2; grid-row: 1; }
  .cee-action-head { grid-template-columns: minmax(0, 1fr) 30px; grid-template-areas: "id remove" "name name" "kind kind"; }
  .cee-sub-grid { grid-template-columns: 1fr; }
}
</style>
