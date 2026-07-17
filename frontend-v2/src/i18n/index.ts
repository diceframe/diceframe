import { createI18n } from 'vue-i18n'
import { en } from './messages/en'
import { zhCN } from './messages/zh-CN'

export type Locale = 'zh-CN' | 'en'
export type MessageKey = keyof typeof zhCN

export const LOCALE_STORAGE_KEY = 'diceframe_locale'

export const messages = {
  'zh-CN': zhCN,
  en,
} as const

export function normalizeLocale(value: unknown): Locale {
  const text = String(value || '').toLowerCase()
  return text === 'en' || text.startsWith('en-') ? 'en' : 'zh-CN'
}

function initialLocale(): Locale {
  if (typeof localStorage === 'undefined') return 'zh-CN'
  return normalizeLocale(localStorage.getItem(LOCALE_STORAGE_KEY))
}

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale(),
  fallbackLocale: 'zh-CN',
  messages,
  missingWarn: false,
  fallbackWarn: false,
})
