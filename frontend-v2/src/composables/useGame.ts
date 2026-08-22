import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, gameEventSource, hasAccessToken, isNotFoundError } from '@/api/client'
import type { CharacterListResponse, GameDetail, GameLogResponse, LogEntry, LorebookResponse, LoreEntry, MapData, Player, PrivateLogResponse, PrivateMessage } from '@/api/types'
import type { LoreKeywords } from '@/utils/renderer'
import { clearCurrentGame, gameFromQuery, queryString, readCurrentGame, rememberCurrentGame } from '@/stores/gameContext'
import { resolveMapBackgroundAsset, revokeMapBackgroundAsset } from '@/api/mapBackgrounds'
import { activePeerGameClient } from '@/peer/game/bridge'
import { gameSseEffect, updatedGameSseCursor, type GameSsePayload } from '@/composables/gameSse'

const KEY_MAP:Record<string,keyof LoreKeywords>={npc:'npc',location:'location',item:'item',faction:'faction',event:'event',puzzle:'puzzle',other:'other',lore:'other'}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || 'Load failed') }
function buildLore(entries:LorebookResponse['entries'] = []):LoreKeywords{
  const lore:LoreKeywords={}
  for(const e of entries){
    const k=KEY_MAP[e.type||'other']||'other'
    if(!lore[k])lore[k]=[]
    if(e.name)lore[k]!.push(e.name)
  }
  return lore
}

const AUTO_REFRESH_DELAY = 120

export function useGame(){
  const route = useRoute()
  const routeGame = () => gameFromQuery(route.query)
  const routeUser = () => queryString(route.query.user)
  const currentGame = ref(routeGame() || readCurrentGame())
  const userId = ref(routeUser())
  const detail = ref<GameDetail|null>(null), players = ref<Player[]>([]), log = ref<LogEntry[]>([]), liveNarration = ref('')
  const privateMessages = ref<PrivateMessage[]>([]), map = ref<MapData>({locations:[]}), lore = ref<LoreKeywords>({}), loreEntries = ref<LoreEntry[]>([]), loading=ref(false), error=ref('')
  let source:EventSource|null=null
  let unsubscribePeerEvents:(()=>void)|null=null
  let pollTimer:number|undefined
  let refreshTimer:number|undefined
  let reconnectTimer:number|undefined
  let connectVersion=0
  let eventCursor=''
  const signatures:Record<string,string> = { detail:'', players:'', log:'', privateMessages:'', map:'', loreEntries:'', lore:'' }
  // GM 判定与后端 is_game_gm 同口径：已登录 owner（管理员账号多人共用都算）或该局主 GM 会话。
  const isGm = computed(()=>!!detail.value && (hasAccessToken() || (!!userId.value && detail.value.gm_uid===userId.value)))
  const actorId = computed(() => userId.value || (isGm.value ? detail.value?.gm_uid || '' : ''))
  const player = computed(()=>players.value.find(p=>p.user_id===actorId.value) || players.value[0])

  function rememberGame(key: string) {
    if (currentGame.value !== key) eventCursor = ''
    currentGame.value = key
    rememberCurrentGame(key, detail.value?.world_name || '')
  }

  function signature(value: unknown): string {
    return JSON.stringify(value) || ''
  }

  function setIfChanged<T>(key: keyof typeof signatures, target: Ref<T>, next: T) {
    const nextSignature = signature(next)
    if (signatures[key] !== nextSignature) {
      signatures[key] = nextSignature
      target.value = next
    }
  }

  function clearRefreshTimer() {
    if (refreshTimer) {
      clearTimeout(refreshTimer)
      refreshTimer = undefined
    }
  }

  function scheduleSilentRefresh() {
    clearRefreshTimer()
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      void refresh(true)
    }, AUTO_REFRESH_DELAY)
  }

  function clearMissingGame(gameKey: string) {
    if (currentGame.value !== gameKey) return
    // P2P 直连局走数据通道而非服务器接口；服务器 404 属预期（局在房主本机），
    // 不能据此清空页面。
    if (activePeerGameClient()?.gameKey === gameKey) return
    clearCurrentGame(gameKey)
    currentGame.value = ''
    detail.value = null
    players.value = []
    log.value = []
    privateMessages.value = []
    revokeMapBackgroundAsset(map.value)
    map.value = { locations: [] }
    signatures.map = ''
    lore.value = {}
    loreEntries.value = []
    liveNarration.value = ''
    error.value = ''
    source?.close()
    source = null
    unsubscribePeerEvents?.()
    unsubscribePeerEvents = null
    clearRefreshTimer()
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = undefined
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = undefined
    }
  }

  async function refresh(silent=false){
    const gameKey = currentGame.value
    if(!gameKey)return
    if(!silent){loading.value=true; error.value=''}
    try{
      const [d,c,l,p,m]=await Promise.all([
        api<GameDetail>(`/games/${encodeURIComponent(gameKey)}`),
        api<CharacterListResponse>(`/games/${encodeURIComponent(gameKey)}/characters`),
        api<GameLogResponse>(`/games/${encodeURIComponent(gameKey)}/log`),
        api<PrivateLogResponse>(`/games/${encodeURIComponent(gameKey)}/private-log`),
        api<MapData>(`/games/${encodeURIComponent(gameKey)}/map`)
      ])
      if(currentGame.value !== gameKey)return
      setIfChanged('detail', detail, d)
      setIfChanged('players', players, c.players||[])
      setIfChanged('log', log, l.log||[])
      setIfChanged('privateMessages', privateMessages, p.messages||p.private_log||[])
      const nextMap = m || { locations: [] }
      const nextMapSignature = signature(nextMap)
      if (signatures.map !== nextMapSignature) {
        const resolvedMap = await resolveMapBackgroundAsset(nextMap)
        if (currentGame.value !== gameKey) {
          revokeMapBackgroundAsset(resolvedMap)
          return
        }
        revokeMapBackgroundAsset(map.value)
        signatures.map = nextMapSignature
        map.value = resolvedMap
      }
      if(d.world_id && isGm.value){
        try{
          const lb=await api<LorebookResponse>(`/lorebook/${encodeURIComponent(d.world_id)}`)
          if(currentGame.value !== gameKey)return
          const entries = lb.entries||[]
          setIfChanged('loreEntries', loreEntries, entries)
          lore.value=buildLore(entries)
        }catch{
          setIfChanged('loreEntries', loreEntries, [])
          setIfChanged('lore', lore, {})
        }
      }
      else {
        setIfChanged('loreEntries', loreEntries, [])
        setIfChanged('lore', lore, {})
      }
      error.value=''
    }catch(e:unknown){
      if (isNotFoundError(e)) {
        clearMissingGame(gameKey)
        return
      }
      if(!silent || !detail.value) error.value=errorMessage(e)
    }finally{if(!silent)loading.value=false}
  }

  async function connect(){
    const version=++connectVersion
    source?.close(); source=null; clearRefreshTimer(); liveNarration.value=''
    unsubscribePeerEvents?.(); unsubscribePeerEvents=null
    if(reconnectTimer){clearTimeout(reconnectTimer);reconnectTimer=undefined}
    if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
    const gameKey=currentGame.value
    if(!gameKey)return
    const peerGame = activePeerGameClient()
    if (peerGame?.gameKey === gameKey) {
      unsubscribePeerEvents = peerGame.subscribe(scheduleSilentRefresh)
      pollTimer=window.setInterval(() => void refresh(true),30000)
      return
    }
    try{
      const next=await gameEventSource(gameKey, eventCursor)
      if(version!==connectVersion || gameKey!==currentGame.value){next.close();return}
      source=next
      source.onopen=()=>{
        if(source!==next)return
        if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
      }
      source.onmessage=(ev:MessageEvent)=>{
        if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
        eventCursor=updatedGameSseCursor(eventCursor, ev.lastEventId)
        let payload:GameSsePayload|null=null
        try{payload=JSON.parse(ev.data)}catch{payload=null}
        const effect=gameSseEffect(payload)
        if(effect==='baseline')return
        if(effect==='narration-delta'){liveNarration.value+=String(payload?.text||'');return}
        if(effect==='narration-reset'){liveNarration.value='';return}
        scheduleSilentRefresh()
      }
      source.onerror=()=>{
        source?.close(); source=null
        if(!pollTimer)pollTimer=window.setInterval(() => void refresh(true),30000)
        if(!reconnectTimer)reconnectTimer=window.setTimeout(()=>{reconnectTimer=undefined;void connect()},5000)
      }
    }catch{
      if(version!==connectVersion)return
      if(!pollTimer)pollTimer=window.setInterval(() => void refresh(true),30000)
      if(!reconnectTimer)reconnectTimer=window.setTimeout(()=>{reconnectTimer=undefined;void connect()},5000)
    }
  }
  async function selectGame(key:string){rememberGame(key);await refresh();await connect()}
  if(currentGame.value) rememberCurrentGame(currentGame.value, detail.value?.world_name || '')
  watch(() => route.query.game, async (value) => {
    const next = queryString(value)
    if(next && next !== currentGame.value){
      rememberGame(next)
      await refresh()
      await connect()
    } else if(!currentGame.value && readCurrentGame()) {
      rememberGame(readCurrentGame())
    }
  })
  watch(() => route.query.user, async () => {
    const nextUser = routeUser()
    if (nextUser === userId.value) return
    userId.value = nextUser
    eventCursor = ''
    await refresh(true)
    await connect()
  })
  watch(() => log.value.length, (next, prev) => { if ((prev ?? 0) < next) liveNarration.value = '' })
  onBeforeUnmount(()=>{connectVersion++;source?.close();unsubscribePeerEvents?.();revokeMapBackgroundAsset(map.value);clearRefreshTimer();if(pollTimer)clearInterval(pollTimer);if(reconnectTimer)clearTimeout(reconnectTimer)})
  return {currentGame,userId,actorId,detail,players,player,log,privateMessages,map,lore,loreEntries,loading,error,isGm,refresh,connect,selectGame,liveNarration}
}
