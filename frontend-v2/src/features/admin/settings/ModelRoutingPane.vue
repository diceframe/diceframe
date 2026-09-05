<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NIcon, NInput, NInputNumber, NSwitch } from 'naive-ui'
import { AlertCircleOutline, CheckmarkCircleOutline, CubeOutline, ImageOutline, MicOutline, ServerOutline, SparklesOutline, VolumeHighOutline } from '@vicons/ionicons5'
import HelpButton from '@/components/common/HelpButton.vue'
import TestResultCard from '@/components/admin/TestResultCard.vue'
import { useLocale } from '@/composables/useLocale'
import { useSettingsStore } from '@/stores/useSettingsStore'
import { modelCapability, type ModelCapability } from '@/utils/providerModels'
import type { AppConfig, TestResult } from '@/api/types'

defineProps<{
  supported: boolean
  saving: boolean
  embeddingTesting: boolean
  embeddingResult: TestResult | null
}>()

const emit = defineEmits<{
  save: []
  'open-providers': []
  'toggle-and-save': [key: keyof AppConfig, value: boolean]
  'test-embedding': []
}>()

const store = useSettingsStore()
const { t } = useLocale()
const providers = computed(() => store.config.ai_providers || [])
const openAiProviders = computed(() => providers.value.filter(provider => provider.api_format === 'openai'))
const ttsProvider = computed(() => String(store.config.tts_provider || 'browser'))
const asrProvider = computed(() => String(store.config.asr_provider || 'disabled'))

function eventValue(event: Event): string {
  return (event.target as HTMLSelectElement | null)?.value || ''
}

function setString(key: keyof AppConfig, value: string | number) {
  store.setConfigField(key, String(value).trim())
}

function setNumber(key: keyof AppConfig, value: string | number | null) {
  if (value != null) store.setConfigField(key, Number(value))
}

function savedModels(providerId: string, capability?: ModelCapability): string[] {
  const provider = providers.value.find(item => item.id === providerId)
  const models = provider?.models || []
  return capability
    ? models.filter(model => modelCapability(model, provider?.model_capabilities?.[model]) === capability)
    : models
}

function setRoleProvider(
  providerKey: keyof AppConfig,
  modelKey: keyof AppConfig,
  providerId: string,
  capability: ModelCapability,
) {
  setString(providerKey, providerId)
  const models = savedModels(providerId, capability)
  const current = String((store.config as Record<string, unknown>)[modelKey] || '')
  if (!models.includes(current)) setString(modelKey, models[0] || '')
}

function setTtsProvider(value: string) {
  setString('tts_provider', value)
  if (value === 'browser' || value === 'edge-tts') setString('tts_provider_ref', '')
  const voice = String(store.config.tts_default_voice || '')
  if (value === 'edge-tts' && !voice.endsWith('Neural')) setString('tts_default_voice', 'zh-CN-XiaoxiaoNeural')
  else if (value === 'openai-compatible' && voice.endsWith('Neural')) setString('tts_default_voice', 'alloy')
}

function setAsrProvider(value: string) {
  setString('asr_provider', value)
  if (value === 'disabled') setString('asr_provider_ref', '')
}
</script>

<template>
  <div class="settings-pane model-routing-pane">
    <header class="model-routing-header">
      <div><h3>{{ t('modelRoutingTitle') }}</h3><p>{{ t('modelRoutingHint') }}</p></div>
      <div class="model-routing-actions">
        <HelpButton :title="t('modelRoutingHelpTitle')">
          <h4>{{ t('modelRoutingHelpMainTitle') }}</h4><p>{{ t('modelRoutingHelpMainText') }}</p>
          <h4>{{ t('modelRoutingHelpFallbackTitle') }}</h4><p>{{ t('modelRoutingHelpFallbackText') }}</p>
          <h4>{{ t('modelRoutingHelpOptionalTitle') }}</h4><p>{{ t('modelRoutingHelpOptionalText') }}</p>
          <h4>{{ t('modelRoutingHelpExampleTitle') }}</h4><p>{{ t('modelRoutingHelpExampleText') }}</p>
        </HelpButton>
        <NButton class="model-routing-save" type="success" :loading="saving" :disabled="!supported" @click="emit('save')">
          <template #icon><NIcon :component="CheckmarkCircleOutline" /></template>{{ t('modelRoutingSave') }}
        </NButton>
      </div>
    </header>

    <div v-if="!supported" class="provider-backend-warning compact">
      <NIcon :component="AlertCircleOutline" /><div><strong>{{ t('providerBackendOutdatedTitle') }}</strong><p>{{ t('providerBackendOutdated') }}</p></div>
    </div>
    <div v-else-if="!providers.length" class="model-routing-empty">
      <NIcon :component="ServerOutline" /><div><strong>{{ t('modelRoutingNoProviders') }}</strong><p>{{ t('modelRoutingNoProvidersHint') }}</p></div>
      <NButton @click="emit('open-providers')">{{ t('providerAdd') }}</NButton>
    </div>

    <div v-if="supported" class="model-routing-grid" :class="{ 'is-saving': saving }">
      <article class="model-role-card model-role-card-main">
        <header><NIcon :component="SparklesOutline" /><div><h4>{{ t('modelRoleMain') }}</h4><p>{{ t('modelRoleMainHint') }}</p></div></header>
        <label><span>{{ t('providerName') }}</span><select :value="store.config.llm_provider_ref || ''" @change="setRoleProvider('llm_provider_ref', 'model', eventValue($event), 'chat')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label>
        <label><span>{{ t('model') }}</span><select :value="store.config.model || ''" :disabled="!store.config.llm_provider_ref" @change="setString('model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config.llm_provider_ref || ''), 'chat')" :key="model" :value="model">{{ model }}</option></select></label>
        <div class="model-fallback-grid">
          <section v-for="slot in [1, 2]" :key="slot" class="model-fallback-slot">
            <header><strong>{{ t(slot === 1 ? 'fallbackSlot1' : 'fallbackSlot2') }}</strong><NSwitch :value="!!store.config[slot === 1 ? 'fallback1_enabled' : 'fallback2_enabled']" :disabled="saving" @update:value="emit('toggle-and-save', slot === 1 ? 'fallback1_enabled' : 'fallback2_enabled', $event)" /></header>
            <label><span>{{ t('providerName') }}</span><select :value="store.config[slot === 1 ? 'fallback1_provider_ref' : 'fallback2_provider_ref'] || ''" :disabled="!store.config[slot === 1 ? 'fallback1_enabled' : 'fallback2_enabled']" @change="setRoleProvider(slot === 1 ? 'fallback1_provider_ref' : 'fallback2_provider_ref', slot === 1 ? 'fallback1_model' : 'fallback2_model', eventValue($event), 'chat')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label>
            <label><span>{{ t('model') }}</span><select :value="store.config[slot === 1 ? 'fallback1_model' : 'fallback2_model'] || ''" :disabled="!store.config[slot === 1 ? 'fallback1_enabled' : 'fallback2_enabled'] || !store.config[slot === 1 ? 'fallback1_provider_ref' : 'fallback2_provider_ref']" @change="setString(slot === 1 ? 'fallback1_model' : 'fallback2_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config[slot === 1 ? 'fallback1_provider_ref' : 'fallback2_provider_ref'] || ''), 'chat')" :key="model" :value="model">{{ model }}</option></select></label>
          </section>
        </div>
      </article>

      <div class="model-capability-grid">
        <div class="model-capability-column">
          <article class="model-role-card model-role-card-embedding">
            <header><NIcon :component="CubeOutline" /><div><h4>{{ t('modelRoleEmbedding') }}</h4><p>{{ t('modelRoleEmbeddingHint') }}</p></div><HelpButton :title="t('embeddingHelpTitle')"><h4>{{ t('embeddingHelpWhatTitle') }}</h4><p>{{ t('embeddingHelpWhatText') }}</p><h4>{{ t('embeddingHelpChooseTitle') }}</h4><p>{{ t('embeddingHelpChooseBefore') }} <code>bge-m3</code>{{ t('embeddingHelpChooseAfter') }} <code>text-embedding-3-small</code>, <code>gte-large</code>, <code>nomic-embed-text</code>{{ t('embeddingHelpChooseSuffix') }}</p><h4>{{ t('embeddingHelpConfigTitle') }}</h4><p>{{ t('embeddingHelpCentralized') }}</p><h4>{{ t('test') }}</h4><p>{{ t('embeddingHelpTest') }}</p></HelpButton></header>
            <label class="model-role-enabled"><span>{{ t('vectorMemory') }}</span><NSwitch :value="!!store.config.embedding_enabled" :disabled="saving" @update:value="emit('toggle-and-save', 'embedding_enabled', $event)" /></label>
            <label><span>{{ t('providerName') }}</span><select :value="store.config.embedding_provider_ref || ''" :disabled="!store.config.embedding_enabled" @change="setRoleProvider('embedding_provider_ref', 'embedding_model', eventValue($event), 'embedding')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label>
            <label><span>{{ t('model') }}</span><select :value="store.config.embedding_model || ''" :disabled="!store.config.embedding_enabled || !store.config.embedding_provider_ref" @change="setString('embedding_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config.embedding_provider_ref || ''), 'embedding')" :key="model" :value="model">{{ model }}</option></select></label>
            <label><span>{{ t('maxInput') }}</span><NInputNumber :value="store.config.embedding_max_input ?? 0" :min="0" :disabled="!store.config.embedding_enabled" @update:value="setNumber('embedding_max_input', $event)" /></label>
            <p class="model-role-field-hint">{{ t('maxInputHint') }}</p><div class="model-role-actions"><NButton :loading="embeddingTesting" :disabled="!store.config.embedding_enabled" @click="emit('test-embedding')">{{ t('testEmbeddingConnection') }}</NButton></div>
            <TestResultCard v-if="embeddingResult" :result="embeddingResult" kind="embedding" />
          </article>
          <article class="model-role-card">
            <header><NIcon :component="VolumeHighOutline" /><div><h4>{{ t('modelRoleTts') }}</h4><p>{{ t('modelRoleTtsHint') }}</p></div></header>
            <label><span>{{ t('modelRoutingMode') }}</span><select :value="store.config.tts_provider || 'browser'" @change="setTtsProvider(eventValue($event))"><option value="browser">{{ t('ttsProviderBrowser') }}</option><option value="edge-tts">{{ t('ttsProviderEdge') }}</option><option value="openai-compatible">{{ t('ttsProviderOpenAI') }}</option><option value="gpt-sovits">GPT-SoVITS</option></select></label>
            <template v-if="ttsProvider === 'openai-compatible' || ttsProvider === 'gpt-sovits'"><label><span>{{ t('providerName') }}</span><select :value="store.config.tts_provider_ref || ''" @change="setRoleProvider('tts_provider_ref', 'tts_model', eventValue($event), 'tts')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label><label><span>{{ t('model') }}</span><select :value="store.config.tts_model || ''" :disabled="!store.config.tts_provider_ref" @change="setString('tts_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config.tts_provider_ref || ''), 'tts')" :key="model" :value="model">{{ model }}</option></select></label></template>
          </article>
        </div>
        <div class="model-capability-column">
          <article class="model-role-card">
            <header><NIcon :component="ImageOutline" /><div><h4>{{ t('modelRoleImagegen') }}</h4><p>{{ t('modelRoleImagegenHint') }}</p></div></header>
            <label class="model-role-enabled"><span>{{ t('enabled') }}</span><NSwitch :value="!!store.config.imagegen_enabled" :disabled="saving" @update:value="emit('toggle-and-save', 'imagegen_enabled', $event)" /></label>
            <label class="model-role-enabled"><span>{{ t('imagegenAutoScene') }}</span><NSwitch :value="!!store.config.imagegen_auto_scene" :disabled="saving || !store.config.imagegen_enabled" @update:value="emit('toggle-and-save', 'imagegen_auto_scene', $event)" /></label>
            <label><span>{{ t('providerName') }}</span><select :value="store.config.imagegen_provider_ref || ''" :disabled="!store.config.imagegen_enabled" @change="setRoleProvider('imagegen_provider_ref', 'imagegen_model', eventValue($event), 'image')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in openAiProviders" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label>
            <label><span>{{ t('model') }}</span><select :value="store.config.imagegen_model || ''" :disabled="!store.config.imagegen_enabled || !store.config.imagegen_provider_ref" @change="setString('imagegen_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config.imagegen_provider_ref || ''), 'image')" :key="model" :value="model">{{ model }}</option></select></label>
            <label><span>{{ t('imagegenStylePrefix') }}</span><NInput :value="String(store.config.imagegen_style_prefix || '')" :disabled="!store.config.imagegen_enabled" @update:value="setString('imagegen_style_prefix', $event)" /></label>
            <label><span>{{ t('imagegenSquareSize') }}</span><NInput :value="String(store.config.imagegen_square_size || '1024x1024')" :disabled="!store.config.imagegen_enabled" @update:value="setString('imagegen_square_size', $event)" /></label>
            <label><span>{{ t('imagegenLandscapeSize') }}</span><NInput :value="String(store.config.imagegen_landscape_size || '1792x1024')" :disabled="!store.config.imagegen_enabled" @update:value="setString('imagegen_landscape_size', $event)" /></label>
            <label><span>{{ t('imagegenTimeout') }}</span><NInputNumber :value="Number(store.config.imagegen_timeout_seconds || 120)" :min="5" :max="300" :disabled="!store.config.imagegen_enabled" @update:value="setNumber('imagegen_timeout_seconds', $event)" /></label>
          </article>
          <article class="model-role-card">
            <header><NIcon :component="MicOutline" /><div><h4>{{ t('modelRoleAsr') }}</h4><p>{{ t('modelRoleAsrHint') }}</p></div></header>
            <label><span>{{ t('modelRoutingMode') }}</span><select :value="store.config.asr_provider || 'disabled'" @change="setAsrProvider(eventValue($event))"><option value="disabled">{{ t('asrProviderDisabled') }}</option><option value="openai-compatible">{{ t('asrProviderOpenAI') }}</option></select></label>
            <template v-if="asrProvider === 'openai-compatible'"><label><span>{{ t('providerName') }}</span><select :value="store.config.asr_provider_ref || ''" @change="setRoleProvider('asr_provider_ref', 'asr_model', eventValue($event), 'asr')"><option value="">{{ t('modelRoutingChooseProvider') }}</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name || provider.id }}</option></select></label><label><span>{{ t('model') }}</span><select :value="store.config.asr_model || ''" :disabled="!store.config.asr_provider_ref" @change="setString('asr_model', eventValue($event))"><option value="">{{ t('modelRoutingChooseModel') }}</option><option v-for="model in savedModels(String(store.config.asr_provider_ref || ''), 'asr')" :key="model" :value="model">{{ model }}</option></select></label></template>
          </article>
        </div>
      </div>
    </div>
  </div>
</template>
