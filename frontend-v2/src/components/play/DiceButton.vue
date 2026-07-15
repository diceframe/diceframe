<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ phase: 'idle' | 'rolling' | 'result'; value?: number; crit?: boolean; fumble?: boolean }>()
const isCrit = computed(() => props.phase === 'result' && (props.crit || props.value === 20))
const isFumble = computed(() => props.phase === 'result' && (props.fumble || props.value === 1))
</script>

<template>
  <div v-if="phase !== 'idle'" class="dice-result">
    <template v-if="phase === 'rolling'">
      <span class="dice-spinner" /> 🎲 掷骰中...
    </template>
    <template v-else>
      🎲 <strong class="dice-value">d20 = {{ value }}</strong>
      <span v-if="isCrit" class="dice-crit">⚡ 大成功！</span>
      <span v-if="isFumble" class="dice-fumble">💥 大失败！</span>
    </template>
  </div>
</template>
