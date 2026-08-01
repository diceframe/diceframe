import { api, apiBlob } from '@/api/client'
import type { CharacterPortrait } from '@/api/types'
import { fileToBase64 } from '@/utils/characterImport'

interface AvatarUploadResponse {
  ok?: boolean
  error?: string
  portrait?: CharacterPortrait
}

const uploadedUrls = new Map<string, string>()

function currentGameKey(): string {
  return new URLSearchParams(location.hash.split('?')[1] || '').get('game') || ''
}

function avatarEndpoint(assetId = ''): string {
  const gameKey = currentGameKey()
  const base = gameKey ? `/games/${encodeURIComponent(gameKey)}/avatars` : '/avatars'
  return assetId ? `${base}/${encodeURIComponent(assetId)}` : base
}

export async function uploadAvatar(file: File): Promise<CharacterPortrait> {
  const fileData = await fileToBase64(file)
  const result = await api<AvatarUploadResponse>(avatarEndpoint(), {
    method: 'POST',
    body: JSON.stringify({ file_name: file.name, file_data: fileData }),
  })
  if (!result.ok || !result.portrait) throw new Error(result.error || 'Avatar upload failed')
  return result.portrait
}

export async function uploadedAvatarUrl(assetId: string): Promise<string> {
  const cached = uploadedUrls.get(assetId)
  if (cached) return cached
  const response = await apiBlob(avatarEndpoint(assetId))
  const url = URL.createObjectURL(await response.blob())
  uploadedUrls.set(assetId, url)
  return url
}
