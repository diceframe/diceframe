<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAssistantWindow, type ResizeEdge } from '@/composables/useAssistantWindow'
import { useLocale } from '@/composables/useLocale'
import { creatorApi } from '@/api/creator'
import { detectProjectKind, computeContentOverview } from './contentOverview'
import CreatorAssistantPanel from './CreatorAssistantPanel.vue'

const props = defineProps<{
  projectId: string
  projectName: string
  projectRevision: number
  projectKind: string
  initialPrompt?: string
}>()
const emit = defineEmits<{ close: []; 'files-changed': [paths: string[]] }>()

const { locale } = useLocale()
const windowState = useAssistantWindow()
const { style: windowStyle, compact: windowCompact, wide: windowWide, startMove, startResize } = windowState
const expanded = ref(false)
const reviewRequest = ref(0)

const RESIZE_EDGES: ResizeEdge[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']

// ── Content overview ────────────────────────────────────────
const projectFiles = ref<Record<string, string> | null>(null)

async function loadProjectFiles() {
  if (!props.projectId) return
  try {
    const r = await creatorApi.read(props.projectId)
    projectFiles.value = r.project.files
  } catch { /* silent */ }
}

onMounted(loadProjectFiles)
watch(() => props.projectId, loadProjectFiles)
watch(() => props.projectRevision, loadProjectFiles)

const resolvedKind = computed(() =>
  projectFiles.value ? detectProjectKind(projectFiles.value) : props.projectKind || 'adventure',
)
const overviewData = computed(() => {
  if (!projectFiles.value) return null
  return computeContentOverview(projectFiles.value, resolvedKind.value, locale.value === 'zh-CN')
})

// ── Window interaction ──────────────────────────────────────
import type { CSSProperties } from 'vue'
const panelStyle: CSSProperties = { position: 'fixed', zIndex: 500, borderRadius: '12px', overflow: 'hidden', boxShadow: '0 24px 64px rgba(0,0,0,.48)', display: 'flex', flexDirection: 'column' }

function onWindowPointerDown(event: PointerEvent) {
  if (expanded.value) return
  const head = (event.target as HTMLElement | null)?.closest('.creator-window-drag-handle')
  if (head) startMove(event)
}
</script>

<template>
  <div
    class="creator-assistant-window"
    :style="windowCompact ? {} : { ...panelStyle, ...windowStyle }"
    :class="{ 'is-compact': windowCompact, 'is-expanded': expanded, 'has-overview': expanded && !!overviewData }"
    @pointerdown="onWindowPointerDown"
  >
    <!-- Resize handles -->
    <template v-if="!windowCompact && !expanded">
      <div
        v-for="edge in RESIZE_EDGES"
        :key="edge"
        :class="`resize-handle resize-${edge}`"
        @pointerdown.stop="startResize($event, edge)"
      />
    </template>

    <!-- Panel -->
    <CreatorAssistantPanel
      :project-id="projectId"
      :project-name="projectName"
      :project-revision="projectRevision"
      :project-kind="resolvedKind"
      :initial-prompt="initialPrompt"
      :expanded="expanded || windowWide"
      :wide="windowWide"
      :review-request="reviewRequest"
      class="creator-window-drag-handle"
      @close="emit('close')"
      @toggle-expanded="expanded = !expanded"
      @files-changed="loadProjectFiles"
    />

    <!-- Overview sidebar (expanded mode only) -->
    <aside v-if="expanded && overviewData" class="creator-window-overview">
      <h3 class="creator-overview-title">{{ locale === 'zh-CN' ? '内容总览' : 'Overview' }}</h3>
      <div class="creator-overview-stats">
        <div v-for="stat in overviewData.stats" :key="stat.label" class="creator-overview-stat">
          <span class="creator-overview-label">{{ stat.label }}</span>
          <span class="creator-overview-value">{{ stat.value }}</span>
        </div>
      </div>
      <ul v-if="overviewData.hints.length" class="creator-overview-hints">
        <li v-for="hint in overviewData.hints" :key="hint" class="creator-overview-hint">
          <span>💡</span>{{ hint }}
        </li>
      </ul>
    </aside>
  </div>
</template>

<style scoped>
.creator-assistant-window { --creator-window-radius: 12px; }

/* Compact = full-screen sheet on narrow screens */
.creator-assistant-window.is-compact {
  position: fixed;
  inset: 0;
  z-index: 500;
  border-radius: 0;
  display: flex;
  flex-direction: column;
}

.creator-assistant-window.is-expanded {
  position: fixed;
  inset: 24px;
  z-index: 500;
  border-radius: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 24px 64px rgba(0,0,0,.48);
}

/* When overview is visible, switch to side-by-side grid */
.creator-assistant-window.is-expanded.has-overview {
  display: grid;
  grid-template-columns: 1fr 240px;
}
.creator-assistant-window.is-expanded.has-overview .creator-window-drag-handle {
  min-height: 0;
  overflow: hidden;
}

/* Overview sidebar */
.creator-window-overview {
  display: flex;
  flex-direction: column;
  gap: .75rem;
  padding: 1rem;
  border-left: 1px solid var(--df-border-soft);
  background: var(--df-surface-1);
  overflow-y: auto;
}
.creator-overview-title {
  margin: 0;
  font-size: .78rem;
  font-weight: 700;
  color: var(--df-text-muted);
  letter-spacing: .06em;
  text-transform: uppercase;
}
.creator-overview-stats { display: grid; gap: .45rem; }
.creator-overview-stat {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: .5rem;
  padding: .38rem .6rem;
  border-radius: var(--df-radius-md);
  background: var(--df-surface-2);
}
.creator-overview-label { font-size: .8rem; color: var(--df-text-muted); }
.creator-overview-value { font-size: 1.05rem; font-weight: 700; color: var(--df-text); font-variant-numeric: tabular-nums; }
.creator-overview-hints { margin: 0; padding: 0; list-style: none; display: grid; gap: .4rem; }
.creator-overview-hint {
  display: flex;
  align-items: flex-start;
  gap: .4rem;
  font-size: .78rem;
  color: var(--df-text-muted);
  line-height: 1.45;
  padding: .32rem .5rem;
  border-radius: var(--df-radius-sm);
  background: color-mix(in srgb, var(--df-interactive) 6%, var(--df-surface-2));
}

/* Resize handles */
.resize-handle {
  position: absolute;
  z-index: 10;
  user-select: none;
}
.resize-n  { top: -4px;    left: 12px;   right: 12px;  height: 8px;  cursor: n-resize; }
.resize-s  { bottom: -4px; left: 12px;   right: 12px;  height: 8px;  cursor: s-resize; }
.resize-e  { right: -4px;  top: 12px;    bottom: 12px; width: 8px;   cursor: e-resize; }
.resize-w  { left: -4px;   top: 12px;    bottom: 12px; width: 8px;   cursor: w-resize; }
.resize-ne { top: -4px;    right: -4px;  width: 16px;  height: 16px; cursor: ne-resize; }
.resize-nw { top: -4px;    left: -4px;   width: 16px;  height: 16px; cursor: nw-resize; }
.resize-se { bottom: -4px; right: -4px;  width: 16px;  height: 16px; cursor: se-resize; }
.resize-sw { bottom: -4px; left: -4px;   width: 16px;  height: 16px; cursor: sw-resize; }
</style>
