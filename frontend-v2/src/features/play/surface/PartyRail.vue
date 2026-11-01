<script setup lang="ts">
import { computed } from 'vue'
import type { GameDetail, Player, RuleMeta } from '@/api/types'
import { primaryResourceList, buildSpecialStats } from '@/utils/play'
import { playerColor } from '@/utils/play'
import { useLocale } from '@/composables/useLocale'
import PortraitImage from '@/components/PortraitImage.vue'
import PartyMemberMenu from './PartyMemberMenu.vue'

const props = defineProps<{
  detail: GameDetail
  players: Player[]
  ruleMeta?: RuleMeta | null
  currentUserId?: string
  isGm?: boolean
  linkBusyUid?: string
  hostingUid?: string
}>()
const emit = defineEmits<{
  'copy-link': [uid: string]
  edit: [uid: string]
  'set-away': [uid: string, away: boolean]
  'set-control': [uid: string, mode: 'ai' | 'human']
  kick: [uid: string]
}>()

const { locale, t } = useLocale()
const submitted = computed(() => new Map((props.detail.multiplayer?.submitted_actions || []).map(action => [action.user_id, action])))
const away = computed(() => new Set((props.detail.multiplayer?.away_players || []).map(player => player.user_id)))

function actionLabel(player: Player): string {
  if (props.hostingUid === player.user_id) return t('controlAiTakingOver')
  if (away.value.has(player.user_id)) return t('away')
  const action = submitted.value.get(player.user_id)
  return action?.dice_pending ? t('needsRoll') : action ? t('acted') : t('waitingAction')
}

const localCopy = computed(() => locale.value === 'zh-CN'
  ? { table: '桌面信息', party: '队伍', members: '队伍成员', recap: '规则提示', round: '回合', human: '玩家', ai: 'AI 队友', unclaimed: '待认领', generic: '通用规则', genericHint: '规则由当前游戏服务提供。' }
  : { table: 'Table information', party: 'Party', members: 'Party members', recap: 'Rules recap', round: 'Round', human: 'Player', ai: 'AI companion', unclaimed: 'Unclaimed', generic: 'Generic rules', genericHint: 'Rules are provided by the current game service.' })

function controlLabel(player: Player): string {
  if (player.control?.mode === 'ai') return localCopy.value.ai
  if (player.control?.mode === 'unclaimed') return localCopy.value.unclaimed
  return localCopy.value.human
}

function playerResources(player: Player) {
  const sheet = player.character_sheet || {}
  return [
    ...primaryResourceList(sheet, props.ruleMeta),
    ...buildSpecialStats(sheet, props.ruleMeta?.rule_special_stats),
  ].slice(0, 2)
}

function displayRuleName() {
  return props.ruleMeta?.rule_name || props.detail.rule_id || localCopy.value.generic
}

function stateLabel(state?: string) {
  const labels: Record<string, string> = {
    paused: t('statePaused'),
    waiting: t('stateWaiting'),
    ended: t('stateEnded'),
    active_action: t('stateActiveAction'),
    active_judgment: t('stateActiveJudgment'),
  }
  return (state && labels[state]) || state || t('stateUnknown')
}
</script>

<template>
  <aside class="party-rail" data-testid="play-party-rail" aria-label="Party and table information">
    <div v-if="$slots.toolbar" class="party-rail__toolbar">
      <slot name="toolbar" />
    </div>
    <section class="party-rail__section party-rail__table">
      <div class="party-rail__eyebrow">{{ localCopy.table }}</div>
      <div class="party-rail__title-row">
        <h2>{{ localCopy.party }}</h2>
        <span class="party-rail__count">{{ players.length }}</span>
      </div>
      <p class="party-rail__rule">{{ displayRuleName() }}<span v-if="ruleMeta?.rule_version"> · {{ ruleMeta.rule_version }}</span></p>
      <dl class="party-rail__facts">
        <div><dt>{{ localCopy.round }}</dt><dd>{{ detail.round_number || 0 }}</dd></div>
        <div><dt>{{ t('status') }}</dt><dd>{{ stateLabel(detail.state) }}</dd></div>
      </dl>
    </section>

    <section class="party-rail__section">
      <div class="party-rail__section-head"><h3>{{ localCopy.members }}</h3><span>{{ players.length }}</span></div>
      <ul v-if="players.length" class="party-rail__members">
        <li v-for="player in players" :key="player.user_id" class="party-rail__member" :class="{ 'is-self': player.user_id === currentUserId }" :style="{ '--party-color': playerColor(player.user_id) }">
          <PortraitImage :portrait="player.character_sheet?.portrait" :rule-id="ruleMeta?.rule_id" :seed="player.user_id" :name="player.character_name" :size="34" />
          <div class="party-rail__member-copy">
            <strong>{{ player.character_name || player.user_id }}</strong>
            <span>{{ controlLabel(player) }}</span>
            <span v-if="detail.solo_mode === false || hostingUid === player.user_id" class="party-rail__action-state" :class="{ 'is-ready': submitted.has(player.user_id) && !submitted.get(player.user_id)?.dice_pending, 'is-away': away.has(player.user_id) }">{{ actionLabel(player) }}</span>
            <div v-if="playerResources(player).length" class="party-rail__resources">
              <span v-for="resource in playerResources(player)" :key="resource.key">{{ 'label' in resource ? resource.label : resource.name }} {{ resource.current }}<template v-if="resource.max">/{{ resource.max }}</template></span>
            </div>
          </div>
          <div class="party-rail__member-trailing">
            <i class="party-rail__presence" :title="controlLabel(player)" aria-hidden="true" />
            <PartyMemberMenu
              :player="player" :is-gm="Boolean(isGm)" :current-user-id="currentUserId" :gm-uid="detail.gm_uid"
              :member-count="players.length" :away="away.has(player.user_id)" :busy="Boolean(linkBusyUid || hostingUid)"
              @copy-link="emit('copy-link', $event)" @edit="emit('edit', $event)" @kick="emit('kick', $event)"
              @set-away="(uid, value) => emit('set-away', uid, value)" @set-control="(uid, mode) => emit('set-control', uid, mode)"
            />
          </div>
        </li>
      </ul>
      <p v-else class="party-rail__empty">{{ t('none') }}</p>
      <div v-if="$slots.membersActions" class="party-rail__members-actions">
        <slot name="membersActions" />
        <p class="party-rail__invite-hint">{{ t('inviteNewCharacterQrHint') }}</p>
      </div>
    </section>

    <slot name="actions" />

    <section class="party-rail__section party-rail__recap">
      <div class="party-rail__section-head"><h3>{{ localCopy.recap }}</h3></div>
      <p>{{ ruleMeta?.mechanics || ruleMeta?.dice_system || localCopy.genericHint }}</p>
      <div v-if="ruleMeta?.attr_hint || ruleMeta?.skill_hint" class="party-rail__hints">
        <span v-if="ruleMeta.attr_hint">{{ ruleMeta.attr_hint }}</span>
        <span v-if="ruleMeta.skill_hint">{{ ruleMeta.skill_hint }}</span>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.party-rail { display: grid; align-content: start; gap: 10px; min-width: 0; height: 100%; padding: 10px; overflow: auto; border-left: 1px solid var(--df-border-soft); background: linear-gradient(180deg, color-mix(in srgb, var(--df-surface-2) 82%, transparent), var(--df-surface-1)); }
.party-rail__toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 6px; min-width: 0; }
.party-rail__toolbar .party-rail__gm-toggle { flex: 1 1 auto; min-width: 0; justify-content: space-between; }
.party-rail.party-rail--collapsed { align-content: start; padding-inline: 6px; }
.party-rail.party-rail--collapsed > :not(.party-rail__toolbar) { display: none; }
.party-rail.party-rail--collapsed .party-rail__toolbar { justify-content: center; align-items: center; }
.party-rail__section { min-width: 0; padding: 12px; border: 1px solid var(--df-border-soft); border-radius: var(--df-radius-md); background: color-mix(in srgb, var(--df-surface-1) 88%, transparent); }
.party-rail__table { background: linear-gradient(145deg, color-mix(in srgb, var(--df-accent) 9%, var(--df-surface-1)), var(--df-surface-1)); }
.party-rail__eyebrow { color: var(--df-text-muted); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; }
.party-rail__title-row, .party-rail__section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.party-rail h2, .party-rail h3 { margin: 0; color: var(--df-text); font-family: var(--df-font-title); }
.party-rail h2 { font-size: 18px; }
.party-rail h3 { font-size: 13px; }
.party-rail__count { display: grid; min-width: 24px; height: 24px; place-items: center; border-radius: 999px; background: var(--df-interactive); color: var(--df-on-accent); font-size: 12px; font-weight: 700; }
.party-rail__rule { margin: 7px 0 0; color: var(--df-accent-strong); font-size: 12px; line-height: 1.45; }
.party-rail__facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin: 12px 0 0; }
.party-rail__facts div { min-width: 0; padding-top: 7px; border-top: 1px solid var(--df-border-soft); }
.party-rail__facts dt { color: var(--df-text-muted); font-size: 10px; }
.party-rail__facts dd { margin: 3px 0 0; overflow: hidden; color: var(--df-text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.party-rail__section-head { margin-bottom: 8px; }
.party-rail__section-head > span { color: var(--df-text-muted); font-size: 11px; }
.party-rail__members { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.party-rail__members-actions { display: grid; gap: 7px; margin-top: 10px; }
.party-rail__members-actions :deep(.party-rail__access-actions) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.party-rail__members-actions :deep(.party-rail__access-actions button) { min-width: 0; padding-inline: 5px; font-size: 12px; }
.party-rail__members-actions :deep(button) { display: inline-flex; align-items: center; justify-content: center; gap: 6px; width: 100%; min-height: 36px; border-color: color-mix(in srgb, var(--df-interactive) 72%, var(--df-border-soft)); background: color-mix(in srgb, var(--df-interactive) 15%, var(--df-surface-1)); color: var(--df-interactive-strong, var(--df-interactive)); }
.party-rail__invite-hint { margin: 0; color: var(--df-text-muted); font-size: 11px; line-height: 1.45; }
.party-rail__member { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-width: 0; padding: 7px; border: 1px solid color-mix(in srgb, var(--party-color, var(--df-border-soft)) 28%, transparent); border-radius: var(--df-radius-sm); background: color-mix(in srgb, var(--party-color, var(--df-surface-2)) 7%, var(--df-surface-2)); }
.party-rail__member .portrait-image { border-color: color-mix(in srgb, var(--party-color, var(--df-border-soft)) 72%, var(--df-border-soft)); box-shadow: 0 0 0 2px color-mix(in srgb, var(--party-color, transparent) 16%, transparent); }
.party-rail__member.is-self { border-color: color-mix(in srgb, var(--df-interactive) 52%, var(--df-border-soft)); }
.party-rail__member-copy { display: grid; min-width: 0; gap: 2px; }
.party-rail__member-copy strong { overflow: hidden; color: var(--party-color, var(--df-text)); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.party-rail__member-copy > span { color: var(--df-text-muted); font-size: 10px; }
.party-rail__member-copy .party-rail__action-state { color: var(--df-interactive-strong); }
.party-rail__member-copy .party-rail__action-state.is-ready { color: var(--df-success-strong); }
.party-rail__member-copy .party-rail__action-state.is-away { color: var(--df-text-muted); }
.party-rail__resources { display: flex; flex-wrap: wrap; gap: 3px 7px; color: var(--df-text-secondary); font-size: 10px; }
.party-rail__presence { width: 7px; height: 7px; border-radius: 50%; background: var(--party-color, var(--df-success-strong)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--party-color, var(--df-success)) 18%, transparent); }
.party-rail__member-trailing { display: grid; justify-items: center; align-content: center; gap: 6px; }
.party-rail__empty, .party-rail__recap p { margin: 0; color: var(--df-text-muted); font-size: 12px; line-height: 1.55; }
.party-rail__hints { display: grid; gap: 8px; margin-top: 9px; color: var(--df-text-secondary); font-size: 11px; }
@media (max-width: 1200px) and (min-width: 801px) { .party-rail { padding-inline: 7px; } .party-rail__section { padding: 9px; } .party-rail__resources { display: none; } }
</style>
