<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorMessage, setAccessToken, validateAccessToken } from '@/api/client'
import { currentBackendUrl, isStandaloneFrontend, normalizeBackendUrl, setBackendUrl } from '@/api/connection'
import type { AppConfig } from '@/api/types'
import { useLocale, type Locale } from '@/composables/useLocale'
import { LOCALE_STORAGE_KEY, SUPPORTED_LOCALES, localeEndonym } from '@/i18n'
import BrandLogo from '@/components/BrandLogo.vue'

const route = useRoute()
const { t, locale, setLocale } = useLocale()
const firstVisit = ref(!localStorage.getItem(LOCALE_STORAGE_KEY))
function pickLocale(next: Locale) {
  setLocale(next)
  firstVisit.value = false
}
function onLocaleChange(event: Event) {
  const next = (event.target as HTMLSelectElement).value as Locale
  setLocale(next)
}
const token = ref('')
const busy = ref(false)
const error = ref('')
const redirect = computed(() => String(route.query.redirect || '/'))
const standalone = isStandaloneFrontend()
const serverUrl = ref(currentBackendUrl())
const serverConnected = ref(!standalone || Boolean(serverUrl.value))
const serverNeedsPassword = ref(true)
const serverBusy = ref(false)
const serverError = ref('')

async function connectServer() {
  const normalized = normalizeBackendUrl(serverUrl.value)
  if (!normalized) {
    serverError.value = t('invalidServerAddress')
    serverConnected.value = false
    return
  }
  serverBusy.value = true
  serverError.value = ''
  try {
    setBackendUrl(normalized)
    serverUrl.value = normalized
    const config = await api<AppConfig>('/config')
    serverConnected.value = true
    serverNeedsPassword.value = Boolean(config.access_password?.configured)
    if (!serverNeedsPassword.value) location.href = redirect.value || '/'
  } catch (e: unknown) {
    serverConnected.value = false
    serverError.value = errorMessage(e) || t('serverConnectionFailed')
  } finally {
    serverBusy.value = false
  }
}

async function submit() {
  if (standalone && !serverConnected.value) {
    await connectServer()
    return
  }
  if (!serverNeedsPassword.value) {
    location.href = redirect.value || '/'
    return
  }
  const value = token.value.trim()
  if (!value) { error.value = t('enterAccessPassword'); return }
  busy.value = true
  error.value = ''
  try {
    await validateAccessToken(value)
    setAccessToken(value)
    location.href = redirect.value || '/'
  } catch (e: unknown) {
    error.value = errorMessage(e) || t('validationFailed')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (standalone && serverUrl.value) void connectServer()
})
</script>

<template>
  <main class="login-page">
    <div v-if="firstVisit" class="lang-picker">
      <BrandLogo :size="56" :with-text="false" class="login-emblem" />
      <h1>DiceFrame</h1>
      <p class="muted">Choose your language / 选择语言</p>
      <div class="lang-buttons">
        <button class="lang-btn" @click="pickLocale('zh-CN')">中文</button>
        <button class="lang-btn" @click="pickLocale('en')">English</button>
      </div>
    </div>
    <template v-else>
      <label class="locale-switch" :aria-label="t('language')">
        <select :value="locale" @change="onLocaleChange">
          <option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">{{ localeEndonym(code) }}</option>
        </select>
      </label>
      <div class="login-stage" aria-hidden="true"><i v-for="n in 12" :key="n" /></div>
      <section class="login-card">
        <div class="login-card-corner corner-tl" aria-hidden="true" />
        <div class="login-card-corner corner-tr" aria-hidden="true" />
        <div class="login-card-corner corner-bl" aria-hidden="true" />
        <div class="login-card-corner corner-br" aria-hidden="true" />
        <header class="login-card-head">
          <span class="login-emblem-wrap">
            <svg class="login-emblem-geometry" viewBox="0 0 96 96" aria-hidden="true">
              <path class="emblem-ray" d="M48 1V13M48 83V95M1 48H13M83 48H95" />
              <path class="emblem-octagon" d="M31 8H65L88 31V65L65 88H31L8 65V31Z" />
              <rect class="emblem-diamond" x="20" y="20" width="56" height="56" />
            </svg>
            <BrandLogo :size="62" :with-text="false" class="login-emblem" />
          </span>
          <h1>DiceFrame</h1>
          <p class="muted">{{ t('loginHelp') }}</p>
        </header>
        <div v-if="standalone" class="server-connection">
          <label>{{ t('serverAddress') }}<input v-model="serverUrl" type="url" autocomplete="url" :placeholder="t('serverAddressPlaceholder')"></label>
          <button class="secondary submit" type="button" :disabled="serverBusy" @click="connectServer"><span>{{ serverBusy ? t('connecting') : t('connectServer') }}</span></button>
          <p class="hint muted">{{ t('serverConnectionHint') }}</p>
          <p v-if="serverConnected" class="server-connected">{{ t('serverConnected') }}</p>
          <p v-if="serverError" class="error-banner">{{ serverError }}</p>
        </div>
        <form v-if="!standalone || (serverConnected && serverNeedsPassword)" @submit.prevent="submit">
          <label>{{ t('accessPassword') }}<input v-model="token" type="password" autocomplete="current-password" :autofocus="!standalone" placeholder="Access token"></label>
          <button class="primary submit" :disabled="busy || serverBusy"><span>{{ busy ? t('validating') : t('enter') }}</span></button>
        </form>
        <p v-if="error" class="error-banner">{{ error }}</p>
        <div class="login-help">
          <p class="hint muted">{{ t('firstPasswordHintBefore') }} <code>data/access_token.txt</code>{{ t('firstPasswordHintAfter') }}</p>
          <details class="forgot-password">
            <summary>{{ t('forgotPassword') }}</summary>
            <p>{{ t('resetPasswordHintBefore') }} <code>data/reset_access_password.txt</code>{{ t('resetPasswordHintAfter') }}</p>
          </details>
        </div>
      </section>
    </template>
    <footer class="login-footer">
      <span>DiceFrame · {{ t('projectTagline') }}</span>
    </footer>
  </main>
</template>

<style scoped>
.login-page {
  padding-bottom: 60px;
}
.server-connection { display: grid; gap: 10px; margin-bottom: 16px; }
.server-connection .submit { margin-top: 0; }
.server-connected { color: var(--df-success, #82c997); margin: 0; font-size: 13px; }
.locale-switch { position: fixed; bottom: 52px; left: 50%; transform: translateX(-50%); z-index: 10; }
.locale-switch select {
  padding: 4px 10px; border-radius: 6px;
  border: 1px solid rgba(128,128,128,0.4);
  background: transparent; color: inherit; font-size: 13px; cursor: pointer;
}
.lang-picker { display: flex; flex-direction: column; align-items: center; gap: 14px; }
.lang-picker h1 { margin: 4px 0 0; }
.lang-buttons { display: flex; gap: 12px; margin-top: 8px; }
.lang-btn {
  padding: 10px 28px; border-radius: 8px;
  border: 1px solid rgba(128,128,128,0.4);
  background: transparent; color: inherit; font-size: 15px; cursor: pointer;
}
.lang-btn:hover { background: rgba(128,128,128,0.12); }
.login-footer {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 16px calc(8px + env(safe-area-inset-bottom));
  color: var(--df-text-muted);
  font-size: 12px;
  letter-spacing: .02em;
}
.login-footer a {
  color: var(--df-accent-strong);
  text-decoration: none;
}
.login-footer a:hover {
  color: var(--df-interactive-strong);
  text-decoration: underline;
}
</style>
