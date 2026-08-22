import { api, apiBlob } from '@/api/client'
import type { GeneratedImagePurpose, GeneratedImageRecord } from '@/api/types'

export interface ImageGenerationStatus {
  enabled: boolean
  available: boolean
  provider: string
  model: string
  auto_scene: boolean
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
