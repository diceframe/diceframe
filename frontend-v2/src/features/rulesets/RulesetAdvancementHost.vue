<script setup lang="ts">
import { computed } from 'vue'
import type { CharacterSheet } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import { resolveRulesetAdvancementExtension } from './registry'

const props = withDefaults(defineProps<{
  runtimeId: string
  ruleId: string
  character: CharacterSheet
  language?: string
  cardId?: string
  gameKey?: string
  userId?: string
  revision?: number
  targetLevel?: number
}>(), {
  language: 'zh-CN',
  cardId: '',
  gameKey: '',
  userId: '',
  revision: 0,
  targetLevel: 0,
})
const emit = defineEmits<{
  applied: [character: CharacterSheet]
  chainCompleted: [character: CharacterSheet]
  cancel: []
}>()
const extension = computed(() => resolveRulesetAdvancementExtension(props.runtimeId))
function onApplied(character: CharacterSheet): void {
  emit('applied', character)
}
function onChainCompleted(character: CharacterSheet): void {
  emit('chainCompleted', character)
}
</script>

<template>
  <Modal
    :title="extension?.title(language) || (language.startsWith('zh') ? '规则升级' : 'Ruleset advancement')"
    @close="emit('cancel')"
  >
    <component
      :is="extension.component"
      v-if="extension"
      :rule-id="ruleId"
      :character="character"
      :language="language"
      :card-id="cardId"
      :game-key="gameKey"
      :user-id="userId"
      :revision="revision"
      :target-level="targetLevel"
      @applied="onApplied"
      @chain-completed="onChainCompleted"
      @cancel="emit('cancel')"
    />
    <p v-else class="error-banner">
      {{ language.startsWith('zh') ? '当前规则尚未注册升级界面。' : 'This ruleset has no registered advancement interface.' }}
    </p>
  </Modal>
</template>
