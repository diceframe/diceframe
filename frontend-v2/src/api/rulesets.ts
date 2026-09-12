import { api } from '@/api/client'
import type {
  JsonObject,
  RulesetAdvancementApplyResponse,
  RulesetAdvancementPreviewResponse,
  RulesetBuilderCharacterResponse,
  RulesetBuilderChoicesResponse,
  RulesetBuilderValidationResponse,
  RulesetExperienceResponse,
  RulesetProgressionResponse,
  RulesetRestResponse,
  RulesetGameplayResponse,
  RulesetTemporaryEncounterResponse,
} from '@/api/types'

function rulePath(ruleId: string, suffix: string, language = ''): string {
  const query = language ? `?language=${encodeURIComponent(language)}` : ''
  return `/rules/${encodeURIComponent(ruleId)}/${suffix}${query}`
}

function postDraft<T>(
  ruleId: string,
  action: 'choices' | 'validate' | 'derive' | 'finalize',
  draft: JsonObject,
  language = '',
): Promise<T> {
  return api<T>(rulePath(ruleId, `builder/${action}`, language), {
    method: 'POST',
    body: JSON.stringify(draft),
  })
}

export function fetchRulesetExperience(
  ruleId: string,
  language = '',
): Promise<RulesetExperienceResponse> {
  return api<RulesetExperienceResponse>(rulePath(ruleId, 'experience', language))
}

export function fetchRulesetBuilderChoices(
  ruleId: string,
  draft: JsonObject,
  language = '',
): Promise<RulesetBuilderChoicesResponse> {
  return postDraft(ruleId, 'choices', draft, language)
}

export function validateRulesetBuilderDraft(
  ruleId: string,
  draft: JsonObject,
  language = '',
): Promise<RulesetBuilderValidationResponse> {
  return postDraft(ruleId, 'validate', draft, language)
}

export function deriveRulesetBuilderCharacter(
  ruleId: string,
  draft: JsonObject,
  language = '',
): Promise<RulesetBuilderCharacterResponse> {
  return postDraft(ruleId, 'derive', draft, language)
}

export function finalizeRulesetBuilderCharacter(
  ruleId: string,
  draft: JsonObject,
  language = '',
): Promise<RulesetBuilderCharacterResponse> {
  return postDraft(ruleId, 'finalize', draft, language)
}

export function fetchRulesetProgression(
  ruleId: string,
  classRef = '',
  language = '',
): Promise<RulesetProgressionResponse> {
  const query = new URLSearchParams()
  if (classRef) query.set('class_ref', classRef)
  if (language) query.set('language', language)
  const suffix = query.size ? `?${query.toString()}` : ''
  return api<RulesetProgressionResponse>(
    `/rules/${encodeURIComponent(ruleId)}/progression${suffix}`,
  )
}

export function previewRulesetAdvancement(
  ruleId: string,
  character: JsonObject,
  choices: JsonObject,
  language = '',
): Promise<RulesetAdvancementPreviewResponse> {
  return api<RulesetAdvancementPreviewResponse>(
    rulePath(ruleId, 'advancement/preview', language),
    { method: 'POST', body: JSON.stringify({ character, choices }) },
  )
}

export function applyRulesetAdvancement(
  ruleId: string,
  character: JsonObject,
  choices: JsonObject,
  language = '',
): Promise<RulesetAdvancementApplyResponse> {
  return api<RulesetAdvancementApplyResponse>(
    rulePath(ruleId, 'advancement/apply', language),
    { method: 'POST', body: JSON.stringify({ character, choices }) },
  )
}

export function previewCharacterCardAdvancement(
  cardId: string,
  choices: JsonObject,
): Promise<RulesetAdvancementPreviewResponse> {
  return api<RulesetAdvancementPreviewResponse>(
    `/character-cards/${encodeURIComponent(cardId)}/advancement/preview`,
    { method: 'POST', body: JSON.stringify({ choices }) },
  )
}

export function applyCharacterCardAdvancement(
  cardId: string,
  choices: JsonObject,
  expectedRevision: number,
  operationId: string,
): Promise<RulesetAdvancementApplyResponse> {
  return api<RulesetAdvancementApplyResponse>(
    `/character-cards/${encodeURIComponent(cardId)}/advancement/apply`,
    {
      method: 'POST',
      body: JSON.stringify({
        choices,
        expected_revision: expectedRevision,
        operation_id: operationId,
      }),
    },
  )
}

export function previewLiveCharacterAdvancement(
  gameKey: string,
  userId: string,
  choices: JsonObject,
): Promise<RulesetAdvancementPreviewResponse> {
  return api<RulesetAdvancementPreviewResponse>(
    `/games/${encodeURIComponent(gameKey)}/character/${encodeURIComponent(userId)}/advancement/preview`,
    { method: 'POST', body: JSON.stringify({ choices }) },
  )
}

export function applyLiveCharacterAdvancement(
  gameKey: string,
  userId: string,
  choices: JsonObject,
  expectedRevision: number,
  operationId: string,
): Promise<RulesetAdvancementApplyResponse> {
  return api<RulesetAdvancementApplyResponse>(
    `/games/${encodeURIComponent(gameKey)}/character/${encodeURIComponent(userId)}/advancement/apply`,
    {
      method: 'POST',
      body: JSON.stringify({
        choices,
        expected_revision: expectedRevision,
        operation_id: operationId,
      }),
    },
  )
}

export function resolveLiveCharacterRest(
  gameKey: string,
  userId: string,
  rest: 'short' | 'long',
  hitDice: Record<string, number>,
  expectedRevision: number,
  operationId: string,
): Promise<RulesetRestResponse> {
  return api<RulesetRestResponse>(
    `/games/${encodeURIComponent(gameKey)}/character/${encodeURIComponent(userId)}/rest`,
    {
      method: 'POST',
      body: JSON.stringify({
        rest,
        hit_dice: rest === 'short' ? hitDice : {},
        confirm_elapsed_time: true,
        expected_revision: expectedRevision,
        operation_id: operationId,
      }),
    },
  )
}

export function resolveRulesetRest(
  ruleId: string,
  character: JsonObject,
  rest: 'short' | 'long',
  hitDieRolls: Record<string, number[]> = {},
  language = '',
): Promise<RulesetRestResponse> {
  return api<RulesetRestResponse>(rulePath(ruleId, 'rest/resolve', language), {
    method: 'POST',
    body: JSON.stringify({ character, rest, hit_die_rolls: hitDieRolls }),
  })
}

export function fetchRulesetAvailableActions(
  gameKey: string,
): Promise<RulesetGameplayResponse> {
  return api<RulesetGameplayResponse>(
    `/games/${encodeURIComponent(gameKey)}/available-actions`,
  )
}

export function submitRulesetIntent(
  gameKey: string,
  intent: JsonObject,
): Promise<RulesetGameplayResponse> {
  return api<RulesetGameplayResponse>(
    `/games/${encodeURIComponent(gameKey)}/intents`,
    { method: 'POST', body: JSON.stringify(intent) },
  )
}

// AI 临时遭遇提案：只读生成，不改任何游戏状态；确认后走现有 /intents combat.start。
export function planRulesetTemporaryEncounter(
  gameKey: string,
): Promise<RulesetTemporaryEncounterResponse> {
  return api<RulesetTemporaryEncounterResponse>(
    `/games/${encodeURIComponent(gameKey)}/ruleset/temporary-encounter`,
    { method: 'POST', body: JSON.stringify({}) },
  )
}

export function resolveRulesetDecision(
  gameKey: string,
  decisionId: string,
  intent: JsonObject,
): Promise<RulesetGameplayResponse> {
  return api<RulesetGameplayResponse>(
    `/games/${encodeURIComponent(gameKey)}/decisions/${encodeURIComponent(decisionId)}`,
    { method: 'POST', body: JSON.stringify(intent) },
  )
}
