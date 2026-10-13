import { api, apiBlob } from '@/api/client'
import type { GeneratedImagePurpose, GeneratedImageRecord } from '@/api/types'

export interface ImageGenerationStatus {
  enabled: boolean
  available: boolean
  provider: string
  model: string
  auto_scene: boolean
  prompt_char_limit: number
}

export interface GenerateImageInput {
  purpose: GeneratedImagePurpose
  prompt: string
  gameKey?: string
  aspectRatio?: string
  style?: string
  context?: Record<string, unknown>
}

export interface GenerateImageResponse extends GeneratedImageRecord {
  ok?: boolean
  error?: string
  reference?: { kind: 'generated'; asset_id: string }
  prompt_budget?: {
    limit?: number
    used?: number
    adjusted?: boolean
    reduced_segments?: string[]
  }
}

export async function imageGenerationStatus(): Promise<ImageGenerationStatus> {
  return api<ImageGenerationStatus>('/image-generation')
}

export async function generateImage(input: GenerateImageInput): Promise<GenerateImageResponse> {
  const path = input.gameKey
    ? `/games/${encodeURIComponent(input.gameKey)}/generated-images`
    : '/generated-images'
  const result = await api<GenerateImageResponse>(path, {
    method: 'POST',
    body: JSON.stringify({
      purpose: input.purpose,
      prompt: input.prompt,
      aspect_ratio: input.aspectRatio || '',
      style: input.style || '',
      context: input.context || {},
    }),
  })
  if (!result.ok || !result.asset_id) throw new Error(result.error || 'image-generation-failed')
  return result
}

export async function generateCurrentRoundImage(gameKey: string, input: { prompt: string; round?: number; panels?: unknown[]; panelCount?: number; useAvatarReferences?: boolean; combineAvatarReferences?: boolean }): Promise<GenerateImageResponse> {
  const result = await api<GenerateImageResponse>(`/games/${encodeURIComponent(gameKey)}/generated-images/current-round`, {
    method: 'POST', body: JSON.stringify({ prompt: input.prompt, round: input.round || 0, panels: input.panels || [], panel_count: input.panelCount, use_avatar_references: !!input.useAvatarReferences, combine_avatar_references: input.combineAvatarReferences ?? true }),
  })
  if (!result.ok || !result.asset_id) throw new Error(result.error || 'image-generation-failed')
  return result
}

export async function fetchStoryboardDraft(gameKey: string, round = 0): Promise<{
  ok?: boolean
  round?: number
  panels?: unknown[]
  candidate?: { panels?: unknown[]; requested_panel_count?: number | null; compressed_count?: number; source_revision?: string } | null
  error?: string
}> {
  return api(`/games/${encodeURIComponent(gameKey)}/generated-images/storyboard?round=${round}`)
}

export async function analyzeStoryboard(gameKey: string, round = 0, panelCount?: number): Promise<{ ok?: boolean; round?: number; panels?: unknown[]; compressed_count?: number; requested_panel_count?: number | null; actual_panel_count?: number; error?: string }> {
  return api(`/games/${encodeURIComponent(gameKey)}/generated-images/storyboard/analyze`, { method: 'POST', body: JSON.stringify({ round, panel_count: panelCount }) })
}

export async function previewImagePrompt(gameKey: string, input: { prompt: string; panels?: unknown[] }): Promise<{ ok?: boolean; prompt?: string; prompt_budget?: Record<string, unknown>; error?: string }> {
  return api(`/games/${encodeURIComponent(gameKey)}/generated-images/prompt/preview`, { method: 'POST', body: JSON.stringify(input) })
}

function currentGameKey(): string {
  return new URLSearchParams(location.hash.split('?')[1] || '').get('game') || ''
}

export async function generatedImageUrl(assetId: string, gameKey = currentGameKey()): Promise<string> {
  const path = gameKey
    ? `/games/${encodeURIComponent(gameKey)}/generated-images/${encodeURIComponent(assetId)}`
    : `/generated-images/${encodeURIComponent(assetId)}`
  const response = await apiBlob(path)
  return URL.createObjectURL(await response.blob())
}

export async function fetchGeneratedImages(
  gameKey: string,
  purpose?: GeneratedImagePurpose,
): Promise<GeneratedImageRecord[]> {
  const query = purpose ? `?purpose=${encodeURIComponent(purpose)}` : ''
  const result = await api<{ images?: GeneratedImageRecord[] }>(
    `/games/${encodeURIComponent(gameKey)}/generated-images${query}`,
  )
  return result.images || []
}

export async function useGeneratedImageAsMapBackground(gameKey: string, assetId: string): Promise<void> {
  const result = await api<{ ok?: boolean; error?: string }>(
    `/games/${encodeURIComponent(gameKey)}/generated-images/${encodeURIComponent(assetId)}/map-background`,
    { method: 'POST', body: '{}' },
  )
  if (!result.ok) throw new Error(result.error || 'map-background-update-failed')
}
