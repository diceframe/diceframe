<script setup lang="ts">
import BattleAvatar from './BattleAvatar.vue'
import type { BattleToken } from './battleView'

export interface DockTab {
  id: string
  label: string
  /** Whether that part of the turn economy is still available. */
  available: boolean
  /** Short status such as "30 尺" or "已用". */
  note?: string
}

const props = defineProps<{
  own: BattleToken | null
  hpLabel: string
  tabs: DockTab[]
  activeTab: string
  dockLabel: string
}>()
const emit = defineEmits<{ 'update:activeTab': [id: string] }>()

// Tablist keyboard pattern: one tab stop, arrows move between tabs.
const STEPS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
function onTabKey(event: KeyboardEvent, index: number): void {
  const count = props.tabs.length
  const next = event.key in STEPS ? (index + STEPS[event.key]! + count) % count
    : event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 : -1
  if (next < 0) return
  event.preventDefault()
  emit('update:activeTab', props.tabs[next]!.id)
  const buttons = (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLElement>('[role="tab"]')
  buttons?.[next]?.focus()
}
</script>

<template>
  <section class="action-dock available-actions" :aria-label="dockLabel">
    <div v-if="own" class="dock-identity">
      <BattleAvatar :name="own.name" :side="own.side" :size="58" />
      <strong>{{ own.name }}</strong>
      <span class="dock-hp">{{ hpLabel }} {{ own.hp }}/{{ own.maxHp }}</span>
      <div class="dock-hp-bar"><i :style="{ width: `${own.hpRatio * 100}%` }" /></div>
      <slot name="identity" />
    </div>
    <div class="dock-main">
      <div class="dock-tabs" role="tablist" :aria-label="dockLabel">
        <button
          v-for="(tab, index) in tabs"
          :id="`dock-tab-${tab.id}`"
          :key="tab.id"
          type="button"
          role="tab"
          :aria-selected="tab.id === activeTab"
          :aria-controls="`dock-panel`"
          :tabindex="tab.id === activeTab ? 0 : -1"
          :class="['dock-tab', { active: tab.id === activeTab, spent: !tab.available }]"
          @click="emit('update:activeTab', tab.id)"
          @keydown="onTabKey($event, index)"
        >
          <slot :name="`tab-icon-${tab.id}`" />
          <span>{{ tab.label }}</span>
          <small v-if="tab.note">{{ tab.note }}</small>
        </button>
      </div>
      <div class="dock-options"><slot name="options" /></div>
      <div id="dock-panel" class="combat-actions dock-tiles" role="tabpanel" :aria-labelledby="`dock-tab-${activeTab}`"><slot name="tiles" /></div>
    </div>
    <div class="dock-summary"><slot name="summary" /></div>
  </section>
</template>

<style scoped>
.action-dock { display: grid; grid-template-columns: 150px minmax(0, 1fr) 230px; gap: 14px; padding: 12px; border: 1px solid rgb(228 183 94 / 40%); border-radius: 16px; background: linear-gradient(180deg, rgb(16 25 36 / 97%), rgb(8 13 20 / 97%)); box-shadow: 0 -8px 26px rgb(0 0 0 / 35%); color: #e8eef2; }
.dock-identity { display: grid; justify-items: center; align-content: center; gap: 4px; }
.dock-identity strong { max-width: 100%; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.dock-hp { color: #aebcc6; font-size: 11px; }
.dock-hp-bar { width: 100%; height: 6px; overflow: hidden; border-radius: 999px; background: rgb(0 0 0 / 55%); }
.dock-hp-bar i { display: block; height: 100%; background: linear-gradient(90deg, #3f9b5f, #7fd48d); }
.dock-main { display: grid; align-content: start; gap: 9px; min-width: 0; }
.dock-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
.dock-tab { display: flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; padding: 0 8px; border: 1px solid rgb(120 150 175 / 35%); border-radius: 10px; background: rgb(15 24 35 / 90%); color: #c9d6de; font-size: 13px; cursor: pointer; }
.dock-tab small { color: #9aa9b4; font-size: 10px; }
.dock-tab.active { border-color: #e4b75e; background: rgb(60 44 16 / 70%); color: #ffe5a8; box-shadow: 0 0 12px rgb(228 183 94 / 30%); }
.dock-tab.spent:not(.active) { opacity: .5; }
.dock-tab:focus-visible { outline: 3px solid #e4b75e; outline-offset: 2px; }
.dock-options { display: grid; gap: 8px; min-width: 0; }
.dock-options:empty { display: none; }
.dock-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.dock-summary { display: grid; align-content: stretch; gap: 8px; min-width: 0; }
@media (max-width: 960px) {
  .action-dock { grid-template-columns: minmax(0, 1fr); }
  .dock-identity { grid-template-columns: auto 1fr; justify-items: start; column-gap: 10px; text-align: left; }
  .dock-identity :deep(.battle-avatar) { grid-row: span 3; }
  .dock-tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
