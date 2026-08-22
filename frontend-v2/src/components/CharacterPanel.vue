<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CharacterItem, CharacterSheet, CharacterSkill, Player, RuleAttribute, RuleMeta } from '@/api/types'
import { attrDisplayName, getCurrencyAmount, getResourceValue, currencyLabel, type RuleAttr } from '@/utils/ruleSchema'
import { buildSpecialStats, primaryResourceList } from '@/utils/play'
import { useLocale } from '@/composables/useLocale'
import PortraitImage from '@/components/PortraitImage.vue'
import Modal from '@/components/ui/Modal.vue'
import LevelUpDialog from '@/components/admin/LevelUpDialog.vue'
import GeneratedImageThumbnail from '@/components/common/GeneratedImageThumbnail.vue'

const props = defineProps<{ player?: Player; ruleMeta?: RuleMeta | null; portraitEditable?: boolean }>()
const emit = defineEmits<{ 'portrait-click': []; 'allocate-level-up': [attrs: Record<string, number>] }>()
const { t } = useLocale()
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
    return { key: k, name: def ? attrDisplayName(def) : attrDisplayName({ key: k, min: 0, max: 0 }), value: a[k] }
  })
})
const specials = computed(() => buildSpecialStats(cs.value, props.ruleMeta?.rule_special_stats))
const primaries = computed(() => primaryResourceList(cs.value, props.ruleMeta))
function pct(cur: number, max: number) { return Math.max(0, Math.min(100, cur / Math.max(1, max) * 100)) }

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
  if (typeof it.damage === 'number' && it.damage) parts.push(`${t('damage')} ${it.damage}`)
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
  for (const [key, value] of Object.entries(s)) {
    if (key === 'name' || key === 'value' || key === 'type' || key === 'key') continue
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
        <span v-if="cs.status" class="tag tag-warn">{{ cs.status }}</span>
        <span class="gold">{{ currencyName }} {{ gold }}</span>
      </div>
    </div>
    <div class="character-vitals">
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
      <span v-for="a in attrs" :key="a.key">{{ a.name }} {{ a.value }}</span>
    </div>
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
.level-up-notice{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 0 4px;padding:7px 9px;border:1px solid var(--df-border-soft);border-radius:var(--df-radius-sm);background:var(--df-hover)}
.level-up-notice span{color:var(--df-warning);font-size:12px;line-height:1.3}
.level-up-notice button{padding:2px 10px;font-size:12px;white-space:nowrap}
.skill-detail-list {
  flex-direction: column;
  align-items: stretch;
}
.skill-detail-meta {
  flex-basis: 100%;
  white-space: pre-line;
}
</style>
