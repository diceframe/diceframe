<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterPortrait } from '@/api/types'
import { uploadAvatar } from '@/api/avatars'
import { builtinPortraits, resolveBuiltinPortrait } from '@/utils/portraits'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import PortraitImage from '@/components/PortraitImage.vue'

const props = defineProps<{ modelValue?: CharacterPortrait; ruleId?: string; seed?: string; name?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: CharacterPortrait | undefined] }>()
const { t } = useLocale()
const toast = useToast()
const input = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const choices = computed(() => builtinPortraits(props.ruleId))
const resolvedId = computed(() => resolveBuiltinPortrait(props.modelValue, props.ruleId, props.seed || props.name).id)

function choose(id: string) { emit('update:modelValue', { kind: 'builtin', id }) }

async function onUpload(event: Event) {
  const element = event.target as HTMLInputElement
  const file = element.files?.[0]
  if (!file) return
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    toast.error(t('avatarFormatHint'))
    element.value = ''
    return
  }
  if (file.size > 3 * 1024 * 1024) {
    toast.error(t('avatarSizeHint'))
    element.value = ''
    return
  }
  uploading.value = true
  try {
    emit('update:modelValue', await uploadAvatar(file))
    toast.success(t('avatarUploaded'))
  } catch (error: unknown) {
    toast.error(error instanceof Error ? error.message : String(error))
  } finally {
    uploading.value = false
    element.value = ''
  }
}
</script>

<template>
  <section class="portrait-picker">
    <div class="portrait-picker-head">
      <div><strong>{{ t('characterAvatar') }}</strong><small>{{ t('avatarHelp') }}</small></div>
      <PortraitImage :portrait="modelValue" :rule-id="ruleId" :seed="seed" :name="name" :size="64" />
    </div>
    <div class="portrait-options">
      <button
        v-for="choice in choices"
        :key="choice.id"
        type="button"
        class="portrait-option"
        :class="{ selected: modelValue?.kind === 'builtin' ? modelValue.id === choice.id : resolvedId === choice.id }"
        :title="t('builtinAvatarOption', { index: choice.index + 1 })"
        @click="choose(choice.id)"
      >
        <PortraitImage :portrait="{ kind: 'builtin', id: choice.id }" :rule-id="ruleId" :name="name" :size="52" />
      </button>
      <button type="button" class="portrait-upload" :disabled="uploading" @click="input?.click()">
        {{ uploading ? t('uploading') : t('uploadCustomAvatar') }}
      </button>
      <button type="button" class="ghost portrait-auto" @click="emit('update:modelValue', undefined)">{{ t('useDefaultAvatar') }}</button>
    </div>
    <input ref="input" hidden type="file" accept="image/png,image/jpeg,image/webp" @change="onUpload">
    <small class="form-hint">{{ t('avatarUploadHint') }}</small>
  </section>
</template>
