<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { NIcon, NModal } from 'naive-ui'
import {
  ArrowBackOutline,
  CloseOutline,
  FlaskOutline,
  LinkOutline,
  PeopleOutline,
  ShieldCheckmarkOutline,
  WarningOutline,
} from '@vicons/ionicons5'
import { useLocale } from '@/composables/useLocale'
import PeerConnectView from './PeerConnectView.vue'
import FriendJoinPanel from './FriendJoinPanel.vue'

const props = defineProps<{ show: boolean; peerAvailable: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean] }>()
const { t } = useLocale()
const step = ref<'join' | 'intro' | 'connect'>('join')
const connectMode = ref<'host' | 'guest'>('host')
const guestInvite = ref('')
const connectVisited = ref(false)
const connectRevision = ref(0)
const modalElement = ref<HTMLElement | null>(null)

watch(step, async value => {
  await nextTick()
  const selector = value === 'join' ? '.friend-join-panel textarea' : value === 'intro' ? '.peer-intro-actions button' : '.peer-modal-back'
  modalElement.value?.querySelector<HTMLElement>(selector)?.focus()
})

watch(() => props.show, (show) => {
  if (show) {
    step.value = 'join'
    connectVisited.value = false
    guestInvite.value = ''
  }
})

function openHost() {
  connectMode.value = 'host'
  guestInvite.value = ''
  step.value = 'intro'
}

function openPeer(code: string) {
  connectMode.value = 'guest'
  guestInvite.value = code
  connectVisited.value = true
  connectRevision.value += 1
  step.value = 'connect'
}

function openSetup() {
  connectVisited.value = true
  connectRevision.value += 1
  step.value = 'connect'
}

function close(): void {
  emit('update:show', false)
}
</script>

<template>
  <NModal
    :show="show"
    display-directive="if"
    :auto-focus="false"
    class="peer-connect-modal-host"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <!-- 弹窗根节点必须是 div：naive-ui FocusTrap 用「起始哨兵后第一个 DIV 兄弟节点」
         识别受控内容，section 会让它误认末尾哨兵为内容，弹窗内所有输入框焦点被抢、无法输入。 -->
    <div
      ref="modalElement"
      class="peer-connect-modal"
      :class="{ 'peer-connect-modal-join': step === 'join' }"
      role="dialog"
      aria-modal="true"
      :aria-label="t(step === 'join' ? 'friendJoinTitle' : 'peerLaunchModalTitle')"
    >
      <button class="peer-modal-close" :aria-label="t('close')" @click="close">
        <NIcon :component="CloseOutline" />
      </button>

      <FriendJoinPanel v-if="show" v-show="step === 'join'" :peer-available="peerAvailable" @peer="openPeer" @host="openHost" />

      <div v-show="step === 'intro'" class="peer-intro">
        <div class="peer-intro-visual" aria-hidden="true">
          <span class="peer-intro-orbit peer-intro-orbit-one" />
          <span class="peer-intro-orbit peer-intro-orbit-two" />
          <NIcon :component="PeopleOutline" />
        </div>
        <div class="peer-intro-copy">
          <span class="peer-experimental-badge"><NIcon :component="FlaskOutline" />{{ t('peerExperimentalBadge') }}</span>
          <p class="section-kicker">{{ t('peerKicker') }}</p>
          <h2>{{ t('peerIntroTitle') }}</h2>
          <p class="peer-intro-lead">{{ t('peerIntroDescription') }}</p>

          <div class="peer-intro-points">
            <article>
              <NIcon :component="LinkOutline" />
              <div><strong>{{ t('peerIntroSignalTitle') }}</strong><span>{{ t('peerIntroSignalText') }}</span></div>
            </article>
            <article>
              <NIcon :component="ShieldCheckmarkOutline" />
              <div><strong>{{ t('peerIntroDirectTitle') }}</strong><span>{{ t('peerIntroDirectText') }}</span></div>
            </article>
            <article class="warning">
              <NIcon :component="WarningOutline" />
              <div><strong>{{ t('peerIntroLimitTitle') }}</strong><span>{{ t('peerIntroLimitText') }}</span></div>
            </article>
          </div>

          <p class="peer-experimental-note">{{ t('peerExperimentalNote') }}</p>
          <div class="peer-intro-actions">
            <button @click="step = 'join'">{{ t('friendJoinBack') }}</button>
            <button class="primary" @click="openSetup">{{ t('peerStartSetup') }}</button>
          </div>
        </div>
      </div>

      <div v-show="step === 'connect'" class="peer-connect-step">
        <header class="peer-modal-toolbar">
          <button class="peer-modal-back" @click="step = connectMode === 'guest' ? 'join' : 'intro'">
            <NIcon :component="ArrowBackOutline" />{{ t(connectMode === 'guest' ? 'friendJoinBack' : 'peerIntroBack') }}
          </button>
          <div>
            <h2>{{ t('peerLaunchModalTitle') }}</h2>
            <span class="peer-experimental-badge"><NIcon :component="FlaskOutline" />{{ t('peerExperimentalBadge') }}</span>
          </div>
        </header>
        <div class="peer-modal-scroll">
          <PeerConnectView v-if="show && connectVisited" :key="connectRevision" embedded :initial-mode="connectMode" :initial-invite="guestInvite" />
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.peer-connect-modal {
  position: relative;
  width: min(1120px, calc(100vw - 32px));
  max-height: min(900px, calc(100dvh - 32px));
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--df-accent) 32%, var(--df-border));
  border-radius: 24px;
  background:
    radial-gradient(circle at 8% 12%, color-mix(in srgb, var(--df-accent) 14%, transparent), transparent 32%),
    var(--df-surface-1);
  color: var(--df-text);
  box-shadow: 0 28px 90px color-mix(in srgb, #000 42%, transparent);
}
.peer-connect-modal-join { width: min(600px, calc(100vw - 32px)); overflow-y: auto; }

.peer-modal-close {
  position: absolute;
  z-index: 4;
  top: 16px;
  right: 16px;
  width: 38px;
  height: 38px;
  padding: 0;
  justify-content: center;
  border-radius: 50%;
  background: color-mix(in srgb, var(--df-surface-2) 88%, transparent);
}

.peer-intro {
  display: grid;
  grid-template-columns: minmax(240px, .72fr) minmax(0, 1.28fr);
  min-height: 610px;
}

.peer-intro-visual {
  position: relative;
  display: grid;
  place-items: center;
  overflow: hidden;
  border-right: 1px solid var(--df-border-soft);
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--df-accent) 17%, var(--df-surface-2)), var(--df-surface-2));
}

.peer-intro-visual > .n-icon {
  z-index: 2;
  display: grid;
  width: 112px;
  height: 112px;
  place-items: center;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--df-accent) 55%, transparent);
  border-radius: 28px;
  color: var(--df-accent-strong);
  background: color-mix(in srgb, var(--df-surface-1) 86%, transparent);
  font-size: 40px;
  line-height: 1;
  box-shadow: 0 18px 48px color-mix(in srgb, var(--df-accent) 22%, transparent);
}

.peer-intro-visual > .n-icon :deep(svg) {
  display: block;
  width: 1em;
  height: 1em;
}

.peer-intro-orbit {
  position: absolute;
  border: 1px solid color-mix(in srgb, var(--df-accent) 30%, transparent);
  border-radius: 50%;
}
.peer-intro-orbit-one { width: 260px; height: 260px; }
.peer-intro-orbit-two { width: 390px; height: 390px; opacity: .55; }

.peer-intro-copy {
  align-self: center;
  padding: clamp(38px, 6vw, 72px);
}

.peer-experimental-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--df-warning) 48%, var(--df-border));
  border-radius: 999px;
  color: var(--df-warning);
  background: color-mix(in srgb, var(--df-warning) 10%, transparent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .04em;
}

.peer-intro-copy .section-kicker { margin: 22px 0 8px; }
.peer-intro-copy h2,
.peer-modal-toolbar h2 {
  margin: 0;
  font: 750 clamp(28px, 4vw, 44px)/1.08 var(--df-font-display);
}
.peer-intro-lead { margin: 16px 0 24px; color: var(--df-text-muted); font-size: 16px; line-height: 1.75; }

.peer-intro-points { display: grid; gap: 10px; }
.peer-intro-points article {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md);
  background: color-mix(in srgb, var(--df-surface-2) 82%, transparent);
}
.peer-intro-points article > .n-icon { margin-top: 2px; color: var(--df-accent-strong); font-size: 22px; }
.peer-intro-points article.warning > .n-icon { color: var(--df-warning); }
.peer-intro-points article div { display: grid; gap: 3px; }
.peer-intro-points article span { color: var(--df-text-muted); font-size: 13px; line-height: 1.5; }
.peer-experimental-note { margin: 18px 0 0; color: var(--df-text-muted); font-size: 12px; line-height: 1.55; }
.peer-intro-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; }

/* 高度随内容生长、仅用 max-height 封顶：没生成码时弹窗收缩紧凑，
   多码时内部滚动不外撑；固定 height 会导致短内容时弹窗底部大片空白。 */
.peer-connect-step {
  display: flex;
  max-height: min(900px, calc(100dvh - 32px));
  min-height: 0;
  flex-direction: column;
}
.peer-modal-toolbar {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 18px;
  padding: 10px 68px 10px 20px;
  border-bottom: 1px solid var(--df-border-soft);
  background: color-mix(in srgb, var(--df-surface-2) 88%, transparent);
}
.peer-modal-toolbar > div { display: flex; align-items: center; gap: 12px; }
.peer-modal-toolbar h2 { font-size: clamp(20px, 3vw, 28px); }
.peer-modal-back { flex: 0 0 auto; }
.peer-modal-scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
  overscroll-behavior: contain;
}

@media (max-width: 760px) {
  .peer-connect-modal {
    width: 100vw;
    max-height: 100dvh;
    border: 0;
    border-radius: 0;
  }
  .peer-connect-modal-join { width: calc(100vw - 24px); max-height: calc(100dvh - 24px); border: 1px solid var(--df-border); border-radius: 20px; }
  .peer-intro {
    display: block;
    height: 100dvh;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding-bottom: env(safe-area-inset-bottom);
  }
  .peer-intro-visual { min-height: 180px; border-right: 0; border-bottom: 1px solid var(--df-border-soft); }
  .peer-intro-orbit-one { width: 170px; height: 170px; }
  .peer-intro-orbit-two { width: 260px; height: 260px; }
  .peer-intro-visual > .n-icon { width: 78px; height: 78px; border-radius: 22px; font-size: 32px; }
  .peer-intro-copy { padding: 28px 18px calc(24px + env(safe-area-inset-bottom)); }
  .peer-intro-copy .section-kicker { margin-top: 16px; }
  .peer-intro-actions { position: sticky; bottom: 0; padding-top: 14px; padding-bottom: env(safe-area-inset-bottom); background: var(--df-surface-1); }
  .peer-intro-actions button { flex: 1; justify-content: center; }
  .peer-connect-step { height: 100dvh; }
  .peer-modal-toolbar {
    min-height: 76px;
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
    padding: 10px 62px 10px 12px;
  }
  .peer-modal-toolbar > div { flex-wrap: wrap; gap: 6px 10px; }
  .peer-modal-toolbar h2 { font-size: 20px; }
  .peer-modal-back { min-height: 30px; padding: 4px 8px; }
  .peer-modal-scroll { padding-bottom: env(safe-area-inset-bottom); }
}
</style>
