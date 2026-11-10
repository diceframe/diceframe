import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, gameEventSource, hasAccessToken, isNotFoundError, isPlayerAccessClosedError } from '@/api/client'
import { i18n } from '@/i18n'
import type { CharacterListResponse, GameDetail, GameLogResponse, LogEntry, CheckRevealRecord, LorebookResponse, LoreEntry, MapData, Player, PrivateLogResponse, PrivateMessage, StorySequence, TableTalkExchange, TableTalkResponse } from '@/api/types'
import type { LoreKeywords } from '@/utils/renderer'
import { clearCurrentGame, gameFromQuery, queryString, readCurrentGame, rememberCurrentGame } from '@/stores/gameContext'
import { resolveMapBackgroundAsset, revokeMapBackgroundAsset } from '@/api/mapBackgrounds'
import { activePeerGameClient } from '@/peer/game/bridge'
import { gameSseEffect, updatedGameSseCursor, type GameSsePayload } from '@/composables/gameSse'
import { isStorySequence } from '@/features/play/story/story'

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
  const detail = ref<GameDetail|null>(null), players = ref<Player[]>([]), log = ref<LogEntry[]>([]), liveNarration = ref(''), liveStory = ref<StorySequence | null>(null)
  const rulesetStateSignal = ref(0)
  const privateMessages = ref<PrivateMessage[]>([]), tableTalk = ref<TableTalkExchange[]>([]), map = ref<MapData>({locations:[]}), lore = ref<LoreKeywords>({}), loreEntries = ref<LoreEntry[]>([]), loading=ref(false), error=ref('')
  const checkReveals = ref<Record<string, CheckRevealRecord>>({}), diceRevealMode = ref<'auto'|'click'>('auto')
  // The GM closed the player entrance: stop reconnecting/polling until the player retries.
  const accessClosed = ref(false)
  let source:EventSource|null=null
  let unsubscribePeerEvents:(()=>void)|null=null
  let pollTimer:number|undefined
  let refreshTimer:number|undefined
  let reconnectTimer:number|undefined
  let connectVersion=0
  let refreshEpoch=0
  let activeRefreshes=0
  let refreshAfterCurrent=false
  let eventCursor=''
  const signatures:Record<string,string> = { detail:'', players:'', log:'', privateMessages:'', tableTalk:'', map:'', loreEntries:'', lore:'' }
  // 登录 owner 在无玩家覆盖时拥有 GM 视图；选择普通玩家预览不继承 owner 的 GM 控件。
  const isGm = computed(()=>!!detail.value && ((!userId.value && hasAccessToken()) || (!!userId.value && detail.value.gm_uid===userId.value)))
  const actorId = computed(() => userId.value || (isGm.value ? detail.value?.gm_uid || '' : ''))
  const player = computed(()=>players.value.find(p=>p.user_id===actorId.value) || players.value[0])

  function rememberGame(key: string) {
    if (currentGame.value !== key) {
      eventCursor = ''
      accessClosed.value = false
    }
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
    if (activeRefreshes) {
      refreshAfterCurrent = true
      return
    }
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
    checkReveals.value = {}
    diceRevealMode.value = 'auto'
    privateMessages.value = []
    tableTalk.value = []
    revokeMapBackgroundAsset(map.value)
    map.value = { locations: [] }
    signatures.map = ''
    lore.value = {}
    loreEntries.value = []
    liveNarration.value = ''
    liveStory.value = null
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

  function stopLiveUpdates() {
    connectVersion++
    source?.close()
    source = null
    unsubscribePeerEvents?.()
    unsubscribePeerEvents = null
    clearRefreshTimer()
    refreshAfterCurrent = false
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = undefined
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = undefined
    }
  }

  /** Retrying against a closed entrance only repeats the same 403; wait for the player. */
  function closeForPlayerAccess() {
    accessClosed.value = true
    stopLiveUpdates()
    liveNarration.value = ''
    error.value = i18n.global.t('apiErrors.PLAYER_ACCESS_CLOSED')
  }

  async function retryAccess() {
    accessClosed.value = false
    error.value = ''
    await refresh()
    if (!accessClosed.value) await connect()
  }

  async function refresh(silent=false){
    const gameKey = currentGame.value
    if(!gameKey)return false
    const viewerId = userId.value
    const epoch = ++refreshEpoch
    activeRefreshes++
    const current = () => epoch === refreshEpoch && currentGame.value === gameKey && userId.value === viewerId
    if(!silent){loading.value=true; error.value=''}
    try{
      const [d,c,l,p,tt,m]=await Promise.all([
        api<GameDetail>(`/games/${encodeURIComponent(gameKey)}`),
        api<CharacterListResponse>(`/games/${encodeURIComponent(gameKey)}/characters`),
        api<GameLogResponse>(`/games/${encodeURIComponent(gameKey)}/log`),
        api<PrivateLogResponse>(`/games/${encodeURIComponent(gameKey)}/private-log`),
        api<TableTalkResponse>(`/games/${encodeURIComponent(gameKey)}/table-talk`),
        api<MapData>(`/games/${encodeURIComponent(gameKey)}/map`)
      ])
      if(!current())return false
      setIfChanged('detail', detail, d)
      setIfChanged('players', players, c.players||[])
      setIfChanged('log', log, l.log||[])
      // 展示层附加信息：揭示标记与揭示方式（自动/点击）。
      checkReveals.value = l.check_reveals || {}
      diceRevealMode.value = l.dice_reveal_mode === 'click' ? 'click' : 'auto'
      setIfChanged('privateMessages', privateMessages, p.messages||p.private_log||[])
      setIfChanged('tableTalk', tableTalk, tt.exchanges||[])
      const nextMap = m || { locations: [] }
      const nextMapSignature = signature(nextMap)
      if (signatures.map !== nextMapSignature) {
        const resolvedMap = await resolveMapBackgroundAsset(nextMap)
        if (!current()) {
          revokeMapBackgroundAsset(resolvedMap)
          return false
        }
        revokeMapBackgroundAsset(map.value)
        signatures.map = nextMapSignature
        map.value = resolvedMap
      }
      if(d.world_id && isGm.value){
        try{
          const lb=await api<LorebookResponse>(`/lorebook/${encodeURIComponent(d.world_id)}`)
          if(!current())return false
          const entries = lb.entries||[]
          setIfChanged('loreEntries', loreEntries, entries)
          lore.value=buildLore(entries)
        }catch{
          if (!current()) return false
          setIfChanged('loreEntries', loreEntries, [])
          setIfChanged('lore', lore, {})
        }
      }
      else {
        setIfChanged('loreEntries', loreEntries, [])
        setIfChanged('lore', lore, {})
      }
      error.value=''
      return true
    }catch(e:unknown){
      if (!current()) return false
      if (isNotFoundError(e)) {
        clearMissingGame(gameKey)
        return false
      }
      if (isPlayerAccessClosedError(e)) {
        if (currentGame.value === gameKey) closeForPlayerAccess()
        return
      }
      if(!silent || !detail.value) error.value=errorMessage(e)
      return false
    }finally{
      activeRefreshes--
      if(current())loading.value=false
      if(!activeRefreshes && refreshAfterCurrent && !accessClosed.value){
        refreshAfterCurrent=false
        scheduleSilentRefresh()
      }
    }
  }

  async function connect(){
    const version=++connectVersion
    source?.close(); source=null; clearRefreshTimer(); liveNarration.value=''
    liveStory.value = null
    unsubscribePeerEvents?.(); unsubscribePeerEvents=null
    if(reconnectTimer){clearTimeout(reconnectTimer);reconnectTimer=undefined}
    if(pollTimer){clearInterval(pollTimer);pollTimer=undefined}
    const gameKey=currentGame.value
    if(!gameKey || accessClosed.value)return
    const peerGame = activePeerGameClient()
    if (peerGame?.gameKey === gameKey) {
      unsubscribePeerEvents = peerGame.subscribe(scheduleSilentRefresh)
      pollTimer=window.setInterval(() => void refresh(true),30000)
      return
    }
    // shortcut: Quick Tunnels lack SSE; poll until the endpoint supports streaming.
    if (window.location.hostname.endsWith('.trycloudflare.com')) {
      pollTimer = window.setInterval(() => {
        if (!activeRefreshes) void refresh(true)
      }, 2000)
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
        if(effect==='narration-reset'){liveNarration.value='';liveStory.value=null;return}
        if(effect==='story-update'){
          const story = (payload as GameSsePayload & { story?: unknown }).story
          if (isStorySequence(story)) liveStory.value = story
          return
        }
        if(payload?.type === 'ruleset_state_changed') {
          rulesetStateSignal.value = Math.max(
            rulesetStateSignal.value + 1,
            Number(payload.state_version || 0),
          )
        }
        scheduleSilentRefresh()
      }
      next.addEventListener('revoked', (ev: MessageEvent) => {
        if (source !== next) return
        let payload: { error_code?: string } | null = null
        try { payload = JSON.parse(ev.data) } catch { payload = null }
        // Other revocations reconnect as usual: the next ticket request is
        // refused and the client sends the player to re-join or log in.
        if (payload?.error_code === 'PLAYER_ACCESS_CLOSED') closeForPlayerAccess()
      })
      source.onerror=()=>{
        if(source!==next)return
        source?.close(); source=null
        if(!pollTimer)pollTimer=window.setInterval(() => void refresh(true),30000)
        if(!reconnectTimer)reconnectTimer=window.setTimeout(()=>{reconnectTimer=undefined;void connect()},5000)
      }
    }catch(e:unknown){
      if(version!==connectVersion)return
      if(isPlayerAccessClosedError(e)){
        if(currentGame.value===gameKey)closeForPlayerAccess()
        return
      }
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
    accessClosed.value = false
    await refresh(true)
    await connect()
  })
  // 新回合写入 log 时清掉流式气泡。第一页满员后长度不再增长（旧条目被挤到下一页），
  // 须按最新条目的 round 判断，否则“GM 思考中”在正式输出落地后残留。
  watch(() => log.value[log.value.length - 1]?.round, (next, prev) => { if (next !== prev) { liveNarration.value = ''; liveStory.value = null } })
  onBeforeUnmount(()=>{connectVersion++;refreshEpoch++;refreshAfterCurrent=false;source?.close();unsubscribePeerEvents?.();revokeMapBackgroundAsset(map.value);clearRefreshTimer();if(pollTimer)clearInterval(pollTimer);if(reconnectTimer)clearTimeout(reconnectTimer)})
  return {currentGame,userId,actorId,detail,players,player,log,privateMessages,tableTalk,map,lore,loreEntries,loading,error,accessClosed,isGm,refresh,connect,retryAccess,selectGame,liveNarration,liveStory,rulesetStateSignal,checkReveals,diceRevealMode}
}
