<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterPortrait } from '@/api/types'
import { uploadAvatar, listUserAvatars, deleteUserAvatar, type UserAvatar } from '@/api/avatars'
import { builtinPortraits, builtinRule, resolveBuiltinPortrait } from '@/utils/portraits'
import type { MessageKey } from '@/i18n'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import PortraitImage from '@/components/PortraitImage.vue'
import Modal from '@/components/ui/Modal.vue'

const props = defineProps<{ modelValue?: CharacterPortrait | null; ruleId?: string; seed?: string; name?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: CharacterPortrait | null] }>()
const { t } = useLocale()
const toast = useToast()
const { confirm } = useConfirm()
const input = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const allOpen = ref(false)
const userOpen = ref(false)
const userAvatars = ref<UserAvatar[]>([])
const userLoading = ref(false)
const choices = computed(() => builtinPortraits(props.ruleId))
const resolvedId = computed(() => {
  // 未显式选择头像时不高亮任何选项（也不自动分配兜底）。
  if (!props.modelValue) return ''
  return resolveBuiltinPortrait(props.modelValue, props.ruleId, props.seed || props.name).id
})

const RULE_LABEL_KEYS: Record<string, MessageKey> = {
  dnd5e: 'ruleNameDnd5e',
  freeform_coc: 'ruleNameCoc',
  freeform_cyberpunk: 'ruleNameCyberpunk',
  freeform_fantasy: 'ruleNameFantasy',
  freeform_wuxia: 'ruleNameWuxia',
  tavern_free: 'ruleNameTavern',
}
const ALL_RULE_IDS = ['dnd5e', 'freeform_coc', 'freeform_cyberpunk', 'freeform_fantasy', 'freeform_wuxia', 'tavern_free']

function ruleLabel(ruleId: string): string {
  return t(RULE_LABEL_KEYS[ruleId] || 'ruleNameFantasy')
}

const allGroups = computed(() => {
  const current = builtinRule(props.ruleId)
  const groups = ALL_RULE_IDS.map(ruleId => ({
    ruleId,
    label: ruleLabel(ruleId),
    current: ruleId === current,
    portraits: builtinPortraits(ruleId),
  }))
  return [...groups].sort((a, b) => Number(b.current) - Number(a.current))
})

function choose(id: string) { emit('update:modelValue', { kind: 'builtin', id }) }

function chooseAll(ruleId: string, index: number) {
  emit('update:modelValue', { kind: 'builtin', id: `${ruleId}:${index}` })
  allOpen.value = false
}

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

async function openUserAvatars() {
  userOpen.value = true
  userLoading.value = true
  try {
    const result = await listUserAvatars()
    userAvatars.value = result.avatars || []
  } catch (error: unknown) {
    userAvatars.value = []
    toast.error(error instanceof Error ? error.message : String(error))
  } finally {
    userLoading.value = false
  }
}

function chooseUser(assetId: string) {
  emit('update:modelValue', { kind: 'upload', asset_id: assetId })
  userOpen.value = false
}

async function removeUserAvatar(assetId: string) {
  const ok = await confirm({
    title: t('deleteAvatarTitle'),
    content: t('deleteAvatarConfirm'),
    positiveText: t('deleteAvatarAction'),
    type: 'warning',
  })
  if (!ok) return
  try {
    await deleteUserAvatar(assetId)
    userAvatars.value = userAvatars.value.filter(a => a.asset_id !== assetId)
    toast.success(t('avatarDeleted'))
  } catch (error: unknown) {
    toast.error(error instanceof Error ? error.message : String(error))
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
      <button type="button" class="portrait-all" @click="allOpen = true">{{ t('allAvatars') }}</button>
      <button type="button" class="portrait-all" @click="openUserAvatars">{{ t('userAvatars') }}</button>
      <button type="button" class="ghost portrait-auto" @click="emit('update:modelValue', null)">{{ t('useDefaultAvatar') }}</button>
    </div>
    <input ref="input" hidden type="file" accept="image/png,image/jpeg,image/webp" @change="onUpload">
    <small class="form-hint">{{ t('avatarUploadHint') }}</small>

    <Modal v-if="allOpen" :title="t('allAvatars')" @close="allOpen = false">
      <div v-for="group in allGroups" :key="group.ruleId" class="portrait-all-group" :class="{ current: group.current }">
        <div class="portrait-all-head">
          <strong>{{ group.label }}</strong>
          <small v-if="group.current" class="muted">{{ t('currentRule') }}</small>
        </div>
        <div class="portrait-options">
          <button
            v-for="p in group.portraits"
            :key="p.id"
            type="button"
            class="portrait-option"
            :class="{ selected: modelValue?.kind === 'builtin' ? modelValue.id === p.id : resolvedId === p.id }"
            :title="group.label + ' · ' + t('builtinAvatarOption', { index: p.index + 1 })"
            @click="chooseAll(group.ruleId, p.index)"
          >
            <PortraitImage :portrait="{ kind: 'builtin', id: p.id }" :rule-id="group.ruleId" :name="name" :size="52" />
          </button>
        </div>
      </div>
    </Modal>

    <Modal v-if="userOpen" :title="t('userAvatars')" @close="userOpen = false">
      <p v-if="userLoading" class="muted">{{ t('userAvatarsLoading') }}</p>
      <p v-else-if="!userAvatars.length" class="muted">{{ t('userAvatarsEmpty') }}</p>
      <div v-else class="portrait-options user-avatar-grid">
        <div v-for="a in userAvatars" :key="a.asset_id" class="user-avatar-item">
          <button
            type="button"
            class="portrait-option"
            :class="{ selected: modelValue?.kind === 'upload' && modelValue.asset_id === a.asset_id }"
            :title="t('clickToChangeAvatar')"
            @click="chooseUser(a.asset_id)"
          >
            <PortraitImage :portrait="{ kind: 'upload', asset_id: a.asset_id }" :size="52" />
          </button>
          <button
            type="button"
            class="user-avatar-remove"
            :title="t('deleteAvatarAction')"
            :aria-label="t('deleteAvatarAction')"
            @click="removeUserAvatar(a.asset_id)"
          >×</button>
        </div>
      </div>
    </Modal>
  </section>
</template>
