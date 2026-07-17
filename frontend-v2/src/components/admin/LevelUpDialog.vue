<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterSheet, Player, RuleMeta } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import { attrDisplayName, type RuleAttr } from '@/utils/ruleSchema'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{
  ruleAttrs: RuleAttr[]
  ruleMeta?: RuleMeta | null
  character: Player
  levelUpPoints: number
}>()
const emit = defineEmits<{ submit: [attributes: Record<string, number>]; cancel: [] }>()
const { t } = useLocale()

const emptySheet: CharacterSheet = { character_name: '' }
const sheet = props.character.character_sheet || emptySheet
const orig: Record<string, number> = {}
for (const a of props.ruleAttrs) {
  orig[a.key] = Number(sheet.attributes?.[a.key] ?? Math.floor((a.min + a.max) / 2)) || 0
}
const local = ref<Record<string, number>>({ ...orig })

const spent = computed(() =>
  props.ruleAttrs.reduce((s, a) => s + Math.max(0, (local.value[a.key] || 0) - (orig[a.key] || 0)), 0)
)
const remaining = computed(() => props.levelUpPoints - spent.value)
const overLimit = computed(() => spent.value > props.levelUpPoints)

function plus(a: RuleAttr) {
  const v = local.value[a.key] || 0
  if (spent.value < props.levelUpPoints && v < a.max) local.value[a.key] = v + 1
}
function minus(a: RuleAttr) {
  const v = local.value[a.key] || 0
  const o = orig[a.key] || 0
  if (spent.value > 0 && v > o && v > a.min) local.value[a.key] = v - 1
}
</script>

<template>
  <Modal :title="t('allocateLevelUpPoints')" @close="emit('cancel')">
    <p class="muted">{{ t('levelUpPointsGained', { name: character?.character_name || t('adventurer'), points: levelUpPoints }) }}</p>
    <p class="attr-points" :class="{ over: overLimit }">{{ t('pointsRemaining', { points: remaining }) }}<span v-if="overLimit" class="warn">{{ t('overLimit') }}</span></p>
    <div class="attr-sliders">
      <div v-for="a in ruleAttrs" :key="a.key" class="attr-row level-row">
        <span class="attr-name">{{ attrDisplayName(a) }}</span>
        <span class="attr-val">{{ local[a.key] }}</span>
        <button type="button" @click="minus(a)">-</button>
        <button type="button" @click="plus(a)">+</button>
        <span class="attr-range">[{{ a.min }}-{{ a.max }}]</span>
      </div>
    </div>
    <p class="form-hint">{{ t('levelUpPointsHint') }}</p>
    <template #actions>
      <button @click="emit('cancel')">{{ t('cancel') }}</button>
      <button class="primary" :disabled="overLimit" @click="emit('submit', { ...local })">{{ t('confirmAllocation') }}</button>
    </template>
  </Modal>
</template>
