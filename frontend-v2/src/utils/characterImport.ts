import { api } from '../api/client'
import type { CharacterCard, CharacterImportResponse } from '../api/types'

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

export async function importTavernCard(file: File): Promise<CharacterCard> {
  const fileData = await fileToBase64(file)
  const r = await api<CharacterImportResponse>('/character-cards/import', {
    method: 'POST',
    body: JSON.stringify({ file_name: file.name, file_data: fileData }),
  })
  if (!r.ok || !r.card) throw new Error(r.error || '导入失败')
  return r.card
}
