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
    <h4>{{ t('combatEditorTitle') }}</h4>

    <label class="cee-row">
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
    <div v-if="model.scheduler?.kind === 'threshold'" class="cee-row">
      <span>{{ t('combatEditorThresholdValue') }}</span>
      <input type="number" :value="model.scheduler.threshold" min="1"
             @change="update(d => { if (d.scheduler) d.scheduler.threshold = Number(($event.target as HTMLInputElement).value) })">
      <span>{{ t('combatEditorOverflow') }}</span>
      <select :value="model.scheduler.overflow" @change="update(d => { if (d.scheduler) d.scheduler.overflow = ($event.target as HTMLSelectElement).value })">
        <option value="carry">{{ t('combatEditorOverflowCarry') }}</option>
        <option value="clamp">{{ t('combatEditorOverflowClamp') }}</option>
      </select>
      <span>{{ t('combatEditorConsume') }}</span>
      <select :value="model.scheduler.consume" @change="update(d => { if (d.scheduler) d.scheduler.consume = ($event.target as HTMLSelectElement).value })">
        <option value="reset">{{ t('combatEditorConsumeReset') }}</option>
        <option value="carry">{{ t('combatEditorConsumeCarry') }}</option>
      </select>
    </div>

    <h5>{{ t('combatEditorResources') }}</h5>
    <div v-for="(resource, index) in model.resources" :key="index" class="cee-resource">
      <input v-model="resource.id" :placeholder="t('combatEditorResourceIdPlaceholder')" @change="update(() => undefined)">
      <select v-model="resource.source" @change="update(() => undefined)">
        <option value="hp">{{ t('combatEditorHp') }}</option>
        <option value="special_stat">{{ t('combatEditorSpecialStat') }}</option>
        <option value="combat_state">{{ t('combatEditorCombatState') }}</option>
      </select>
      <input v-if="resource.source === 'special_stat'" v-model="resource.stat" :placeholder="t('combatEditorStatKeyPlaceholder')" @change="update(() => undefined)">
      <input v-if="resource.source !== 'hp'" type="number" :value="resource.maximum ?? undefined" :placeholder="t('combatEditorMaximumPlaceholder')"
             @change="update(d => { d.resources[index].maximum = ($event.target as HTMLInputElement).valueAsNumber || null })">
      <button type="button" class="cee-remove" @click="removeResource(index)">×</button>
    </div>
    <button type="button" class="cee-add" @click="addResource">+ {{ t('combatEditorAddResource') }}</button>

    <h5>{{ t('combatEditorActions') }}</h5>
    <div v-for="(action, aIndex) in model.actions" :key="aIndex" class="cee-action">
      <div class="cee-row">
        <input v-model="action.id" :placeholder="t('combatEditorActionIdPlaceholder')" @change="update(() => undefined)">
        <select v-model="action.kind" @change="update(() => undefined)">
          <option value="attack">{{ t('combatEditorAttack') }}</option>
          <option value="ability">{{ t('combatEditorAbility') }}</option>
          <option value="consumable">{{ t('combatEditorConsumable') }}</option>
        </select>
        <input v-model="action.name" :placeholder="t('combatEditorDisplayNamePlaceholder')" @change="update(() => undefined)">
        <button type="button" class="cee-remove" @click="removeAction(aIndex)">×</button>
      </div>
      <div v-for="(cost, cIndex) in action.costs" :key="`c${cIndex}`" class="cee-sub">
        <span>{{ t('combatEditorCost') }}</span>
        <select v-model="cost.resource" @change="update(() => undefined)">
          <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
        </select>
        <select :value="cost.amount.type" @change="setCostFormulaType(aIndex, cIndex, ($event.target as HTMLSelectElement).value)">
          <option value="constant">{{ t('combatEditorFormulaConstant') }}</option>
          <option value="dice">{{ t('combatEditorFormulaDice') }}</option>
          <option value="attribute">{{ t('combatEditorFormulaAttribute') }}</option>
          <option v-if="cost.amount.type === 'combine'" value="combine">{{ t('combatEditorFormulaCombine') }}</option>
          <option v-if="cost.amount.type === 'json'" value="json">{{ t('combatEditorFormulaJson') }}</option>
        </select>
        <input v-if="cost.amount.type === 'constant'" type="number" :value="cost.amount.value" :placeholder="t('combatEditorFormulaValuePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'constant') value.value = Number(($event.target as HTMLInputElement).value) })">
        <input v-else-if="cost.amount.type === 'dice'" :value="cost.amount.formula" :placeholder="t('combatEditorFormulaDicePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'dice') value.formula = ($event.target as HTMLInputElement).value.trim() })">
        <input v-else-if="cost.amount.type === 'attribute'" :value="cost.amount.id" :placeholder="t('combatEditorFormulaAttributePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].costs[cIndex].amount; if (value.type === 'attribute') value.id = ($event.target as HTMLInputElement).value.trim() })">
        <small v-else>{{ formulaTypeLabel(cost.amount.type) }} · {{ t('combatEditorAdvancedFormula') }}</small>
        <button type="button" class="cee-remove" @click="update(d => d.actions[aIndex].costs.splice(cIndex, 1))">×</button>
      </div>
      <div class="cee-sub">
        <span>{{ t('combatEditorInventoryCost') }}</span>
        <input :value="action.consume_item?.item || ''" :placeholder="t('combatEditorItemPlaceholder')"
               @change="update(d => {
                 const raw = ($event.target as HTMLInputElement).value.trim()
                 if (raw) d.actions[aIndex].consume_item = { item: raw, qty: d.actions[aIndex].consume_item?.qty || 1 }
                 else delete d.actions[aIndex].consume_item
               })">
        <input v-if="action.consume_item" type="number" min="1" :value="action.consume_item.qty"
               @change="update(d => { if (d.actions[aIndex].consume_item) d.actions[aIndex].consume_item.qty = Number(($event.target as HTMLInputElement).value) || 1 })">
      </div>
      <div v-for="(effect, eIndex) in action.effects" :key="`e${eIndex}`" class="cee-sub">
        <span>{{ t('combatEditorEffect') }}</span>
        <select :value="effect.kind" @change="setEffectKind(aIndex, eIndex, ($event.target as HTMLSelectElement).value)">
          <option value="damage">{{ t('combatEditorDamage') }}</option>
          <option value="resource_change">{{ t('combatEditorResourceChange') }}</option>
          <option value="modify_stat">{{ t('combatEditorModifyStat') }}</option>
        </select>
        <select v-if="effect.kind === 'resource_change'" v-model="effect.resource" @change="update(() => undefined)">
          <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
        </select>
        <input v-if="effect.kind === 'modify_stat'" v-model="effect.resource" :placeholder="t('combatEditorStatKeyPlaceholder')" @change="update(() => undefined)">
        <input v-if="effect.kind === 'modify_stat'" type="number" min="1" v-model.number="effect.duration" :placeholder="t('combatEditorDurationPlaceholder')" @change="update(() => undefined)">
        <input v-if="effect.kind === 'damage'" v-model="effect.damage_type" :placeholder="t('combatEditorDamageTypePlaceholder')" @change="update(() => undefined)">
        <select :value="(effect.amount || { type: 'constant' }).type" @change="setEffectFormulaType(aIndex, eIndex, ($event.target as HTMLSelectElement).value)">
          <option value="constant">{{ t('combatEditorFormulaConstant') }}</option>
          <option value="dice">{{ t('combatEditorFormulaDice') }}</option>
          <option value="attribute">{{ t('combatEditorFormulaAttribute') }}</option>
          <option v-if="effect.amount?.type === 'combine'" value="combine">{{ t('combatEditorFormulaCombine') }}</option>
          <option v-if="effect.amount?.type === 'json'" value="json">{{ t('combatEditorFormulaJson') }}</option>
        </select>
        <input v-if="effect.amount?.type === 'constant'" type="number" :value="effect.amount.value" :placeholder="t('combatEditorFormulaValuePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'constant') value.value = Number(($event.target as HTMLInputElement).value) })">
        <input v-else-if="effect.amount?.type === 'dice'" :value="effect.amount.formula" :placeholder="t('combatEditorFormulaDicePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'dice') value.formula = ($event.target as HTMLInputElement).value.trim() })">
        <input v-else-if="effect.amount?.type === 'attribute'" :value="effect.amount.id" :placeholder="t('combatEditorFormulaAttributePlaceholder')"
               @change="update(d => { const value = d.actions[aIndex].effects[eIndex].amount; if (value?.type === 'attribute') value.id = ($event.target as HTMLInputElement).value.trim() })">
        <small v-else-if="effect.amount">{{ formulaTypeLabel(effect.amount.type) }} · {{ t('combatEditorAdvancedFormula') }}</small>
        <button type="button" class="cee-remove" @click="update(d => d.actions[aIndex].effects.splice(eIndex, 1))">×</button>
      </div>
      <button type="button" class="cee-add" @click="update(d => d.actions[aIndex].costs.push({ resource: resourceOptions[0] || '', amount: { type: 'constant', value: 1 } }))">+ {{ t('combatEditorAddCost') }}</button>
      <button type="button" class="cee-add" @click="update(d => d.actions[aIndex].effects.push({ kind: 'damage', amount: { type: 'dice', formula: '1d6' } }))">+ {{ t('combatEditorAddEffect') }}</button>
    </div>
    <button type="button" class="cee-add" @click="addAction">+ {{ t('combatEditorAddAction') }}</button>
    <p class="cee-hint">{{ t('combatEditorHint') }}</p>
  </section>
</template>

<style scoped>
.combat-ext-editor { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--df-border-soft); border-radius: 8px; }
.combat-ext-editor h4 { margin: 0; font-size: 13px; }
.combat-ext-editor h5 { margin: 6px 0 2px; font-size: 12px; color: var(--df-text-muted); }
.cee-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cee-row span { font-size: 12px; color: var(--df-text-muted); }
.cee-resource, .cee-sub { display: flex; gap: 6px; align-items: center; padding-left: 8px; }
.cee-action { display: grid; gap: 4px; padding: 6px; border: 1px dashed var(--df-border-soft); border-radius: 6px; }
.cee-add { justify-self: start; font-size: 12px; }
.cee-remove { border: 0; background: transparent; cursor: pointer; color: var(--df-danger, #e5484d); }
.cee-hint { margin: 0; font-size: 11px; color: var(--df-text-muted); }
</style>
