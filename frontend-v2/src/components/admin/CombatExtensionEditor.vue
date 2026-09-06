<script setup lang="ts">
import { computed } from 'vue'

/** 受限公式 AST 的可视化编辑模型（v1：叶子 + 一层加减/乘组合 + JSON 兜底）。 */
export type FormulaDraft =
  | { type: 'dice'; formula: string }
  | { type: 'constant'; value: number }
  | { type: 'attribute'; id: string }
  | { type: 'combine'; op: 'add' | 'multiply'; left: FormulaDraft; right: FormulaDraft }
  | { type: 'json'; raw: string }

export interface CostDraft { resource: string; amount: FormulaDraft }
export interface EffectDraft {
  kind: string
  resource?: string
  amount?: FormulaDraft
  damage_type?: string
}
export interface ActionDraft {
  id: string
  kind: string
  name: string
  costs: CostDraft[]
  effects: EffectDraft[]
}
export interface ResourceDraft {
  id: string
  source: 'hp' | 'special_stat' | 'combat_state'
  stat?: string
  maximum?: number | null
  costable?: boolean
  damage_priority?: string | null
  damage_types?: string[] | null
}
export interface CombatDraft {
  scheduler: { kind: string; threshold: number; overflow: string; consume: string; gauge: string; speed: string } | null
  resources: ResourceDraft[]
  actions: ActionDraft[]
}

const props = defineProps<{ modelValue: CombatDraft; resourceOptions: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: CombatDraft] }>()

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
  return { dice: '骰子', constant: '常数', attribute: '属性', combine: '组合', json: 'JSON' }[type]
}
</script>

<template>
  <section class="combat-ext-editor">
    <h4>战斗扩展（可选声明）</h4>

    <label class="cee-row">
      <span>调度器</span>
      <select :value="model.scheduler?.kind || ''" @change="update(d => {
        const kind = ($event.target as HTMLSelectElement).value
        d.scheduler = kind ? { kind, threshold: 100, overflow: 'carry', consume: 'reset', gauge: 'action_gauge', speed: 'action_speed' } : null
      })">
        <option value="">不启用</option>
        <option value="round_robin">固定轮转</option>
        <option value="initiative">先攻轮转</option>
        <option value="threshold">ATB 行动条</option>
      </select>
    </label>
    <div v-if="model.scheduler" class="cee-row">
      <span>阈值</span>
      <input type="number" :value="model.scheduler.threshold" min="1"
             @change="update(d => { if (d.scheduler) d.scheduler.threshold = Number(($event.target as HTMLInputElement).value) })">
      <span>溢出</span>
      <select :value="model.scheduler.overflow" @change="update(d => { if (d.scheduler) d.scheduler.overflow = ($event.target as HTMLSelectElement).value })">
        <option value="carry">保留溢出</option>
        <option value="clamp">钳制在阈值</option>
      </select>
      <span>消耗</span>
      <select :value="model.scheduler.consume" @change="update(d => { if (d.scheduler) d.scheduler.consume = ($event.target as HTMLSelectElement).value })">
        <option value="reset">归零</option>
        <option value="carry">扣除阈值</option>
      </select>
    </div>

    <h5>资源池</h5>
    <div v-for="(resource, index) in model.resources" :key="index" class="cee-resource">
      <input v-model="resource.id" placeholder="id（如 qi）" @change="update(() => undefined)">
      <select v-model="resource.source" @change="update(() => undefined)">
        <option value="hp">HP</option>
        <option value="special_stat">special_stat</option>
        <option value="combat_state">战斗状态</option>
      </select>
      <input v-if="resource.source === 'special_stat'" v-model="resource.stat" placeholder="stat key" @change="update(() => undefined)">
      <input v-if="resource.source !== 'hp'" type="number" :value="resource.maximum ?? undefined" placeholder="上限"
             @change="update(d => { d.resources[index].maximum = ($event.target as HTMLInputElement).valueAsNumber || null })">
      <button class="cee-remove" @click="removeResource(index)">×</button>
    </div>
    <button class="cee-add" @click="addResource">+ 资源池</button>

    <h5>动作目录</h5>
    <div v-for="(action, aIndex) in model.actions" :key="aIndex" class="cee-action">
      <div class="cee-row">
        <input v-model="action.id" placeholder="id（如 ability:qi_palm）" @change="update(() => undefined)">
        <select v-model="action.kind" @change="update(() => undefined)">
          <option value="attack">攻击</option>
          <option value="ability">技能</option>
          <option value="consumable">消耗品</option>
        </select>
        <input v-model="action.name" placeholder="显示名" @change="update(() => undefined)">
        <button class="cee-remove" @click="removeAction(aIndex)">×</button>
      </div>
      <div v-for="(cost, cIndex) in action.costs" :key="`c${cIndex}`" class="cee-sub">
        <span>消耗</span>
        <select v-model="cost.resource" @change="update(() => undefined)">
          <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
        </select>
        <small>{{ formulaTypeLabel(cost.amount.type) }}</small>
        <button class="cee-remove" @click="update(d => d.actions[aIndex].costs.splice(cIndex, 1))">×</button>
      </div>
      <div v-for="(effect, eIndex) in action.effects" :key="`e${eIndex}`" class="cee-sub">
        <span>效果</span>
        <select v-model="effect.kind" @change="update(() => undefined)">
          <option value="damage">伤害</option>
          <option value="resource_change">资源变化</option>
        </select>
        <select v-if="effect.kind === 'resource_change'" v-model="effect.resource" @change="update(() => undefined)">
          <option v-for="resource in resourceOptions" :key="resource" :value="resource">{{ resource }}</option>
        </select>
        <input v-if="effect.kind === 'damage'" v-model="effect.damage_type" placeholder="伤害类型" @change="update(() => undefined)">
        <small>{{ formulaTypeLabel((effect.amount || { type: 'constant', value: 0 }).type) }}</small>
        <button class="cee-remove" @click="update(d => d.actions[aIndex].effects.splice(eIndex, 1))">×</button>
      </div>
      <button class="cee-add" @click="update(d => d.actions[aIndex].costs.push({ resource: resourceOptions[0] || '', amount: { type: 'constant', value: 1 } }))">+ 消耗</button>
      <button class="cee-add" @click="update(d => d.actions[aIndex].effects.push({ kind: 'damage', amount: { type: 'dice', formula: '1d6' } }))">+ 效果</button>
    </div>
    <button class="cee-add" @click="addAction">+ 动作</button>
    <p class="cee-hint">组合公式与高级节点暂用动作 JSON 视图编辑；保存时服务端会完整校验。</p>
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
