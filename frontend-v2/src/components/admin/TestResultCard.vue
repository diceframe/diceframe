<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NTag } from 'naive-ui'
import type { TestResult } from '@/api/types'

const props = defineProps<{ result: TestResult; kind: 'model' | 'embedding' | 'proxy' }>()

const ok = computed(() => props.result.ok)
const elapsedSec = computed(() => (props.result.elapsed != null ? (props.result.elapsed / 1000).toFixed(2) + 's' : ''))
</script>

<template>
  <NCard class="test-result" :class="ok ? 'ok' : 'err'" size="small">
    <div class="test-result-head">
      <NTag :type="ok ? 'success' : 'error'" size="small">{{ ok ? '成功' : '失败' }}</NTag>
      <span v-if="elapsedSec" class="muted">耗时 {{ elapsedSec }}</span>
    </div>
    <dl class="test-result-dl">
      <template v-if="kind === 'model'">
        <div v-if="result.response"><dt>响应</dt><dd>{{ result.response }}</dd></div>
        <div v-if="result.tokens != null"><dt>Token</dt><dd>{{ result.tokens }}</dd></div>
      </template>
      <template v-else-if="kind === 'embedding'">
        <div v-if="result.dimension != null"><dt>维度</dt><dd>{{ result.dimension }}</dd></div>
      </template>
      <template v-else-if="kind === 'proxy'">
        <div v-if="result.status != null"><dt>HTTP 状态</dt><dd>{{ result.status }}</dd></div>
      </template>
      <div v-if="result.error"><dt>错误</dt><dd>{{ result.error }}</dd></div>
    </dl>
  </NCard>
</template>
