<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '../../api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterListResponse, CharacterSkill, GameDetail, PlayerCreateResponse, RuleAttribute, RuleMeta } from '../../api/types'
import { rememberCurrentGame } from '../../stores/gameContext'
import { attrDisplayName, suggestedAttributes, skillPointCost } from '../../utils/ruleSchema'

interface JoinSkill { name: string; value: string | number }
interface JoinForm {
  character_name: string
  race: string
  class: string
  hp: string | number
  background: string
  attributes: Record<string, number>
  skills: JoinSkill[]
  identity?: Record<string, unknown>
  [key: string]: unknown
}

const route = useRoute(), router = useRouter()
const gameKey = computed(() => String(route.query.game || ''))
const linkUser = computed(() => route.query.user ? String(route.query.user) : '')
const detail = ref<Partial<GameDetail>>({})
const attrs = ref<RuleAttribute[]>([])
const attrTotal = ref(0)
const ruleMeta = ref<RuleMeta>({})
const cards = ref<CharacterCard[]>([])
const form = ref<JoinForm>({ character_name: '', race: '', class: '', hp: '', background: '', attributes: {}, skills: [{ name: '', value: '' }] })
const error = ref(''), busy = ref(false)
const sheetReady = ref(false)
const needRoomPassword = ref(false), roomPasswordInput = ref('')
const resumeUser = ref('')

const fallbackAttrs: RuleAttribute[] = [
  { key: 'str', name: '力量', min: 1, max: 100 },
  { key: 'con', name: '体质', min: 1, max: 100 },
  { key: 'dex', name: '敏捷', min: 1, max: 100 },
  { key: 'int', name: '智力', min: 1, max: 100 },
  { key: 'wis', name: '感知', min: 1, max: 100 },
  { key: 'cha', name: '魅力', min: 1, max: 100 },
]

const attrSum = computed(() => Object.values(form.value.attributes || {}).reduce((sum, value) => sum + (Number(value) || 0), 0))
const inferredAttrTotal = computed(() => attrs.value.reduce((sum, attr) => {
  const min = Number(attr.min ?? 0) || 0
  const max = Number(attr.max ?? attr.min ?? 0) || min
  return sum + Math.floor((min + max) / 2)
}, 0))
const attrLimit = computed(() => attrTotal.value || Number(ruleMeta.value.attribute_points || 0) || inferredAttrTotal.value)
const attrRemaining = computed(() => Math.max(attrLimit.value, attrSum.value) - attrSum.value)
const attrOverLimit = computed(() => Boolean(attrLimit.value && attrSum.value > attrLimit.value))
const maxSkills = computed(() => Number(ruleMeta.value.max_skills || 0))
const skillPointTotal = computed(() => Number(ruleMeta.value.skill_point_total || 0))
const maxSkillValue = computed(() => Number(ruleMeta.value.max_skill_value || 0))
const filledSkills = computed(() => form.value.skills.filter(s => s.name.trim()))
const skillSpent = computed(() => filledSkills.value.reduce((sum, skill) => sum + skillPointCost(skill, ruleMeta.value), 0))
const skillOverLimit = computed(() =>
  Boolean((maxSkills.value && filledSkills.value.length > maxSkills.value)
    || (skillPointTotal.value && skillSpent.value > skillPointTotal.value)
    || (maxSkillValue.value && filledSkills.value.some(s => (Number(s.value || 0) || 0) > maxSkillValue.value)))
)
const diceHint = computed(() =>
  ruleMeta.value.mechanics === 'dnd5e_core'
    ? 'DND 小抄：优势=2d20取高，劣势=2d20取低；同时有优势和劣势时抵消。'
    : ''
)

function skillToForm(skill: string | CharacterSkill): JoinSkill {
  return typeof skill === 'string' ? { name: skill, value: '' } : { name: skill.name, value: skill.value ?? '' }
}

onMounted(async () => {
  const stored = localStorage.getItem('trpg_play_user_' + gameKey.value)
  if (stored) { rememberCurrentGame(gameKey.value); router.replace({ name: 'play', query: { game: gameKey.value, user: stored, share: '1' } }); return }
  if (linkUser.value) resumeUser.value = linkUser.value
  try {
    const d = await api<GameDetail>(`/games/${encodeURIComponent(gameKey.value)}`)
    detail.value = d
    if (d.has_room_password && !localStorage.getItem('trpg_play_room_' + gameKey.value)) {
      needRoomPassword.value = true
      return
    }
    await afterGate()
  } catch (e: unknown) { error.value = errorMessage(e) }
})

async function afterGate() {
  if (resumeUser.value) { await resumeIdentity(resumeUser.value); return }
  await loadGameData()
}

async function resumeIdentity(uid: string) {
  busy.value = true; error.value = ''
  try {
    const r = await api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, { method: 'POST', body: JSON.stringify({ user_id: uid, join_as_new: false }) })
    if (r.error) throw new Error(r.error)
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

async function loadGameData() {
  sheetReady.value = false
  try {
    const [c, k] = await Promise.all([
      api<CharacterListResponse>(`/games/${encodeURIComponent(gameKey.value)}/characters`),
      api<CharacterCardsResponse>(`/games/${encodeURIComponent(gameKey.value)}/character-cards`).catch(() => ({ cards: [] })),
    ])
    attrs.value = c.rule_attrs?.length ? c.rule_attrs : fallbackAttrs
    ruleMeta.value = c.rule_meta || {}
    attrTotal.value = Number(c.rule_attrs_total || ruleMeta.value.attribute_points || 0) || inferredAttrTotal.value
    form.value.attributes = suggestedAttributes(attrs.value, attrLimit.value)
    cards.value = k.cards || []
    sheetReady.value = true
  } catch (e: unknown) { error.value = errorMessage(e) }
}

function fillSuggestedAttrs() {
  form.value.attributes = suggestedAttributes(attrs.value, attrLimit.value)
}

async function verifyRoomPassword() {
  busy.value = true; error.value = ''
  try {
    const r = await api<{ room_token: string }>(`/games/${encodeURIComponent(gameKey.value)}/verify-room-password`, { method: 'POST', body: JSON.stringify({ password: roomPasswordInput.value }) })
    localStorage.setItem('trpg_play_room_' + gameKey.value, r.room_token)
    needRoomPassword.value = false
    await afterGate()
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}

function applyCard(event: Event) {
  const i = Number((event.target as HTMLSelectElement).value)
  const card = cards.value[i]
  if (!card) return
  form.value = { ...form.value, ...JSON.parse(JSON.stringify(card)), skills: (card.skills || []).map(skillToForm) }
  if (!form.value.skills.length) form.value.skills = [{ name: '', value: '' }]
}

async function create() {
  busy.value = true; error.value = ''
  try {
    const payload = {
      ...form.value,
      hp: form.value.hp === '' ? undefined : Number(form.value.hp),
      skills: form.value.skills
        .filter(s => s.name.trim())
        .map(s => ({ name: s.name.trim(), value: s.value === '' ? undefined : Number(s.value) })),
      join_as_new: true,
    }
    const r = await api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, { method: 'POST', body: JSON.stringify(payload) })
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>
<template>
  <main class="join-page">
    <header class="join-hud">
      <div>
        <span class="section-kicker">玩家席位</span>
        <h1>{{ detail.world_name || '加入游戏' }}</h1>
        <p>{{ detail.scene || '创建角色后开始冒险' }}</p>
      </div>
      <button @click="router.push({ name: 'overview' })">返回总览</button>
    </header>

    <section v-if="needRoomPassword" class="join-form room-gate">
      <div class="sheet-head">
        <div>
          <h2>本房间需要密码</h2>
          <p>请输入房主设置的房间密码以加入「{{ detail.world_name || '本局游戏' }}」。</p>
        </div>
      </div>
      <label class="sheet-field">房间密码<input type="password" v-model="roomPasswordInput" @keyup.enter="verifyRoomPassword" placeholder="房间密码"></label>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <button class="primary submit" :disabled="busy || !roomPasswordInput.trim()" @click="verifyRoomPassword">{{ busy ? '验证中' : '验证并继续' }}</button>
    </section>

    <section v-else-if="resumeUser" class="join-form resume-block">
      <div class="sheet-head">
        <div>
          <h2>正在恢复你的角色</h2>
          <p>{{ busy ? '正在通过操作链接恢复身份…' : '准备进入游戏桌' }}</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </section>

    <section v-else-if="!sheetReady" class="join-form">
      <div class="sheet-head">
        <div>
          <h2>正在读取建卡规则</h2>
          <p>正在同步属性、技能和当前世界规则。</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </section>

    <section v-else class="join-form player-sheet-form">
      <div class="sheet-head">
        <div>
          <h2>创建你的角色</h2>
          <p>选择卡库角色或现场填写，完成后直接进入当前游戏桌。</p>
        </div>
        <span class="badge badge-active">{{ detail.solo_mode ? '单人冒险' : '多人冒险' }}</span>
      </div>

      <label v-if="cards.length" class="sheet-field">从共享卡库选择
        <select @change="applyCard">
          <option value="">新建角色卡</option>
          <option v-for="(c, i) in cards" :key="c.character_name || i" :value="i">{{ c.character_name }} · {{ c.race }} {{ c.class }}</option>
        </select>
      </label>

      <section class="sheet-section">
        <h3>身份</h3>
        <div class="two-cols">
          <label>角色名<input v-model="form.character_name" maxlength="40"></label>
          <label>种族<input v-model="form.race"></label>
          <label>职业<input v-model="form.class"></label>
          <label>HP<input type="number" v-model="form.hp" placeholder="留空按规则计算"></label>
        </div>
      </section>

      <section class="sheet-section">
        <h3>属性</h3>
        <p class="muted sheet-hint">滑块用于快速调整，数字框可直接输入任意数值。</p>
        <p v-if="ruleMeta.attr_hint" class="muted sheet-hint">{{ ruleMeta.attr_hint }}</p>
        <p v-if="diceHint" class="muted sheet-hint">{{ diceHint }}</p>
        <p class="muted sheet-hint" :class="{ warn: attrOverLimit }">
          属性总和 {{ attrSum }}<span v-if="attrLimit"> / {{ attrLimit }} · 剩余 {{ attrRemaining }} 点</span>
          <button type="button" class="chip" @click="fillSuggestedAttrs">填入建议值</button>
        </p>
        <div class="attrs">
          <label v-for="a in attrs" :key="a.key">
            <span>{{ attrDisplayName(a) }}</span>
            <input type="range" :min="a.min || 1" :max="a.max || 100" v-model.number="form.attributes[a.key]">
            <input type="number" v-model.number="form.attributes[a.key]" title="可直接输入任意数值">
          </label>
        </div>
      </section>

      <section class="sheet-section">
        <div class="skills-title"><h3>技能</h3><button @click="form.skills.push({ name: '', value: '' })">添加技能</button></div>
        <p v-if="ruleMeta.skill_hint" class="muted sheet-hint">{{ ruleMeta.skill_hint }}</p>
        <p class="muted sheet-hint" :class="{ warn: skillOverLimit }">
          <span v-if="maxSkills">技能 {{ filledSkills.length }} / {{ maxSkills }}</span>
          <span v-if="skillPointTotal"> · 技能点 {{ skillSpent }} / {{ skillPointTotal }}</span>
          <span v-if="maxSkillValue"> · 单技能上限 {{ maxSkillValue }}</span>
        </p>
        <div class="skill-row" v-for="(s, i) in form.skills" :key="i">
          <input v-model="s.name" placeholder="技能名">
          <input type="number" v-model="s.value" placeholder="数值">
          <button @click="form.skills.splice(i, 1)" title="删除技能">×</button>
        </div>
      </section>

      <label class="sheet-field">角色背景<textarea rows="5" v-model="form.background"></textarea></label>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <button class="primary submit" :disabled="busy || !form.character_name.trim()" @click="create">{{ busy ? '创建中' : '创建角色并进入' }}</button>
    </section>
  </main>
</template>
