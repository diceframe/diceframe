<script setup lang="ts">
import { computed } from 'vue'
import type { CharacterSheet, Player, RuleAttribute, RuleMeta } from '../api/types'
import { attrDisplayName, getCurrencyAmount, getResourceValue, currencyLabel } from '../utils/ruleSchema'
import { buildSpecialStats, primaryResourceList } from '../utils/play'

const props = defineProps<{ player?: Player; ruleMeta?: RuleMeta | null }>()
function label(item: unknown) { if (typeof item === 'string') return item; if (item && typeof item === 'object' && 'name' in item) return String((item as { name?: unknown }).name || JSON.stringify(item)); return JSON.stringify(item) }

const cs = computed<CharacterSheet>(() => props.player?.character_sheet || {})
const hp = computed(() => getResourceValue(cs.value, 'hp'))
const hpPct = computed(() => Math.max(0, Math.min(100, hp.value.current / Math.max(1, hp.value.max) * 100)))
const gold = computed(() => getCurrencyAmount(cs.value))
const currencyName = computed(() => currencyLabel(props.ruleMeta))
const attrs = computed(() => {
  const a = cs.value.attributes || {}
  const defs = ((props.ruleMeta?.attributes_schema as RuleAttribute[] | undefined) || props.ruleMeta?.attributes || [])
  return Object.keys(a).map(k => {
    const def = defs.find(d => d.key === k)
    return { key: k, name: def ? attrDisplayName(def) : k, value: a[k] }
  })
})
const specials = computed(() => buildSpecialStats(cs.value, props.ruleMeta?.rule_special_stats))
const primaries = computed(() => primaryResourceList(cs.value, props.ruleMeta))
function pct(cur: number, max: number) { return Math.max(0, Math.min(100, cur / Math.max(1, max) * 100)) }
</script>

<template>
  <section class="panel character" v-if="player">
    <header>
      <h2>角色状态</h2>
    </header>
    <div class="character-title">
      <h3>{{ player.character_name }}</h3>
      <span v-if="cs.deceased" class="tag tag-deceased">不可操作</span>
      <span v-if="cs.status" class="tag tag-warn">{{ cs.status }}</span>
      <span class="gold">{{ currencyName }} {{ gold }}</span>
    </div>
    <div class="hp"><span>HP</span><strong>{{ hp.current }} / {{ hp.max }}</strong></div>
    <div class="hpbar"><i :style="{ width: hpPct + '%' }" /></div>

    <div v-for="s in specials" :key="s.key" class="stat-row" :class="s.color">
      <div class="stat-head"><span class="stat-label">{{ s.name }}</span><strong>{{ s.current }}{{ s.max ? ' / ' + s.max : '' }}</strong></div>
      <div v-if="s.max" class="hpbar"><i :style="{ width: pct(s.current, s.max) + '%' }" /></div>
    </div>

    <div v-for="r in primaries" :key="r.key" class="stat-row">
      <div class="stat-head"><span class="stat-label">{{ r.label }}</span><strong>{{ r.current }} / {{ r.max }}</strong></div>
      <div class="hpbar"><i :style="{ width: pct(r.current, r.max) + '%' }" /></div>
    </div>

    <div class="chips">
      <span v-for="a in attrs" :key="a.key">{{ a.name }} {{ a.value }}</span>
    </div>
    <details><summary>技能</summary>
      <div class="chips">
        <span v-for="s in cs.skills || []" :key="label(s)">{{ label(s) }}<template v-if="typeof s === 'object'"> {{ s.value }}</template></span>
      </div>
    </details>
    <details><summary>装备与背包</summary>
      <p><strong>装备：</strong>{{ (cs.equipment || []).map(label).join('、') || '无' }}</p>
      <p><strong>背包：</strong>{{ (cs.inventory || []).map(label).join('、') || '无' }}</p>
      <p><strong>关键物品：</strong>{{ (cs.key_items || []).map(label).join('、') || '无' }}</p>
    </details>
  </section>
</template>
