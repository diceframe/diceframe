<script setup lang="ts">
import { computed, h, ref, watch, type Component } from 'vue'
import { NDropdown, NIcon, type DropdownOption } from 'naive-ui'
import { EllipsisHorizontal, LinkOutline, CreateOutline, PauseCircleOutline, PlayCircleOutline, PersonOutline, SparklesOutline, CloseCircleOutline } from '@vicons/ionicons5'
import type { Player } from '@/api/types'
import { useLocale } from '@/composables/useLocale'

const props = defineProps<{
  player: Player
  isGm: boolean
  currentUserId?: string
  gmUid?: string
  memberCount: number
  away?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{
  'copy-link': [uid: string]
  edit: [uid: string]
  'set-away': [uid: string, away: boolean]
  'set-control': [uid: string, mode: 'ai' | 'human']
  kick: [uid: string]
}>()
const { locale, t } = useLocale()
const open = ref(false)
const memberName = computed(() => props.player.character_name || props.player.user_id)
const label = computed(() => `${locale.value === 'zh-CN' ? '成员操作' : 'Member actions'} · ${memberName.value}`)
/** GM 自己的席位绑定房主、拿不到分享链接，没有人能来认领它。 */
const isGmSeat = computed(() => !!props.gmUid && props.player.user_id === props.gmUid)
const awaitsClaim = computed(() => props.player.control?.mode === 'unclaimed' && !isGmSeat.value)
const linkLabel = computed(() => awaitsClaim.value ? t('inviteClaim') : t('operationLink'))
const icon = (component: Component) => () => h(NIcon, { size: 16 }, { default: () => h(component) })
const option = (key: string, text: string, component: Component): DropdownOption => ({ key, label: text, icon: icon(component), props: { role: 'menuitem' } })
const options = computed<DropdownOption[]>(() => {
  const uid = props.player.user_id
  const result: DropdownOption[] = [option('copy-link', linkLabel.value, LinkOutline), option('edit', t('edit'), CreateOutline)]
  if (uid !== props.currentUserId) result.push(option('away', t(props.away ? 'back' : 'away'), props.away ? PlayCircleOutline : PauseCircleOutline))
  if (props.player.control?.mode === 'ai') {
    if (!props.player.control.temporary) result.push(option('control-human', t('controlStopAi'), PersonOutline))
  } else {
    // 旧版建房页可能把 GM 席位建成「等待认领」：没人能认领它，GM 直接由自己接手。
    if (isGmSeat.value && props.player.control?.mode === 'unclaimed') result.push(option('control-human', t('controlClaimOwnSeat'), PersonOutline))
    result.push(option('control-ai', t('controlSetAi'), SparklesOutline))
  }
  if (props.memberCount > 1 && uid !== props.currentUserId && uid !== props.gmUid) {
    result.push({ key: 'danger-divider', type: 'divider' }, {
      ...option('kick', t('kick'), CloseCircleOutline),
      label: () => h('span', { style: { color: 'var(--df-danger-strong)' } }, t('kick')),
    })
  }
  return result
})
watch(() => [props.isGm, props.busy], () => { open.value = false })
function select(key: string | number) {
  open.value = false
  if (!props.isGm || props.busy || !options.value.some(item => item.key === key && item.type !== 'divider')) return
  const uid = props.player.user_id
  switch (key) {
    case 'copy-link': emit('copy-link', uid); break
    case 'edit': emit('edit', uid); break
    case 'away': emit('set-away', uid, !props.away); break
    case 'control-ai': emit('set-control', uid, 'ai'); break
    case 'control-human': emit('set-control', uid, 'human'); break
    case 'kick': emit('kick', uid); break
  }
}
</script>

<template>
  <NDropdown v-if="isGm" v-model:show="open" trigger="click" placement="bottom-end" size="large" :options="options" :menu-props="() => ({ role: 'menu', 'aria-label': label })" @select="select">
    <button class="party-member-menu" data-testid="party-member-menu" :data-player-id="player.user_id" type="button" :title="awaitsClaim ? t('inviteClaim') : label" :aria-label="awaitsClaim ? t('inviteClaim') : label" aria-haspopup="menu" :aria-expanded="open" :disabled="busy" :aria-busy="busy" @keydown.down.prevent="open = true" @keydown.esc.prevent="open = false">
      <NIcon :component="EllipsisHorizontal" size="18" />
    </button>
  </NDropdown>
</template>

<style scoped>
.party-member-menu { display: inline-flex; align-items: center; justify-content: center; width: 32px; min-width: 32px; height: 32px; min-height: 32px; padding: 0; border-radius: var(--df-radius-sm); color: var(--df-interactive-strong); background: color-mix(in srgb, var(--df-interactive) 8%, var(--df-surface-1)); }
.party-member-menu:hover, .party-member-menu:focus-visible { border-color: var(--df-interactive); background: color-mix(in srgb, var(--df-interactive) 18%, var(--df-surface-1)); }
@media (max-width: 800px) { .party-member-menu { width: 40px; min-width: 40px; height: 40px; min-height: 40px; } }
</style>
