<script setup lang="ts">
import { computed } from 'vue'
import { useLocale } from '@/composables/useLocale'
import type { CharacterItem } from '@/api/types'
import HelpButton from '@/components/common/HelpButton.vue'

const props = defineProps<{
  equipment: CharacterItem[]
  inventory: CharacterItem[]
}>()
const emit = defineEmits<{ 'update:equipment': [v: CharacterItem[]]; 'update:inventory': [v: CharacterItem[]] }>()

const { t } = useLocale()

const typeOptions = computed(() => [
  { value: 'weapon', label: t('itemTypeWeapon') },
  { value: 'armor', label: t('itemTypeArmor') },
  { value: 'item', label: t('itemTypeItem') },
])
const slotOptions = computed(() => [
  { value: 'main_hand', label: t('itemSlotMainHand') },
  { value: 'off_hand', label: t('itemSlotOffHand') },
  { value: 'armor', label: t('itemSlotArmor') },
  { value: 'head', label: t('itemSlotHead') },
  { value: 'none', label: t('itemSlotNone') },
])

function setEquipment(next: CharacterItem[]) { emit('update:equipment', next) }
function setInventory(next: CharacterItem[]) { emit('update:inventory', next) }
</script>

<template>
  <div class="item-editor">
    <div class="item-section">
      <label>{{ t('equipment') }} <HelpButton :title="t('equipmentHelpTitle')">
        <p style="white-space:pre-line">{{ t('equipmentHelpContent') }}</p>
      </HelpButton></label>
      <div v-for="(it, i) in equipment" :key="i" class="item-row">
        <input v-model="it.name" :placeholder="t('itemName')" class="item-name">
        <select v-model="it.type" class="item-sel">
          <option v-for="o in typeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <input type="number" v-model.number="it.damage" :placeholder="t('damage')" class="item-num">
        <select v-model="it.slot" class="item-sel">
          <option v-for="o in slotOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <button type="button" class="item-del" @click="setEquipment(equipment.filter((_, x) => x !== i))">×</button>
      </div>
      <button type="button" class="add-item" @click="setEquipment([...equipment, { name: '', type: 'weapon', damage: 0, slot: 'main_hand', quality: 'common' }])">+ {{ t('addItem') }}</button>
    </div>
    <div class="item-section">
      <label>{{ t('inventory') }}</label>
      <div v-for="(it, i) in inventory" :key="i" class="item-row">
        <input v-model="it.name" :placeholder="t('itemName')" class="item-name">
        <input type="number" v-model.number="it.qty" :placeholder="t('qty')" class="item-num">
        <input v-model="it.effect" :placeholder="t('effect')" class="item-eff">
        <button type="button" class="item-del" @click="setInventory(inventory.filter((_, x) => x !== i))">×</button>
      </div>
      <button type="button" class="add-item" @click="setInventory([...inventory, { name: '', qty: 1, effect: '' }])">+ {{ t('addItem') }}</button>
    </div>
  </div>
</template>

<style scoped>
.item-section{margin:8px 0}
.item-row{display:flex;gap:6px;align-items:center;margin:4px 0}
.item-name{flex:1;min-width:0}
.item-num{width:64px}
.item-eff{flex:1;min-width:0}
.item-sel{width:96px}
.item-del{padding:2px 8px;border-radius:6px;border:1px solid rgba(128,128,128,.3);background:transparent;color:inherit;cursor:pointer}
.add-item{margin-top:4px;padding:4px 12px;border-radius:6px;border:1px dashed rgba(128,128,128,.4);background:transparent;color:inherit;cursor:pointer;font-size:13px}
</style>