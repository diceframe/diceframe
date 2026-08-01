<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CharacterPortrait } from '@/api/types'
import { uploadedAvatarUrl } from '@/api/avatars'
import { initials, resolveBuiltinPortrait } from '@/utils/portraits'

const props = withDefaults(defineProps<{
  portrait?: CharacterPortrait
  ruleId?: string
  seed?: string
  name?: string
  size?: number
}>(), { size: 48 })

const uploadUrl = ref('')
const uploadFailed = ref(false)
const builtin = computed(() => resolveBuiltinPortrait(props.portrait, props.ruleId, props.seed || props.name))
const isUpload = computed(() => props.portrait?.kind === 'upload' && !!props.portrait.asset_id && !uploadFailed.value)
const boxStyle = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }))
const builtinStyle = computed(() => ({
  ...boxStyle.value,
  backgroundImage: `url("${builtin.value.image}")`,
  backgroundPosition: builtin.value.position,
}))

watch(
  () => props.portrait?.kind === 'upload' ? props.portrait.asset_id : '',
  async (assetId) => {
    uploadUrl.value = ''
    uploadFailed.value = false
    if (!assetId) return
    try { uploadUrl.value = await uploadedAvatarUrl(assetId) }
    catch { uploadFailed.value = true }
  },
  { immediate: true },
)
</script>

<template>
  <span class="portrait-image" :style="boxStyle" :title="name" role="img" :aria-label="name || 'avatar'">
    <img v-if="isUpload && uploadUrl" :src="uploadUrl" alt="" @error="uploadFailed = true">
    <span v-else class="portrait-builtin" :style="builtinStyle"><i>{{ initials(name) }}</i></span>
  </span>
</template>
