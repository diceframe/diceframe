<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { errorMessage, setAccessToken, validateAccessToken } from '@/api/client'
import { useLocale, type Locale } from '@/composables/useLocale'
import { LOCALE_STORAGE_KEY } from '@/i18n'
import BrandLogo from '@/components/BrandLogo.vue'

const route = useRoute()
const { t, locale, setLocale } = useLocale()
const firstVisit = ref(!localStorage.getItem(LOCALE_STORAGE_KEY))
function pickLocale(next: Locale) {
  setLocale(next)
  firstVisit.value = false
}
function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}
const token = ref('')
const busy = ref(false)
const error = ref('')
const redirect = computed(() => String(route.query.redirect || '/'))

async function submit() {
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
          <option value="zh-CN">中文</option>
          <option value="en">EN</option>
        </select>
      </label>
      <section class="login-card">
        <BrandLogo :size="56" :with-text="false" class="login-emblem" />
        <h1>DiceFrame</h1>
        <p class="muted">{{ t('loginHelp') }}</p>
        <form @submit.prevent="submit">
          <label>{{ t('accessPassword') }}<input v-model="token" type="password" autocomplete="current-password" autofocus placeholder="Access token"></label>
          <button class="primary submit" :disabled="busy">{{ busy ? t('validating') : t('enter') }}</button>
        </form>
        <p v-if="error" class="error-banner">{{ error }}</p>
        <p class="hint muted">{{ t('firstPasswordHintBefore') }} <code>data/access_token.txt</code>{{ t('firstPasswordHintAfter') }}</p>
        <details class="forgot-password">
          <summary>{{ t('forgotPassword') }}</summary>
          <p>{{ t('resetPasswordHintBefore') }} <code>reset_access_password.txt</code>{{ t('resetPasswordHintAfter') }}</p>
        </details>
      </section>
    </template>
  </main>
</template>

<style scoped>
.locale-switch { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); z-index: 10; }
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
</style>
