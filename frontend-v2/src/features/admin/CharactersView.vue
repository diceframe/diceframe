<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, apiBlob } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterItem, CharacterListResponse, CharacterPortrait, CharacterSchemaResponse, CharacterSheet, CharacterSkill, JsonObject, Player, RuleMeta, RulesResponse, RuleSummary, SkillSpec, WorldListResponse, WorldSummary } from '@/api/types'
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
import RulesetAdvancementHost from '@/features/rulesets/RulesetAdvancementHost.vue'
import RulesetExperienceHost from '@/features/rulesets/RulesetExperienceHost.vue'
import RulesetCharacterCenterHost from '@/features/rulesets/RulesetCharacterCenterHost.vue'
import {
  identitySchema, identityLabel, getIdentityValue, setIdentityUpdate,
  currencyLabel, getCurrencyAmount, getResourceValue,
  isAutoHpRule, calcAutoHp, attrDisplayName,
  type IdentityField, type RuleAttr,
} from '@/utils/ruleSchema'
import { currencyAmountToInputText, currencyEditableUnitLabel, currencyInputStep, parseCurrencyInput } from '@/utils/currency'
import ContentPageHeader from '@/components/content/ContentPageHeader.vue'
import { characterJourneyCopy } from '@/features/characters/copy'
import { playContextQuery } from '@/navigation/appNavigation'

interface CharacterData extends CharacterListResponse { cards: CharacterCard[] }
interface ResourceEdit { current: number; max: number }
interface CharacterEditForm {
  player: import('@/api/types').Player
  user_id: string
  character_name: string
  level: number
  hp: ResourceEdit
  gold: number
  goldText: string
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
  goldText: string
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
interface ProfessionalEditTarget {
  runtimeId: string
  target: 'game' | 'card'
  character: CharacterSheet
  ruleId: string
  gameKey?: string
  userId?: string
  cardId?: string
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
const journeyCopy = computed(() => characterJourneyCopy(String(locale.value)))
const playQuery = computed(() => playContextQuery(route.query, game.value) || { game: game.value })

const route = useRoute()
const routeGame = typeof route.query.game === 'string' ? route.query.game.trim() : ''
const game = ref(routeGame || readCurrentGame())
const activePane = ref<'library' | 'current'>(route.query.pane === 'current' ? 'current' : 'library')
const currentError = ref('')
const libraryError = ref('')
const libraryLoading = ref(false)
const currentLoading = ref(false)
let loadSequence = 0
const data = ref<CharacterData | null>(null)
const error = ref('')
const characterSaveError = ref('')
const busy = ref(false)
const edit = ref<CharacterEditForm | null>(null)
const editLevelUp = ref<LevelUpState | null>(null)
const editCard = ref<CardEditForm | null>(null)
const editNpcPortrait = ref<NpcPortraitEdit | null>(null)
const advancementCard = ref<CharacterCard | null>(null)
const advancementPlayer = ref<Player | null>(null)
const professionalEdit = ref<ProfessionalEditTarget | null>(null)
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
// The current game binding and the library authoring binding are deliberately
// separate. A character-card draft may target another installed ruleset while
// a game is open; the game pane keeps projecting its own rule.
const libraryRuleId = ref('')
const libraryRuleMeta = ref<RuleMeta>({})
const libraryRuleAttrs = ref<RuleAttr[]>([])
const libraryRuleAttrsTotal = ref(60)
const libraryRuleDetail = ref<{ skill_pool?: Array<string | SkillSpec>; skills?: Array<string | SkillSpec> } | null>(null)
const libraryRuleSchemaLoading = ref(false)
const canCreateCard = computed(() => Boolean(libraryRuleId.value && libraryRuleDetail.value && !libraryRuleSchemaLoading.value))
let ruleSchemaSequence = 0
let libraryRuleSchemaSequence = 0
const ruleOptions = computed<RuleSummary[]>(() => {
  if (!game.value || !ruleMeta.value.rule_id || rules.value.some(rule => rule.rule_id === ruleMeta.value.rule_id)) return rules.value
  return [{ rule_id: String(ruleMeta.value.rule_id), rule_name: String(ruleMeta.value.rule_name || ruleMeta.value.rule_id) }, ...rules.value]
})

const skillPool = computed<Array<string | SkillSpec>>(() => {
  const detail = libraryRuleDetail.value || {}
  return detail.skill_pool || detail.skills || []
})
const editRuleAttrs = computed<RuleAttr[]>(() => {
  if (!edit.value) return ruleAttrs.value
  if (ruleAttrs.value.length) return ruleAttrs.value
  const attrs = edit.value.attributes || {}
  return Object.keys(attrs).map(key => ({ key, name: key, min: 0, max: Math.max(100, Number(attrs[key]) || 100) }))
})
const damageDiceEnabled = computed(() => ruleMeta.value.mechanics === 'dnd5e_core')

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || t('operationFailed')) }
function toSkillList(input: CharacterSheet['skills']): CharacterSkill[] {
  return (input || []).map(s => {
    if (typeof s === 'string') return { name: s, value: 20 }
    const row: CharacterSkill = { name: s.name || '', value: s.value || 20 }
    if (s.id) row.id = s.id
    const effect = String(s.effect || '').trim()
    if (effect) row.effect = effect
    return row
  })
}

/** 保存侧技能投影：effect 是玩家说明文本，编辑路径不得丢失。 */
function toSkillPayload(skills: CharacterSkill[]): CharacterSkill[] {
  return skills
    .filter(s => s.name?.trim())
    .map(s => {
      const row: CharacterSkill = { name: s.name.trim(), value: Number(s.value) || 0 }
      if (s.id) row.id = s.id
      const effect = String(s.effect || '').trim().slice(0, 500)
      if (effect) row.effect = effect
      return row
    })
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
  const lang = String(locale.value || '').toLowerCase()
  if (lang.startsWith('zh')) {
    return String(rule.rule_name || rule.rule_name_en || rule.rule_id)
  }
  // de/ja 等非中文界面回退英文名，而不是中文 canonical 名（#277 followup）。
  return String(rule.rule_name_en || rule.rule_name || rule.rule_id)
}
function cardRuleLabel(card: CharacterCard): string {
  if (!card.rule_id) return t('unboundRule')
  const rule = rules.value.find(candidate => candidate.rule_id === card.rule_id)
  return rule ? ruleNameOf(rule) : String(card.rule_name || card.rule_id)
}
function hasRulesAwareLifecycle(capabilities: unknown): boolean {
  if (!capabilities || typeof capabilities !== 'object' || Array.isArray(capabilities)) return false
  return (capabilities as JsonObject).character_lifecycle === 'rules_aware'
}
function isProfessionalCard(card: CharacterCard): boolean {
  return hasRulesAwareLifecycle(card.ruleset_runtime?.capabilities)
}
function isProfessionalGame(): boolean {
  return hasRulesAwareLifecycle(data.value?.ruleset_runtime?.capabilities)
}
function isSelectedProfessionalRule(): boolean {
  const rule = rules.value.find(candidate => candidate.rule_id === libraryRuleId.value)
  return hasRulesAwareLifecycle(rule?.ruleset_runtime?.capabilities)
}
function professionalLevel(card: CharacterSheet): number {
  const canonical = card.ruleset_character as JsonObject | undefined
  const build = canonical?.build as JsonObject | undefined
  return Number(build?.level || card.level || 1)
}
function liveAdvancementRow(userId: string) {
  return data.value?.advancement?.players.find(row => row.user_id === userId)
}
function currentRuleBinding(): Pick<CharacterCard, 'rule_id' | 'rule_name' | 'rule_version' | 'mechanics' | 'language'> {
  const rule = rules.value.find(candidate => candidate.rule_id === libraryRuleId.value)
  return {
    rule_id: libraryRuleId.value,
    rule_name: String(libraryRuleMeta.value.rule_name || rule?.rule_name || libraryRuleId.value),
    rule_version: String(libraryRuleMeta.value.rule_version || ''),
    mechanics: String(libraryRuleMeta.value.mechanics || ''),
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
  if (!id) {
    ruleSchemaSequence++
    ruleDetail.value = null
    ruleSchemaLoading.value = false
    return
  }
  const schemaGame = game.value
  const sequence = ++ruleSchemaSequence
  ruleDetail.value = null
  ruleSchemaLoading.value = true
  try {
    const schema = await api<CharacterSchemaResponse>(
      `/rules/${encodeURIComponent(String(id))}/character-schema?language=${encodeURIComponent(String(locale.value))}`,
    )
    if (schemaGame !== game.value || sequence !== ruleSchemaSequence) return
    if (!schema.ok) throw new Error(schema.error || t('ruleLoadFailed'))
    ruleMeta.value = schema.rule_meta || ruleMeta.value
    ruleAttrs.value = schema.rule_attrs || ruleAttrs.value
    ruleAttrsTotal.value = Number(schema.rule_attrs_total || ruleAttrsTotal.value)
    ruleDetail.value = { skill_pool: schema.skill_pool || [] }
    if (game.value && libraryRuleId.value === id) {
      libraryRuleSchemaSequence++
      libraryRuleMeta.value = ruleMeta.value
      libraryRuleAttrs.value = ruleAttrs.value
      libraryRuleAttrsTotal.value = ruleAttrsTotal.value
      libraryRuleDetail.value = ruleDetail.value
      libraryRuleSchemaLoading.value = false
    }
  } catch (e: unknown) {
    if (schemaGame !== game.value || sequence !== ruleSchemaSequence) return
    ruleDetail.value = null
    error.value = errorMessage(e)
  } finally {
    if (sequence === ruleSchemaSequence) ruleSchemaLoading.value = false
  }
})

watch([libraryRuleId, ruleId, () => String(ruleMeta.value.rule_id || ''), locale], async ([id]) => {
  if (!id) {
    libraryRuleSchemaLoading.value = false
    libraryRuleDetail.value = null
    libraryRuleMeta.value = {}
    libraryRuleAttrs.value = []
    libraryRuleAttrsTotal.value = 60
    return
  }
  // While a game is open, wait until its binding is known. If the library
  // selects the same rule, the current-game schema request is shared below;
  // this avoids two concurrent reads and keeps refresh/loading semantics stable.
  if (game.value && !ruleId.value) return
  if (game.value && id === ruleId.value) {
    if (String(ruleMeta.value.rule_id || '') !== id || !ruleDetail.value) return
    libraryRuleMeta.value = ruleMeta.value
    libraryRuleAttrs.value = ruleAttrs.value
    libraryRuleAttrsTotal.value = ruleAttrsTotal.value
    libraryRuleDetail.value = ruleDetail.value
    libraryRuleSchemaLoading.value = false
    return
  }
  const sequence = ++libraryRuleSchemaSequence
  libraryRuleSchemaLoading.value = true
  libraryRuleDetail.value = null
  try {
    const schema = await api<CharacterSchemaResponse>(
      `/rules/${encodeURIComponent(String(id))}/character-schema?language=${encodeURIComponent(String(locale.value))}`,
    )
    if (sequence !== libraryRuleSchemaSequence) return
    if (!schema.ok) throw new Error(schema.error || t('ruleLoadFailed'))
    libraryRuleMeta.value = schema.rule_meta || {}
    libraryRuleAttrs.value = schema.rule_attrs || []
    libraryRuleAttrsTotal.value = Number(schema.rule_attrs_total || 60)
    libraryRuleDetail.value = { skill_pool: schema.skill_pool || [] }
  } catch (e: unknown) {
    if (sequence !== libraryRuleSchemaSequence) return
    libraryRuleDetail.value = null
    error.value = errorMessage(e)
  } finally {
    if (sequence === libraryRuleSchemaSequence) libraryRuleSchemaLoading.value = false
  }
})

async function load() {
  const requestedGame = game.value
  const sequence = ++loadSequence
  error.value = ''
  currentError.value = ''
  libraryError.value = ''
  data.value = { players: [], cards: [] }
  libraryLoading.value = true
  currentLoading.value = Boolean(requestedGame)
  ruleSchemaSequence++
  libraryRuleSchemaSequence++
  ruleId.value = ''
  libraryRuleId.value = ''
  ruleMeta.value = {}
  ruleAttrs.value = []
  ruleAttrsTotal.value = 60
  ruleDetail.value = null
  libraryRuleMeta.value = {}
  libraryRuleAttrs.value = []
  libraryRuleAttrsTotal.value = 60
  libraryRuleDetail.value = null

  const cardsRequest = api<CharacterCardsResponse>('/character-cards')
  const rulesRequest = api<RulesResponse>('/rules')
  const gameRequest = requestedGame
    ? api<CharacterListResponse>(`/games/${encodeURIComponent(requestedGame)}/characters`)
    : Promise.resolve(null)
  const isCurrent = () => sequence === loadSequence && requestedGame === game.value
  void cardsRequest.then(result => {
    if (!isCurrent()) return
    data.value!.cards = result.cards || []
    libraryLoading.value = false
  }).catch(reason => {
    if (!isCurrent()) return
    libraryLoading.value = false
    libraryError.value = errorMessage(reason)
    error.value = errorMessage(reason)
  })
  void rulesRequest.then(result => {
    if (!isCurrent()) return
    rules.value = result.rules || []
    if (!libraryRuleId.value || !rules.value.some(rule => rule.rule_id === libraryRuleId.value)) libraryRuleId.value = rules.value[0]?.rule_id || ''
  }).catch(reason => {
    if (isCurrent() && !error.value) error.value = errorMessage(reason)
  })
  void gameRequest.then(result => {
    if (!isCurrent()) return
    currentLoading.value = false
    if (result) {
      data.value = { ...result, cards: data.value?.cards || [] }
      ruleMeta.value = result.rule_meta || {}
      ruleAttrs.value = result.rule_attrs || []
      ruleAttrsTotal.value = result.rule_attrs_total || 60
      ruleId.value = String(ruleMeta.value.rule_id || '')
      if (!libraryRuleId.value) libraryRuleId.value = ruleId.value
    }
  }).catch(reason => {
    if (!isCurrent()) return
    currentLoading.value = false
    if (requestedGame) currentError.value = errorMessage(reason)
  })
  await Promise.allSettled([cardsRequest, rulesRequest, gameRequest])
}
const tavernInput = ref<HTMLInputElement | null>(null)
const diceframeInput = ref<HTMLInputElement | null>(null)
const tavernImportOpen = ref(false)
const tavernTarget = ref<'npc' | 'character_card'>('npc')
const tavernWorlds = ref<WorldSummary[]>([])
const tavernWorldId = ref('')
// 角色卡内嵌世界书是真数据，不是说明脚注：不勾就真的不落库（后端 include_character_book）。
const tavernIncludeLore = ref(true)

async function openTavernImport() {
  tavernTarget.value = 'npc'
  tavernWorldId.value = ''
  tavernIncludeLore.value = true
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

function queryString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

watch(() => route.query.game, rawGame => {
  const nextGame = queryString(rawGame)
  if (nextGame === game.value) return
  game.value = nextGame
  ruleId.value = ''
  libraryRuleId.value = ''
  edit.value = null
  editCard.value = null
  editNpcPortrait.value = null
  editLevelUp.value = null
  advancementCard.value = null
  advancementPlayer.value = null
  professionalEdit.value = null
  showWizard.value = false
  void load()
})

watch(() => route.query.pane, rawPane => {
  if (rawPane === 'current' || rawPane === 'library') activePane.value = rawPane
})

onMounted(async () => {
  if (route.query.edit_user) activePane.value = 'current'
  const mountedGame = game.value
  const mountedUser = queryString(route.query.edit_user)
  await load()
  if (mountedGame !== game.value) return
  const uid = mountedUser
  if (uid && data.value?.players?.length) {
    const p = data.value.players.find(x => x.user_id === uid)
    if (p) openPlayerEditor(p)
  }
})

async function onImportTavern(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const target = tavernTarget.value
  const worldId = target === 'npc' ? tavernWorldId.value : ''
  const includeCharacterBook = target === 'npc' ? true : tavernIncludeLore.value
  busy.value = true
  try {
    const r = await importTavernCard(file, { target, worldId, includeCharacterBook })
    if (target === 'npc') {
      toast.success(t('importedTavernNpc', { name: r.npc_name || file.name, world: worldId, count: r.lorebook_entries || 0 }))
    } else if (r.lorebook) {
      toast.success(t('importedCharacterWithLore', {
        name: r.card?.character_name || file.name,
        book: r.lorebook.name || r.lorebook_book_id || '',
        count: r.lorebook.entries ?? r.lorebook_entries ?? 0,
      }))
    } else {
      toast.success(t('importedCharacter', { name: r.card?.character_name || file.name }))
    }
    if (r.nsfw_warning) toast.warning(t('tavernImportNsfwWarning'))
    await load()
  } catch (err: unknown) { toast.error(errorMessage(err)) } finally { busy.value = false; input.value = '' }
}

function openEdit(p: import('@/api/types').Player) {
  characterSaveError.value = ''
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
    goldText: currencyAmountToInputText(getCurrencyAmount(cs), ruleMeta.value.currency_system || null),
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

function openPlayerEditor(p: import('@/api/types').Player) {
  if (isProfessionalGame()) {
    professionalEdit.value = {
      runtimeId: String(data.value?.ruleset_runtime?.id || ''),
      target: 'game',
      character: { ...(p.character_sheet || {}), character_name: p.character_name },
      ruleId: ruleId.value,
      gameKey: game.value,
      userId: p.user_id,
    }
    return
  }
  openEdit(p)
}

const attrSum = computed(() => {
  const attrs = edit.value?.attributes || {}
  return Object.values(attrs).reduce((sum, value) => sum + (parseInt(String(value)) || 0), 0)
})
const attrPoints = computed(() => Math.max(ruleAttrsTotal.value, attrSum.value) - attrSum.value)
const editableUnitSuffix = computed(() => {
  // 编辑框实际解析单位（可与顶层货币名不同，如 rate=3 时回退铜币）。
  const name = currencyEditableUnitLabel(ruleMeta.value.currency_system || null)
  return name ? `（${name}）` : ''
})
const autoHp = computed(() => isAutoHpRule(ruleMeta.value))
const autoHpValue = computed(() => calcAutoHp(edit.value?.attributes || {}, ruleMeta.value))

async function saveCharacter() {
  const e = edit.value
  if (!e) return
  characterSaveError.value = ''
  const cs = e.player.character_sheet || {}
  busy.value = true
  try {
    const level = parseInt(String(e.level)) || 1
    const parsedGold = parseCurrencyInput(e.goldText, ruleMeta.value.currency_system || null, { allowZero: true })
    if (parsedGold === null) throw new Error(t('invalidAmount'))
    const gold = parsedGold
    const hpCurrent = parseInt(String(e.hp.current)) || 0
    const hpMax = parseInt(String(e.hp.max)) || 50
    const updates: UpdateCharacterPayload = {
      character_name: e.character_name,
      level,
      gold,
      currency: { amount: gold },
      progression: { level, xp: cs.xp || 0 },
      attributes: e.attributes,
      skills: toSkillPayload(e.skills),
      background: e.background,
      hp: hpCurrent,
      max_hp: hpMax,
      resources: { hp: { current: hpCurrent, max: hpMax, min: 0 } },
      portrait: e.portrait ? { ...e.portrait } : null,
    }
    e.fields.forEach((f: IdentityField) => setIdentityUpdate(updates, f, e.identityValues[f.key]))
    updates.identity = updates.identity || {}
    updates.identity.background = e.background
    updates.equipment = e.equipment.filter(it => String(it.name || '').trim()).map(it => {
      const { damage_dice: _damageDice, ...otherFields } = it
      return {
        ...otherFields,
        name: String(it.name).trim(), type: it.type || 'weapon', damage: Number(it.damage) || 0,
        ...(damageDiceEnabled.value && String(it.damage_dice || '').trim() ? { damage_dice: String(it.damage_dice).trim() } : {}),
        slot: it.slot || 'main_hand', quality: it.quality || 'common',
      }
    })
    updates.inventory = e.inventory.filter(it => String(it.name || '').trim()).map(it => ({ name: String(it.name).trim(), qty: Number(it.qty) || 1, effect: it.effect || '' }))
    updates.key_items = parseLines(e.keyText, p => p.length >= 3 ? { name: p[0], category: p[1] || 'key_item', note: p[2] } : p.length >= 2 ? { name: p[0], category: p[1] || 'key_item' } : { name: p[0] || '', category: 'key_item' })
    const result = await api<{ ok?: boolean; error?: string }>(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(e.user_id)}`, { method: 'PUT', body: JSON.stringify(updates) })
    if (result?.ok === false) throw new Error(result.error || t('operationFailed'))
    edit.value = null
    await load()
    toast.success(t('characterSaved'))
  } catch (cause: unknown) {
    characterSaveError.value = errorMessage(cause)
    error.value = characterSaveError.value
    toast.error(characterSaveError.value)
  } finally { busy.value = false }
}

async function deleteCharacter(p: import('@/api/types').Player) {
  const requestedGame = game.value
  const requestedUser = p.user_id
  const ok = await confirm({ title: t('removeCharacterTitle'), content: t('removeCharacterContent', { name: p.character_name }), positiveText: t('removeCharacterAction'), type: 'warning' })
  if (!ok || requestedGame !== game.value || requestedUser !== p.user_id) return
  try {
    await api(`/games/${encodeURIComponent(requestedGame)}/character/${encodeURIComponent(requestedUser)}`, { method: 'DELETE' })
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
    goldText: currencyAmountToInputText(Number(c.gold ?? 30), ruleMeta.value.currency_system || null),
    portrait: c.portrait ? { ...c.portrait } : undefined,
    rule_id: c.rule_id,
  }
}
function openCardEditor(c: CharacterCard) {
  if (isProfessionalCard(c)) {
    professionalEdit.value = {
      runtimeId: String(c.ruleset_runtime?.id || ''),
      target: 'card',
      character: c,
      ruleId: String(c.rule_id || ''),
      cardId: cardId(c),
    }
    return
  }
  openCardEdit(c)
}
async function saveCardEdit() {
  const e = editCard.value
  if (!e) return
  const parsedGold = parseCurrencyInput(e.goldText, ruleMeta.value.currency_system || null, { allowZero: true })
  if (parsedGold === null) {
    toast.error(t('invalidAmount'))
    return
  }
  busy.value = true
  try {
    const patch: CharacterCardPatch = {
      character_name: e.character_name.trim() || t('unnamed'),
      race: e.race.trim() || t('human'),
      class: e.class.trim() || t('adventurer'),
      skills: toSkillPayload(e.skills),
      background: e.background.trim(),
      gold: parsedGold,
      portrait: e.portrait ? { ...e.portrait } : null,
    }
    const r = await api<{ ok?: boolean; error?: string }>(`/character-cards/${encodeURIComponent(e.card_id)}`, { method: 'PUT', body: JSON.stringify(patch) })
    if (!r.ok) throw new Error(r.error || t('saveFailed'))
    editCard.value = null
    await load()
    toast.success(t('characterCardUpdated'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function onCardAdvanced() {
  busy.value = true
  try {
    advancementCard.value = null
    await load()
    toast.success(String(locale.value).startsWith('zh') ? '角色升级已完成。' : 'Character advanced.')
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally { busy.value = false }
}

async function onLiveCharacterAdvanced() {
  busy.value = true
  try {
    advancementPlayer.value = null
    await load()
    toast.success(String(locale.value).startsWith('zh') ? '角色升级已完成。' : 'Character advanced.')
  } catch (cause: unknown) {
    error.value = errorMessage(cause)
  } finally { busy.value = false }
}

async function onProfessionalSaved(_character?: CharacterSheet, reason?: 'profile' | 'rest') {
  professionalEdit.value = null
  await load()
  toast.success(reason === 'rest'
    ? (String(locale.value).startsWith('zh') ? '休息已按规则结算。' : 'Rest completed.')
    : (String(locale.value).startsWith('zh') ? '人物资料已安全保存。' : 'Character profile saved.'))
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

async function onWizardSubmit(c: CharacterSheet) {
  if (!canCreateCard.value) return
  busy.value = true
  try {
    await api('/character-cards', {
      method: 'POST',
      body: JSON.stringify({
        ...c,
        character_name: String(c.character_name || t('unnamed')),
        ...currentRuleBinding(),
      }),
    })
    showWizard.value = false
    await load()
    toast.success(t('characterCardCreated'))
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page characters-page">
    <ContentPageHeader class="view-title archive-hero characters-page-heading" kicker="CHARACTER ROSTER" :title="t('characterManagement')" :description="journeyCopy.description">
      <template #actions>
        <div class="actions character-page-actions">
          <template v-if="activePane === 'library'">
          <label class="standalone-rule-select">
            <span>{{ t('characterCardRule') }}</span>
            <select v-model="libraryRuleId" :disabled="libraryRuleSchemaLoading || !rules.length">
              <option v-for="rule in ruleOptions" :key="rule.rule_id" :value="rule.rule_id">{{ ruleNameOf(rule) }}</option>
            </select>
          </label>
          <button data-testid="new-character-card" class="primary" :disabled="!canCreateCard" @click="showWizard = true">+ {{ t('newCharacterCard') }}</button>
          <span v-if="!currentLoading && !libraryRuleSchemaLoading && !canCreateCard" class="form-hint">{{ journeyCopy.creationUnavailable }}</span>
          </template>
          <button @click="load">{{ t('refresh') }}</button>
        </div>
      </template>
    </ContentPageHeader>

    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>

    <nav class="character-collection-tabs" role="tablist" :aria-label="journeyCopy.collectionsLabel">
      <button id="character-library-tab" type="button" role="tab" aria-controls="character-library-panel" :aria-selected="activePane === 'library'" :class="{ active: activePane === 'library' }" @click="activePane = 'library'">
        {{ journeyCopy.libraryTab }}
      </button>
      <button id="character-current-tab" type="button" role="tab" aria-controls="character-current-panel" :aria-selected="activePane === 'current'" :class="{ active: activePane === 'current' }" @click="activePane = 'current'">
        {{ journeyCopy.currentTab }}
      </button>
    </nav>

    <section v-show="activePane === 'current'" id="character-current-panel" class="character-section current-character-section" role="tabpanel" aria-labelledby="character-current-tab">
      <div v-if="!game" data-testid="characters-current-guide" class="character-current-guide">
        <h2>{{ journeyCopy.chooseSave }}</h2>
        <p class="muted">{{ journeyCopy.chooseSaveHint }}</p>
        <RouterLink class="content-button" :to="{ name: 'overview' }">{{ journeyCopy.backToSaves }}</RouterLink>
      </div>
      <p v-else-if="currentError" data-testid="characters-current-error" class="error-banner" role="alert">{{ currentError }}</p>
      <p v-if="currentLoading" role="status">{{ journeyCopy.loading }}</p>
      <template v-if="game">
      <div class="character-game-context">
        <span class="muted">{{ t('currentSave') }}: <strong>{{ game }}</strong></span>
        <RouterLink class="content-button" :to="{ name: 'play', query: playQuery }">{{ journeyCopy.returnToPlay }}</RouterLink>
      </div>
      <header class="character-section-head"><h2>{{ t('currentGameCharacters') }}</h2><span>{{ data?.players?.length || 0 }}</span></header>
      <div class="current-character-grid">
      <article v-for="p in data?.players || []" :key="p.user_id" class="char-card current-character-card">
        <div class="current-character-identity">
          <button type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="openPlayerEditor(p)">
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
            <p v-if="isProfessionalGame() && liveAdvancementRow(p.user_id)" class="muted character-level-notice">
              <template v-if="liveAdvancementRow(p.user_id)?.entitled">{{ t('advancementGranted', { level: liveAdvancementRow(p.user_id)?.target_level || 0 }) }}</template>
              <template v-else-if="data?.advancement?.mode === 'xp'">XP {{ liveAdvancementRow(p.user_id)?.xp || 0 }} / {{ liveAdvancementRow(p.user_id)?.next_level_xp || 0 }}</template>
              <template v-else>{{ t('advancementWaiting') }}</template>
            </p>
          </div>
        </div>
        <div class="actions current-character-actions">
          <button class="primary" @click="openPlayerEditor(p)">{{ isProfessionalGame() ? (String(locale).startsWith('zh') ? '高级角色中心' : 'Character center') : t('edit') }}</button>
          <button v-if="isProfessionalGame() && p.character_sheet && professionalLevel(p.character_sheet) < 20 && liveAdvancementRow(p.user_id)?.entitled" class="primary" @click="advancementPlayer = p">{{ String(locale).startsWith('zh') ? '职业升级' : 'Class advancement' }}</button>
          <button v-if="!isProfessionalGame() && levelUpPoints(p) > 0" class="primary" @click="openLevelUp(p)">{{ t('allocateAttributePointsWithCount', { points: levelUpPoints(p) }) }}</button>
          <button @click="saveToCard(p)">{{ t('saveToSharedLibrary') }}</button>
          <button class="danger" @click="deleteCharacter(p)">{{ t('remove') }}</button>
        </div>
      </article>
      <p v-if="!currentLoading && !currentError && !data?.players?.length" class="muted">{{ t('noCharacters') }}</p>
      </div>
      </template>
    </section>

    <section v-if="activePane === 'current' && Boolean(game) && Boolean(data?.npcs?.length)" class="character-section npc-section">
      <header class="character-section-head"><h2>{{ t('currentGameNpcs') }}</h2><span>{{ data?.npcs?.length || 0 }}</span></header>
      <div class="npc-strip">
        <article v-for="n in data?.npcs || []" :key="npcKey(n)" class="char-card npc-mini-card">
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

    <section v-show="activePane === 'library'" id="character-library-panel" class="character-section shared-character-section" role="tabpanel" aria-labelledby="character-library-tab">
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
      <div v-else class="check-row lore-import-toggle">
        <label>
          <input v-model="tavernIncludeLore" type="checkbox" class="tavern-include-lore">
          {{ t('tavernImportIncludeLore') }}
        </label>
        <p class="muted">{{ t('tavernImportIncludeLoreHint') }}</p>
      </div>
      <template #actions>
        <button @click="tavernImportOpen = false">{{ t('cancel') }}</button>
        <button class="primary" :disabled="tavernTarget === 'npc' && !tavernWorldId" @click="confirmTavernChoice">{{ t('chooseFile') }}</button>
      </template>
    </Modal>
      <div class="character-library-toolbar">
        <input v-model="characterSearch" type="search" :placeholder="t('characterLibrarySearch')">
        <select v-model="characterSort"><option value="name">{{ t('characterSortName') }}</option><option value="rule">{{ t('characterSortRule') }}</option></select>
        <div class="character-view-switch"><button type="button" :aria-label="journeyCopy.gridView" :class="{ active: libraryView === 'grid' }" @click="libraryView = 'grid'">▦</button><button type="button" :aria-label="journeyCopy.listView" :class="{ active: libraryView === 'list' }" @click="libraryView = 'list'">☷</button></div>
        <div class="actions character-import-actions"><template v-if="selectedCardIds.size"><button :disabled="busy" @click="exportSelected">{{ t('exportSelected') }}</button><button class="danger" :disabled="busy" @click="deleteSelectedCards">{{ t('deleteSelectedCards') }}</button></template><button class="primary" :disabled="busy" @click="diceframeInput?.click()">{{ t('importDiceframeCard') }}</button><button :disabled="busy" @click="openTavernImport">{{ t('importTavernCard') }}</button></div>
      </div>
    <div class="card-grid character-library-grid" :class="`view-${libraryView}`">
      <article v-for="c in filteredCards" :key="c.card_id || c.id" class="char-card library-character-card">
        <div class="character-card-summary">
          <input type="checkbox" :checked="selectedCardIds.has(cardId(c))" @change="toggleCardSelect(cardId(c))" class="card-select" :title="t('selectCard')">
          <button type="button" class="portrait-edit-button" :title="t('clickToChangeAvatar')" @click="openCardEditor(c)">
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
          <button v-if="isProfessionalCard(c) && professionalLevel(c) < 20" class="primary" @click="advancementCard = c">{{ String(locale).startsWith('zh') ? '职业升级' : 'Level up' }}</button>
          <button @click="openCardEditor(c)">{{ isProfessionalCard(c) ? (String(locale).startsWith('zh') ? '高级角色中心' : 'Character center') : t('editCard') }}</button>
          <button @click="exportSingleCard(c)">{{ t('export') }}</button>
          <button class="danger" @click="deleteCard(c)">{{ t('delete') }}</button>
        </div>
      </article>
      <p v-if="libraryLoading" role="status">{{ journeyCopy.loading }}</p>
      <p v-if="!libraryLoading && !libraryError && !filteredCards.length" class="muted">{{ data?.cards?.length ? t('characterLibraryNoMatches') : t('noSharedCards') }}</p>
    </div>
    </section>

    <Modal v-if="edit" :title="t('editCharacter')" @close="edit = null">
      <p v-if="characterSaveError" class="error-banner" role="alert">{{ characterSaveError }}</p>
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
      <label>{{ currencyEditableUnitLabel(ruleMeta.currency_system || null, currencyLabel(ruleMeta)) }}<input type="text" inputmode="decimal" v-model="edit.goldText" :step="currencyInputStep(ruleMeta.currency_system || null)"></label>
      <label>{{ t('attributes') }} <span class="attr-points">{{ t('pointsRemaining', { points: attrPoints }) }}</span></label>
      <div class="attr-sliders">
        <div v-for="a in editRuleAttrs" :key="a.key" class="attr-row">
          <span class="attr-name">{{ attrDisplayName(a) }}</span>
          <input type="range" :min="a.min" :max="a.max * 2" v-model.number="edit.attributes[a.key]">
          <input type="number" class="attr-val" :min="a.min" v-model.number="edit.attributes[a.key]">
        </div>
      </div>
      <label>{{ t('skills') }}</label>
      <SkillEditor v-model="edit.skills" :pool="skillPool" :meta="ruleMeta" :attributes="edit.attributes" />
      <label>{{ t('backgroundStory') }}<textarea rows="3" v-model="edit.background"></textarea></label>
      <ItemEditor v-model:equipment="edit.equipment" v-model:inventory="edit.inventory" :damage-dice-enabled="damageDiceEnabled" />
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
      <SkillEditor v-model="editCard.skills" :pool="skillPool" :meta="ruleMeta" />
      <label>{{ t('background') }}<textarea rows="4" v-model="editCard.background"></textarea></label>
      <label>{{ t('initialMoney') }}{{ editableUnitSuffix }}<input type="text" inputmode="decimal" v-model="editCard.goldText" :step="currencyInputStep(ruleMeta.currency_system || null)"></label>
      <template #actions>
        <button @click="editCard = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveCardEdit">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <RulesetAdvancementHost
      v-if="advancementCard"
      :runtime-id="String(advancementCard.ruleset_runtime?.id || '')"
      :rule-id="String(advancementCard.rule_id || ruleId)"
      :character="advancementCard"
      :card-id="cardId(advancementCard)"
      :revision="Number(advancementCard.ruleset_revision || 0)"
      :language="String(advancementCard.language || locale)"
      @applied="onCardAdvanced"
      @cancel="advancementCard = null"
    />

    <RulesetAdvancementHost
      v-if="advancementPlayer?.character_sheet"
      :runtime-id="String(data?.ruleset_runtime?.id || '')"
      :rule-id="ruleId"
      :character="advancementPlayer.character_sheet"
      :game-key="game"
      :user-id="advancementPlayer.user_id"
      :revision="Number(advancementPlayer.character_sheet.ruleset_revision || 0)"
      :language="String(locale)"
      @applied="onLiveCharacterAdvanced"
      @cancel="advancementPlayer = null"
    />

    <Modal v-if="professionalEdit" dialog-class="professional-character-dialog" :title="String(locale).startsWith('zh') ? '高级角色中心' : 'Advanced character center'" @close="professionalEdit = null">
      <RulesetCharacterCenterHost
        :runtime-id="professionalEdit.runtimeId"
        :character="professionalEdit.character"
        :target="professionalEdit.target"
        :rule-id="professionalEdit.ruleId"
        :language="String(locale)"
        :game-key="professionalEdit.gameKey"
        :user-id="professionalEdit.userId"
        :card-id="professionalEdit.cardId"
        @saved="onProfessionalSaved"
        @cancel="professionalEdit = null"
      />
    </Modal>

    <Modal v-if="editNpcPortrait" :title="`${editNpcPortrait.name} · ${t('characterAvatar')}`" @close="editNpcPortrait = null">
      <PortraitPicker v-model="editNpcPortrait.portrait" :rule-id="ruleId" :seed="editNpcPortrait.npcId" :name="editNpcPortrait.name" />
      <template #actions>
        <button @click="editNpcPortrait = null">{{ t('cancel') }}</button>
        <button class="primary" :disabled="busy" @click="saveNpcPortrait">{{ t('saveAction') }}</button>
      </template>
    </Modal>

    <RulesetExperienceHost
      v-if="showWizard && isSelectedProfessionalRule()"
      :rule-id="libraryRuleId"
      :language="String(locale)"
      @submit="onWizardSubmit"
      @cancel="showWizard = false"
    />
    <CharacterWizard
      v-else-if="showWizard"
      :rule-meta="libraryRuleMeta"
      :rule-attrs="libraryRuleAttrs"
      :attr-total="libraryRuleAttrsTotal"
      :skill-pool="skillPool"
      :rule-id="libraryRuleId"
      :language="String(locale)"
      @submit="onWizardSubmit"
      @cancel="showWizard = false"
    />
  </section>
</template>
