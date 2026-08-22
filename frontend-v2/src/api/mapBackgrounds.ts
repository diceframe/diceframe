import { api, apiBlob } from '@/api/client'
import type { MapBackgroundSelection, MapData } from '@/api/types'

export const MAX_MAP_BACKGROUND_BYTES = 8 * 1024 * 1024
export const MAP_BACKGROUND_ACCEPT = 'image/jpeg,image/png,image/webp'

export const BUILTIN_MAP_BACKGROUNDS = [
  { id: 'fantasy-region-v1', url: '/v2-assets/ui/maps/fantasy-region-v1.webp' },
  { id: 'occult-town-v1', url: '/v2-assets/ui/maps/occult-town-v1.webp' },
  { id: 'cyber-city-v1', url: '/v2-assets/ui/maps/cyber-city-v1.webp' },
] as const

interface MapBackgroundMutationResponse {
  ok?: boolean
  error?: string
  map_background?: MapBackgroundSelection
}

export function validateMapBackgroundFile(file: File): void {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    throw new Error('unsupported-map-background-type')
  }
  if (file.size > MAX_MAP_BACKGROUND_BYTES) throw new Error('map-background-too-large')
}

export async function mapBackgroundFileToBase64(file: File): Promise<string> {
  validateMapBackgroundFile(file)
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

export function mapBackgroundChoice(selection?: MapBackgroundSelection | null): string {
  if (!selection || selection.kind === 'auto') return 'auto'
  if (selection.kind === 'none') return 'none'
  if (selection.kind === 'builtin') return `builtin:${selection.id || ''}`
  if (selection.kind === 'plugin') return `plugin-map:${selection.map_id || ''}`
  if (selection.kind === 'upload') return `upload:${selection.asset_id || ''}`
  if (selection.kind === 'generated') return `generated:${selection.asset_id || ''}`
  return 'auto'
}

export function mapBackgroundSelection(choice: string): MapBackgroundSelection {
  if (choice === 'none') return { kind: 'none' }
  if (choice.startsWith('builtin:')) return { kind: 'builtin', id: choice.slice('builtin:'.length) }
  if (choice.startsWith('plugin-map:')) return { kind: 'plugin', map_id: choice.slice('plugin-map:'.length) }
  if (choice.startsWith('upload:')) return { kind: 'upload', asset_id: choice.slice('upload:'.length) }
  if (choice.startsWith('generated:')) return { kind: 'generated', asset_id: choice.slice('generated:'.length) }
  return { kind: 'auto' }
}

export async function uploadMapBackground(file: File): Promise<MapBackgroundSelection> {
  const result = await api<MapBackgroundMutationResponse>('/map-backgrounds', {
    method: 'POST',
    body: JSON.stringify({
      file_data: await mapBackgroundFileToBase64(file),
      file_name: file.name,
    }),
  })
  if (!result.ok || !result.map_background) {
    throw new Error(result.error || 'map-background-upload-failed')
  }
  return result.map_background
}

export async function updateGameMapBackground(
  gameKey: string,
  choice: string,
  file?: File | null,
): Promise<MapBackgroundSelection> {
  const body = file
    ? {
        file_data: await mapBackgroundFileToBase64(file),
        file_name: file.name,
      }
    : { map_background: mapBackgroundSelection(choice) }
  const result = await api<MapBackgroundMutationResponse>(
    `/games/${encodeURIComponent(gameKey)}/map-background`,
    { method: 'POST', body: JSON.stringify(body) },
  )
  if (!result.ok || !result.map_background) {
    throw new Error(result.error || 'map-background-update-failed')
  }
  return result.map_background
}

export async function resolveMapBackgroundAsset(map: MapData): Promise<MapData> {
  const url = String(map.active_map?.background?.url || '')
  if (!url.startsWith('/api/')) return map
  try {
    const response = await apiBlob(url.slice('/api'.length))
    return {
      ...map,
      active_map: map.active_map ? {
        ...map.active_map,
        background: map.active_map.background ? {
          ...map.active_map.background,
          url: URL.createObjectURL(await response.blob()),
        } : undefined,
      } : undefined,
    }
  } catch {
    return {
      ...map,
      active_map: map.active_map ? {
        ...map.active_map,
        background: map.active_map.background ? {
          ...map.active_map.background,
          url: '',
        } : undefined,
      } : undefined,
    }
  }
}

export function revokeMapBackgroundAsset(map?: MapData | null): void {
  const url = String(map?.active_map?.background?.url || '')
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
}
