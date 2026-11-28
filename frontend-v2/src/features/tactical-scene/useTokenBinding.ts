import { ref, type Ref } from 'vue'
import { ApiError, api, errorMessage } from '@/api/client'
import { fetchRulesetAvailableActions } from '@/api/rulesets'
import type { CharacterListResponse, Player, RulesetGameplayView } from '@/api/types'
import {
  updateTacticalScenePlaceable,
  type TacticalSceneContentRef,
  type TacticalSceneTokenDocument,
  type TacticalSceneWriteResponse,
} from '@/api/tacticalScene'

// GM token→actor binding (frontend-05). The grid combat authority
// (ruleset_gameplay._token_actor_id) binds a token whose actor_ref carries
// kind player/companion/enemy plus an id. FVTT imports carry kind="actor" and
// never match, so the GM binds them here through placeable_update.
// Frozen contract: docs/internal/FVTT_COMPAT_WORKPLAN.md §6.15 / §10.3 F3.

export type TokenBindKind = 'player' | 'enemy' | 'companion'

export interface TokenBindTarget {
  kind: TokenBindKind
  id: string
  label: string
}

const STALE_CODE = 'tactical_scene_stale_revision'
const BIND_KINDS: ReadonlySet<string> = new Set(['player', 'enemy', 'companion'])

// The reference shape the backend validates (contract.py _content_ref) and the
// grid start reads. source_kind must match ^[a-z][a-z0-9_-]{0,31}$; the game key
// identifies the source, digest stays empty (no content digest is resolved).
export function diceframeActorRef(gameKey: string, kind: TokenBindKind, id: string): TacticalSceneContentRef {
  return { source_kind: 'diceframe', source_id: gameKey, kind, id, digest: '' }
}

function targetKey(kind: string, id: string): string {
  return `${kind}:${id}`
}

export function useTokenBinding(gameKey: Ref<string>) {
  const targets = ref<TokenBindTarget[]>([])
  const loading = ref(false)
  const loadError = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const conflict = ref(false)

  // Participants come from real authority data, never from token names:
  // - players: the game's character roster (user_id / character_name);
  // - enemies: the GM encounter preset catalog (enemies[].id / name);
  // - companions and live combatants: combat.actors[] actor_id "kind:id".
  async function loadTargets(): Promise<void> {
    const key = gameKey.value
    if (!key) return
    loading.value = true
    loadError.value = ''
    const merged = new Map<string, TokenBindTarget>()
    const add = (kind: TokenBindKind, id: string, label: string) => {
      if (!id) return
      const mapKey = targetKey(kind, id)
      if (!merged.has(mapKey)) merged.set(mapKey, { kind, id, label: label || id })
    }
    try {
      const [characters, actions] = await Promise.all([
        api<CharacterListResponse>(`/games/${encodeURIComponent(key)}/characters`),
        fetchRulesetAvailableActions(key).catch(() => null),
      ])
      for (const player of (characters.players || []) as Player[]) {
        if (player?.user_id) add('player', player.user_id, player.character_name || player.user_id)
      }
      const gameplay: RulesetGameplayView | null = actions?.gameplay || null
      if (gameplay) {
        for (const preset of gameplay.encounter_presets || []) {
          for (const enemy of preset.enemies || []) {
            const id = typeof enemy.id === 'string' ? enemy.id : ''
            const label = typeof enemy.name === 'string' ? enemy.name : ''
            if (id) add('enemy', id, label)
          }
        }
        for (const actor of gameplay.combat?.actors || []) {
          const actorId = typeof actor.actor_id === 'string' ? actor.actor_id : ''
          const split = actorId.indexOf(':')
          if (split <= 0) continue
          const kind = actorId.slice(0, split)
          const id = actorId.slice(split + 1)
          if (BIND_KINDS.has(kind) && id) {
            add(kind as TokenBindKind, id, typeof actor.name === 'string' ? actor.name : '')
          }
        }
      }
      targets.value = [...merged.values()]
    } catch (cause) {
      loadError.value = errorMessage(cause)
      targets.value = []
    } finally {
      loading.value = false
    }
  }

  async function write(
    sceneId: string,
    token: TacticalSceneTokenDocument,
    actorRef: TacticalSceneContentRef | null,
    revision: number,
  ): Promise<TacticalSceneWriteResponse> {
    saving.value = true
    saveError.value = ''
    conflict.value = false
    try {
      // placeable_update validates the whole token document, so send the full
      // projection with only actor_ref changed; canonical_id stays immutable.
      const next: TacticalSceneTokenDocument = { ...token, actor_ref: actorRef }
      return await updateTacticalScenePlaceable(
        gameKey.value, sceneId, 'tokens', token.canonical_id, next, revision,
      )
    } catch (cause) {
      const code = cause instanceof ApiError ? cause.code || '' : ''
      conflict.value = code === STALE_CODE
      saveError.value = errorMessage(cause)
      throw cause
    } finally {
      saving.value = false
    }
  }

  function bind(sceneId: string, token: TacticalSceneTokenDocument, kind: TokenBindKind, id: string, revision: number) {
    return write(sceneId, token, diceframeActorRef(gameKey.value, kind, id), revision)
  }

  function unbind(sceneId: string, token: TacticalSceneTokenDocument, revision: number) {
    return write(sceneId, token, null, revision)
  }

  return { targets, loading, loadError, saving, saveError, conflict, loadTargets, bind, unbind }
}