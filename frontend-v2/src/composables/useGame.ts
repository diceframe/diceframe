import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, gameEventSource, hasAccessToken } from '@/api/client'
import type { CharacterListResponse, GameDetail, GameLogResponse, LogEntry, LorebookResponse, LoreEntry, MapData, Player, PrivateLogResponse, PrivateMessage } from '@/api/types'
import type { LoreKeywords } from '@/utils/renderer'
import { gameFromQuery, queryString, readCurrentGame, rememberCurrentGame } from '@/stores/gameContext'

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
  const detail = ref<GameDetail|null>(null), players = ref<Player[]>([]), log = ref<LogEntry[]>([])
  const privateMessages = ref<PrivateMessage[]>([]), map = ref<MapData>({locations:[]}), lore = ref<LoreKeywords>({}), loreEntries = ref<LoreEntry[]>([]), loading=ref(false), error=ref('')
  let source:EventSource|null=null
  let pollTimer:number|undefined
  let refreshTimer:number|undefined
  const signatures:Record<string,string> = { detail:'', players:'', log:'', privateMessages:'', map:'', loreEntries:'', lore:'' }
  const player = computed(()=>players.value.find(p=>p.user_id===userId.value) || players.value[0])
  const isGm = computed(()=>!!detail.value && (!userId.value || (detail.value.gm_uid===userId.value && hasAccessToken())))

  function rememberGame(key: string) {
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
      setIfChanged('map', map, m||{locations:[]})
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
      if(!silent || !detail.value) error.value=errorMessage(e)
    }finally{if(!silent)loading.value=false}
  }

  function connect(){
    source?.close(); clearRefreshTimer(); if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
    if(!currentGame.value)return
    source=gameEventSource(currentGame.value)
    source.onmessage=()=>{ if(pollTimer){clearInterval(pollTimer);pollTimer=undefined} scheduleSilentRefresh() }
    source.onerror=()=>{ if(!pollTimer)pollTimer=window.setInterval(() => void refresh(true),30000) }
  }
  function selectGame(key:string){rememberGame(key);refresh();connect()}
  if(currentGame.value) rememberCurrentGame(currentGame.value, detail.value?.world_name || '')
  watch(() => route.query.game, (value) => {
    const next = queryString(value)
    if(next && next !== currentGame.value){
      rememberGame(next)
      refresh()
      connect()
    } else if(!currentGame.value && readCurrentGame()) {
      rememberGame(readCurrentGame())
    }
  })
  watch(() => route.query.user, () => { userId.value = routeUser() })
  onBeforeUnmount(()=>{source?.close();clearRefreshTimer();if(pollTimer)clearInterval(pollTimer)})
  return {currentGame,userId,detail,players,player,log,privateMessages,map,lore,loreEntries,loading,error,isGm,refresh,connect,selectGame}
}
