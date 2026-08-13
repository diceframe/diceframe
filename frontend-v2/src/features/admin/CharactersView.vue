<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, apiBlob } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterItem, CharacterListResponse, CharacterPortrait, CharacterSchemaResponse, CharacterSheet, CharacterSkill, JsonObject, RuleMeta, RulesResponse, RuleSummary, SkillSpec, WorldListResponse, WorldSummary } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { importTavernCard } from '@/utils/characterImport'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocale } from '@/composables/useLocale'
import Modal from '@/components/ui/Modal.vue'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import SkillEditor from '@/components/admin/SkillEditor.vue'
import LevelUpDialog from '@/components/admin/LevelUpDialog.vue'
import ItemEditor from '@/components/admin/ItemEditor.vue'
import PortraitImage from '@/components/PortraitImage.vue'
import PortraitPicker from '@/components/admin/PortraitPicker.vue'
import {
  identitySchema, identityLabel, getIdentityValue, setIdentityUpdate,
  currencyLabel, getCurrencyAmount, getResourceValue,
  isAutoHpRule, calcAutoHp, attrDisplayName,
  type IdentityField, type RuleAttr,
} from '@/utils/ruleSchema'

interface CharacterData extends CharacterListResponse { cards: CharacterCard[] }
interface ResourceEdit { current: number; max: number }
interface CharacterEditForm {
  player: import('@/api/types').Player
  user_id: string
  character_name: string
  level: number
  hp: ResourceEdit
  gold: number
  attributes: Record<string, number>
  skills: CharacterSkill[]
  background: string
  equipment: CharacterItem[]
  inventory: CharacterItem[]
  keyText: string
  fields: IdentityField[]
  identityValues: Record<string, string>
  portrait?: CharacterPortrait | null
}
interface LevelUpState { player: import('@/api/types').Player; levelUpPoints: number }
interface NpcPortraitEdit { npcId: string; name: string; portrait?: CharacterPortrait | null }
interface CardEditForm {
  card_id: string
  character_name: string
  race: string
  class: string
  skills: CharacterSkill[]
  background: string
  gold: number
  portrait?: CharacterPortrait | null
  rule_id?: string
}
interface CharacterCardPatch extends JsonObject {
  character_name: string
  race: string
  class: string
  skills: CharacterSkill[]
  background: string
  gold: number
  portrait?: CharacterPortrait | null
}
interface UpdateCharacterPayload extends JsonObject {
  character_name: string
  level: number
  gold: number
  currency: { amount: number }
  progression: { level: number; xp: unknown }
  attributes: Record<string, number>
  skills: CharacterSkill[]
  background: string
  hp: number
  max_hp: number
  resources: { hp: { current: number; max: number; min: number } }
  identity?: Record<string, string>
  equipment?: CharacterItem[]
  inventory?: CharacterItem[]
  key_items?: CharacterItem[]
  portrait?: CharacterPortrait | null
}

const toast = useToast()
const { confirm } = useConfirm()
const { locale, t } = useLocale()

const game = ref(readCurrentGame())
const data = ref<CharacterData | null>(null)
const error = ref('')
const busy = ref(false)
const edit = ref<CharacterEditForm | null>(null)
const editLevelUp = ref<LevelUpState | null>(null)
const editCard = ref<CardEditForm | null>(null)
const editNpcPortrait = ref<NpcPortraitEdit | null>(null)
const showWizard = ref(false)
const characterSearch = ref('')
const characterSort = ref<'name' | 'rule'>('name')
const libraryView = ref<'grid' | 'list'>('grid')
const filteredCards = computed(() => {
  const keyword = characterSearch.value.trim().toLocaleLowerCase()
  const cards = [...(data.value?.cards || [])]
  const filtered = keyword
    ? cards.filter(card => [card.character_name, card.race, card.class, card.rule_name, card.rule_id].some(value => String(value || '').toLocaleLowerCase().includes(keyword)))
    : cards
  return filtered.sort((a, b) => {
    if (characterSort.value === 'rule') return cardRuleLabel(a).localeCompare(cardRuleLabel(b))
    return String(a.character_name || '').localeCompare(String(b.character_name || ''))
  })
})

const rules = ref<RuleSummary[]>([])
const ruleMeta = ref<RuleMeta>({})
const ruleAttrs = ref<RuleAttr[]>([])
const ruleAttrsTotal = ref(60)
const ruleId = ref('')
const ruleDetail = ref<{ skill_pool?: Array<string | SkillSpec>; skills?: Array<string | SkillSpec> } | null>(null)
const ruleSchemaLoading = ref(false)

const skillPool = computed<Array<string | SkillSpec>>(() => {
  const detail = ruleDetail.value || {}
  return detail.skill_pool || detail.skills || []
})
const editRuleAttrs = computed<RuleAttr[]>(() => {
  if (!edit.value) return ruleAttrs.value
  if (ruleAttrs.value.length) return ruleAttrs.value
  const attrs = edit.value.attributes || {}
  return Object.keys(attrs).map(key => ({ key, name: key, min: 0, max: Math.max(100, Number(attrs[key]) || 100) }))
})

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || t('operationFailed')) }
function toSkillList(input: CharacterSheet['skills']): CharacterSkill[] {
  return (input || []).map(s => typeof s === 'string' ? { name: s, value: 20 } : { name: s.name || '', value: s.value || 20 })
}
function itemLines(items: CharacterItem[] | undefined, fields: Array<keyof CharacterItem>, defaults: Record<string, string | number>): string {
  return (items || []).map(item => fields.map(field => String(item[field] ?? defaults[String(field)] ?? '')).join('|')).join('\n')
}
function parseLines<T extends CharacterItem>(text: string, fn: (p: string[]) => T): T[] {
  const t = text.trim()
  if (!t) return []
  return t.split('\n').map(l => fn(l.split('|').map(x => x.trim())))
}
function cardId(card: CharacterCard): string { return String(card.card_id || card.id || '') }
function ruleNameOf(rule: RuleSummary): string {
  return String(locale.value).startsWith('en')
    ? String(rule.rule_name_en || rule.rule_name || rule.rule_id)
    : String(rule.rule_name || rule.rule_id)
}
function cardRuleLabel(card: CharacterCard): string {
  if (!card.rule_id) return t('unboundRule')
  const rule = rules.value.find(candidate => candidate.rule_id === card.rule_id)
  return rule ? ruleNameOf(rule) : String(card.rule_name || card.rule_id)
}
function currentRuleBinding(): Pick<CharacterCard, 'rule_id' | 'rule_name' | 'rule_version' | 'mechanics' | 'language'> {
  const rule = rules.value.find(candidate => candidate.rule_id === ruleId.value)
  return {
    rule_id: ruleId.value,
    rule_name: String(ruleMeta.value.rule_name || rule?.rule_name || ruleId.value),
    rule_version: String(ruleMeta.value.rule_version || ''),
    mechanics: String(ruleMeta.value.mechanics || ''),
    language: String(locale.value),
  }
}
function levelUpPoints(player: import('@/api/types').Player): number { return Number(player.character_sheet?.level_up_points || 0) }
function npcKey(card: CharacterCard): string { return String(card.npc_id || card.id || card.card_id || card.name || card.character_name || Math.random()) }
function npcSummary(card: CharacterCard): string {
  return [
    card.relation ? `${t('relationshipPrefix')} ${card.relation}` : '',
    card.status ? `${t('statusPrefix')} ${card.status}` : '',
    card.first_seen_round ? `${t('firstSeenRound')} ${card.first_seen_round}` : '',
  ].filter(Boolean).join(' · ')
}

function hpPercent(player: import('@/api/types').Player): number {
  const current = Number(player.character_sheet?.hp || 0)
  const maximum = Math.max(1, Number(player.character_sheet?.max_hp || 1))
  return Math.max(0, Math.min(100, (current / maximum) * 100))
}

watch([ruleId, locale], async ([id]) => {
  if (!id) { ruleDetail.value = null; return }
  ruleSchemaLoading.value = true
  try {
    const schema = await api<CharacterSchemaResponse>(
      `/rules/${encodeURIComponent(String(id))}/character-schema?language=${encodeURIComponent(String(locale.value))}`,
    )
    if (!schema.ok) throw new Error(schema.error || t('ruleLoadFailed'))
    if (!game.value) {
      ruleMeta.value = schema.rule_meta || {}
      ruleAttrs.value = schema.rule_attrs || []
      ruleAttrsTotal.value = Number(schema.rule_attrs_total || 60)
    }
    ruleDetail.value = { skill_pool: schema.skill_pool || [] }
  } catch (e: unknown) {
    ruleDetail.value = null
    error.value = errorMessage(e)
  } finally { ruleSchemaLoading.value = false }
})

async function load() {
  error.value = ''; data.value = null
  try {
    if (game.value) {
      const [chars, cards, availableRules] = await Promise.all([
        api<CharacterListResponse>(`/games/${encodeURIComponent(game.value)}/characters`),
        api<CharacterCardsResponse>('/character-cards'),
        api<RulesResponse>('/rules'),
      ])
      rules.value = availableRules.rules || []
      data.value = { ...chars, cards: cards.cards || [] }
      ruleMeta.value = chars.rule_meta || {}
      ruleAttrs.value = chars.rule_attrs || []
      ruleAttrsTotal.value = chars.rule_attrs_total || 60
      ruleId.value = String(ruleMeta.value.rule_id || '')
    } else {
      const [cards, availableRules] = await Promise.all([
        api<CharacterCardsResponse>('/character-cards'),
        api<RulesResponse>('/rules'),
      ])
      rules.value = availableRules.rules || []
      data.value = { players: [], cards: cards.cards || [] }
      if (!ruleId.value || !rules.value.some(rule => rule.rule_id === ruleId.value)) {
        ruleId.value = rules.value[0]?.rule_id || ''
      }
    }
  } catch (e: unknown) { error.value = errorMessage(e) }
}
const route = useRoute()
const tavernInput = ref<HTMLInputElement | null>(null)
const diceframeInput = ref<HTMLInputElement | null>(null)
const tavernImportOpen = ref(false)
const tavernTarget = ref<'npc' | 'character_card'>('npc')
const tavernWorlds = ref<WorldSummary[]>([])
const tavernWorldId = ref('')

async function openTavernImport() {
  tavernTarget.value = 'npc'
  tavernWorldId.value = ''
  tavernImportOpen.value = true
  try {
    const r = await api<WorldListResponse>('/worlds')
    tavernWorlds.value = r.worlds || []
    if (tavernWorlds.value.length && !tavernWorldId.value) {
      const first = tavernWorlds.value[0]
      tavernWorldId.value = String(first.id || first.world_id || '')
    }
  } catch (err: unknown) { toast.error(errorMessage(err)) }
}

function confirmTavernChoice() {
  if (tavernTarget.value === 'npc' && !tavernWorldId.value) {
    toast.error(t('tavernImportPickWorld'))
    return
  }
  tavernImportOpen.value = false
  tavernInput.value?.click()
}

async function onImportDiceframe(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  try {
    const r = await importTavernCard(file, { target: 'character_card' })
    toast.success(t('importedCharacter', { name: r.card?.character_name || file.name }))
    if (r.nsfw_warning) toast.warning(t('tavernImportNsfwWarning'))
    await load()
  } catch (err: unknown) { toast.error(errorMessage(err)) } finally { busy.value = false; input.value = '' }
}

const selectedCardIds = ref<Set<string>>(new Set())

function toggleCardSelect(id: string) {
  const next = new Set(selectedCardIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedCardIds.value = next
}

async function exportCards(ids: string[]) {
  if (!ids.length) return
  try {
    const res = await apiBlob('/character-cards/export', {
      method: 'POST',
      body: JSON.stringify({ card_ids: ids }),
    })
    const blob = await res.blob()
    const dispo = res.headers.get('Content-Disposition') || ''
    const m = dispo.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
    const filename = m ? decodeURIComponent(m[1]) : 'characters.json'
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('exportedCards'))
  } catch (err: unknown) { toast.error(errorMessage(err)) }
}

async function exportSingleCard(card: CharacterCard) {
  await exportCards([cardId(card)])
}

async function exportSelected() {
  await exportCards([...selectedCardIds.value])
}

async function deleteSelectedCards() {
  const ids = [...selectedCardIds.value]
  if (!ids.length) return
  const ok = await confirm({
    title: t('deleteSelectedCards'),
    content: t('deleteSelectedCardsConfirm', { count: ids.length }),
    positiveText: t('delete'),
    type: 'error',
  })
  if (!ok) return
  busy.value = true
  try {
    const results = await Promise.allSettled(
      ids.map(id => api<{ ok?: boolean }>(`/character-cards/${encodeURIComponent(id)}`, { method: 'DELETE' })),
    )
    const failed = results.filter(r => r.status === 'rejected' || (r.status === 'fulfilled' && !(r.value as { ok?: boolean })?.ok)).length
    const success = ids.length - failed
    const next = new Set(selectedCardIds.value)
    ids.forEach(id => next.delete(id))
    selectedCardIds.value = next
    if (success > 0) {
      await load()
      toast.success(t('deleteSelectedCardsResult', { count: success }))
    }
    if (failed > 0) toast.error(t('deleteSelectedCardsFailed', { count: failed }))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

onMounted(async () => {
  await load()
  const uid = route.query.edit_user ? String(route.query.edit_user) : ''
  if (uid && data.value?.players?.length) {
    const p = data.value.players.find(x => x.user_id === uid)
    if (p) openEdit(p)
  }
})

async function onImportTavern(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const target = tavernTarget.value
  const worldId = target === 'npc' ? tavernWorldId.value : ''
  busy.value = true
  try {
    const r = await importTavernCard(file, { target, worldId })
    if (target === 'npc') {
      toast.success(t('importedTavernNpc', { name: r.npc_name || file.name, world: worldId, count: r.lorebook_entries || 0 }))
    } else {
      toast.success(t('importedCharacter', { name: r.card?.character_name || file.name }))
    }
    if (r.nsfw_warning) toast.warning(t('tavernImportNsfwWarning'))
    await load()
  } catch (err: unknown) { toast.error(errorMessage(err)) } finally { busy.value = false; input.value = '' }
}

function openEdit(p: import('@/api/types').Player) {
  const cs = p.character_sheet || {}
  const fields = identitySchema(ruleMeta.value).filter((f: IdentityField) => f.key !== 'background')
  const attrs: Record<string, number> = { ...(cs.attributes || {}) }
  ruleAttrs.value.forEach(a => { if (attrs[a.key] === undefined) attrs[a.key] = Math.floor((a.min + a.max) / 2) })
  if (!Object.keys(attrs).length) attrs.str = attrs.con = attrs.dex = attrs.int = attrs.wis = attrs.cha = 50
  edit.value = {
    player: p, user_id: p.user_id,
    character_name: String(cs.character_name || p.character_name || ''),
    level: Number(cs.level || 1),
    hp: getResourceValue(cs, 'hp') as ResourceEdit,
    gold: getCurrencyAmount(cs),
    attributes: attrs,
    skills: toSkillList(cs.skills),
    background: String(cs.background || ''),
    equipment: (cs.equipment || []).map(it => ({ ...it })),
    inventory: (cs.inventory || []).map(it => ({ ...it })),
    keyText: itemLines(cs.key_items, ['name', 'category', 'note'], { category: 'key_item', note: '' }),
    fields,
    identityValues: Object.fromEntries(fields.map((f: IdentityField) => [f.key, getIdentityValue(cs, f)])),
    portrait: cs.portrait ? { ...cs.portrait } : undefined,
  }
}

const attrSum = computed(() => {
  const attrs = edit.value?.attributes || {}
  return Object.values(attrs).reduce((sum, value) => sum + (parseInt(String(value)) || 0), 0)
})
const attrPoints = computed(() => Math.max(ruleAttrsTotal.value, attrSum.value) - attrSum.value)
const autoHp = computed(() => isAutoHpRule(ruleMeta.value))
const autoHpValue = computed(() => calcAutoHp(edit.value?.attributes || {}, ruleMeta.value))

async function saveCharacter() {
  const e = edit.value
  if (!e) return
  const cs = e.player.character_sheet || {}
  busy.value = true
  try {
    const level = parseInt(String(e.level)) || 1
    const gold = parseInt(String(e.gold)) || 0
    const hpCurrent = parseInt(String(e.hp.current)) || 0
    const hpMax = parseInt(String(e.hp.max)) || 50
    const updates: UpdateCharacterPayload = {
      character_name: e.character_name,
      level,
      gold,
      currency: { amount: gold },
      progression: { level, xp: cs.xp || 0 },
      attributes: e.attributes,
      skills: e.skills.filter(s => s.name?.trim()).map(s => ({ name: s.name.trim(), value: Number(s.value) || 0 })),
      background: e.background,
      hp: hpCurrent,
      max_hp: hpMax,
      resources: { hp: { current: hpCurrent, max: hpMax, min: 0 } },
      portrait: e.portrait ? { ...e.portrait } : null,
    }
    e.fields.forEach((f: IdentityField) => setIdentityUpdate(updates, f, e.identityValues[f.key]))
    updates.identity = updates.identity || {}
    updates.identity.background = e.background
    updates.equipment = e.equipment.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), type: it.type || 'weapon', damage: Number(it.damage) || 0, slot: it.slot || 'main_hand', quality: it.quality || 'common' }))
    updates.inventory = e.inventory.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), qty: Number(it.qty) || 1, effect: it.effect || '' }))
    updates.key_items = parseLines(e.keyText, p => p.length >= 3 ? { name: p[0], category: p[1] || 'key_item', note: p[2] } : p.length >= 2 ? { name: p[0], category: p[1] || 'key_item' } : { name: p[0] || '', category: 'key_item' })
    await api(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(e.user_id)}`, { method: 'PUT', body: JSON.stringify(updates) })
    edit.value = null
    await load()
    toast.success(t('characterSaved'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteCharacter(p: import('@/api/types').Player) {
  const ok = await confirm({ title: t('removeCharacterTitle'), content: t('removeCharacterContent', { name: p.character_name }), positiveText: t('removeCharacterAction'), type: 'warning' })
  if (!ok) return
  try {
    await api(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(p.user_id)}`, { method: 'DELETE' })
    await load()
    toast.success(t('removed'))
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function saveToCard(p: import('@/api/types').Player) {
  const cs = p.character_sheet || {}
  try {
    await api('/character-cards', { method: 'POST', body: JSON.stringify({ character_name: p.character_name, ...cs, ...currentRuleBinding() }) })
    await load()
    toast.success(t('savedToSharedLibrary'))
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function openLevelUp(p: import('@/api/types').Player) {
  editLevelUp.value = { player: p, levelUpPoints: Number(p.character_sheet?.level_up_points || 0) }
}
async function saveLevelUp(attrs: Record<string, number>) {
  const p = editLevelUp.value?.player
  if (!p) return
  busy.value = true
  try {
    await api(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(p.user_id)}`, { method: 'PUT', body: JSON.stringify({ attributes: attrs }) })
    editLevelUp.value = null
    await load()
    toast.success(t('attributePointsAllocated'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

function openCardEdit(c: CharacterCard) {
  editCard.value = {
    card_id: cardId(c),
    character_name: c.character_name || '',
    race: c.race || '',
    class: c.class || '',
    skills: toSkillList(c.skills),
    background: c.background || '',
    gold: Number(c.gold ?? 30),
    portrait: c.portrait ? { ...c.portrait } : undefined,
    rule_id: c.rule_id,
  }
}
async function saveCardEdit() {
  const e = editCard.value
  if (!e) return
  busy.value = true
  try {
    const patch: CharacterCardPatch = {
      character_name: e.character_name.trim() || t('unnamed'),
      race: e.race.trim() || t('human'),
      class: e.class.trim() || t('adventurer'),
      skills: e.skills.filter(s => s.name?.trim()).map(s => ({ name: s.name.trim(), value: Number(s.value) || 0 })),
      background: e.background.trim(),
      gold: parseInt(String(e.gold)) || 0,
      portrait: e.portrait ? { ...e.portrait } : null,
    }
    const r = await api<{ ok?: boolean; error?: string }>(`/character-cards/${encodeURIComponent(e.card_id)}`, { method: 'PUT', body: JSON.stringify(patch) })
    if (!r.ok) throw new Error(r.error || t('saveFailed'))
    editCard.value = null
    await load()
    toast.success(t('characterCardUpdated'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteCard(c: CharacterCard) {
  const ok = await confirm({ title: t('deleteCharacterCardTitle'), content: t('deleteCharacterCardContent', { name: c.character_name || t('unnamed') }), positiveText: t('deleteCharacterCardAction'), type: 'error' })
  if (!ok) return
  try {
    await api(`/character-cards/${encodeURIComponent(cardId(c))}`, { method: 'DELETE' })
    await load()
    toast.success(t('deleted'))
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function openNpcPortrait(npc: CharacterCard) {
  editNpcPortrait.value = {
    npcId: npcKey(npc),
    name: String(npc.character_name || npc.name || t('unnamed')),
    portrait: npc.portrait ? { ...npc.portrait } : undefined,
  }
}

async function saveNpcPortrait() {
  const npc = editNpcPortrait.value
  if (!npc || !game.value) return
  busy.value = true
  try {
    const result = await api<{ ok?: boolean; error?: string }>(
      `/games/${encodeURIComponent(game.value)}/npc/${encodeURIComponent(npc.npcId)}/portrait`,
      { method: 'PUT', body: JSON.stringify({ portrait: npc.portrait ?? null }) },
    )
    if (!result.ok) throw new Error(result.error || t('saveFailed'))
    editNpcPortrait.value = null
    await load()
    toast.success(t('characterSaved'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function onWizardSubmit(c: CharacterSheet & { character_name: string }) {
  busy.value = true
  try {
    await api('/character-cards', { method: 'POST', body: JSON.stringify({ ...c, ...currentRuleBinding() }) })
    showWizard.value = false
    await load()
    toast.success(t('characterCardCreated'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page characters-page">
    <header class="view-title archive-hero">
      <div>
        <span class="section-kicker">{{ t('charactersKicker') }}</span>
        <h1>{{ t('characterManagement') }}</h1>
        <p v-if="game">{{ t('currentSave') }}: {{ game }}</p>
        <p v-else class="muted">{{ t('noSaveSelectedHint') }}</p>
      </div>
      <div class="actions">
        <label v-if="!game" class="standalone-rule-select">
          <span>{{ t('characterCardRule') }}</span>
          <select v-model="ruleId" :disabled="ruleSchemaLoading">
            <option v-for="rule in rules" :key="rule.rule_id" :value="rule.rule_id">{{ ruleNameOf(rule) }}</option>
          </select>
        </label>
        <button class="success" :disabled="!ruleId || ruleSchemaLoading" @click="showWizard = true">+ {{ t('newCharacterCard') }}</button>
        <button @click="load">{{ t('refresh') }}</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <section v-if="game" class="character-section current-character-section">
      <header class="character-section-head"><h2>{{ t('currentGameCharacters') }}</h2><span>{{ data?.players?.length || 0 }}</span></header>
      <div class="current-character-grid">
      <article v-for="p in data?.players || []" :key="p.user_id" class="char-card current-character-card">
        <div class="current-character-identity">
          <button type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="openEdit(p)">
            <PortraitImage :portrait="p.character_sheet?.portrait" :rule-id="ruleId" :seed="p.user_id" :name="p.character_name" :size="96" />
            <span>{{ t('clickToChangeAvatar') }}</span>
          </button>
          <div class="current-character-copy">
            <div class="character-name-line"><h2>{{ p.character_name }}</h2><span class="badge badge-active">{{ t('playerSlot') }}</span></div>
            <div class="character-identity-chips">
              <span>{{ p.user_id }}</span>
              <span>{{ t('level') }} {{ p.character_sheet?.level || 1 }}</span>
              <span v-if="p.character_sheet?.race">{{ p.character_sheet.race }}</span>
              <span v-if="p.character_sheet?.class">{{ p.character_sheet.class }}</span>
            </div>
            <div class="character-resource-line">
              <span>HP</span>
              <div class="character-resource-track"><i :style="{ width: `${hpPercent(p)}%` }" /></div>
              <strong>{{ p.character_sheet?.hp }}/{{ p.character_sheet?.max_hp }}</strong>
            </div>
            <p v-if="levelUpPoints(p) > 0" class="warn character-level-notice">
              {{ t('pointsToAllocate', { points: levelUpPoints(p) }) }}
            </p>
          </div>
        </div>
        <div class="actions current-character-actions">
          <button class="success" @click="openEdit(p)">{{ t('edit') }}</button>
          <button v-if="levelUpPoints(p) > 0" class="primary" @click="openLevelUp(p)">{{ t('allocateAttributePointsWithCount', { points: levelUpPoints(p) }) }}</button>
          <button @click="saveToCard(p)">{{ t('saveToSharedLibrary') }}</button>
          <button class="danger" @click="deleteCharacter(p)">{{ t('remove') }}</button>
        </div>
      </article>
      <p v-if="!data?.players?.length" class="muted">{{ t('noCharacters') }}</p>
      </div>
    </section>

    <section v-if="data?.npcs?.length" class="character-section npc-section">
      <header class="character-section-head"><h2>{{ t('currentGameNpcs') }}</h2><span>{{ data.npcs.length }}</span></header>
      <div class="npc-strip">
        <article v-for="n in data.npcs" :key="npcKey(n)" class="char-card npc-mini-card">
          <button type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="openNpcPortrait(n)">
            <PortraitImage v-if="n.portrait" :portrait="n.portrait" :rule-id="ruleId" :seed="npcKey(n)" :name="String(n.character_name || n.name || '')" :size="54" />
            <span v-else class="portrait-image npc-portrait-empty" aria-hidden="true">＋</span>
            <span>{{ t('clickToChangeAvatar') }}</span>
          </button>
          <div>
            <h2>{{ n.character_name || n.name || t('unnamed') }}<small v-if="n.tier === 'core'" class="badge">{{ t('core') }}</small></h2>
            <p class="muted">{{ npcSummary(n) }}</p>
          </div>
        </article>
      </div>
    </section>

    <section class="character-section shared-character-section">
      <header class="character-section-head shared-character-head">
        <div><h2>{{ t('sharedCharacterLibrary') }}</h2><span>{{ data?.cards?.length || 0 }}</span></div>
      </header>
    <input ref="tavernInput" type="file" accept=".json,application/json" @change="onImportTavern" hidden>
    <input ref="diceframeInput" type="file" accept=".json,application/json" @change="onImportDiceframe" hidden>
    <Modal v-if="tavernImportOpen" :title="t('importTavernCard')" @close="tavernImportOpen = false">
      <label>{{ t('tavernImportAs') }}</label>
      <div class="check-row">
        <label><input type="radio" value="npc" v-model="tavernTarget"> {{ t('tavernImportAsNpc') }}</label>
        <label><input type="radio" value="character_card" v-model="tavernTarget"> {{ t('tavernImportAsCard') }}</label>
      </div>
      <p class="muted">{{ t('tavernImportAsNpcHint') }}</p>
      <div v-if="tavernTarget === 'npc'">
        <label>{{ t('tavernImportTargetWorld') }}</label>
        <select v-model="tavernWorldId">
          <option v-for="w in tavernWorlds" :key="w.id || w.world_id" :value="w.id || w.world_id">{{ w.name || w.world_name }}</option>
        </select>
        <p v-if="!tavernWorlds.length" class="muted">{{ t('tavernImportNoWorlds') }}</p>
      </div>
      <template #actions>
        <button @click="tavernImportOpen = false">{{ t('cancel') }}</button>
        <button class="primary" :disabled="tavernTarget === 'npc' && !tavernWorldId" @click="confirmTavernChoice">{{ t('chooseFile') }}</button>
      </template>
    </Modal>
      <div class="character-library-toolbar">
        <input v-model="characterSearch" :placeholder="t('characterLibrarySearch')">
        <select v-model="characterSort"><option value="name">{{ t('characterSortName') }}</option><option value="rule">{{ t('characterSortRule') }}</option></select>
        <div class="character-view-switch"><button :class="{ active: libraryView === 'grid' }" @click="libraryView = 'grid'">▦</button><button :class="{ active: libraryView === 'list' }" @click="libraryView = 'list'">☷</button></div>
        <div class="actions character-import-actions"><button class="danger" :disabled="busy || !selectedCardIds.size" @click="exportSelected">{{ t('exportSelected') }}</button><button class="danger" :disabled="busy || !selectedCardIds.size" @click="deleteSelectedCards">{{ t('deleteSelectedCards') }}</button><button class="success" :disabled="busy" @click="diceframeInput?.click()">{{ t('importDiceframeCard') }}</button><button :disabled="busy" @click="openTavernImport">{{ t('importTavernCard') }}</button></div>
      </div>
    <div class="card-grid character-library-grid" :class="`view-${libraryView}`">
      <article v-for="c in filteredCards" :key="c.card_id || c.id" class="char-card library-character-card">
        <div class="character-card-summary">
          <input type="checkbox" :checked="selectedCardIds.has(cardId(c))" @change="toggleCardSelect(cardId(c))" class="card-select" :title="t('selectCard')">
          <button type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="openCardEdit(c)">
            <PortraitImage :portrait="c.portrait" :rule-id="c.rule_id" :seed="cardId(c) || c.character_name" :name="c.character_name" :size="64" />
            <span>{{ t('clickToChangeAvatar') }}</span>
          </button>
          <div>
          <h2>{{ c.character_name }}</h2>
          <p class="muted card-rule"><span class="badge" :title="cardRuleLabel(c)">{{ cardRuleLabel(c) }}</span></p>
          <p class="muted card-identity">{{ c.race }} · {{ c.class }}<span v-if="c.source"> · {{ t('source') }} {{ c.source }}</span></p>
          <p v-if="c.background" class="muted card-bg" :title="String(c.background)">{{ String(c.background).slice(0, 80) }}</p>
          </div>
        </div>
        <div class="actions">
          <button @click="openCardEdit(c)">{{ t('editCard') }}</button>
          <button @click="exportSingleCard(c)">{{ t('export') }}</button>
          <button class="danger" @click="deleteCard(c)">{{ t('delete') }}</button>
        </div>
      </article>
      <p v-if="!filteredCards.length" class="muted">{{ data?.cards?.length ? t('characterLibraryNoMatches') : t('noSharedCards') }}</p>
    </div>
    </section>

    <Modal v-if="edit" :title="t('editCharacter')" @close="edit = null">
      <label>{{ t('characterName') }}<input v-model="edit.character_name"></label>
      <PortraitPicker v-model="edit.portrait" :rule-id="ruleId" :seed="edit.user_id" :name="edit.character_name" />
      <label v-for="f in edit.fields" :key="f.key">{{ identityLabel(f) }}<input v-model="edit.identityValues[f.key]"></label>
      <label>{{ t('level') }}<input type="number" v-model.number="edit.level"></label>
      <label>HP / {{ t('maxHp') }}
        <div class="row">
          <input type="number" v-model.number="edit.hp.current" placeholder="HP">
          <input type="number" v-model.number="edit.hp.max" :placeholder="t('maxHp')">
        </div>
      </label>
      <p v-if="autoHp" class="form-hint">{{ t('ruleSuggestedHp') }}: <strong>{{ autoHpValue }}</strong>{{ t('manualHpStillAllowed') }}</p>
      <label>{{ currencyLabel(ruleMeta) }}<input type="number" v-model.number="edit.gold"></label>
      <label>{{ t('attributes') }} <span class="attr-points">{{ t('pointsRemaining', { points: attrPoints }) }}</span></label>
      <div class="attr-sliders">
        <div v-for="a in editRuleAttrs" :key="a.key" class="attr-row">
          <span class="attr-name">{{ attrDisplayName(a) }}</span>
          <input type="range" :min="a.min" :max="a.max * 2" v-model.number="edit.attributes[a.key]">
          <input type="number" class="attr-val" :min="a.min" v-model.number="edit.attributes[a.key]">
        </div>
      </div>
      <label>{{ t('skills') }}</label>
      <SkillEditor v-model="edit.skills" :pool="skillPool" />
      <label>{{ t('backgroundStory') }}<textarea rows="3" v-model="edit.background"></textarea></label>
      <ItemEditor v-model:equipment="edit.equipment" v-model:inventory="edit.inventory" />
      <label>{{ t('keyItemsLineHelp') }}<textarea rows="3" v-model="edit.keyText"></textarea></label>
      <template #actions>
        <button @click="edit = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveCharacter">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <LevelUpDialog
      v-if="editLevelUp"
      :rule-attrs="ruleAttrs"
      :rule-meta="ruleMeta"
      :character="editLevelUp.player"
      :level-up-points="editLevelUp.levelUpPoints"
      @submit="saveLevelUp"
      @cancel="editLevelUp = null"
    />

    <Modal v-if="editCard" :title="t('editCharacterCard')" @close="editCard = null">
      <label>{{ t('characterName') }}<input v-model="editCard.character_name"></label>
      <PortraitPicker v-model="editCard.portrait" :rule-id="editCard.rule_id || ruleId" :seed="editCard.card_id" :name="editCard.character_name" />
      <label>{{ t('originIdentity') }}<input v-model="editCard.race"></label>
      <label>{{ t('classRole') }}<input v-model="editCard.class"></label>
      <label>{{ t('skills') }}</label>
      <SkillEditor v-model="editCard.skills" :pool="skillPool" />
      <label>{{ t('background') }}<textarea rows="4" v-model="editCard.background"></textarea></label>
      <label>{{ t('initialMoney') }}<input type="number" v-model.number="editCard.gold"></label>
      <template #actions>
        <button @click="editCard = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveCardEdit">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <Modal v-if="editNpcPortrait" :title="`${editNpcPortrait.name} · ${t('characterAvatar')}`" @close="editNpcPortrait = null">
      <PortraitPicker v-model="editNpcPortrait.portrait" :rule-id="ruleId" :seed="editNpcPortrait.npcId" :name="editNpcPortrait.name" />
      <template #actions>
        <button @click="editNpcPortrait = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveNpcPortrait">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <CharacterWizard
      v-if="showWizard"
      :rule-meta="ruleMeta"
      :rule-attrs="ruleAttrs"
      :attr-total="ruleAttrsTotal"
      :skill-pool="skillPool"
      :rule-id="ruleId"
      :language="String(locale)"
      @submit="onWizardSubmit"
      @cancel="showWizard = false"
    />
  </section>
</template>
