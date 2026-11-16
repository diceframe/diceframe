<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { NIcon } from 'naive-ui'
import { CubeOutline, MapOutline, OptionsOutline, ColorPaletteOutline, ConstructOutline } from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import type { ModuleMarketplaceItem } from '@/api/modules'

const props = defineProps<{
  item: ModuleMarketplaceItem
  installing: boolean
  hasNewerVersion: boolean
  showHubStats: boolean
}>()
const emit = defineEmits<{ install: []; detail: [] }>()

const { t, locale } = useLocale()

// Hash id → HSL hue (0–360), consistent per package
function idHue(id: string): number {
  let h = 0
  for (const ch of id) h = ((h << 5) - h + ch.charCodeAt(0)) | 0
  return Math.abs(h) % 360
}

// Art background style: hue-based with subtle noise overlay
const artStyle = computed(() => {
  const hue = idHue(props.item.id)
  // Pick a complementary dark palette
  const bg1 = `hsl(${hue}, 55%, 22%)`
  const bg2 = `hsl(${(hue + 30) % 360}, 60%, 14%)`
  return {
    background: `linear-gradient(135deg, ${bg1} 0%, ${bg2} 100%)`,
    '--cp-icon-color': `hsl(${hue}, 70%, 72%)`,
  }
})

// Big icon per capability
const capIcon = computed(() => {
  const caps = props.item.capabilities || []
  if (caps.includes('adventure')) return MapOutline
  if (caps.includes('rules')) return OptionsOutline
  if (caps.includes('ui')) return ColorPaletteOutline
  if (caps.includes('materials')) return ConstructOutline
  return CubeOutline
})

const trustBadge = computed(() => {
  if (props.item.trust_level === 'official') return { label: t('pluginTrustOfficial'), cls: 'cp-trust-official' }
  if (props.item.trust_level === 'verified') return { label: t('pluginTrustVerified'), cls: 'cp-trust-verified' }
  return null
})

const rulesetTags = computed(() => {
  const targets = props.item.ruleset_targets || []
  return targets.map(target => target.includes(':') ? target.split(':').slice(1).join(':') : target).slice(0, 2)
})

function formatCount(value: number | undefined): string {
  if (!value) return '0'
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}
</script>

<template>
  <article class="content-pack-card" :style="artStyle">
    <div class="cp-bg-icon" aria-hidden="true">
      <NIcon :component="capIcon" />
    </div>
    <div class="cp-body">
      <div class="cp-header">
        <div class="cp-title-row">
          <h3 class="cp-name">{{ item.name || item.id }}</h3>
          <span v-if="trustBadge" :class="['cp-trust', trustBadge.cls]">{{ trustBadge.label }}</span>
        </div>
        <p class="cp-author">{{ item.author || item.id }}</p>
      </div>
      <p class="cp-desc">{{ item.description || t('noDescription') }}</p>
      <div v-if="showHubStats" class="cp-stats">
        <span>↓ {{ formatCount(item.stats?.downloads_total) }}</span>
        <span>★ {{ (item.stats?.rating_average || 0).toFixed(1) }}</span>
        <span>♥ {{ formatCount(item.stats?.likes) }}</span>
      </div>
      <div class="cp-tags">
        <NTag v-for="tag in rulesetTags" :key="tag" size="small" type="info">{{ tag }}</NTag>
        <NTag v-for="lang in (item.languages || []).slice(0, 1)" :key="lang" size="small">{{ lang }}</NTag>
        <NTag v-if="item.adventure_count > 0" size="small">{{ locale === 'zh-CN' ? `${item.adventure_count} 个冒险` : `${item.adventure_count} adventures` }}</NTag>
        <NTag v-if="item.installed && !hasNewerVersion" size="small" type="success">{{ t('installed') }}</NTag>
        <NTag v-if="hasNewerVersion" size="small" type="warning">{{ t('update') }}</NTag>
      </div>
      <div class="cp-actions">
        <NButton
          v-if="!item.installed || hasNewerVersion"
          type="primary"
          size="small"
          :loading="installing"
          :disabled="item.installable === false"
          @click.stop="emit('install')"
        >
          {{ item.installed ? t('update') : t('install') }}
        </NButton>
        <NButton v-else size="small" secondary disabled>{{ t('installed') }}</NButton>
        <NButton v-if="showHubStats" size="small" secondary @click.stop="emit('detail')">{{ t('hubDataAndReviews') }}</NButton>
      </div>
    </div>
    <div v-if="installing" class="cp-install-bar" role="progressbar" aria-label="Installing…">
      <div class="cp-install-bar-fill" />
    </div>
  </article>
</template>

<style scoped>
.content-pack-card {
  position: relative;
  display: flex;
  flex-direction: column;
  border-radius: var(--df-radius-lg);
  border: 1px solid rgba(255,255,255,.08);
  overflow: hidden;
  cursor: pointer;
  transition: transform .15s ease, box-shadow .15s ease;
  min-height: 180px;
}
.content-pack-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.28); }

/* Large faint background icon */
.cp-bg-icon {
  position: absolute;
  right: -12px;
  bottom: -12px;
  font-size: 90px;
  color: rgba(255,255,255,.1);
  pointer-events: none;
  z-index: 0;
  transform: rotate(-10deg);
}

/* Content overlay with gradient scrim for readability */
.cp-body {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: .45rem;
  padding: .85rem;
  flex: 1;
  background: linear-gradient(160deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,.2) 100%);
}

.cp-header { display: grid; gap: .12rem; }
.cp-title-row { display: flex; align-items: flex-start; gap: .4rem; }
.cp-name { margin: 0; font-size: .95rem; font-weight: 700; flex: 1; min-width: 0; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.4); }
.cp-author { margin: 0; font-size: .74rem; color: rgba(255,255,255,.65); }

.cp-trust { flex: 0 0 auto; padding: .12rem .4rem; border-radius: 999px; font-size: .66rem; font-weight: 800; letter-spacing: .04em; }
.cp-trust-official { background: rgba(255,215,0,.25); color: #ffd700; border: 1px solid rgba(255,215,0,.4); }
.cp-trust-verified  { background: rgba(100,180,255,.2); color: #90caf9; border: 1px solid rgba(100,180,255,.35); }

.cp-desc { margin: 0; font-size: .8rem; line-height: 1.45; color: rgba(255,255,255,.75); display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }

.cp-stats { display: flex; gap: .8rem; font-size: .74rem; color: rgba(255,255,255,.6); }

.cp-tags { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: auto; }
/* Override NTag color in dark overlay context */
.cp-tags :deep(.n-tag) { background: rgba(255,255,255,.15) !important; color: rgba(255,255,255,.85) !important; border-color: rgba(255,255,255,.18) !important; }

.cp-actions { display: flex; gap: .4rem; margin-top: .5rem; }

/* Install progress bar */
.cp-install-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: rgba(255,255,255,.15);
  overflow: hidden;
  z-index: 2;
}
.cp-install-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--df-interactive), color-mix(in srgb, var(--df-interactive) 60%, #fff));
  animation: cp-progress 1.6s ease-in-out infinite;
  transform-origin: left;
}
@keyframes cp-progress {
  0%   { transform: translateX(-100%) scaleX(.4); }
  50%  { transform: translateX(30%) scaleX(.6); }
  100% { transform: translateX(200%) scaleX(.4); }
}
</style>
