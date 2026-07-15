<script setup lang="ts">
import { computed } from 'vue'
import type { RuleMeta } from '../api/types'

const props = defineProps<{ meta?: RuleMeta | null }>()
defineEmits<{ close: [] }>()

const diceHint = computed(() => {
  if (props.meta?.mechanics === 'dnd5e_core') {
    return 'DND 小抄：优势=掷 2 个 d20 取高；劣势=掷 2 个 d20 取低；同时有优势和劣势时互相抵消。'
  }
  return ''
})
</script>

<template>
  <div class="modal" @click.self="$emit('close')">
    <section class="dialog rule-help-dialog">
      <header>
        <h2>规则帮助</h2>
        <button title="关闭" aria-label="关闭" @click="$emit('close')">×</button>
      </header>

      <div class="rule-help-grid">
        <article>
          <strong>骰制</strong>
          <span>{{ String(meta?.dice_system || 'd20').toUpperCase() }}</span>
        </article>
        <article>
          <strong>行动方式</strong>
          <span>直接用自然语言描述行动，系统会结合角色能力和场景判断是否需要检定。</span>
        </article>
      </div>

      <section v-if="diceHint" class="rule-help-section">
        <h3>优势 / 劣势</h3>
        <p>{{ diceHint }}</p>
      </section>

      <section class="rule-help-section">
        <h3>属性与技能</h3>
        <p>{{ meta?.attr_hint || '属性代表角色在不同方面的能力。' }}</p>
        <p>{{ meta?.skill_hint || '技能描述角色擅长的领域。' }}</p>
      </section>

      <section class="rule-help-section">
        <h3>生命</h3>
        <p>{{ meta?.hp_formula ? '参考公式：' + meta.hp_formula : 'HP 可按本局约定填写。' }}</p>
      </section>
    </section>
  </div>
</template>
