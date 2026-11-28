<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useLocale } from '@/composables/useLocale'
import BadgeInspectModal from './BadgeInspectModal.vue'
import { buildBadgeFrontSvg, resolveLocalizedText } from './badgeArtwork'
import { useHonorsDisplay } from './useHonorsDisplay'

const props = withDefaults(defineProps<{
  size?: number
  clickable?: boolean
  instanceId?: string
}>(), {
  size: 20,
  clickable: false,
  instanceId: 'pin',
})

const { locale } = useLocale()
const { activeCredential, equippedPlayBadge, ensureHonorsLoaded } = useHonorsDisplay()
const inspectOpen = ref(false)

onMounted(() => {
  void ensureHonorsLoaded()
})

const badgeName = computed(() =>
  equippedPlayBadge.value
    ? resolveLocalizedText(equippedPlayBadge.value.name, locale.value, equippedPlayBadge.value.id)
    : '',
)

const tooltip = computed(() => {
  if (!equippedPlayBadge.value) return ''
  const holder = activeCredential.value
  if (!holder) return badgeName.value
  return `${badgeName.value} · ${holder.name} (NO. ${holder.id})`
})

const svgHtml = computed(() =>
  equippedPlayBadge.value && !equippedPlayBadge.value.image_url
    ? buildBadgeFrontSvg(
        equippedPlayBadge.value,
        locale.value,
        `play-${props.instanceId}-${equippedPlayBadge.value.id}`,
        true,
      )
    : '',
)

const boxStyle = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
}))
</script>

<template>
  <template v-if="equippedPlayBadge">
    <button
      v-if="clickable"
      type="button"
      class="honor-badge-pin is-clickable"
      :style="boxStyle"
      :title="tooltip"
      :aria-label="tooltip"
      @click.stop="inspectOpen = true"
    >
      <img
        v-if="equippedPlayBadge.image_url"
        :src="equippedPlayBadge.image_url"
        :alt="badgeName"
        class="pin-img"
      >
      <span v-else class="pin-svg" v-html="svgHtml" />
    </button>
    <span
      v-else
      class="honor-badge-pin"
      :style="boxStyle"
      :title="tooltip"
      aria-hidden="true"
    >
      <img
        v-if="equippedPlayBadge.image_url"
        :src="equippedPlayBadge.image_url"
        :alt="badgeName"
        class="pin-img"
      >
      <span v-else class="pin-svg" v-html="svgHtml" />
    </span>

    <BadgeInspectModal
      v-if="clickable"
      v-model:show="inspectOpen"
      :badge="equippedPlayBadge"
      :active="activeCredential"
      :owned="true"
      :is-featured="true"
    />
  </template>
</template>

<style scoped>
.honor-badge-pin {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  vertical-align: middle;
  flex-shrink: 0;
  filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.45));
}

.honor-badge-pin.is-clickable {
  cursor: pointer;
  transition: transform 0.16s ease, filter 0.16s ease;
}

.honor-badge-pin.is-clickable:hover {
  transform: scale(1.14);
  filter: drop-shadow(0 3px 8px rgba(216, 173, 82, 0.55));
}

.pin-img,
.pin-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.pin-img {
  object-fit: contain;
}

.pin-svg :deep(.df-badge-svg) {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
