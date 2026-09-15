<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '@/api/client'
import type { CharacterCard, CharacterCardsResponse, CharacterListResponse, CharacterPortrait, CharacterSheet, CharacterSkill, GameDetail, PlayerCreateResponse, RuleAttribute, RuleMeta, RulesetRuntimeMeta } from '@/api/types'
import { rememberCurrentGame } from '@/stores/gameContext'
import { isStoredPlayerMember } from '@/utils/joinIdentity'
import { attrDisplayName, suggestedAttributes, skillPointCost } from '@/utils/ruleSchema'
import { useLocale, type Locale } from '@/composables/useLocale'
import { useConfirm } from '@/composables/useConfirm'
import { characterCardNeedsConversion, characterCardRuleName } from '@/utils/characterCards'
import { activePeerGameClient } from '@/peer/game/bridge'
import { usePeerSessionStore } from '@/peer/store/peerSession'
import { friendlyPeerDetail } from '@/features/peer/friendlyDetail'
import PortraitPicker from '@/components/admin/PortraitPicker.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import RulesetExperienceHost from '@/features/rulesets/RulesetExperienceHost.vue'

interface JoinSkill { name: string; value: string | number; effect?: string }
interface JoinForm {
  character_name: string
  race: string
  class: string
  hp: string | number
  background: string
  attributes: Record<string, number>
  skills: JoinSkill[]
  identity?: Record<string, unknown>
  portrait?: CharacterPortrait | null
  [key: string]: unknown
}

const route = useRoute(), router = useRouter()
const { locale, setLocale, t } = useLocale()
const { confirm } = useConfirm()
const gameKey = computed(() => String(route.query.game || ''))
const linkUser = computed(() => route.query.user ? String(route.query.user) : '')
const detail = ref<Partial<GameDetail>>({})
const attrs = ref<RuleAttribute[]>([])
const attrTotal = ref(0)
const ruleMeta = ref<RuleMeta>({})
const cards = ref<CharacterCard[]>([])
const rulesetRuntime = ref<RulesetRuntimeMeta | null>(null)
const professionalInitial = ref<CharacterSheet | undefined>()
const professionalHostKey = ref(0)
const form = ref<JoinForm>({ character_name: '', race: '', class: '', hp: '', background: '', attributes: {}, skills: [{ name: '', value: '' }] })
// 效果说明默认折叠，避免技能多时铺开一排 textarea。
const openSkillEffects = ref<Set<number>>(new Set())
function toggleSkillEffect(i: number) {
  const next = new Set(openSkillEffects.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  openSkillEffects.value = next
}
const error = ref(''), busy = ref(false)
/** peer 会话恢复失败等场景的原始错误码转成人话；其它错误原样展示。 */
const displayError = computed(() => friendlyPeerDetail(error.value, t))
const sheetReady = ref(false)
const needRoomPassword = ref(false), roomPasswordInput = ref('')
const resumeUser = ref('')
const backgroundLimit = 8000
const usesProfessionalBuilder = computed(() => (
  rulesetRuntime.value?.capabilities.character_builder === 'professional'
))

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

function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}

function skillToForm(skill: string | CharacterSkill): JoinSkill {
  if (typeof skill === 'string') return { name: skill, value: '' }
  const row: JoinSkill = { name: skill.name, value: skill.value ?? '' }
  const effect = String(skill.effect || '').trim()
  if (effect) row.effect = effect
  return row
}

onMounted(async () => {
  // P2P 直连局：刷新后先恢复 peer 会话（不落服务器 /games 接口）。
  if (route.query.peer === '1' && !activePeerGameClient()) {
    const peerSession = usePeerSessionStore()
    if (peerSession.hasPersistedSession()) {
      peerSession.restore(api)
      const savedGameKey = peerSession.gameKey || gameKey.value
      if (!peerSession.isHost) {
        // guest 等连接建立后向 host 重绑身份再进游玩页。
        watch(() => peerSession.state, async (next) => {
          if (next !== 'connected' && next !== 'error') return
          if (next === 'connected') await peerSession.rebindIdentity()
          rememberCurrentGame(savedGameKey)
          router.replace({
            name: 'play',
            query: { game: savedGameKey, share: '1', peer: '1' },
          })
        }, { immediate: true })
      } else {
        rememberCurrentGame(savedGameKey)
        router.replace({ name: 'play', query: { game: savedGameKey, peer: '1' } })
      }
      return
    }
  }
  const stored = localStorage.getItem('trpg_play_user_' + gameKey.value)
  try {
    const d = await api<GameDetail>(`/games/${encodeURIComponent(gameKey.value)}`)
    detail.value = d
    if (stored) {
      // 校验本地身份是否仍是成员：被踢后缓存过期，不能再盲跳游玩界面。
      if (isStoredPlayerMember(d, stored)) {
        rememberCurrentGame(gameKey.value)
        router.replace({ name: 'play', query: { game: gameKey.value, user: stored, share: '1' } })
        return
      }
      // 被踢/身份过期：清掉本地缓存，走正常重新加入（尊重房间密码等门槛）。
      localStorage.removeItem('trpg_play_user_' + gameKey.value)
    }
    if (linkUser.value) resumeUser.value = linkUser.value
    if (d.has_room_password && !localStorage.getItem('trpg_play_room_' + gameKey.value)) {
      needRoomPassword.value = true
      return
    }
    await afterGate()
  } catch (e: unknown) {
    // 详情获取失败：若本地有身份，按旧行为放行进游玩，避免成员被临时故障锁住。
    if (stored) {
      rememberCurrentGame(gameKey.value)
      router.replace({ name: 'play', query: { game: gameKey.value, user: stored, share: '1' } })
      return
    }
    error.value = errorMessage(e)
  }
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
    rulesetRuntime.value = c.ruleset_runtime || null
    attrTotal.value = Number(c.rule_attrs_total || ruleMeta.value.attribute_points || 0) || inferredAttrTotal.value
    form.value.attributes = suggestedAttributes(attrs.value, attrLimit.value)
    cards.value = k.cards || []
    sheetReady.value = true
  } catch (e: unknown) { error.value = errorMessage(e) }
}

async function applyProfessionalCard(event: Event) {
  const select = event.target as HTMLSelectElement
  if (select.value === '') return
  const card = cards.value[Number(select.value)]
  if (!card) return
  if (characterCardNeedsConversion(card, ruleMeta.value.rule_id)) {
    const ok = await confirm({
      title: t('cardRuleMismatchTitle'),
      content: t('cardRuleMismatchContent', {
        source: characterCardRuleName(card, t('unboundRule')),
        target: String(ruleMeta.value.rule_name || ruleMeta.value.rule_id || ''),
      }),
      positiveText: t('continueAndReview'),
      negativeText: t('cancel'),
      type: 'warning',
    })
    if (!ok) { select.value = ''; return }
  }
  professionalInitial.value = JSON.parse(JSON.stringify(card)) as CharacterCard
  professionalHostKey.value += 1
}

async function createProfessional(character: CharacterSheet) {
  busy.value = true; error.value = ''
  try {
    const r = await api<PlayerCreateResponse>(`/games/${encodeURIComponent(gameKey.value)}/players`, {
      method: 'POST', body: JSON.stringify({ ...character, join_as_new: true }),
    })
    localStorage.setItem('trpg_play_user_' + gameKey.value, r.user_id)
    rememberCurrentGame(gameKey.value, detail.value?.world_name || '')
    router.replace({ name: 'play', query: { game: gameKey.value, user: r.user_id, share: '1' } })
  } catch (e: unknown) { error.value = errorMessage(e) } finally { busy.value = false }
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

async function applyCard(event: Event) {
  const select = event.target as HTMLSelectElement
  if (select.value === '') return
  const i = Number(select.value)
  const card = cards.value[i]
  if (!card) return
  const mismatch = characterCardNeedsConversion(card, ruleMeta.value.rule_id)
  if (mismatch) {
    const ok = await confirm({
      title: t('cardRuleMismatchTitle'),
      content: t('cardRuleMismatchContent', {
        source: characterCardRuleName(card, t('unboundRule')),
        target: String(ruleMeta.value.rule_name || ruleMeta.value.rule_id || ''),
      }),
      positiveText: t('continueAndReview'),
      negativeText: t('cancel'),
      type: 'warning',
    })
    if (!ok) { select.value = ''; return }
  }
  const copied = JSON.parse(JSON.stringify(card)) as CharacterCard
  const targetSuggested = suggestedAttributes(attrs.value, attrLimit.value)
  const targetAttributes = mismatch
    ? Object.fromEntries(attrs.value.map(attr => [attr.key, Number(card.attributes?.[attr.key] ?? targetSuggested[attr.key]) || 0]))
    : (card.attributes || {})
  form.value = { ...form.value, ...copied, attributes: targetAttributes, skills: (card.skills || []).map(skillToForm) }
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
        .map(s => {
          const row: JoinSkill = { name: s.name.trim(), value: s.value }
          const effect = String(s.effect || '').trim().slice(0, 500)
          if (effect) row.effect = effect
          return row
        })
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
      <div class="join-title-block">
        <BrandLogo :size="36" />
        <div class="join-title-copy">
          <span class="section-kicker">PLAYER SLOT</span>
          <h1>{{ detail.world_name || t('joinGame') }}</h1>
          <p>{{ detail.scene || t('createCharacterStartAdventure') }}</p>
        </div>
      </div>
      <div class="join-actions">
        <label class="locale-select">
          <span>{{ t('language') }}</span>
          <select :value="locale" @change="onLocaleChange">
            <option value="zh-CN">{{ t('chinese') }}</option>
            <option value="en">{{ t('english') }}</option>
          </select>
        </label>
        <button @click="router.push({ name: 'overview' })">{{ t('backToOverview') }}</button>
      </div>
    </header>

    <section v-if="needRoomPassword" class="join-form room-gate">
      <div class="sheet-head">
        <div>
          <h2>{{ t('roomNeedsPassword') }}</h2>
          <p>{{ t('roomPasswordPrompt', { name: detail.world_name || t('thisGame') }) }}</p>
        </div>
      </div>
      <label class="sheet-field">{{ t('roomPassword') }}<input type="password" v-model="roomPasswordInput" @keyup.enter="verifyRoomPassword" :placeholder="t('roomPassword')"></label>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
      <button class="primary submit" :disabled="busy || !roomPasswordInput.trim()" @click="verifyRoomPassword">{{ busy ? t('validating') : t('verifyAndContinue') }}</button>
    </section>

    <section v-else-if="resumeUser" class="join-form resume-block">
      <div class="sheet-head">
        <div>
          <h2>{{ t('restoringCharacter') }}</h2>
          <p>{{ busy ? t('restoringViaLink') : t('preparingTable') }}</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
    </section>

    <section v-else-if="!sheetReady" class="join-form">
      <div class="sheet-head">
        <div>
          <h2>{{ t('readingCharacterRules') }}</h2>
          <p>{{ t('syncingCharacterRules') }}</p>
        </div>
      </div>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
    </section>

    <section v-else-if="usesProfessionalBuilder" class="join-form professional-join-form">
      <div class="sheet-head">
        <div><h2>{{ t('createYourCharacter') }}</h2><p>{{ t('createCharacterHelp') }}</p></div>
        <span class="badge badge-active">5E 2024 SRD</span>
      </div>
      <label v-if="cards.length" class="sheet-field">{{ t('chooseFromSharedLibrary') }}
        <select @change="applyProfessionalCard"><option value="">{{ t('newCharacterCard') }}</option><option v-for="(c, i) in cards" :key="c.id || i" :value="i">{{ c.character_name }} · {{ c.race }} {{ c.class }}</option></select>
      </label>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
      <RulesetExperienceHost :key="professionalHostKey" embedded :rule-id="String(ruleMeta.rule_id || detail.rule_id || '')" :language="locale" :initial="professionalInitial" @submit="createProfessional" @cancel="router.push({ name: 'overview' })" />
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
        <PortraitPicker v-model="form.portrait" :rule-id="String(ruleMeta.rule_id || '')" :seed="form.character_name" :name="form.character_name" />
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
        <div class="skill-entry" v-for="(s, i) in form.skills" :key="i">
          <div class="skill-row">
            <input v-model="s.name" :placeholder="t('skillName')">
            <input type="number" v-model="s.value" :placeholder="t('numericValue')">
            <button
              type="button"
              class="chip skill-effect-toggle"
              :class="{ active: openSkillEffects.has(i) || Boolean(s.effect) }"
              :aria-expanded="openSkillEffects.has(i)"
              :title="t('skillEffect')"
              @click="toggleSkillEffect(i)"
            >{{ t('skillEffect') }}</button>
            <button @click="form.skills.splice(i, 1)" :title="t('deleteSkill')">×</button>
          </div>
          <textarea
            v-if="openSkillEffects.has(i)"
            class="skill-effect-input"
            rows="2"
            maxlength="500"
            v-model="s.effect"
            :placeholder="t('skillEffectPlaceholder')"
          />
        </div>
      </section>

      <label class="sheet-field">
        <span class="field-label-row"><span>{{ t('characterBackground') }}</span><small>{{ form.background.length }} / {{ backgroundLimit }}</small></span>
        <textarea class="character-background-input" rows="8" :maxlength="backgroundLimit" v-model="form.background" :placeholder="t('characterBackgroundLongHint')"></textarea>
      </label>
      <p v-if="error" class="error-banner">{{ displayError }}</p>
      <button class="primary submit" :disabled="busy || !form.character_name.trim()" @click="create">{{ busy ? t('creating') : t('createCharacterAndEnter') }}</button>
    </section>
  </main>
</template>
