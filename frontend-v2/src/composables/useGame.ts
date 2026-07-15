import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, gameEventSource, hasAccessToken } from '../api/client'
import type { CharacterListResponse, GameDetail, GameLogResponse, LogEntry, LorebookResponse, LoreEntry, MapData, Player, PrivateLogResponse, PrivateMessage } from '../api/types'
import type { LoreKeywords } from '../utils/renderer'
import { gameFromQuery, queryString, readCurrentGame, rememberCurrentGame } from '../stores/gameContext'

const KEY_MAP:Record<string,keyof LoreKeywords>={npc:'npc',location:'location',item:'item',faction:'faction',event:'event',puzzle:'puzzle',other:'other',lore:'other'}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error || '加载失败') }
function buildLore(entries:LorebookResponse['entries'] = []):LoreKeywords{
  const lore:LoreKeywords={}
  for(const e of entries){
    const k=KEY_MAP[e.type||'other']||'other'
    if(!lore[k])lore[k]=[]
    if(e.name)lore[k]!.push(e.name)
  }
  return lore
}

export function useGame(){
  const route = useRoute()
  const routeGame = () => gameFromQuery(route.query)
  const routeUser = () => queryString(route.query.user)
  const currentGame = ref(routeGame() || readCurrentGame())
  const userId = ref(routeUser())
  const detail = ref<GameDetail|null>(null), players = ref<Player[]>([]), log = ref<LogEntry[]>([])
  const privateMessages = ref<PrivateMessage[]>([]), map = ref<MapData>({locations:[]}), lore = ref<LoreKeywords>({}), loreEntries = ref<LoreEntry[]>([]), loading=ref(false), error=ref('')
  let source:EventSource|null=null
  const player = computed(()=>players.value.find(p=>p.user_id===userId.value) || players.value[0])
  const isGm = computed(()=>!!detail.value && (!userId.value || (detail.value.gm_uid===userId.value && hasAccessToken())))

  function rememberGame(key: string) {
    currentGame.value = key
    rememberCurrentGame(key, detail.value?.world_name || '')
  }

  async function refresh(silent=false){
    if(!currentGame.value)return
    if(!silent){loading.value=true; error.value=''}
    try{
      const [d,c,l,p,m]=await Promise.all([
        api<GameDetail>(`/games/${encodeURIComponent(currentGame.value)}`),
        api<CharacterListResponse>(`/games/${encodeURIComponent(currentGame.value)}/characters`),
        api<GameLogResponse>(`/games/${encodeURIComponent(currentGame.value)}/log`),
        api<PrivateLogResponse>(`/games/${encodeURIComponent(currentGame.value)}/private-log`),
        api<MapData>(`/games/${encodeURIComponent(currentGame.value)}/map`)
      ])
      detail.value=d; players.value=c.players||[]; log.value=l.log||[]
      privateMessages.value=p.messages||p.private_log||[]; map.value=m||{locations:[]}
      if(d.world_id && isGm.value){try{const lb=await api<LorebookResponse>(`/lorebook/${encodeURIComponent(d.world_id)}`);loreEntries.value=lb.entries||[];lore.value=buildLore(lb.entries)}catch{loreEntries.value=[];lore.value={}}}
      else { loreEntries.value=[]; lore.value={} }
    }catch(e:unknown){error.value=errorMessage(e)}finally{if(!silent)loading.value=false}
  }
  let pollTimer:number|undefined
  function connect(){
    source?.close(); if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
    if(!currentGame.value)return
    source=gameEventSource(currentGame.value)
    source.onmessage=()=>{ if(pollTimer){clearInterval(pollTimer);pollTimer=undefined} refresh(true) }
    source.onerror=()=>{ if(!pollTimer)pollTimer=window.setInterval(refresh,30000) }
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
  onBeforeUnmount(()=>{source?.close();if(pollTimer)clearInterval(pollTimer)})
  return {currentGame,userId,detail,players,player,log,privateMessages,map,lore,loreEntries,loading,error,isGm,refresh,connect,selectGame}
}
