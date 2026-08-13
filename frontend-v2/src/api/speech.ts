import { api, apiBlob } from '@/api/client'
import type {
  AppConfig,
  TtsPersonalVoiceProfile,
  TtsPersonalVoiceProfileInput,
  TtsPersonalVoiceProfilesResponse,
  TtsSpeechRequest,
  TtsVoiceCatalog,
} from '@/api/types'

export const MAX_TTS_REFERENCE_BYTES = 10 * 1024 * 1024

async function referenceFilePayload(file: File): Promise<{ file_data: string; file_name: string }> {
  if (file.size > MAX_TTS_REFERENCE_BYTES) throw new Error('tts-reference-too-large')
  if (file.type && !['audio/wav', 'audio/x-wav', 'audio/wave'].includes(file.type)) {
    throw new Error('tts-reference-invalid')
  }
  if (!file.name.toLowerCase().endsWith('.wav')) throw new Error('tts-reference-invalid')
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return { file_data: btoa(binary), file_name: file.name }
}

export const speechApi = {
  publicConfig: () => api<AppConfig>('/config'),
  voices: () => api<TtsVoiceCatalog>('/tts/voices'),
  profiles: () => api<TtsPersonalVoiceProfilesResponse>('/tts/profiles'),
  saveProfile: async (
    profile: TtsPersonalVoiceProfileInput,
    profileId = '',
    referenceFile?: File | null,
  ): Promise<TtsPersonalVoiceProfile> => {
    const upload = referenceFile ? await referenceFilePayload(referenceFile) : {}
    const result = await api<{ ok: boolean; profile: TtsPersonalVoiceProfile }>(
      profileId ? `/tts/profiles/${encodeURIComponent(profileId)}` : '/tts/profiles',
      {
        method: profileId ? 'PUT' : 'POST',
        body: JSON.stringify({ ...profile, ...upload }),
      },
    )
    return result.profile
  },
  deleteProfile: (profileId: string) => api<{ ok: boolean }>(
    `/tts/profiles/${encodeURIComponent(profileId)}`,
    { method: 'DELETE' },
  ),
  synthesize: async (gameKey: string, request: TtsSpeechRequest): Promise<Blob> => {
    const response = await apiBlob(`/games/${encodeURIComponent(gameKey)}/speech`, {
      method: 'POST',
      body: JSON.stringify(request),
    })
    return response.blob()
  },
  test: async (request: TtsSpeechRequest): Promise<Blob> => {
    const response = await apiBlob('/tts/test', {
      method: 'POST',
      body: JSON.stringify(request),
    })
    return response.blob()
  },
}
