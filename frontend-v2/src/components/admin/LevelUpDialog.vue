<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterSheet, Player, RuleMeta } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import { attrDisplayName, type RuleAttr } from '@/utils/ruleSchema'

const props = defineProps<{
  ruleAttrs: RuleAttr[]
  ruleMeta?: RuleMeta | null
  character: Player
  levelUpPoints: number
}>()
const emit = defineEmits<{ submit: [attributes: Record<string, number>]; cancel: [] }>()

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
  <Modal title="分配升级属性点" @close="emit('cancel')">
    <p class="muted">{{ character?.character_name || '冒险者' }} 获得了 {{ levelUpPoints }} 点升级属性</p>
    <p class="attr-points" :class="{ over: overLimit }">剩余: {{ remaining }} 点<span v-if="overLimit" class="warn">已超限</span></p>
    <div class="attr-sliders">
      <div v-for="a in ruleAttrs" :key="a.key" class="attr-row level-row">
        <span class="attr-name">{{ attrDisplayName(a) }}</span>
        <span class="attr-val">{{ local[a.key] }}</span>
        <button type="button" @click="minus(a)">-</button>
        <button type="button" @click="plus(a)">+</button>
        <span class="attr-range">[{{ a.min }}-{{ a.max }}]</span>
      </div>
    </div>
    <p class="form-hint">未分配的点数会保留到下次。允许有剩余，但不能超限。</p>
    <template #actions>
      <button @click="emit('cancel')">取消</button>
      <button class="primary" :disabled="overLimit" @click="emit('submit', { ...local })">确认分配</button>
    </template>
  </Modal>
</template>
