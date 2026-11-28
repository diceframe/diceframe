<script setup lang="ts">
import { computed } from 'vue'
import { NIcon, NSwitch } from 'naive-ui'
import { DiceOutline } from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { useLocale } from '@/composables/useLocale'
import { modelCapability } from '@/utils/providerModels'
import { generationSettingsCopy } from './generationSettingsCopy'
import ReasoningEffortField from './ReasoningEffortField.vue'

defineProps<{ disabled?: boolean }>()
const store = useSettingsStore()
const { locale } = useLocale()
const copy = computed(() => generationSettingsCopy(locale.value))
const providers = computed(() => store.config.ai_providers || [])
const selected = computed(() => providers.value.find(provider => provider.id === store.config.check_provider_ref))
const models = computed(() => (selected.value?.models || []).filter(model => modelCapability(model, selected.value?.model_capabilities?.[model]) === 'chat'))
function setProvider(event: Event) {
  store.setConfigField('check_provider_ref', (event.target as HTMLSelectElement).value)
  store.setConfigField('check_model', '')
  // 换服务商后旧模型的推理强度不再适用，恢复默认。
  store.setConfigField('check_model_reasoning_effort', '')
}
</script>

<template>
  <article id="settings-check-model" class="model-role-card">
    <header><NIcon :component="DiceOutline" /><div><h4>{{ copy.checkTitle }}</h4><p>{{ copy.checkHint }}</p></div></header>
    <label class="model-role-enabled"><span>{{ copy.checkEnabled }}</span><NSwitch :value="!!store.config.check_model_enabled" :disabled="disabled" @update:value="store.setConfigField('check_model_enabled', $event)" /></label>
    <label><span>{{ copy.provider }}</span><select :value="store.config.check_provider_ref || ''" :disabled="disabled || !store.config.check_model_enabled" @change="setProvider"><option value="">{{ copy.chooseProvider }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label>
    <label><span>{{ copy.model }}</span><select :value="store.config.check_model || ''" :disabled="disabled || !store.config.check_model_enabled || !selected" @change="store.setConfigField('check_model', ($event.target as HTMLSelectElement).value)"><option value="">{{ copy.chooseModel }}</option><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
    <ReasoningEffortField config-key="check_model_reasoning_effort" :api-format="selected?.api_format || 'openai'" :disabled="disabled || !store.config.check_model_enabled || !selected" />
    <p v-if="store.config.check_model_enabled && (!selected || !store.config.check_model)" class="muted">{{ copy.checkMissing }}</p>
  </article>
</template>
