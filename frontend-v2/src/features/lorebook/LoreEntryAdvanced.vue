<script setup lang="ts">
// 条目编辑器的折叠区：除「名称/类型/内容/触发方式/触发词/可见性」之外的一切。
// 所有字段都直接绑定后端 canonical payload key（order 即 insertion_order）。
import { useLocale } from '@/composables/useLocale'
import { normalizeVectorActivation } from './entryFilters'

export interface LoreEntryAdvancedModel {
  secondary_keys: string[]
  /** canonical「次级关键词是否参与过滤」开关；selective=false 时 keys 仅作为数据保留。 */
  selective?: boolean
  selective_logic: string
  use_regex: boolean
  case_sensitive: boolean
  match_whole_words: boolean
  vector_activation?: string
  /** legacy 主关键词逻辑（DiceFrame 旧字段），仅兼容旧配置，不是产品「触发方式」。 */
  match_mode?: string
  scan_depth: number
  priority: number
  probability: number
  prompt_slot: string
  groups: string[]
  group_weight: number
  group_scoring?: string
  sticky?: number
  cooldown?: number
  delay?: number
  /** canonical 插入顺序：DB/接口字段名就是 order。 */
  order?: number
  /** 预算不足时优先纳入。 */
  prioritize_inclusion?: boolean
  non_recursable: boolean
  prevent_further_recursion: boolean
  delay_until_recursion: boolean
  recursion_level: number
  tier?: string
  unreliable?: boolean
  sync_on_enter?: boolean
  is_constant?: boolean
  connected_to?: string[]
  triggers_recursive?: string[]
}
const props = defineProps<{ modelValue: LoreEntryAdvancedModel }>()
const emit = defineEmits<{ 'update:modelValue':[value:LoreEntryAdvancedModel] }>()
const { t } = useLocale()
function update<K extends keyof LoreEntryAdvancedModel>(key:K, value:LoreEntryAdvancedModel[K]) { emit('update:modelValue', { ...props.modelValue, [key]: value }) }
function updateList(key: 'secondary_keys' | 'groups' | 'connected_to' | 'triggers_recursive', value: string) { update(key, value.split(',').map(item => item.trim()).filter(Boolean)) }
function updateNumber<K extends 'scan_depth' | 'priority' | 'probability' | 'group_weight' | 'sticky' | 'cooldown' | 'delay' | 'order' | 'recursion_level'>(key: K, event: Event) { update(key, Number((event.target as HTMLInputElement).value) as LoreEntryAdvancedModel[K]) }
function updateCheckbox<K extends 'selective' | 'use_regex' | 'case_sensitive' | 'match_whole_words' | 'non_recursable' | 'prevent_further_recursion' | 'delay_until_recursion' | 'prioritize_inclusion' | 'unreliable' | 'sync_on_enter' | 'is_constant'>(key: K, event: Event) { update(key, (event.target as HTMLInputElement).checked as LoreEntryAdvancedModel[K]) }
</script>

<template>
  <details class="lore-entry-advanced">
    <summary>{{ t('loreEntryAdvanced') }}</summary>
    <label data-field="tier">{{ t('tier') }} <select :value="modelValue.tier || 'background'" @change="update('tier', ($event.target as HTMLSelectElement).value)"><option value="core">{{ t('core') }}</option><option value="background">{{ t('background') }}</option><option value="archived">{{ t('archived') }}</option></select></label>
    <label data-field="order">{{ t('loreEntryInsertionOrder') }} <input type="number" :value="modelValue.order ?? 100" @input="updateNumber('order', $event)"></label>
    <label data-field="prioritize_inclusion"><input type="checkbox" :checked="!!modelValue.prioritize_inclusion" @change="updateCheckbox('prioritize_inclusion', $event)"> {{ t('loreEntryPrioritizeInclusion') }}</label>
    <label data-field="connected_to">{{ t('connectedEntries') }} <input type="text" :value="(modelValue.connected_to || []).join(', ')" :placeholder="t('connectedEntriesPlaceholder')" @input="updateList('connected_to', ($event.target as HTMLInputElement).value)"></label>
    <label data-field="triggers_recursive">{{ t('recursiveTrigger') }} <input type="text" :value="(modelValue.triggers_recursive || []).join(', ')" :placeholder="t('recursiveTriggerPlaceholder')" @input="updateList('triggers_recursive', ($event.target as HTMLInputElement).value)"></label>
    <div class="check-row">
      <label data-field="unreliable"><input type="checkbox" :checked="!!modelValue.unreliable" @change="updateCheckbox('unreliable', $event)"> {{ t('unreliableMemory') }}</label>
      <label data-field="sync_on_enter"><input type="checkbox" :checked="!!modelValue.sync_on_enter" @change="updateCheckbox('sync_on_enter', $event)"> {{ t('syncOnEnter') }}</label>
      <label data-field="is_constant"><input type="checkbox" :checked="!!modelValue.is_constant" @change="updateCheckbox('is_constant', $event)"> {{ t('loreActivationAlways') }}</label>
    </div>
    <label data-field="match_mode">{{ t('loreActivationLegacy') }} <select :value="modelValue.match_mode || 'any'" @change="update('match_mode', ($event.target as HTMLSelectElement).value)"><option value="any">{{ t('matchAny') }}</option><option value="all">{{ t('matchAll') }}</option><option value="not_any">{{ t('matchNotAny') }}</option><option value="not_all">{{ t('matchNotAll') }}</option></select></label>
    <!-- match_mode / selective / selective_logic 是三个独立概念：主匹配逻辑、次级门控开关、次级组合逻辑。 -->
    <!-- 读回路径里 SQLite 布尔列是 0/1：必须布尔化再判断，缺省（undefined）按 canonical 默认 true。 -->
    <label data-field="selective"><input type="checkbox" :checked="!!(modelValue.selective ?? true)" @change="updateCheckbox('selective', $event)"> {{ t('loreEntrySelective') }}</label>
    <label data-field="secondary_keys">{{ t('loreEntrySecondaryKeys') }} <input type="text" :value="(modelValue.secondary_keys || []).join(', ')" @input="updateList('secondary_keys', ($event.target as HTMLInputElement).value)"></label>
    <label data-field="selective_logic">{{ t('loreEntrySelectiveLogic') }} <select :value="modelValue.selective_logic" @change="update('selective_logic', ($event.target as HTMLSelectElement).value)"><option>any</option><option>all</option><option>not_any</option><option>not_all</option></select></label>
    <label data-field="use_regex"><input type="checkbox" :checked="modelValue.use_regex" @change="updateCheckbox('use_regex', $event)"> {{ t('loreEntryRegex') }}</label>
    <label data-field="case_sensitive"><input type="checkbox" :checked="modelValue.case_sensitive" @change="updateCheckbox('case_sensitive', $event)"> {{ t('loreEntryCaseSensitive') }}</label>
    <label data-field="match_whole_words"><input type="checkbox" :checked="modelValue.match_whole_words" @change="updateCheckbox('match_whole_words', $event)"> {{ t('loreEntryMatchWholeWords') }}</label>
    <label data-field="vector_activation">{{ t('loreVectorActivation') }} <select :value="normalizeVectorActivation(modelValue.vector_activation)" @change="update('vector_activation', ($event.target as HTMLSelectElement).value)"><option value="off">off</option><option value="hybrid">hybrid</option><option value="vector_only">vector only</option></select></label>
    <label data-field="scan_depth">{{ t('loreEntryScanDepth') }} <input type="number" :value="modelValue.scan_depth" @input="updateNumber('scan_depth', $event)"></label>
    <label data-field="priority">{{ t('loreEntryPriority') }} <input type="number" :value="modelValue.priority" @input="updateNumber('priority', $event)"></label>
    <label data-field="probability">{{ t('loreEntryProbability') }} <input type="number" min="0" max="100" :value="modelValue.probability" @input="updateNumber('probability', $event)"></label>
    <label data-field="groups">{{ t('loreEntryGroups') }} <input type="text" :value="(modelValue.groups || []).join(', ')" @input="updateList('groups', ($event.target as HTMLInputElement).value)"></label>
    <label data-field="group_weight">{{ t('loreEntryGroupWeight') }} <input type="number" :value="modelValue.group_weight" @input="updateNumber('group_weight', $event)"></label>
    <label data-field="group_scoring">{{ t('loreEntryGroupScoring') }} <input type="text" :value="modelValue.group_scoring || ''" @input="update('group_scoring', ($event.target as HTMLInputElement).value)"></label>
    <label data-field="sticky">{{ t('loreEntrySticky') }} <input type="number" :value="modelValue.sticky ?? 0" @input="updateNumber('sticky', $event)"></label>
    <label data-field="cooldown">{{ t('loreEntryCooldown') }} <input type="number" :value="modelValue.cooldown ?? 0" @input="updateNumber('cooldown', $event)"></label>
    <label data-field="delay">{{ t('loreEntryDelay') }} <input type="number" :value="modelValue.delay ?? 0" @input="updateNumber('delay', $event)"></label>
    <label data-field="non_recursable"><input type="checkbox" :checked="modelValue.non_recursable" @change="updateCheckbox('non_recursable', $event)"> {{ t('loreEntryNonRecursable') }}</label>
    <label data-field="prevent_further_recursion"><input type="checkbox" :checked="modelValue.prevent_further_recursion" @change="updateCheckbox('prevent_further_recursion', $event)"> {{ t('loreEntryPreventRecursion') }}</label>
    <label data-field="delay_until_recursion"><input type="checkbox" :checked="modelValue.delay_until_recursion" @change="updateCheckbox('delay_until_recursion', $event)"> {{ t('loreEntryDelayUntilRecursion') }}</label>
    <label data-field="recursion_level">{{ t('loreEntryRecursionLevel') }} <input type="number" :value="modelValue.recursion_level" @input="updateNumber('recursion_level', $event)"></label>
    <label data-field="prompt_slot">{{ t('loreEntryPromptSlot') }} <input type="text" :value="modelValue.prompt_slot" @input="update('prompt_slot', ($event.target as HTMLInputElement).value)"></label>
  </details>
</template>
