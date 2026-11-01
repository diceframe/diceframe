<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterItem, CharacterSheet, CharacterSkill, Player, RuleAttribute, RuleMeta } from '@/api/types'
import { attrDisplayName, getCurrencyAmount, getResourceValue, currencyLabel, type RuleAttr } from '@/utils/ruleSchema'
import { formatCurrencyAmount } from '@/utils/currency'
import { buildSpecialStats, primaryResourceList } from '@/utils/play'
import { useLocale } from '@/composables/useLocale'
import PortraitImage from '@/components/PortraitImage.vue'
import Modal from '@/components/ui/Modal.vue'
import LevelUpDialog from '@/components/admin/LevelUpDialog.vue'
import GeneratedImageThumbnail from '@/components/common/GeneratedImageThumbnail.vue'

const props = defineProps<{ player?: Player; ruleMeta?: RuleMeta | null; portraitEditable?: boolean; showAdvancedCenter?: boolean }>()
const emit = defineEmits<{ 'portrait-click': []; 'allocate-level-up': [attrs: Record<string, number>]; 'open-character-center': [] }>()
const { t } = useLocale()
function label(item: unknown) { if (typeof item === 'string') return item; if (item && typeof item === 'object' && 'name' in item) return String((item as { name?: unknown }).name || JSON.stringify(item)); return JSON.stringify(item) }

const cs = computed<CharacterSheet>(() => props.player?.character_sheet || {})
const hp = computed(() => getResourceValue(cs.value, 'hp'))
function hasNumericValue(value: unknown): boolean {
  return value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value))
}
const hasHp = computed(() => {
  const resource = cs.value.resources?.hp
  return Boolean(
    (resource && (hasNumericValue(resource.current) || hasNumericValue(resource.max)))
    || hasNumericValue(cs.value.hp)
    || hasNumericValue(cs.value.max_hp),
  )
})
const hpPct = computed(() => Math.max(0, Math.min(100, hp.value.current / Math.max(1, hp.value.max) * 100)))
const gold = computed(() => getCurrencyAmount(cs.value))
// canonical 金额统一经 formatter（25 美分 → $0.25），无 currency_system 时回退「label 金额」。
const goldText = computed(() => formatCurrencyAmount(
  gold.value,
  props.ruleMeta?.currency_system || null,
  currencyLabel(props.ruleMeta),
))
const attrs = computed(() => {
  const a = cs.value.attributes || {}
  const defs = ((props.ruleMeta?.attributes_schema as RuleAttribute[] | undefined) || props.ruleMeta?.attributes || [])
  return Object.keys(a).map(k => {
    const def = defs.find(d => d.key === k)
    return { key: k, name: def ? attrDisplayName(def) : attrDisplayName({ key: k, min: 0, max: 0 }), value: a[k] }
  })
})
const specials = computed(() => buildSpecialStats(cs.value, props.ruleMeta?.rule_special_stats))
const primaries = computed(() => primaryResourceList(cs.value, props.ruleMeta))
function pct(cur: number, max: number) { return Math.max(0, Math.min(100, cur / Math.max(1, max) * 100)) }
/** Keep the compact status rail readable when a ruleset exposes several resources. */
function resourceColor(key: string): string {
  const normalized = String(key || '').toLowerCase().replace(/[^a-z0-9]+/g, '_')
  if (normalized === 'hp' || normalized === 'health' || normalized === 'hit_points') return 'stat-hp'
  if (normalized === 'sanity' || normalized === 'san') return 'stat-sanity'
  if (normalized === 'luck') return 'stat-luck'
  if (normalized === 'qi' || normalized === 'mana' || normalized === 'mp' || normalized === 'magic') return 'stat-qi'
  if (normalized === 'stamina' || normalized === 'energy' || normalized === 'endurance') return 'stat-stamina'
  if (normalized === 'humanity') return 'stat-humanity'
  if (normalized === 'heat' || normalized === 'stress') return 'stat-heat'
  if (normalized.includes('cyber') || normalized === 'body') return 'stat-cyber'
  return 'stat-resource'
}

const PREVIEW_LIMIT = 4
const showItemsModal = ref(false)
const showSkillsModal = ref(false)
const equipment = computed<CharacterItem[]>(() => cs.value.equipment || [])
const inventory = computed<CharacterItem[]>(() => cs.value.inventory || [])
const keyItems = computed<CharacterItem[]>(() => cs.value.key_items || [])
const hasItems = computed(() => !!(equipment.value.length || inventory.value.length || keyItems.value.length))

function itemName(it: CharacterItem): string {
  return it.name || label(it)
}
function itemQty(it: CharacterItem): number | undefined {
  const q = it.qty
  return typeof q === 'number' && q > 1 ? q : undefined
}
function itemAssetId(it: CharacterItem): string {
  const image = it.image
  return image && typeof image === 'object' && 'asset_id' in image ? String(image.asset_id || '') : ''
}
function typeLabel(type?: string): string {
  if (type === 'weapon') return t('itemTypeWeapon')
  if (type === 'armor') return t('itemTypeArmor')
  if (type === 'item') return t('itemTypeItem')
  return ''
}
function slotLabel(slot?: string): string {
  if (slot === 'main_hand') return t('itemSlotMainHand')
  if (slot === 'off_hand') return t('itemSlotOffHand')
  if (slot === 'armor') return t('itemSlotArmor')
  if (slot === 'head') return t('itemSlotHead')
  if (slot === 'none') return t('itemSlotNone')
  return ''
}
function equipmentDetail(it: CharacterItem): string {
  const parts: string[] = []
  const ty = typeLabel(it.type); if (ty) parts.push(ty)
  if (it.slot && it.slot !== 'none') { const sl = slotLabel(it.slot); if (sl) parts.push(sl) }
  if (it.damage_dice) parts.push(`${t('damage')} ${it.damage_dice}`)
  else if (typeof it.damage === 'number' && it.damage) parts.push(`${t('damage')} ${it.damage}`)
  if (it.quality) parts.push(it.quality)
  return parts.join(' · ')
}
function inventoryDetail(it: CharacterItem): string {
  return it.effect ? `${t('effect')}: ${it.effect}` : ''
}
function keyItemDetail(it: CharacterItem): string {
  const parts: string[] = []
  if (it.category) parts.push(it.category)
  if (it.note) parts.push(it.note)
  return parts.join(' · ')
}
function restCount(len: number): number {
  return Math.max(0, len - PREVIEW_LIMIT)
}
function skillDetail(s: string | CharacterSkill): string {
  if (typeof s === 'string') return ''
  const parts: string[] = []
  // effect 是玩家填写的技能说明：正式识别为「效果：…」，不再按通用 key: value 展示。
  const effect = String(s.effect || '').trim()
  if (effect) parts.push(t('skillEffectLine', { effect }))
  for (const [key, value] of Object.entries(s)) {
    if (key === 'name' || key === 'value' || key === 'type' || key === 'key' || key === 'effect') continue
    if (value === undefined || value === null || value === '') continue
    parts.push(`${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
  }
  return parts.join('\n')
}
function skillTitle(s: string | CharacterSkill): string {
  const name = label(s)
  const value = typeof s === 'object' && s.value !== undefined ? ` (${s.value})` : ''
  const detail = skillDetail(s)
  return detail ? `${name}${value}\n\n${detail}` : ''
}

const levelUpPoints = computed(() => Number(cs.value.level_up_points || 0))
const statusLabel = computed(() => {
  const s = String(cs.value.status || '')
  if (s === 'downed') return t('statusDowned')
  if (s === 'stable') return t('statusStable')
  return s
})
const deathSaves = computed<{ success: number; failure: number } | null>(() => {
  const ds = cs.value.death_saves
  if (!ds || typeof ds !== 'object') return null
  return { success: Number((ds as Record<string, unknown>).success || 0), failure: Number((ds as Record<string, unknown>).failure || 0) }
})
const showLevelUp = ref(false)
const fallbackLevelUpAttrs: RuleAttr[] = [
  { key: 'str', name: '力量', name_en: 'STR', min: 1, max: 100 },
  { key: 'con', name: '体质', name_en: 'CON', min: 1, max: 100 },
  { key: 'dex', name: '敏捷', name_en: 'DEX', min: 1, max: 100 },
  { key: 'int', name: '智力', name_en: 'INT', min: 1, max: 100 },
  { key: 'wis', name: '感知', name_en: 'WIS', min: 1, max: 100 },
  { key: 'cha', name: '魅力', name_en: 'CHA', min: 1, max: 100 },
]
const levelUpAttrs = computed<RuleAttr[]>(() => {
  const defs = props.ruleMeta?.attributes as RuleAttribute[] | undefined
  return defs && defs.length ? defs : fallbackLevelUpAttrs
})
function submitLevelUp(attrs: Record<string, number>) {
  showLevelUp.value = false
  emit('allocate-level-up', attrs)
}
</script>

<template>
  <section class="panel character" v-if="player">
    <header>
      <h2>{{ t('characterStatus') }}</h2>
    </header>
    <div class="character-profile">
      <button v-if="portraitEditable" type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="emit('portrait-click')">
        <PortraitImage :portrait="cs.portrait" :rule-id="String(ruleMeta?.rule_id || '')" :seed="player.user_id" :name="player.character_name" :size="64" />
        <span>{{ t('changeAvatar') }}</span>
      </button>
      <PortraitImage v-else :portrait="cs.portrait" :rule-id="String(ruleMeta?.rule_id || '')" :seed="player.user_id" :name="player.character_name" :size="64" />
      <div class="character-title">
        <h3>{{ player.character_name }}</h3>
        <span v-if="cs.deceased" class="tag tag-deceased">{{ t('unavailable') }}</span>
        <span v-if="cs.status" class="tag tag-warn">{{ statusLabel }}</span>
        <span class="gold">{{ goldText }}</span>
      </div>
    </div>
    <div class="character-vitals">
      <template v-if="hasHp">
        <div class="hp stat-hp"><span>HP</span><strong>{{ hp.max > 0 ? `${hp.current} / ${hp.max}` : hp.current }}</strong></div>
        <div v-if="hp.max > 0" class="hpbar stat-hp"><i :style="{ width: hpPct + '%' }" /></div>
      </template>
      <div v-if="deathSaves && cs.status === 'downed'" class="stat-row">
        <div class="stat-head"><span class="stat-label">{{ t('deathSaves') }}</span><strong>{{ deathSaves.success }}✓ / {{ deathSaves.failure }}✗</strong></div>
      </div>

      <div v-for="s in specials" :key="s.key" class="stat-row" :class="s.color">
        <div class="stat-head"><span class="stat-label">{{ s.name }}</span><strong>{{ s.current }}{{ s.max ? ' / ' + s.max : '' }}</strong></div>
        <div v-if="s.max" class="hpbar"><i :style="{ width: pct(s.current, s.max) + '%' }" /></div>
      </div>

      <div v-for="r in primaries" :key="r.key" class="stat-row" :class="resourceColor(r.key)">
        <div class="stat-head"><span class="stat-label">{{ r.label }}</span><strong>{{ r.max > 0 ? `${r.current} / ${r.max}` : r.current }}</strong></div>
        <div v-if="r.max > 0" class="hpbar"><i :style="{ width: pct(r.current, r.max) + '%' }" /></div>
      </div>
    </div>

    <div v-if="levelUpPoints > 0" class="level-up-notice">
      <span>{{ t('pointsToAllocate', { points: levelUpPoints }) }}</span>
      <button type="button" class="primary" @click="showLevelUp = true">{{ t('allocateAttributePointsWithCount', { points: levelUpPoints }) }}</button>
    </div>
    <LevelUpDialog
      v-if="showLevelUp"
      :rule-attrs="levelUpAttrs"
      :rule-meta="ruleMeta"
      :character="player"
      :level-up-points="levelUpPoints"
      @submit="submitLevelUp"
      @cancel="showLevelUp = false"
    />
    <div class="chips character-attributes">
      <span v-for="a in attrs" :key="a.key"><small>{{ a.name }}</small> <strong>{{ a.value }}</strong></span>
    </div>
    <button v-if="showAdvancedCenter" type="button" class="character-center-trigger" @click="emit('open-character-center')">{{ t('advancedCharacterCenter') }}</button>
    <details class="character-detail-block"><summary>{{ t('skills') }}</summary>
      <div class="chips">
        <span v-for="(s, i) in cs.skills || []" :key="'s' + i" :title="skillTitle(s) || undefined">{{ label(s) }}<template v-if="typeof s === 'object' && s.value !== undefined"> {{ s.value }}</template></span>
      </div>
      <button v-if="(cs.skills || []).length" type="button" class="item-view-all" @click="showSkillsModal = true">{{ t('viewAllDetails') }}</button>
    </details>
    <details class="character-detail-block"><summary>{{ t('equipmentAndInventory') }}</summary>
      <div class="item-groups">
        <div v-if="equipment.length" class="item-group">
          <span class="item-label item-label-equipment">{{ t('equipment') }}</span>
          <div class="item-chips">
            <span v-for="(it, i) in equipment.slice(0, PREVIEW_LIMIT)" :key="'e'+i" class="item-chip"><GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="22" />{{ itemName(it) }}</span>
            <span v-if="restCount(equipment.length)" class="item-more-count">{{ t('moreItems', { count: restCount(equipment.length) }) }}</span>
          </div>
        </div>
        <div v-if="inventory.length" class="item-group">
          <span class="item-label item-label-inventory">{{ t('inventory') }}</span>
          <div class="item-chips">
            <span v-for="(it, i) in inventory.slice(0, PREVIEW_LIMIT)" :key="'i'+i" class="item-chip"><GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="22" />{{ itemName(it) }}<template v-if="itemQty(it)"> ×{{ itemQty(it) }}</template></span>
            <span v-if="restCount(inventory.length)" class="item-more-count">{{ t('moreItems', { count: restCount(inventory.length) }) }}</span>
          </div>
        </div>
        <div v-if="keyItems.length" class="item-group">
          <span class="item-label item-label-keyitems">{{ t('keyItems') }}</span>
          <div class="item-chips">
            <span v-for="(it, i) in keyItems.slice(0, PREVIEW_LIMIT)" :key="'k'+i" class="item-chip"><GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="22" />{{ itemName(it) }}</span>
            <span v-if="restCount(keyItems.length)" class="item-more-count">{{ t('moreItems', { count: restCount(keyItems.length) }) }}</span>
          </div>
        </div>
        <p v-if="!hasItems" class="item-empty">{{ t('none') }}</p>
        <button v-else type="button" class="item-view-all" @click="showItemsModal = true">{{ t('viewAllDetails') }}</button>
      </div>
    </details>

    <Modal v-if="showItemsModal" :title="t('equipmentAndInventory')" @close="showItemsModal = false">
      <div class="item-modal-body">
        <section v-if="equipment.length" class="item-detail-section">
          <h3 class="item-detail-h item-label-equipment">{{ t('equipment') }}</h3>
          <ul class="item-detail-list">
            <li v-for="(it, i) in equipment" :key="'e'+i" class="item-detail-row">
              <GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="48" />
              <span class="item-detail-name">{{ itemName(it) }}</span>
              <span v-if="equipmentDetail(it)" class="item-detail-meta">{{ equipmentDetail(it) }}</span>
            </li>
          </ul>
        </section>
        <section v-if="inventory.length" class="item-detail-section">
          <h3 class="item-detail-h item-label-inventory">{{ t('inventory') }}</h3>
          <ul class="item-detail-list">
            <li v-for="(it, i) in inventory" :key="'i'+i" class="item-detail-row">
              <GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="48" />
              <span class="item-detail-name">{{ itemName(it) }}<template v-if="itemQty(it)"> ×{{ itemQty(it) }}</template></span>
              <span v-if="inventoryDetail(it)" class="item-detail-meta">{{ inventoryDetail(it) }}</span>
            </li>
          </ul>
        </section>
        <section v-if="keyItems.length" class="item-detail-section">
          <h3 class="item-detail-h item-label-keyitems">{{ t('keyItems') }}</h3>
          <ul class="item-detail-list">
            <li v-for="(it, i) in keyItems" :key="'k'+i" class="item-detail-row">
              <GeneratedImageThumbnail v-if="itemAssetId(it)" :asset-id="itemAssetId(it)" :alt="itemName(it)" :size="48" />
              <span class="item-detail-name">{{ itemName(it) }}</span>
              <span v-if="keyItemDetail(it)" class="item-detail-meta">{{ keyItemDetail(it) }}</span>
            </li>
          </ul>
        </section>
        <p v-if="!hasItems" class="item-empty">{{ t('none') }}</p>
      </div>
    </Modal>

    <Modal v-if="showSkillsModal" :title="t('skills')" @close="showSkillsModal = false">
      <div class="item-modal-body">
        <ul class="item-detail-list skill-detail-list">
          <li v-for="(s, i) in cs.skills || []" :key="'skill' + i" class="item-detail-row">
            <span class="item-detail-name">{{ label(s) }}<template v-if="typeof s === 'object' && s.value !== undefined"> ({{ s.value }})</template></span>
            <span v-if="skillDetail(s)" class="item-detail-meta skill-detail-meta">{{ skillDetail(s) }}</span>
          </li>
        </ul>
        <p v-if="!(cs.skills || []).length" class="item-empty">{{ t('none') }}</p>
      </div>
    </Modal>
  </section>
</template>

<style scoped>
.character {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-md, 12px);
  background: var(--df-surface-1);
  color: var(--df-text);
}
.character > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 28px;
}
.character > header h2 {
  margin: 0;
  color: var(--df-accent-strong);
  font-size: 0.95rem;
  letter-spacing: .04em;
}
.character-profile {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 9px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-sm, 8px);
  background: var(--df-surface-2, var(--df-surface-1));
}
.portrait-edit-button {
  position: relative;
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--df-border-soft);
  border-radius: 10px;
  background: var(--df-control-bg, var(--df-surface-1));
  color: var(--df-text);
  cursor: pointer;
}
.portrait-edit-button span {
  position: absolute;
  inset: auto 2px 2px;
  padding: 2px 3px;
  border-radius: 4px;
  background: rgb(0 0 0 / 65%);
  color: #fff;
  font-size: 9px;
  line-height: 1.15;
}
.portrait-edit-button:hover { border-color: var(--df-accent); }
.character-title { display: flex; align-items: center; flex-wrap: wrap; gap: 5px 7px; min-width: 0; }
.character-title h3 { flex: 1 1 100%; min-width: 0; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 1.12rem; }
.character-title .gold { margin-left: auto; color: var(--df-warning); font-size: .82rem; font-weight: 700; }
.character-vitals { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 7px; }
.hp, .stat-row {
  --resource-color: var(--df-accent);
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid var(--df-border-soft);
  border-radius: var(--df-radius-sm, 8px);
  background: var(--df-surface-2, var(--df-surface-1));
}
.stat-row,
.hp.stat-hp { border-color: color-mix(in srgb, var(--resource-color) 42%, var(--df-border-soft)); background: color-mix(in srgb, var(--resource-color) 7%, var(--df-surface-2, var(--df-surface-1))); }
.stat-row .stat-label, .stat-row .stat-head strong, .hp.stat-hp span, .hp.stat-hp strong { color: color-mix(in srgb, var(--resource-color) 78%, var(--df-text)); }
.stat-hp { --resource-color: #e36b68; }
.stat-sanity { --resource-color: #b989d6; }
.stat-luck { --resource-color: #f5c842; }
.stat-qi { --resource-color: #4fb3a8; }
.stat-stamina { --resource-color: #64c4e8; }
.stat-cyber { --resource-color: #6f9ee8; }
.stat-humanity { --resource-color: #5fd38d; }
.stat-heat { --resource-color: #ff6b6b; }
.stat-resource { --resource-color: var(--df-interactive); }
.hp { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.hp span, .stat-label { color: var(--df-text-muted); font-size: .72rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.hp { grid-column: 1 / -1; }
.hp strong, .stat-head strong { font-size: .9rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.hpbar { grid-column: 1 / -1; height: 5px; overflow: hidden; border-radius: 999px; background: var(--df-border-soft); }
.hpbar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, color-mix(in srgb, var(--resource-color, var(--df-danger, #c65b5b)) 72%, #fff), var(--resource-color, var(--df-accent))); transition: width .2s ease; }
.stat-row { grid-column: 1 / -1; }
.stat-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.character-attributes, .character-specials { display: grid; grid-template-columns: repeat(auto-fit, minmax(88px, 1fr)); gap: 6px; }
.attribute, .special-stat { padding: 7px 8px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm, 8px); background: var(--df-surface-2, var(--df-surface-1)); }
.character-attributes > span { display: block; padding: 7px 8px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm, 8px); background: var(--df-surface-2, var(--df-surface-1)); font-size: .8rem; font-weight: 650; }
.attribute-name, .special-stat-name { display: block; overflow: hidden; color: var(--df-text-muted); font-size: .7rem; text-overflow: ellipsis; white-space: nowrap; }
.attribute-value, .special-stat-value { display: block; margin-top: 2px; font-size: 1rem; font-weight: 750; font-variant-numeric: tabular-nums; }
.character-detail-block { border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-sm, 8px); background: var(--df-surface-2, var(--df-surface-1)); }
.character-detail-block > summary { padding: 8px 10px; color: var(--df-text); cursor: pointer; font-size: .82rem; font-weight: 700; list-style-position: inside; }
.character-detail-block > summary::marker { color: var(--df-accent); }
.character-detail-block > .chips, .character-detail-block > .item-groups { padding: 0 10px 9px; }
.item-groups { display: grid; gap: 8px; }
.item-group { display: grid; gap: 5px; }
.item-label { color: var(--df-text-muted); font-size: .69rem; font-weight: 750; letter-spacing: .05em; text-transform: uppercase; }
.item-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.item-chip, .character-detail-block .chips > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 27px;
  max-width: 100%;
  padding: 4px 8px;
  border: 1px solid var(--df-border-soft);
  border-radius: 5px;
  background: linear-gradient(145deg, color-mix(in srgb, var(--df-surface-3) 58%, transparent), var(--df-surface-1));
  color: var(--df-text-secondary);
  font-size: .76rem;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-more-count { color: var(--df-text-muted); font-size: .72rem; align-self: center; }
.item-empty { margin: 0; color: var(--df-text-muted); font-size: .78rem; }
.item-view-all { width: fit-content; padding: 3px 0; border: 0; background: transparent; color: var(--df-accent-strong, var(--df-accent)); font-size: .75rem; cursor: pointer; }
.item-view-all:hover { text-decoration: underline; }
.level-up-notice{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 0 4px;padding:7px 9px;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-hover)}
.level-up-notice span{color:var(--df-warning);font-size:12px;line-height:1.3}
.level-up-notice button{padding:2px 10px;font-size:12px;white-space:nowrap}
.character-center-trigger{display:flex;align-items:center;justify-content:center;width:100%;min-height:38px;margin:10px 0 4px;padding:7px 10px;border:1px solid var(--df-accent);border-radius:var(--df-radius-sm);background:color-mix(in srgb,var(--df-accent) 12%,transparent);color:var(--df-text);font-weight:700;cursor:pointer}
.character-center-trigger:hover{background:color-mix(in srgb,var(--df-accent) 22%,transparent)}
.skill-detail-list {
  flex-direction: column;
  align-items: stretch;
}
.skill-detail-meta {
  flex-basis: 100%;
  white-space: pre-line;
}
@media (max-width: 560px) {
  .character { padding: 10px; }
  .character-vitals { grid-template-columns: 1fr; }
  .hp, .stat-row { grid-column: 1; }
  .hpbar { grid-column: 1; }
  .character-attributes, .character-specials { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
