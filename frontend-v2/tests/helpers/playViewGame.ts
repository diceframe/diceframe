import { computed, ref, shallowRef } from 'vue'
import type { Ref } from 'vue'
import type { GameDetail, Player } from '@/api/types'

/**
 * PlayView 依赖多个 REST 组与 SSE；挂载前需要替身它的 game 状态载体。
 * 状态放在模块顶层，spec 通过 gameState 读写，保持与生产 useGame 的 ref 契约一致。
 */
const detail = shallowRef<GameDetail | null>(null)
const players = ref<Player[]>([{ user_id: 'ally', character_name: '阿刃' }])
const isGm = ref(false)
const currentGame = ref('web|combat|bot')
const userId = ref('ally')

export const gameState: {
  detail: Ref<GameDetail | null>
  players: Ref<Player[]>
  isGm: Ref<boolean>
  currentGame: Ref<string>
  userId: Ref<string>
} = { detail, players, isGm, currentGame, userId }

export function useGame() {
  return {
    currentGame,
    userId,
    actorId: computed(() => userId.value),
    detail,
    players,
    player: computed(() => players.value[0]),
    log: ref([]),
    privateMessages: ref([]),
    tableTalk: ref([]),
    map: ref({ locations: [] }),
    lore: ref({}),
    loreEntries: ref([]),
    loading: ref(false),
    error: ref(''),
    isGm,
    refresh: async () => undefined,
    connect: () => undefined,
    selectGame: async () => undefined,
    liveNarration: ref(''),
    checkReveals: ref<Record<string, { by: string; at: string }>>({}),
    diceRevealMode: ref<'auto' | 'click'>('auto'),
    rulesetStateSignal: shallowRef(0),
  }
}
