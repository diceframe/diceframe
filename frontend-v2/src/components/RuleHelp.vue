<script setup lang="ts">
import { computed } from 'vue'
import type { RuleMeta } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import { localizedField } from '@/utils/ruleSchema'

const props = defineProps<{ meta?: RuleMeta | null }>()
defineEmits<{ close: [] }>()
const { t } = useLocale()

const attrHint = computed(() => localizedField<string>(props.meta, 'attr_hint') || '')
const skillHint = computed(() => localizedField<string>(props.meta, 'skill_hint') || '')

const diceHint = computed(() => {
  if (props.meta?.mechanics === 'dnd5e_core') {
    return t('dndDiceHintLong')
  }
  return ''
})

const hpHelp = computed(() => props.meta?.hp_formula ? t('referenceFormula', { formula: props.meta.hp_formula }) : t('hpFillByRule'))
</script>

<template>
  <div class="modal" @click.self="$emit('close')">
    <section class="dialog rule-help-dialog">
      <header>
        <h2>{{ t('ruleHelp') }}</h2>
        <button :title="t('close')" :aria-label="t('close')" @click="$emit('close')">×</button>
      </header>

      <div class="rule-help-grid">
        <article>
          <strong>{{ t('diceSystem') }}</strong>
          <span>{{ String(meta?.dice_system || 'd20').toUpperCase() }}</span>
        </article>
        <article>
          <strong>{{ t('actionStyle') }}</strong>
          <span>{{ t('actionStyleHelp') }}</span>
        </article>
      </div>

      <section v-if="diceHint" class="rule-help-section">
        <h3>{{ t('advantageDisadvantage') }}</h3>
        <p>{{ diceHint }}</p>
      </section>

      <section class="rule-help-section">
        <h3>{{ t('attributesAndSkills') }}</h3>
        <p>{{ attrHint || t('attrHintDefault') }}</p>
        <p>{{ skillHint || t('skillHintDefault') }}</p>
      </section>

      <section class="rule-help-section">
        <h3>{{ t('hp') }}</h3>
        <p>{{ hpHelp }}</p>
      </section>
    </section>
  </div>
</template>
