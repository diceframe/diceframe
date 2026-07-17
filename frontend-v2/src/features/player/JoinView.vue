<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterListResponse, CharacterSkill, GameDetail, PlayerCreateResponse, RuleAttribute, RuleMeta } from '@/api/types'
import { rememberCurrentGame } from '@/stores/gameContext'
import { attrDisplayName, suggestedAttributes, skillPointCost } from '@/utils/ruleSchema'
import { useLocale } from '@/composables/useLocale'

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
const { t } = useLocale()
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

const fallbackAttrs = computed<RuleAttribute[]>(() => [
  { key: 'str', name: t('attrStrength'), min: 1, max: 100 },
  { key: 'con', name: t('attrConstitution'), min: 1, max: 100 },
  { key: 'dex', name: t('attrDexterity'), min: 1, max: 100 },
  { key: 'int', name: t('attrIntelligence'), min: 1, max: 100 },
  { key: 'wis', name: t('attrWisdom'), min: 1, max: 100 },
  { key: 'cha', name: t('attrCharisma'), min: 1, max: 100 },
])

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
    ? t('dndDiceHint')
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
    attrs.value = c.rule_attrs?.length ? c.rule_attrs : fallbackAttrs.value
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
        <span class="section-kicker">{{ t('playerSlot') }}</span>
        <h1>{{ detail.world_name || t('joinGame') }}</h1>
        <p>{{ detail.scene || t('createCharacterStartAdventure') }}</p>
      </div>
      <button @click="router.push({ name: 'overview' })">{{ t('backToOverview') }}</button>
    </header>

    <section v-if="needRoomPassword" class="join-form room-gate">
      <div class="sheet-head">
        <div>
          <h2>{{ t('roomNeedsPassword') }}</h2>
          <p>{{ t('roomPasswordPrompt', { name: detail.world_name || t('thisGame') }) }}</p>
        </div>
      </div>
      <label class="sheet-field">{{ t('roomPassword') }}<input type="password" v-model="roomPasswordInput" @keyup.enter="verifyRoomPassword" :placeholder="t('roomPassword')"></label>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <button class="primary submit" :disabled="busy || !roomPasswordInput.trim()" @click="verifyRoomPassword">{{ busy ? t('validating') : t('verifyAndContinue') }}</button>
    </section>

    <section v-else-if="resumeUser" class="join-form resume-block">
      <div class="sheet-head">
        <div>
          <h2>{{ t('restoringCharacter') }}</h2>
          <p>{{ busy ? t('restoringViaLink') : t('preparingTable') }}</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </section>

    <section v-else-if="!sheetReady" class="join-form">
      <div class="sheet-head">
        <div>
          <h2>{{ t('readingCharacterRules') }}</h2>
          <p>{{ t('syncingCharacterRules') }}</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </section>

    <section v-else class="join-form player-sheet-form">
      <div class="sheet-head">
        <div>
          <h2>{{ t('createYourCharacter') }}</h2>
          <p>{{ t('createCharacterHelp') }}</p>
        </div>
        <span class="badge badge-active">{{ detail.solo_mode ? t('soloAdventure') : t('multiplayerAdventure') }}</span>
      </div>

      <label v-if="cards.length" class="sheet-field">{{ t('chooseFromSharedLibrary') }}
        <select @change="applyCard">
          <option value="">{{ t('newCharacterCard') }}</option>
          <option v-for="(c, i) in cards" :key="c.character_name || i" :value="i">{{ c.character_name }} · {{ c.race }} {{ c.class }}</option>
        </select>
      </label>

      <section class="sheet-section">
        <h3>{{ t('identityStep') }}</h3>
        <div class="two-cols">
          <label>{{ t('characterName') }}<input v-model="form.character_name" maxlength="40"></label>
          <label>{{ t('race') }}<input v-model="form.race"></label>
          <label>{{ t('classRole') }}<input v-model="form.class"></label>
          <label>HP<input type="number" v-model="form.hp" :placeholder="t('leaveBlankAuto')"></label>
        </div>
      </section>

      <section class="sheet-section">
        <h3>{{ t('attributes') }}</h3>
        <p class="muted sheet-hint">{{ t('sliderNumberHint') }}</p>
        <p v-if="ruleMeta.attr_hint" class="muted sheet-hint">{{ ruleMeta.attr_hint }}</p>
        <p v-if="diceHint" class="muted sheet-hint">{{ diceHint }}</p>
        <p class="muted sheet-hint" :class="{ warn: attrOverLimit }">
          {{ t('attrSum') }} {{ attrSum }}<span v-if="attrLimit"> / {{ attrLimit }} · {{ t('pointsRemaining', { points: attrRemaining }) }}</span>
          <button type="button" class="chip" @click="fillSuggestedAttrs">{{ t('fillSuggestedValues') }}</button>
        </p>
        <div class="attrs">
          <label v-for="a in attrs" :key="a.key">
            <span>{{ attrDisplayName(a) }}</span>
            <input type="range" :min="a.min || 1" :max="a.max || 100" v-model.number="form.attributes[a.key]">
            <input type="number" v-model.number="form.attributes[a.key]" :title="t('directNumberInput')">
          </label>
        </div>
      </section>

      <section class="sheet-section">
        <div class="skills-title"><h3>{{ t('skills') }}</h3><button @click="form.skills.push({ name: '', value: '' })">{{ t('addSkill') }}</button></div>
        <p v-if="ruleMeta.skill_hint" class="muted sheet-hint">{{ ruleMeta.skill_hint }}</p>
        <p class="muted sheet-hint" :class="{ warn: skillOverLimit }">
          <span v-if="maxSkills">{{ t('skillCount', { count: filledSkills.length, max: maxSkills }) }}</span>
          <span v-if="skillPointTotal"> · {{ t('skillPointsSpent', { spent: skillSpent, total: skillPointTotal }) }}</span>
          <span v-if="maxSkillValue"> · {{ t('maxSingleSkill', { max: maxSkillValue }) }}</span>
        </p>
        <div class="skill-row" v-for="(s, i) in form.skills" :key="i">
          <input v-model="s.name" :placeholder="t('skillName')">
          <input type="number" v-model="s.value" :placeholder="t('numericValue')">
          <button @click="form.skills.splice(i, 1)" :title="t('deleteSkill')">×</button>
        </div>
      </section>

      <label class="sheet-field">{{ t('characterBackground') }}<textarea rows="5" v-model="form.background"></textarea></label>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <button class="primary submit" :disabled="busy || !form.character_name.trim()" @click="create">{{ busy ? t('creating') : t('createCharacterAndEnter') }}</button>
    </section>
  </main>
</template>
