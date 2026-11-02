<script setup lang="ts">
import { ref } from 'vue'
import { NModal, NTabPane, NTabs } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'

const { t } = useLocale()
const show = ref(false)
</script>

<template>
  <button type="button" class="access-help-open" data-testid="access-help-open" @click="show = true">
    {{ t('accessHelpOpen') }}
  </button>
  <NModal
    v-model:show="show"
    preset="card"
    class="access-help-modal"
    :title="t('accessHelpTitle')"
    :style="{ width: 'min(560px, calc(100vw - 32px))' }"
    :content-style="{ maxHeight: 'min(70vh, 560px)', overflowY: 'auto' }"
  >
    <p class="access-help-intro">{{ t('accessHelpIntro') }}</p>
    <NTabs type="line" animated default-value="portable">
      <NTabPane name="portable" :tab="t('accessHelpTabPortable')">
        <ol class="access-help-steps">
          <li>{{ t('accessHelpPortableStep1') }}</li>
          <li>{{ t('accessHelpPortableStep2') }}</li>
          <li>
            {{ t('accessHelpPortableStep3') }}
            <code class="access-help-code">data\access_token.txt</code>
          </li>
        </ol>
      </NTabPane>
      <NTabPane name="docker" :tab="t('accessHelpTabDocker')">
        <ol class="access-help-steps">
          <li>
            {{ t('accessHelpDockerStep1') }}
            <code class="access-help-code">docker logs diceframe-web</code>
            <code class="access-help-code">docker logs diceframe</code>
          </li>
          <li>{{ t('accessHelpDockerStep2') }}</li>
        </ol>
      </NTabPane>
      <NTabPane name="source" :tab="t('accessHelpTabSource')">
        <ol class="access-help-steps">
          <li>{{ t('accessHelpSourceStep1') }}</li>
          <li>{{ t('accessHelpSourceStep2') }}</li>
        </ol>
      </NTabPane>
    </NTabs>
    <p class="access-help-env">{{ t('accessHelpEnvToken') }}</p>
    <p class="access-help-gone">
      {{ t('accessHelpGone') }}
      {{ t('resetPasswordHintBefore') }} <code>data/reset_access_password.txt</code>{{ t('resetPasswordHintAfter') }}
    </p>
    <p class="access-help-player">{{ t('accessHelpPlayerNote') }}</p>
  </NModal>
</template>

<style scoped>
.access-help-open {
  display: block;
  margin: 12px 0 0;
  padding: 0;
  font-size: 13px;
  color: var(--df-interactive-strong);
  background: none;
  border: 0;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}
.access-help-open:hover { color: var(--df-interactive); }
.access-help-open:focus-visible { outline: 2px solid var(--df-interactive-strong); outline-offset: 3px; border-radius: 4px; }
.access-help-intro { margin: 0 0 14px; line-height: 1.7; }
.access-help-steps { margin: 12px 0 0; padding-left: 20px; display: grid; gap: 10px; line-height: 1.7; }
.access-help-code {
  display: block;
  margin-top: 6px;
  padding: 6px 10px;
  overflow-wrap: anywhere;
  user-select: all;
  background: var(--df-surface-2);
  border-radius: var(--df-radius-md);
}
.access-help-env,
.access-help-gone,
.access-help-player { margin: 16px 0 0; font-size: 13px; line-height: 1.7; color: var(--df-text-secondary); }
.access-help-player { padding-top: 12px; border-top: 1px solid var(--df-border-soft); }
</style>
