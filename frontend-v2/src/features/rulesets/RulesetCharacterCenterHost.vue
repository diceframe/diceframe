<script setup lang="ts">
import { computed } from 'vue'
import type {
  CharacterSheet,
  RestSessionStatus,
} from '@/api/types'
import { resolveRulesetCharacterCenter } from './registry'

const props = withDefaults(defineProps<{
  runtimeId: string
  character: CharacterSheet
  target: 'game' | 'card'
  ruleId: string
  language?: string
  gameKey?: string
  userId?: string
  cardId?: string
  restSession?: RestSessionStatus | null
  initialFocus?: 'rest'
  isGm?: boolean
}>(), {
  language: 'zh-CN',
  gameKey: '',
  userId: '',
  cardId: '',
  restSession: null,
  isGm: false,
})
const emit = defineEmits<{
  saved: [character: CharacterSheet, reason?: 'profile' | 'rest']
  'rest-pending': []
  cancel: []
}>()
const component = computed(() => resolveRulesetCharacterCenter(props.runtimeId))
function onSaved(character: CharacterSheet, reason?: 'profile' | 'rest'): void {
  emit('saved', character, reason)
}
</script>

<template>
  <component
    :is="component"
    v-if="component"
    :character="character"
    :target="target"
    :rule-id="ruleId"
    :language="language"
    :game-key="gameKey"
    :user-id="userId"
    :card-id="cardId"
    :rest-session="restSession"
    :initial-focus="initialFocus"
    :is-gm="isGm"
    @saved="onSaved"
    @rest-pending="emit('rest-pending')"
    @cancel="emit('cancel')"
  />
  <p v-else class="error-banner">
    {{ language.startsWith('zh') ? '当前规则尚未注册角色中心。' : 'This ruleset has no registered character center.' }}
  </p>
</template>
