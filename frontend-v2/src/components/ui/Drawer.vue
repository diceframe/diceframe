<script setup lang="ts">
import { onMounted, onUnmounted, ref, useId } from 'vue'
import { useLocale } from '@/composables/useLocale'

defineProps<{ title: string; drawerClass?: string; closeTestId?: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useLocale()
const titleId = useId()
const panel = ref<HTMLElement | null>(null)
let opener: HTMLElement | null = null

function onKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented) return
  if (event.key === 'Escape') { event.preventDefault(); emit('close') }
  if (event.key === 'Tab' && panel.value) {
    const controls = [...panel.value.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]'
    )].filter(el => el.getClientRects().length > 0)
    const first = controls[0]; const last = controls[controls.length - 1]
    if (!first) { event.preventDefault(); panel.value.focus() }
    else if (event.shiftKey && (document.activeElement === first || !panel.value.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && (document.activeElement === last || !panel.value.contains(document.activeElement))) { event.preventDefault(); first?.focus() }
  }
}

onMounted(() => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  document.addEventListener('keydown', onKeydown)
  document.body.style.overflow = 'hidden'
  panel.value?.focus()
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
  if (opener?.isConnected) opener.focus()
})
</script>

<template>
  <Teleport to="body">
    <div class="drawer-backdrop" data-df-ui="drawer" @click.self="emit('close')">
      <section
        ref="panel"
        class="drawer-panel"
        :class="drawerClass"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        tabindex="-1"
      >
        <header class="drawer-header">
          <h2 :id="titleId">{{ title }}</h2>
          <slot name="header-actions" />
          <button class="drawer-close" type="button" :data-testid="closeTestId" :title="t('close')" @click="emit('close')">✕</button>
        </header>
        <div class="drawer-body">
          <slot />
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 400;
  background: rgba(0, 0, 0, .45);
  backdrop-filter: blur(2px);
  display: flex;
  justify-content: flex-end;
  animation: drawer-fade-in .18s ease;
}
@keyframes drawer-fade-in { from { opacity: 0 } to { opacity: 1 } }

.drawer-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: min(1340px, calc(100vw - 48px));
  max-width: 100vw;
  height: 100vh;
  overflow: hidden;
  border-left: 1px solid var(--df-border-soft);
  background: var(--df-canvas);
  outline: none;
  animation: drawer-slide-in .22s cubic-bezier(.22, 1, .36, 1);
}
@keyframes drawer-slide-in { from { transform: translateX(40px); opacity: .6 } to { transform: translateX(0); opacity: 1 } }

.drawer-header {
  display: flex;
  align-items: center;
  gap: .75rem;
  padding: .75rem 1rem .75rem 1.25rem;
  border-bottom: 1px solid var(--df-border-soft);
  background: var(--df-surface-1);
  flex: 0 0 auto;
}
.drawer-header h2 {
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.drawer-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border: 0;
  border-radius: var(--df-radius-md);
  background: transparent;
  color: var(--df-text-muted);
  font-size: 1rem;
  cursor: pointer;
}
.drawer-close:hover { background: var(--df-surface-2); color: var(--df-text); }

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
