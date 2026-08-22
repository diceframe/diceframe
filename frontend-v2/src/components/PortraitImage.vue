<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { CharacterPortrait } from '@/api/types'
import { uploadedAvatarUrl } from '@/api/avatars'
import { generatedImageUrl } from '@/api/generatedImages'
import { builtinPortraits, initials, resolveBuiltinPortrait } from '@/utils/portraits'

const props = withDefaults(defineProps<{
  portrait?: CharacterPortrait | null
  ruleId?: string
  seed?: string
  name?: string
  size?: number
}>(), { size: 48 })

const uploadUrl = ref('')
const uploadFailed = ref(false)
let ownsUploadUrl = false
let loadVersion = 0

function clearUploadUrl() {
  if (ownsUploadUrl && uploadUrl.value.startsWith('blob:')) URL.revokeObjectURL(uploadUrl.value)
  uploadUrl.value = ''
  ownsUploadUrl = false
}
// 内置头像只有显式选择且 id 有效才显示；未选择时不按名字/规则自动分配。
const hasValidBuiltin = computed(() => {
  const portrait = props.portrait
  if (!portrait || portrait.kind !== 'builtin') return false
  const [storedRule, rawIndex] = String(portrait.id || '').split(':')
  const options = builtinPortraits(storedRule)
  const index = Number(rawIndex)
  return Number.isInteger(index) && index >= 0 && index < options.length
})
const builtin = computed(() => resolveBuiltinPortrait(props.portrait, props.ruleId, props.seed || props.name))
const isUpload = computed(() => props.portrait?.kind === 'upload' && !!props.portrait.asset_id && !uploadFailed.value)
const isGenerated = computed(() => props.portrait?.kind === 'generated' && !!props.portrait.asset_id && !uploadFailed.value)
const pluginUrl = computed(() => {
  const portrait = props.portrait
  if (portrait?.kind !== 'plugin' || !portrait.plugin_id || !portrait.path || uploadFailed.value) return ''
  const path = portrait.path.split('/').map(encodeURIComponent).join('/')
  return `/api/plugins/assets/${encodeURIComponent(portrait.plugin_id)}/${path}`
})
const hasImage = computed(() => isUpload.value || isGenerated.value || Boolean(pluginUrl.value))
const boxStyle = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }))
const builtinStyle = computed(() => ({
  width: '100%',
  height: '100%',
  backgroundImage: `url("${builtin.value.image}")`,
  backgroundPosition: builtin.value.position,
  backgroundSize: 'cover',
}))

watch(
  () => ['upload', 'generated'].includes(String(props.portrait?.kind || '')) ? `${props.portrait?.kind}:${props.portrait?.asset_id || ''}` : '',
  async (key) => {
    const version = ++loadVersion
    clearUploadUrl()
    uploadFailed.value = false
    const [kind, assetId] = key.split(':', 2)
    if (!assetId) return
    let nextUrl = ''
    try {
      nextUrl = kind === 'generated' ? await generatedImageUrl(assetId) : await uploadedAvatarUrl(assetId)
      if (version !== loadVersion) {
        if (kind === 'generated' && nextUrl.startsWith('blob:')) URL.revokeObjectURL(nextUrl)
        return
      }
      uploadUrl.value = nextUrl
      ownsUploadUrl = kind === 'generated'
    } catch {
      if (kind === 'generated' && nextUrl.startsWith('blob:')) URL.revokeObjectURL(nextUrl)
      if (version === loadVersion) uploadFailed.value = true
    }
  },
  { immediate: true },
)
watch(
  () => props.portrait?.kind === 'plugin' ? `${props.portrait.plugin_id || ''}:${props.portrait.path || ''}` : '',
  () => { uploadFailed.value = false },
)

onBeforeUnmount(() => {
  loadVersion += 1
  clearUploadUrl()
})
</script>

<template>
  <span class="portrait-image" :class="{ 'portrait-empty': !hasValidBuiltin && !hasImage }" :style="boxStyle" :title="name" role="img" :aria-label="name || 'avatar'">
    <img v-if="(isUpload || isGenerated) && uploadUrl" :src="uploadUrl" alt="" @error="uploadFailed = true">
    <img v-else-if="pluginUrl" :src="pluginUrl" alt="" @error="uploadFailed = true">
    <span v-else-if="hasValidBuiltin" class="portrait-builtin" :style="builtinStyle"><i>{{ initials(name) }}</i></span>
    <span v-else class="portrait-empty-text">{{ initials(name) }}</span>
  </span>
</template>
