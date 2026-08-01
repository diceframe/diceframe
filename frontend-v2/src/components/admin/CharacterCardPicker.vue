<script setup lang="ts">
import type { CharacterCard } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { characterCardNeedsConversion, characterCardRuleName } from '@/utils/characterCards'
import PortraitImage from '@/components/PortraitImage.vue'

defineProps<{ cards: CharacterCard[]; targetRuleId?: string }>()
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
        :class="{ 'rule-mismatch': characterCardNeedsConversion(c, targetRuleId) }"
        @click="emit('pick', c)"
      >
        <PortraitImage :portrait="c.portrait" :rule-id="c.rule_id || targetRuleId" :seed="String(c.card_id || c.id || c.character_name)" :name="c.character_name" :size="48" />
        <span class="card-choice-copy">
          <strong>{{ c.character_name }}</strong>
          <span>{{ characterCardRuleName(c, t('unboundRule')) }} · {{ c.race }} · {{ c.class }}</span>
          <small v-if="characterCardNeedsConversion(c, targetRuleId)">{{ t('cardNeedsRuleConversion') }}</small>
        </span>
      </button>
      <p v-if="!cards.length" class="muted">{{ t('characterLibraryEmpty') }}</p>
    </section>
  </div>
</template>
