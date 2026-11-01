<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { AppConfig } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import Button from '@/components/ui/Button.vue'
import { isLlmConfigReady } from '@/utils/modelConfiguration'
import { gettingStartedCopy } from './copy'

const props = withDefaults(defineProps<{
  hasGames?: boolean
}>(), {
  hasGames: false,
})

const emit = defineEmits<{ connect: []; dismiss: [] }>()
const { locale } = useLocale()
const copy = computed(() => gettingStartedCopy(locale.value))
const collapsedKey = 'diceframe:onboarding:collapsed:v1'

function readCollapsed(): boolean {
  try {
    const stored = localStorage.getItem(collapsedKey)
    if (stored === '1') return true
    if (stored === '0') return false
  } catch {
    // Storage can be blocked by browser policy; the in-memory state still works.
  }
  return props.hasGames
}

function persistCollapsed(value: boolean): void {
  try {
    localStorage.setItem(collapsedKey, value ? '1' : '0')
  } catch {
    // A failed write should never prevent toggling this card for this visit.
  }
}

const isCollapsed = ref(readCollapsed())
const detail = ref<'solo' | 'friends' | null>(null)
const configState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const configReady = ref(false)
const configError = ref('')
const expandButton = ref<HTMLElement | null>(null)
const startButton = ref<HTMLElement | null>(null)
const detailPanel = ref<HTMLElement | null>(null)
let disposed = false

function focusDetail(): void {
  void nextTick(() => detailPanel.value?.focus())
}

function expand(): void {
  isCollapsed.value = false
  persistCollapsed(false)
  void nextTick(() => startButton.value?.focus())
}

function collapse(): void {
  detail.value = null
  isCollapsed.value = true
  persistCollapsed(true)
  void nextTick(() => expandButton.value?.focus())
}

async function loadConfig(): Promise<void> {
  if (configState.value === 'loading') return
  configState.value = 'loading'
  configError.value = ''
  try {
    const config = await api<AppConfig>('/config')
    if (disposed) return
    configReady.value = isLlmConfigReady(config)
    configState.value = 'ready'
  } catch (error: unknown) {
    if (disposed) return
    configReady.value = false
    configError.value = errorMessage(error)
    configState.value = 'error'
  }
}

function openSolo(): void {
  detail.value = 'solo'
  focusDetail()
  void loadConfig()
}

function openFriends(): void {
  detail.value = 'friends'
  focusDetail()
}

function dismiss(): void {
  emit('dismiss')
}

onUnmounted(() => { disposed = true })

defineExpose({ expand })
</script>

<template>
  <section v-if="isCollapsed" id="getting-started-card" class="getting-started-collapsed" data-testid="getting-started-collapsed">
    <span class="getting-started-collapsed-mark" aria-hidden="true">+</span>
    <span class="getting-started-collapsed-title">{{ copy.kicker }} · {{ copy.title }}</span>
    <span class="getting-started-collapsed-actions">
      <button ref="expandButton" type="button" class="getting-started-expand-button" data-df-ui="button" data-testid="getting-started-expand" :aria-expanded="false" aria-controls="getting-started-card" @click="expand">{{ copy.expand }}</button>
      <button type="button" class="getting-started-close" data-testid="getting-started-close" :aria-label="copy.close" :title="copy.close" @click="dismiss">×</button>
    </span>
  </section>

  <section v-else id="getting-started-card" class="getting-started-card" data-testid="getting-started-card" aria-labelledby="getting-started-title">
    <div class="getting-started-controls">
      <button type="button" class="getting-started-collapse" data-df-ui="button" data-testid="getting-started-collapse" :aria-expanded="true" aria-controls="getting-started-details" @click="collapse">{{ copy.collapse }}</button>
      <button type="button" class="getting-started-close" data-testid="getting-started-close" :aria-label="copy.close" :title="copy.close" @click="dismiss">×</button>
    </div>
    <div class="getting-started-layout">
      <div class="getting-started-intro">
        <span class="getting-started-kicker">{{ copy.kicker }}</span>
        <h2 id="getting-started-title">{{ copy.title }}</h2>
        <p>{{ copy.description }}</p>
        <div class="getting-started-actions">
          <button ref="startButton" type="button" class="getting-started-action getting-started-primary" data-df-ui="button" data-testid="getting-started-start" :aria-expanded="detail === 'solo'" aria-controls="getting-started-details" @click="openSolo">
            {{ copy.start }}
          </button>
          <Button variant="outline" size="lg" data-testid="getting-started-friends" :aria-expanded="detail === 'friends'" aria-controls="getting-started-details" @click="openFriends">
            {{ copy.friends }}
          </Button>
        </div>
      </div>

      <ol class="getting-started-steps" :aria-label="copy.title">
        <li><span class="step-number">1</span><div><span>{{ copy.stepOne }}</span><strong>{{ copy.stepOneTitle }}</strong></div></li>
        <li><span class="step-number">2</span><div><span>{{ copy.stepTwo }}</span><strong>{{ copy.stepTwoTitle }}</strong></div></li>
        <li><span class="step-number">3</span><div><span>{{ copy.stepThree }}</span><strong>{{ copy.stepThreeTitle }}</strong></div></li>
      </ol>
    </div>

    <div id="getting-started-details" ref="detailPanel" v-show="detail" class="getting-started-details" :aria-hidden="!detail" tabindex="-1" aria-live="polite">
      <div v-if="detail === 'solo'" class="getting-started-detail-content">
        <div>
          <span class="getting-started-kicker">{{ copy.stepOne }}</span>
          <h3>{{ copy.soloTitle }}</h3>
          <p>{{ copy.soloIntro }}</p>
        </div>
        <div class="getting-started-status">
          <span>{{ copy.readyLabel }}</span>
          <strong v-if="configState === 'loading'">{{ copy.loading }}</strong>
          <strong v-else-if="configState === 'ready'" :class="configReady ? 'is-ready' : 'is-pending'">{{ configReady ? copy.configured : copy.notConfigured }}</strong>
        </div>
        <div v-if="configState === 'error'" role="alert" class="getting-started-error">
          <span>{{ copy.configError }}<template v-if="configError"> {{ configError }}</template></span>
          <Button variant="outline" size="sm" data-testid="getting-started-retry" @click="loadConfig">{{ copy.retry }}</Button>
        </div>
        <div class="getting-started-detail-actions">
          <RouterLink data-testid="getting-started-settings" class="getting-started-link" :to="{ name: 'settings', query: { section: 'api' } }">{{ copy.settings }}</RouterLink>
          <RouterLink data-testid="getting-started-models" class="getting-started-link" :to="{ name: 'settings', query: { section: 'models' } }">{{ copy.settingsModels }}</RouterLink>
          <RouterLink data-testid="getting-started-create" class="getting-started-link primary" :to="{ name: 'create' }">{{ copy.create }}</RouterLink>
        </div>
        <p class="getting-started-note">{{ copy.testConnection }} {{ copy.mediaHint }}</p>
      </div>

      <div v-else class="getting-started-detail-content">
        <div>
          <span class="getting-started-kicker">{{ copy.stepTwo }}</span>
          <h3>{{ copy.friendsTitle }}</h3>
          <p>{{ copy.friendsIntro }}</p>
        </div>
        <ol class="friends-steps">
          <li>{{ copy.hostInvite }}</li>
          <li>{{ copy.playerJoin }}</li>
        </ol>
        <Button variant="primary" size="lg" data-testid="getting-started-connect" @click="emit('connect')">{{ copy.joinFriends }}</Button>
      </div>

      <div class="getting-started-example">
        <span class="getting-started-kicker">{{ copy.stepThree }}</span>
        <h3>{{ copy.stepThreeTitle }}</h3>
        <p>{{ copy.actionInstruction }}</p>
        <blockquote>{{ copy.actionExample }}</blockquote>
        <p>{{ copy.resultHint }}</p>
        <p>{{ copy.resultExplanation }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.getting-started-card,
.getting-started-collapsed {
  position: relative;
  width: 100%;
  margin-bottom: 20px;
  overflow: hidden;
  box-sizing: border-box;
  border: 1px solid color-mix(in srgb, var(--df-accent) 34%, var(--df-border-soft));
  border-radius: var(--df-radius-lg);
  background: linear-gradient(125deg, var(--df-surface-1), color-mix(in srgb, var(--df-surface-raised) 88%, var(--df-canvas)));
  box-shadow: var(--df-shadow);
}

.getting-started-card::after {
  position: absolute;
  top: -74px;
  right: -52px;
  width: 210px;
  height: 210px;
  border: 1px solid color-mix(in srgb, var(--df-accent) 18%, transparent);
  border-radius: 50%;
  content: '';
  pointer-events: none;
  box-shadow: 0 0 0 15px color-mix(in srgb, var(--df-accent) 5%, transparent), 0 0 0 30px color-mix(in srgb, var(--df-accent) 4%, transparent);
}

.getting-started-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(250px, .85fr); gap: clamp(24px, 5vw, 72px); padding: 32px clamp(24px, 3vw, 40px); }
.getting-started-intro { position: relative; z-index: 1; min-width: 0; }
.getting-started-kicker { color: var(--df-accent-strong); font-size: 11px; font-weight: 700; letter-spacing: .13em; text-transform: uppercase; }
h2, h3, p { margin: 0; }
.getting-started-intro h2 { margin-top: 8px; color: var(--df-text); font-family: var(--df-font-title); font-size: clamp(26px, 3vw, 34px); line-height: 1.2; }
.getting-started-intro p { max-width: 540px; margin-top: 12px; color: var(--df-text-secondary); line-height: 1.65; }
.getting-started-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
.getting-started-actions > button { min-width: 148px; min-height: 46px; padding: 0 20px; font-size: 14px; white-space: normal; }
.getting-started-action { display: inline-flex; min-width: 148px; min-height: 46px; align-items: center; justify-content: center; padding: 0 20px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); color: var(--df-text); background: var(--df-surface-2); font: 650 14px/1.2 var(--df-font-body); cursor: pointer; }
.getting-started-action:hover { border-color: var(--df-interactive); }
.getting-started-primary { border-color: color-mix(in srgb, var(--df-accent) 70%, var(--df-border-soft)); color: var(--df-on-accent); background: var(--df-accent); }
.getting-started-primary:hover { border-color: var(--df-accent-strong); background: var(--df-accent-strong); }
.getting-started-controls { position: absolute; z-index: 2; top: 10px; right: 14px; display: flex; align-items: center; gap: 4px; }
.getting-started-collapse { min-height: 44px; padding: 4px; border: 0; color: var(--df-text-muted); background: transparent; font-size: 12px; cursor: pointer; }
.getting-started-collapse:hover { color: var(--df-text); }
.getting-started-close { display: inline-grid; width: 44px; height: 44px; min-width: 44px; place-items: center; padding: 0; border: 1px solid var(--df-border-soft); border-radius: 50%; color: var(--df-text-muted); background: var(--df-surface-2); font-size: 22px; line-height: 1; cursor: pointer; }
.getting-started-close:hover { border-color: var(--df-interactive); color: var(--df-interactive-strong); }
.getting-started-collapse:focus-visible, .getting-started-action:focus-visible, .getting-started-link:focus-visible, .getting-started-close:focus-visible, .getting-started-expand-button:focus-visible { outline: 2px solid var(--df-interactive-strong); outline-offset: 3px; }
.getting-started-steps { position: relative; z-index: 1; display: grid; gap: 16px; align-content: center; margin: 0; padding: 4px 0; list-style: none; }
.getting-started-steps li { display: grid; grid-template-columns: 36px minmax(0, 1fr); align-items: center; gap: 12px; min-width: 0; }
.getting-started-steps li + li { position: relative; }
.getting-started-steps li + li::before { position: absolute; top: -17px; left: 17px; height: 17px; border-left: 1px solid color-mix(in srgb, var(--df-accent) 40%, var(--df-border-soft)); content: ''; }
.step-number { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid var(--df-accent); border-radius: 50%; color: var(--df-accent-strong); background: var(--df-surface-2); font-family: var(--df-font-title); }
.getting-started-steps li div { display: grid; gap: 3px; min-width: 0; }
.getting-started-steps li div span { color: var(--df-text-muted); font-size: 11px; }
.getting-started-steps li strong { color: var(--df-text); font-size: 14px; font-weight: 650; }
.getting-started-details { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(240px, .9fr); gap: 24px; padding: 24px clamp(24px, 4vw, 42px) 30px; border-top: 1px solid var(--df-border-soft); background: color-mix(in srgb, var(--df-surface-2) 72%, transparent); }
.getting-started-detail-content, .getting-started-example { display: grid; align-content: start; gap: 12px; min-width: 0; }
.getting-started-details h3 { margin-top: 6px; color: var(--df-text); font: 700 22px/1.3 var(--df-font-title); }
.getting-started-details p { color: var(--df-text-secondary); line-height: 1.65; }
.getting-started-status { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--df-text-muted); font-size: 13px; }
.getting-started-detail-content, .getting-started-example { display: grid; align-content: start; gap: 12px; min-width: 0; }
.getting-started-details h3 { margin-top: 5px; color: var(--df-text); font-size: 20px; }
.getting-started-details p { color: var(--df-text-secondary); line-height: 1.6; }
.getting-started-status { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--df-text-muted); font-size: 12px; }
.getting-started-status strong { color: var(--df-text-secondary); font-size: 13px; }
.getting-started-status .is-ready { color: var(--df-success, var(--df-accent-strong)); }
.getting-started-status .is-pending { color: var(--df-accent-strong); }
.getting-started-error { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--df-danger, var(--df-accent)) 52%, var(--df-border-soft)); border-radius: var(--df-radius-sm); color: var(--df-text-secondary); background: color-mix(in srgb, var(--df-surface-1) 78%, transparent); }
.getting-started-detail-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.getting-started-link { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 15px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); color: var(--df-text-secondary); background: var(--df-surface-1); text-decoration: none; }
.getting-started-link.primary { border-color: color-mix(in srgb, var(--df-accent) 62%, var(--df-border-soft)); color: var(--df-accent-strong); }
.getting-started-note { color: var(--df-text-muted) !important; font-size: 12px; }
.getting-started-example { padding-left: 22px; border-left: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); }
.getting-started-example blockquote { margin: 0; padding: 12px 14px; border-left: 2px solid var(--df-accent); color: var(--df-text); background: color-mix(in srgb, var(--df-control-bg) 72%, transparent); line-height: 1.55; }
.friends-steps { display: grid; gap: 8px; margin: 0; padding-left: 20px; color: var(--df-text-secondary); line-height: 1.55; }
.getting-started-collapsed { display: flex; min-height: 58px; align-items: center; gap: 12px; padding: 8px 14px 8px 18px; }
.getting-started-collapsed-mark { color: var(--df-accent); font-size: 22px; }
.getting-started-collapsed-title { flex: 1; min-width: 0; color: var(--df-text); font-family: var(--df-font-title); font-size: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.getting-started-collapsed-actions { display: inline-flex; align-items: center; gap: 4px; flex: 0 0 auto; }
.getting-started-expand-button { min-height: 44px; padding: 0 12px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm); color: var(--df-text-secondary); background: var(--df-surface-2); cursor: pointer; }
.getting-started-expand-button:hover { border-color: var(--df-interactive); color: var(--df-interactive-strong); }

@media (max-width: 700px) {
  .getting-started-layout, .getting-started-details { grid-template-columns: minmax(0, 1fr); gap: 22px; }
  .getting-started-layout { padding: 22px 18px; }
  .getting-started-details { padding: 22px 18px 26px; }
  .getting-started-example { padding-top: 20px; padding-left: 0; border-top: 1px solid color-mix(in srgb, var(--df-accent) 30%, var(--df-border-soft)); border-left: 0; }
  .getting-started-layout { padding-top: 70px; }
  .getting-started-controls { top: 8px; right: 12px; }
  .getting-started-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .getting-started-actions > button { min-width: 0; padding: 8px 10px; }
  .getting-started-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding-top: 18px; border-top: 1px solid var(--df-border-soft); }
  .getting-started-steps li { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
  .getting-started-steps li + li::before { display: none; }
  .getting-started-steps li div span { display: none; }
  .getting-started-steps li strong { font-size: 13px; }
}
</style>
