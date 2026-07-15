import type { MessageApi, DialogApi } from 'naive-ui'

let messageApi: MessageApi | null = null
let dialogApi: DialogApi | null = null

export function registerNaiveApis(message: MessageApi, dialog: DialogApi) {
  messageApi = message
  dialogApi = dialog
}

export function getMessage(): MessageApi {
  if (!messageApi) throw new Error('Naive MessageProvider 未初始化：请在 App.vue 根挂载 NaiveBridge')
  return messageApi
}

export function getDialog(): DialogApi {
  if (!dialogApi) throw new Error('Naive DialogProvider 未初始化：请在 App.vue 根挂载 NaiveBridge')
  return dialogApi
}
