<script setup lang="ts">
import { computed } from 'vue'
import type { MapData } from '@/api/types'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'
import {
  resolveRulesetPlayExtension,
  type RulesetPlayTool,
} from './registry'

const props = withDefaults(defineProps<{
  runtimeId: string
  activeTool: RulesetPlayTool
  hasCampaign?: boolean
  hasCombat?: boolean
  gameKey: string
  actorId: string
  characterName?: string
  sceneName?: string
  worldName?: string
  map?: MapData
  isGm: boolean
  refreshKey?: number
}>(), {
  hasCampaign: false,
  hasCombat: false,
  characterName: '',
  sceneName: '',
  worldName: '',
  refreshKey: 0,
})

const emit = defineEmits<{
  close: []
  refresh: []
  navigate: [target: RulesetPlayTool]
  'open-map': []
}>()
const { locale } = useLocale()
const extension = computed(() => resolveRulesetPlayExtension(props.runtimeId))
const copy = computed(() => extension.value?.copy(String(locale.value)) || {
  menu: 'Ruleset tools', campaign: 'Campaign', combat: 'Combat', title: 'Ruleset tools',
})
</script>

<template>
  <Modal :title="copy.title" dialog-class="dnd-toolbox-dialog" @close="emit('close')">
    <div v-if="extension" class="ruleset-play-host">
    <nav class="dnd-toolbox-tabs" :aria-label="copy.menu">
      <button
        v-if="hasCampaign && extension.campaign"
        :class="{ active: activeTool === 'campaign' }"
        @click="emit('navigate', 'campaign')"
      >{{ copy.campaign }}</button>
      <button
        v-if="hasCombat && extension.combat"
        :class="{ active: activeTool === 'combat' }"
        @click="emit('navigate', 'combat')"
      >{{ copy.combat }}</button>
    </nav>
    <component
      :is="extension.campaign"
      v-if="activeTool === 'campaign' && hasCampaign && extension.campaign"
      :game-key="gameKey"
      :actor-id="actorId"
      :character-name="characterName"
      :scene-name="sceneName"
      :world-name="worldName"
      :map="map"
      :is-gm="isGm"
      :refresh-key="refreshKey"
      @refresh="emit('refresh')"
      @navigate="emit('navigate', $event)"
      @open-map="emit('open-map')"
    />
    <component
      :is="extension.combat"
      v-else-if="activeTool === 'combat' && hasCombat && extension.combat"
      :game-key="gameKey"
      :actor-id="actorId"
      :is-gm="isGm"
      :refresh-key="refreshKey"
      @refresh="emit('refresh')"
      @navigate="emit('navigate', $event)"
    />
    </div>
    <p v-else class="error-banner">
      {{ String(locale).startsWith('zh') ? '当前规则尚未注册玩法工具界面。' : 'This ruleset has no registered play tools.' }}
    </p>
  </Modal>
</template>

<style scoped>
/* 标签栏固定高度，当前工具面板占满剩余空间并自己滚动（minmax(0,…) 才能让
   overflow:auto 生效；否则自动行会被内容撑开，滚动条永远不出现）。 */
.ruleset-play-host {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
  min-height: 0;
}
</style>
