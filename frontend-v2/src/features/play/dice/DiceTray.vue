<script setup lang="ts">
import { computed, useId } from 'vue'
import type { CheckResult } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { projectedFaces, validPercentileDice } from './presentation'
import { dicePresentationCopy } from './copy'

const props = defineProps<{ check: CheckResult; revealed: boolean; rolling?: boolean; animateReveal?: boolean }>()
const { t, locale } = useLocale()
const materialId = useId()
const labels = computed(() => dicePresentationCopy(locale.value))
const percentile = computed(() => props.revealed && validPercentileDice(props.check.percentile_dice))
const faces = computed(() => props.revealed ? projectedFaces(props.check) : [])
const fallback = computed(() => (props.check.rolls?.length ? props.check.rolls : [props.check.roll])
  .filter(value => typeof value === 'number').join(', '))

// 读屏只需要"哪颗骰子、几点、有没有被采用"；视觉上的刻印、材质交给 CSS。
function dieLabel(face: { value: number; sides: number; taken: boolean }, index: number): string {
  const identity = percentile.value ? (index === 0 ? labels.value.units : labels.value.tens) : `d${face.sides}`
  const adopted = face.taken && !(percentile.value && index === 0)
  return `${identity} ${face.value}${adopted ? ` ${t('checkDiceTaken')}` : ''}`
}
</script>

<template>
  <div class="dice-tray" :class="{ 'dice-tray-rolling': rolling, 'dice-tray-sealed': !revealed, 'dice-tray-reveal': animateReveal && revealed, 'dice-tray-has-taken': faces.some(face => face.taken) }" data-testid="dice-tray">
    <div class="dice-tray-rim" aria-hidden="true" />
    <template v-if="revealed && faces.length">
      <div v-for="(face, index) in faces.slice(0, 12)" :key="index" class="tray-die" :class="{ 'tray-die-taken': face.taken, 'tray-die-cube': face.sides === 6 }"
        :style="{ '--die-delay': `${index * 90}ms` }" role="group" :aria-label="dieLabel(face, index)"
        :data-die-value="face.value" :data-taken="String(face.taken)">
        <span class="tray-die-shadow" aria-hidden="true" />
        <span class="tray-die-impact" aria-hidden="true" />
        <div class="tray-die-body">
        <svg viewBox="0 0 100 104" aria-hidden="true" focusable="false">
          <defs>
            <linearGradient :id="`${materialId}-${index}`" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" class="die-material-light" />
              <stop offset=".5" class="die-material-mid" />
              <stop offset="1" class="die-material-shadow" />
            </linearGradient>
          </defs>
          <g :style="{ fill: `url(#${materialId}-${index})` }">
          <template v-if="face.sides === 6">
            <polygon points="50,4 94,26 94,78 50,100 6,78 6,26" class="die-body" />
            <path d="M6 26 50 48 94 26M50 48V100M50 4V48" class="die-facets" />
          </template>
          <template v-else-if="face.sides === 4">
            <polygon points="50,5 96,91 4,91" class="die-body" />
            <path d="M50 5 50 66 4 91M50 66 96 91" class="die-facets" />
          </template>
          <template v-else-if="face.sides === 8">
            <polygon points="50,3 94,52 50,101 6,52" class="die-body" />
            <path d="M50 3 30 52 50 101M50 3 70 52 50 101M6 52H94" class="die-facets" />
          </template>
          <template v-else-if="face.sides === 10 || face.sides === 100">
            <polygon points="50,3 93,36 78,82 50,101 22,82 7,36" class="die-body" />
            <path d="M50 3 28 38 50 79 72 38 50 3M7 36 28 38 22 82M93 36 72 38 78 82M50 79V101" class="die-facets" />
          </template>
          <template v-else-if="face.sides === 12">
            <polygon points="50,3 94,34 82,85 50,101 18,85 6,34" class="die-body" />
            <path d="M50 22 78 43 67 75 33 75 22 43ZM50 3V22M94 34 78 43M82 85 67 75M18 85 33 75M6 34 22 43" class="die-facets" />
          </template>
          <template v-else>
            <polygon points="50,3 95,28 95,77 50,101 5,77 5,28" class="die-body" />
            <path d="M50 3 25 37 5 28M50 3 75 37 95 28M25 37 75 37 50 78 25 37M5 77 25 37M95 77 75 37M5 77 50 78 95 77M50 101 50 78" class="die-facets" />
          </template>
          </g>
        </svg>
        <b class="tray-die-number">{{ face.label }}</b>
        </div>
        <small>{{ percentile ? (index === 0 ? labels.units : labels.tens) : `d${face.sides}` }}</small>
        <span class="tray-die-caption">{{ face.taken && !(percentile && index === 0) ? t('checkDiceTaken') : '\u00a0' }}</span>
      </div>
      <span v-if="faces.length > 12" class="tray-extra">+{{ faces.length - 12 }}</span>
    </template>
    <p v-else-if="revealed" class="dice-tray-fallback">{{ check.dice }} · {{ t('checkDiceFaces', { rolls: fallback }) }}</p>
    <div v-else class="tray-seal" aria-hidden="true"><span>✧</span><small>{{ check.dice }}</small></div>
  </div>
</template>

<style scoped>
.dice-tray { position: relative; display: flex; flex-wrap: wrap; justify-content: center; align-items: start; gap: 12px 20px; isolation: isolate; min-height: 136px; padding: 14px 18px; margin: 10px 0; border: 1px solid var(--df-border-soft); border-radius: 10px; background: radial-gradient(ellipse at 50% 35%, color-mix(in srgb, var(--df-interactive) 10%, var(--df-surface-2)), var(--df-control-bg) 82%); }
.dice-tray-rim { position: absolute; z-index: -1; inset: 5px; border: 1px solid color-mix(in srgb, var(--df-border-soft) 38%, transparent); border-radius: 6px; pointer-events: none; }
.tray-die { position: relative; display: grid; grid-template-rows: 74px minmax(18px, auto) 18px; justify-items: center; align-items: center; width: 76px; font-variant-numeric: tabular-nums; }
.tray-die-body { position: relative; display: grid; place-items: center; width: 100%; height: 74px; will-change: transform; }
.tray-die svg { width: 68px; height: 74px; overflow: visible; filter: drop-shadow(0 6px 9px color-mix(in srgb, var(--df-canvas) 72%, transparent)) drop-shadow(0 0 12px color-mix(in srgb, var(--df-accent) 14%, transparent)); }
/* 地面阴影：抛掷时变小变淡，落地恢复；这是"在空中"的主要视觉线索。 */
.tray-die-shadow { position: absolute; z-index: 0; left: 50%; top: 58px; width: 54px; height: 13px; margin-left: -27px; border-radius: 50%; background: radial-gradient(ellipse at center, color-mix(in srgb, var(--df-canvas) 68%, transparent), transparent 72%); opacity: .55; pointer-events: none; }
.tray-die-impact { position: absolute; z-index: 0; left: 50%; top: 54px; width: 16px; height: 16px; margin-left: -8px; border-radius: 50%; border: 2px solid color-mix(in srgb, var(--df-accent) 55%, transparent); opacity: 0; pointer-events: none; }
/* 材质跟随主题：accent 与 surface 混出来的金属，不写死金色。 */
.die-body { stroke: color-mix(in srgb, white 30%, var(--df-accent)); stroke-width: 1.1; }
.die-material-light { stop-color: color-mix(in srgb, white 46%, var(--df-accent)); }
.die-material-mid { stop-color: color-mix(in srgb, var(--df-accent) 66%, var(--df-surface-3)); }
.die-material-shadow { stop-color: color-mix(in srgb, var(--df-canvas) 46%, var(--df-accent)); }
.die-facets { fill: none; stroke: color-mix(in srgb, white 58%, var(--df-accent)); stroke-width: .7; opacity: .45; }
.tray-die-taken .die-body { stroke: color-mix(in srgb, white 55%, var(--df-accent-strong)); stroke-width: 1.8; }
.tray-die-taken .die-material-light { stop-color: color-mix(in srgb, white 62%, var(--df-accent)); }
.tray-die-taken .die-material-mid { stop-color: color-mix(in srgb, var(--df-accent) 78%, var(--df-surface-3)); }
/* 多骰时把没被采用的那几颗压暗：只有被采用的那颗保持金属高光。 */
.dice-tray-has-taken .tray-die:not(.tray-die-taken) .tray-die-body { opacity: .5; filter: saturate(.45) brightness(.85); }
.tray-die-number { position: absolute; top: 20px; left: 0; width: 100%; text-align: center; font: 700 27px var(--df-font-title); color: color-mix(in srgb, var(--df-text) 90%, white); text-shadow: 0 -1px 0 color-mix(in srgb, var(--df-canvas) 75%, transparent), 0 1px 0 color-mix(in srgb, white 16%, transparent); }
.tray-die > small { width: 100%; text-align: center; color: var(--df-text-muted); font-size: 10px; line-height: 1.35; overflow-wrap: anywhere; }
.tray-die-cube .tray-die-number { top: 23px; font-size: 26px; }
.tray-die-caption { text-align: center; color: var(--df-accent-strong); font-size: 10px; line-height: 18px; }
.tray-seal { display: grid; place-items: center; color: var(--df-text-secondary); }
.tray-seal span { font-size: 46px; line-height: 1; }
.tray-seal small { margin-top: 8px; color: var(--df-text-muted); }
.dice-tray-sealed { min-height: 90px; padding: 15px; }
.dice-tray-fallback { margin: 0; color: var(--df-text-secondary); overflow-wrap: anywhere; }
.tray-extra { color: var(--df-text-muted); }
.dice-tray-rolling .tray-seal { animation: settle .72s ease-in-out infinite; }
@keyframes settle { 0%,100% { transform: rotate(-12deg) scale(.92); } 50% { transform: rotate(16deg) scale(1.05); } }
/* 抛掷：骰体走抛物线并挤压落地，影子留在地面缩放，落地再来一圈冲击。 */
.dice-tray-reveal .tray-die-body { animation: die-toss .64s cubic-bezier(.33,.02,.62,1) var(--die-delay, 0ms) both; }
.dice-tray-reveal .tray-die-shadow { animation: shadow-toss .64s cubic-bezier(.33,.02,.62,1) var(--die-delay, 0ms) both; }
.dice-tray-reveal .tray-die-impact { animation: impact-ring .52s ease-out calc(var(--die-delay, 0ms) + 520ms) both; }
@keyframes die-toss {
  0% { opacity: 0; transform: translateY(40px) scale(.84) rotate(-16deg); }
  16% { opacity: 1; }
  42% { transform: translateY(-32px) scale(1.05) rotate(46deg); }
  74% { transform: translateY(-6px) scale(1.01) rotate(122deg); }
  88% { transform: translateY(2px) scale(.95, 1.05) rotate(142deg); }
  100% { opacity: 1; transform: none; }
}
@keyframes shadow-toss {
  0% { opacity: .58; transform: scale(1.02); }
  42% { opacity: .18; transform: scale(.52); }
  74% { opacity: .38; transform: scale(.88); }
  100% { opacity: .55; transform: scale(1); }
}
@keyframes impact-ring { 0% { opacity: .8; transform: scale(.4); } 100% { opacity: 0; transform: scale(3.2); } }
@media(max-width: 520px) { .dice-tray { gap: 10px; min-height: 116px; padding: 10px; } .tray-die { width: 64px; grid-template-rows: 64px minmax(18px, auto) 18px; } .tray-die-body { height: 64px; } .tray-die svg { width: 58px; height: 64px; } .tray-die-number { top: 17px; font-size: 22px; } .tray-die-cube .tray-die-number { top: 19px; } .tray-die-shadow { top: 50px; width: 46px; margin-left: -23px; } .tray-die-impact { top: 46px; } .dice-tray-sealed { min-height: 80px; } }
@media(prefers-reduced-motion: reduce) { .dice-tray-rolling .tray-seal, .dice-tray-reveal .tray-die-body, .dice-tray-reveal .tray-die-shadow, .dice-tray-reveal .tray-die-impact { animation: none; } }
</style>
