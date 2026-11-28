<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import type { CheckResult } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { projectedFaces, validPercentileDice } from './presentation'
import { dicePresentationCopy } from './copy'
import { polyGeometryForSides } from './polyhedron'
import { isDiceSoundMuted, playDiceRollSound, setDiceSoundMuted, type DiceSoundOutcome } from './sound'

type DieTone = 'normal' | 'critical' | 'fumble'

const props = defineProps<{
  check: CheckResult
  revealed: boolean
  rolling?: boolean
  animateReveal?: boolean
  customFaces?: Array<{ value: number; label: string; sides: number; taken: boolean }>
}>()

const { t, locale } = useLocale()
const materialId = useId()
const labels = computed(() => dicePresentationCopy(locale.value))
const zh = computed(() => locale.value.startsWith('zh'))
const muted = ref(isDiceSoundMuted())

const percentile = computed(() => !props.customFaces?.length && props.revealed && validPercentileDice(props.check.percentile_dice))
const faces = computed(() => {
  if (!props.revealed) return []
  if (props.customFaces?.length) return props.customFaces
  return projectedFaces(props.check)
})
const fallback = computed(() => (props.check.rolls?.length ? props.check.rolls : [props.check.roll])
  .filter(value => typeof value === 'number').join(', '))

const sealedSides = computed(() => {
  const match = String(props.check.dice || '').match(/d(4|6|8|10|12|20|100)\b/i)
  return match ? Number(match[1]) : 20
})
const sealedGeometry = computed(() => polyGeometryForSides(sealedSides.value))

function dieTone(face: { value: number; sides: number; taken: boolean }, index: number): DieTone {
  const adopted = face.taken && !(percentile.value && index === 0)
  if (!adopted && faces.value.length > 1) return 'normal'
  if (props.check.is_critical) return 'critical'
  if (props.check.is_fumble) return 'fumble'
  if (face.sides === 20 && face.value === 20) return 'critical'
  if (face.sides === 20 && face.value === 1) return 'fumble'
  return 'normal'
}

const trayOutcome = computed<DiceSoundOutcome>(() => {
  if (!props.revealed) return 'normal'
  if (props.check.is_critical) return 'critical'
  if (props.check.is_fumble) return 'fumble'
  for (let i = 0; i < faces.value.length; i++) {
    const tone = dieTone(faces.value[i]!, i)
    if (tone !== 'normal') return tone
  }
  return 'normal'
})

// 仅用于视觉翻滚阶段的临时跳字，绝不改变权威值或 data-die-value。
const scrambleLabels = ref<string[] | null>(null)
let scrambleRaf = 0

function stopScramble() {
  if (scrambleRaf) {
    cancelAnimationFrame(scrambleRaf)
    scrambleRaf = 0
  }
  scrambleLabels.value = null
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function randomFaceLabel(sides: number, isPercentileTens: boolean, isPercentileUnits: boolean): string {
  if (isPercentileUnits) return String(Math.floor(Math.random() * 10))
  if (isPercentileTens) return String(Math.floor(Math.random() * 10) * 10).padStart(2, '0')
  return String(Math.floor(Math.random() * Math.max(2, sides)) + 1)
}

function triggerRevealEffects() {
  stopScramble()
  if (!props.revealed || !props.animateReveal || !faces.value.length) return
  playDiceRollSound(faces.value.length, trayOutcome.value)
  if (prefersReducedMotion() || typeof requestAnimationFrame !== 'function') return

  const targetFaces = faces.value.slice(0, 12)
  const isPct = percentile.value
  const duration = 360
  let startTime = 0
  let lastTick = 0

  const step = (now: number) => {
    if (!startTime) startTime = now
    const elapsed = now - startTime
    if (elapsed >= duration) {
      stopScramble()
      return
    }
    if (!lastTick || now - lastTick >= 46) {
      lastTick = now
      scrambleLabels.value = targetFaces.map((face, idx) => {
        // 按顺序逐颗定格，更有真实多颗骰子先后停稳的感觉
        const settleAt = 210 + idx * 32
        if (elapsed >= settleAt) return face.label
        return randomFaceLabel(face.sides, isPct && idx > 0, isPct && idx === 0)
      })
    }
    scrambleRaf = requestAnimationFrame(step)
  }
  scrambleRaf = requestAnimationFrame(step)
}

function toggleMute() {
  muted.value = !muted.value
  setDiceSoundMuted(muted.value)
}

function displayLabel(face: { label: string }, index: number): string {
  return scrambleLabels.value?.[index] ?? face.label
}

// 读屏只需要"哪颗骰子、几点、有没有被采用"；视觉上的刻印、材质交给 CSS。
function dieLabel(face: { value: number; sides: number; taken: boolean }, index: number): string {
  const identity = percentile.value ? (index === 0 ? labels.value.units : labels.value.tens) : `d${face.sides}`
  const adopted = face.taken && !(percentile.value && index === 0)
  return `${identity} ${face.value}${adopted ? ` ${t('checkDiceTaken')}` : ''}`
}

onMounted(() => {
  if (props.revealed && props.animateReveal) triggerRevealEffects()
})

watch(
  () => [props.revealed, props.animateReveal, props.check.roll, props.customFaces] as const,
  ([revealed, animate]) => {
    if (revealed && animate) triggerRevealEffects()
    else stopScramble()
  },
)

onBeforeUnmount(stopScramble)
</script>

<template>
  <div
    class="dice-tray"
    :class="{
      'dice-tray-rolling': rolling,
      'dice-tray-sealed': !revealed,
      'dice-tray-reveal': animateReveal && revealed,
      'dice-tray-has-taken': faces.some(face => face.taken),
      'dice-tray-critical': trayOutcome === 'critical',
      'dice-tray-fumble': trayOutcome === 'fumble',
    }"
    data-testid="dice-tray"
  >
    <div class="dice-tray-felt" aria-hidden="true" />
    <div class="dice-tray-rim" aria-hidden="true" />

    <button
      v-if="revealed"
      type="button"
      class="dice-sound-toggle"
      data-testid="dice-sound-toggle"
      :aria-pressed="!muted"
      :title="muted ? (zh ? '开启落骰音效' : 'Unmute dice sound') : (zh ? '静音落骰音效' : 'Mute dice sound')"
      :aria-label="muted ? (zh ? '开启落骰音效' : 'Unmute dice sound') : (zh ? '静音落骰音效' : 'Mute dice sound')"
      @click="toggleMute"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor" />
        <path v-if="!muted" d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        <path v-else d="M16 9l5 6M21 9l-5 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
      </svg>
    </button>

    <template v-if="revealed && faces.length">
      <div
        v-for="(face, index) in faces.slice(0, 12)"
        :key="index"
        class="tray-die"
        :class="[
          { 'tray-die-taken': face.taken, 'tray-die-cube': face.sides === 6, 'tray-die-d4': face.sides === 4 },
          `tray-die--${dieTone(face, index)}`,
        ]"
        :style="{ '--die-delay': `${index * 85}ms` }"
        role="group"
        :aria-label="dieLabel(face, index)"
        :data-die-value="face.value"
        :data-taken="String(face.taken)"
        :data-tone="dieTone(face, index)"
      >
        <span class="tray-die-shadow" aria-hidden="true" />
        <span class="tray-die-impact" aria-hidden="true" />
        <span v-if="dieTone(face, index) !== 'normal'" class="tray-die-aura" aria-hidden="true" />
        <div class="tray-die-body">
          <svg viewBox="0 0 100 104" aria-hidden="true" focusable="false">
            <defs>
              <linearGradient :id="`${materialId}-sheen-${index}`" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="white" stop-opacity="0.34" />
                <stop offset="45%" stop-color="white" stop-opacity="0.06" />
                <stop offset="100%" stop-color="black" stop-opacity="0.18" />
              </linearGradient>
            </defs>
            <g class="die-facets-group">
              <polygon
                v-for="(facet, fIdx) in polyGeometryForSides(face.sides).facets"
                :key="fIdx"
                :points="facet.points"
                :class="`die-facet die-facet--${facet.shade}`"
              />
              <polygon
                :points="polyGeometryForSides(face.sides).outline"
                :fill="`url(#${materialId}-sheen-${index})`"
                class="die-sheen"
              />
              <path :d="polyGeometryForSides(face.sides).ridges" class="die-ridges" />
              <polygon :points="polyGeometryForSides(face.sides).outline" class="die-outline" />
            </g>
          </svg>
          <b
            class="tray-die-number"
            :style="{ top: `${polyGeometryForSides(face.sides).numberTop}px` }"
          >{{ displayLabel(face, index) }}</b>
        </div>
        <small>{{ percentile ? (index === 0 ? labels.units : labels.tens) : `d${face.sides}` }}</small>
        <span class="tray-die-caption">{{ face.taken && !(percentile && index === 0) ? t('checkDiceTaken') : '\u00a0' }}</span>
      </div>
      <span v-if="faces.length > 12" class="tray-extra">+{{ faces.length - 12 }}</span>
    </template>

    <p v-else-if="revealed" class="dice-tray-fallback">{{ check.dice }} · {{ t('checkDiceFaces', { rolls: fallback }) }}</p>

    <div v-else class="tray-seal" aria-hidden="true">
      <div class="tray-seal-die">
        <svg viewBox="0 0 100 104" focusable="false">
          <polygon
            v-for="(facet, fIdx) in sealedGeometry.facets"
            :key="fIdx"
            :points="facet.points"
            :class="`die-facet die-facet--${facet.shade}`"
          />
          <path :d="sealedGeometry.ridges" class="die-ridges" />
          <polygon :points="sealedGeometry.outline" class="die-outline" />
        </svg>
        <span class="tray-seal-glyph">?</span>
      </div>
      <small>{{ check.dice }}</small>
    </div>
  </div>
</template>

<style scoped>
.dice-tray {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: start;
  gap: 14px 22px;
  isolation: isolate;
  perspective: 720px;
  min-height: 142px;
  padding: 18px 22px 14px;
  margin: 10px 0;
  border: 1px solid color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft));
  border-radius: 12px;
  background:
    radial-gradient(ellipse 80% 55% at 50% 18%, color-mix(in srgb, var(--df-accent) 14%, transparent), transparent 72%),
    radial-gradient(ellipse 100% 90% at 50% 50%, color-mix(in srgb, var(--df-surface-2) 92%, var(--df-canvas)), color-mix(in srgb, var(--df-canvas) 82%, var(--df-control-bg)));
  box-shadow:
    inset 0 10px 24px color-mix(in srgb, var(--df-canvas) 62%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 10%, transparent),
    0 2px 8px color-mix(in srgb, var(--df-canvas) 35%, transparent);
  overflow: hidden;
  transition: border-color 0.28s ease, box-shadow 0.28s ease;
}

.dice-tray-critical {
  border-color: color-mix(in srgb, #f5b942 62%, var(--df-border-soft));
  box-shadow:
    inset 0 10px 24px color-mix(in srgb, var(--df-canvas) 56%, transparent),
    inset 0 0 28px color-mix(in srgb, #f5b942 16%, transparent),
    0 0 16px color-mix(in srgb, #f5b942 22%, transparent);
}

.dice-tray-fumble {
  border-color: color-mix(in srgb, #e05252 58%, var(--df-border-soft));
  box-shadow:
    inset 0 10px 24px color-mix(in srgb, var(--df-canvas) 58%, transparent),
    inset 0 0 26px color-mix(in srgb, #e05252 16%, transparent);
}

.dice-tray-felt {
  position: absolute;
  inset: 0;
  z-index: -2;
  opacity: 0.22;
  background-image: radial-gradient(color-mix(in srgb, white 18%, transparent) 0.75px, transparent 0.75px);
  background-size: 6px 6px;
  pointer-events: none;
}

.dice-tray-rim {
  position: absolute;
  z-index: -1;
  inset: 6px;
  border: 1px dashed color-mix(in srgb, var(--df-accent) 26%, var(--df-border-soft));
  border-radius: 8px;
  pointer-events: none;
}

.dice-sound-toggle {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--df-border-soft) 70%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--df-surface-1) 76%, transparent);
  color: var(--df-text-muted);
  cursor: pointer;
  transition: color 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.dice-sound-toggle:hover {
  color: var(--df-accent-strong);
  border-color: color-mix(in srgb, var(--df-accent) 45%, var(--df-border-soft));
  background: color-mix(in srgb, var(--df-surface-2) 92%, transparent);
}

.dice-sound-toggle svg {
  width: 14px;
  height: 14px;
}

.tray-die {
  --die-base: var(--df-accent);
  --die-surface: var(--df-surface-3);
  --die-highlight: color-mix(in srgb, white 68%, var(--die-base));
  position: relative;
  display: grid;
  grid-template-rows: 78px minmax(18px, auto) 18px;
  justify-items: center;
  align-items: center;
  width: 80px;
  font-variant-numeric: tabular-nums;
  transform-style: preserve-3d;
}

.tray-die--critical {
  --die-base: #e7a935;
  --die-surface: #78480a;
  --die-highlight: #fff4c2;
}

.tray-die--fumble {
  --die-base: #d44747;
  --die-surface: #58171c;
  --die-highlight: #ffd0d0;
}

.tray-die-body {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  height: 78px;
  transform-style: preserve-3d;
  will-change: transform, opacity;
}

.tray-die svg {
  width: 72px;
  height: 76px;
  overflow: visible;
  filter:
    drop-shadow(0 8px 10px color-mix(in srgb, var(--df-canvas) 76%, transparent))
    drop-shadow(0 0 12px color-mix(in srgb, var(--die-base) 22%, transparent));
}

/* 6 档分面明暗：让 2D SVG 呈现立体多面体切面受光 */
.die-facet { stroke: none; }
.die-facet--top    { fill: color-mix(in srgb, white 42%, var(--die-base)); }
.die-facet--light  { fill: color-mix(in srgb, white 24%, var(--die-base)); }
.die-facet--center { fill: color-mix(in srgb, var(--die-base) 76%, var(--die-surface)); }
.die-facet--mid    { fill: color-mix(in srgb, var(--die-base) 58%, var(--die-surface)); }
.die-facet--dark   { fill: color-mix(in srgb, var(--df-canvas) 34%, var(--die-base)); }
.die-facet--deep   { fill: color-mix(in srgb, var(--df-canvas) 54%, var(--die-base)); }

.die-sheen { pointer-events: none; }

.die-ridges {
  fill: none;
  stroke: color-mix(in srgb, var(--die-highlight) 72%, transparent);
  stroke-width: 1;
  stroke-linejoin: round;
  opacity: 0.72;
}

.die-outline {
  fill: none;
  stroke: color-mix(in srgb, var(--die-highlight) 62%, var(--die-base));
  stroke-width: 1.5;
  stroke-linejoin: round;
}

.tray-die-taken .die-outline {
  stroke: var(--die-highlight);
  stroke-width: 2;
}

.tray-die-taken svg {
  filter:
    drop-shadow(0 9px 12px color-mix(in srgb, var(--df-canvas) 78%, transparent))
    drop-shadow(0 0 16px color-mix(in srgb, var(--die-base) 38%, transparent));
}

/* 多骰时未采纳的骰子压暗，主骰光感更突出 */
.dice-tray-has-taken .tray-die:not(.tray-die-taken) .tray-die-body {
  opacity: 0.44;
  filter: saturate(0.38) brightness(0.78);
  transform: scale(0.92);
}

.tray-die-shadow {
  position: absolute;
  z-index: 0;
  left: 50%;
  top: 63px;
  width: 58px;
  height: 14px;
  margin-left: -29px;
  border-radius: 50%;
  background: radial-gradient(ellipse at center, color-mix(in srgb, var(--df-canvas) 78%, transparent), transparent 72%);
  opacity: 0.65;
  pointer-events: none;
}

.tray-die-impact {
  position: absolute;
  z-index: 0;
  left: 50%;
  top: 58px;
  width: 18px;
  height: 18px;
  margin-left: -9px;
  border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--die-highlight) 75%, transparent);
  opacity: 0;
  pointer-events: none;
}

.tray-die-aura {
  position: absolute;
  z-index: -1;
  left: 50%;
  top: 38px;
  width: 72px;
  height: 72px;
  margin: -36px 0 0 -36px;
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--die-highlight) 38%, transparent), transparent 70%);
  animation: aura-pulse 2.2s ease-in-out infinite;
  pointer-events: none;
}

.tray-die-number {
  position: absolute;
  left: 0;
  width: 100%;
  text-align: center;
  font: 800 25px/1 var(--df-font-title);
  letter-spacing: -0.02em;
  color: color-mix(in srgb, white 94%, var(--die-highlight));
  text-shadow:
    0 2px 4px color-mix(in srgb, var(--df-canvas) 88%, transparent),
    0 -1px 0 color-mix(in srgb, black 55%, transparent),
    0 0 8px color-mix(in srgb, var(--die-base) 45%, transparent);
  pointer-events: none;
}

.tray-die-d4 .tray-die-number {
  font-size: 21px;
}

.tray-die > small {
  width: 100%;
  text-align: center;
  color: var(--df-text-muted);
  font-size: 10px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.tray-die-caption {
  text-align: center;
  color: var(--df-accent-strong);
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

/* 封存/滚动中：3D 翻滚的神秘多面体轮廓 */
.tray-seal {
  display: grid;
  justify-items: center;
  gap: 8px;
  color: var(--df-text-secondary);
}

.tray-seal-die {
  --die-base: var(--df-accent);
  --die-surface: var(--df-surface-3);
  --die-highlight: color-mix(in srgb, white 60%, var(--df-accent));
  position: relative;
  display: grid;
  place-items: center;
  width: 68px;
  height: 70px;
  transform-style: preserve-3d;
}

.tray-seal-die svg {
  width: 64px;
  height: 68px;
  opacity: 0.88;
  filter: drop-shadow(0 6px 10px color-mix(in srgb, var(--df-canvas) 70%, transparent));
}

.tray-seal-glyph {
  position: absolute;
  font: 800 22px/1 var(--df-font-title);
  color: color-mix(in srgb, white 90%, var(--df-accent));
  text-shadow: 0 2px 4px color-mix(in srgb, var(--df-canvas) 80%, transparent);
}

.tray-seal small {
  color: var(--df-text-muted);
  font-size: 11px;
}

.dice-tray-sealed {
  min-height: 106px;
  padding: 16px;
}

.dice-tray-fallback {
  margin: 0;
  color: var(--df-text-secondary);
  overflow-wrap: anywhere;
}

.tray-extra {
  color: var(--df-text-muted);
  align-self: center;
}

.dice-tray-rolling .tray-seal-die {
  animation: sealed-tumble 0.86s ease-in-out infinite;
}

/* 3D 抛掷翻滚 → 落地两段回弹 → 稳稳定格 */
.dice-tray-reveal .tray-die-body {
  animation: die-toss-3d 0.72s cubic-bezier(0.22, 0.9, 0.32, 1) var(--die-delay, 0ms) both;
}

.dice-tray-reveal .tray-die--fumble .tray-die-body {
  animation:
    die-toss-3d 0.72s cubic-bezier(0.22, 0.9, 0.32, 1) var(--die-delay, 0ms) both,
    fumble-shake 0.36s ease-in-out calc(var(--die-delay, 0ms) + 680ms) 1;
}

.dice-tray-reveal .tray-die-shadow {
  animation: shadow-toss 0.72s cubic-bezier(0.22, 0.9, 0.32, 1) var(--die-delay, 0ms) both;
}

.dice-tray-reveal .tray-die-impact {
  animation: impact-ring 0.56s ease-out calc(var(--die-delay, 0ms) + 480ms) both;
}

@keyframes sealed-tumble {
  0%   { transform: rotateX(0deg) rotateY(0deg) rotateZ(-10deg) scale(0.94); }
  50%  { transform: rotateX(22deg) rotateY(180deg) rotateZ(12deg) scale(1.06); }
  100% { transform: rotateX(0deg) rotateY(360deg) rotateZ(-10deg) scale(0.94); }
}

@keyframes die-toss-3d {
  0% {
    opacity: 0;
    transform: translate3d(-18px, -38px, 60px) scale(0.76) rotateX(68deg) rotateY(-140deg) rotateZ(-36deg);
  }
  20% {
    opacity: 1;
    transform: translate3d(-8px, -22px, 32px) scale(1.08) rotateX(34deg) rotateY(-40deg) rotateZ(24deg);
  }
  58% {
    transform: translate3d(2px, 4px, 0) scale(1.04, 0.92) rotateX(-8deg) rotateY(10deg) rotateZ(8deg);
  }
  76% {
    transform: translate3d(0, -9px, 12px) scale(0.98, 1.03) rotateX(6deg) rotateY(-5deg) rotateZ(-4deg);
  }
  90% {
    transform: translate3d(0, 2px, 0) scale(1.01, 0.98) rotateX(0deg) rotateY(0deg) rotateZ(1deg);
  }
  100% {
    opacity: 1;
    transform: none;
  }
}

@keyframes shadow-toss {
  0%   { opacity: 0.14; transform: scale(0.45); }
  58%  { opacity: 0.78; transform: scale(1.12); }
  76%  { opacity: 0.36; transform: scale(0.78); }
  90%  { opacity: 0.7;  transform: scale(1.04); }
  100% { opacity: 0.65; transform: scale(1); }
}

@keyframes impact-ring {
  0%   { opacity: 0.85; transform: scale(0.45); }
  100% { opacity: 0; transform: scale(3.5); }
}

@keyframes aura-pulse {
  0%, 100% { opacity: 0.55; transform: scale(0.96); }
  50%      { opacity: 0.95; transform: scale(1.1); }
}

@keyframes fumble-shake {
  0%, 100% { transform: translateX(0); }
  20%      { transform: translateX(-4px) rotate(-3deg); }
  45%      { transform: translateX(4px) rotate(3deg); }
  70%      { transform: translateX(-2px) rotate(-1.5deg); }
}

@media (max-width: 520px) {
  .dice-tray { gap: 10px 12px; min-height: 122px; padding: 14px 12px 10px; }
  .tray-die { width: 66px; grid-template-rows: 66px minmax(18px, auto) 18px; }
  .tray-die-body { height: 66px; }
  .tray-die svg { width: 60px; height: 64px; }
  .tray-die-number { font-size: 21px; }
  .tray-die-shadow { top: 53px; width: 48px; margin-left: -24px; }
  .tray-die-impact { top: 48px; }
  .dice-tray-sealed { min-height: 92px; }
}

@media (prefers-reduced-motion: reduce) {
  .dice-tray-rolling .tray-seal-die,
  .dice-tray-reveal .tray-die-body,
  .dice-tray-reveal .tray-die-shadow,
  .dice-tray-reveal .tray-die-impact,
  .tray-die-aura {
    animation: none;
  }
}
</style>
