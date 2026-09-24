import { describe, expect, it, vi } from 'vitest'
import {
  PeerHostGameBridge,
  PeerRemoteGameClient,
  type PeerLocalApiExecutor,
} from '@/peer/game/bridge'
import type { MultiPeerConnectionSession } from '@/peer/session/MultiPeerConnectionSession'

describe('peer host game bridge', () => {
  const lobbyMultiplayer = {
    state: 'active_action', round_number: 3, solo_mode: false,
    player_count: 5, max_players: 6, ready_count: 1, alive_count: 5,
    active_count: 4, away_count: 1, ai_count: 1, unclaimed_count: 1,
    can_accept_actions: true, can_advance: true, action_count: 1,
    pending_action_count: 0, player_access_open: true,
  }
  const privatePlayer = { user_id: 'p1-secret', character_name: 'Character-secret' }
  const privateAction = {
    ...privatePlayer, text: 'Secret submitted action',
    check_request: { skill: 'Secret check payload' },
  }
  const hostDetail = {
    game_key: 'web|game|host', player_access_open: true, player_count: 5, max_players: 6,
    has_room_password: true, world_name: 'World', scene: 'Gate', rule_id: 'freeform',
    solo_mode: false,
    multiplayer: {
      ...lobbyMultiplayer, gm_uid: 'gm-secret',
      ready_players: [privatePlayer], waiting_players: [privatePlayer],
      away_players: [privatePlayer], ai_players: [privatePlayer],
      unclaimed_players: [privatePlayer], submitted_actions: [privateAction],
      away_control_policy: 'pause', future_private: 'Future-secret',
    },
    players: [privatePlayer], npcs: [{ character_name: 'NPC-secret' }],
    gm_style_override: { note: 'GM-secret-note' }, economy_proposals: ['Economy-secret'],
    plot_tracker: { note: 'Plot-secret' }, future_private: 'Future-secret',
  }
  const privateValues = [
    'gm-secret', 'p1-secret', 'Character-secret', 'NPC-secret',
    'Secret submitted action', 'Secret check payload', 'check_request',
    'submitted_actions', 'ready_players', 'waiting_players', 'away_players',
    'ai_players', 'unclaimed_players', 'gm_uid', 'GM-secret-note',
    'Economy-secret', 'Plot-secret', 'Future-secret',
  ]

  it('L4 returns the bound actor detail fetched with delegated identity', async () => {
    const current = { game_key: 'web|game|host', player_access_open: true, gm_style_override: { note: 'secret' } }
    const projected = { game_key: 'web|game|host', gm_style_override: null, scene: 'player scene' }
    const executor = vi.fn<PeerLocalApiExecutor>(async (path) => (
      path === '/games/web%7Cgame%7Chost' ? current : projected
    ))
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined, {}, { peer_1: 'player_1' })
    const result = await bridge.handle('peer_1', 'game.detail', {})
    expect(executor.mock.calls.map(([path]) => path)).toEqual([
      '/games/web%7Cgame%7Chost',
      '/games/web%7Cgame%7Chost?user=player_1&share=1&delegate=1',
    ])
    expect(result).toEqual({ ...projected, has_room_password: false, peer_transport: true })
  })

  it('L4 returns only lobby fields for an unbound peer', async () => {
    const executor = vi.fn<PeerLocalApiExecutor>(async () => hostDetail)
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)
    const result = await bridge.handle('peer_1', 'game.detail', {})
    expect(result).toEqual({
      game_key: 'web|game|host', player_access_open: true, player_count: 5, max_players: 6,
      has_room_password: false, world_name: 'World', scene: 'Gate', rule_id: 'freeform',
      solo_mode: false, multiplayer: lobbyMultiplayer, peer_transport: true,
    })
    for (const value of privateValues) expect(JSON.stringify(result)).not.toContain(value)
    expect(executor.mock.calls).toEqual([['/games/web%7Cgame%7Chost', undefined]])
  })

  it('projects only ruleset bootstrap fields to an unbound joiner, without host identities', async () => {
    const characters = {
      players: [privatePlayer], npcs: [{ character_name: 'NPC-secret' }],
      rule_attrs: [{ key: 'str', name: '力量', min: 1, max: 20 }],
      rule_attrs_total: 27, rule_classes: ['战士'], rule_special_stats: [],
      rule_meta: { rule_id: 'dnd2024_srd' },
      ruleset_runtime: { capabilities: { character_builder: 'professional' } },
      user_id: 'gm-secret', cards: [{ character_name: 'Card-secret' }],
      actions: [privateAction],
    }
    const executor = vi.fn<PeerLocalApiExecutor>(async (path) => (
      path === '/games/web%7Cgame%7Chost' ? hostDetail : characters
    ))
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)
    const result = await bridge.handle('peer_1', 'game.characters', {})
    expect(result).toEqual({
      players: [], npcs: [],
      rule_attrs: characters.rule_attrs, rule_attrs_total: characters.rule_attrs_total,
      rule_classes: characters.rule_classes, rule_special_stats: characters.rule_special_stats,
      rule_meta: characters.rule_meta, ruleset_runtime: characters.ruleset_runtime,
    })
    const serialized = JSON.stringify(result)
    expect(serialized).not.toContain('user_id')
    for (const name of ['Character-secret', 'NPC-secret', 'Card-secret']) {
      expect(serialized).not.toContain(name)
    }
    expect(executor.mock.calls).toEqual([
      ['/games/web%7Cgame%7Chost', undefined],
      ['/games/web%7Cgame%7Chost/characters?share=1', undefined],
    ])
  })

  it.each(['roll.requests', 'game.log'] as const)(
    'rejects unbound %s without fetching GM-view data', async (operation) => {
      const executor = vi.fn<PeerLocalApiExecutor>(async (path) => (
        path === '/games/web%7Cgame%7Chost'
          ? hostDetail
          : { requests: [privateAction], log: [privateAction], total: 1 }
      ))
      const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)
      await expect(bridge.handle('peer_1', operation, {}))
        .rejects.toThrow(/^player_identity_required$/u)
      expect(executor.mock.calls).toEqual([['/games/web%7Cgame%7Chost', undefined]])
    },
  )

  it.each([
    ['game.characters', '/characters', { players: [privatePlayer], npcs: [] }],
    ['roll.requests', '/roll-requests', { requests: [privateAction] }],
    ['game.log', '/log?page=2&per_page=10', { log: [privateAction], total: 1 }],
  ] as const)('keeps bound %s delegated and preserves its response', async (operation, suffix, projected) => {
    const executor = vi.fn<PeerLocalApiExecutor>(async (path) => (
      path === '/games/web%7Cgame%7Chost' ? hostDetail : projected
    ))
    const bridge = new PeerHostGameBridge(
      'web|game|host', executor, () => undefined, {}, { peer_1: 'player_1' },
    )
    expect(await bridge.handle('peer_1', operation, { page: 2, per_page: 10 })).toEqual(projected)
    expect(executor.mock.calls.map(([path]) => path)).toEqual([
      '/games/web%7Cgame%7Chost',
      `/games/web%7Cgame%7Chost${suffix}${suffix.includes('?') ? '&' : '?'}user=player_1&share=1&delegate=1`,
    ])
  })

  it('resolves a roll for the bound actor and ignores forged target fields', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') return { game_key: 'web|game|host', player_access_open: true }
      return { ok: true }
    }
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined, {}, { peer_1: 'player_1' })
    await bridge.handle('peer_1', 'roll.resolve', { request_id: 'req_1', run_id: 'run_1', target_uid: 'forged' })
    expect(calls[1].path).toBe('/games/web%7Cgame%7Chost/roll-requests/req_1/roll?user=player_1&share=1&delegate=1')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ run_id: 'run_1', target_uid: 'player_1' })
  })

  it('maps allowlisted operations to a delegated player identity', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') {
        return {
          game_key: 'web|game|host',
          player_access_open: true,
          player_count: 1,
          max_players: 6,
        }
      }
      if (path === '/games/web%7Cgame%7Chost/players') {
        return { ok: true, user_id: 'player_123' }
      }
      return { ok: true }
    }
    const changed = vi.fn()
    const bridge = new PeerHostGameBridge('web|game|host', executor, changed)

    await bridge.handle('p_abcdefghijk', 'player.create', {
      character_name: '调查员',
      user_id: 'attempted-impersonation',
      path: '/api/config',
    })
    await bridge.handle('p_abcdefghijk', 'action.submit', { text: '调查房间' })
    await bridge.handle('p_abcdefghijk', 'kp.question', {
      question: '我认识墙上的符号吗？',
      visibility: 'party',
      action: 'forged',
    })
    await bridge.handle('p_abcdefghijk', 'ruleset.intent', {
      intent_id: 'intent-1', type: 'attack', expected_version: 2,
      actor_id: 'player:someone-else', target_id: 'enemy:goblin',
      weapon_ref: 'item:longsword', submitted_by: 'forged', damage: 9999,
    })

    const createBody = JSON.parse(String(calls[1].init?.body))
    expect(createBody.user_id).toBeUndefined()
    expect(createBody.join_as_new).toBe(true)
    expect(createBody.path).toBeUndefined()
    expect(createBody.character_name).toBe('调查员')
    expect(calls[3].path).toBe(
      '/games/web%7Cgame%7Chost/action?user=player_123&share=1&delegate=1',
    )
    const actionBody = JSON.parse(String(calls[3].init?.body))
    expect(actionBody).toEqual({ text: '调查房间' })
    expect(calls[5].path).toBe(
      '/games/web%7Cgame%7Chost/kp-question?user=player_123&share=1&delegate=1',
    )
    const questionBody = JSON.parse(String(calls[5].init?.body))
    expect(questionBody).toEqual({ question: '我认识墙上的符号吗？', visibility: 'party' })
    expect(calls[7].path).toBe(
      '/games/web%7Cgame%7Chost/intents?user=player_123&share=1&delegate=1',
    )
    const intentBody = JSON.parse(String(calls[7].init?.body))
    expect(intentBody).toEqual({
      intent_id: 'intent-1', type: 'attack', expected_version: 2,
      actor_id: 'player:someone-else', target_id: 'enemy:goblin',
      weapon_ref: 'item:longsword',
    })
    expect(changed).toHaveBeenCalledTimes(4)
  })

  it('strips non-whitelisted fields from peer payloads', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') {
        return { game_key: 'web|game|host', player_access_open: true }
      }
      if (path === '/games/web%7Cgame%7Chost/players') {
        return { ok: true, user_id: 'player_123' }
      }
      return { ok: true }
    }
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)

    await bridge.handle('p_abcdefghijk', 'player.create', {
      character_name: '调查员',
      user_id: 'attempted-impersonation',
      path: '/api/config',
      role: 'gm',
    })
    const createBody = JSON.parse(String(calls[1].init?.body))
    expect(createBody).toEqual({
      character_name: '调查员',
      join_as_new: true,
    })

    await bridge.handle('p_abcdefghijk', 'luck.resolve', {
      check_id: 'check-1',
      spend: true,
      user_id: 'someone-else',
    })
    expect(calls[3].path).toBe(
      '/games/web%7Cgame%7Chost/checks/check-1/luck?user=player_123&share=1&delegate=1',
    )
    const luckBody = JSON.parse(String(calls[3].init?.body))
    expect(luckBody).toEqual({ spend: true })

    // 职业特性能力 intent：只放行 capability id 与目标，伪造的成本字段被剥离，
    // 与 Web 端一样仍由服务端 capability 声明决定实际消耗。
    await bridge.handle('p_abcdefghijk', 'ruleset.intent', {
      intent_id: 'intent-monk-1', type: 'class_capability', expected_version: 4,
      actor_id: 'player:someone', target_id: 'enemy:goblin',
      capability_id: 'flurry_of_blows', costs: [], focus_points: 99,
    })
    const capabilityCall = calls[calls.length - 1]
    expect(capabilityCall.path).toBe(
      '/games/web%7Cgame%7Chost/intents?user=player_123&share=1&delegate=1',
    )
    expect(JSON.parse(String(capabilityCall.init?.body))).toEqual({
      intent_id: 'intent-monk-1', type: 'class_capability', expected_version: 4,
      actor_id: 'player:someone', target_id: 'enemy:goblin',
      capability_id: 'flurry_of_blows',
    })
  })

  it('forwards the canonical professional character for authoritative host validation', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') {
        return {
          game_key: 'web|game|host',
          player_access_open: true,
          player_count: 1,
          max_players: 2,
        }
      }
      if (path === '/games/web%7Cgame%7Chost/players') {
        return { ok: true, user_id: 'player_123' }
      }
      return { ok: true }
    }
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)
    const canonical = {
      rule_binding: {
        rule_id: 'dnd2024_srd',
        runtime_id: 'core:dnd2024',
        runtime_version: 1,
      },
      identity: { name: '守护者' },
      build: { level: 1 },
      derived: { armor_class: 999 },
    }

    await bridge.handle('p_abcdefghijk', 'player.create', {
      character_name: '守护者',
      ruleset_character: canonical,
      user_id: 'attempted-impersonation',
      role: 'gm',
    })

    const createBody = JSON.parse(String(calls[1].init?.body))
    expect(createBody.ruleset_character).toEqual(canonical)
    expect(createBody.user_id).toBeUndefined()
    expect(createBody.role).toBeUndefined()
    expect(createBody.join_as_new).toBe(true)
  })

  it('keeps professional character operations bound to the peer actor', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') {
        return { game_key: 'web|game|host', player_access_open: true }
      }
      return { ok: true }
    }
    const changed = vi.fn()
    const bridge = new PeerHostGameBridge(
      'web|game|host', executor, changed, {}, { p_abcdefghijk: 'player_bound' },
    )

    await bridge.handle('p_abcdefghijk', 'character.profile', {
      character_name: '守护者', profile: { backstory: '守住城门' }, user_id: 'player_forged',
    })
    await bridge.handle('p_abcdefghijk', 'character.rest', {
      rest: 'short', hit_dice: { d10: 1 }, expected_revision: 3, operation_id: 'rest-1',
      user_id: 'player_forged',
    })

    expect(calls[1].path).toBe(
      '/games/web%7Cgame%7Chost/character/player_bound/profile?user=player_bound&share=1&delegate=1',
    )
    expect(calls[1].init?.method).toBe('PATCH')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      character_name: '守护者', profile: { backstory: '守住城门' },
    })
    expect(calls[3].path).toBe(
      '/games/web%7Cgame%7Chost/character/player_bound/rest?user=player_bound&share=1&delegate=1',
    )
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({
      rest: 'short', hit_dice: { d10: 1 }, expected_revision: 3, operation_id: 'rest-1',
    })
    expect(changed).toHaveBeenCalledTimes(2)
  })

  it('allows only player-side Session 0 and tutorial intent fields', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const executor: PeerLocalApiExecutor = async (path, init) => {
      calls.push({ path, init })
      if (path === '/games/web%7Cgame%7Chost') return { game_key: 'web|game|host', player_access_open: true }
      if (path === '/games/web%7Cgame%7Chost/players') return { ok: true, user_id: 'player_123' }
      return { ok: true }
    }
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)
    await bridge.handle('p_abcdefghijk', 'player.create', { character_name: 'Guide' })
    await bridge.handle('p_abcdefghijk', 'ruleset.intent', {
      intent_id: 'session-1', type: 'session_zero.respond', expected_version: 3,
      response: 'accept', comment: 'Looks good', choice_id: 'forged',
      agreement: { difficulty: 'lethal' }, submitted_by: 'gm',
    })

    const body = JSON.parse(String(calls[3].init?.body))
    expect(body).toEqual({
      intent_id: 'session-1', type: 'session_zero.respond', expected_version: 3,
      response: 'accept', comment: 'Looks good', choice_id: 'forged',
    })
    expect(body.agreement).toBeUndefined()
    expect(body.submitted_by).toBeUndefined()
  })

  it('stops all player operations when the local game owner closes access', async () => {
    const executor: PeerLocalApiExecutor = async () => ({
      game_key: 'web|game|host',
      player_access_open: false,
    })
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)

    await expect(bridge.handle('p_abcdefghijk', 'game.detail', {}))
      .rejects.toThrow('player_access_closed')
  })
})

describe('peer remote game client', () => {
  it('translates only known game routes into semantic operations', async () => {
    const requestGame = vi.fn(async (_peerId, operation, payload) => {
      if (operation === 'player.create') return { ok: true, user_id: 'player_456' }
      return { ok: true, operation, payload }
    })
    const session = { requestGame } as unknown as MultiPeerConnectionSession
    const client = new PeerRemoteGameClient(
      session,
      'h_abcdefghijk',
      'web|game|host',
    )

    const result = await client.tryApi<{ user_id: string }>(
      '/games/web%7Cgame%7Chost/players',
      { method: 'POST', body: JSON.stringify({ character_name: '调查员' }) },
    )

    expect(result.value?.user_id).toBe('player_456')
    expect(client.userId).toBe('player_456')
    expect(requestGame).toHaveBeenCalledWith(
      'h_abcdefghijk',
      'player.create',
      { character_name: '调查员' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/available-actions')
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'ruleset.actions', {},
    )
    await client.tryApi('/games/web%7Cgame%7Chost/intents', {
      method: 'POST', body: JSON.stringify({ intent_id: 'i-1', type: 'end_turn' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'ruleset.intent', { intent_id: 'i-1', type: 'end_turn' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/decisions/decision-1', {
      method: 'POST', body: JSON.stringify({ intent_id: 'i-2', option: 'accept' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'ruleset.decision',
      { intent_id: 'i-2', option: 'accept', decision_id: 'decision-1' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/character/player_forged/profile', {
      method: 'PATCH', body: JSON.stringify({ character_name: '守护者', profile: { notes: 'safe' } }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'character.profile',
      { character_name: '守护者', profile: { notes: 'safe' } },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/character/player_forged/rest', {
      method: 'POST', body: JSON.stringify({ rest: 'long', operation_id: 'rest-1' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'character.rest', { rest: 'long', operation_id: 'rest-1' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/character/player_forged/advancement/preview', {
      method: 'POST', body: JSON.stringify({ choices: { feat_ref: 'feat:alert' } }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'character.advancement.preview',
      { choices: { feat_ref: 'feat:alert' } },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/character/player_forged/advancement/apply', {
      method: 'POST', body: JSON.stringify({ choices: {}, expected_revision: 3, operation_id: 'advance-1' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'character.advancement.apply',
      { choices: {}, expected_revision: 3, operation_id: 'advance-1' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/kp-question', {
      method: 'POST', body: JSON.stringify({ question: 'What do I know?', visibility: 'party' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'kp.question', { question: 'What do I know?', visibility: 'party' },
    )
    await client.tryApi('/games/web%7Cgame%7Chost/table-talk')
    expect(requestGame).toHaveBeenLastCalledWith(
      'h_abcdefghijk', 'game.table_talk', {},
    )
    await client.tryApi('/games/web%7Cgame%7Chost/roll-requests')
    expect(requestGame).toHaveBeenLastCalledWith('h_abcdefghijk', 'roll.requests', {})
    await client.tryApi('/games/web%7Cgame%7Chost/roll-requests/r-1/roll', {
      method: 'POST', body: JSON.stringify({ run_id: 'run-1', target_uid: 'forged' }),
    })
    expect(requestGame).toHaveBeenLastCalledWith('h_abcdefghijk', 'roll.resolve', {
      run_id: 'run-1', target_uid: 'forged', request_id: 'r-1',
    })
    await expect(client.tryApi('/games/web%7Cgame%7Chost/export'))
      .rejects.toThrow('peer_game_operation_not_supported')
    expect(client.handlesGamePath('/games/web%7Cgame%7Chost/scene-image')).toBe(true)
    expect(client.handlesGamePath('/games/another-game/scene-image')).toBe(false)
    expect((await client.tryApi('/config')).handled).toBe(false)
  })

  it('rebinds a returning guest to its previous actor instead of creating a new character', async () => {
    localStorage.setItem('diceframe_peer_actor_web|game|host', 'player_789')
    try {
      const requestGame = vi.fn(async (_peerId, operation) => {
        if (operation === 'player.rebind') return { ok: true, user_id: 'player_789', rebound: true }
        throw new Error('unexpected_operation')
      })
      const session = { requestGame } as unknown as MultiPeerConnectionSession
      const client = new PeerRemoteGameClient(session, 'h_abcdefghijk', 'web|game|host')

      // 构造函数从 localStorage 恢复身份
      expect(client.userId).toBe('player_789')

      const ok = await client.rebindIdentity()
      expect(ok).toBe(true)
      expect(requestGame).toHaveBeenCalledWith(
        'h_abcdefghijk',
        'player.rebind',
        { user_id: 'player_789' },
      )
    } finally {
      localStorage.removeItem('diceframe_peer_actor_web|game|host')
    }
  })

  it('prefers the actor assigned by the invite over an unrelated local cache', async () => {
    localStorage.setItem('diceframe_peer_actor_web|game|host', 'player_old')
    try {
      const requestGame = vi.fn(async () => ({
        ok: true,
        user_id: 'player_assigned',
        rebound: true,
      }))
      const session = { requestGame } as unknown as MultiPeerConnectionSession
      const client = new PeerRemoteGameClient(
        session,
        'h_abcdefghijk',
        'web|game|host',
        'player_assigned',
      )

      expect(client.userId).toBe('player_assigned')
      await expect(client.rebindIdentity()).resolves.toBe(true)
      expect(requestGame).toHaveBeenCalledWith(
        'h_abcdefghijk',
        'player.rebind',
        { user_id: 'player_assigned' },
      )
    } finally {
      localStorage.removeItem('diceframe_peer_actor_web|game|host')
    }
  })

  it('reports rebind failure without throwing when the identity is gone', async () => {
    localStorage.setItem('diceframe_peer_actor_web|game|host', 'player_dead')
    try {
      const requestGame = vi.fn(async () => {
        throw new Error('player_identity_unknown')
      })
      const session = { requestGame } as unknown as MultiPeerConnectionSession
      const client = new PeerRemoteGameClient(session, 'h_abcdefghijk', 'web|game|host')

      await expect(client.rebindIdentity()).resolves.toBe(false)
    } finally {
      localStorage.removeItem('diceframe_peer_actor_web|game|host')
    }
  })
})

describe('peer host bridge player.rebind', () => {
  it('restores the identity map after a guest refresh and rejects unknown actors', async () => {
    const players = [{ user_id: 'player_789', character_name: '调查员' }]
    const executor: PeerLocalApiExecutor = async (path) => {
      if (path.startsWith('/games/web%7Cgame%7Chost')) {
        return {
          game_key: 'web|game|host',
          player_access_open: true,
          players,
        }
      }
      return { ok: true }
    }
    const bridge = new PeerHostGameBridge(
      'web|game|host',
      executor,
      () => undefined,
      { p_abcdefghijk: 'player_789' },
    )

    // 邀请未指定的身份：拒绝，不能拿普通邀请码枚举并冒充角色。
    await expect(bridge.handle('p_abcdefghijk', 'player.rebind', { user_id: 'player_ghost' }))
      .rejects.toThrow('player_identity_not_assigned')

    // 已知身份：恢复映射，后续操作直接以该身份执行
    const result = await bridge.handle('p_abcdefghijk', 'player.rebind', { user_id: 'player_789' })
    expect(result).toMatchObject({ ok: true, user_id: 'player_789', rebound: true })

    await bridge.handle('p_abcdefghijk', 'game.player_context', {})
    const context = await bridge.handle('p_abcdefghijk', 'game.player_context', {})
    expect(context.user_id).toBe('player_789')
  })

  it('refuses to rebind an actor already claimed by a different peer', async () => {
    const players = [{ user_id: 'player_789', character_name: '调查员' }]
    const executor: PeerLocalApiExecutor = async () => ({
      game_key: 'web|game|host',
      player_access_open: true,
      players,
    })
    const bridge = new PeerHostGameBridge(
      'web|game|host',
      executor,
      () => undefined,
      {
        p_abcdefghijk: 'player_789',
        p_cdefghijklm: 'player_789',
      },
    )

    await bridge.handle('p_abcdefghijk', 'player.rebind', { user_id: 'player_789' })
    await expect(bridge.handle('p_cdefghijklm', 'player.rebind', { user_id: 'player_789' }))
      .rejects.toThrow('player_identity_taken')

    // 同一 peer 重复 rebind 幂等
    const again = await bridge.handle('p_abcdefghijk', 'player.rebind', { user_id: 'player_789' })
    expect(again.rebound).toBe(true)
  })

  it('does not let an unassigned new-player invite claim an occupied character', async () => {
    const executor: PeerLocalApiExecutor = async () => ({
      game_key: 'web|game|host',
      player_access_open: true,
      players: [{ user_id: 'player_789', character_name: '调查员' }],
    })
    const bridge = new PeerHostGameBridge('web|game|host', executor, () => undefined)

    await expect(bridge.handle('p_abcdefghijk', 'player.rebind', { user_id: 'player_789' }))
      .rejects.toThrow('player_identity_not_assigned')
  })
})
