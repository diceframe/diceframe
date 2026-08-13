<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { speechApi } from '@/api/speech'
import type { TtsPersonalVoiceProfile, TtsPersonalVoiceProfileInput } from '@/api/types'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { errorMessage } from '@/api/client'

const props = defineProps<{ provider: string }>()
const emit = defineEmits<{ changed: [] }>()
const { t, locale } = useLocale()
const toast = useToast()
const { confirm } = useConfirm()

const profiles = ref<TtsPersonalVoiceProfile[]>([])
const loading = ref(false)
const saving = ref(false)
const testingId = ref('')
const editingId = ref('')
const formOpen = ref(false)
const referenceMode = ref<'upload' | 'server'>('upload')
const referenceFile = ref<File | null>(null)
const draft = ref<TtsPersonalVoiceProfileInput>(emptyDraft())

const currentProfiles = computed(() => profiles.value.filter(profile => profile.engine === props.provider))

function emptyDraft(): TtsPersonalVoiceProfileInput {
  return {
    name: '',
    engine: props.provider === 'gpt-sovits' ? 'gpt-sovits' : 'openai-compatible',
    voice_id: '',
    language: '',
    description: '',
    prompt_text: '',
    prompt_language: 'zh-CN',
    server_reference_path: '',
  }
}

function eventValue(event: Event): string {
  return (event.target as HTMLInputElement | HTMLSelectElement).value
}

async function loadProfiles() {
  loading.value = true
  try {
    profiles.value = (await speechApi.profiles()).profiles
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function startCreate() {
  editingId.value = ''
  draft.value = emptyDraft()
  referenceMode.value = 'upload'
  referenceFile.value = null
  formOpen.value = true
}

function startEdit(profile: TtsPersonalVoiceProfile) {
  editingId.value = profile.id
  draft.value = {
    name: profile.name,
    engine: profile.engine,
    voice_id: profile.voice_id || '',
    language: profile.language || '',
    description: profile.description || '',
    prompt_text: profile.prompt_text || '',
    prompt_language: profile.prompt_language || profile.language || 'zh-CN',
    server_reference_path: profile.server_reference_path || '',
  }
  referenceMode.value = profile.server_reference_path ? 'server' : 'upload'
  referenceFile.value = null
  formOpen.value = true
}

function closeForm() {
  formOpen.value = false
  editingId.value = ''
  referenceFile.value = null
}

function selectReference(event: Event) {
  const input = event.target as HTMLInputElement
  referenceFile.value = input.files?.[0] || null
}

function changeReferenceMode(value: string) {
  referenceMode.value = value === 'server' ? 'server' : 'upload'
  if (referenceMode.value === 'upload') draft.value.server_reference_path = ''
  else referenceFile.value = null
}

async function saveProfile() {
  saving.value = true
  try {
    const payload = { ...draft.value }
    if (payload.engine === 'gpt-sovits' && referenceMode.value === 'upload') {
      payload.server_reference_path = ''
    }
    await speechApi.saveProfile(payload, editingId.value, referenceFile.value)
    toast.success(t('ttsProfileSaved'))
    closeForm()
    await loadProfiles()
    emit('changed')
  } catch (error: unknown) {
    const code = errorMessage(error)
    toast.error(t(code === 'tts-reference-too-large' ? 'ttsReferenceTooLarge' : code === 'tts-reference-invalid' ? 'ttsReferenceInvalid' : 'ttsProfileSaveFailed', { error: code }))
  } finally {
    saving.value = false
  }
}

async function deleteProfile(profile: TtsPersonalVoiceProfile) {
  const accepted = await confirm({
    title: t('ttsDeleteProfileTitle'),
    content: t('ttsDeleteProfileConfirm', { name: profile.name }),
    positiveText: t('delete'),
    type: 'error',
  })
  if (!accepted) return
  try {
    await speechApi.deleteProfile(profile.id)
    await loadProfiles()
    emit('changed')
    toast.success(t('ttsProfileDeleted'))
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  }
}

async function testProfile(profile: TtsPersonalVoiceProfile) {
  testingId.value = profile.id
  try {
    const blob = await speechApi.test({
      text: t('ttsTestText'),
      voice: profile.id,
      language: locale.value === 'ja' ? 'ja-JP' : locale.value === 'en' ? 'en-US' : 'zh-CN',
      speed: 1,
    })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    audio.onended = () => URL.revokeObjectURL(url)
    audio.onerror = () => URL.revokeObjectURL(url)
    await audio.play()
  } catch (error: unknown) {
    toast.error(errorMessage(error))
  } finally {
    testingId.value = ''
  }
}

onMounted(loadProfiles)
watch(() => props.provider, closeForm)
</script>

<template>
  <div class="tts-profile-manager">
    <div class="tts-group-heading">
      <div><strong>{{ t('ttsMyVoices') }}</strong><small>{{ t('ttsMyVoicesHint') }}</small></div>
      <NButton size="small" @click="startCreate">{{ t('ttsAddVoice') }}</NButton>
    </div>

    <p v-if="loading" class="muted">{{ t('pluginLoading') }}</p>
    <div v-else-if="currentProfiles.length" class="tts-profile-list">
      <article v-for="profile in currentProfiles" :key="profile.id" class="tts-profile-card">
        <div>
          <strong>{{ profile.name }}</strong>
          <small>{{ profile.engine === 'openai-compatible' ? profile.voice_id : (profile.has_reference_audio ? t('ttsUploadedReference') : t('ttsServerReference')) }}</small>
        </div>
        <div class="tts-profile-actions">
          <NButton size="tiny" :loading="testingId === profile.id" :disabled="provider !== profile.engine" @click="testProfile(profile)">{{ t('ttsPreview') }}</NButton>
          <NButton size="tiny" @click="startEdit(profile)">{{ t('edit') }}</NButton>
          <NButton size="tiny" type="error" ghost @click="deleteProfile(profile)">{{ t('delete') }}</NButton>
        </div>
      </article>
    </div>
    <p v-else class="muted">{{ t('ttsNoPersonalVoices') }}</p>

    <div v-if="formOpen" class="tts-profile-form">
      <div class="form-row">
        <label>{{ t('ttsVoiceName') }}</label>
        <NInput v-model:value="draft.name" :placeholder="t('ttsVoiceNamePlaceholder')" />
      </div>
      <div class="form-row">
        <label>{{ t('ttsVoiceEngine') }}</label>
        <span>{{ draft.engine === 'gpt-sovits' ? 'GPT-SoVITS' : 'OpenAI compatible' }}</span>
      </div>
      <div v-if="draft.engine === 'openai-compatible'" class="form-row">
        <label>Voice ID</label>
        <NInput v-model:value="draft.voice_id" placeholder="voice-id" />
      </div>
      <template v-else>
        <div class="form-row">
          <label>{{ t('ttsReferenceSource') }}</label>
          <select :value="referenceMode" @change="changeReferenceMode(eventValue($event))">
            <option value="upload">{{ t('ttsUploadReference') }}</option>
            <option value="server">{{ t('ttsServerPath') }}</option>
          </select>
        </div>
        <div v-if="referenceMode === 'upload'" class="form-row">
          <label>{{ t('ttsReferenceWav') }}</label>
          <input type="file" accept=".wav,audio/wav,audio/x-wav" @change="selectReference">
          <small v-if="editingId && !referenceFile" class="muted">{{ t('ttsKeepExistingReference') }}</small>
        </div>
        <div v-else class="form-row">
          <label>{{ t('ttsServerPath') }}</label>
          <NInput v-model:value="draft.server_reference_path" placeholder="/path/visible/to/gpt-sovits/reference.wav" />
        </div>
        <div class="form-row">
          <label>{{ t('ttsPromptText') }}</label>
          <NInput v-model:value="draft.prompt_text" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
        </div>
        <div class="form-row">
          <label>{{ t('ttsPromptLanguage') }}</label>
          <NInput v-model:value="draft.prompt_language" placeholder="zh-CN" />
        </div>
      </template>
      <div class="form-row">
        <label>{{ t('language') }}</label>
        <NInput v-model:value="draft.language" :placeholder="t('ttsOptional')" />
      </div>
      <div class="form-row">
        <label>{{ t('description') }}</label>
        <NInput v-model:value="draft.description" :placeholder="t('ttsOptional')" />
      </div>
      <div class="tts-profile-form-actions">
        <NButton @click="closeForm">{{ t('cancel') }}</NButton>
        <NButton type="primary" :loading="saving" @click="saveProfile">{{ t('saveAction') }}</NButton>
      </div>
    </div>
  </div>
</template>
