<script setup lang="ts">
import type { CharacterCard } from '../../api/types'

defineProps<{ cards: CharacterCard[] }>()
const emit = defineEmits<{ pick: [card: CharacterCard]; close: [] }>()
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <section class="dialog">
      <header>
        <h2>选择角色卡</h2>
        <button @click="emit('close')">×</button>
      </header>
      <p>从共享卡库选择，将作为新角色加入列表。</p>
      <button
        v-for="c in cards"
        :key="c.card_id || c.character_name"
        class="card-choice"
        @click="emit('pick', c)"
      >
        <strong>{{ c.character_name }}</strong>
        <span>{{ c.race }} · {{ c.class }}</span>
      </button>
      <p v-if="!cards.length" class="muted">卡库为空。</p>
    </section>
  </div>
</template>
