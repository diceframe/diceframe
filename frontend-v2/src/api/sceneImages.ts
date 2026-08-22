import { api, apiBlob } from '@/api/client'
import type { MapBackgroundSelection, SceneImageRef, SceneGalleryItem } from '@/api/types'
import { ruleSceneUrl } from '@/composables/useBackgroundImages'

export const MAX_SCENE_IMAGE_BYTES = 8 * 1024 * 1024
export const SCENE_IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp'

export interface SceneImageUploadResponse {
  ok: boolean
  error?: string
  scene_image?: SceneImageRef
}

function encodeAssetPath(path: string): string {
  return path.split('/').filter(Boolean).map(encodeURIComponent).join('/')
}

export function validateSceneImageFile(file: File): void {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('unsupported-scene-image-type')
  if (file.size > MAX_SCENE_IMAGE_BYTES) throw new Error('scene-image-too-large')
}

export async function fileToBase64(file: File): Promise<string> {
  validateSceneImageFile(file)
  const buffer = await file.arrayBuffer()
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

export async function uploadSceneImage(file: File): Promise<SceneImageRef> {
  const result = await api<SceneImageUploadResponse>('/scene-images', {
    method: 'POST',
    body: JSON.stringify({ file_data: await fileToBase64(file), file_name: file.name }),
  })
  if (!result.ok || !result.scene_image) throw new Error(result.error || 'scene-image-upload-failed')
  return result.scene_image
}

export async function resolveSceneImageUrl(reference: SceneImageRef | undefined, fallbackRuleId = ''): Promise<string> {
  if (!reference) return ruleSceneUrl(fallbackRuleId)
  if (reference.kind === 'builtin') return ruleSceneUrl(reference.id || fallbackRuleId)
  let path = ''
  if (reference.kind === 'upload' && reference.asset_id) {
    path = `/scene-images/${encodeURIComponent(reference.asset_id)}`
  } else if (reference.kind === 'plugin' && reference.plugin_id && reference.path) {
    path = `/plugins/assets/${encodeURIComponent(reference.plugin_id)}/${encodeAssetPath(reference.path)}`
  }
  if (!path) return ruleSceneUrl(fallbackRuleId)
  const response = await apiBlob(path)
  return URL.createObjectURL(await response.blob())
}

export async function resolveGameSceneImageUrl(gameKey: string, fallbackRuleId = '', useDefault = false): Promise<string> {
  try {
    const response = await apiBlob(`/games/${encodeURIComponent(gameKey)}/scene-image${useDefault ? '?default=1' : ''}`)
    return URL.createObjectURL(await response.blob())
  } catch {
    return ruleSceneUrl(fallbackRuleId)
  }
}

export function sceneImageStyle(url: string): Record<string, string> {
  return { '--df-bg-scene-image': `url("${url.replace(/"/g, '%22')}")` }
}

export function revokeSceneImageUrl(url: string): void {
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
}

// --- 自动生成场景图（imagegen provider 插件） ---

interface SceneGalleryResponse {
  images?: SceneGalleryItem[]
}

export async function fetchSceneGallery(gameKey: string): Promise<SceneGalleryItem[]> {
  const result = await api<SceneGalleryResponse>(
    `/games/${encodeURIComponent(gameKey)}/images`,
  )
  return result.images || []
}

interface MapBackgroundFromSceneResponse {
  ok?: boolean
  error?: string
  map_background?: MapBackgroundSelection
}

export async function setMapBackgroundFromScene(gameKey: string, assetId: string): Promise<void> {
  const result = await api<MapBackgroundFromSceneResponse>(
    `/games/${encodeURIComponent(gameKey)}/map-background-from-scene`,
    { method: 'POST', body: JSON.stringify({ asset_id: assetId }) },
  )
  if (!result.ok) throw new Error(result.error || 'map-background-update-failed')
}

export interface ImagegenTestResponse {
  ok?: boolean
  error?: string
  asset_id?: string
  revised_prompt?: string
}

export async function testImageGeneration(prompt: string): Promise<ImagegenTestResponse> {
  return api<ImagegenTestResponse>('/imagegen/test', {
    method: 'POST',
    body: JSON.stringify({ prompt }),
  })
}
