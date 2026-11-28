<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NIcon, NModal, NTag } from 'naive-ui'
import {
  CloseOutline,
  DownloadOutline,
  ExpandOutline,
  RefreshOutline,
  StarOutline,
  Star,
} from '@vicons/ionicons5'
import type { HonorsActiveCredential, HonorsBadgeDefinition } from '@/api/honors'
import { useLocale } from '@/composables/useLocale'
import { useToast } from '@/composables/useToast'
import {
  buildBadgeBackSvg,
  buildBadgeFrontSvg,
  buildBadgeMaskDataUrl,
  exportHonorCardPng,
  resolveLocalizedText,
} from './badgeArtwork'

const props = defineProps<{
  show: boolean
  badge: HonorsBadgeDefinition | null
  active: HonorsActiveCredential | null
  owned: boolean
  isFeatured: boolean
  featuredBusy?: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  'set-featured': [badgeId: string]
}>()

const { t, locale } = useLocale()
const toast = useToast()

const isFlipped = ref(false)
const isFlipping = ref(false)
const exportBusy = ref(false)
const tiltX = ref(0)
const tiltY = ref(0)
const lightX = ref(32)
const lightY = ref(26)

// Fullscreen magnified inspection loupe state
const zoomOpen = ref(false)
const zoomScale = ref<1 | 1.5 | 2>(1)
const panX = ref(0)
const panY = ref(0)
const dragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const dragMoved = ref(false)

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape' && zoomOpen.value) {
    e.stopPropagation()
    closeZoom()
  }
}

watch(() => props.show, (open) => {
  if (open) {
    isFlipped.value = false
    zoomOpen.value = false
    zoomScale.value = 1
    panX.value = 0
    panY.value = 0
    tiltX.value = 0
    tiltY.value = 0
    lightX.value = 32
    lightY.value = 26
  } else {
    zoomOpen.value = false
  }
})

watch(zoomOpen, (open) => {
  if (typeof window === 'undefined') return
  if (open) window.addEventListener('keydown', onKeyDown, true)
  else window.removeEventListener('keydown', onKeyDown, true)
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', onKeyDown, true)
  }
})

const uid = computed(() => `inspect-${props.badge?.id || 'none'}`)
const zoomUid = computed(() => `zoom-${props.badge?.id || 'none'}`)

const title = computed(() =>
  props.badge ? resolveLocalizedText(props.badge.name, locale.value, props.badge.id) : '',
)
const description = computed(() =>
  props.badge ? resolveLocalizedText(props.badge.description, locale.value, '') : '',
)
const craft = computed(() =>
  props.badge ? resolveLocalizedText(props.badge.craft, locale.value, '') : '',
)
const obtainHint = computed(() =>
  props.badge ? resolveLocalizedText(props.badge.obtain_hint, locale.value, '') : '',
)

const issuedDate = computed(() => {
  const ts = props.active?.iat
  if (!ts) return '2026'
  const date = new Date(ts * 1000)
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  return `${yyyy}.${mm}`
})

const holderName = computed(() =>
  props.owned && props.active ? props.active.name : t('honorsPreviewHolderName'),
)

const holderId = computed(() =>
  props.owned && props.active ? `NO. ${props.active.id}` : 'NO. DF-0000',
)

const frontSvg = computed(() =>
  props.badge ? buildBadgeFrontSvg(props.badge, locale.value, uid.value, false) : '',
)

const backSvg = computed(() =>
  props.badge
    ? buildBadgeBackSvg(props.badge, {
        holderName: holderName.value,
        holderId: holderId.value,
        issuedLabel: props.owned ? `ISSUED · ${issuedDate.value}` : 'PREVIEW · SAMPLE',
        awardedPrefix: t('honorsAwardedTo'),
        uid: uid.value,
      })
    : '',
)

const zoomFrontSvg = computed(() =>
  props.badge ? buildBadgeFrontSvg(props.badge, locale.value, zoomUid.value, false) : '',
)

const zoomBackSvg = computed(() =>
  props.badge
    ? buildBadgeBackSvg(props.badge, {
        holderName: holderName.value,
        holderId: holderId.value,
        issuedLabel: props.owned ? `ISSUED · ${issuedDate.value}` : 'PREVIEW · SAMPLE',
        awardedPrefix: t('honorsAwardedTo'),
        uid: zoomUid.value,
      })
    : '',
)

const maskImage = computed(() =>
  props.badge ? buildBadgeMaskDataUrl(props.badge) : 'none',
)

const ornamentStyle = computed(() => {
  const baseY = isFlipped.value ? 180 : 0
  return {
    transform: `rotateX(${tiltX.value}deg) rotateY(${baseY + tiltY.value}deg)`,
  }
})

const zoomStageStyle = computed(() => ({
  transform: `translate3d(${panX.value}px, ${panY.value}px, 0) scale(${zoomScale.value})`,
}))

const sheenStyle = computed(() => ({
  WebkitMaskImage: maskImage.value,
  maskImage: maskImage.value,
  WebkitMaskSize: '100% 100%',
  maskSize: '100% 100%',
  WebkitMaskRepeat: 'no-repeat',
  maskRepeat: 'no-repeat',
  background: `radial-gradient(circle at ${lightX.value}% ${lightY.value}%, rgba(255,250,230,0.82) 0%, rgba(255,240,200,0.22) 32%, rgba(0,0,0,0.32) 78%)`,
}))

const specularStyle = computed(() => {
  const angle = Math.atan2(lightY.value - 50, lightX.value - 50) * (180 / Math.PI) + 90
  return {
    WebkitMaskImage: maskImage.value,
    maskImage: maskImage.value,
    WebkitMaskSize: '100% 100%',
    maskSize: '100% 100%',
    WebkitMaskRepeat: 'no-repeat',
    maskRepeat: 'no-repeat',
    background: `linear-gradient(${angle}deg, transparent 22%, rgba(255,252,240,0.72) 48%, rgba(255,252,240,0.14) 56%, transparent 78%)`,
  }
})

function onPointerMove(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null
  if (!target) return
  const rect = target.getBoundingClientRect()
  const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
  tiltY.value = (nx - 0.5) * 30
  tiltX.value = -(ny - 0.5) * 30
  lightX.value = Number((nx * 100).toFixed(1))
  lightY.value = Number((ny * 100).toFixed(1))
}

function resetLighting() {
  tiltX.value = 0
  tiltY.value = 0
  lightX.value = 32
  lightY.value = 26
}

function toggleFlip() {
  isFlipped.value = !isFlipped.value
  isFlipping.value = true
  resetLighting()
  window.setTimeout(() => {
    isFlipping.value = false
  }, 660)
}

function openZoom() {
  zoomScale.value = 1
  panX.value = 0
  panY.value = 0
  zoomOpen.value = true
}

function closeZoom() {
  zoomOpen.value = false
  dragging.value = false
}

function setZoomScale(next: 1 | 1.5 | 2) {
  zoomScale.value = next
  if (next === 1) {
    panX.value = 0
    panY.value = 0
  }
}

function onZoomPointerDown(e: PointerEvent) {
  dragMoved.value = false
  if (zoomScale.value <= 1) return
  dragging.value = true
  dragStartX.value = e.clientX - panX.value
  dragStartY.value = e.clientY - panY.value
}

function onZoomPointerMove(e: PointerEvent) {
  onPointerMove(e)
  if (!dragging.value || zoomScale.value <= 1) return
  const nextX = e.clientX - dragStartX.value
  const nextY = e.clientY - dragStartY.value
  if (Math.abs(nextX - panX.value) > 2 || Math.abs(nextY - panY.value) > 2) {
    dragMoved.value = true
  }
  const bound = zoomScale.value === 2 ? 190 : 110
  panX.value = Math.max(-bound, Math.min(bound, nextX))
  panY.value = Math.max(-bound, Math.min(bound, nextY))
}

function onZoomPointerUp() {
  dragging.value = false
}

function onZoomOrnamentClick() {
  if (dragMoved.value) {
    dragMoved.value = false
    return
  }
  toggleFlip()
}

async function downloadCard() {
  if (!props.badge || !props.active || !props.owned) return
  exportBusy.value = true
  try {
    const blob = await exportHonorCardPng({
      badge: props.badge,
      locale: locale.value,
      holderName: props.active.name,
      holderId: props.active.id,
      issuedDate: issuedDate.value,
      awardedPrefix: t('honorsAwardedTo'),
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `diceframe-honor-${props.badge.id}-${props.active.id}.png`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    toast.success(t('honorsExportDone'))
  } catch {
    toast.error(t('honorsExportFailed'))
  } finally {
    exportBusy.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    class="honors-inspect-modal"
    style="max-width: 680px; width: 94vw;"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="badge" class="inspect-layout">
      <div
        class="inspect-stage"
        role="button"
        tabindex="0"
        :aria-label="t('honorsFlipHint')"
        @pointermove="onPointerMove"
        @pointerleave="resetLighting"
        @click="toggleFlip"
        @keydown.enter.prevent="toggleFlip"
        @keydown.space.prevent="toggleFlip"
      >
        <button
          type="button"
          class="stage-zoom-btn"
          :title="t('honorsZoomIn')"
          :aria-label="t('honorsZoomIn')"
          @click.stop="openZoom"
        >
          <NIcon :component="ExpandOutline" :size="16" />
        </button>

        <div
          class="ornament-wrapper"
          :class="{ 'is-flipping': isFlipping, 'is-unowned': !owned }"
          :style="ornamentStyle"
        >
          <div class="ornament-face ornament-front">
            <img
              v-if="badge.image_url"
              :src="badge.image_url"
              :alt="title"
              class="ornament-img"
              draggable="false"
            >
            <div v-else class="ornament-svg" v-html="frontSvg" />
            <div class="ornament-sheen" :style="sheenStyle" />
            <div class="ornament-specular" :style="specularStyle" />
          </div>

          <div class="ornament-face ornament-back">
            <div class="ornament-svg" v-html="backSvg" />
            <div class="ornament-sheen" :style="sheenStyle" />
            <div class="ornament-specular" :style="specularStyle" />
          </div>
        </div>

        <p class="stage-hint">
          {{ isFlipped ? t('honorsStageBackHint') : t('honorsStageFrontHint') }}
        </p>
      </div>

      <div class="inspect-details">
        <div class="inspect-tags">
          <NTag :type="owned ? 'success' : 'default'" size="small" round>
            {{ owned ? t('honorsBadgeOwned') : t('honorsBadgeLocked') }}
          </NTag>
          <NTag size="small" round>
            {{ badge.wall_mode === 'counter' ? t('honorsModeCounter') : t('honorsModeListed') }}
          </NTag>
        </div>

        <p v-if="description" class="inspect-desc">{{ description }}</p>

        <dl class="inspect-meta">
          <div v-if="obtainHint" class="meta-stack">
            <dt>{{ t('honorsMetaObtain') }}</dt>
            <dd>{{ obtainHint }}</dd>
          </div>
          <div v-if="craft">
            <dt>{{ t('honorsMetaCraft') }}</dt>
            <dd>{{ craft }}</dd>
          </div>
          <div>
            <dt>{{ t('honorsMetaHolder') }}</dt>
            <dd>{{ holderName }} <span class="mono">({{ holderId }})</span></dd>
          </div>
        </dl>

        <div class="inspect-actions">
          <NButton size="small" @click="openZoom">
            <template #icon><NIcon :component="ExpandOutline" /></template>
            {{ t('honorsZoomIn') }}
          </NButton>
          <NButton size="small" @click="toggleFlip">
            <template #icon><NIcon :component="RefreshOutline" /></template>
            {{ isFlipped ? t('honorsFlipToFront') : t('honorsFlipToBack') }}
          </NButton>
          <NButton
            v-if="owned"
            size="small"
            :type="isFeatured ? 'primary' : 'default'"
            :loading="featuredBusy"
            :disabled="isFeatured"
            @click="emit('set-featured', badge.id)"
          >
            <template #icon><NIcon :component="isFeatured ? Star : StarOutline" /></template>
            {{ isFeatured ? t('honorsFeaturedCurrent') : t('honorsSetFeatured') }}
          </NButton>
          <NButton
            v-if="owned"
            size="small"
            type="primary"
            ghost
            :loading="exportBusy"
            @click="downloadCard"
          >
            <template #icon><NIcon :component="DownloadOutline" /></template>
            {{ t('honorsExportCard') }}
          </NButton>
        </div>
      </div>
    </div>
  </NModal>

  <!-- 独立暗房聚光放大检视口 -->
  <Teleport to="body">
    <div
      v-if="zoomOpen && badge"
      class="zoom-overlay"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @click.self="closeZoom"
    >
      <div class="zoom-toolbar">
        <div class="zoom-title">
          <strong>{{ title }}</strong>
          <span v-if="craft" class="zoom-craft">{{ craft }}</span>
        </div>
        <div class="zoom-controls">
          <button
            v-for="s in ([1, 1.5, 2] as const)"
            :key="s"
            type="button"
            class="zoom-pill"
            :class="{ active: zoomScale === s }"
            @click="setZoomScale(s)"
          >
            {{ s }}×
          </button>
          <button type="button" class="zoom-pill" @click="toggleFlip">
            <NIcon :component="RefreshOutline" :size="14" />
            <span>{{ isFlipped ? t('honorsFlipToFront') : t('honorsFlipToBack') }}</span>
          </button>
          <button
            type="button"
            class="zoom-close-btn"
            :aria-label="t('honorsZoomClose')"
            @click="closeZoom"
          >
            <NIcon :component="CloseOutline" :size="18" />
          </button>
        </div>
      </div>

      <div
        class="zoom-viewport"
        :class="{ 'is-pannable': zoomScale > 1, 'is-dragging': dragging }"
        @pointerdown="onZoomPointerDown"
        @pointermove="onZoomPointerMove"
        @pointerup="onZoomPointerUp"
        @pointerleave="onZoomPointerUp(); resetLighting()"
      >
        <div class="zoom-scale-shell" :style="zoomStageStyle">
          <div
            class="ornament-wrapper zoom-ornament"
            :class="{ 'is-flipping': isFlipping, 'is-unowned': !owned }"
            :style="ornamentStyle"
            @click.stop="onZoomOrnamentClick"
          >
            <div class="ornament-face ornament-front">
              <img
                v-if="badge.image_url"
                :src="badge.image_url"
                :alt="title"
                class="ornament-img"
                draggable="false"
              >
              <div v-else class="ornament-svg" v-html="zoomFrontSvg" />
              <div class="ornament-sheen" :style="sheenStyle" />
              <div class="ornament-specular" :style="specularStyle" />
            </div>

            <div class="ornament-face ornament-back">
              <div class="ornament-svg" v-html="zoomBackSvg" />
              <div class="ornament-sheen" :style="sheenStyle" />
              <div class="ornament-specular" :style="specularStyle" />
            </div>
          </div>
        </div>
      </div>

      <p class="zoom-footer-hint">
        {{ zoomScale > 1 ? t('honorsZoomDragHint') : (isFlipped ? t('honorsStageBackHint') : t('honorsStageFrontHint')) }}
      </p>
    </div>
  </Teleport>
</template>

<style scoped>
.inspect-layout {
  display: grid;
  grid-template-columns: minmax(250px, 280px) minmax(0, 1fr);
  gap: 20px;
  align-items: center;
}

.inspect-stage {
  position: relative;
  perspective: 960px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px 14px 14px;
  border-radius: var(--df-radius-lg, 12px);
  border: 1px solid var(--df-border-soft);
  background: radial-gradient(circle at 50% 42%, color-mix(in srgb, var(--df-accent) 16%, transparent), color-mix(in srgb, var(--df-canvas) 92%, black) 78%);
  cursor: pointer;
  user-select: none;
  outline: none;
}

.inspect-stage:focus-visible {
  box-shadow: 0 0 0 2px var(--df-accent);
}

.stage-zoom-btn {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-1) 82%, transparent);
  color: var(--df-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, background 0.16s ease;
  z-index: 2;
}

.stage-zoom-btn:hover {
  color: var(--df-accent-strong);
  border-color: var(--df-accent);
  background: color-mix(in srgb, var(--df-surface-2) 94%, transparent);
}

.ornament-wrapper {
  width: 224px;
  height: 224px;
  position: relative;
  transform-style: preserve-3d;
  transition: transform 0.12s ease-out;
}

.ornament-wrapper.is-flipping {
  transition: transform 0.64s cubic-bezier(0.22, 1, 0.36, 1);
}

.ornament-face {
  position: absolute;
  inset: 0;
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
  filter: drop-shadow(0 20px 28px rgba(0, 0, 0, 0.52));
}

.ornament-wrapper.is-unowned .ornament-face {
  filter: drop-shadow(0 16px 24px rgba(0, 0, 0, 0.45)) saturate(0.72);
}

.ornament-front {
  transform: rotateY(0deg);
}

.ornament-back {
  transform: rotateY(180deg);
}

.ornament-svg,
.ornament-img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
}

.ornament-svg :deep(.df-badge-svg) {
  width: 100%;
  height: 100%;
  display: block;
}

.ornament-sheen,
.ornament-specular {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.ornament-sheen {
  mix-blend-mode: soft-light;
  opacity: 0.88;
}

.ornament-specular {
  mix-blend-mode: screen;
  opacity: 0.42;
}

.stage-hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--df-text-muted);
  text-align: center;
}

.inspect-details {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.inspect-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.inspect-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--df-text-secondary);
}

.inspect-meta {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--df-radius-md, 8px);
  border: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-1) 86%, transparent);
  display: grid;
  gap: 8px;
}

.inspect-meta > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.inspect-meta > div.meta-stack {
  flex-direction: column;
  gap: 4px;
}

.inspect-meta > div.meta-stack dd {
  text-align: left;
  line-height: 1.5;
  color: var(--df-text-secondary);
}

.inspect-meta dt {
  color: var(--df-text-muted);
}

.inspect-meta dd {
  margin: 0;
  color: var(--df-text);
  text-align: right;
}

.mono {
  font-family: var(--df-font-mono);
  color: var(--df-text-muted);
}

.inspect-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
}

/* 全屏暗房放大检视口 */
.zoom-overlay {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 24px;
  background: radial-gradient(circle at 50% 48%, rgba(28, 42, 66, 0.94), rgba(6, 10, 17, 0.97) 75%);
  backdrop-filter: blur(12px);
  color: #f0eee5;
}

.zoom-toolbar {
  width: min(860px, 100%);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.zoom-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.zoom-title strong {
  font-size: 16px;
  font-family: var(--df-font-title);
  color: #f5dfaa;
}

.zoom-craft {
  font-size: 12px;
  color: #a4b1b5;
}

.zoom-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.zoom-pill,
.zoom-close-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(216, 173, 82, 0.32);
  background: rgba(16, 26, 38, 0.85);
  color: #d7deda;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.16s ease, color 0.16s ease, background 0.16s ease;
}

.zoom-pill:hover,
.zoom-close-btn:hover {
  border-color: #d8ad52;
  color: #fff3d1;
}

.zoom-pill.active {
  background: rgba(216, 173, 82, 0.22);
  border-color: #e6c273;
  color: #ffe8a8;
  font-weight: 600;
}

.zoom-close-btn {
  padding: 6px 8px;
}

.zoom-viewport {
  flex: 1;
  width: 100%;
  max-width: 860px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  perspective: 1200px;
  user-select: none;
}

.zoom-viewport.is-pannable {
  cursor: grab;
}

.zoom-viewport.is-dragging {
  cursor: grabbing;
}

.zoom-scale-shell {
  transition: transform 0.2s ease-out;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-style: preserve-3d;
}

.zoom-viewport.is-dragging .zoom-scale-shell {
  transition: none;
}

.zoom-ornament {
  width: min(400px, 76vw, 62vh);
  height: min(400px, 76vw, 62vh);
  cursor: pointer;
}

.zoom-footer-hint {
  margin: 0;
  font-size: 12px;
  color: #9ba8ac;
  text-align: center;
}

@media (max-width: 640px) {
  .inspect-layout {
    grid-template-columns: 1fr;
  }
}
</style>
