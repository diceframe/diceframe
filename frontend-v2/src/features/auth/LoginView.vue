<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { errorMessage, setAccessToken, validateAccessToken } from '@/api/client'
import { useLocale, type Locale } from '@/composables/useLocale'
import BrandLogo from '@/components/BrandLogo.vue'

const route = useRoute()
const { t, locale, setLocale } = useLocale()
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
  </main>
</template>

<style scoped>
.locale-switch { position: fixed; top: 14px; right: 14px; z-index: 10; }
.locale-switch select {
  padding: 4px 10px; border-radius: 6px;
  border: 1px solid rgba(128,128,128,0.4);
  background: transparent; color: inherit; font-size: 13px; cursor: pointer;
}
</style>
