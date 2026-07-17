<script setup lang="ts">
import type { CharacterCard } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

defineProps<{ cards: CharacterCard[] }>()
const emit = defineEmits<{ pick: [card: CharacterCard]; close: [] }>()
const { t } = useLocale()
</script>

<template>
  <div class="modal" @click.self="emit('close')">
    <section class="dialog">
      <header>
        <h2>{{ t('chooseCharacterCard') }}</h2>
        <button @click="emit('close')">×</button>
      </header>
      <p>{{ t('chooseCharacterCardHelp') }}</p>
      <button
        v-for="c in cards"
        :key="c.card_id || c.character_name"
        class="card-choice"
        @click="emit('pick', c)"
      >
        <strong>{{ c.character_name }}</strong>
        <span>{{ c.race }} · {{ c.class }}</span>
      </button>
      <p v-if="!cards.length" class="muted">{{ t('characterLibraryEmpty') }}</p>
    </section>
  </div>
</template>
