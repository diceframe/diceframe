<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterItem, CharacterListResponse, CharacterSheet, CharacterSkill, JsonObject, RuleDetailResponse, RuleMeta, SkillSpec } from '@/api/types'
import { readCurrentGame } from '@/stores/gameContext'
import { importTavernCard } from '@/utils/characterImport'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import Modal from '@/components/ui/Modal.vue'
import CharacterWizard from '@/components/admin/CharacterWizard.vue'
import SkillEditor from '@/components/admin/SkillEditor.vue'
import LevelUpDialog from '@/components/admin/LevelUpDialog.vue'
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
  equipText: string
  invText: string
  keyText: string
  fields: IdentityField[]
  identityValues: Record<string, string>
}
interface LevelUpState { player: import('@/api/types').Player; levelUpPoints: number }
interface CardEditForm {
  card_id: string
  character_name: string
  race: string
  class: string
  skills: CharacterSkill[]
  background: string
  gold: number
}
interface CharacterCardPatch extends JsonObject {
  character_name: string
  race: string
  class: string
  skills: CharacterSkill[]
  background: string
  gold: number
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
}

const toast = useToast()
const { confirm } = useConfirm()

const game = ref(readCurrentGame())
const data = ref<CharacterData | null>(null)
const error = ref('')
const busy = ref(false)
const edit = ref<CharacterEditForm | null>(null)
const editLevelUp = ref<LevelUpState | null>(null)
const editCard = ref<CardEditForm | null>(null)
const showWizard = ref(false)

const ruleMeta = ref<RuleMeta>({})
const ruleAttrs = ref<RuleAttr[]>([])
const ruleAttrsTotal = ref(60)
const ruleId = ref('')
const ruleDetail = ref<{ skill_pool?: Array<string | SkillSpec>; skills?: Array<string | SkillSpec> } | null>(null)

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

function errorMessage(err: unknown): string { return err instanceof Error ? err.message : String(err || '操作失败') }
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
function levelUpPoints(player: import('@/api/types').Player): number { return Number(player.character_sheet?.level_up_points || 0) }
function npcKey(card: CharacterCard): string { return String(card.id || card.card_id || card.name || card.character_name || Math.random()) }

watch(ruleId, async (id) => {
  if (!id) { ruleDetail.value = null; return }
  try {
    const rd = await api<RuleDetailResponse>(`/rules/${id}`)
    ruleDetail.value = rd.rule || null
  } catch { ruleDetail.value = null }
})

async function load() {
  error.value = ''; data.value = null
  try {
    if (game.value) {
      const [chars, cards] = await Promise.all([
        api<CharacterListResponse>(`/games/${encodeURIComponent(game.value)}/characters`),
        api<CharacterCardsResponse>('/character-cards'),
      ])
      data.value = { ...chars, cards: cards.cards || [] }
      ruleMeta.value = chars.rule_meta || {}
      ruleAttrs.value = chars.rule_attrs || []
      ruleAttrsTotal.value = chars.rule_attrs_total || 60
      ruleId.value = String(ruleMeta.value.rule_id || '')
    } else {
      const cards = await api<CharacterCardsResponse>('/character-cards')
      data.value = { players: [], cards: cards.cards || [] }
    }
  } catch (e: unknown) { error.value = errorMessage(e) }
}
const route = useRoute()
const tavernInput = ref<HTMLInputElement | null>(null)
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
  busy.value = true
  try {
    const card = await importTavernCard(file)
    toast.success(`已导入「${card.character_name || file.name}」`)
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
    equipText: itemLines(cs.equipment, ['name', 'type', 'damage', 'slot', 'quality'], { type: 'weapon', damage: 0, slot: 'main_hand', quality: 'common' }),
    invText: itemLines(cs.inventory, ['name', 'qty', 'effect'], { qty: 1, effect: '' }),
    keyText: itemLines(cs.key_items, ['name', 'category', 'note'], { category: 'key_item', note: '' }),
    fields,
    identityValues: Object.fromEntries(fields.map((f: IdentityField) => [f.key, getIdentityValue(cs, f)])),
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
    }
    e.fields.forEach((f: IdentityField) => setIdentityUpdate(updates, f, e.identityValues[f.key]))
    updates.identity = updates.identity || {}
    updates.identity.background = e.background
    updates.equipment = parseLines(e.equipText, p => p.length >= 5 ? { name: p[0], type: p[1], damage: parseInt(p[2]) || 0, slot: p[3], quality: p[4] } : { name: p[0] || '', type: 'weapon', damage: 0, slot: 'main_hand', quality: 'common' })
    updates.inventory = parseLines(e.invText, p => p.length >= 3 ? { name: p[0], qty: parseInt(p[1]) || 1, effect: p[2] } : { name: p[0] || '', qty: 1, effect: '' })
    updates.key_items = parseLines(e.keyText, p => p.length >= 3 ? { name: p[0], category: p[1] || 'key_item', note: p[2] } : p.length >= 2 ? { name: p[0], category: p[1] || 'key_item' } : { name: p[0] || '', category: 'key_item' })
    await api(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(e.user_id)}`, { method: 'PUT', body: JSON.stringify(updates) })
    edit.value = null
    await load()
    toast.success('角色已保存')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteCharacter(p: import('@/api/types').Player) {
  const ok = await confirm({ title: '移除角色', content: `确定移除 ${p.character_name} 吗？该角色会从当前存档中删除。`, positiveText: '移除角色', type: 'warning' })
  if (!ok) return
  try {
    await api(`/games/${encodeURIComponent(game.value)}/character/${encodeURIComponent(p.user_id)}`, { method: 'DELETE' })
    await load()
    toast.success('已移除')
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function saveToCard(p: import('@/api/types').Player) {
  const cs = p.character_sheet || {}
  try {
    await api('/character-cards', { method: 'POST', body: JSON.stringify({ character_name: p.character_name, ...cs }) })
    await load()
    toast.success('已存入共享卡库')
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
    toast.success('属性点已分配')
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
  }
}
async function saveCardEdit() {
  const e = editCard.value
  if (!e) return
  busy.value = true
  try {
    const patch: CharacterCardPatch = {
      character_name: e.character_name.trim() || '未命名',
      race: e.race.trim() || '人类',
      class: e.class.trim() || '冒险者',
      skills: e.skills.filter(s => s.name?.trim()).map(s => ({ name: s.name.trim(), value: Number(s.value) || 0 })),
      background: e.background.trim(),
      gold: parseInt(String(e.gold)) || 0,
    }
    const r = await api<{ ok?: boolean; error?: string }>(`/character-cards/${encodeURIComponent(e.card_id)}`, { method: 'PUT', body: JSON.stringify(patch) })
    if (!r.ok) throw new Error(r.error || '保存失败')
    editCard.value = null
    await load()
    toast.success('角色卡已更新')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function deleteCard(c: CharacterCard) {
  const ok = await confirm({ title: '删除角色卡', content: `确定删除「${c.character_name}」吗？此操作不可撤销。`, positiveText: '删除角色卡', type: 'error' })
  if (!ok) return
  try {
    await api(`/character-cards/${encodeURIComponent(cardId(c))}`, { method: 'DELETE' })
    await load()
    toast.success('已删除')
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function onWizardSubmit(c: CharacterSheet & { character_name: string }) {
  busy.value = true
  try {
    await api('/character-cards', { method: 'POST', body: JSON.stringify(c) })
    showWizard.value = false
    await load()
    toast.success('已新建角色卡')
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="view archive-page characters-page">
    <header class="view-title archive-hero">
      <div>
        <h1>角色管理</h1>
        <p v-if="game">当前存档：{{ game }}</p>
        <p v-else class="muted">未选择存档，请在游玩页进入一局游戏。</p>
      </div>
      <div class="actions">
        <button class="primary" :disabled="!ruleId" @click="showWizard = true">+ 新建角色卡</button>
        <button @click="load">刷新</button>
      </div>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <h2 class="field-group">本局角色</h2>
    <div class="card-grid">
      <article v-for="p in data?.players || []" :key="p.user_id" class="char-card">
        <div>
          <h2>{{ p.character_name }}</h2>
          <p class="muted">
            {{ p.user_id }} · HP {{ p.character_sheet?.hp }}/{{ p.character_sheet?.max_hp }}
            <span v-if="levelUpPoints(p) > 0" class="warn"> · 待分配 {{ levelUpPoints(p) }} 点</span>
          </p>
        </div>
        <div class="actions">
          <button @click="openEdit(p)">编辑</button>
          <button v-if="levelUpPoints(p) > 0" class="primary" @click="openLevelUp(p)">分配属性点 ({{ levelUpPoints(p) }})</button>
          <button @click="saveToCard(p)">存入共享卡库</button>
          <button class="danger" @click="deleteCharacter(p)">移除</button>
        </div>
      </article>
      <p v-if="!data?.players?.length" class="muted">暂无角色。</p>
    </div>

    <h2 class="field-group" v-if="data?.npcs?.length">本局 NPC</h2>
    <div class="card-grid" v-if="data?.npcs?.length">
      <article v-for="n in data.npcs" :key="npcKey(n)" class="char-card">
        <div>
          <h2>{{ n.character_name || n.name || '未命名' }}<small v-if="n.tier === 'core'" class="muted"> · 核心</small></h2>
          <p class="muted">{{ [n.relation ? '关系 ' + n.relation : '', n.status ? '状态 ' + n.status : '', n.first_seen_round ? '首次 Round ' + n.first_seen_round : ''].filter(Boolean).join(' · ') }}</p>
          <p v-if="n.note || n.description" class="muted">{{ String(n.note || n.description).slice(0, 120) }}</p>
        </div>
      </article>
    </div>

    <h2 class="field-group" style="display:flex;align-items:center;justify-content:space-between"><span>共享角色卡库</span><button class="primary" :disabled="busy" @click="tavernInput?.click()">导入酒馆卡</button></h2>
    <input ref="tavernInput" type="file" accept=".json,application/json" @change="onImportTavern" hidden>
    <div class="card-grid">
      <article v-for="c in data?.cards || []" :key="c.card_id || c.id" class="char-card">
        <div>
          <h2>{{ c.character_name }}</h2>
          <p class="muted">{{ c.race }} · {{ c.class }}<span v-if="c.source"> · 来源 {{ c.source }}</span></p>
          <p v-if="c.background" class="muted">{{ String(c.background).slice(0, 80) }}</p>
        </div>
        <div class="actions">
          <button @click="openCardEdit(c)">编辑卡片</button>
          <button class="danger" @click="deleteCard(c)">删除</button>
        </div>
      </article>
      <p v-if="!data?.cards?.length" class="muted">暂无共享卡。</p>
    </div>

    <Modal v-if="edit" title="编辑角色" @close="edit = null">
      <label>角色名<input v-model="edit.character_name"></label>
      <label v-for="f in edit.fields" :key="f.key">{{ identityLabel(f) }}<input v-model="edit.identityValues[f.key]"></label>
      <label>等级<input type="number" v-model.number="edit.level"></label>
      <label>HP / 最大 HP
        <div class="row">
          <input type="number" v-model.number="edit.hp.current" placeholder="HP">
          <input type="number" v-model.number="edit.hp.max" placeholder="最大 HP">
        </div>
      </label>
      <p v-if="autoHp" class="form-hint">规则建议 HP：<strong>{{ autoHpValue }}</strong>。你仍然可以手填。</p>
      <label>{{ currencyLabel(ruleMeta) }}<input type="number" v-model.number="edit.gold"></label>
      <label>属性 <span class="attr-points">剩余 {{ attrPoints }} 点</span></label>
      <div class="attr-sliders">
        <div v-for="a in editRuleAttrs" :key="a.key" class="attr-row">
          <span class="attr-name">{{ attrDisplayName(a) }}</span>
          <input type="range" :min="a.min" :max="a.max * 2" v-model.number="edit.attributes[a.key]">
          <input type="number" class="attr-val" :min="a.min" v-model.number="edit.attributes[a.key]">
        </div>
      </div>
      <label>技能</label>
      <SkillEditor v-model="edit.skills" :pool="skillPool" />
      <label>背景故事<textarea rows="3" v-model="edit.background"></textarea></label>
      <label>装备（每行: 名称|类型|伤害|部位|品质，不含|则视为名称）<textarea rows="3" v-model="edit.equipText"></textarea></label>
      <label>背包（每行: 名称|数量|效果）<textarea rows="3" v-model="edit.invText"></textarea></label>
      <label>关键物品（每行: 名称|分类|备注）<textarea rows="3" v-model="edit.keyText"></textarea></label>
      <template #actions>
        <button @click="edit = null">取消</button>
        <button class="primary" :disabled="busy" @click="saveCharacter">保存</button>
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

    <Modal v-if="editCard" title="编辑角色卡" @close="editCard = null">
      <label>角色名<input v-model="editCard.character_name"></label>
      <label>族群/身份<input v-model="editCard.race"></label>
      <label>职业/定位<input v-model="editCard.class"></label>
      <label>技能</label>
      <SkillEditor v-model="editCard.skills" :pool="skillPool" />
      <label>背景<textarea rows="4" v-model="editCard.background"></textarea></label>
      <label>初始金钱<input type="number" v-model.number="editCard.gold"></label>
      <template #actions>
        <button @click="editCard = null">取消</button>
        <button class="primary" :disabled="busy" @click="saveCardEdit">保存</button>
      </template>
    </Modal>

    <CharacterWizard
      v-if="showWizard"
      :rule-meta="ruleMeta"
      :rule-attrs="ruleAttrs"
      :attr-total="ruleAttrsTotal"
      :skill-pool="skillPool"
      :rule-id="ruleId"
      @submit="onWizardSubmit"
      @cancel="showWizard = false"
    />
  </section>
</template>
