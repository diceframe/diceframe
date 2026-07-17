<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NTag } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
import type { TestResult } from '@/api/types'

const props = defineProps<{ result: TestResult; kind: 'model' | 'embedding' | 'proxy' }>()
const { t } = useLocale()

const ok = computed(() => props.result.ok)
const elapsedSec = computed(() => (props.result.elapsed != null ? (props.result.elapsed / 1000).toFixed(2) + 's' : ''))
</script>

<template>
  <NCard class="test-result" :class="ok ? 'ok' : 'err'" size="small">
    <div class="test-result-head">
      <NTag :type="ok ? 'success' : 'error'" size="small">{{ ok ? t('success') : t('failure') }}</NTag>
      <span v-if="elapsedSec" class="muted">{{ t('elapsedTime', { time: elapsedSec }) }}</span>
    </div>
    <dl class="test-result-dl">
      <template v-if="kind === 'model'">
        <div v-if="result.response"><dt>{{ t('response') }}</dt><dd>{{ result.response }}</dd></div>
        <div v-if="result.tokens != null"><dt>Token</dt><dd>{{ result.tokens }}</dd></div>
      </template>
      <template v-else-if="kind === 'embedding'">
        <div v-if="result.dimension != null"><dt>{{ t('dimension') }}</dt><dd>{{ result.dimension }}</dd></div>
      </template>
      <template v-else-if="kind === 'proxy'">
        <div v-if="result.status != null"><dt>{{ t('httpStatus') }}</dt><dd>{{ result.status }}</dd></div>
      </template>
      <div v-if="result.error"><dt>{{ t('error') }}</dt><dd>{{ result.error }}</dd></div>
    </dl>
  </NCard>
</template>
