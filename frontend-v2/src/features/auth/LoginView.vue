<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { errorMessage, setAccessToken, validateAccessToken } from '@/api/client'
import BrandLogo from '@/components/BrandLogo.vue'

const route = useRoute()
const token = ref('')
const busy = ref(false)
const error = ref('')
const redirect = computed(() => String(route.query.redirect || '/'))

async function submit() {
  const value = token.value.trim()
  if (!value) { error.value = '请输入访问密码'; return }
  busy.value = true
  error.value = ''
  try {
    await validateAccessToken(value)
    setAccessToken(value)
    location.href = redirect.value || '/'
  } catch (e: unknown) {
    error.value = errorMessage(e) || '验证失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <BrandLogo :size="56" :with-text="false" class="login-emblem" />
      <h1>DiceFrame</h1>
      <p class="muted">输入访问密码进入游戏桌。</p>
      <form @submit.prevent="submit">
        <label>访问密码<input v-model="token" type="password" autocomplete="current-password" autofocus placeholder="Access token"></label>
        <button class="primary submit" :disabled="busy">{{ busy ? '验证中' : '进入' }}</button>
      </form>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <p class="hint muted">密码可在启动控制台的 Access token 行查看，也会保存到 <code>data/access_token.txt</code>。</p>
    </section>
  </main>
</template>
