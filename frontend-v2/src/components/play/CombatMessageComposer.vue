<script setup lang="ts">
import { computed, ref } from 'vue'
import { NIcon } from 'naive-ui'
import { ChatbubbleEllipsesOutline, ShieldOutline } from '@vicons/ionicons5'
import type { RulesetGameplayView } from '@/api/types'
import { submitRulesetIntent } from '@/api/rulesets'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{
  gameKey: string
  gameplay: RulesetGameplayView
}>()
const emit = defineEmits<{ refresh: []; openCombat: [] }>()
const { locale } = useLocale()
const text = ref('')
const busy = ref(false)
const notice = ref('')
const zh = computed(() => locale.value.startsWith('zh'))

async function submit(): Promise<void> {
  const message = text.value.trim()
  if (!message || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    await submitRulesetIntent(props.gameKey, {
      intent_id: globalThis.crypto?.randomUUID?.() || `combat-message-${Date.now()}`,
      type: 'combat.message',
      expected_version: props.gameplay.state_version,
      text: message,
    })
    text.value = ''
    notice.value = zh.value ? '战斗发言已发送；不会消耗动作或推进回合。' : 'Sent without spending an action or advancing the turn.'
    emit('refresh')
  } catch (error: unknown) {
    notice.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="combat-message-composer">
    <header>
      <div><NIcon :component="ChatbubbleEllipsesOutline" /><strong>{{ zh ? '战斗交流' : 'Combat communication' }}</strong></div>
      <slot name="tools" />
    </header>
    <p>{{ zh ? '这里可以说话或回复队友，不会触发检定；机械行动请打开战斗工具。' : 'Talk to the party here without triggering checks. Use the combat tool for mechanical actions.' }}</p>
    <!-- 宿主级提示条（如房主预览模式）；贴在输入行上方。 -->
    <slot name="before-input" />
    <div class="combat-message-row">
      <textarea
        v-model="text"
        maxlength="500"
        :disabled="busy"
        :placeholder="zh ? '例如：我来挡住它，你先后退！' : 'For example: I will hold it off—fall back!'"
        @keydown.ctrl.enter.prevent="submit"
      />
      <button class="combat-tool-button" type="button" :disabled="busy" :title="zh ? '打开战斗工具' : 'Open combat tool'" @click="emit('openCombat')"><NIcon :component="ShieldOutline" /><span>{{ zh ? '战斗工具' : 'Combat' }}</span></button>
      <button type="button" class="primary" :disabled="busy || !text.trim()" @click="submit">{{ busy ? (zh ? '发送中…' : 'Sending…') : (zh ? '发送发言' : 'Send') }}</button>
    </div>
    <small v-if="notice">{{ notice }}</small>
  </section>
</template>

<style scoped>
.combat-message-composer { display: grid; gap: 7px; padding: 11px; border: 1px solid var(--df-border-soft); border-radius: 12px; background: color-mix(in srgb, var(--df-surface-raised) 92%, transparent); }
.combat-message-composer header, .combat-message-composer header > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.combat-message-composer header > div { justify-content: flex-start; color: var(--df-interactive-strong); }
.combat-message-composer p, .combat-message-composer small { margin: 0; color: var(--df-text-muted); font-size: 12px; }
.combat-message-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 7px; }
.combat-message-row textarea { min-height: 46px; max-height: 100px; resize: vertical; }
.combat-message-row button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 44px; }
@media (max-width: 700px) {
  .combat-message-composer { padding: 9px; }
  .combat-message-composer p { display: none; }
  .combat-message-row { grid-template-columns: minmax(0, 1fr) 44px 76px; }
  .combat-message-row .combat-tool-button { padding: 0; }
  .combat-message-row .combat-tool-button span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  .combat-message-row textarea { min-height: 42px; }
}
</style>
