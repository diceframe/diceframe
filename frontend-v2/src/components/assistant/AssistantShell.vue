<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, type CSSProperties } from 'vue'
import AssistantPanel from '@/components/AssistantPanel.vue'
import AssistantAvatar, { type AssistantAvatarMood } from '@/components/assistant/AssistantAvatar.vue'
import AssistantGreetingBubble from '@/features/overview/AssistantGreetingBubble.vue'
import { useAssistantPreferences } from '@/composables/useAssistantPreferences'
import { useAssistantLauncherPosition } from '@/composables/useAssistantLauncherPosition'
import { useAssistantWindow, type ResizeEdge } from '@/composables/useAssistantWindow'
import { useAssistant } from '@/composables/useAssistant'
import { useAssistantVoice } from '@/composables/useAssistantVoice'
import { onAssistantReaction, useAssistantReactions } from '@/composables/useAssistantReactions'
import { ASSISTANT_REACTIONS } from '@/features/assistant/reactions'
import { useLocale } from '@/composables/useLocale'
import { assistantAuthorizationCopy } from '@/features/assistant/authorizationCopy'

withDefaults(defineProps<{ isHome?: boolean }>(), { isHome: false })

const { t, locale } = useLocale()
const authorizationCopy = computed(() => assistantAuthorizationCopy(locale.value))
const { enabled, reactionSpeech } = useAssistantPreferences()
const {
  launcherRef,
  style: launcherStyle,
  dragging: launcherDragging,
  onPointerDown,
  onPointerMove,
  onPointerUp: finishLauncherPointer,
  onPointerCancel,
  consumeClick,
} = useAssistantLauncherPosition()
const { streaming, actionBusy, pendingApproval, actionError, restoreSession } = useAssistant()
const { listening, speaking, speak, cancelListening } = useAssistantVoice()
const { reaction } = useAssistantReactions()
const reactionSpec = computed(() => reaction.value ? ASSISTANT_REACTIONS[reaction.value.kind] : null)
const reactionLabel = computed(() => reaction.value && reactionSpec.value
  ? reactionSpec.value.label(locale.value.startsWith('zh'), reaction.value.detail)
  : '')
// Quiet, frequent beats are shown but never read aloud.
const UNSPOKEN = new Set(['diceRoll', 'newRound', 'sceneImage'])
const stopSpeakingReactions = onAssistantReaction((next) => {
  if (!reactionSpeech.value || UNSPOKEN.has(next.kind) || speaking.value || listening.value) return
  speak(ASSISTANT_REACTIONS[next.kind].label(locale.value.startsWith('zh'), next.detail), locale.value)
})
onBeforeUnmount(stopSpeakingReactions)
const open = ref(false)
const expanded = ref(false)
const hovered = ref(false)
const avatarMood = computed<AssistantAvatarMood>(() => {
  if (launcherDragging.value) return 'surprised'
  if (listening.value) return 'listening'
  if (reactionSpec.value) return reactionSpec.value.mood
  if (speaking.value) return 'speaking'
  if (streaming.value || actionBusy.value) return 'thinking'
  if (pendingApproval.value || actionError.value) return 'attention'
  if (hovered.value) return 'happy'
  return 'idle'
})
const windowState = useAssistantWindow()
const { style: windowStyle, compact: windowCompact, wide: windowWide, interacting: windowInteracting, startMove, startResize } = windowState
const RESIZE_EDGES: ResizeEdge[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
// Mount the panel on first open and keep it alive afterwards, so drafts and
// scroll position survive closing and maximizing the window.
const mounted = ref(false)
const reviewRequest = ref(0)
const panelStyle = computed<CSSProperties>(() => {
  if (windowCompact.value) return {}
  if (!expanded.value) return windowStyle.value
  return {
    width: 'min(1060px, calc(100vw - 48px))',
    height: 'min(800px, calc(100dvh - 48px))',
    left: 'max(24px, calc((100vw - 1060px) / 2))',
    top: 'max(24px, calc((100dvh - 800px) / 2))',
  }
})
function onWindowPointerDown(event: PointerEvent) {
  if (expanded.value) return
  const head = (event.target as HTMLElement | null)?.closest('.assistant-panel-head')
  if (head) startMove(event)
}
function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && !event.defaultPrevented) {
    if (expanded.value) expanded.value = false
    else open.value = false
  }
}

function openAssistant() {
  if (!enabled.value) return
  mounted.value = true
  open.value = true
  if (pendingApproval.value) reviewRequest.value += 1
}

// The launcher toggles the window, like a support chat bubble.
function toggleAssistant() {
  if (open.value) open.value = false
  else openAssistant()
}

function onLauncherClick(event: MouseEvent) {
  if (consumeClick(event)) return
  toggleAssistant()
}

function onLauncherPointerUp(event: PointerEvent) {
  if (finishLauncherPointer(event)) toggleAssistant()
}

watch(enabled, (isEnabled) => {
  if (!isEnabled) open.value = false
  else if (!streaming.value && !actionBusy.value) void restoreSession()
}, { immediate: true })
// The panel is intentionally kept mounted while closed so drafts and scroll
// position survive.  Closing therefore must cancel any microphone session
// explicitly instead of relying on AssistantPanel's unmount hook.
watch(open, (isOpen) => {
  if (!isOpen) cancelListening()
})
</script>

<template>
  <div v-if="enabled" class="assistant-shell" data-testid="assistant-shell">
    <Transition name="assistant-shell-window">
      <section
        v-if="mounted"
        v-show="open"
        class="assistant-shell-drawer"
        :class="{
          'assistant-shell-expanded': expanded,
          'assistant-shell-compact': windowCompact,
          'assistant-shell-interacting': windowInteracting,
          'assistant-shell-moving': windowInteracting === 'move',
        }"
        :style="panelStyle"
        role="dialog"
        :aria-label="t('assistantTitle')"
        data-testid="assistant-window"
        @pointerdown="onWindowPointerDown"
        @keydown="onWindowKeydown"
      >
        <AssistantPanel
          :expanded="expanded"
          :wide="windowWide"
          :review-request="reviewRequest"
          @toggle-expanded="expanded = !expanded"
          @close="open = false"
        />
        <template v-if="!windowCompact && !expanded">
          <span
            v-for="edge in RESIZE_EDGES"
            :key="edge"
            class="assistant-shell-resize"
            :class="`assistant-shell-resize-${edge}`"
            aria-hidden="true"
            @pointerdown="startResize($event, edge)"
          />
        </template>
      </section>
    </Transition>

    <AssistantGreetingBubble v-if="isHome" @open="openAssistant" />
    <button
      ref="launcherRef"
      type="button"
      class="assistant-shell-fab"
      :class="{ 'assistant-shell-fab-dragging': launcherDragging }"
      data-testid="assistant-shell-fab"
      data-tour="assistant"
      :data-mood="avatarMood"
      :style="launcherStyle"
      :aria-label="t('toggleAssistant')"
      :aria-expanded="open"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onLauncherPointerUp"
      @pointercancel="onPointerCancel"
      @lostpointercapture="onPointerCancel"
      @pointerenter="hovered = true"
      @pointerleave="hovered = false"
      @click="onLauncherClick"
    >
      <AssistantAvatar :key="reaction?.id ?? 0" :mood="avatarMood" />
      <span v-if="pendingApproval" class="assistant-shell-pending-badge" :title="authorizationCopy.reviewPending" :aria-label="authorizationCopy.pendingApproval">1</span>
      <Transition name="assistant-shell-bubble">
        <span
          v-if="reactionLabel"
          :key="reaction?.id"
          class="assistant-shell-reaction"
          :class="`assistant-shell-reaction-${reactionSpec?.tone || 'plain'}`"
          role="status"
        >{{ reactionLabel }}</span>
      </Transition>
    </button>
  </div>
</template>

<style scoped>
.assistant-shell {
  position: relative;
  z-index: 80;
  --assistant-shell-entry-bottom: calc(24px + env(safe-area-inset-bottom));
}

.assistant-shell-drawer {
  position: fixed;
  z-index: 85;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--df-interactive) 30%, var(--df-border-soft));
  border-radius: 18px;
  background: var(--df-surface-1);
  box-shadow: 0 24px 60px rgb(0 0 0 / 0.45), 0 4px 14px rgb(0 0 0 / 0.3);
  transform-origin: 92% 100%;
}

.assistant-shell-drawer > :deep(.assistant-panel) { flex: 1 1 auto; min-height: 0; }
.assistant-shell-drawer:not(.assistant-shell-expanded):not(.assistant-shell-compact) :deep(.assistant-panel-head) { cursor: grab; }
.assistant-shell-moving :deep(.assistant-panel-head) { cursor: grabbing; }
.assistant-shell-interacting { user-select: none; }
.assistant-shell-expanded { transition: left 0.25s ease, top 0.25s ease, width 0.25s ease, height 0.25s ease; }

.assistant-shell-compact {
  inset: 0;
  border: 0;
  border-radius: 0;
}

.assistant-shell-resize { position: absolute; z-index: 5; touch-action: none; }
.assistant-shell-resize-n, .assistant-shell-resize-s { left: 14px; right: 14px; height: 8px; cursor: ns-resize; }
.assistant-shell-resize-e, .assistant-shell-resize-w { top: 14px; bottom: 14px; width: 8px; cursor: ew-resize; }
.assistant-shell-resize-n { top: -3px; }
.assistant-shell-resize-s { bottom: -3px; }
.assistant-shell-resize-e { right: -3px; }
.assistant-shell-resize-w { left: -3px; }
.assistant-shell-resize-ne, .assistant-shell-resize-nw, .assistant-shell-resize-se, .assistant-shell-resize-sw { width: 16px; height: 16px; }
.assistant-shell-resize-ne { top: -3px; right: -3px; cursor: nesw-resize; }
.assistant-shell-resize-sw { bottom: -3px; left: -3px; cursor: nesw-resize; }
.assistant-shell-resize-nw { top: -3px; left: -3px; cursor: nwse-resize; }
.assistant-shell-resize-se { bottom: -3px; right: -3px; cursor: nwse-resize; }
/* A visible grip in the corner people reach for first. */
.assistant-shell-resize-se::after {
  content: "";
  position: absolute;
  right: 6px;
  bottom: 6px;
  width: 8px;
  height: 8px;
  border-right: 2px solid color-mix(in srgb, var(--df-text-muted) 70%, transparent);
  border-bottom: 2px solid color-mix(in srgb, var(--df-text-muted) 70%, transparent);
  border-radius: 0 0 3px 0;
}

.assistant-shell-window-enter-active,
.assistant-shell-window-leave-active { transition: opacity 0.18s ease, transform 0.24s cubic-bezier(0.34, 1.3, 0.64, 1); }
.assistant-shell-window-enter-from,
.assistant-shell-window-leave-to { opacity: 0; transform: translateY(12px) scale(0.96); }

.assistant-shell-fab {
  position: fixed;
  right: 18px;
  bottom: var(--assistant-shell-entry-bottom);
  z-index: 80;
  width: 56px;
  height: 56px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: grab;
  touch-action: none;
  -webkit-tap-highlight-color: transparent;
}

.assistant-shell-fab-dragging {
  cursor: grabbing;
}

.assistant-shell-reaction {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  padding: 4px 10px;
  border: 1px solid rgb(255 255 255 / 0.28);
  border-radius: 999px;
  color: var(--df-text, #e9eef5);
  background: color-mix(in srgb, var(--df-surface-2) 55%, transparent);
  backdrop-filter: blur(10px) saturate(170%);
  -webkit-backdrop-filter: blur(10px) saturate(170%);
  box-shadow: 0 6px 18px rgb(0 0 0 / 0.28), inset 0 1px 0 rgb(255 255 255 / 0.25);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  transform: translateX(-50%);
  pointer-events: none;
}

.assistant-shell-reaction-great { color: var(--df-accent-strong); border-color: color-mix(in srgb, var(--df-accent-strong) 60%, transparent); }
.assistant-shell-reaction-good { color: var(--df-success, #8fd19e); }
.assistant-shell-reaction-bad { color: var(--df-danger, #e57373); }

.assistant-shell-bubble-enter-active,
.assistant-shell-bubble-leave-active { transition: opacity 0.25s ease, transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1); }
.assistant-shell-bubble-enter-from,
.assistant-shell-bubble-leave-to { opacity: 0; transform: translate(-50%, 6px) scale(0.9); }

.assistant-shell-fab:focus-visible {
  outline: 2px solid var(--df-interactive-strong);
  outline-offset: 4px;
}
.assistant-shell-pending-badge { position: absolute; top: -2px; right: -2px; display: grid; place-items: center; min-width: 19px; height: 19px; padding: 0 4px; border: 2px solid var(--df-surface-1); border-radius: 999px; color: var(--df-on-accent); background: var(--df-danger-strong, #c94b55); font-size: 10px; font-weight: 800; pointer-events: none; }

/* The owner play surface has a bottom composer on narrow screens. Keep the
   global entry above that operation area while preserving a comfortable tap
   target on the rest of the app. */
@media (max-width: 800px) {
  .assistant-shell {
    --assistant-shell-entry-bottom: calc(76px + env(safe-area-inset-bottom));
  }

  .assistant-shell-fab {
    right: 14px;
    bottom: var(--assistant-shell-entry-bottom);
    width: 50px;
    height: 50px;
  }

  :global(.app-shell-play) .assistant-shell-fab {
    bottom: calc(92px + env(safe-area-inset-bottom));
  }

  :global(.app-shell-play) .assistant-shell {
    --assistant-shell-entry-bottom: calc(92px + env(safe-area-inset-bottom));
  }
}
</style>
