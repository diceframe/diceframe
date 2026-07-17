import type { MessageApi, DialogApi } from 'naive-ui'

let messageApi: MessageApi | null = null
let dialogApi: DialogApi | null = null

export function registerNaiveApis(message: MessageApi, dialog: DialogApi) {
  messageApi = message
  dialogApi = dialog
}

export function getMessage(): MessageApi {
  if (!messageApi) throw new Error('Naive MessageProvider is not initialized. Mount NaiveBridge under the App.vue root.')
  return messageApi
}

export function getDialog(): DialogApi {
  if (!dialogApi) throw new Error('Naive DialogProvider is not initialized. Mount NaiveBridge under the App.vue root.')
  return dialogApi
}
