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
const { locale, t } = useLocale()
const extension = computed(() => resolveRulesetPlayExtension(props.runtimeId))
const copy = computed(() => extension.value?.copy(String(locale.value)) || {
  menu: 'Ruleset tools', campaign: 'Campaign', combat: 'Combat', title: 'Ruleset tools',
})
</script>

<template>
  <Modal :title="copy.title" dialog-class="dnd-toolbox-dialog" @close="emit('close')">
    <!-- 关闭按钮和工具标签同一行：标题行收起后不要再多占一条。 -->
    <div class="dnd-toolbox-bar">
      <nav v-if="extension" class="dnd-toolbox-tabs" :aria-label="copy.menu">
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
      <button
        type="button"
        class="dnd-toolbox-close"
        data-testid="dnd-toolbox-close"
        :aria-label="t('close')"
        :title="t('close')"
        @click="emit('close')"
      >✕</button>
    </div>
    <div v-if="extension" class="ruleset-play-host">
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
/* 标签栏已移到外面的 .dnd-toolbox-bar；这里只剩当前工具面板，占满剩余空间
   并自己滚动（minmax(0,…) 才能让 overflow:auto 生效；否则自动行会被内容撑开，
   滚动条永远不出现）。 */
.ruleset-play-host {
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
}
</style>
