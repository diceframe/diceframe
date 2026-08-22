<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { errorMessage } from '@/api/client'
import { testImageGeneration } from '@/api/sceneImages'
import { useLocale } from '@/composables/useLocale'
import SceneImageBlock from '@/components/play/SceneImageBlock.vue'

defineProps<{ running: boolean }>()
const { t } = useLocale()
const prompt = ref('')
const busy = ref(false)
const error = ref('')
const previewAssetId = ref('')

async function run() {
  const text = prompt.value.trim()
  if (!text || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await testImageGeneration(text)
    if (!result.ok || !result.asset_id) {
      error.value = result.error || t('imagegenTestFailed')
      previewAssetId.value = ''
    } else {
      previewAssetId.value = result.asset_id
    }
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="imagegen-test" data-testid="imagegen-test">
    <p class="muted">{{ t('imagegenTestHelp') }}</p>
    <div class="imagegen-test-row">
      <NInput
        v-model:value="prompt"
        :placeholder="t('imagegenTestPrompt')"
        :disabled="busy || !running"
        @keyup.enter="run"
      />
      <NButton type="primary" :loading="busy" :disabled="!running || !prompt.trim()" @click="run">
        {{ t('imagegenTestRun') }}
      </NButton>
    </div>
    <p v-if="!running" class="muted">{{ t('imagegenTestNotRunning') }}</p>
    <p v-else-if="error" class="imagegen-test-error">{{ error }}</p>
    <SceneImageBlock v-if="previewAssetId" :asset-id="previewAssetId" :alt="prompt" />
  </div>
</template>

<style scoped>
.imagegen-test {
  display: grid;
  gap: 10px;
}

.imagegen-test-row {
  display: flex;
  gap: 10px;
}

.imagegen-test-row > :first-child {
  flex: 1;
}

.imagegen-test-error {
  margin: 0;
  color: var(--df-danger, #e2a1a1);
  font-size: 13px;
}
</style>
