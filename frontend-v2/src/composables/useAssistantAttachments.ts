// DF Agent 会话附件：文本文件上传（规则书 / 剧本 / 设定资料）。
// 内容存在服务端会话里，模型经 read_attachment 分页读取。
import { computed, ref, type Ref } from 'vue'
import { apiUrl, authHeaders } from '@/api/client'

export type SessionAttachment = { id: string; name: string; chars: number }

const ALLOWED = ['.txt', '.md', '.markdown', '.json', '.csv', '.log']
const MAX_BYTES = 2 * 1024 * 1024

export function useAssistantAttachments(sessionId: Ref<string | null>, locale: Ref<string>) {
  const uploading = ref(false)
  const uploadError = ref('')
  const attached = ref<SessionAttachment[]>([])
  const acceptList = computed(() => ALLOWED.join(','))

  async function upload(file: File, onSession: (sid: string) => void): Promise<boolean> {
    if (!file) return false
    if (!ALLOWED.some(ext => file.name.toLowerCase().endsWith(ext))) {
      uploadError.value = locale.value === 'zh-CN'
        ? `仅支持文本文件（${ALLOWED.join(' ')}）`
        : `Only text files are supported (${ALLOWED.join(' ')})`
      return false
    }
    if (file.size > MAX_BYTES) {
      uploadError.value = locale.value === 'zh-CN' ? '文件超过 2MB 上限' : 'File exceeds the 2MB limit'
      return false
    }
    uploading.value = true
    uploadError.value = ''
    try {
      const form = new FormData()
      if (sessionId.value) form.append('session_id', sessionId.value)
      form.append('file', file)
      const resp = await fetch(apiUrl('/assistant/agent/attachments'), {
        method: 'POST',
        headers: authHeaders(undefined, false),
        body: form,
        credentials: 'include',
      })
      const result = await resp.json() as { ok: boolean; session_id?: string; attachment?: SessionAttachment; error?: string }
      if (!resp.ok || !result.ok) throw new Error(result.error || `HTTP_${resp.status}`)
      if (result.session_id) onSession(result.session_id)
      if (result.attachment) attached.value = [...attached.value.slice(-3), result.attachment]
      return true
    } catch (e) {
      uploadError.value = String(e instanceof Error ? e.message : e)
      return false
    } finally {
      uploading.value = false
      setTimeout(() => { uploadError.value = '' }, 5000)
    }
  }

  return { uploading, uploadError, attached, acceptList, upload }
}
